"""Response convention for NEW routes only (Phase 0+).

Legacy routes keep their frozen contract (HTTP 201 + `{"status": false}` on business
errors, see core/http.py) and must not use this module. New blueprints use real HTTP
status codes:

    success   2xx  {"message": str, "status": true,  "data": ...}
    failure   4xx  {"message": str, "status": false, "error": ..., "code": "snake_case"}

`code` is a stable machine-readable identifier the frontend can switch on; `message` is
human text (Arabic or English); `error` carries details (field errors, the current
revision on a conflict, ...) or null.

Usage in a new blueprint:

    from core.api_response import ok, created, ApiError, register_api_errors

    bp = Blueprint("cases_v2", __name__)
    register_api_errors(bp)              # ApiError / RevisionConflict -> JSON, this bp only

    @bp.post("/v2/cases/<uuid:case_id>/notes")
    @jwt_required()
    def add_note(case_id):
        body = json_body()               # 400 invalid_json on a malformed body
        if not body.get("text"):
            raise ApiError(422, "validation_failed", "text is required", {"text": "required"})
        ...
        return created({"id": str(note_id)}, "saved")

Never return 401 for a business error: the frontend force-logs-out on 401.
"""
from __future__ import annotations

import logging

from flask import jsonify, request

log = logging.getLogger("cawp.api")

# Stable codes shared by the Phase 0 helpers. Later phases add their own.
BAD_REQUEST = "bad_request"
INVALID_JSON = "invalid_json"
FORBIDDEN = "forbidden"
NOT_FOUND = "not_found"
CONFLICT = "conflict"
REVISION_CONFLICT = "revision_conflict"
VALIDATION_FAILED = "validation_failed"
IDEMPOTENCY_KEY_REQUIRED = "idempotency_key_required"
IDEMPOTENCY_KEY_INVALID = "idempotency_key_invalid"
IDEMPOTENCY_KEY_REUSED = "idempotency_key_reused"
IDEMPOTENCY_IN_PROGRESS = "idempotency_request_in_progress"
INTERNAL_ERROR = "internal_error"


class ApiError(Exception):
    """Raise from a new route; `register_api_errors` turns it into a failure response."""

    def __init__(self, status: int, code: str, message: str, error=None):
        super().__init__(message)
        if status == 401:
            raise ValueError("business errors must not use 401 (the frontend logs the user out)")
        self.status = status
        self.code = code
        self.message = message
        self.error = error


def ok(data=None, message: str = "ok", status: int = 200):
    return jsonify({"message": message, "status": True, "data": data}), status


def created(data=None, message: str = "created"):
    return ok(data, message, 201)


def fail(status: int, code: str, message: str, error=None):
    if status < 400 or status == 401:
        raise ValueError(f"fail() needs a 4xx/5xx status other than 401, got {status}")
    return jsonify({"message": message, "status": False, "error": error, "code": code}), status


def bad_request(message="bad request", error=None, code=BAD_REQUEST):
    return fail(400, code, message, error)


def forbidden(message="You are not authorized to access this resource", error=None, code=FORBIDDEN):
    return fail(403, code, message, error)


def not_found(message="not found", error=None, code=NOT_FOUND):
    return fail(404, code, message, error)


def conflict(message="conflict", error=None, code=CONFLICT):
    return fail(409, code, message, error)


def unprocessable(message="validation failed", error=None, code=VALIDATION_FAILED):
    return fail(422, code, message, error)


def json_body(required: bool = True) -> dict:
    """The request's JSON object, or ApiError(400, invalid_json)."""
    data = request.get_json(silent=True)
    if data is None:
        if not required and not request.get_data():
            return {}
        raise ApiError(400, INVALID_JSON, "request body must be a JSON object")
    if not isinstance(data, dict):
        raise ApiError(400, INVALID_JSON, "request body must be a JSON object")
    return data


def register_api_errors(blueprint):
    """Install ApiError / RevisionConflict handlers on ONE blueprint (never app-wide,
    so legacy routes keep their own error bodies)."""
    from core.revisions import RevisionConflict, RevisionNotFound

    @blueprint.errorhandler(ApiError)
    def _api_error(e: ApiError):
        return fail(e.status, e.code, e.message, e.error)

    @blueprint.errorhandler(RevisionConflict)
    def _revision_conflict(e: RevisionConflict):
        return fail(409, REVISION_CONFLICT, str(e), e.as_error())

    @blueprint.errorhandler(RevisionNotFound)
    def _revision_not_found(e: RevisionNotFound):
        return fail(404, NOT_FOUND, str(e), {"entity": e.entity, "id": str(e.entity_id)})

    return blueprint
