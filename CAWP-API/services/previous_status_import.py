"""Previous statuses import ("استيراد الحالات السابقة", customer request 2026-10-19; migration 0074,
api/previous_status_import.py).

The customer sent one spreadsheet per portfolio with each account's main and sub status from the
old system (headers `رقم الحساب | الحالة الرئيسية | الحالة الفرعية`; `---` = no status). An admin
uploads it here, previews the matching and applies it. Nothing is written outside the apply step.

* **Upload**: the Phase 2 private store and inspection (`core.bulk.inspect` + `core.bulk.storage`),
  a `bulk_uploads` row with purpose `previous_statuses_import`, like the portfolio upload. The three
  columns are guessed from their headers (`guess_mapping`); the user can map them otherwise.
* **Matching** (`analyse`, read-only, shared by preview and apply), per row:
  account -> case by exact `"accountNumber"`, then by the trimmed value (`btrim`); main status by
  name among LIVE main statuses (`deleted_at` NULL, `parent_id` NULL) that are general (contract
  NULL) or the case's contract's own (the contract's own wins); sub-status by name among the live
  sub-statuses OF THAT main status. Names compare after trimming and collapsing whitespace
  (`norm_name`); letters are kept exactly (ه/ة, أ/ا are different names). Deleted statuses never match.
* **Missing statuses**: a missing MAIN status is reported and its rows skipped (never created).
  A missing sub-status under a found main status is created by the apply step when
  `create_missing_sub_statuses` is on (default), with its parent's contract and reachability
  (the rule of services/comment_status_tree.py); otherwise its rows are skipped.
* **Apply** (`start_apply` in the request, `run_import` in a background job): ONE transaction
  writes the created sub-statuses, one legacy `"Comments"` note per ready row, the report rows and
  the audit events. The note is what every note writer stores, plus the 0068 sub-status:
  comment_status_id = main, comment_sub_status_id = sub, comment = MARKER (+ the admin's note),
  user_id = the importer, contactMethod_id = the chosen contact method (NOT NULL column),
  comment_reachability_id = the main status's reachability, no callback, and
  create_at = the chosen day (Riyadh midnight, before today; default 2026-10-11, before go-live).
  So `comments_last_comment_trg` (db/03, newest create_at wins) keeps a newer note collectors
  already wrote as the case's latest note, and today's productivity reports never count them.
* **Idempotency**: `previous_status_imports.upload_id` is UNIQUE (one import per upload; a
  succeeded one never runs again), apply runs under one advisory lock, and a case that already
  has a live note with the same main + sub status whose text starts with MARKER is skipped
  (`already_imported`), so uploading the same file again adds nothing.

Permission: `settings_comments_status_add` AND the SuperAdmin role (see api/previous_status_import.py).
"""
from __future__ import annotations

import dataclasses
import datetime as _dt
import logging
import os
import re
import uuid

from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from core import audit
from core import riyadh
from core.api_response import ApiError
from core.bulk import inspect as _inspect
from core.bulk import settings as _settings
from core.bulk import sources, storage
from core.bulk.common import dumps, iso, valid_uuid
from services.portfolio_import import norm_header

log = logging.getLogger("cawp.previous_status_import")

PERMISSION = "settings_comments_status_add"
UPLOAD_PURPOSE = "previous_statuses_import"
MARKER = "حالة سابقة (من النظام السابق)"
DEFAULT_NOTED_ON = _dt.date(2026, 10, 11)      # the day before the system went live (Asia/Riyadh)
NO_STATUS = {"---", "--", "-", "—"}
NOTE_MAX = 1000
NAME_MAX = 200
ACCOUNT_MAX = 200
PROBLEM_LIMIT = 200
BATCH = 1000
IN_CHUNK = 1000
STALE_SECONDS = 900
LOCK_KEY = "previous_status_import"
SUB_STATUS_COLOR = "#FFFFFF"

FIELDS = (
    ("account_number", "Account number", "رقم الحساب", True,
     ("رقم الحساب", "الحساب", "رقم العقد", "accountnumber", "account", "accountno", "accno")),
    ("main_status", "Main status", "الحالة الرئيسية", True,
     ("الحالة الرئيسية", "الحالة", "حالة", "الحالة السابقة", "mainstatus", "status", "previousstatus")),
    ("sub_status", "Sub-status", "الحالة الفرعية", False,
     ("الحالة الفرعية", "الفرعية", "حالة فرعية", "substatus", "previoussubstatus")),
)
FIELD_KEYS = tuple(f[0] for f in FIELDS)
_FIELD_GUESS = {f[0]: {norm_header(x) for x in (f[0], f[1], f[2], *f[4])} for f in FIELDS}

# outcome codes -> (English, Arabic)
CODES = {
    "ready": ("Ready", "جاهز"),
    "imported": ("Imported", "تم الاستيراد"),
    "no_status": ("No previous status (empty or ---)", "لا توجد حالة سابقة (فارغة أو ---)"),
    "missing_account": ("Account number is empty", "رقم الحساب فارغ"),
    "formula_not_allowed": ("A mapped cell holds a formula", "خلية مربوطة تحتوي على معادلة"),
    "value_too_long": ("A value is longer than allowed", "قيمة أطول من المسموح"),
    "duplicate_in_file": ("The account repeats an earlier row of the file", "رقم الحساب مكرر في الملف"),
    "account_not_found": ("No case has this account number", "لا يوجد عميل بهذا رقم الحساب"),
    "account_ambiguous": ("Several cases match this account number", "أكثر من عميل يطابق رقم الحساب"),
    "case_archived": ("The case is archived", "العميل مؤرشف"),
    "case_locked": ("The case is locked; notes are blocked", "العميل مقفل؛ لا يمكن إضافة متابعة"),
    "status_not_found": ("The main status does not exist (create it in the statuses settings)",
                         "الحالة الرئيسية غير موجودة (أضفها من إعدادات الحالات)"),
    "status_ambiguous": ("Several live main statuses have this name", "أكثر من حالة رئيسية بنفس الاسم"),
    "sub_status_not_found": ("The sub-status does not exist under this main status",
                             "الحالة الفرعية غير موجودة تحت هذه الحالة الرئيسية"),
    "sub_status_ambiguous": ("Several live sub-statuses have this name under the main status",
                             "أكثر من حالة فرعية بنفس الاسم تحت الحالة الرئيسية"),
    "already_imported": ("The case already has this imported previous status", "الحالة السابقة مستوردة مسبقاً لهذا العميل"),
}
SKIP_CODES = tuple(k for k in CODES if k not in ("ready", "imported"))


class ImportRefused(Exception):
    def __init__(self, code: str, message: str, counts: dict | None = None):
        super().__init__(message)
        self.code, self.message, self.counts = code, message, counts


def _s():
    from extensions import db
    return db.session


# =========================================================================== text helpers
_INVISIBLE = re.compile("[​-‏‪-‮⁦-⁩﻿]")


def norm_name(value) -> str:
    """A status name as compared: invisible marks removed, trimmed, whitespace collapsed."""
    return " ".join(_INVISIBLE.sub("", str(value or "")).split())


def clean(value) -> str | None:
    if value is None:
        return None
    value = _INVISIBLE.sub("", str(value)).strip()
    return value or None


def status_value(value) -> str | None:
    """A status cell: None for empty and for the old system's `---` placeholder."""
    v = norm_name(value)
    return None if not v or v in NO_STATUS else v


def _chunks(seq, size=IN_CHUNK):
    seq = list(seq)
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


# =========================================================================== mapping
def guess_mapping(headers: list[str]) -> dict:
    out, used = {}, set()
    normed = [(h, norm_header(h)) for h in headers]
    for key in FIELD_KEYS:
        hit = next((h for h, n in normed if n in _FIELD_GUESS[key] and h not in used), None)
        if hit is not None:
            out[key] = hit
            used.add(hit)
    return out


def validate_mapping(raw, headers: list[str]) -> dict:
    if not isinstance(raw, dict):
        raise ApiError(422, "invalid_mapping", "mapping must be an object", {"mapping": "invalid"})
    errors, out, used = {}, {}, set()
    for k in raw:
        if k not in FIELD_KEYS:
            errors[str(k)] = "unknown_field"
    for key, _en, _ar, required, _g in FIELDS:
        col = raw.get(key)
        if col in (None, ""):
            if required:
                errors[key] = "required"
            continue
        if not isinstance(col, str) or col not in headers:
            errors[key] = "unknown_column"
        elif col in used:
            errors[key] = "column_used_twice"
        else:
            used.add(col)
            out[key] = col
    if errors:
        raise ApiError(422, "invalid_mapping", "the column mapping is not valid", {"mapping": errors})
    return out


# =========================================================================== lookups
def contact_methods(session) -> list[dict]:
    rows = session.execute(text('SELECT id::text, name FROM "ContactMethods" ORDER BY name, id')).all()
    return [{"id": r[0], "name": r[1]} for r in rows]


_CALL_WORDS = ("اتصال", "هاتف", "مكالمة", "جوال", "call", "phone")


def default_contact_method(methods: list[dict]) -> str | None:
    """A phone-call-like method when one exists, else the first (the note needs one: NOT NULL)."""
    for m in methods:
        n = (m["name"] or "").strip().lower()
        if any(w in n for w in _CALL_WORDS):
            return m["id"]
    return methods[0]["id"] if methods else None


def options(session, actor: str, upload_id=None, sheet=None) -> dict:
    methods = contact_methods(session)
    latest = riyadh.today() - _dt.timedelta(days=1)
    out = {"fields": [{"key": k, "label_en": en, "label_ar": ar, "required": req} for k, en, ar, req, _g in FIELDS],
           "contact_methods": methods, "default_contact_method_id": default_contact_method(methods),
           "default_noted_on": min(DEFAULT_NOTED_ON, latest).isoformat(), "latest_noted_on": latest.isoformat(),
           "marker": MARKER, "note_max": NOTE_MAX}
    if upload_id not in (None, ""):
        upload = sources.load_upload(session, upload_id, actor)
        _check_purpose(upload)
        sh = sources.sheet_info(upload, sheet or None)
        out["sheet"] = sh.get("name")
        out["headers"] = sh.get("headers") or []
        out["guess"] = guess_mapping(out["headers"])
    return out


# =========================================================================== upload
def create_upload(file_storage, actor: str) -> dict:
    """The Phase 2 private store and inspection (as services/portfolio_import.create_upload),
    purpose `previous_statuses_import`."""
    from core.bulk.service import serialize_upload
    if file_storage is None or not file_storage.filename:
        raise ApiError(422, "validation_failed", "a file is required (multipart field 'file')", {"file": "required"})
    original = os.path.basename(file_storage.filename.replace("\\", "/"))[:255] or "upload"
    tmp = storage.tmp_path()
    try:
        size, sha = _inspect.receive(file_storage.stream, tmp)
        detected = _inspect.detect_type(tmp, original)
        info = _inspect.inspect_file(tmp, detected)
    except _inspect.InspectionError as e:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise ApiError(e.status, e.code, e.message, e.error) from None
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
    upload_id = str(uuid.uuid4())
    now = _dt.datetime.now(_dt.timezone.utc)
    relative = storage.upload_relative(upload_id, detected, now)
    storage.place(tmp, relative)
    row = _s().execute(text("""
        INSERT INTO bulk_uploads (id, purpose, original_name, stored_path, size_bytes, sha256, detected_type,
                                  encoding, delimiter, sheets, row_count, header_row, formula_cells,
                                  uploaded_by, uploaded_at, expires_at)
        VALUES (CAST(:id AS uuid), :purpose, :name, :path, :size, :sha, :type, :enc, :delim, CAST(:sheets AS jsonb),
                :rows, :hrow, :formulas, CAST(:by AS uuid), :now, :now + make_interval(days => :ttl))
        RETURNING *
    """), {"id": upload_id, "purpose": UPLOAD_PURPOSE, "name": original, "path": relative, "size": size,
           "sha": sha, "type": detected, "enc": info["encoding"], "delim": info["delimiter"],
           "sheets": dumps(info["sheets"]), "rows": info["row_count"], "hrow": info["header_row"],
           "formulas": info["formula_cells"], "by": actor, "now": now,
           "ttl": _settings.get().upload_ttl_days}).mappings().one()
    audit.record("bulk_upload.create", "bulk_upload", upload_id,
                 after={"purpose": UPLOAD_PURPOSE, "original_name": original, "size_bytes": size, "sha256": sha,
                        "detected_type": detected, "row_count": info["row_count"],
                        "formula_cells": info["formula_cells"], "scan_status": "not_scanned"})
    out = serialize_upload(dict(row))
    first = sources.sheet_info(dict(row), None)
    out["guess"] = guess_mapping(first.get("headers") or [])
    out["guess_sheet"] = first.get("name")
    return out


def _check_purpose(upload: dict) -> None:
    if upload["purpose"] != UPLOAD_PURPOSE:
        raise ApiError(409, "upload_purpose_mismatch",
                       f"this file was uploaded for {upload['purpose']}, not for a previous statuses import",
                       {"purpose": upload["purpose"]})


# =========================================================================== request validation
def _bool(body: dict, key: str, default: bool) -> bool:
    v = body.get(key, default)
    if v is None:
        return default
    if not isinstance(v, bool):
        raise ApiError(422, "validation_failed", f"{key} must be true or false", {key: "invalid"})
    return v


def parse_noted_on(value) -> tuple[_dt.date, _dt.datetime]:
    """'YYYY-MM-DD' -> (date, Riyadh midnight as aware UTC). Must be before today (Asia/Riyadh)."""
    if value in (None, ""):
        day = DEFAULT_NOTED_ON
    else:
        try:
            day = riyadh.parse_date(value)
        except (ValueError, TypeError):
            raise ApiError(422, "validation_failed", "noted_on must be a date (YYYY-MM-DD)",
                           {"noted_on": "invalid"}) from None
    if day >= riyadh.today():
        raise ApiError(422, "validation_failed", "noted_on must be before today (Asia/Riyadh)",
                       {"noted_on": "not_before_today"})
    if day.year < 2000:
        raise ApiError(422, "validation_failed", "noted_on is too old", {"noted_on": "invalid"})
    return day, riyadh.day_bounds(day)[0]


def _validated(session, body: dict, actor: str, *, for_apply: bool = False) -> dict:
    if not isinstance(body, dict):
        raise ApiError(400, "invalid_json", "request body must be a JSON object")
    upload = sources.load_upload(session, body.get("upload_id"), actor)
    _check_purpose(upload)
    sources.upload_usable(upload)
    sheet = sources.sheet_info(upload, body.get("sheet") or None)
    s = _settings.get()
    if not sheet.get("complete", True) or (sheet.get("rows") or 0) > s.max_rows:
        raise ApiError(422, "validation_failed", f"the sheet has more than {s.max_rows} rows",
                       {"sheet": "too_many_rows", "max_rows": s.max_rows})
    if not sheet.get("rows"):
        raise ApiError(422, "validation_failed", "the sheet has no data rows", {"sheet": "empty"})
    raw_mapping = body.get("mapping")
    if raw_mapping in (None, {}):
        raw_mapping = guess_mapping(sheet.get("headers") or [])
    mapping = validate_mapping(raw_mapping, sheet.get("headers") or [])
    opts = {"create_missing_sub_statuses": _bool(body, "create_missing_sub_statuses", True),
            "include_archived": _bool(body, "include_archived", False)}
    if for_apply:
        day, noted_at = parse_noted_on(body.get("noted_on"))
        method = valid_uuid(body.get("contact_method_id"))
        if method is None:
            method = default_contact_method(contact_methods(session))
            if method is None:
                raise ApiError(422, "validation_failed", "no contact method exists", {"contact_method_id": "required"})
        elif session.execute(text('SELECT 1 FROM "ContactMethods" WHERE id = CAST(:id AS uuid)'),
                             {"id": method}).first() is None:
            raise ApiError(422, "validation_failed", "the contact method does not exist",
                           {"contact_method_id": "unknown"})
        note = body.get("note")
        if note is not None and not isinstance(note, str):
            raise ApiError(422, "validation_failed", "note must be text", {"note": "invalid"})
        note = (note or "").strip() or None
        if note and len(note) > NOTE_MAX:
            raise ApiError(422, "validation_failed", "note is too long", {"note": "too_long"})
        opts.update({"noted_on": day.isoformat(), "noted_at": iso(noted_at), "contact_method_id": method,
                     "note": note})
    return {"upload": upload, "sheet": sheet, "mapping": mapping, "options": opts}


# =========================================================================== analysis
@dataclasses.dataclass
class RowPlan:
    row_number: int
    account: str | None = None
    main: str | None = None
    sub: str | None = None
    code: str | None = None             # None until decided; "ready" when it will be imported
    case_id: str | None = None
    contract_id: str | None = None
    main_id: str | None = None
    sub_id: str | None = None
    sub_key: tuple | None = None        # (main_id, sub name) of a sub-status to create
    reach_id: str | None = None
    comment_id: str | None = None
    archived: bool = False
    locked: bool = False

    @property
    def ready(self) -> bool:
        return self.code == "ready"

    def skip(self, code: str):
        self.code = code

    def message(self) -> str:
        return CODES.get(self.code, (self.code, self.code))[1]

    def problem(self) -> dict:
        return {"row_number": self.row_number, "account_number": self.account, "main_status": self.main,
                "sub_status": self.sub, "code": self.code, "message": self.message(), "case_id": self.case_id,
                "creates_sub_status": self.sub_key is not None}


@dataclasses.dataclass
class Analysis:
    plans: list
    counts: dict
    to_create: list                      # [{main_id, main_name, name, rows}]
    missing_main: list                   # [{name, rows}]
    missing_sub: list                    # [{main_name, name, rows}] (option off)


def _iter_rows(upload: dict, sheet: dict):
    if upload["purged_at"] is not None or not storage.exists(upload["stored_path"]):
        raise ImportRefused("upload_purged", "the uploaded file was purged; upload it again")
    yield from _inspect.iter_data_rows(
        storage.resolve(upload["stored_path"]), upload["detected_type"], sheet.get("name"),
        int(sheet.get("header_row") or 1), sheet.get("headers") or [],
        encoding=upload["encoding"], delimiter=upload["delimiter"])


def _plan_row(row_no: int, raw: dict, formula_headers: set, mapping: dict) -> RowPlan:
    p = RowPlan(row_no)
    p.account = clean(raw.get(mapping["account_number"]))
    p.main = status_value(raw.get(mapping["main_status"]))
    p.sub = status_value(raw.get(mapping["sub_status"])) if mapping.get("sub_status") else None
    if any(col in formula_headers for col in mapping.values()):
        p.skip("formula_not_allowed")
    elif p.main is None:
        p.skip("no_status")
    elif p.account is None:
        p.skip("missing_account")
    elif len(p.account) > ACCOUNT_MAX or len(p.main) > NAME_MAX or len(p.sub or "") > NAME_MAX:
        p.skip("value_too_long")
    return p


def load_statuses(session) -> tuple[dict, dict]:
    """(mains by name, subs by (main id, name)): LIVE statuses only; each value a list of rows."""
    rows = session.execute(text("""
        SELECT s.id::text AS id, s.name, s.parent_id::text AS parent_id, s.contract_id::text AS contract_id,
               s."CommentReachability_id"::text AS reach, s.is_active
          FROM "CommentStatuses" s
          LEFT JOIN "CommentStatuses" p ON p.id = s.parent_id
         WHERE s.deleted_at IS NULL AND (s.parent_id IS NULL OR p.deleted_at IS NULL)
         ORDER BY s.create_at, s.id
    """)).mappings().all()
    mains, subs = {}, {}
    for r in rows:
        r = dict(r)
        if r["parent_id"] is None:
            mains.setdefault(norm_name(r["name"]), []).append(r)
        else:
            subs.setdefault((r["parent_id"], norm_name(r["name"])), []).append(r)
    return mains, subs


def _match_cases(session, plans) -> None:
    wanted = {p.account for p in plans if p.code is None}
    found: dict[str, list] = {}
    sql = """
        SELECT c.id::text AS id, c."accountNumber" AS account, c.is_archive, c.is_locked,
               p.contract_id::text AS contract_id
          FROM clints c LEFT JOIN portfolios p ON p.id = c.portfolios_id
         WHERE {where}"""
    for part in _chunks(wanted):
        for r in session.execute(text(sql.format(where='c."accountNumber" = ANY(CAST(:a AS text[]))')),
                                 {"a": part}).mappings():
            found.setdefault(r["account"], []).append(dict(r))
    rest = [a for a in wanted if a not in found]
    for part in _chunks(rest):              # an account number stored with spaces around it
        for r in session.execute(text(sql.format(where='btrim(c."accountNumber") = ANY(CAST(:a AS text[]))')),
                                 {"a": part}).mappings():
            found.setdefault(r["account"].strip(), []).append(dict(r))
    for p in plans:
        if p.code is not None:
            continue
        cases = found.get(p.account) or []
        if not cases:
            p.skip("account_not_found")
        elif len(cases) > 1:
            p.skip("account_ambiguous")
        else:
            c = cases[0]
            p.case_id, p.contract_id = c["id"], c["contract_id"]
            p.archived, p.locked = bool(c["is_archive"]), bool(c["is_locked"])


def analyse(session, upload: dict, sheet: dict, mapping: dict, opts: dict) -> Analysis:
    """Decide every row. Reads only; shared by preview and apply."""
    max_rows = _settings.get().max_rows
    plans = []
    for row_no, raw, formula_headers in _iter_rows(upload, sheet):
        plans.append(_plan_row(row_no, raw, formula_headers, mapping))
        if len(plans) > max_rows:
            raise ImportRefused("too_many_rows", f"the sheet has more than {max_rows} rows")

    first_row = {}
    for p in plans:
        if p.code is None:
            if p.account in first_row:
                p.skip("duplicate_in_file")
            else:
                first_row[p.account] = p.row_number

    _match_cases(session, plans)
    seen_cases = set()
    for p in plans:
        if p.code is not None:
            continue
        if p.case_id in seen_cases:          # two spellings of one account (trimmed match)
            p.skip("duplicate_in_file")
            continue
        seen_cases.add(p.case_id)
        if p.archived and not opts.get("include_archived"):
            p.skip("case_archived")
        elif p.locked:
            p.skip("case_locked")

    mains, subs = load_statuses(session)
    create = opts.get("create_missing_sub_statuses", True)
    for p in plans:
        if p.code is not None:
            continue
        cands = mains.get(p.main) or []
        own = [m for m in cands if m["contract_id"] is not None and m["contract_id"] == p.contract_id]
        fits = own or [m for m in cands if m["contract_id"] is None]
        if not fits:
            p.skip("status_not_found")
            continue
        if len(fits) > 1:
            p.skip("status_ambiguous")
            continue
        m = fits[0]
        p.main_id, p.reach_id = m["id"], m["reach"]
        if p.sub is None:
            p.code = "ready"
            continue
        sc = subs.get((m["id"], p.sub)) or []
        if len(sc) == 1:
            p.sub_id, p.code = sc[0]["id"], "ready"
        elif len(sc) > 1:
            p.skip("sub_status_ambiguous")
        elif create:
            p.sub_key, p.code = (m["id"], p.sub), "ready"
        else:
            p.skip("sub_status_not_found")

    _mark_already_imported(session, plans)

    main_names = {m["id"]: m["name"] for ms in mains.values() for m in ms}
    to_create, missing_main, missing_sub = {}, {}, {}
    for p in plans:
        if p.ready and p.sub_key is not None:
            e = to_create.setdefault(p.sub_key, {"main_id": p.sub_key[0], "main_name": main_names.get(p.sub_key[0]),
                                                 "name": p.sub_key[1], "rows": 0})
            e["rows"] += 1
        elif p.code == "status_not_found":
            missing_main.setdefault(p.main, {"name": p.main, "rows": 0})["rows"] += 1
        elif p.code == "sub_status_not_found":
            e = missing_sub.setdefault((p.main_id, p.sub), {"main_name": main_names.get(p.main_id),
                                                            "name": p.sub, "rows": 0})
            e["rows"] += 1
    a = Analysis(plans, {}, sorted(to_create.values(), key=lambda e: (e["main_name"] or "", e["name"])),
                 sorted(missing_main.values(), key=lambda e: -e["rows"]),
                 sorted(missing_sub.values(), key=lambda e: -e["rows"]))
    a.counts = _counts(a)
    return a


def _mark_already_imported(session, plans) -> None:
    """A case that already has a live imported note with the same main + sub status."""
    ready = [p for p in plans if p.ready]
    have = set()
    for part in _chunks(sorted({p.case_id for p in ready})):
        for r in session.execute(text("""
            SELECT clint_id::text, comment_status_id::text, comment_sub_status_id::text
              FROM "Comments"
             WHERE clint_id = ANY(CAST(:ids AS uuid[])) AND is_deleted = false
               AND left(comment, :n) = :marker
        """), {"ids": part, "n": len(MARKER), "marker": MARKER}):
            have.add((r[0], r[1], r[2]))
    for p in ready:
        # a sub-status still to be created cannot be on an existing note
        if p.sub_key is None and (p.case_id, p.main_id, p.sub_id) in have:
            p.skip("already_imported")


def _counts(a: Analysis) -> dict:
    c = {k: 0 for k in SKIP_CODES}
    ready = 0
    for p in a.plans:
        if p.ready:
            ready += 1
        else:
            c[p.code] = c.get(p.code, 0) + 1
    return {
        "total": len(a.plans), "ready": ready, "skipped": len(a.plans) - ready, **c,
        "ready_with_sub_status": sum(1 for p in a.plans if p.ready and (p.sub_id or p.sub_key)),
        "ready_creating_sub_status": sum(1 for p in a.plans if p.ready and p.sub_key),
        "sub_statuses_to_create": len(a.to_create),
        "missing_main_statuses": len(a.missing_main),
    }


def _problems(plans, limit=PROBLEM_LIMIT) -> list[dict]:
    """Skipped rows first (except the plain `---` rows), then ready rows that create a sub-status."""
    out = [p.problem() for p in plans if not p.ready and p.code != "no_status"][:limit]
    if len(out) < limit:
        out += [p.problem() for p in plans if p.ready and p.sub_key is not None][:limit - len(out)]
    return out


# =========================================================================== preview
def preview(body: dict, actor: str) -> dict:
    session = _s()
    v = _validated(session, body, actor)
    try:
        a = analyse(session, v["upload"], v["sheet"], v["mapping"], v["options"])
    except ImportRefused as e:
        raise ApiError(409 if e.code == "upload_purged" else 422, e.code, e.message) from None
    existing = _import_for_upload(session, v["upload"]["id"])
    blocking = []
    if a.counts["ready"] == 0:
        blocking.append({"code": "nothing_to_import"})
    if existing and existing["status"] == "succeeded":
        blocking.append({"code": "already_imported", "import_id": str(existing["id"])})
    session.rollback()
    return {"counts": a.counts, "problems": _problems(a.plans), "problem_limit": PROBLEM_LIMIT,
            "sub_statuses_to_create": a.to_create, "missing_main_statuses": a.missing_main,
            "missing_sub_statuses": a.missing_sub, "blocking": blocking, "can_apply": not blocking,
            "mapping": v["mapping"], "options": v["options"], "sheet": v["sheet"].get("name"),
            "import": serialize_import(existing) if existing else None}


# =========================================================================== apply (request side)
def _import_for_upload(session, upload_id, lock: bool = False) -> dict | None:
    sql = "SELECT * FROM previous_status_imports WHERE upload_id = CAST(:u AS uuid)" + (
        " FOR UPDATE NOWAIT" if lock else "")
    row = session.execute(text(sql), {"u": str(upload_id)}).mappings().first()
    return dict(row) if row else None


def _stale(imp: dict) -> bool:
    return (_dt.datetime.now(_dt.timezone.utc) - imp["updated_at"]).total_seconds() > STALE_SECONDS


def start_apply(body: dict, actor: str) -> dict:
    """Queue the import (create or reuse THE import row of this upload). The caller commits and then
    runs `run_import` in the background. 409 when it already succeeded or is running."""
    session = _s()
    v = _validated(session, body, actor, for_apply=True)
    upload_id = v["upload"]["id"]
    values = {"u": str(upload_id), "sheet": v["sheet"].get("name"), "mapping": dumps(v["mapping"]),
              "options": dumps(v["options"]), "by": actor}
    try:
        imp = _import_for_upload(session, upload_id, lock=True)
    except OperationalError as e:
        session.rollback()
        if getattr(getattr(e, "orig", None), "pgcode", None) == "55P03":
            raise ApiError(409, "import_in_progress", "this file is being imported right now") from None
        raise
    if imp is None:
        row = session.execute(text("""
            INSERT INTO previous_status_imports (upload_id, sheet, mapping, options, status, attempts, created_by)
            VALUES (CAST(:u AS uuid), :sheet, CAST(:mapping AS jsonb), CAST(:options AS jsonb), 'queued', 1,
                    CAST(:by AS uuid))
            ON CONFLICT (upload_id) DO NOTHING
            RETURNING *
        """), values).mappings().first()
        if row is None:
            raise ApiError(409, "import_in_progress", "this file is being imported right now")
        imp = dict(row)
    else:
        if imp["status"] == "succeeded":
            raise ApiError(409, "already_imported", "this file was already imported", {"import_id": str(imp["id"])})
        if imp["status"] in ("queued", "running") and not _stale(imp):
            raise ApiError(409, "import_in_progress", "this file is being imported right now",
                           {"import_id": str(imp["id"])})
        imp = dict(session.execute(text("""
            UPDATE previous_status_imports
               SET sheet = :sheet, mapping = CAST(:mapping AS jsonb), options = CAST(:options AS jsonb),
                   status = 'queued', attempts = attempts + 1, error_code = NULL, error_message = NULL,
                   started_at = NULL, finished_at = NULL, updated_at = now()
             WHERE id = CAST(:id AS uuid)
            RETURNING *
        """), {**values, "id": str(imp["id"])}).mappings().one())
    audit.record("previous_status_import.queue", "previous_status_import", imp["id"],
                 after={"upload_id": str(upload_id), "mapping": v["mapping"], "options": v["options"],
                        "attempt": imp["attempts"]})
    return serialize_import(imp)


# =========================================================================== apply (job side)
def run_import(import_id: str) -> dict:
    """Background job body: claim a queued import, write everything in ONE transaction; on any failure
    roll back and mark the import failed separately."""
    session = _s()
    row = session.execute(text("SELECT * FROM previous_status_imports WHERE id = CAST(:id AS uuid) FOR UPDATE"),
                          {"id": str(import_id)}).mappings().first()
    if row is None or row["status"] != "queued":
        session.rollback()
        return {"status": None if row is None else row["status"], "skipped": True}
    session.execute(text("""UPDATE previous_status_imports SET status = 'running', started_at = now(),
                             updated_at = now() WHERE id = CAST(:id AS uuid)"""), {"id": str(import_id)})
    session.commit()
    try:
        result = _execute(session, str(import_id))
        session.commit()
        if result.get("created_sub_statuses"):
            from services import comment_status_tree
            comment_status_tree.invalidate_cache()
        return result
    except Exception as e:  # noqa: BLE001 - every failure leaves nothing behind
        session.rollback()
        if isinstance(e, ImportRefused):
            code, message, counts = e.code, e.message, e.counts
        elif isinstance(e, ApiError):
            code, message, counts = e.code, e.message, None
        else:
            log.exception("previous status import %s failed", import_id)
            code, message, counts = "import_failed", (str(e).splitlines() or ["error"])[0][:500], None
        session.execute(text("""
            UPDATE previous_status_imports
               SET status = 'failed', error_code = :code, error_message = :msg, finished_at = now(),
                   updated_at = now(), counts = coalesce(CAST(:counts AS jsonb), counts)
             WHERE id = CAST(:id AS uuid)
        """), {"id": str(import_id), "code": code, "msg": message, "counts": dumps(counts)})
        imp = session.execute(text("SELECT created_by FROM previous_status_imports WHERE id = CAST(:id AS uuid)"),
                              {"id": str(import_id)}).first()
        audit.record("previous_status_import.fail", "previous_status_import", import_id,
                     after={"code": code, "message": message}, actor_user_id=imp[0] if imp else None,
                     request_id=str(import_id), idempotency_key=None, source="previous_status_import")
        session.commit()
        return {"status": "failed", "error": {"code": code, "message": message}}


def _execute(session, import_id: str) -> dict:
    imp = dict(session.execute(text("SELECT * FROM previous_status_imports WHERE id = CAST(:id AS uuid) FOR UPDATE"),
                               {"id": import_id}).mappings().one())
    if imp["status"] != "running":
        raise ImportRefused("not_running", "the import is not running")
    actor = str(imp["created_by"])
    opts = dict(imp["options"] or {})
    upload = sources.load_upload(session, imp["upload_id"])
    sources.upload_usable(upload)
    sheet = sources.sheet_info(upload, imp["sheet"])
    mapping = validate_mapping(imp["mapping"], sheet.get("headers") or [])
    if session.execute(text('SELECT 1 FROM "ContactMethods" WHERE id = CAST(:id AS uuid)'),
                       {"id": opts.get("contact_method_id")}).first() is None:
        raise ImportRefused("contact_method_missing", "the contact method no longer exists")

    # one writer at a time: two imports (or a retry and a new upload) never insert the same note twice;
    # the settings screen's status writes wait for the locked main statuses below
    session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"), {"k": LOCK_KEY})
    a = analyse(session, upload, sheet, mapping, opts)
    if a.counts["ready"] == 0:
        raise ImportRefused("nothing_to_import", "no row of the file can be imported", a.counts)
    _lock_cases(session, a, bool(opts.get("include_archived")))
    if a.counts["ready"] == 0:
        raise ImportRefused("nothing_to_import", "no row of the file can be imported", a.counts)
    _lock_mains(session, a)
    created = _create_sub_statuses(session, a, actor, import_id)
    _insert_notes(session, a, actor, opts)
    _insert_report(session, a, import_id)
    _audit_success(session, a, imp, actor, created)
    a.counts["imported"] = a.counts.pop("ready")
    a.counts["sub_statuses_created"] = len(created)
    session.execute(text("""
        UPDATE previous_status_imports
           SET status = 'succeeded', counts = CAST(:counts AS jsonb), created_sub_statuses = CAST(:created AS jsonb),
               error_code = NULL, error_message = NULL, finished_at = now(), updated_at = now()
         WHERE id = CAST(:id AS uuid)
    """), {"id": import_id, "counts": dumps(a.counts), "created": dumps(created)})
    return {"status": "succeeded", "counts": a.counts, "created_sub_statuses": created}


def _lock_cases(session, a: Analysis, include_archived: bool) -> None:
    """Lock the target cases and recheck what the analysis read (archived / locked meanwhile)."""
    ready = {p.case_id: p for p in a.plans if p.ready}
    current = {}
    for part in _chunks(sorted(ready)):
        for r in session.execute(text("""
            SELECT id::text, is_archive, is_locked FROM clints
             WHERE id = ANY(CAST(:ids AS uuid[])) ORDER BY id FOR NO KEY UPDATE
        """), {"ids": part}):
            current[r[0]] = (r[1], r[2])
    for cid, p in ready.items():
        c = current.get(cid)
        if c is None:
            p.skip("account_not_found")
        elif c[0] and not include_archived:
            p.skip("case_archived")
        elif c[1]:
            p.skip("case_locked")
    a.counts = _counts(a)


def _lock_mains(session, a: Analysis) -> None:
    ids = sorted({p.sub_key[0] for p in a.plans if p.ready and p.sub_key})
    if not ids:
        return
    alive = {r[0] for r in session.execute(text("""
        SELECT id::text FROM "CommentStatuses"
         WHERE id = ANY(CAST(:ids AS uuid[])) AND deleted_at IS NULL AND parent_id IS NULL
         ORDER BY id FOR UPDATE
    """), {"ids": ids})}
    if alive != set(ids):
        raise ImportRefused("statuses_changed", "a main status changed during the import; try again")


def _create_sub_statuses(session, a: Analysis, actor: str, import_id: str) -> list[dict]:
    keys = {}
    for p in a.plans:
        if p.ready and p.sub_key is not None:
            keys.setdefault(p.sub_key, []).append(p)
    created = []
    for (main_id, name), plans in sorted(keys.items()):
        row = session.execute(text("""
            INSERT INTO "CommentStatuses" (name, contract_id, "CommentReachability_id", color, is_active, parent_id)
            SELECT :name, m.contract_id, m."CommentReachability_id", :color, m.is_active, m.id
              FROM "CommentStatuses" m WHERE m.id = CAST(:main AS uuid)
            RETURNING id::text, contract_id::text, "CommentReachability_id"::text
        """), {"name": name, "color": SUB_STATUS_COLOR, "main": main_id}).first()
        main_name = session.execute(text('SELECT name FROM "CommentStatuses" WHERE id = CAST(:id AS uuid)'),
                                    {"id": main_id}).scalar()
        for p in plans:
            p.sub_id = row[0]
        created.append({"id": row[0], "name": name, "parent_id": main_id, "parent_name": main_name,
                        "rows": len(plans)})
        audit.record("comment_status.create", "comment_status", row[0],
                     after={"name": name, "parent_id": main_id, "contract_id": row[1],
                            "CommentReachability_id": row[2], "color": SUB_STATUS_COLOR},
                     reason="created by the previous statuses import (missing sub-status)",
                     actor_user_id=actor, request_id=import_id, idempotency_key=None,
                     source="previous_status_import")
    return created


def note_text(note: str | None) -> str:
    return f"{MARKER}\n{note}" if note else MARKER


def _insert_notes(session, a: Analysis, actor: str, opts: dict) -> None:
    todo = [p for p in a.plans if p.ready]
    for p in todo:
        p.comment_id = str(uuid.uuid4())
    comment = note_text(opts.get("note"))
    for part in _chunks(todo, BATCH):
        data = [{"id": p.comment_id, "c": p.case_id, "s": p.main_id, "sub": p.sub_id, "r": p.reach_id,
                 "n": p.row_number} for p in part]
        session.execute(text("""
            INSERT INTO "Comments" (id, clint_id, user_id, "contactMethod_id", comment, comment_status_id,
                                    comment_reachability_id, next_contact_date, comment_sub_status_id,
                                    create_at, update_at)
            SELECT d.id, d.c, CAST(:actor AS uuid), CAST(:method AS uuid), :comment, d.s, d.r, NULL, d.sub,
                   CAST(:at AS timestamptz), CAST(:at AS timestamptz)
              FROM jsonb_to_recordset(CAST(:rows AS jsonb)) AS d(id uuid, c uuid, s uuid, sub uuid, r uuid, n int)
             ORDER BY d.n
        """), {"rows": dumps(data), "actor": actor, "method": opts["contact_method_id"], "comment": comment,
               "at": opts["noted_at"]})


def _insert_report(session, a: Analysis, import_id: str) -> None:
    data = [{"n": p.row_number, "a": p.account, "m": p.main, "s": p.sub,
             "o": "imported" if p.ready else "skipped", "c": "imported" if p.ready else p.code,
             "msg": CODES["imported"][1] if p.ready else p.message(), "case": p.case_id,
             "cm": p.comment_id if p.ready else None} for p in a.plans]
    for part in _chunks(data, BATCH):
        session.execute(text("""
            INSERT INTO previous_status_import_rows (import_id, row_number, account_number, main_status, sub_status,
                                                     outcome, code, message, case_id, comment_id)
            SELECT CAST(:imp AS uuid), d.n, d.a, d.m, d.s, d.o, d.c, d.msg, d.case, d.cm
              FROM jsonb_to_recordset(CAST(:rows AS jsonb))
                   AS d(n integer, a text, m text, s text, o text, c text, msg text, "case" uuid, cm uuid)
        """), {"rows": dumps(part), "imp": import_id})


_AUDIT_MANY = text("""
    INSERT INTO audit_events (actor_user_id, action, entity_type, entity_id, case_id, before, after,
                              reason, request_id, idempotency_key, source)
    SELECT CAST(:actor AS uuid), 'note.create', 'comment', e->>'id', CAST(e->>'case_id' AS uuid),
           NULL, e->'after', 'previous status from the old system', :rid, NULL, 'previous_status_import'
      FROM jsonb_array_elements(CAST(:events AS jsonb)) e
""")


def _audit_success(session, a: Analysis, imp: dict, actor: str, created: list) -> None:
    """One `note.create` per note (like the bulk notes kind) plus one event for the import."""
    opts = imp["options"] or {}
    events = [{"id": p.comment_id, "case_id": p.case_id,
               "after": {"comment_status_id": p.main_id, "comment_sub_status_id": p.sub_id,
                         "comment_reachability_id": p.reach_id, "contactMethod_id": opts.get("contact_method_id"),
                         "create_at": opts.get("noted_at"), "row_number": p.row_number}}
              for p in a.plans if p.ready]
    for part in _chunks(events, BATCH):
        session.execute(_AUDIT_MANY, {"events": dumps(part), "actor": actor, "rid": str(imp["id"])})
    audit.record("previous_status_import.succeed", "previous_status_import", imp["id"],
                 after={"counts": a.counts, "options": opts, "created_sub_statuses": [c["id"] for c in created]},
                 actor_user_id=actor, request_id=str(imp["id"]), idempotency_key=None,
                 source="previous_status_import")


# =========================================================================== reads
def serialize_import(imp: dict, extra: dict | None = None) -> dict:
    out = {
        "id": str(imp["id"]), "upload_id": str(imp["upload_id"]), "sheet": imp["sheet"],
        "mapping": imp["mapping"], "options": imp["options"] or {}, "status": imp["status"],
        "counts": imp["counts"] or {}, "created_sub_statuses": imp["created_sub_statuses"] or [],
        "error": None if imp["error_code"] is None else {"code": imp["error_code"], "message": imp["error_message"]},
        "attempts": imp["attempts"], "created_by": str(imp["created_by"]),
        "created_at": iso(imp["created_at"]), "updated_at": iso(imp["updated_at"]),
        "started_at": iso(imp["started_at"]), "finished_at": iso(imp["finished_at"]),
        "report_available": imp["status"] == "succeeded",
    }
    if extra:
        out.update(extra)
    return out


def get_import(import_id) -> dict:
    iid = valid_uuid(import_id)
    row = None if iid is None else _s().execute(text("""
        SELECT i.*, u.original_name FROM previous_status_imports i JOIN bulk_uploads u ON u.id = i.upload_id
         WHERE i.id = CAST(:id AS uuid)
    """), {"id": iid}).mappings().first()
    if row is None:
        raise ApiError(404, "import_not_found", "import not found", {"import_id": str(import_id)})
    return dict(row)


def import_detail(import_id) -> dict:
    r = get_import(import_id)
    return serialize_import(r, {"original_name": r["original_name"]})


REPORT_HEADERS = ("رقم الصف", "رقم الحساب", "الحالة الرئيسية", "الحالة الفرعية", "الرمز", "السبب")


def build_report(import_id, dest_dir: str) -> tuple[str, str]:
    """XLSX: the skipped rows with their reasons, a summary and the sub-statuses created."""
    from core.reports.xlsx import Workbook
    imp = get_import(import_id)
    if imp["status"] != "succeeded":
        raise ApiError(409, "report_not_ready", "the report exists only for a completed import",
                       {"status": imp["status"]})
    name = f"previous-statuses-{str(import_id)[:8]}.xlsx"
    path = os.path.join(dest_dir, name)
    wb = Workbook(path, rtl=True)
    sh = wb.add_sheet("الصفوف المتجاوزة", freeze_header=True)
    sh.append([("s", h) for h in REPORT_HEADERS], header=True)
    for r in _s().execute(text("""
        SELECT row_number, account_number, main_status, sub_status, code, message
          FROM previous_status_import_rows WHERE import_id = CAST(:id AS uuid) AND outcome = 'skipped'
         ORDER BY row_number
    """), {"id": str(import_id)}):
        sh.append([("int", r[0]), ("s", r[1]), ("s", r[2]), ("s", r[3]), ("s", r[4]), ("s", r[5])])
    summary = wb.add_sheet("الملخص")
    counts = imp["counts"] or {}
    summary.append([("s", "البند"), ("s", "العدد")], header=True)
    summary.append([("s", "إجمالي الصفوف"), ("int", int(counts.get("total") or 0))])
    summary.append([("s", CODES["imported"][1]), ("int", int(counts.get("imported") or 0))])
    summary.append([("s", "حالات فرعية أُنشئت"), ("int", int(counts.get("sub_statuses_created") or 0))])
    for code in SKIP_CODES:
        if counts.get(code):
            summary.append([("s", CODES[code][1]), ("int", int(counts[code]))])
    created = imp["created_sub_statuses"] or []
    if created:
        cs = wb.add_sheet("الحالات الفرعية المنشأة", freeze_header=True)
        cs.append([("s", "الحالة الرئيسية"), ("s", "الحالة الفرعية"), ("s", "عدد الصفوف")], header=True)
        for c in created:
            cs.append([("s", c.get("parent_name")), ("s", c.get("name")), ("int", int(c.get("rows") or 0))])
    wb.save()
    return path, name
