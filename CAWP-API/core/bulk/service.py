"""API-side operations on uploads and bulk jobs (used by api/bulk.py).

Never commits: the `@idempotent()` decorator commits a mutation together with its stored
response. Read routes only read (the lazy generation of a missing artifact commits itself).

Visibility: a job is visible to its creator, and to a holder of `bulk_jobs_all` when the
creator is inside the holder's user scope (SuperAdmin: everyone; supervisor: the supervised
users). A non-creator who is not SuperAdmin only sees the rows whose case is in their own
case scope, and cannot download artifacts (they contain every row). A job that is not
visible answers 404, like a case outside the scope.
"""
from __future__ import annotations

import datetime as _dt
import os
import uuid

from flask import request
from flask_jwt_extended import get_jwt_identity
from sqlalchemy import text

from core import audit, riyadh
from core.api_response import ApiError
from core.auth import has_permission
from core.bulk import artifacts, engine, inspect, registry, sources, storage
from core.bulk import settings as _settings
from core.bulk.common import (ACTIVE, STATUSES, TERMINAL, iso, scope_sql, uid, user_names, valid_uuid)
from core.revisions import RevisionConflict, parse_revision
from services.clients_scope import SUPER, resolve_scope

ALL_JOBS_KEY = "bulk_jobs_all"
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200
IMMEDIATE_CANCEL = ("queued", "ready", "confirmed", "retrying")


def _s():
    from extensions import db
    return db.session


def me() -> str:
    return str(get_jwt_identity())


class Viewer:
    def __init__(self):
        self.id = me()
        self.scope = resolve_scope(self.id)
        self.all = has_permission(ALL_JOBS_KEY)

    @property
    def is_super(self) -> bool:
        return self.scope.kind == SUPER


def forbidden(keys):
    raise ApiError(403, "forbidden", "You are not authorized to access this resource",
                   {"required_any": list(keys)})


def kind_or_404(key: str) -> registry.BulkKind:
    kind = registry.get(key)
    if kind is None:
        raise ApiError(404, "unknown_kind", f"there is no bulk action {key!r}",
                       {"kinds": [k.key for k in registry.all_kinds()]})
    return kind


def has_kind_permission(kind) -> bool:
    return any(has_permission(k) for k in kind.permissions)


def require_kind_permission(kind) -> None:
    if not has_kind_permission(kind):
        forbidden(kind.permissions)


def any_bulk_permission() -> bool:
    return has_permission(ALL_JOBS_KEY) or any(has_permission(k) for k in registry.all_permission_keys())


def _paging(args):
    try:
        page = int(args.get("page", 1))
        size = int(args.get("page_size", args.get("pageSize", DEFAULT_PAGE_SIZE)))
    except (TypeError, ValueError):
        raise ApiError(422, "validation_failed", "page and page_size must be integers", {"page": "invalid"}) from None
    if page < 1 or size < 1:
        raise ApiError(422, "validation_failed", "page and page_size must be >= 1", {"page": "invalid"})
    size = min(size, MAX_PAGE_SIZE)
    return page, size, (page - 1) * size


def _list_arg(args, name, allowed=None):
    raw = args.get(name)
    if raw in (None, ""):
        return []
    values = [v.strip() for v in raw.split(",") if v.strip()]
    if allowed is not None:
        bad = [v for v in values if v not in allowed]
        if bad:
            raise ApiError(422, "validation_failed", f"unknown {name}: {', '.join(bad)}",
                           {name: "invalid", "allowed": list(allowed)})
    return values


def _now():
    return _dt.datetime.now(_dt.timezone.utc)


# =========================================================================== visibility
def _visible_sql(v: Viewer, alias: str = "j") -> tuple[str, dict]:
    if v.all and v.is_super:
        return "TRUE", {}
    if v.all and v.scope.user_ids:
        return (f"({alias}.created_by = CAST(:v_me AS uuid) OR {alias}.created_by = ANY(CAST(:v_users AS uuid[])))",
                {"v_me": v.id, "v_users": sorted(v.scope.user_ids)})
    return f"{alias}.created_by = CAST(:v_me AS uuid)", {"v_me": v.id}


def get_job(v: Viewer, job_id, lock: bool = False) -> dict:
    pred, params = _visible_sql(v)
    row = _s().execute(text(f"SELECT j.* FROM bulk_jobs j WHERE j.id = CAST(:id AS uuid) AND {pred}"
                            + (" FOR UPDATE OF j" if lock else "")),
                       {"id": str(job_id), **params}).mappings().first()
    if row is None:
        raise ApiError(404, "job_not_found", "job not found", {"job_id": str(job_id)})
    return dict(row)


def _is_owner(v: Viewer, job) -> bool:
    return str(job["created_by"]) == v.id


def _full_access(v: Viewer, job) -> bool:
    return _is_owner(v, job) or (v.all and v.is_super)


# =========================================================================== serialisation
def _preview_expired(job) -> bool:
    exp = job.get("preview_expires_at")
    return exp is not None and exp <= _now()


def effective_status(job) -> str:
    if job["status"] == "ready" and _preview_expired(job):
        return "expired"
    return job["status"]


def _source_summary(job) -> dict:
    ref = job["source_ref"] or {}
    src = job["source"]
    if src == "selection":
        return {"count": ref["count"] if "count" in ref and "case_ids" not in ref else len(ref.get("case_ids") or [])}
    if src == "filter":
        from urllib.parse import urlencode
        return {"list": ref.get("list"), "query": urlencode([tuple(p) for p in ref.get("query") or []])}
    if src == "portfolio":
        return {"portfolio_id": ref.get("portfolio_id")}
    if src == "spreadsheet":
        return {"upload_id": ref.get("upload_id"), "sheet": ref.get("sheet")}
    return dict(ref)


def _actions(v: Viewer | None, job, kind) -> dict:
    if v is None:
        return {}
    perm = kind is not None and has_kind_permission(kind)
    status = effective_status(job)
    confirm = (perm and status == "ready" and kind is not None and not kind.read_only
               and job["eligible_rows"] > 0)
    cancel = (status in ACTIVE and job["cancel_requested_at"] is None
              and (_is_owner(v, job) or perm or v.all))
    retry = perm and (
        (job["status"] == "failed" and job["phase"] == "prepare")
        or (job["phase"] == "execute" and job["status"] in ("failed", "partially_succeeded")
            and not _preview_expired(job)
            and (job["failed_rows"] > 0 or job["processed_rows"] < job["eligible_rows"])))
    reopen = perm and status in TERMINAL
    return {"confirm": bool(confirm), "cancel": bool(cancel), "retry": bool(retry), "reopen": bool(reopen),
            "download": _full_access(v, job)}


def _uploads(ids) -> dict:
    ids = sorted({str(i) for i in ids if i})
    if not ids:
        return {}
    rows = _s().execute(text("""SELECT id, original_name, detected_type, row_count, purged_at, expires_at
                                FROM bulk_uploads WHERE id = ANY(CAST(:ids AS uuid[]))"""), {"ids": ids}).mappings()
    return {str(u["id"]): {"id": str(u["id"]), "original_name": u["original_name"],
                           "detected_type": u["detected_type"], "row_count": u["row_count"],
                           "purged": u["purged_at"] is not None, "expires_at": iso(u["expires_at"])} for u in rows}


def serialize_job(job: dict, v: Viewer | None = None, names: dict | None = None, uploads: dict | None = None) -> dict:
    kind = registry.get(job["kind"])
    if names is None:
        names = user_names(_s(), [job["created_by"], job["confirmed_by"], job["cancel_requested_by"],
                                  job["retried_by"]])

    def person(x):
        return None if x is None else names.get(str(x), {"id": str(x), "name": None, "user_name": None})

    status = effective_status(job)
    counts = {"total": job["total_rows"], "prepared": job["prepared_rows"], "eligible": job["eligible_rows"],
              "excluded": job["excluded_rows"], "error": job["error_rows"], "processed": job["processed_rows"],
              "succeeded": job["succeeded_rows"], "skipped": job["skipped_rows"], "failed": job["failed_rows"]}
    if job["phase"] == "execute":
        done, total = job["processed_rows"], job["eligible_rows"]
    else:
        done, total = job["prepared_rows"], job["total_rows"]
    progress = {"phase": "executing" if job["phase"] == "execute" else "validating", "done": done, "total": total,
                "percent": round(100.0 * done / total, 1) if total else (100.0 if status in TERMINAL + ("ready",) else 0.0)}
    if uploads is None:
        uploads = _uploads([job["upload_id"]])
    upload = uploads.get(str(job["upload_id"])) if job["upload_id"] is not None else None
    return {
        "id": str(job["id"]), "kind": job["kind"],
        "kind_label": {"en": kind.label_en, "ar": kind.label_ar} if kind else None,
        "source": job["source"], "source_summary": _source_summary(job),
        "status": status, "stored_status": job["status"], "phase": job["phase"], "read_only": job["read_only"],
        "params": job["params"] or {}, "mapping": job["mapping"], "sheet": job["sheet"], "upload": upload,
        "counts": counts, "progress": progress, "revision": job["revision"],
        "created_by": person(job["created_by"]), "created_at": iso(job["created_at"]),
        "updated_at": iso(job["updated_at"]),
        "preview": {"ready_at": iso(job["preview_ready_at"]), "expires_at": iso(job["preview_expires_at"]),
                    "expired": _preview_expired(job) and job["status"] in ("ready", "expired")},
        "needs_accept_partial": (job["excluded_rows"] + job["error_rows"]) > 0,
        "confirmation": None if job["confirmed_at"] is None else {
            "by": person(job["confirmed_by"]), "at": iso(job["confirmed_at"]), "accept_partial": job["accept_partial"]},
        "retry": {"count": job["retry_count"], "last_by": person(job["retried_by"]), "last_at": iso(job["retried_at"])},
        "cancel": None if job["cancel_requested_at"] is None and job["cancelled_at"] is None else {
            "requested_by": person(job["cancel_requested_by"]), "requested_at": iso(job["cancel_requested_at"]),
            "reason": job["cancel_reason"], "cancelled_at": iso(job["cancelled_at"])},
        "started_at": iso(job["started_at"]), "execution_started_at": iso(job["execution_started_at"]),
        "finished_at": iso(job["finished_at"]),
        "attempt": job["attempt"], "max_attempts": job["max_attempts"], "failed_attempts": job["failed_attempts"],
        "worker": None if job["locked_by"] is None else {
            "id": job["locked_by"], "lease_until": iso(job["lease_until"]), "heartbeat_at": iso(job["heartbeat_at"])},
        "error": None if job["error_code"] is None else {"code": job["error_code"], "message": job["error_message"]},
        "reopened_from": uid(job["reopened_from"]),
        "actions": _actions(v, job, kind),
    }


def serialize_upload(u: dict) -> dict:
    warnings = [{"code": "not_scanned",
                 "message": "no antivirus scan was performed on this file (scan_status not_scanned)"}]
    if u["formula_cells"]:
        warnings.append({"code": "formula_cells", "count": u["formula_cells"],
                         "message": "the file contains formula cells; they are never evaluated, and a formula "
                                    "in a mapped column makes that row an error"})
    return {
        "id": str(u["id"]), "purpose": u["purpose"], "original_name": u["original_name"],
        "size_bytes": int(u["size_bytes"]), "sha256": u["sha256"], "detected_type": u["detected_type"],
        "encoding": u["encoding"], "delimiter": u["delimiter"], "sheets": u["sheets"] or [],
        "row_count": u["row_count"], "header_row": u["header_row"], "formula_cells": u["formula_cells"],
        "scan_status": u["scan_status"], "uploaded_by": uid(u["uploaded_by"]),
        "uploaded_at": iso(u["uploaded_at"]), "expires_at": iso(u["expires_at"]),
        "purged_at": iso(u["purged_at"]), "warnings": warnings,
    }


# =========================================================================== kinds
def kinds_list() -> list[dict]:
    return [{**k.describe(), "allowed": has_kind_permission(k)} for k in registry.all_kinds()]


# =========================================================================== uploads
def create_upload(file_storage, purpose: str | None) -> dict:
    purpose = (purpose or "bulk").strip()
    if purpose == "bulk":
        capable = [k for k in registry.all_kinds() if "spreadsheet" in k.sources]
        if not any(has_kind_permission(k) for k in capable):
            forbidden(sorted({p for k in capable for p in k.permissions}))
    else:
        kind = registry.get(purpose)
        if kind is None or "spreadsheet" not in kind.sources:
            raise ApiError(422, "validation_failed", "purpose must be 'bulk' or a bulk action that accepts spreadsheets",
                           {"purpose": "invalid",
                            "allowed": ["bulk"] + [k.key for k in registry.all_kinds() if "spreadsheet" in k.sources]})
        require_kind_permission(kind)
    if file_storage is None or not file_storage.filename:
        raise ApiError(422, "validation_failed", "a file is required (multipart field 'file')", {"file": "required"})
    original = os.path.basename(file_storage.filename.replace("\\", "/"))[:255] or "upload"
    tmp = storage.tmp_path()
    try:
        size, sha = inspect.receive(file_storage.stream, tmp)
        detected = inspect.detect_type(tmp, original)
        info = inspect.inspect_file(tmp, detected)
    except inspect.InspectionError as e:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise ApiError(e.status, e.code, e.message, e.error) from None
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
    upload_id = str(uuid.uuid4())
    now = _now()
    relative = storage.upload_relative(upload_id, detected, now)
    storage.place(tmp, relative)
    s = _settings.get()
    row = _s().execute(text("""
        INSERT INTO bulk_uploads (id, purpose, original_name, stored_path, size_bytes, sha256, detected_type,
                                  encoding, delimiter, sheets, row_count, header_row, formula_cells,
                                  uploaded_by, uploaded_at, expires_at)
        VALUES (CAST(:id AS uuid), :purpose, :name, :path, :size, :sha, :type, :enc, :delim, CAST(:sheets AS jsonb),
                :rows, :hrow, :formulas, CAST(:by AS uuid), :now, :now + make_interval(days => :ttl))
        RETURNING *
    """), {"id": upload_id, "purpose": purpose, "name": original, "path": relative, "size": size, "sha": sha,
           "type": detected, "enc": info["encoding"], "delim": info["delimiter"],
           "sheets": _json(info["sheets"]), "rows": info["row_count"], "hrow": info["header_row"],
           "formulas": info["formula_cells"], "by": me(), "now": now, "ttl": s.upload_ttl_days}).mappings().one()
    audit.record("bulk_upload.create", "bulk_upload", upload_id,
                 after={"purpose": purpose, "original_name": original, "size_bytes": size, "sha256": sha,
                        "detected_type": detected, "row_count": info["row_count"],
                        "formula_cells": info["formula_cells"], "scan_status": "not_scanned"})
    return serialize_upload(dict(row))


def _json(v):
    from core.bulk.common import dumps
    return dumps(v)


def get_upload(upload_id) -> dict:
    v = Viewer()
    row = _s().execute(text("SELECT * FROM bulk_uploads WHERE id = CAST(:id AS uuid)"),
                       {"id": str(upload_id)}).mappings().first()
    if row is None or not (str(row["uploaded_by"]) == v.id or (v.all and v.is_super)):
        raise ApiError(404, "upload_not_found", "upload not found", {"upload_id": str(upload_id)})
    return serialize_upload(dict(row))


# =========================================================================== jobs: create
def create_job(kind_key: str, body: dict) -> dict:
    kind = kind_or_404(kind_key)
    require_kind_permission(kind)
    v = Viewer()
    session = _s()
    params = body.get("params", {})
    if params is None:
        params = {}
    if not isinstance(params, dict):
        raise ApiError(422, "validation_failed", "params must be an object", {"params": "invalid"})
    src = sources.validate(kind, body, session, v.id)
    try:
        params = kind.validate_params(params, registry.CreateContext(session, v.id, v.scope, src["source"])) or {}
    except registry.ParamError as e:
        raise ApiError(422, "validation_failed", e.message, {"params": e.errors}) from None
    return _insert_job(kind, src, params, v, reopened_from=None)


def _insert_job(kind, src, params, v: Viewer, reopened_from):
    from core.bulk.common import dumps
    s = _settings.get()
    row = _s().execute(text("""
        INSERT INTO bulk_jobs (kind, source, source_ref, upload_id, sheet, mapping, params, read_only,
                               created_by, max_attempts, reopened_from)
        VALUES (:kind, :source, CAST(:ref AS jsonb), CAST(:upload AS uuid), :sheet, CAST(:mapping AS jsonb),
                CAST(:params AS jsonb), :ro, CAST(:by AS uuid), :max, CAST(:reopened AS uuid))
        RETURNING *
    """), {"kind": kind.key, "source": src["source"], "ref": dumps(src["source_ref"]), "upload": src["upload_id"],
           "sheet": src["sheet"], "mapping": dumps(src["mapping"]), "params": dumps(params), "ro": kind.read_only,
           "by": v.id, "max": s.max_attempts, "reopened": reopened_from}).mappings().one()
    job = dict(row)
    audit.record("bulk_job.reopen" if reopened_from else "bulk_job.create", "bulk_job", job["id"],
                 after={"kind": kind.key, "source": src["source"], "source_summary": _source_summary(job),
                        "params": params, "mapping": src["mapping"], "reopened_from": reopened_from})
    return serialize_job(job, v)


# =========================================================================== jobs: transitions
def _expected_revision(body, required: bool):
    raw = body.get("expected_revision", request.headers.get("If-Match"))
    if raw is None:
        if required:
            raise ApiError(422, "validation_failed", "expected_revision is required", {"expected_revision": "required"})
        return None
    try:
        return parse_revision(raw)
    except ValueError:
        raise ApiError(422, "validation_failed", "expected_revision must be a non-negative integer",
                       {"expected_revision": "invalid"}) from None


def _check_revision(job, expected):
    if expected is not None and expected != job["revision"]:
        raise RevisionConflict("bulk_job", job["id"], expected, job["revision"])


def _upload_ok(job):
    if job["source"] == "spreadsheet":
        sources.upload_usable(sources.load_upload(_s(), job["upload_id"]))


def _record(action, job, before=None, after=None, reason=None):
    audit.record(action, "bulk_job", job["id"], before=before, after=after, reason=reason)


def _reload(v, job_id):
    return serialize_job(engine.load_job(_s(), job_id), v)


def confirm(job_id, body: dict) -> dict:
    v = Viewer()
    job = get_job(v, job_id, lock=True)
    kind = kind_or_404(job["kind"])
    require_kind_permission(kind)
    if kind.read_only:
        raise ApiError(409, "read_only_kind", "this action is read-only; its preview is the result")
    if job["status"] != "ready":
        raise ApiError(409, "job_not_ready", f"the job is {effective_status(job)}, not ready for confirmation",
                       {"status": effective_status(job)})
    _check_revision(job, _expected_revision(body, required=True))
    if _preview_expired(job):
        raise ApiError(409, "preview_expired", "the preview expired; reopen the job to validate again",
                       {"expired_at": iso(job["preview_expires_at"])})
    if job["eligible_rows"] == 0:
        raise ApiError(409, "nothing_to_execute", "no row is eligible")
    accept = body.get("accept_partial", False)
    if not isinstance(accept, bool):
        raise ApiError(422, "validation_failed", "accept_partial must be true or false", {"accept_partial": "invalid"})
    if (job["excluded_rows"] + job["error_rows"]) > 0 and not accept:
        raise ApiError(422, "accept_partial_required",
                       "some rows are excluded or have errors; confirm with accept_partial: true to run the eligible rows only",
                       {"eligible": job["eligible_rows"], "excluded": job["excluded_rows"], "error": job["error_rows"]})
    _upload_ok(job)
    _s().execute(text("""
        UPDATE bulk_jobs SET status = 'confirmed', phase = 'execute', confirmed_by = CAST(:by AS uuid),
               confirmed_at = now(), accept_partial = :ap, updated_at = now()
         WHERE id = CAST(:id AS uuid)
    """), {"by": v.id, "ap": accept, "id": str(job["id"])})
    _record("bulk_job.confirm", job, before={"status": "ready", "revision": job["revision"]},
            after={"status": "confirmed", "accept_partial": accept, "eligible": job["eligible_rows"],
                   "excluded": job["excluded_rows"], "error": job["error_rows"]})
    return _reload(v, job["id"])


def cancel(job_id, body: dict) -> tuple[dict, int]:
    v = Viewer()
    job = get_job(v, job_id, lock=True)
    kind = registry.get(job["kind"])
    if not (_is_owner(v, job) or v.all or (kind is not None and has_kind_permission(kind))):
        forbidden((kind.permissions if kind else ()) + (ALL_JOBS_KEY,))
    _check_revision(job, _expected_revision(body, required=False))
    reason = body.get("reason")
    if reason is not None and (not isinstance(reason, str) or len(reason) > 1000):
        raise ApiError(422, "validation_failed", "reason must be text (max 1000)", {"reason": "invalid"})
    reason = reason.strip() if isinstance(reason, str) and reason.strip() else None
    status = job["status"]
    if status in IMMEDIATE_CANCEL:
        _s().execute(text("""
            UPDATE bulk_jobs SET status = 'cancelled', cancelled_at = now(), finished_at = now(),
                   cancel_requested_at = coalesce(cancel_requested_at, now()),
                   cancel_requested_by = coalesce(cancel_requested_by, CAST(:by AS uuid)),
                   cancel_reason = coalesce(cancel_reason, :r), updated_at = now()
             WHERE id = CAST(:id AS uuid)
        """), {"by": v.id, "r": reason, "id": str(job["id"])})
        if job["phase"] == "execute":
            engine._skip_pending(_s(), [job["id"]], "cancelled", "the job was cancelled before this row ran")
            engine._recount(_s(), [job["id"]])
        _record("bulk_job.cancel", job, before={"status": status}, after={"status": "cancelled"}, reason=reason)
        return _reload(v, job["id"]), 200
    if status in ("validating", "running"):
        if job["cancel_requested_at"] is None:
            _s().execute(text("""
                UPDATE bulk_jobs SET cancel_requested_at = now(), cancel_requested_by = CAST(:by AS uuid),
                       cancel_reason = :r, updated_at = now()
                 WHERE id = CAST(:id AS uuid)
            """), {"by": v.id, "r": reason, "id": str(job["id"])})
            _record("bulk_job.cancel_request", job, before={"status": status},
                    after={"cancel_requested": True}, reason=reason)
        return _reload(v, job["id"]), 202
    raise ApiError(409, "job_not_cancellable", f"a {effective_status(job)} job cannot be cancelled",
                   {"status": effective_status(job)})


def retry(job_id, body: dict) -> dict:
    v = Viewer()
    job = get_job(v, job_id, lock=True)
    kind = kind_or_404(job["kind"])
    require_kind_permission(kind)
    _check_revision(job, _expected_revision(body, required=False))
    session = _s()
    if job["status"] == "failed" and job["phase"] == "prepare":
        next_status, reset = "queued", 0
    elif job["phase"] == "execute" and job["status"] in ("failed", "partially_succeeded"):
        if _preview_expired(job):
            raise ApiError(409, "preview_expired", "the preview expired; reopen the job to validate again",
                           {"expired_at": iso(job["preview_expires_at"])})
        _upload_ok(job)
        reset = session.execute(text("""
            UPDATE bulk_job_rows SET outcome = 'pending', outcome_code = NULL, outcome_message = NULL,
                   processed_at = NULL
             WHERE job_id = CAST(:j AS uuid) AND eligibility = 'eligible' AND outcome = 'failed'
        """), {"j": str(job["id"])}).rowcount
        pending = session.execute(text("""
            SELECT count(*) FROM bulk_job_rows
             WHERE job_id = CAST(:j AS uuid) AND eligibility = 'eligible' AND outcome = 'pending'
        """), {"j": str(job["id"])}).scalar()
        if pending == 0:
            raise ApiError(409, "nothing_to_retry",
                           "no failed or unprocessed row to retry; skipped rows need a new preview (reopen)")
        next_status = "confirmed"
    else:
        raise ApiError(409, "job_not_retryable", f"a {effective_status(job)} job cannot be retried",
                       {"status": effective_status(job), "phase": job["phase"]})
    session.execute(text("""
        UPDATE bulk_jobs SET status = :st, finished_at = NULL, error_code = NULL, error_message = NULL,
               failed_attempts = 0, retried_by = CAST(:by AS uuid), retried_at = now(),
               retry_count = retry_count + 1, updated_at = now()
         WHERE id = CAST(:id AS uuid)
    """), {"st": next_status, "by": v.id, "id": str(job["id"])})
    if job["phase"] == "execute":
        engine._recount(session, [job["id"]])
    _record("bulk_job.retry", job, before={"status": job["status"]},
            after={"status": next_status, "rows_reset": reset})
    return _reload(v, job["id"])


def reopen(job_id, body: dict) -> dict:
    v = Viewer()
    job = get_job(v, job_id, lock=True)
    kind = kind_or_404(job["kind"])
    require_kind_permission(kind)
    status = effective_status(job)
    if status not in TERMINAL:
        raise ApiError(409, "job_not_reopenable", f"a {status} job cannot be reopened; cancel it first",
                       {"status": status})
    src = {"source": job["source"], "source_ref": job["source_ref"] or {}, "upload_id": uid(job["upload_id"]),
           "sheet": job["sheet"], "mapping": job["mapping"]}
    if job["source"] == "spreadsheet":
        upload = sources.load_upload(_s(), job["upload_id"])
        sources.upload_usable(upload)
    return _insert_job(kind, src, job["params"] or {}, v, reopened_from=str(job["id"]))


# =========================================================================== jobs: reads
def job_detail(job_id) -> dict:
    v = Viewer()
    job = get_job(v, job_id)
    out = serialize_job(job, v)
    session = _s()
    out["attempts"] = [{
        "attempt": a["attempt"], "phase": a["phase"], "worker_id": a["worker_id"],
        "started_at": iso(a["started_at"]), "finished_at": iso(a["finished_at"]), "outcome": a["outcome"],
        "rows_processed": a["rows_processed"], "error": a["error"],
    } for a in session.execute(text("""
        SELECT * FROM bulk_job_attempts WHERE job_id = CAST(:j AS uuid) ORDER BY attempt
    """), {"j": str(job["id"])}).mappings()]
    out["artifacts"] = [artifacts.serialize(a) for a in session.execute(text("""
        SELECT * FROM bulk_job_artifacts WHERE job_id = CAST(:j AS uuid) ORDER BY kind
    """), {"j": str(job["id"])}).mappings()]
    return out


def preview_summary(job_id) -> dict:
    v = Viewer()
    job = get_job(v, job_id)
    rows_pred, params = _row_scope(v, job)
    by_code = [{"eligibility": r[0], "code": r[1], "message": r[2], "count": r[3]} for r in _s().execute(text(f"""
        SELECT r.eligibility, r.eligibility_code, min(r.eligibility_message), count(*)
          FROM bulk_job_rows r LEFT JOIN clints c ON c.id = r.case_id
         WHERE r.job_id = CAST(:j AS uuid) AND r.eligibility IN ('excluded', 'error') AND {rows_pred}
         GROUP BY 1, 2 ORDER BY 1, 4 DESC, 2
    """), {"j": str(job["id"]), **params})]
    kind = registry.get(job["kind"])
    status = effective_status(job)
    return {
        "job_id": str(job["id"]), "status": status, "revision": job["revision"],
        "ready": status == "ready", "expires_at": iso(job["preview_expires_at"]),
        "expired": _preview_expired(job) and job["status"] in ("ready", "expired"),
        "counts": {"total": job["total_rows"], "eligible": job["eligible_rows"],
                   "excluded": job["excluded_rows"], "error": job["error_rows"]},
        "by_code": by_code,
        "needs_accept_partial": (job["excluded_rows"] + job["error_rows"]) > 0,
        "can_confirm": _actions(v, job, kind).get("confirm", False),
        "rows_limited_to_your_scope": not _full_access(v, job),
        # optional per-kind summary (e.g. per-collector totals for assign / round_robin),
        # over the same visible rows as `by_code`
        "kind_summary": (kind.summarize(_s(), job, rows_pred, params)
                         if kind is not None and hasattr(kind, "summarize") else None),
    }


def _row_scope(v: Viewer, job) -> tuple[str, dict]:
    """Rows a non-creator (not SuperAdmin) may see: those whose case is in their scope."""
    if _full_access(v, job):
        return "TRUE", {}
    pred, params = scope_sql(_s(), v.scope, "c", prefix="rv")
    return f"(r.case_id IS NOT NULL AND c.id IS NOT NULL AND {pred})", params


ROW_ELIGIBILITY = ("pending", "eligible", "excluded", "error")
ROW_OUTCOMES = ("pending", "succeeded", "skipped", "failed")


def list_rows(job_id, args) -> dict:
    v = Viewer()
    job = get_job(v, job_id)
    page, size, offset = _paging(args)
    where, params = ["r.job_id = CAST(:j AS uuid)"], {"j": str(job["id"])}
    elig = _list_arg(args, "eligibility", ROW_ELIGIBILITY)
    if elig:
        where.append("r.eligibility = ANY(:elig)")
        params["elig"] = elig
    outc = _list_arg(args, "outcome", ROW_OUTCOMES)
    if outc:
        where.append("r.outcome = ANY(:outc)")
        params["outc"] = outc
    code = args.get("code")
    if code:
        where.append("(r.eligibility_code = :code OR r.outcome_code = :code)")
        params["code"] = code
    q = (args.get("q") or "").strip()
    if q:
        if q.isdigit():
            where.append("(r.row_no = :qn OR r.match_value = :q)")
            params["qn"] = int(q) if int(q) < 2**31 else -1
        else:
            where.append("r.match_value = :q")
        params["q"] = q
    rows_pred, sp = _row_scope(v, job)
    where.append(rows_pred)
    params.update(sp)
    sql_where = " AND ".join(where)
    session = _s()
    total = session.execute(text(f"""
        SELECT count(*) FROM bulk_job_rows r LEFT JOIN clints c ON c.id = r.case_id WHERE {sql_where}
    """), params).scalar()
    items = session.execute(text(f"""
        SELECT r.*, c."accountNumber" AS c_account_number, c.name AS c_name
          FROM bulk_job_rows r LEFT JOIN clints c ON c.id = r.case_id
         WHERE {sql_where}
         ORDER BY r.row_no LIMIT :lim OFFSET :off
    """), {**params, "lim": size, "off": offset}).mappings().all()
    return {"items": [_serialize_row(r) for r in items], "total": total, "page": page, "page_size": size,
            "filters": {"eligibility": elig or None, "outcome": outc or None, "code": code or None, "q": q or None},
            "rows_limited_to_your_scope": not _full_access(v, job)}


def _serialize_row(r) -> dict:
    return {
        "id": r["id"], "row_no": r["row_no"], "case_id": uid(r["case_id"]),
        "case": None if r["case_id"] is None or r["c_account_number"] is None else {
            "id": str(r["case_id"]), "account_number": r["c_account_number"], "name": r["c_name"]},
        "match_value": r["match_value"], "raw": r["raw"], "mapped": r["mapped"],
        "eligibility": r["eligibility"], "eligibility_code": r["eligibility_code"],
        "eligibility_message": r["eligibility_message"], "expected_revision": r["expected_revision"],
        "outcome": r["outcome"], "outcome_code": r["outcome_code"], "outcome_message": r["outcome_message"],
        "processed_at": iso(r["processed_at"]), "attempt": r["attempt"], "details": r["details"],
    }


_LIGHT_COLUMNS: str | None = None


def _light_columns(session) -> str:
    """Every bulk_jobs column, with a selection's (possibly 100k-long) case id array replaced by
    its count: a list page never ships the arrays."""
    global _LIGHT_COLUMNS
    if _LIGHT_COLUMNS is None:
        cols = [r[0] for r in session.execute(text("""
            SELECT column_name FROM information_schema.columns
             WHERE table_schema = current_schema() AND table_name = 'bulk_jobs' ORDER BY ordinal_position"""))]
        light = ("CASE WHEN j.source = 'selection' THEN (j.source_ref - 'case_ids') || jsonb_build_object("
                 "'count', coalesce(jsonb_array_length(j.source_ref -> 'case_ids'), 0)) ELSE j.source_ref END "
                 "AS source_ref")
        _LIGHT_COLUMNS = ", ".join(light if c == "source_ref" else f'j."{c}"' for c in cols)
    return _LIGHT_COLUMNS


def list_jobs(args) -> dict:
    v = Viewer()
    page, size, offset = _paging(args)
    pred, params = _visible_sql(v)
    where = [pred]
    kinds = _list_arg(args, "kind")
    if kinds:
        where.append("j.kind = ANY(:kinds)")
        params["kinds"] = kinds
    creator = (args.get("created_by") or "").strip()
    if creator:
        cid = v.id if creator == "me" else valid_uuid(creator)
        if cid is None:
            raise ApiError(422, "validation_failed", "created_by must be 'me' or a user id", {"created_by": "invalid"})
        where.append("j.created_by = CAST(:creator AS uuid)")
        params["creator"] = cid
    for name, op, idx in (("date_from", ">=", 0), ("date_to", "<", 1)):
        raw = args.get(name)
        if raw:
            try:
                bound = riyadh.day_bounds(riyadh.parse_date(raw))[idx]
            except ValueError:
                raise ApiError(422, "validation_failed", f"{name} must be YYYY-MM-DD", {name: "invalid"}) from None
            where.append(f"j.created_at {op} :{name}")
            params[name] = bound
    base_where = " AND ".join(where)
    statuses = _list_arg(args, "status", STATUSES)
    status_sql = ""
    if statuses:
        # 'expired' also covers a ready preview whose TTL passed before the retention task ran
        conds = ["j.status = ANY(:statuses)"]
        if "expired" in statuses:
            conds.append("(j.status = 'ready' AND j.preview_expires_at <= now())")
        if "ready" in statuses:
            conds[0] = "(j.status = ANY(:statuses) AND NOT (j.status = 'ready' AND j.preview_expires_at <= now()))"
        status_sql = " AND (" + " OR ".join(conds) + ")"
        params["statuses"] = statuses
    session = _s()
    counts = {k: 0 for k in STATUSES}
    for st, n in session.execute(text(f"""
        SELECT CASE WHEN j.status = 'ready' AND j.preview_expires_at <= now() THEN 'expired' ELSE j.status END, count(*)
          FROM bulk_jobs j WHERE {base_where} GROUP BY 1
    """), params):
        counts[st] = n
    total = session.execute(text(f"SELECT count(*) FROM bulk_jobs j WHERE {base_where}{status_sql}"), params).scalar()
    rows = [dict(r) for r in session.execute(text(f"""
        SELECT {_light_columns(session)} FROM bulk_jobs j WHERE {base_where}{status_sql}
         ORDER BY j.created_at DESC, j.id DESC LIMIT :lim OFFSET :off
    """), {**params, "lim": size, "off": offset}).mappings()]
    names = user_names(session, [x for r in rows for x in (r["created_by"], r["confirmed_by"],
                                                            r["cancel_requested_by"], r["retried_by"])])
    uploads = _uploads([r["upload_id"] for r in rows])
    return {"items": [serialize_job(r, v, names, uploads) for r in rows], "total": total, "page": page, "page_size": size,
            "counts_by_status": counts,
            "filters": {"kind": kinds or None, "status": statuses or None, "created_by": creator or None,
                        "date_from": args.get("date_from") or None, "date_to": args.get("date_to") or None}}


# =========================================================================== artifacts
def artifact_for_download(job_id, kind_name: str) -> tuple[str, str]:
    """-> (absolute path, download file name). Generates a missing artifact from the rows."""
    if kind_name not in artifacts.KINDS:
        raise ApiError(404, "artifact_not_found", "unknown artifact", {"allowed": list(artifacts.KINDS)})
    v = Viewer()
    job = get_job(v, job_id)
    if not _full_access(v, job):
        raise ApiError(403, "forbidden", "only the job's creator (or SuperAdmin with bulk_jobs_all) can download its files",
                       {"required_any": [ALL_JOBS_KEY]})
    session = _s()
    row = session.execute(text("""
        SELECT * FROM bulk_job_artifacts WHERE job_id = CAST(:j AS uuid) AND kind = :k
    """), {"j": str(job["id"]), "k": kind_name}).mappings().first()
    if row is not None and row["purged_at"] is not None:
        raise ApiError(410, "artifact_purged", "this file has expired and was deleted",
                       {"purged_at": iso(row["purged_at"])})
    if row is None or not storage.exists(row["stored_path"]):
        if not _artifact_available(job, kind_name):
            raise ApiError(409, "artifact_not_ready", f"the {kind_name} file is not available while the job is "
                           f"{effective_status(job)}", {"status": effective_status(job)})
        created = artifacts.write(session, job, kind_name)
        session.commit()
        storage.delete(created["replaced_path"])
        row = created
    return storage.resolve(row["stored_path"]), row["file_name"]


def _artifact_available(job, kind_name) -> bool:
    if job["status"] in ("queued", "validating") or (job["status"] == "retrying" and job["phase"] == "prepare"):
        return False
    if job["status"] == "failed" and job["phase"] == "prepare" and job["preview_ready_at"] is None:
        return False
    if kind_name == "results":
        return (job["phase"] == "execute" and job["status"] in TERMINAL) or (job["read_only"] and job["status"] == "succeeded")
    return True
