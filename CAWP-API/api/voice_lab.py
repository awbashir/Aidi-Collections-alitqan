"""Phase 6 Q3: the voice training lab (review/phase6-voicelab.md). Permission key `voice_lab`.

    GET  /v2/ai/voice-lab/scenarios            the fictional catalogue, limits, readiness
    POST /v2/ai/voice-lab/sessions             {scenario_id, language} -> an ephemeral Realtime secret
    POST /v2/ai/voice-lab/sessions/<id>/end    {outcome, connected_seconds?} -> records the end
    GET  /v2/ai/voice-lab/sessions             my sessions (paged), my open session, my usage

FICTIONAL TRAINING ONLY (docs/phase6-spec.md hard rule 6): no case, contact or PBX is involved and
no real debtor data is read. The persona instructions are built on the server from the scenario;
the browser receives the short-lived client secret and connects to the provider itself (WebRTC).
The permanent key never leaves the server.

- Not configured / no approved policy: 503 `ai_not_configured` / `model_not_set`, 409
  `ai_policy_required` before anything is written (`requires_ai`); the gateway checks again.
- One open session per user (409 `voice_lab_session_active`), per-user rate limits (429
  `voice_lab_rate_limited`), a 5-minute limit (`max_seconds`, also the secret's `expires_in`).
- POST /sessions is deliberately NOT `@idempotent`: a stored response would keep the client
  secret in `idempotency_keys`. The one-open-session rule stops duplicates instead.
- Every response is `Cache-Control: no-store`. The secret is never logged or stored.
"""
from __future__ import annotations

from flask import Blueprint, request
from flask_jwt_extended import jwt_required

from core.api_response import VALIDATION_FAILED, ApiError, created, json_body, ok, register_api_errors
from core.idempotency import idempotent
from services.ai import voice_lab as svc
from services.ai import voice_scenarios as vs
from services.ai.permissions import VOICE_LAB, ai_permission_required, requires_ai
from services.case_history_common import me, page_args

bp = Blueprint("voice_lab", __name__)
register_api_errors(bp)
URL_PREFIX = None


@bp.after_request
def _no_store(resp):
    resp.headers["Cache-Control"] = "no-store, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    return resp


@bp.get("/v2/ai/voice-lab/scenarios")
@jwt_required()
@ai_permission_required(VOICE_LAB)
def scenarios():
    return ok({"scenarios": vs.public_catalogue(), "languages": list(vs.LANGUAGES), "limits": svc.limits(),
               "readiness": svc.readiness(), "fictional": True})


def _start_body():
    body = json_body()
    errors = {}
    scenario = vs.get(body.get("scenario_id"))
    if scenario is None:
        errors["scenario_id"] = "unknown" if body.get("scenario_id") else "required"
    language = body.get("language")
    if language not in vs.LANGUAGES:
        errors["language"] = "invalid" if language else "required"
    extra = sorted(set(body) - {"scenario_id", "language"})
    if extra:                # nothing else is accepted: no case, contact, name or free text
        errors.update({k: "not_allowed" for k in extra})
    if errors:
        raise ApiError(422, VALIDATION_FAILED, "invalid voice lab session request", errors)
    return scenario, language


@bp.post("/v2/ai/voice-lab/sessions")
@jwt_required()
@ai_permission_required(VOICE_LAB)
@requires_ai("realtime_session")
def start_session():
    scenario, language = _start_body()
    return created(svc.start(user_id=me(), scenario=scenario, language=language,
                             request_id=request.headers.get("X-Request-ID")), "voice lab session started")


@bp.post("/v2/ai/voice-lab/sessions/<uuid:session_id>/end")
@jwt_required()
@ai_permission_required(VOICE_LAB)
@idempotent(required=False)
def end_session(session_id):
    body = json_body(required=False) or {}
    errors = {}
    outcome = body.get("outcome", "user_ended")
    if outcome not in svc.END_OUTCOMES:
        errors["outcome"] = "invalid"
    conn = body.get("connected_seconds")
    if conn is not None and (isinstance(conn, bool) or not isinstance(conn, (int, float)) or not 0 <= conn <= 3600):
        errors["connected_seconds"] = "invalid"
    extra = sorted(set(body) - {"outcome", "connected_seconds"})
    if extra:                # no transcript, notes or audio are accepted
        errors.update({k: "not_allowed" for k in extra})
    if errors:
        raise ApiError(422, VALIDATION_FAILED, "invalid voice lab end request", errors)
    out = svc.end(user_id=me(), session_id=str(session_id), outcome=outcome,
                  connected_seconds=None if conn is None else int(conn))
    return ok(out, "voice lab session ended" if out["changed"] else "voice lab session already finished")


@bp.get("/v2/ai/voice-lab/sessions")
@jwt_required()
@ai_permission_required(VOICE_LAB)
def my_sessions():
    page, size, offset = page_args()
    return ok(svc.list_mine(user_id=me(), page=page, size=size, offset=offset))
