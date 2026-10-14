"""Portfolio upload ("رفع المحافظ", customer request 2026-10-14; migration 0070, api/portfolio_import.py).

Replaces, for the /clients screen, the legacy `POST /clint/addClintsByExcel` path (which stays
in place): that path committed the portfolio first and then inserted the rows in batches that
silently dropped failing rows, so a failed or repeated upload left extra, half-filled
portfolios behind. Here:

* **Upload** goes to the private Phase 2 store (`core.bulk.inspect` + `core.bulk.storage`, a
  `bulk_uploads` row with purpose `portfolio_import`). Blank headers become "Column C"
  (`inspect.normalise_headers`), so values never shift into the wrong column.
* **Mapping**: the user maps file columns to the system fields (`FIELDS`), maps any number of
  columns to contact types, and optionally a collector-username column. Every original column
  is still kept in `clints.attribute`, keyed by its header, like the legacy import.
* **Preview** (`preview`) writes nothing: it validates every row and answers counts and the
  first problem rows.
* **Commit** (`start_commit` in the request, `run_import` in a background job) writes the
  portfolio and ALL its cases, contacts, assignments, lineage links and report rows in ONE
  transaction. `portfolio_imports.upload_id` is UNIQUE: a retry after a failure reuses the
  same import row, and a succeeded import can never run again, so one upload yields at most
  one portfolio. If anything unexpected fails, nothing is created and the import is marked
  failed in a separate transaction.

Row outcomes (`portfolio_import_rows.outcome`):

| outcome | when |
|---|---|
| `created` | the account number is new |
| `created_from_archive` | a case with this account number exists and is ARCHIVED: the old case stays archived with all its notes, payments, promises and follow-ups; its account number becomes `<A>-أرشيف<n>` (smallest free n); the new case keeps `<A>`; `case_lineage` links new -> old. History is linked, never copied, so old money never reaches the new balance. |
| `skipped_active_exists` | a case with this account number exists and is NOT archived: nothing changes |
| `rejected` | a required value is missing/invalid, a formula, or the account number repeats an earlier row of the file |

Collector username (optional column): matched to `users.user_name` trimmed and
case-insensitively among ACTIVE users. Exactly one match -> the new case is assigned the way
every assignment path writes it (`clints.user_id` + one `"SepcifyUsers"` row, create_by = the
importer). No match / only inactive / several matches -> imported unassigned with a warning.

Contacts: every mapped contact column with a value becomes a `"ClintContacts"` row (by
identity number, status 'new', like legacy), unless the same (identity number, contact)
pair already exists (not deleted) or appears earlier in the file.
"""
from __future__ import annotations

import dataclasses
import datetime as _dt
import logging
import os
import re
import uuid
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from core import audit
from core.api_response import ApiError
from core.bulk import inspect as _inspect
from core.bulk import settings as _settings
from core.bulk import sources, storage
from core.bulk.common import dumps, iso, valid_uuid

log = logging.getLogger("cawp.portfolio_import")

PERMISSION = "clints_add"
UPLOAD_PURPOSE = "portfolio_import"
PROBLEM_LIMIT = 200
BATCH = 1000
IN_CHUNK = 1000
NAME_MAX = 200
ACCOUNT_MAX = 200
TEXT_MAX = 500
ARCHIVE_SUFFIX = "-أرشيف"
RENAME_PROBE = 20             # candidate suffix numbers probed per query round
STALE_SECONDS = 900           # a queued/running import untouched this long may be retried
LINEAGE_MAX_DEPTH = 20
CENT = Decimal("0.01")
MAX_AMOUNT = Decimal("999999999999.99")
OUTCOMES = ("created", "created_from_archive", "skipped_active_exists", "rejected")


def _s():
    from extensions import db
    return db.session


# =========================================================================== field catalogue
@dataclasses.dataclass(frozen=True)
class SysField:
    key: str
    label_en: str
    label_ar: str
    required: bool
    guesses: tuple

    def describe(self) -> dict:
        return {"key": self.key, "label_en": self.label_en, "label_ar": self.label_ar, "required": self.required}


FIELDS = (
    SysField("name", "Name", "الاسم", True,
             ("name", "clientname", "customername", "debtorname", "fullname", "الاسم", "اسم", "اسم العميل",
              "اسم المدين", "الاسم الكامل", "اسم العميل الكامل")),
    SysField("identity_number", "Identity number", "رقم الهوية", True,
             ("identitynumber", "identity", "identityno", "idnumber", "nationalid", "iqama", "iqamanumber",
              "رقم الهوية", "الهوية", "رقم الهويه", "رقم الاقامة", "الاقامة", "السجل المدني", "رقم السجل المدني")),
    SysField("account_number", "Account number", "رقم الحساب", True,
             ("accountnumber", "account", "accountno", "accno", "acc", "رقم الحساب", "الحساب", "رقم العقد")),
    SysField("amount", "Amount", "المبلغ", True,
             ("amount", "balance", "debt", "outstanding", "debtamount", "المبلغ", "المديونية", "مبلغ المديونية",
              "الرصيد", "المبلغ المستحق", "قيمة المديونية")),
    SysField("collector_username", "Collector username", "اسم مستخدم المحصل", False,
             ("username", "user_name", "user", "login", "collector", "collectorusername", "اسم المستخدم",
              "المستخدم", "المحصل", "اسم المحصل", "اسم مستخدم المحصل")),
)
FIELD_KEYS = tuple(f.key for f in FIELDS)

# Contact columns: the legacy files name them `<type>_<n>` (mobile_1, phone_2, email_1).
# A header matches a contact type when its base (trailing number removed) equals the type's
# name, or both belong to one of these synonym groups.
CONTACT_SYNONYMS = (
    ("mobile", "mobileno", "mobilenumber", "cell", "cellphone", "جوال", "الجوال", "رقم الجوال", "موبايل",
     "الموبايل", "محمول", "هاتف محمول"),
    ("phone", "phonenumber", "tel", "telephone", "landline", "هاتف", "الهاتف", "رقم الهاتف", "تلفون",
     "هاتف ثابت", "ثابت"),
    ("email", "e-mail", "mail", "بريد", "البريد", "البريد الالكتروني", "البريد الإلكتروني", "ايميل", "الايميل"),
    ("fax", "فاكس", "الفاكس"),
    ("whatsapp", "واتساب", "واتس اب", "الواتساب"),
)

# --------------------------------------------------------------------------- codes / labels
ROW_CODES = {
    "created": ("Created", "تمت الإضافة"),
    "created_from_archive": ("Created; the archived record was kept and linked",
                             "أُضيف سجل جديد، وبقي السجل المؤرشف السابق مرتبطاً به"),
    "active_exists": ("An active case with this account number already exists; nothing changed",
                      "يوجد عميل نشط بنفس رقم الحساب؛ لم يتغير شيء"),
    "missing_name": ("Name is empty", "الاسم فارغ"),
    "missing_identity_number": ("Identity number is empty", "رقم الهوية فارغ"),
    "missing_account_number": ("Account number is empty", "رقم الحساب فارغ"),
    "missing_amount": ("Amount is empty", "المبلغ فارغ"),
    "invalid_amount": ("Amount is not a number", "المبلغ ليس رقماً صحيحاً"),
    "negative_amount": ("Amount is negative", "المبلغ سالب"),
    "amount_too_precise": ("Amount has more than 2 decimals", "المبلغ يحتوي على أكثر من خانتين عشريتين"),
    "amount_too_large": ("Amount is too large", "المبلغ أكبر من المسموح"),
    "duplicate_in_file": ("The account number repeats an earlier row of the file", "رقم الحساب مكرر في الملف"),
    "formula_not_allowed": ("A mapped cell holds a formula; enter the value itself",
                            "خلية مربوطة تحتوي على معادلة؛ أدخل القيمة نفسها"),
    "value_too_long": ("A value is longer than allowed", "قيمة أطول من المسموح"),
    "archived_already_linked": ("This archived record was already replaced by a newer record",
                                "هذا السجل المؤرشف استُبدل سابقاً بسجل أحدث"),
}
WARNING_CODES = {
    "unknown_collector": ("No user has this username; imported unassigned",
                          "اسم المستخدم غير موجود؛ أُضيف العميل دون تعيين"),
    "inactive_collector": ("The collector's account is inactive; imported unassigned",
                           "حساب المحصل غير نشط؛ أُضيف العميل دون تعيين"),
    "ambiguous_collector": ("The username matches several users; imported unassigned",
                            "اسم المستخدم يطابق أكثر من مستخدم؛ أُضيف العميل دون تعيين"),
    "identity_changed": ("The identity number differs from the archived record",
                         "رقم الهوية في الملف يختلف عن السجل المؤرشف"),
    "contact_formula_skipped": ("A contact cell holding a formula was skipped",
                                "تم تجاهل خلية تواصل تحتوي على معادلة"),
}
OUTCOME_AR = {
    "created": "جديد",
    "created_from_archive": "جديد من الأرشيف",
    "skipped_active_exists": "متجاوز - موجود ونشط",
    "rejected": "مرفوض",
}
COLLECTOR_WARNINGS = ("unknown_collector", "inactive_collector", "ambiguous_collector")


class ImportRefused(Exception):
    """A business refusal inside the import job (the import becomes `failed` with this code)."""

    def __init__(self, code: str, message: str, counts: dict | None = None):
        super().__init__(message)
        self.code, self.message, self.counts = code, message, counts


# =========================================================================== text helpers
_AR_MARKS = re.compile("[ً-ْٰـ]")      # harakat, superscript alef, tatweel
_SEPARATORS = re.compile(r"[\s_\-.:/\\()#*]+")
_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
_NUMBER = re.compile(r"^\+?(\d+(\.\d*)?|\.\d+)$")


def norm_header(value) -> str:
    """Comparable form of a header: lower case, no separators, Arabic letter variants folded."""
    s = _AR_MARKS.sub("", str(value or "").strip().lower())
    s = s.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا").replace("ى", "ي").replace("ة", "ه")
    return _SEPARATORS.sub("", s)


_FIELD_GUESS = {f.key: {norm_header(x) for x in (f.key, f.label_en, f.label_ar, *f.guesses)} for f in FIELDS}
_SYNONYM_SETS = [frozenset(norm_header(x) for x in group) for group in CONTACT_SYNONYMS]


def clean(value) -> str | None:
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def parse_amount(raw: str) -> tuple[Decimal | None, str | None]:
    """A non-negative amount with at most 2 decimals. Arabic-Indic digits, the Arabic decimal
    separator and thousands separators (`,` `٬` spaces) are accepted."""
    s = raw.translate(_DIGITS).strip()
    for ch in (",", "٬", " ", " ", " ", "'"):
        s = s.replace(ch, "")
    s = s.replace("٫", ".")
    negative = s.startswith("-") or (s.startswith("(") and s.endswith(")"))
    if negative:
        s = s.strip("()").lstrip("-")
    if not _NUMBER.match(s):
        return None, "invalid_amount"
    try:
        d = Decimal(s)
    except InvalidOperation:
        return None, "invalid_amount"
    if negative and d != 0:
        return None, "negative_amount"
    q = d.quantize(CENT, rounding=ROUND_HALF_UP)
    if abs(d - q) > Decimal("0.000001"):      # float artifacts (1234.5600000001) are tolerated
        return None, "amount_too_precise"
    if q > MAX_AMOUNT:
        return None, "amount_too_large"
    return q, None


# =========================================================================== catalogue / guess
def contact_types(session) -> list[dict]:
    rows = session.execute(text("""
        SELECT id::text, name FROM "ClintContactTypes" WHERE is_deleted = false ORDER BY name, id
    """)).all()
    return [{"id": r[0], "name": r[1]} for r in rows]


def contact_type_for(header: str, types: list[dict]) -> str | None:
    """The contact type a header most likely holds (case-insensitive; see CONTACT_SYNONYMS)."""
    base = norm_header(re.sub(r"[\s_\-]*\d+$", "", str(header or "").strip()))
    if not base:
        return None
    for t in types:
        if norm_header(t["name"]) == base:
            return t["id"]
    group = next((g for g in _SYNONYM_SETS if base in g), None)
    if group is None:
        return None
    for t in types:
        if norm_header(t["name"]) in group:
            return t["id"]
    return None


def guess_mapping(headers: list[str], types: list[dict]) -> dict:
    fields, used = {}, set()
    normed = [(h, norm_header(h)) for h in headers]
    for f in FIELDS:
        hit = next((h for h, n in normed if n in _FIELD_GUESS[f.key] and h not in used), None)
        if hit is not None:
            fields[f.key] = hit
            used.add(hit)
    contacts = []
    for h, _n in normed:
        if h in used:
            continue
        tid = contact_type_for(h, types)
        if tid:
            contacts.append({"column": h, "contact_type_id": tid})
            used.add(h)
    return {"fields": fields, "contacts": contacts}


def catalogue(session, actor: str, upload_id=None, sheet=None) -> dict:
    types = contact_types(session)
    out = {"fields": [f.describe() for f in FIELDS], "contact_types": types,
           "attribute_note": "every column of the file is also kept in the case's additional fields"}
    if upload_id not in (None, ""):
        upload = sources.load_upload(session, upload_id, actor)
        _check_purpose(upload)
        sh = sources.sheet_info(upload, sheet or None)
        out["sheet"] = sh.get("name")
        out["headers"] = sh.get("headers") or []
        out["guess"] = guess_mapping(out["headers"], types)
    return out


# =========================================================================== upload
def create_upload(file_storage, actor: str) -> dict:
    """`POST /v2/portfolio-imports/uploads`: the Phase 2 private store and inspection, purpose
    `portfolio_import` (the /v2/uploads gate is the bulk permission set, not clints_add)."""
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
    out["guess"] = guess_mapping(first.get("headers") or [], contact_types(_s()))
    out["guess_sheet"] = first.get("name")
    return out


def _check_purpose(upload: dict) -> None:
    if upload["purpose"] != UPLOAD_PURPOSE:
        raise ApiError(409, "upload_purpose_mismatch",
                       f"this file was uploaded for {upload['purpose']}, not for a portfolio import",
                       {"purpose": upload["purpose"]})


# =========================================================================== request validation
def _portfolio_name(value) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ApiError(422, "validation_failed", "portfolio_name is required", {"portfolio_name": "required"})
    value = " ".join(value.split())
    if len(value) > NAME_MAX:
        raise ApiError(422, "validation_failed", "portfolio_name is too long", {"portfolio_name": "too_long"})
    return value


def _contract(session, value) -> dict:
    cid = valid_uuid(value)
    if cid is None:
        raise ApiError(422, "validation_failed", "contract_id is required",
                       {"contract_id": "required" if value in (None, "") else "invalid"})
    row = session.execute(text("SELECT id::text AS id, name, is_active FROM contracts WHERE id = CAST(:id AS uuid)"),
                          {"id": cid}).mappings().first()
    if row is None:
        raise ApiError(422, "validation_failed", "the contract does not exist", {"contract_id": "unknown"})
    if not row["is_active"]:
        raise ApiError(422, "validation_failed", "the contract is inactive", {"contract_id": "inactive"})
    return dict(row)


def validate_mapping(raw, headers: list[str], types: list[dict]) -> dict:
    """{"fields": {key: header}, "contacts": [{"column", "contact_type_id"}]} or 422 invalid_mapping."""
    if not isinstance(raw, dict):
        raise ApiError(422, "invalid_mapping", "mapping must be an object", {"mapping": "invalid"})
    fields = raw.get("fields") or {}
    contacts = raw.get("contacts") or []
    if not isinstance(fields, dict) or not isinstance(contacts, list):
        raise ApiError(422, "invalid_mapping", "mapping.fields must be an object and mapping.contacts a list",
                       {"mapping": "invalid"})
    header_set = set(headers)
    errors, out_fields, used = {}, {}, {}
    for k in fields:
        if k not in FIELD_KEYS:
            errors[str(k)] = "unknown_field"
    for f in FIELDS:
        col = fields.get(f.key)
        if col in (None, ""):
            if f.required:
                errors[f.key] = "required"
            continue
        if not isinstance(col, str) or col not in header_set:
            errors[f.key] = "unknown_column"
        elif col in used:
            errors[f.key] = "column_used_twice"
        else:
            used[col] = f.key
            out_fields[f.key] = col
    type_ids = {t["id"] for t in types}
    out_contacts = []
    for i, c in enumerate(contacts):
        key = f"contacts[{i}]"
        if not isinstance(c, dict):
            errors[key] = "invalid"
            continue
        col, tid = c.get("column"), valid_uuid(c.get("contact_type_id"))
        if not isinstance(col, str) or col not in header_set:
            errors[key] = "unknown_column"
        elif col in used:
            errors[key] = "column_used_twice"
        elif tid is None or tid not in type_ids:
            errors[key] = "unknown_contact_type"
        else:
            used[col] = key
            out_contacts.append({"column": col, "contact_type_id": tid})
    if errors:
        raise ApiError(422, "invalid_mapping", "the column mapping is not valid", {"mapping": errors})
    return {"fields": out_fields, "contacts": out_contacts}


def _validated(session, body: dict, actor: str) -> dict:
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
    contract = _contract(session, body.get("contract_id"))
    name = _portfolio_name(body.get("portfolio_name"))
    mapping = validate_mapping(body.get("mapping"), sheet.get("headers") or [], contact_types(session))
    return {"upload": upload, "sheet": sheet, "contract": contract, "portfolio_name": name, "mapping": mapping}


def portfolio_name_taken(session, contract_id: str, name: str) -> dict | None:
    """An existing portfolio of the contract with the same trimmed name (case-insensitive).
    Portfolios are hard-deleted (DELETE /portfolios/<id>); `is_deleted` rows do not count."""
    row = session.execute(text("""
        SELECT id::text AS id, name FROM portfolios
         WHERE contract_id = CAST(:cid AS uuid) AND is_deleted = false
           AND lower(btrim(name)) = lower(btrim(:name))
         ORDER BY create_at LIMIT 1
    """), {"cid": contract_id, "name": name}).mappings().first()
    return dict(row) if row else None


# =========================================================================== analysis
@dataclasses.dataclass
class RowPlan:
    row_number: int
    account: str | None = None
    name: str | None = None
    identity: str | None = None
    amount: Decimal | None = None
    collector: str | None = None
    contacts: list = dataclasses.field(default_factory=list)
    outcome: str | None = None          # None until decided
    code: str | None = None
    detail: str | None = None
    warnings: list = dataclasses.field(default_factory=list)
    case_id: str | None = None
    previous_case_id: str | None = None
    previous_identity: str | None = None
    renamed_to: str | None = None
    user_id: str | None = None

    def reject(self, code: str, detail: str | None = None):
        self.outcome, self.code, self.detail = "rejected", code, detail

    @property
    def creates(self) -> bool:
        return self.outcome in ("created", "created_from_archive")

    def message(self) -> str:
        base = ROW_CODES.get(self.code, (self.code, self.code))[1]
        if self.code == "created_from_archive" and self.renamed_to:
            base = f"{base} (رقم حسابه أصبح {self.renamed_to})"
        return f"{base} ({self.detail})" if self.detail else base

    def problem(self) -> dict:
        return {"row_number": self.row_number, "account_number": self.account, "outcome": self.outcome,
                "code": self.code, "message": self.message(), "detail": self.detail,
                "warnings": self.warnings, "case_id": self.case_id, "previous_case_id": self.previous_case_id}


@dataclasses.dataclass
class Analysis:
    plans: list
    counts: dict
    contacts_to_insert: list
    renames: list = dataclasses.field(default_factory=list)


def _iter_rows(upload: dict, sheet: dict):
    if upload["purged_at"] is not None or not storage.exists(upload["stored_path"]):
        raise ImportRefused("upload_purged", "the uploaded file was purged; upload it again")
    yield from _inspect.iter_data_rows(
        storage.resolve(upload["stored_path"]), upload["detected_type"], sheet.get("name"),
        int(sheet.get("header_row") or 1), sheet.get("headers") or [],
        encoding=upload["encoding"], delimiter=upload["delimiter"])


def _plan_row(row_no: int, raw: dict, formula_headers: set, mapping: dict) -> RowPlan:
    f = mapping["fields"]
    p = RowPlan(row_no)
    p.account = clean(raw.get(f["account_number"]))
    formula = next((k for k, col in f.items() if col in formula_headers), None)
    p.name = clean(raw.get(f["name"]))
    p.identity = clean(raw.get(f["identity_number"]))
    amount_raw = clean(raw.get(f["amount"]))
    if f.get("collector_username"):
        p.collector = clean(raw.get(f["collector_username"]))
    if formula is not None:
        p.reject("formula_not_allowed", formula)
    elif p.account is None:
        p.reject("missing_account_number")
    elif p.name is None:
        p.reject("missing_name")
    elif p.identity is None:
        p.reject("missing_identity_number")
    elif amount_raw is None:
        p.reject("missing_amount")
    elif len(p.account) > ACCOUNT_MAX or len(p.identity) > ACCOUNT_MAX or len(p.name) > TEXT_MAX:
        p.reject("value_too_long")
    else:
        p.amount, problem = parse_amount(amount_raw)
        if problem:
            p.reject(problem, amount_raw[:50])
    for c in mapping["contacts"]:
        v = clean(raw.get(c["column"]))
        if v is None:
            continue
        if c["column"] in formula_headers:
            p.warnings.append({"code": "contact_formula_skipped", "column": c["column"]})
            continue
        p.contacts.append((c["contact_type_id"], v[:TEXT_MAX]))
    return p


def _chunks(seq, size=IN_CHUNK):
    seq = list(seq)
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


def analyse(session, upload: dict, sheet: dict, mapping: dict) -> Analysis:
    """Validate every row and decide its outcome. Reads only; shared by preview and commit."""
    max_rows = _settings.get().max_rows
    plans = []
    for row_no, raw, formula_headers in _iter_rows(upload, sheet):
        plans.append(_plan_row(row_no, raw, formula_headers, mapping))
        if len(plans) > max_rows:
            raise ImportRefused("too_many_rows", f"the sheet has more than {max_rows} rows")

    # account numbers repeated inside the file: the first row wins, later rows are rejected
    first_row = {}
    for p in plans:
        if p.outcome is None:
            if p.account in first_row:
                p.reject("duplicate_in_file", f"الصف {first_row[p.account]}")
            else:
                first_row[p.account] = p.row_number

    # existing cases by account number (globally unique)
    existing = {}
    for part in _chunks(first_row):
        for r in session.execute(text("""
            SELECT id::text AS id, "accountNumber" AS account, is_archive, "identityNumber" AS identity
              FROM clints WHERE "accountNumber" = ANY(CAST(:a AS text[]))
        """), {"a": part}).mappings():
            existing[r["account"]] = dict(r)
    for p in plans:
        if p.outcome is not None:
            continue
        ex = existing.get(p.account)
        if ex is None:
            p.outcome, p.code = "created", "created"
        elif not ex["is_archive"]:
            p.outcome, p.code, p.case_id = "skipped_active_exists", "active_exists", ex["id"]
        else:
            p.outcome, p.code = "created_from_archive", "created_from_archive"
            p.previous_case_id, p.previous_identity = ex["id"], ex["identity"]
            if (ex["identity"] or "").strip() != p.identity:
                p.warnings.append({"code": "identity_changed", "previous": ex["identity"]})

    # an archived case that was already replaced by a newer case (its account number then
    # carries the -أرشيف suffix) cannot be linked a second time
    linked = set()
    for part in _chunks([p.previous_case_id for p in plans if p.outcome == "created_from_archive"]):
        linked.update(r[0] for r in session.execute(text("""
            SELECT previous_case_id::text FROM case_lineage WHERE previous_case_id = ANY(CAST(:ids AS uuid[]))
        """), {"ids": part}))
    for p in plans:
        if p.outcome == "created_from_archive" and p.previous_case_id in linked:
            p.warnings, p.previous_case_id = [], None
            p.reject("archived_already_linked")

    _match_collectors(session, plans)
    contacts_to_insert, existing_contacts = _plan_contacts(session, plans)
    counts = _counts(plans, contacts_to_insert, existing_contacts)
    return Analysis(plans, counts, contacts_to_insert)


def _match_collectors(session, plans) -> None:
    wanted = {p.collector.lower() for p in plans if p.creates and p.collector}
    by_login: dict[str, list] = {}
    for part in _chunks(wanted):
        for r in session.execute(text("""
            SELECT id::text AS id, lower(btrim(user_name)) AS login, is_active
              FROM users WHERE lower(btrim(user_name)) = ANY(CAST(:n AS text[]))
        """), {"n": part}).mappings():
            by_login.setdefault(r["login"], []).append(dict(r))
    for p in plans:
        if not (p.creates and p.collector):
            continue
        users = by_login.get(p.collector.lower(), [])
        active = [u for u in users if u["is_active"]]
        if len(active) == 1:
            p.user_id = active[0]["id"]
        elif len(active) > 1:
            p.warnings.append({"code": "ambiguous_collector", "value": p.collector})
        elif users:
            p.warnings.append({"code": "inactive_collector", "value": p.collector})
        else:
            p.warnings.append({"code": "unknown_collector", "value": p.collector})


def _plan_contacts(session, plans) -> tuple[list, int]:
    """(contacts to insert, number skipped because they already exist or repeat in the file)."""
    candidates = [(p.identity, v, tid) for p in plans if p.creates for tid, v in p.contacts]
    identities = sorted({c[0] for c in candidates})
    known = set()
    for part in _chunks(identities):
        for r in session.execute(text("""
            SELECT "identityNumber", btrim(contact) FROM "ClintContacts"
             WHERE "identityNumber" = ANY(CAST(:ids AS text[])) AND is_deleted = false
        """), {"ids": part}):
            known.add((r[0], r[1]))
    out, skipped = [], 0
    for identity, value, tid in candidates:
        if (identity, value) in known:
            skipped += 1
            continue
        known.add((identity, value))
        out.append({"identity": identity, "contact": value, "type_id": tid})
    return out, skipped


def _counts(plans, contacts_to_insert, existing_contacts) -> dict:
    c = {k: 0 for k in OUTCOMES}
    for p in plans:
        c[p.outcome] += 1
    warn = {}
    for p in plans:
        for w in p.warnings:
            warn[w["code"]] = warn.get(w["code"], 0) + 1
    return {
        "total": len(plans), **c,
        "to_create": c["created"] + c["created_from_archive"],
        "assigned": sum(1 for p in plans if p.creates and p.user_id),
        "collector_not_matched": sum(warn.get(k, 0) for k in COLLECTOR_WARNINGS),
        "warnings": warn,
        "contacts_new": len(contacts_to_insert),
        "contacts_existing": existing_contacts,
    }


def _problems(plans, limit=PROBLEM_LIMIT) -> list[dict]:
    out = []
    for p in plans:
        if p.outcome == "created" and not p.warnings:
            continue
        out.append(p.problem())
        if len(out) >= limit:
            break
    return out


# =========================================================================== preview
def preview(body: dict, actor: str) -> dict:
    """Validate the mapping and every row. Writes nothing."""
    session = _s()
    v = _validated(session, body, actor)
    try:
        a = analyse(session, v["upload"], v["sheet"], v["mapping"])
    except ImportRefused as e:
        raise ApiError(409 if e.code == "upload_purged" else 422, e.code, e.message) from None
    taken = portfolio_name_taken(session, v["contract"]["id"], v["portfolio_name"])
    existing = _import_for_upload(session, v["upload"]["id"])
    blocking = []
    if taken:
        blocking.append({"code": "portfolio_name_exists", "portfolio": taken})
    if a.counts["to_create"] == 0:
        blocking.append({"code": "nothing_to_import"})
    if existing and existing["status"] == "succeeded":
        blocking.append({"code": "already_imported", "import_id": str(existing["id"]),
                         "portfolio_id": None if existing["portfolio_id"] is None else str(existing["portfolio_id"])})
    session.rollback()
    return {"counts": a.counts, "problems": _problems(a.plans), "problem_limit": PROBLEM_LIMIT,
            "blocking": blocking, "can_commit": not blocking,
            "mapping": v["mapping"], "sheet": v["sheet"].get("name"),
            "import": serialize_import(existing) if existing else None}


# =========================================================================== commit (request side)
def _import_for_upload(session, upload_id, lock: bool = False) -> dict | None:
    sql = "SELECT * FROM portfolio_imports WHERE upload_id = CAST(:u AS uuid)" + (" FOR UPDATE NOWAIT" if lock else "")
    row = session.execute(text(sql), {"u": str(upload_id)}).mappings().first()
    return dict(row) if row else None


def _stale(imp: dict) -> bool:
    updated = imp["updated_at"]
    return (_dt.datetime.now(_dt.timezone.utc) - updated).total_seconds() > STALE_SECONDS


def start_commit(body: dict, actor: str) -> dict:
    """Queue the import (create or reuse THE import row of this upload). The caller commits and
    then dispatches `run_import` in the background. 409 when it already succeeded or is running."""
    session = _s()
    v = _validated(session, body, actor)
    upload_id = v["upload"]["id"]
    values = {"u": str(upload_id), "sheet": v["sheet"].get("name"), "cid": v["contract"]["id"],
              "name": v["portfolio_name"], "mapping": dumps(v["mapping"]), "by": actor}
    try:
        imp = _import_for_upload(session, upload_id, lock=True)
    except OperationalError as e:
        session.rollback()
        if getattr(getattr(e, "orig", None), "pgcode", None) == "55P03":
            raise ApiError(409, "import_in_progress", "this file is being imported right now") from None
        raise
    if imp is None:
        row = session.execute(text("""
            INSERT INTO portfolio_imports (upload_id, sheet, contract_id, portfolio_name, mapping, status,
                                           attempts, created_by)
            VALUES (CAST(:u AS uuid), :sheet, CAST(:cid AS uuid), :name, CAST(:mapping AS jsonb), 'queued', 1,
                    CAST(:by AS uuid))
            ON CONFLICT (upload_id) DO NOTHING
            RETURNING *
        """), values).mappings().first()
        if row is None:     # a concurrent first commit of the same file won the insert
            raise ApiError(409, "import_in_progress", "this file is being imported right now")
        imp = dict(row)
    else:
        if imp["status"] == "succeeded":
            raise ApiError(409, "already_imported", "this file was already imported",
                           {"import_id": str(imp["id"]), "portfolio_id": None if imp["portfolio_id"] is None
                            else str(imp["portfolio_id"])})
        if imp["status"] in ("queued", "running") and not _stale(imp):
            raise ApiError(409, "import_in_progress", "this file is being imported right now",
                           {"import_id": str(imp["id"])})
        imp = dict(session.execute(text("""
            UPDATE portfolio_imports
               SET sheet = :sheet, contract_id = CAST(:cid AS uuid), portfolio_name = :name,
                   mapping = CAST(:mapping AS jsonb), status = 'queued', attempts = attempts + 1,
                   error_code = NULL, error_message = NULL, started_at = NULL, finished_at = NULL,
                   updated_at = now()
             WHERE id = CAST(:id AS uuid)
            RETURNING *
        """), {**values, "id": str(imp["id"])}).mappings().one())
    taken = portfolio_name_taken(session, v["contract"]["id"], v["portfolio_name"])
    if taken:
        raise ApiError(409, "portfolio_name_exists", "a portfolio with this name already exists in the contract",
                       {"portfolio": taken})
    audit.record("portfolio_import.queue", "portfolio_import", imp["id"],
                 after={"upload_id": str(upload_id), "contract_id": v["contract"]["id"],
                        "portfolio_name": v["portfolio_name"], "mapping": v["mapping"], "attempt": imp["attempts"]})
    return serialize_import(imp)


# =========================================================================== commit (job side)
def run_import(import_id: str) -> dict:
    """Background job body. Claims a queued import, then writes everything in ONE transaction.
    On any failure that transaction is rolled back and the import is marked failed separately."""
    session = _s()
    row = session.execute(text("SELECT * FROM portfolio_imports WHERE id = CAST(:id AS uuid) FOR UPDATE"),
                          {"id": str(import_id)}).mappings().first()
    if row is None or row["status"] != "queued":
        session.rollback()
        return {"status": None if row is None else row["status"], "skipped": True}
    session.execute(text("""UPDATE portfolio_imports SET status = 'running', started_at = now(), updated_at = now()
                             WHERE id = CAST(:id AS uuid)"""), {"id": str(import_id)})
    session.commit()
    try:
        result = _execute(session, str(import_id))
        session.commit()
        return result
    except Exception as e:  # noqa: BLE001 - every failure leaves nothing behind
        session.rollback()
        if isinstance(e, ImportRefused):
            code, message, counts = e.code, e.message, e.counts
        elif isinstance(e, ApiError):
            code, message, counts = e.code, e.message, None
        else:
            log.exception("portfolio import %s failed", import_id)
            code, message, counts = "import_failed", (str(e).splitlines() or ["error"])[0][:500], None
        session.execute(text("""
            UPDATE portfolio_imports
               SET status = 'failed', error_code = :code, error_message = :msg, finished_at = now(),
                   updated_at = now(), counts = coalesce(CAST(:counts AS jsonb), counts)
             WHERE id = CAST(:id AS uuid)
        """), {"id": str(import_id), "code": code, "msg": message, "counts": dumps(counts)})
        imp = session.execute(text("SELECT created_by FROM portfolio_imports WHERE id = CAST(:id AS uuid)"),
                              {"id": str(import_id)}).first()
        audit.record("portfolio_import.fail", "portfolio_import", import_id, after={"code": code, "message": message},
                     actor_user_id=imp[0] if imp else None, request_id=str(import_id), idempotency_key=None,
                     source="portfolio_import")
        session.commit()
        return {"status": "failed", "error": {"code": code, "message": message}}


def _set_contact_audit_context(session, actor: str, import_id: str) -> None:
    session.execute(text("""
        SELECT set_config('cawp_hist.actor', :actor, true), set_config('cawp_hist.action', 'contact.import', true),
               set_config('cawp_hist.reason', '', true), set_config('cawp_hist.request_id', :rid, true),
               set_config('cawp_hist.idempotency_key', '', true),
               set_config('cawp_hist.source', 'portfolio_import', true)
    """), {"actor": actor, "rid": import_id})


def _execute(session, import_id: str) -> dict:
    imp = dict(session.execute(text("SELECT * FROM portfolio_imports WHERE id = CAST(:id AS uuid) FOR UPDATE"),
                               {"id": import_id}).mappings().one())
    if imp["status"] != "running":
        raise ImportRefused("not_running", "the import is not running")
    actor = str(imp["created_by"])
    upload = sources.load_upload(session, imp["upload_id"])
    sources.upload_usable(upload)
    sheet = sources.sheet_info(upload, imp["sheet"])
    mapping = validate_mapping(imp["mapping"], sheet.get("headers") or [], contact_types(session))
    contract = _contract(session, imp["contract_id"])
    name = imp["portfolio_name"]

    # one writer per (contract, name): two imports with the same name cannot both pass the check
    session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
                    {"k": f"portfolio_name:{contract['id']}:{name.strip().lower()}"})
    if portfolio_name_taken(session, contract["id"], name):
        raise ImportRefused("portfolio_name_exists", "a portfolio with this name already exists in the contract")

    a = analyse(session, upload, sheet, mapping)
    _lock_archived(session, a)
    a.contacts_to_insert, existing_contacts = _plan_contacts(session, a.plans)
    a.counts = _counts(a.plans, a.contacts_to_insert, existing_contacts)
    if a.counts["to_create"] == 0:
        raise ImportRefused("nothing_to_import", "no row of the file can be created", a.counts)

    _set_contact_audit_context(session, actor, import_id)
    portfolio_id = session.execute(text("""
        INSERT INTO portfolios (name, contract_id) VALUES (:name, CAST(:cid AS uuid)) RETURNING id::text
    """), {"name": name, "cid": contract["id"]}).scalar_one()

    _rename_archived(session, a)
    _insert_cases(session, a, upload, sheet, portfolio_id)
    _insert_assignments(session, a, actor)
    _insert_lineage(session, a, actor, import_id)
    _insert_contacts(session, a)
    _insert_report(session, a, import_id)
    _audit_success(session, a, imp, actor, portfolio_id)
    session.execute(text("""
        UPDATE portfolio_imports
           SET status = 'succeeded', portfolio_id = CAST(:pid AS uuid), counts = CAST(:counts AS jsonb),
               error_code = NULL, error_message = NULL, finished_at = now(), updated_at = now()
         WHERE id = CAST(:id AS uuid)
    """), {"id": import_id, "pid": portfolio_id, "counts": dumps(a.counts)})
    return {"status": "succeeded", "portfolio_id": portfolio_id, "counts": a.counts}


def _lock_archived(session, a: Analysis) -> None:
    """Lock the archived cases about to be renamed and recheck them: a case restored meanwhile
    is now an active case (skipped), a case renamed meanwhile is simply new."""
    ids = [p.previous_case_id for p in a.plans if p.outcome == "created_from_archive"]
    current = {}
    for part in _chunks(ids):
        for r in session.execute(text("""
            SELECT id::text AS id, "accountNumber" AS account, is_archive FROM clints
             WHERE id = ANY(CAST(:ids AS uuid[])) ORDER BY id FOR UPDATE
        """), {"ids": part}).mappings():
            current[r["id"]] = dict(r)
    for p in a.plans:
        if p.outcome != "created_from_archive":
            continue
        c = current.get(p.previous_case_id)
        if c is None or c["account"] != p.account:
            raise ImportRefused("cases_changed", "a matching case changed during the import; try again")
        if not c["is_archive"]:
            p.outcome, p.code, p.case_id, p.previous_case_id = "skipped_active_exists", "active_exists", c["id"], None
            p.warnings = [w for w in p.warnings if w["code"] != "identity_changed"]
            p.user_id = None


def _rename_archived(session, a: Analysis) -> None:
    todo = [p for p in a.plans if p.outcome == "created_from_archive"]
    if not todo:
        return
    taken = {p.account for p in a.plans if p.account}
    start = 1
    pending = todo
    while pending:
        cands = [f"{p.account}{ARCHIVE_SUFFIX}{n}" for p in pending for n in range(start, start + RENAME_PROBE)]
        for part in _chunks(cands):
            taken.update(r[0] for r in session.execute(text("""
                SELECT "accountNumber" FROM clints WHERE "accountNumber" = ANY(CAST(:a AS text[]))
            """), {"a": part}))
        left = []
        for p in pending:
            for n in range(start, start + RENAME_PROBE):
                cand = f"{p.account}{ARCHIVE_SUFFIX}{n}"
                if cand not in taken:
                    p.renamed_to = cand
                    taken.add(cand)
                    break
            else:
                left.append(p)
        pending, start = left, start + RENAME_PROBE
    for part in _chunks(todo, BATCH):
        data = [{"id": p.previous_case_id, "old": p.account, "new": p.renamed_to} for p in part]
        n = session.execute(text("""
            UPDATE clints c SET "accountNumber" = d.new, update_at = now()
              FROM jsonb_to_recordset(CAST(:rows AS jsonb)) AS d(id uuid, old text, new text)
             WHERE c.id = d.id AND c."accountNumber" = d.old AND c.is_archive
        """), {"rows": dumps(data)}).rowcount
        if n != len(data):
            raise ImportRefused("cases_changed", "a matching case changed during the import; try again")


def _insert_cases(session, a: Analysis, upload, sheet, portfolio_id) -> None:
    """Second pass over the file: the full row becomes `attribute`, bulk-inserted per batch."""
    by_row = {p.row_number: p for p in a.plans if p.creates}
    buf: list = []

    def flush():
        if not buf:
            return
        data = [{"n": p.name, "i": p.identity, "a": p.account, "m": str(p.amount), "u": p.user_id, "attr": attr}
                for p, attr in buf]
        rows = session.execute(text("""
            INSERT INTO clints (name, "identityNumber", "accountNumber", amount, attribute, user_id, portfolios_id)
            SELECT d.n, d.i, d.a, CAST(d.m AS numeric)::double precision, d.attr, d.u, CAST(:pid AS uuid)
              FROM jsonb_to_recordset(CAST(:rows AS jsonb)) AS d(n text, i text, a text, m text, u uuid, attr jsonb)
            RETURNING id::text, "accountNumber"
        """), {"rows": dumps(data), "pid": portfolio_id}).all()
        ids = {r[1]: r[0] for r in rows}
        for p, _attr in buf:
            p.case_id = ids[p.account]
        buf.clear()

    for row_no, raw, _f in _iter_rows(upload, sheet):
        p = by_row.get(row_no)
        if p is None:
            continue
        buf.append((p, dict(raw)))
        if len(buf) >= BATCH:
            flush()
    flush()
    missing = [p.row_number for p in by_row.values() if p.case_id is None]
    if missing:
        raise ImportRefused("file_changed", "the file could not be read the same way twice")


def _insert_assignments(session, a: Analysis, actor: str) -> None:
    data = [{"c": p.case_id, "u": p.user_id} for p in a.plans if p.creates and p.user_id]
    for part in _chunks(data, BATCH):
        session.execute(text("""
            INSERT INTO "SepcifyUsers" (user_id, clint_id, create_by)
            SELECT d.u, d.c, CAST(:actor AS uuid) FROM jsonb_to_recordset(CAST(:rows AS jsonb)) AS d(c uuid, u uuid)
        """), {"rows": dumps(part), "actor": actor})


def _insert_lineage(session, a: Analysis, actor: str, import_id: str) -> None:
    data = [{"c": p.case_id, "p": p.previous_case_id, "a": p.account, "r": p.renamed_to}
            for p in a.plans if p.outcome == "created_from_archive"]
    for part in _chunks(data, BATCH):
        session.execute(text("""
            INSERT INTO case_lineage (case_id, previous_case_id, previous_account_number, renamed_to, import_id,
                                      created_by)
            SELECT d.c, d.p, d.a, d.r, CAST(:imp AS uuid), CAST(:actor AS uuid)
              FROM jsonb_to_recordset(CAST(:rows AS jsonb)) AS d(c uuid, p uuid, a text, r text)
        """), {"rows": dumps(part), "imp": import_id, "actor": actor})


def _insert_contacts(session, a: Analysis) -> None:
    for part in _chunks(a.contacts_to_insert, BATCH):
        session.execute(text("""
            INSERT INTO "ClintContacts" (contact_type_id, "identityNumber", contact_status, contact)
            SELECT d.type_id, d.identity, 'new', d.contact
              FROM jsonb_to_recordset(CAST(:rows AS jsonb)) AS d(type_id uuid, identity text, contact text)
        """), {"rows": dumps(part)})


def _insert_report(session, a: Analysis, import_id: str) -> None:
    data = [{"n": p.row_number, "a": p.account, "o": p.outcome, "c": p.code, "m": p.message(),
             "case": p.case_id, "prev": p.previous_case_id, "w": p.warnings} for p in a.plans]
    for part in _chunks(data, BATCH):
        session.execute(text("""
            INSERT INTO portfolio_import_rows (import_id, row_number, account_number, outcome, code, message,
                                               case_id, previous_case_id, warnings)
            SELECT CAST(:imp AS uuid), d.n, d.a, d.o, d.c, d.m, d.case, d.prev, coalesce(d.w, '[]'::jsonb)
              FROM jsonb_to_recordset(CAST(:rows AS jsonb))
                   AS d(n integer, a text, o text, c text, m text, "case" uuid, prev uuid, w jsonb)
        """), {"rows": dumps(part), "imp": import_id})


_AUDIT_MANY = text("""
    INSERT INTO audit_events (actor_user_id, action, entity_type, entity_id, case_id, before, after,
                              reason, request_id, idempotency_key, source)
    SELECT CAST(:actor AS uuid), e->>'action', 'case', e->>'case_id', CAST(e->>'case_id' AS uuid),
           e->'before', e->'after', NULL, :rid, NULL, 'portfolio_import'
      FROM jsonb_array_elements(CAST(:events AS jsonb)) e
""")


def _audit_success(session, a: Analysis, imp: dict, actor: str, portfolio_id: str) -> None:
    """One event per import, plus one per archived case renamed and linked (both sides)."""
    events = []
    for p in a.plans:
        if p.outcome != "created_from_archive":
            continue
        events.append({"action": "case.archive_relinked", "case_id": p.previous_case_id,
                       "before": {"accountNumber": p.account},
                       "after": {"accountNumber": p.renamed_to, "replaced_by_case_id": p.case_id}})
        events.append({"action": "case.created_from_archive", "case_id": p.case_id,
                       "before": None, "after": {"accountNumber": p.account, "previous_case_id": p.previous_case_id,
                                                 "previous_renamed_to": p.renamed_to}})
    for part in _chunks(events, BATCH):
        session.execute(_AUDIT_MANY, {"events": dumps(part), "actor": actor, "rid": str(imp["id"])})
    audit.record("portfolio_import.succeed", "portfolio_import", imp["id"],
                 after={"portfolio_id": portfolio_id, "portfolio_name": imp["portfolio_name"],
                        "contract_id": str(imp["contract_id"]), "counts": a.counts},
                 actor_user_id=actor, request_id=str(imp["id"]), idempotency_key=None, source="portfolio_import")


# =========================================================================== reads
def serialize_import(imp: dict, extra: dict | None = None) -> dict:
    out = {
        "id": str(imp["id"]), "upload_id": str(imp["upload_id"]), "sheet": imp["sheet"],
        "contract_id": str(imp["contract_id"]), "portfolio_name": imp["portfolio_name"],
        "portfolio_id": None if imp["portfolio_id"] is None else str(imp["portfolio_id"]),
        "mapping": imp["mapping"], "status": imp["status"], "counts": imp["counts"] or {},
        "error": None if imp["error_code"] is None else {"code": imp["error_code"], "message": imp["error_message"]},
        "attempts": imp["attempts"], "created_by": str(imp["created_by"]),
        "created_at": iso(imp["created_at"]), "updated_at": iso(imp["updated_at"]),
        "started_at": iso(imp["started_at"]), "finished_at": iso(imp["finished_at"]),
        "report_available": imp["status"] == "succeeded",
    }
    if extra:
        out.update(extra)
    return out


def get_import(import_id, actor: str, scope) -> dict:
    """The import, visible to its creator and to SuperAdmin (404 otherwise)."""
    from services.clients_scope import SUPER
    iid = valid_uuid(import_id)
    row = None if iid is None else _s().execute(text("""
        SELECT i.*, u.original_name, ct.name AS contract_name, pf.name AS portfolio_current_name
          FROM portfolio_imports i
          JOIN bulk_uploads u ON u.id = i.upload_id
          LEFT JOIN contracts ct ON ct.id = i.contract_id
          LEFT JOIN portfolios pf ON pf.id = i.portfolio_id
         WHERE i.id = CAST(:id AS uuid)
    """), {"id": iid}).mappings().first()
    if row is None or not (str(row["created_by"]) == str(actor) or scope.kind == SUPER):
        raise ApiError(404, "import_not_found", "import not found", {"import_id": str(import_id)})
    return dict(row)


def import_detail(import_id, actor: str, scope) -> dict:
    r = get_import(import_id, actor, scope)
    return serialize_import(r, {"original_name": r["original_name"], "contract_name": r["contract_name"],
                                "portfolio": None if r["portfolio_id"] is None else
                                {"id": str(r["portfolio_id"]), "name": r["portfolio_current_name"]}})


def import_rows(import_id, actor: str, scope, args) -> dict:
    get_import(import_id, actor, scope)
    outcome = (args.get("outcome") or "").strip() or None
    if outcome is not None and outcome not in OUTCOMES:
        raise ApiError(422, "validation_failed", "unknown outcome", {"outcome": "invalid", "allowed": list(OUTCOMES)})
    try:
        page = max(1, int(args.get("page", 1)))
        size = min(200, max(1, int(args.get("page_size", 50))))
    except (TypeError, ValueError):
        raise ApiError(422, "validation_failed", "page and page_size must be integers", {"page": "invalid"}) from None
    where = "import_id = CAST(:id AS uuid)" + (" AND outcome = :o" if outcome else "")
    params = {"id": str(import_id), "o": outcome}
    total = _s().execute(text(f"SELECT count(*) FROM portfolio_import_rows WHERE {where}"), params).scalar()
    rows = _s().execute(text(f"""
        SELECT row_number, account_number, outcome, code, message, case_id::text, previous_case_id::text, warnings
          FROM portfolio_import_rows WHERE {where}
         ORDER BY row_number LIMIT :lim OFFSET :off
    """), {**params, "lim": size, "off": (page - 1) * size}).mappings().all()
    return {"items": [dict(r) for r in rows], "total": int(total), "page": page, "page_size": size}


REPORT_HEADERS = ("رقم الصف", "رقم الحساب", "النتيجة", "الرمز", "التفاصيل", "تنبيهات", "رقم الحساب المؤرشف بعد التغيير")


def _warnings_text(warnings) -> str:
    parts = []
    for w in warnings or []:
        label = WARNING_CODES.get(w.get("code"), (w.get("code"), w.get("code")))[1]
        value = w.get("value") or w.get("previous") or w.get("column")
        parts.append(f"{label}: {value}" if value else label)
    return "؛ ".join(parts)


def build_report(import_id, actor: str, scope, dest_dir: str) -> tuple[str, str]:
    """XLSX of the import's rows with Arabic labels -> (path, download name)."""
    from core.reports.xlsx import Workbook
    imp = get_import(import_id, actor, scope)
    if imp["status"] != "succeeded":
        raise ApiError(409, "report_not_ready", "the report exists only for a completed import",
                       {"status": imp["status"]})
    renamed = {str(r[0]): r[1] for r in _s().execute(text("""
        SELECT case_id, renamed_to FROM case_lineage WHERE import_id = CAST(:id AS uuid)
    """), {"id": str(import_id)})}
    path = os.path.join(dest_dir, f"portfolio-import-{str(import_id)[:8]}.xlsx")
    wb = Workbook(path, rtl=True)
    sh = wb.add_sheet("تقرير الرفع", freeze_header=True)
    sh.append([("s", h) for h in REPORT_HEADERS], header=True)
    result = _s().execute(text("""
        SELECT row_number, account_number, outcome, code, message, warnings, case_id::text
          FROM portfolio_import_rows WHERE import_id = CAST(:id AS uuid) ORDER BY row_number
    """), {"id": str(import_id)}).all()
    for r in result:
        sh.append([("int", r[0]), ("s", r[1]), ("s", OUTCOME_AR.get(r[2], r[2])), ("s", r[3]), ("s", r[4]),
                   ("s", _warnings_text(r[5]) or None), ("s", renamed.get(r[6]))])
    summary = wb.add_sheet("الملخص")
    counts = imp["counts"] or {}
    summary.append([("s", "البند"), ("s", "العدد")], header=True)
    for key, label in (("total", "إجمالي الصفوف"), ("created", OUTCOME_AR["created"]),
                       ("created_from_archive", OUTCOME_AR["created_from_archive"]),
                       ("skipped_active_exists", OUTCOME_AR["skipped_active_exists"]),
                       ("rejected", OUTCOME_AR["rejected"]), ("assigned", "تم تعيين محصل"),
                       ("collector_not_matched", "لم يُطابق اسم المحصل"),
                       ("contacts_new", "أرقام تواصل جديدة"), ("contacts_existing", "أرقام تواصل موجودة مسبقاً")):
        summary.append([("s", label), ("int", int(counts.get(key) or 0))])
    wb.save()
    return path, f"portfolio-import-{str(import_id)[:8]}.xlsx"


# =========================================================================== previous records
SECTION_KEYS = {
    "notes": ("clints_details", "clints_comments"),
    "payments": ("clints_Payments", "accounts_payments", "payments_approve", "payments_reverse"),
    "promises": ("promises", "clints_PayemntDates"),
    "follow_ups": ("follow_ups",),
}


def previous_records(case_id, has_permission) -> dict:
    """The lineage chain of a case (previous, previous of previous, ...) with each previous
    record's notes, payments (with their approval state), promises and follow-ups. Read-only;
    the caller has already been authorized for the NEW case. Nothing is summed as recovered."""
    session = _s()
    chain = session.execute(text(f"""
        WITH RECURSIVE chain AS (
            SELECT l.case_id, l.previous_case_id, l.previous_account_number, l.renamed_to, l.import_id,
                   l.created_at, 1 AS depth
              FROM case_lineage l WHERE l.case_id = CAST(:cid AS uuid)
            UNION ALL
            SELECT l.case_id, l.previous_case_id, l.previous_account_number, l.renamed_to, l.import_id,
                   l.created_at, ch.depth + 1
              FROM case_lineage l JOIN chain ch ON l.case_id = ch.previous_case_id
             WHERE ch.depth < {LINEAGE_MAX_DEPTH}
        )
        SELECT ch.*, c.id IS NOT NULL AS present, c.name, c."accountNumber" AS account_now, c."identityNumber",
               round(c.amount::numeric, 2) AS original_amount, c.is_archive, c.create_at AS case_created_at,
               pf.id::text AS portfolio_id, pf.name AS portfolio_name, ct.id::text AS contract_id,
               ct.name AS contract_name, ca.create_at AS archived_at, ar.name AS archive_reason,
               ca.user_id::text AS archived_by_id,
               btrim(coalesce(au.frist_name, '') || ' ' || coalesce(au.last_name, '')) AS archived_by_name
          FROM chain ch
          LEFT JOIN clints c ON c.id = ch.previous_case_id
          LEFT JOIN portfolios pf ON pf.id = c.portfolios_id
          LEFT JOIN contracts ct ON ct.id = pf.contract_id
          LEFT JOIN "clintArchive" ca ON ca.id = c."clintArchive_id"
          LEFT JOIN "archiveReason" ar ON ar.id = ca.archive_reason_id
          LEFT JOIN users au ON au.id = ca.user_id
         ORDER BY ch.depth
    """), {"cid": str(case_id)}).mappings().all()
    allowed = {k: any(has_permission(x) for x in keys) for k, keys in SECTION_KEYS.items()}
    records = []
    for r in chain:
        prev = str(r["previous_case_id"])
        rec = {
            "depth": r["depth"], "case_id": prev, "linked_from_case_id": str(r["case_id"]),
            "exists": bool(r["present"]), "name": r["name"],
            "account_number": r["account_now"], "original_account_number": r["previous_account_number"],
            "renamed_to": r["renamed_to"], "identity_number": r["identityNumber"],
            "original_amount": None if r["original_amount"] is None else str(r["original_amount"]),
            "is_archived": r["is_archive"], "created_at": iso(r["case_created_at"]),
            "linked_at": iso(r["created_at"]), "import_id": None if r["import_id"] is None else str(r["import_id"]),
            "portfolio": None if r["portfolio_id"] is None else {"id": r["portfolio_id"], "name": r["portfolio_name"]},
            "contract": None if r["contract_id"] is None else {"id": r["contract_id"], "name": r["contract_name"]},
            "archive": None if r["archived_at"] is None else {
                "at": iso(r["archived_at"]), "reason": r["archive_reason"],
                "by": None if r["archived_by_id"] is None else {"id": r["archived_by_id"],
                                                                "name": r["archived_by_name"] or None}},
        }
        if r["present"]:
            rec.update(_record_sections(session, prev, allowed))
        else:
            rec.update({k: None for k in SECTION_KEYS})
        records.append(rec)
    return {"case_id": str(case_id), "has_previous": bool(records), "permitted": allowed, "records": records}


def _record_sections(session, case_id: str, allowed: dict) -> dict:
    out = {}
    if allowed["notes"]:
        from services.case_history_notes import _NOTE_SELECT, _note_json
        rows = session.execute(text(_NOTE_SELECT + """
            WHERE m.clint_id = CAST(:cid AS uuid) AND m.is_deleted = false
            ORDER BY m.create_at DESC, m.id DESC LIMIT 500"""), {"cid": case_id}).mappings().all()
        out["notes"] = [_note_json(r) for r in rows]
    else:
        out["notes"] = None
    if allowed["payments"]:
        from services import money
        items = money.case_payments(case_id)["items"]
        for i in items:
            i.pop("changes", None)
        out["payments"] = items
        counts = {}
        for i in items:
            counts[i["status"]] = counts.get(i["status"], 0) + 1
        out["payment_counts"] = counts       # counts only: previous money is never summed as recovered here
    else:
        out["payments"] = None
    if allowed["promises"]:
        from services import money_promises
        out["promises"] = money_promises.case_promises(case_id)["items"]
    else:
        out["promises"] = None
    if allowed["follow_ups"]:
        import sqlalchemy as sa
        from services import worklist_common as wc
        from services.worklist_follow_ups import FU, follow_up_dict
        day = wc.Day()
        rows = session.execute(sa.select(FU).where(FU.c.case_id == case_id)
                               .order_by(FU.c.created_at.desc(), FU.c.id.desc())).mappings().all()
        out["follow_ups"] = [follow_up_dict(r, day) for r in rows]
    else:
        out["follow_ups"] = None
    return out
