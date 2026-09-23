"""Spreadsheet additional-field corrections through the review flow (Phase 2, stream D).

Kind `attribute_corrections` (docs/phase2-spec.md, review/phase2-inquiry.md). **Execution
never edits a case.** It creates one *pending* `case_corrections` row per case (kind
`attribute`, `changes = {key: {before, after}}`, the job's reason, `requested_by` = the
confirmer), exactly what `POST /v2/cases/<id>/corrections` stores. Reviewers then approve or
reject them in `/corrections` with the Phase 1 API (four eyes, stale check, locked-case
check and the attribute merge all stay there).

Request:

    POST /v2/bulk/attribute_corrections
    {"source": "spreadsheet", "upload_id": "...", "sheet": "Sheet1",
     "mapping": {"account_number": "Account No"},
     "params": {"reason": "bank file 2026-09",
                "attributes": {"city": "City", "employer": "Employer"},   # attribute key -> column
                "blank_cells": "skip"}}                                   # or "remove"

Validation and `before` capture reuse services.case_history_corrections (`_parse_changes`
with kind `attribute`), key by key, so the rules are the Phase 1 ones:
* keys: any `clints.attribute` key of 1-200 characters, at most 50 (MAX_ATTRIBUTE_KEYS);
  a key the case does not have yet is accepted (before = null) and listed in `new_keys`;
* values: text of at most 2000 characters (or null = remove the key);
* `before` = the case's current `attribute[key]` (an absent key and a null are the same "no
  value"); a key whose value would not change is dropped from the request, and a row with
  nothing left is `excluded / no_change`.

Spreadsheet specifics:
* Cells are text. When the case's current value is a JSON number (or boolean) and the cell is
  that type written as text, the new value keeps the stored type ("1200" -> 1200), so an
  unchanged number is a no_change and a changed one stays a number. Otherwise it is text.
* A blank cell asks for nothing (`blank_cells: "skip"`, default); with `"remove"` it asks to
  remove the key (after = null), like the Phase 1 API's null.
* A cell of 2000 or more characters is refused (`error / value_too_long`): the framework keeps
  at most 2000 characters of a source cell, so a longer value could not be requested intact.
* A cell that starts with "=" is refused (`error / formula_not_allowed`): formulas are never
  evaluated, and an attribute column is not a mapped column, so the framework cannot tell a
  formula cell from text here.
* A case that already has a *pending* attribute correction touching one of the row's keys is
  `excluded / pending_correction_exists` (checked at preview and again at execution, after the
  case row is locked).

Permission: `case_corrections_request` (the Phase 1 key for requesting corrections).
"""
from __future__ import annotations

import decimal
import json
import re

from sqlalchemy import text

from core.api_response import ApiError
from core.bulk.common import NOT_FOUND_OR_NOT_PERMITTED
from core.bulk.registry import BulkKind, Eligibility, MappingField, Outcome, ParamError
from services.bulk_common import reject_unknown, required_reason

KIND = "attribute"
BLANK_MODES = ("skip", "remove")
REASON_MAX = 2000                        # same limit as POST /v2/cases/<id>/corrections
_NUMBER = re.compile(r"^-?(0|[1-9][0-9]*)(\.[0-9]+)?$")


def _corr():
    """services.case_history_corrections, imported lazily (it imports the Flask app's db)."""
    from services import case_history_corrections
    return case_history_corrections


def _parse(changes: dict, case) -> dict:
    """Phase 1 validation + `before` capture (raises ApiError like the API route)."""
    return _corr()._parse_changes(KIND, changes, case)


def _coerce(cell: str, before):
    """Keep the stored JSON type when the cell is that type written as text."""
    if isinstance(before, bool):
        low = cell.strip().lower()
        if low in ("true", "false"):
            return low == "true"
        return cell
    if isinstance(before, (int, float)) and _NUMBER.match(cell.strip()):
        d = decimal.Decimal(cell.strip())
        if d == d.to_integral_value() and "." not in cell:
            return int(d)
        return float(d)
    return cell


def _problem(e: ApiError, key: str) -> tuple[str, str, dict]:
    err = e.error if isinstance(e.error, dict) else {}
    inner = err.get("changes") if isinstance(err.get("changes"), dict) else None
    if inner and "no_change" in inner:
        return "no_change", e.message, {}
    problem = err.get(key) or (inner and next(iter(inner), None)) or (err and next(iter(err.values()), None))
    return "invalid_attribute_value", e.message, {"key": key, "problem": problem}


class AttributeCorrectionsKind(BulkKind):
    key = "attribute_corrections"
    label_en, label_ar = "Request additional-field corrections", "طلب تصحيح الحقول الإضافية"
    # the legacy "attributes by Excel" button shows on clints_UpdateByExcel (screen-key rule)
    permissions = ("case_corrections_request", "clints_UpdateByExcel")
    sources = ("spreadsheet",)
    mapping_fields = (
        MappingField("account_number", "Account number", "رقم الحساب", required=True, match=True, max_length=200),
    )
    match_by = "account_number"
    result_columns = ("changes", "unchanged_keys", "new_keys", "blank_keys", "pending_correction_ids",
                      "is_locked", "is_archived", "correction_id")

    # ------------------------------------------------------------------ params
    def validate_params(self, params, ctx):
        reject_unknown(params, {"reason", "attributes", "blank_cells"})
        reason = required_reason(params, max_len=REASON_MAX)
        blank = params.get("blank_cells", "skip")
        if blank not in BLANK_MODES:
            raise ParamError({"blank_cells": "invalid"}, "blank_cells must be 'skip' or 'remove'")
        attrs = params.get("attributes")
        if not isinstance(attrs, dict) or not attrs:
            raise ParamError({"attributes": "required"},
                             "attributes must map at least one attribute key to a column: {key: column}")
        try:       # the Phase 1 key rules (1-200 characters, at most 50 keys)
            _parse({k: "x" for k in attrs}, {"id": None, "attribute": {}})
        except ApiError as e:
            raise ParamError({"attributes": (e.error or {}).get("changes", "invalid")}, e.message) from None
        headers = _request_headers(ctx)
        errors, out = {}, {}
        for k, col in attrs.items():
            if isinstance(col, bool) or not isinstance(col, (str, int)) or col == "":
                errors[k] = "invalid"
            elif headers is None:
                out[k] = col.strip() if isinstance(col, str) else col
            elif isinstance(col, int):
                if 0 <= col < len(headers):
                    out[k] = headers[col]
                else:
                    errors[k] = "unknown_column"
            elif col.strip() in headers:
                out[k] = col.strip()
            elif col in headers:
                out[k] = col
            else:
                errors[k] = "unknown_column"
        if errors:
            raise ParamError({"attributes": errors}, "the attribute column mapping is not valid")
        return {"reason": reason, "attributes": out, "blank_cells": blank}

    # ------------------------------------------------------------------ preview
    def prepare(self, rows, ctx):
        params = ctx.params
        cases = _load_cases(ctx.session, [r.case_id for r in rows])
        pending = _pending(ctx.session, [r.case_id for r in rows])
        out = {}
        for r in rows:
            case = cases.get(r.case_id)
            if case is None:            # deleted since matching: same answer as an unknown account
                out[r.id] = Eligibility.error(NOT_FOUND_OR_NOT_PERMITTED, "case not found or not in your scope")
                continue
            requested, blank, problem = _requested(r.raw or {}, params, case)
            flags = {"is_locked": bool(case["is_locked"]), "is_archived": bool(case["is_archive"])}
            if problem is not None:
                out[r.id] = Eligibility.error(problem[0], problem[1], {**problem[2], **flags})
                continue
            changes, unchanged, problem = {}, [], None
            for key, after in requested.items():
                try:
                    changes.update(_parse({key: after}, case))
                except ApiError as e:
                    code, msg, det = _problem(e, key)
                    if code == "no_change":
                        unchanged.append(key)
                    else:
                        problem = (code, msg, det)
                        break
            if problem is not None:
                out[r.id] = Eligibility.error(problem[0], problem[1], {**problem[2], **flags})
                continue
            info = {"unchanged_keys": unchanged, "blank_keys": blank, **flags}
            if not changes:
                out[r.id] = Eligibility.excluded("no_change", "the case already has these values", info)
                continue
            info["changes"] = changes
            info["new_keys"] = sorted(k for k, ch in changes.items() if ch["before"] is None)
            clash = _clashes(pending.get(r.case_id, []), changes)
            if clash:
                info["pending_correction_ids"] = clash
                out[r.id] = Eligibility.excluded(
                    "pending_correction_exists",
                    "the case already has a pending attribute correction for one of these keys", info)
                continue
            out[r.id] = Eligibility.eligible(info)
        return out

    # ------------------------------------------------------------------ execution
    def execute_batch(self, rows, ctx):
        reason = ctx.params["reason"]
        ids = [r.case_id for r in rows]
        cases = _load_cases(ctx.session, ids)                 # rows locked by the framework
        pending = _pending(ctx.session, ids)
        out, todo = {}, []
        for r in rows:
            preview = (r.details or {}).get("changes") or {}
            wanted = {k: ch["after"] for k, ch in preview.items()}
            try:
                changes = _parse(wanted, cases[r.case_id]) if wanted else {}
            except ApiError as e:
                code, msg, _ = _problem(e, next(iter(wanted), ""))
                out[r.id] = Outcome.skipped("no_change" if code == "no_change" else "invalid_attribute_value", msg)
                continue
            if not changes:
                out[r.id] = Outcome.skipped("no_change", "nothing to request")
                continue
            if changes != preview:              # revision is unchanged, so this cannot differ
                out[r.id] = Outcome.skipped("case_changed", "the attribute values changed after the preview")
                continue
            clash = _clashes(pending.get(r.case_id, []), changes)
            if clash:
                out[r.id] = Outcome.skipped("pending_correction_exists",
                                            "a pending attribute correction for one of these keys was "
                                            "requested after the preview", {"pending_correction_ids": clash})
                continue
            todo.append((r, changes))
        if not todo:
            return out
        from core import audit
        data = [{"case_id": r.case_id, "changes": json.loads(audit.to_json(ch)), "rev": cases[r.case_id]["revision"]}
                for r, ch in todo]
        created = {row[1]: row[0] for row in ctx.session.execute(text("""
            INSERT INTO case_corrections (case_id, kind, changes, reason, requested_by, base_revision)
            SELECT d.case_id, :kind, d.changes, :reason, CAST(:by AS uuid), d.rev
              FROM jsonb_to_recordset(CAST(:rows AS jsonb)) AS d(case_id uuid, changes jsonb, rev int)
            RETURNING id::text, case_id::text
        """), {"kind": KIND, "reason": reason, "by": ctx.actor_id, "rows": json.dumps(data, ensure_ascii=False)})}
        for r, changes in todo:
            cid = created[r.case_id]
            ctx.audit(r, "case_correction.request", entity_type="case_correction", entity_id=cid,
                      after={"kind": KIND, "changes": changes, "status": "pending"}, reason=reason)
            out[r.id] = Outcome.succeeded({"correction_id": cid})
        return out


# --------------------------------------------------------------------------- helpers
def _request_headers(ctx) -> list[str] | None:
    """Headers of the job's sheet, read from the creating request (the framework validated
    the upload just before validate_params). None outside a request (e.g. tooling)."""
    from flask import has_request_context, request

    from core.bulk import sources
    if not has_request_context() or ctx.source != "spreadsheet":
        return None
    body = request.get_json(silent=True) or {}
    upload = sources.load_upload(ctx.session, body.get("upload_id"), ctx.actor_id)
    return list(sources.sheet_info(upload, body.get("sheet")).get("headers") or [])


def _load_cases(session, ids) -> dict:
    rows = session.execute(text("""
        SELECT c.id::text AS id, c.attribute, c.revision, c.is_locked, c.is_archive
          FROM clints c WHERE c.id = ANY(CAST(:ids AS uuid[]))
    """), {"ids": sorted(set(ids))}).mappings()
    return {m["id"]: dict(m) for m in rows}


def _pending(session, ids) -> dict:
    """case id -> [(correction id, {keys})] of pending attribute corrections."""
    out: dict = {}
    for cid, corr_id, keys in session.execute(text("""
        SELECT case_id::text, id::text, ARRAY(SELECT jsonb_object_keys(changes))
          FROM case_corrections
         WHERE case_id = ANY(CAST(:ids AS uuid[])) AND status = 'pending' AND kind = :kind
         ORDER BY requested_at, id
    """), {"ids": sorted(set(ids)), "kind": KIND}):
        out.setdefault(cid, []).append((corr_id, set(keys)))
    return out


def _clashes(pending: list, changes: dict) -> list[str]:
    return [cid for cid, keys in pending if keys & set(changes)]


def _requested(raw: dict, params: dict, case) -> tuple[dict, list, tuple | None]:
    """{key: after} asked by one spreadsheet row, the blank keys, or a row problem."""
    from core.bulk.inspect import RAW_CELL_MAX, is_formula_text
    attrs = case["attribute"] or {}
    requested, blank = {}, []
    for key, col in params["attributes"].items():
        cell = raw.get(col)
        cell = cell.strip() if isinstance(cell, str) else cell
        if cell is None or cell == "":
            blank.append(key)
            if params.get("blank_cells") == "remove":
                requested[key] = None
            continue
        if len(cell) >= RAW_CELL_MAX:
            return {}, blank, ("value_too_long",
                               f"column {col!r} holds {RAW_CELL_MAX} or more characters (attribute values "
                               f"are limited to {RAW_CELL_MAX - 1} here, longer cells are cut when the file is read)",
                               {"key": key, "column": col})
        if is_formula_text(cell):
            return {}, blank, ("formula_not_allowed",
                               f"column {col!r} holds a formula; formulas are never evaluated, enter the value itself",
                               {"key": key, "column": col})
        requested[key] = _coerce(cell, attrs.get(key))
    return requested, blank, None


KINDS = (AttributeCorrectionsKind(),)
