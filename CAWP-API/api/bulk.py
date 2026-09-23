"""Phase 2 stream F: private uploads, reviewed bulk jobs and the jobs list
(docs/phase2-spec.md, review/phase2-framework.md for the full contract and examples).

Conventions (core/api_response.py): real HTTP codes, `{message, status, data}` on success,
`{message, status:false, error, code}` on failure, never 401 for a business error. Every
mutation needs an `Idempotency-Key` header (`@idempotent()`); a replay returns the stored
response with `Idempotent-Replayed: true`. Instants are ISO 8601 UTC (`...Z`).

    GET  /v2/bulk/kinds                         kinds, their sources/mapping fields, allowed for me
    POST /v2/uploads                            multipart file (+ purpose) -> 201 inspection result
    GET  /v2/uploads/<id>                       inspection result (uploader)
    POST /v2/bulk/<kind>                        create a job (source + params [+ mapping]) -> 201
    GET  /v2/bulk/jobs/<id>  | /v2/jobs/<id>    job detail (+ attempts, artifacts)
    GET  /v2/bulk/jobs/<id>/preview             eligibility summary by code
    GET  /v2/bulk/jobs/<id>/rows | /v2/jobs/<id>/rows     paged rows (eligibility/outcome/code/q)
    POST /v2/bulk/jobs/<id>/confirm             {expected_revision, accept_partial?}
    POST /v2/bulk/jobs/<id>/cancel              {reason?} -> 200 cancelled | 202 cancel requested
    POST /v2/bulk/jobs/<id>/retry               failed rows / failed validation
    POST /v2/bulk/jobs/<id>/reopen              new job (fresh validation) from the same source -> 201
    GET  /v2/bulk/jobs/<id>/artifacts | /v2/jobs/<id>/artifacts
    GET  /v2/bulk/jobs/<id>/artifacts/<preview|errors|results>   CSV download (permission checked)
    GET  /v2/jobs                               every run: kind, status, created_by, date_from/date_to, paged
"""
from __future__ import annotations

from flask import Blueprint, request, send_file
from flask_jwt_extended import jwt_required

from core.api_response import created, json_body, ok, register_api_errors
from core.bulk import service as svc
from core.idempotency import idempotent

bp = Blueprint("bulk_v2", __name__)
register_api_errors(bp)
URL_PREFIX = None


def _require_any_bulk():
    if not svc.any_bulk_permission():
        from core.bulk import registry
        svc.forbidden(sorted(registry.all_permission_keys() | {svc.ALL_JOBS_KEY}))


# --------------------------------------------------------------------------- kinds / uploads
@bp.get("/v2/bulk/assign/collectors")
@jwt_required()
def assign_collectors():
    """Collectors the caller may pick as an assignment target (same rules as the preview)."""
    from core.auth import has_permission
    from extensions import db
    from services import bulk_assign
    if not has_permission("clints_sepcifyUser"):
        svc.forbidden(["clints_sepcifyUser"])
    items = bulk_assign.eligible_collectors(db.session, svc.Viewer().scope)
    return ok({"items": items, "total": len(items)})


@bp.get("/v2/bulk/kinds")
@jwt_required()
def kinds():
    return ok(svc.kinds_list())


@bp.post("/v2/uploads")
@jwt_required()
@idempotent()
def upload_create():
    return created(svc.create_upload(request.files.get("file"), request.form.get("purpose")), "uploaded")


@bp.get("/v2/uploads/<uuid:upload_id>")
@jwt_required()
def upload_get(upload_id):
    return ok(svc.get_upload(upload_id))


# --------------------------------------------------------------------------- jobs list / detail
@bp.get("/v2/jobs")
@jwt_required()
def jobs_list():
    _require_any_bulk()
    return ok(svc.list_jobs(request.args))


@bp.get("/v2/jobs/<uuid:job_id>")
@bp.get("/v2/bulk/jobs/<uuid:job_id>")
@jwt_required()
def job_detail(job_id):
    return ok(svc.job_detail(job_id))


@bp.get("/v2/bulk/jobs/<uuid:job_id>/preview")
@jwt_required()
def job_preview(job_id):
    return ok(svc.preview_summary(job_id))


@bp.get("/v2/jobs/<uuid:job_id>/rows")
@bp.get("/v2/bulk/jobs/<uuid:job_id>/rows")
@jwt_required()
def job_rows(job_id):
    return ok(svc.list_rows(job_id, request.args))


@bp.get("/v2/jobs/<uuid:job_id>/artifacts")
@bp.get("/v2/bulk/jobs/<uuid:job_id>/artifacts")
@jwt_required()
def job_artifacts(job_id):
    return ok(svc.job_detail(job_id)["artifacts"])


@bp.get("/v2/jobs/<uuid:job_id>/artifacts/<kind_name>")
@bp.get("/v2/bulk/jobs/<uuid:job_id>/artifacts/<kind_name>")
@jwt_required()
def job_artifact_download(job_id, kind_name):
    path, name = svc.artifact_for_download(job_id, kind_name)
    resp = send_file(path, mimetype="text/csv", as_attachment=True, download_name=name, max_age=0,
                     conditional=False, etag=False)
    resp.headers["Cache-Control"] = "private, no-store"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


# --------------------------------------------------------------------------- job mutations
@bp.post("/v2/bulk/jobs/<uuid:job_id>/confirm")
@jwt_required()
@idempotent()
def job_confirm(job_id):
    return ok(svc.confirm(job_id, json_body(required=False)), "confirmed")


@bp.post("/v2/bulk/jobs/<uuid:job_id>/cancel")
@jwt_required()
@idempotent()
def job_cancel(job_id):
    data, status = svc.cancel(job_id, json_body(required=False))
    return ok(data, "cancelled" if status == 200 else "cancel requested", status)


@bp.post("/v2/bulk/jobs/<uuid:job_id>/retry")
@jwt_required()
@idempotent()
def job_retry(job_id):
    return ok(svc.retry(job_id, json_body(required=False)), "retry queued")


@bp.post("/v2/bulk/jobs/<uuid:job_id>/reopen")
@jwt_required()
@idempotent()
def job_reopen(job_id):
    return created(svc.reopen(job_id, json_body(required=False)), "reopened")


# `<kind>` last so the fixed /v2/bulk/jobs/... and /v2/bulk/kinds paths win
@bp.post("/v2/bulk/<kind_key>")
@jwt_required()
@idempotent()
def job_create(kind_key):
    return created(svc.create_job(kind_key, json_body()), "job created")
