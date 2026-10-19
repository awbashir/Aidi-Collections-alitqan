"""Previous statuses import ("استيراد الحالات السابقة", customer request 2026-10-19): upload, column
mapping, preview, one-transaction apply in the background, XLSX report. Service:
services/previous_status_import.py; tables: db/migrations/0074_previous_status_imports.sql.

Conventions (core/api_response.py): real HTTP codes, `{message, status, data}`; mutations need an
`Idempotency-Key` (`@idempotent()`).

Gate: `settings_comments_status_add` AND the SuperAdmin role (403 `super_admin_required`).
* the key: the screen sits in the note statuses settings and the import may create sub-statuses,
  which is exactly what that key allows on the settings screen;
* SuperAdmin: the import writes notes on cases of every contract and portfolio, so it is not
  bounded by a supervisor's scope; only the unrestricted scope may run it. Each row is still
  matched to an existing case (exact account number), and every write is audited.

    POST /v2/previous-status-imports/uploads   multipart `file` -> 201 inspection (+ guessed mapping)
    GET  /v2/previous-status-imports/options   fields, contact methods, default date, marker text
                                                [?upload_id&sheet -> headers + guess]
    POST /v2/previous-status-imports/preview   {upload_id, sheet?, mapping?, create_missing_sub_statuses?,
                                                include_archived?} -> counts, problem rows, statuses to
                                                create / missing. Writes nothing.
    POST /v2/previous-status-imports/apply     same + {noted_on: "YYYY-MM-DD" (before today), contact_method_id?,
                                                note?} -> 202 the import (queued); runs in the background.
                                                409 already_imported | import_in_progress
    GET  /v2/previous-status-imports/<id>      status, counts, created sub-statuses, error
    GET  /v2/previous-status-imports/<id>/report  XLSX: skipped rows with reasons, summary, created sub-statuses

`mapping` = {"account_number": "<header>", "main_status": "<header>", "sub_status"?: "<header>"};
omitted -> the guess from the headers.
"""
from __future__ import annotations

import io
import logging
import shutil
import tempfile

from flask import Blueprint, request, send_file
from flask_jwt_extended import get_jwt_identity, jwt_required

from core import jobs
from core.api_response import ApiError, created, json_body, ok, register_api_errors
from core.auth import has_permission
from core.idempotency import idempotent
from extensions import db
from services import money
from services import previous_status_import as svc

bp = Blueprint("previous_status_import_v2", __name__)
register_api_errors(bp)
URL_PREFIX = None
log = logging.getLogger("cawp.api.previous_status_import")


def _me() -> str:
    return str(get_jwt_identity())


def _require() -> str:
    if not has_permission(svc.PERMISSION):
        raise ApiError(403, "forbidden", "You are not authorized to access this resource",
                       {"required_any": [svc.PERMISSION]})
    me = _me()
    if not money.caller_scope(me).is_super:
        raise ApiError(403, "super_admin_required", "only a SuperAdmin can import previous statuses",
                       {"required_role": "SuperAdmin"})
    return me


def _job(_job_id, import_id):
    """Worker entry point: `core.jobs` pushes the app context, we own the session."""
    try:
        return svc.run_import(import_id)
    finally:
        db.session.remove()


def _start(import_id: str, actor: str) -> None:
    """Run the import in the background thread pool. Tests replace this to run inline."""
    jobs.submit("previous-status-import", _job, import_id, owner=actor)


@bp.post("/v2/previous-status-imports/uploads")
@jwt_required()
@idempotent()
def upload_create():
    me = _require()
    return created(svc.create_upload(request.files.get("file"), me), "uploaded")


@bp.get("/v2/previous-status-imports/options")
@jwt_required()
def options():
    me = _require()
    data = svc.options(db.session, me, request.args.get("upload_id"), request.args.get("sheet"))
    db.session.rollback()
    return ok(data)


@bp.post("/v2/previous-status-imports/preview")
@jwt_required()
def preview():
    me = _require()
    return ok(svc.preview(json_body(), me))


@bp.post("/v2/previous-status-imports/apply")
@jwt_required()
@idempotent()
def apply():
    me = _require()
    data = svc.start_apply(json_body(), me)
    # commit before the worker starts, so it sees the queued row
    db.session.commit()
    _start(data["id"], me)
    return ok(data, "import queued", 202)


@bp.get("/v2/previous-status-imports/<uuid:import_id>")
@jwt_required()
def detail(import_id):
    _require()
    data = svc.import_detail(import_id)
    db.session.rollback()
    return ok(data)


@bp.get("/v2/previous-status-imports/<uuid:import_id>/report")
@jwt_required()
def report(import_id):
    _require()
    tmp = tempfile.mkdtemp(prefix="prev-status-")
    try:
        path, name = svc.build_report(import_id, tmp)
        db.session.rollback()
        with open(path, "rb") as f:
            payload = f.read()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    resp = send_file(io.BytesIO(payload), as_attachment=True, download_name=name, max_age=0,
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    resp.headers["Cache-Control"] = "private, no-store"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp
