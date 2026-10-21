"""Portfolio upload ("رفع المحافظ"): column mapping, preview, one-transaction import, report,
and the previous (archived) record of a re-imported case. Service: services/portfolio_import.py;
tables: db/migrations/0070_portfolio_imports.sql.

Conventions (core/api_response.py): real HTTP codes, `{message, status, data}`; mutations need
an `Idempotency-Key` (`@idempotent()`). Permission: `clints_add` (the key the /clients screen
checks before it shows the add-by-Excel button).

    POST /v2/portfolio-imports/uploads          multipart `file` -> 201 inspection (+ guessed mapping)
    GET  /v2/portfolio-imports/fields           field catalogue, contact types [?upload_id&sheet -> headers + guess]
    POST /v2/portfolio-imports/preview          {upload_id, sheet?, contract_id, portfolio_name, mapping} -> counts,
                                                first problem rows, blocking reasons. Writes nothing.
    POST /v2/portfolio-imports/commit           same body -> 202 the import (queued); runs in the background.
                                                409 already_imported | import_in_progress | portfolio_name_exists
    GET  /v2/portfolio-imports/<id>             status, counts, error, portfolio
    GET  /v2/portfolio-imports/<id>/rows        report rows (?outcome=&page=&page_size=)
    GET  /v2/portfolio-imports/<id>/report      XLSX report, Arabic labels (succeeded imports)
    GET  /v2/cases/<case_id>/previous-records   the archived record(s) a case replaced, read-only history

`mapping` = {"fields": {"name": "<header>", "identity_number": ..., "account_number": ...,
"amount": ..., "collector_username"?: ...}, "contacts": [{"column": "<header>", "contact_type_id": "<uuid>"}]}.
"""
from __future__ import annotations

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
from services import portfolio_import as svc

bp = Blueprint("portfolio_import_v2", __name__)
register_api_errors(bp)
URL_PREFIX = None
log = logging.getLogger("cawp.api.portfolio_import")

PREVIOUS_RECORDS_KEYS = ("clints_details", "clints")


def _me() -> str:
    return str(get_jwt_identity())


def _require(*keys):
    if not any(has_permission(k) for k in keys):
        raise ApiError(403, "forbidden", "You are not authorized to access this resource", {"required_any": list(keys)})


def _job(_job_id, import_id):
    """Worker entry point: `core.jobs` pushes the app context, we own the session."""
    try:
        return svc.run_import(import_id)
    finally:
        db.session.remove()


def _start(import_id: str, actor: str) -> None:
    """Run the import in the background thread pool. Tests replace this to run inline."""
    jobs.submit("portfolio-import", _job, import_id, owner=actor)


@bp.post("/v2/portfolio-imports/uploads")
@jwt_required()
@idempotent()
def upload_create():
    _require(svc.PERMISSION)
    return created(svc.create_upload(request.files.get("file"), _me()), "uploaded")


@bp.get("/v2/portfolio-imports/fields")
@jwt_required()
def fields():
    _require(svc.PERMISSION)
    data = svc.catalogue(db.session, _me(), request.args.get("upload_id"), request.args.get("sheet"))
    db.session.rollback()
    return ok(data)


@bp.post("/v2/portfolio-imports/preview")
@jwt_required()
def preview():
    _require(svc.PERMISSION)
    return ok(svc.preview(json_body(), _me()))


@bp.post("/v2/portfolio-imports/commit")
@jwt_required()
@idempotent()
def commit():
    _require(svc.PERMISSION)
    actor = _me()
    data = svc.start_commit(json_body(), actor)
    # commit before the worker starts, so it sees the queued row (the idempotent decorator
    # then stores the response in a second, separate commit)
    db.session.commit()
    _start(data["id"], actor)
    return ok(data, "import queued", 202)


@bp.get("/v2/portfolio-imports/<uuid:import_id>")
@jwt_required()
def detail(import_id):
    _require(svc.PERMISSION)
    data = svc.import_detail(import_id, _me(), money.caller_scope(_me()))
    db.session.rollback()
    return ok(data)


@bp.get("/v2/portfolio-imports/<uuid:import_id>/rows")
@jwt_required()
def rows(import_id):
    _require(svc.PERMISSION)
    data = svc.import_rows(import_id, _me(), money.caller_scope(_me()), request.args)
    db.session.rollback()
    return ok(data)


@bp.get("/v2/portfolio-imports/<uuid:import_id>/report")
@jwt_required()
def report(import_id):
    _require(svc.PERMISSION)
    tmp = tempfile.mkdtemp(prefix="pf-import-")
    try:
        path, name = svc.build_report(import_id, _me(), money.caller_scope(_me()), tmp)
        db.session.rollback()
        with open(path, "rb") as f:
            payload = f.read()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    import io
    resp = send_file(io.BytesIO(payload), as_attachment=True, download_name=name, max_age=0,
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    resp.headers["Cache-Control"] = "private, no-store"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


@bp.get("/v2/cases/<uuid:case_id>/previous-records")
@jwt_required()
def previous_records(case_id):
    _require(*PREVIOUS_RECORDS_KEYS)
    money.require_case(case_id, money.caller_scope(_me()), grant=True)
    data = svc.previous_records(case_id, has_permission)
    db.session.rollback()
    return ok(data)
