"""Payment attachments: a receipt / transfer slip on a payment (db/migrations/0069).

Customer request "السدادات: إرفاق مستند عند تسجيل سداد"; the attachment is optional. The
collector records the payment as before (legacy POST /payment) and then uploads the file here.

Storage is exactly the client-document storage: the file goes to <UPLOAD_DIR>/documents under a
random name (core/files.document_path) with a "clint_documents" row of the payment's case, so it
also shows in the case's Documents tab; `payment_attachments` links that row to the payment.

Rules:

* The payment's case must be in the caller's scope (services.money.case_in_scope); outside it
  the payment answers 404 like a missing one. A deleted or reversed payment takes no new file
  (409). A locked case refuses adds and removals (423), as the legacy `check_client_lock` does
  for recording the payment.
* Only PDF / JPEG / PNG, recognised from the first bytes of the file (the extension must agree),
  at most MAX_BYTES (checked while the upload is received) and MAX_PER_PAYMENT live files.
* Removal: only while the payment is pending, by the person who attached the file or an
  `accounts_payments` holder. The link row is kept (removed_at/removed_by) and the document is
  soft-deleted.
* A document deleted from the Documents tab simply stops being listed here.

This module commits its own writes (the file on disk must be removed when the commit fails).
"""
from __future__ import annotations

import os
import uuid

from sqlalchemy import text

from core import audit, files
from core.api_response import ApiError
from services import money

MAX_BYTES = 10 * 1024 * 1024
MAX_PER_PAYMENT = 5
NAME_MAX = 200
ORIGINAL_NAME_MAX = 255

# extension -> (stored file_type, content type); the same set as the legacy client documents
TYPES = {
    ".pdf": ("pdf", "application/pdf"),
    ".jpg": ("jpg", "image/jpeg"),
    ".jpeg": ("jpeg", "image/jpeg"),
    ".png": ("png", "image/png"),
}
MIME_BY_TYPE = {ft: mime for ft, mime in TYPES.values()}
MAGIC = {
    "application/pdf": (b"%PDF-",),
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/png": (b"\x89PNG\r\n\x1a\n",),
}


def _s():
    from extensions import db
    return db.session


def _not_found(payment_id):
    code, message, error = money.payment_not_found(payment_id)
    return ApiError(404, code, message, error)


def _attachment_not_found(payment_id, document_id):
    return ApiError(404, "attachment_not_found", "attachment not found",
                    {"payment_id": str(payment_id), "document_id": str(document_id)})


_PAYMENT_SQL = """
    SELECT p.id, p.clint_id, round(p.amount::numeric, 2) AS amount,
           (p.payment_date AT TIME ZONE 'Asia/Riyadh')::date AS civil_date,
           p.approval_status, p.is_deleted, c.is_locked,
           EXISTS (SELECT 1 FROM payment_reversals r WHERE r.payment_id = p.id) AS reversed
      FROM "Payments" p JOIN clints c ON c.id = p.clint_id
     WHERE p.id = CAST(:pid AS uuid)
"""


def _status(row) -> str:
    if row["reversed"]:
        return "reversed"
    if row["is_deleted"]:
        return "deleted"
    if row["approval_status"] is None:
        return "pending"
    return "approved" if row["approval_status"] else "rejected"


def _payment(payment_id, scope, lock: bool = False):
    """The payment with its case flags, or 404 (missing or outside the caller's scope)."""
    sql = _PAYMENT_SQL + (" FOR NO KEY UPDATE OF p" if lock else "")
    row = _s().execute(text(sql), {"pid": str(payment_id)}).mappings().first()
    if row is None or not money.case_in_scope(row["clint_id"], scope):
        raise _not_found(payment_id)
    return row


def _writable(row):
    status = _status(row)
    if status == "reversed":
        raise ApiError(409, "payment_reversed", "payment was reversed", {"status": status})
    if status == "deleted":
        raise ApiError(409, "payment_deleted", "payment is deleted", {"status": status})
    if row["is_locked"]:
        raise ApiError(423, "case_locked", "العميل مقفل ولا يمكن تنفيذ هذا الإجراء",
                       {"case_id": str(row["clint_id"])})


def _items(payment_id) -> list[dict]:
    raw = _s().execute(text(f'SELECT {money.ATTACHMENTS_SQL} AS a FROM "Payments" p '
                            "WHERE p.id = CAST(:pid AS uuid)"), {"pid": str(payment_id)}).scalar()
    return money.attachment_items(payment_id, raw)


def _live_count(payment_id) -> int:
    return _s().execute(text("""
        SELECT count(*) FROM payment_attachments a
          JOIN clint_documents d ON d.id = a.document_id AND d.is_deleted = false
         WHERE a.payment_id = CAST(:pid AS uuid) AND a.removed_at IS NULL
    """), {"pid": str(payment_id)}).scalar()


def _display_name(form, row) -> str:
    raw = form.get("name") if form is not None else None
    if raw is not None and not isinstance(raw, str):
        raise ApiError(422, "validation_failed", "name must be text", {"name": "invalid"})
    name = (raw or "").strip()
    if len(name) > NAME_MAX:
        raise ApiError(422, "validation_failed", "name is too long", {"name": "too_long"})
    return name or f"إيصال سداد {row['civil_date'].isoformat()} {money.money(row['amount'])}"


def _original_name(filename: str) -> str:
    base = os.path.basename(str(filename).replace("\\", "/")).strip() or "receipt"
    if len(base) > ORIGINAL_NAME_MAX:
        stem, ext = os.path.splitext(base)
        base = stem[: ORIGINAL_NAME_MAX - len(ext)] + ext
    return base


def _check_magic(path: str, mime: str):
    with open(path, "rb") as f:
        head = f.read(16)
    if not any(head.startswith(m) for m in MAGIC[mime]):
        raise ApiError(415, "type_mismatch", "the file content does not match its type (PDF, JPEG or PNG)",
                       {"file": "type_mismatch"})


def _remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


# --------------------------------------------------------------------------- reads
def list_attachments(payment_id, scope) -> dict:
    row = _payment(payment_id, scope)
    return {"payment_id": str(payment_id), "payment_status": _status(row),
            "items": _items(payment_id), "max_attachments": MAX_PER_PAYMENT, "max_bytes": MAX_BYTES}


def case_attachments(case_id, scope) -> dict:
    """{payment_id: [attachments]} for every payment of the case that has any."""
    money.require_case(case_id, scope)
    rows = _s().execute(text(f"""
        SELECT p.id, {money.ATTACHMENTS_SQL} AS a
          FROM "Payments" p
         WHERE p.clint_id = CAST(:cid AS uuid)
           AND EXISTS (SELECT 1 FROM payment_attachments x
                        WHERE x.payment_id = p.id AND x.removed_at IS NULL)
    """), {"cid": str(case_id)}).mappings().all()
    out = {}
    for r in rows:
        items = money.attachment_items(r["id"], r["a"])
        if items:
            out[str(r["id"])] = items
    return {"case_id": str(case_id), "payments": out}


def download(payment_id, document_id, scope) -> tuple[str, str, str]:
    """(path, download name, content type) of a live attachment."""
    _payment(payment_id, scope)
    row = _s().execute(text("""
        SELECT d.file_name, d.original_name, d.file_type
          FROM payment_attachments a
          JOIN clint_documents d ON d.id = a.document_id AND d.is_deleted = false
         WHERE a.payment_id = CAST(:pid AS uuid) AND a.document_id = CAST(:did AS uuid)
           AND a.removed_at IS NULL
    """), {"pid": str(payment_id), "did": str(document_id)}).mappings().first()
    if row is None:
        raise _attachment_not_found(payment_id, document_id)
    try:
        path = files.document_path(row["file_name"])
    except ValueError:
        path = None
    if not path or not os.path.exists(path):
        raise ApiError(404, "file_missing", "the file is missing on the server",
                       {"document_id": str(document_id)})
    mime = MIME_BY_TYPE.get(str(row["file_type"]).lower(), "application/octet-stream")
    return path, row["original_name"], mime


# --------------------------------------------------------------------------- writes
def add_attachment(payment_id, file_storage, form, actor, scope) -> dict:
    if file_storage is None or not (file_storage.filename or "").strip():
        raise ApiError(422, "validation_failed", "a file is required (multipart field 'file')",
                       {"file": "required"})
    ext = os.path.splitext(file_storage.filename)[1].lower()
    if ext not in TYPES:
        raise ApiError(415, "unsupported_file_type", "only PDF, JPG, JPEG or PNG files are accepted",
                       {"extension": ext or None, "accepted": sorted(TYPES)})
    file_type, mime = TYPES[ext]
    row = _payment(payment_id, scope)              # 404 before anything is written
    _writable(row)
    name = _display_name(form, row)

    os.makedirs(files.DOCUMENTS_DIR, exist_ok=True)
    stored = f"{uuid.uuid4()}{ext}"
    path = files.document_path(stored)
    from core.bulk.inspect import InspectionError, receive
    try:
        size, sha = receive(file_storage.stream, path, MAX_BYTES)
    except InspectionError as e:
        raise ApiError(e.status, e.code, e.message, e.error) from None
    try:
        _check_magic(path, mime)
        row = _payment(payment_id, scope, lock=True)   # serialises concurrent adds; state again
        _writable(row)
        if _live_count(payment_id) >= MAX_PER_PAYMENT:
            raise ApiError(409, "too_many_attachments",
                           f"a payment can have at most {MAX_PER_PAYMENT} attachments",
                           {"max_attachments": MAX_PER_PAYMENT})
        case_id = str(row["clint_id"])
        doc_id = _s().execute(text("""
            INSERT INTO clint_documents (clint_id, file_name, original_name, display_name, file_type, uploaded_by)
            VALUES (CAST(:cid AS uuid), :file_name, :original, :display, :ftype, CAST(:actor AS uuid))
            RETURNING id
        """), {"cid": case_id, "file_name": stored, "original": _original_name(file_storage.filename),
               "display": name, "ftype": file_type, "actor": str(actor)}).scalar()
        _s().execute(text("""
            INSERT INTO payment_attachments (payment_id, document_id, size_bytes, sha256, added_by)
            VALUES (CAST(:pid AS uuid), :did, :size, :sha, CAST(:actor AS uuid))
        """), {"pid": str(payment_id), "did": doc_id, "size": size, "sha": sha, "actor": str(actor)})
        audit.record("payment.attachment.add", "payment", payment_id, case_id=case_id,
                     after={"document_id": str(doc_id), "name": name, "file_type": file_type,
                            "size_bytes": size, "sha256": sha, "payment_status": _status(row)})
        _s().commit()
    except BaseException:
        _s().rollback()
        _remove(path)
        raise
    item = next((i for i in _items(payment_id) if i["document_id"] == str(doc_id)), None)
    return {"payment_id": str(payment_id), "attachment": item, "items": _items(payment_id)}


def remove_attachment(payment_id, document_id, actor, scope, manage_any: bool) -> dict:
    row = _payment(payment_id, scope, lock=True)
    link = _s().execute(text("""
        SELECT a.id, a.added_by
          FROM payment_attachments a
          JOIN clint_documents d ON d.id = a.document_id AND d.is_deleted = false
         WHERE a.payment_id = CAST(:pid AS uuid) AND a.document_id = CAST(:did AS uuid)
           AND a.removed_at IS NULL
           FOR UPDATE OF a
    """), {"pid": str(payment_id), "did": str(document_id)}).mappings().first()
    if link is None:
        raise _attachment_not_found(payment_id, document_id)
    status = _status(row)
    if status != "pending":
        raise ApiError(409, "payment_not_pending",
                       "an attachment can only be removed while the payment is pending", {"status": status})
    if row["is_locked"]:
        raise ApiError(423, "case_locked", "العميل مقفل ولا يمكن تنفيذ هذا الإجراء",
                       {"case_id": str(row["clint_id"])})
    if not manage_any and str(link["added_by"] or "") != str(actor):
        raise ApiError(403, "forbidden", "only the person who attached the file or accounts can remove it",
                       {"required_any": ["accounts_payments"], "uploader_only": True})
    _s().execute(text("""
        UPDATE payment_attachments SET removed_at = now(), removed_by = CAST(:actor AS uuid)
         WHERE id = :id
    """), {"actor": str(actor), "id": link["id"]})
    _s().execute(text("UPDATE clint_documents SET is_deleted = true WHERE id = CAST(:did AS uuid)"),
                 {"did": str(document_id)})
    audit.record("payment.attachment.remove", "payment", payment_id, case_id=row["clint_id"],
                 before={"document_id": str(document_id), "added_by": money.uid(link["added_by"])},
                 after={"document_id": str(document_id), "removed": True})
    _s().commit()
    return {"payment_id": str(payment_id), "document_id": str(document_id), "removed": True,
            "items": _items(payment_id)}
