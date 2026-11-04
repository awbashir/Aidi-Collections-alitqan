"""AI calling agent test (customer request 2026-11-04; services/ai/test_calls.py, migration 0092).

  GET  /v2/ai/test-calls/options    voices, default script, outbound numbers, limits, AI readiness,
                                    the AI voice service heartbeat                      ai_admin
  GET  /v2/ai/test-calls            my test calls, newest first (page, page_size)       ai_admin
  POST /v2/ai/test-calls            {destination, caller_id?, voice?, instructions}     ai_admin, Idempotency-Key
       409 ai_not_ready {reasons} | 409 ai_test_call_active | 429 ai_test_call_limit
  GET  /v2/ai/test-calls/<id>       one of my test calls (state, result, transcript)    ai_admin
  POST /v2/ai/test-calls/<id>/stop  hang up / cancel                                    ai_admin

The AI voice service (`python -m ai_voice`) places the call; no route talks to the PBX or to OpenAI.
"""
from __future__ import annotations

from flask import Blueprint
from flask_jwt_extended import jwt_required

from core.api_response import created, json_body, ok, register_api_errors
from core.idempotency import idempotent
from services.ai import test_calls as svc
from services.ai.permissions import AI_ADMIN, ai_permission_required
from services.case_history_common import me, page_args, parse_uuid

bp = Blueprint("ai_test_calls", __name__)
register_api_errors(bp)
URL_PREFIX = None


@bp.after_request
def _no_store(resp):
    resp.headers["Cache-Control"] = "no-store, max-age=0"
    return resp


def _commit():
    from extensions import db
    db.session.commit()


@bp.get("/v2/ai/test-calls/options")
@jwt_required()
@ai_permission_required(AI_ADMIN)
def options():
    return ok(svc.options())


@bp.get("/v2/ai/test-calls")
@jwt_required()
@ai_permission_required(AI_ADMIN)
def list_calls():
    page, size, offset = page_args()
    return ok(svc.list_calls(me(), page, size, offset))


@bp.post("/v2/ai/test-calls")
@jwt_required()
@ai_permission_required(AI_ADMIN)
@idempotent()
def create():
    return created(svc.create(me(), json_body()))          # the idempotency layer commits


@bp.get("/v2/ai/test-calls/<call_id>")
@jwt_required()
@ai_permission_required(AI_ADMIN)
def get_call(call_id):
    return ok(svc.get_call(me(), parse_uuid(call_id, "call_id")))


@bp.post("/v2/ai/test-calls/<call_id>/stop")
@jwt_required()
@ai_permission_required(AI_ADMIN)
def stop(call_id):
    out = svc.stop(me(), parse_uuid(call_id, "call_id"))
    _commit()
    return ok(out)
