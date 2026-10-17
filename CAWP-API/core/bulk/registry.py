"""Kind registry: how a bulk operation plugs into the framework.

A *kind* is one reviewed bulk operation (lock, unlock, assign, archive, notes, inquiry, ...).
It declares what it accepts and implements two hooks; the framework does everything else
(sources, uploads, mapping, matching, scope, duplicates, preview, confirmation, batches,
rechecks, outcomes, artifacts, retries, audit plumbing).

    from core.bulk import BulkKind, MappingField, Eligibility, Outcome

    class ArchiveKind(BulkKind):
        key = "archive"                                  # URL: POST /v2/bulk/archive
        label_en, label_ar = "Archive cases", "أرشفة العملاء"
        permissions = ("clints_move_archive",)           # any-of, legacy keys
        sources = ("selection", "filter", "portfolio", "spreadsheet")
        mapping_fields = (MappingField("account_number", "Account number", "رقم الحساب",
                                       required=True, match=True),)
        match_by = "account_number"                      # framework resolves rows to cases

        def validate_params(self, params, ctx):          # at job creation (HTTP), raise ParamError
            return {"reason_id": require_uuid(params, "reason_id")}

        def prepare(self, rows, ctx):                    # preview, NO side effects
            return {r.id: Eligibility.excluded("already_archived", "...")
                    for r in rows if r.case["is_archive"]}

        def execute_batch(self, rows, ctx):              # one transaction per batch
            ...UPDATE clints ... ; ctx.audit(row, "case.archive", before=..., after=...)
            return {r.id: Outcome.succeeded() for r in rows}

    KINDS = (ArchiveKind(),)                             # in services/bulk_archive.py

Discovery: every `services/bulk_*.py` module (except `bulk_actions`, `bulk_common`) that
defines `KINDS` is imported and registered on first use, so a new stream adds a module and
never edits the framework.

What the framework guarantees around the hooks:

* `prepare(rows, ctx)` gets only rows that matched a case **in the creator's scope** and are
  not duplicates; `row.case` is a snapshot (see `CASE_SNAPSHOT_COLUMNS`). Rows it does not
  return an Eligibility for are eligible. It must not write business tables (tested).
* `execute_batch(rows, ctx)` runs inside the batch transaction. Before it, the framework has
  locked every case row (`FOR NO KEY UPDATE`, id order), and removed rows whose case vanished
  (`skipped/case_missing`), whose confirmer lost the permission (`failed/permission_denied`)
  or scope (`failed/out_of_scope`), whose revision changed since the preview
  (`skipped/case_changed`) or that hold a call reservation (`skipped/call_reserved`).
  `row.case` is the locked, current snapshot. The kind still checks its own state rules
  (e.g. already locked) and returns an Outcome per row; the framework writes the outcomes,
  the `ctx.audit(...)` events (source `bulk_job`, request_id = job id) and the counters in
  the same transaction, then commits. A missing Outcome is recorded as `failed/no_outcome`.
* An exception inside `execute_batch` rolls the batch back; the framework then re-runs that
  batch one row per transaction, so a single bad row fails alone (`failed/internal_error`).
"""
from __future__ import annotations

import dataclasses
import importlib
import logging
import pkgutil
import threading
from typing import Any

log = logging.getLogger("cawp.bulk.registry")

SOURCES = ("selection", "filter", "spreadsheet", "portfolio", "query")
FILTER_LISTS = ("clients", "archive")
# framework-resolved match fields: mapped field key -> SQL column of `clints c`
MATCH_COLUMNS = {"account_number": 'c."accountNumber"', "case_id": "c.id"}
CASE_SNAPSHOT_COLUMNS = ("id", "revision", "account_number", "identity_number", "name", "user_id",
                         "portfolio_id", "contract_id", "is_locked", "is_archive", "is_active")

ELIGIBLE, EXCLUDED, ERROR = "eligible", "excluded", "error"
SUCCEEDED, SKIPPED, FAILED = "succeeded", "skipped", "failed"


class ParamError(Exception):
    """Raised by `validate_params`; becomes 422 validation_failed with `error` = errors."""

    def __init__(self, errors: dict, message: str = "invalid parameters"):
        super().__init__(message)
        self.errors = errors
        self.message = message


@dataclasses.dataclass(frozen=True)
class MappingField:
    key: str
    label_en: str
    label_ar: str
    required: bool = False
    match: bool = False          # identifies the case (see BulkKind.match_by)
    max_length: int = 1000
    description: str = ""

    def as_dict(self) -> dict:
        return dataclasses.asdict(self)


@dataclasses.dataclass
class Eligibility:
    status: str
    code: str | None = None
    message: str | None = None
    details: dict | None = None

    @classmethod
    def eligible(cls, details: dict | None = None) -> "Eligibility":
        return cls(ELIGIBLE, None, None, details)

    @classmethod
    def excluded(cls, code: str, message: str, details: dict | None = None) -> "Eligibility":
        return cls(EXCLUDED, code, message, details)

    @classmethod
    def error(cls, code: str, message: str, details: dict | None = None) -> "Eligibility":
        return cls(ERROR, code, message, details)


@dataclasses.dataclass
class Outcome:
    status: str
    code: str | None = None
    message: str | None = None
    details: dict | None = None

    @classmethod
    def succeeded(cls, details: dict | None = None, code: str | None = None) -> "Outcome":
        return cls(SUCCEEDED, code, None, details)

    @classmethod
    def skipped(cls, code: str, message: str, details: dict | None = None) -> "Outcome":
        return cls(SKIPPED, code, message, details)

    @classmethod
    def failed(cls, code: str, message: str, details: dict | None = None) -> "Outcome":
        return cls(FAILED, code, message, details)


@dataclasses.dataclass
class BulkRow:
    """One `bulk_job_rows` row as a kind sees it."""
    id: int
    row_no: int
    raw: dict
    mapped: dict
    match_value: str | None
    case_id: str | None
    expected_revision: int | None
    eligibility: str
    eligibility_code: str | None = None
    details: dict | None = None
    case: dict | None = None       # snapshot, keys = CASE_SNAPSHOT_COLUMNS


class BulkKind:
    """Base class of a kind. Subclasses set the class attributes and override the hooks."""

    key: str = ""
    label_en: str = ""
    label_ar: str = ""
    permissions: tuple[str, ...] = ()           # any-of; checked at create/confirm AND execution
    sources: tuple[str, ...] = ("selection",)
    filter_lists: tuple[str, ...] = ("clients",)  # clients-list filters ("archive" = archive list)
    read_only: bool = False                     # True: the preview is the result (no confirm)
    mapping_fields: tuple[MappingField, ...] = ()
    match_by: str | None = "account_number"     # MATCH_COLUMNS key, or None: kind matches itself
    batch_size: int | None = None               # default BULK_BATCH_SIZE
    result_columns: tuple[str, ...] = ()        # row.details keys written to the CSV artifacts
    # False: the kind changes neither ownership nor case state (e.g. a PBX evidence import), so
    # cases with an open call are not skipped (services.bulk_common.call_reservations)
    call_reservation_guard: bool = True

    # ---- hooks -------------------------------------------------------------------------
    def validate_params(self, params: dict, ctx: "CreateContext") -> dict:
        """Normalise `params` at job creation. Raise ParamError({field: code}) when invalid.
        Runs in the HTTP request; may read the DB through ctx.session (no writes)."""
        return {}

    def resolve_query(self, params: dict, ctx: "KindContext") -> tuple[str, dict]:
        """For source 'query': SQL selecting `case_id` (uuid) rows, in the wanted order, and
        its bind parameters. The framework applies scope, limits and duplicates."""
        raise NotImplementedError(f"kind {self.key} has no query source")

    def match_rows(self, rows: list[BulkRow], ctx: "KindContext") -> dict[int, Any]:
        """Only when match_by is None: {row.id: case_id | Eligibility(error...)} for the rows
        the kind could resolve; unresolved rows become error/not_found_or_not_permitted."""
        raise NotImplementedError(f"kind {self.key} does not match rows itself")

    def prepare(self, rows: list[BulkRow], ctx: "KindContext") -> dict[int, Eligibility]:
        """Preview decision per row. MUST NOT change business data."""
        return {}

    def execute_batch(self, rows: list[BulkRow], ctx: "ExecuteContext") -> dict[int, Outcome]:
        """Apply the change for rows that passed the framework rechecks (one transaction)."""
        raise NotImplementedError(f"kind {self.key} cannot execute")

    # ---- description for the API -------------------------------------------------------
    def describe(self) -> dict:
        return {
            "key": self.key, "label_en": self.label_en, "label_ar": self.label_ar,
            "permissions": list(self.permissions), "sources": list(self.sources),
            "filter_lists": list(self.filter_lists), "read_only": self.read_only,
            "mapping_fields": [f.as_dict() for f in self.mapping_fields],
            "match_by": self.match_by, "result_columns": list(self.result_columns),
        }

    def validate_definition(self) -> None:
        if not self.key or not self.key.replace("_", "a").isalnum() or not self.key[0].isalpha() \
                or self.key != self.key.lower():
            raise ValueError(f"bulk kind key {self.key!r} must be lower_snake_case")
        if not self.permissions:
            raise ValueError(f"bulk kind {self.key}: at least one permission key is required")
        bad = [s for s in self.sources if s not in SOURCES]
        if bad or not self.sources:
            raise ValueError(f"bulk kind {self.key}: unknown sources {bad}")
        if "spreadsheet" in self.sources:
            if not self.mapping_fields:
                raise ValueError(f"bulk kind {self.key}: spreadsheet source needs mapping_fields")
            if self.match_by is not None:
                if self.match_by not in MATCH_COLUMNS:
                    raise ValueError(f"bulk kind {self.key}: match_by must be one of {sorted(MATCH_COLUMNS)}")
                if self.match_by not in {f.key for f in self.mapping_fields if f.match}:
                    raise ValueError(f"bulk kind {self.key}: match_by {self.match_by!r} must be a match field")
        bad_lists = [x for x in self.filter_lists if x not in FILTER_LISTS]
        if bad_lists:
            raise ValueError(f"bulk kind {self.key}: unknown filter lists {bad_lists}")


# --------------------------------------------------------------------------- contexts
@dataclasses.dataclass
class CreateContext:
    session: Any
    actor_id: str
    scope: Any
    source: str


@dataclasses.dataclass
class KindContext:
    """Passed to resolve_query / match_rows / prepare (validation phase, in the worker)."""
    session: Any
    job: dict
    actor_id: str            # the job creator (validation runs with the creator's scope)
    scope: Any
    params: dict


@dataclasses.dataclass
class ExecuteContext:
    """Passed to execute_batch. `actor_id` = the confirmer (or the last retrier)."""
    session: Any
    job: dict
    actor_id: str
    scope: Any
    params: dict
    attempt: int
    events: list = dataclasses.field(default_factory=list)

    def audit(self, row: BulkRow, action: str, *, before=None, after=None, reason=None,
              entity_type: str = "case", entity_id=None) -> None:
        """Queue one audit_events row; written by the framework in the batch transaction with
        source 'bulk_job', request_id = the job id, actor = ctx.actor_id."""
        after = dict(after or {})
        after.setdefault("bulk_job_id", str(self.job["id"]))
        after.setdefault("row_no", row.row_no)
        self.events.append({
            "actor": self.actor_id, "action": action, "entity_type": entity_type,
            "entity_id": str(entity_id if entity_id is not None else row.case_id),
            "case_id": row.case_id, "before": before, "after": after, "reason": reason,
            "request_id": str(self.job["id"]),
        })


# --------------------------------------------------------------------------- registry
_KINDS: dict[str, BulkKind] = {}
_lock = threading.RLock()
_discovered = False
SKIP_MODULES = {"services.bulk_actions", "services.bulk_common"}


def register(kind: BulkKind) -> BulkKind:
    kind.validate_definition()
    with _lock:
        existing = _KINDS.get(kind.key)
        if existing is not None and type(existing) is not type(kind):
            raise ValueError(f"bulk kind {kind.key!r} is registered twice")
        _KINDS[kind.key] = kind
    return kind


def discover() -> None:
    """Import every services/bulk_*.py module that defines KINDS (once per process)."""
    global _discovered
    with _lock:
        if _discovered:
            return
        import services
        for info in pkgutil.iter_modules(services.__path__):
            name = f"services.{info.name}"
            if not info.name.startswith("bulk_") or name in SKIP_MODULES:
                continue
            mod = importlib.import_module(name)
            for kind in getattr(mod, "KINDS", ()) or ():
                register(kind)
        _discovered = True


def get(key: str) -> BulkKind | None:
    discover()
    return _KINDS.get(key)


def all_kinds() -> list[BulkKind]:
    discover()
    return [_KINDS[k] for k in sorted(_KINDS)]


def all_permission_keys() -> set[str]:
    return {p for k in all_kinds() for p in k.permissions}
