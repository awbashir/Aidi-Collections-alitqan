"""Call control: transfers, conferences, supervisor listening (customer request 2026-10-20;
services/calling/call_control.py, migration 0075). No route here talks to the PBX: each writes a
call_control_ops row that the `calling` service (the only AMI owner) claims, re-checks against
the PBX channel list and sends once. AMI Success = submitted.

                                                                                     permission
  GET  /v2/me/call-controls              capabilities + my open controls             calls | calls_make
  GET  /v2/calling/control/targets       ?q=  app users' extensions + presence       calls_make
  POST /v2/calling/control/transfers     {mode: blind|attended, target_extension,    calls_make
                                          attempt_id?, remote?}  -> 202 control
  POST /v2/calling/control/conferences   {target_extension, attempt_id?, remote?}   calls_make
  POST /v2/calling/control/spy           {call_id (live call id), extension (the     calls_supervise | calls_manage
                                          agent's), mode: listen|whisper|barge}
  GET  /v2/calling/control/<id>          the control + what the PBX shows now        requester / target user /
                                                                                     supervisors (not a spy)
  POST /v2/calling/control/<id>/<op>     complete | cancel | remove | mode {mode} |  the requester
                                         stop
  GET  /v2/calling/control/incoming/<tag>  a ringing call's X-CAWP-Control: role,    calls | calls_make
                                         who, case link (in my scope), auto_answer
  GET  /v2/calling/controls              ?kind&page&page_size  the control log in    calls_supervise | calls_manage
                                         the monitor scope

Errors: 409 no_connected_call | ambiguous_call | call_not_two_party | not_your_call |
control_in_progress | control_busy | control_not_active | call_not_live | call_not_connected,
422 target_not_app_user (external numbers and non-users are never targets), 403 not_in_scope,
503 transport_not_connected | live_calls_unknown.
"""
from __future__ import annotations

from flask import Blueprint, request
from flask_jwt_extended import jwt_required

from core.api_response import json_body, ok, register_api_errors
from core.idempotency import idempotent
from services.calling import call_control as svc
from services.calling.common import CALLS, CALLS_MAKE, SUPERVISE_KEYS, permission_required

bp = Blueprint("calling_control", __name__)
register_api_errors(bp)
URL_PREFIX = None


@bp.get("/v2/me/call-controls")
@jwt_required()
@permission_required(CALLS, CALLS_MAKE, *SUPERVISE_KEYS)
def my_controls():
    return ok(svc.mine())


@bp.get("/v2/calling/control/targets")
@jwt_required()
@permission_required(CALLS_MAKE)
def control_targets():
    return ok(svc.targets(request.args))


@bp.post("/v2/calling/control/transfers")
@jwt_required()
@permission_required(CALLS_MAKE)
@idempotent()
def start_transfer():
    cid = svc.request_call_control("transfer", json_body())
    return ok(svc.view(cid), "transfer submitted", 202)


@bp.post("/v2/calling/control/conferences")
@jwt_required()
@permission_required(CALLS_MAKE)
@idempotent()
def start_conference():
    cid = svc.request_call_control("conference", json_body())
    return ok(svc.view(cid), "conference submitted", 202)


@bp.post("/v2/calling/control/spy")
@jwt_required()
@permission_required(*SUPERVISE_KEYS)
@idempotent()
def start_spy():
    cid = svc.request_spy(json_body())
    return ok(svc.view(cid), "listening submitted", 202)


@bp.get("/v2/calling/control/incoming/<tag>")
@jwt_required()
@permission_required(CALLS, CALLS_MAKE, *SUPERVISE_KEYS)
def incoming_control(tag):
    return ok(svc.incoming(tag))


@bp.get("/v2/calling/control/<uuid:control_id>")
@jwt_required()
@permission_required(CALLS, CALLS_MAKE, *SUPERVISE_KEYS)
def control_view(control_id):
    return ok(svc.view(control_id))


@bp.post("/v2/calling/control/<uuid:control_id>/<op>")
@jwt_required()
@permission_required(CALLS_MAKE, *SUPERVISE_KEYS)
@idempotent()
def control_command(control_id, op):
    cid = svc.command(control_id, op, json_body(required=False))
    return ok(svc.view(cid), f"{op} submitted", 202)


@bp.get("/v2/calling/controls")
@jwt_required()
@permission_required(*SUPERVISE_KEYS)
def control_log():
    return ok(svc.history(request.args))
