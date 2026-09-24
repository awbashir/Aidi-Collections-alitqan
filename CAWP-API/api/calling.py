"""Phase 5 P0: human calling foundation - call log, manual log + reviewed wrap-up, calling
settings and calling administration. review/phase5-foundation.md has request/response examples.

Conventions (core/api_response.py): real HTTP codes, `{message, status, data}` on success,
`{message, status:false, error, code}` on failure, never 401 for a business error. Mutating
routes need an `Idempotency-Key` header (@idempotent, which also commits) and, where they edit
an existing row, `expected_revision` (body) or `If-Match`. Every write appends audit_events.
Everything is limited to the caller's case scope; out-of-scope ids answer 404.

My calls and the call log                                  permission
  GET  /v2/calls                     scoped call log        calls
  GET  /v2/calls/mine                my calls               calls | calls_make
  GET  /v2/calls/<id>                detail + evidence      calls | calls_make
  GET  /v2/cases/<case_id>/calls     a case's calls         calls | calls_make
  POST /v2/calls/manual              log a manual call + wrap-up           calls
  POST /v2/calls/<id>/wrapup         wrap up a PBX call (own; others: calls_manage)  calls
Calling blocks
  GET  /v2/cases/<case_id>/calling-blocks                   calls | calls_make
  POST /v2/calling-blocks/<id>/release                      calls_manage
Calling settings (mine)
  GET  /v2/me/calling                extension, preferences, window status, can_call   calls | calls_make
  PUT  /v2/me/calling/preferences    auto-answer + devices                            calls | calls_make
Calling administration
  GET  /v2/calling/status            calling service / PBX session seen from the DB   calls_manage
  GET  /v2/calling/users             active-user picker                               calls_manage
  GET  /v2/calling/extensions        ownership list                                   calls_manage
  GET  /v2/calling/extensions/legacy-drift                                            calls_manage
  POST /v2/calling/extensions/legacy-drift/adopt                                      calls_manage
  GET  /v2/calling/extensions/<ext>/history                                           calls_manage
  POST /v2/calling/extensions/<ext>/assign                                            calls_manage
  POST /v2/calling/extensions/<ext>/release                                           calls_manage
  GET  /v2/calling/windows           policy + Riyadh status + history                  calls | calls_make | calls_manage
  PUT  /v2/calling/windows           replace the policy (reviewed, with reason)       calls_manage

No route here originates a call or talks to the PBX: that is the `calling` service (P1 adds
click-to-call). `can_call` is therefore always false in P0, with the first blocking reason.
"""
from __future__ import annotations

from flask import Blueprint, request
from flask_jwt_extended import jwt_required

from core.api_response import created, json_body, ok, register_api_errors
from core.idempotency import idempotent
from core.revisions import expected_revision_from_request
from services.calling import extensions as ext_svc
from services.calling import queries
from services.calling import settings as settings_svc
from services.calling import wrapup as wrap_svc
from services.calling.common import CALLS, CALLS_MAKE, CALLS_MANAGE, me, page_args, permission_required

bp = Blueprint("calling", __name__)
register_api_errors(bp)
URL_PREFIX = None


# --------------------------------------------------------------------------- calls
@bp.get("/v2/calls")
@jwt_required()
@permission_required(CALLS)
def call_log():
    page, size, offset = page_args()
    return ok(queries.call_log(request.args, page, size, offset))


@bp.get("/v2/calls/mine")
@jwt_required()
@permission_required(CALLS, CALLS_MAKE)
def my_calls():
    page, size, offset = page_args()
    return ok(queries.my_calls(request.args, page, size, offset))


@bp.get("/v2/calls/<uuid:attempt_id>")
@jwt_required()
@permission_required(CALLS, CALLS_MAKE)
def call_detail(attempt_id):
    return ok(queries.call_detail(attempt_id))


@bp.get("/v2/cases/<uuid:case_id>/calls")
@jwt_required()
@permission_required(CALLS, CALLS_MAKE)
def case_calls(case_id):
    page, size, offset = page_args()
    return ok(queries.case_calls(case_id, request.args, page, size, offset))


@bp.post("/v2/calls/manual")
@jwt_required()
@permission_required(CALLS)
@idempotent()
def log_manual_call():
    attempt_id = wrap_svc.log_manual_call(json_body())
    return created(queries.call_detail(attempt_id), "call logged")


@bp.post("/v2/calls/<uuid:attempt_id>/wrapup")
@jwt_required()
@permission_required(CALLS)
@idempotent()
def wrapup(attempt_id):
    body = json_body()
    wrap_svc.wrapup(attempt_id, body, expected_revision_from_request())
    return ok(queries.call_detail(attempt_id), "wrap-up saved")


# --------------------------------------------------------------------------- blocks
@bp.get("/v2/cases/<uuid:case_id>/calling-blocks")
@jwt_required()
@permission_required(CALLS, CALLS_MAKE)
def case_blocks(case_id):
    return ok(queries.case_blocks(case_id))


@bp.post("/v2/calling-blocks/<uuid:block_id>/release")
@jwt_required()
@permission_required(CALLS_MANAGE)
@idempotent()
def release_block(block_id):
    out = wrap_svc.release_block(block_id, json_body())
    return ok(queries.case_blocks(out["case_id"]), "calling block released")


# --------------------------------------------------------------------------- my settings
def _my_calling() -> dict:
    user_id = me()
    extension = ext_svc.my_extension(user_id)
    window = settings_svc.window_status()
    status = queries.service_status()
    if extension is None:
        reason = "no_extension"
    elif not window["configured"]:
        reason = "calling_windows_missing"
    elif not window["open"]:
        reason = window["reason"]
    elif not (status["pbx"] or {}).get("connected"):
        reason = "transport_not_connected"
    else:
        reason = "click_to_call_not_enabled"          # P1 enables placement
    return {"user_id": user_id, "extension": extension, "preferences": settings_svc.get_preferences(user_id),
            "window": window, "can_call": False, "reason": reason}


@bp.get("/v2/me/calling")
@jwt_required()
@permission_required(CALLS, CALLS_MAKE)
def my_calling():
    return ok(_my_calling())


@bp.put("/v2/me/calling/preferences")
@jwt_required()
@permission_required(CALLS, CALLS_MAKE)
@idempotent()
def save_preferences():
    body = json_body()
    settings_svc.save_preferences(me(), body, expected_revision_from_request())
    return ok(_my_calling(), "preferences saved")


# --------------------------------------------------------------------------- administration
@bp.get("/v2/calling/status")
@jwt_required()
@permission_required(CALLS_MANAGE)
def calling_status():
    return ok({**queries.service_status(), "window": settings_svc.window_status()})


@bp.get("/v2/calling/users")
@jwt_required()
@permission_required(CALLS_MANAGE)
def user_picker():
    page, size, offset = page_args()
    return ok(queries.user_picker(request.args, page, size, offset))


@bp.get("/v2/calling/extensions")
@jwt_required()
@permission_required(CALLS_MANAGE)
def list_extensions():
    page, size, offset = page_args()
    return ok(ext_svc.list_extensions(request.args, page, size, offset))


@bp.get("/v2/calling/extensions/legacy-drift")
@jwt_required()
@permission_required(CALLS_MANAGE)
def legacy_drift():
    page, size, offset = page_args()
    return ok(ext_svc.legacy_drift(request.args, page, size, offset))


@bp.post("/v2/calling/extensions/legacy-drift/adopt")
@jwt_required()
@permission_required(CALLS_MANAGE)
@idempotent()
def adopt_legacy():
    return created(ext_svc.adopt_legacy(json_body()), "legacy extension adopted")


@bp.get("/v2/calling/extensions/<extension>/history")
@jwt_required()
@permission_required(CALLS_MANAGE)
def extension_history(extension):
    return ok(ext_svc.extension_history(extension))


@bp.post("/v2/calling/extensions/<extension>/assign")
@jwt_required()
@permission_required(CALLS_MANAGE)
@idempotent()
def assign_extension(extension):
    return ok(ext_svc.assign(extension, json_body()), "extension assigned")


@bp.post("/v2/calling/extensions/<extension>/release")
@jwt_required()
@permission_required(CALLS_MANAGE)
@idempotent()
def release_extension(extension):
    return ok(ext_svc.release(extension, json_body()), "extension released")


@bp.get("/v2/calling/windows")
@jwt_required()
@permission_required(CALLS, CALLS_MAKE, CALLS_MANAGE)
def get_windows():
    return ok(settings_svc.windows_view())


@bp.put("/v2/calling/windows")
@jwt_required()
@permission_required(CALLS_MANAGE)
@idempotent()
def save_windows():
    body = json_body()
    return ok(settings_svc.save_window_policy(body, expected_revision_from_request()), "calling windows saved")
