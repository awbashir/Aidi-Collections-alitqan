"""Phase 6 Q0: AI status, the AI data policy and the provider-call audit (review/phase6-foundation.md).

    GET  /v2/ai/status               readiness per capability + reason codes    any AI key (details: ai_admin)
    GET  /v2/ai/policy               the current AI data policy + options      ai_admin
    PUT  /v2/ai/policy               record a new policy revision              ai_admin, Idempotency-Key,
                                                                               expected_revision, reason, confirmed
    GET  /v2/ai/policy/history       every revision, newest first              ai_admin
    GET  /v2/ai/provider-calls       provider-call audit (metadata only)       ai_admin
    POST /v2/ai/check                a minimal live provider call (no data)    ai_admin

Q1, AI assistance drafts (review/phase6-drafts.md; services/ai/drafts*.py), all `ai_assist`, case scope
enforced (404 outside it):

    POST  /v2/ai/drafts                          generate a draft (kind, case_id | attempt_id, language,
                                                 options) or an honest refusal; Idempotency-Key required
    GET   /v2/ai/drafts?case=|attempt=           the drafts of a case / call (paged; kind, status, mine)
    GET   /v2/ai/drafts/<id>                     one draft (content, text, checks, citations, provenance)
    PATCH /v2/ai/drafts/<id>                     edited text (expected_revision, Idempotency-Key)
    POST  /v2/ai/drafts/<id>/save-as-note        THE explicit action: the text becomes a case note
                                                 (status/method chosen by the user; no callback)
    POST  /v2/ai/drafts/<id>/discard             discard (the content is cleared)

A draft never changes a case, books a payment, creates a promise, authorizes a call or sends a message.

"Any AI key" = ai_assist, ai_admin, quality_review, quality_approve or voice_lab. Responses are
`Cache-Control: no-store`. The key is never returned; ai_admin sees whether it is configured.

Q1 extends this blueprint with the draft routes (export `bp`; registered by the existing
"api.ai" entry in api/__init__.py).
"""
from __future__ import annotations

from flask import Blueprint, request
from flask_jwt_extended import jwt_required

from core.api_response import VALIDATION_FAILED, ApiError, created, json_body, ok, register_api_errors
from core.idempotency import idempotent
from core.revisions import expected_revision_from_request
from services.ai import calls as calls_svc
from services.ai import drafts as drafts_svc
from services.ai import gateway
from services.ai import policy as policy_svc
from services.ai.permissions import AI_ADMIN, AI_ASSIST, ANY_AI_KEY, ai_permission_required, has
from services.case_history_common import me, page_args, paged, parse_uuid, required_text

bp = Blueprint("ai", __name__)
register_api_errors(bp)
URL_PREFIX = None


@bp.after_request
def _no_store(resp):
    resp.headers["Cache-Control"] = "no-store, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    return resp


def _db():
    from extensions import db
    return db.session


@bp.get("/v2/ai/status")
@jwt_required()
@ai_permission_required(*ANY_AI_KEY)
def ai_status():
    return ok(gateway.status(details=has(AI_ADMIN)))


@bp.get("/v2/ai/policy")
@jwt_required()
@ai_permission_required(AI_ADMIN)
def get_policy():
    pol = policy_svc.current()
    return ok({"policy": pol.as_dict() if pol else None, "revision": pol.revision if pol else 0,
               "options": policy_svc.options()})


@bp.put("/v2/ai/policy")
@jwt_required()
@ai_permission_required(AI_ADMIN)
@idempotent()
def put_policy():
    body = json_body()
    expected = expected_revision_from_request()
    reason = required_text(body, "reason", max_len=2000)
    values = policy_svc.parse_body(body)
    pol = policy_svc.save(values, expected_revision=expected, reason=reason, actor_user_id=me(),
                          request_id=request.headers.get("X-Request-ID"),
                          idempotency_key=request.headers.get("Idempotency-Key"), session=_db())
    return ok({"policy": pol.as_dict(), "revision": pol.revision}, "AI data policy recorded")


@bp.get("/v2/ai/policy/history")
@jwt_required()
@ai_permission_required(AI_ADMIN)
def policy_history():
    page, size, _ = page_args()
    items, total = policy_svc.history(page, size)
    return ok(paged(items, total, page, size))


_OUTCOMES = ("started", "succeeded", "failed", "refused", "interrupted")


@bp.get("/v2/ai/provider-calls")
@jwt_required()
@ai_permission_required(AI_ADMIN)
def provider_calls():
    page, size, _ = page_args()
    a = request.args
    filters = {k: a.get(k) for k in ("purpose", "capability", "outcome", "entity_type", "entity_id")}
    if filters["outcome"] and filters["outcome"] not in _OUTCOMES:
        raise ApiError(422, VALIDATION_FAILED, f"outcome must be one of {', '.join(_OUTCOMES)}", {"outcome": "invalid"})
    if a.get("actor_user_id"):
        filters["actor_user_id"] = parse_uuid(a["actor_user_id"], "actor_user_id")
    if a.get("case_id"):
        filters["case_id"] = parse_uuid(a["case_id"], "case_id")
    items, total = calls_svc.list_calls(page=page, size=size, filters=filters, session=_db())
    return ok(paged(items, total, page, size))


_CHECK_SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"],
                 "additionalProperties": False}


@bp.post("/v2/ai/check")
@jwt_required()
@ai_permission_required(AI_ADMIN)
def provider_check():
    """One minimal structured call with a fixed text (no data category), to prove the key, model and
    policy work end to end. Audited like any provider call. Not @idempotent: it changes nothing."""
    from services.ai.provider import Message
    out = gateway.generate_structured(
        purpose="connectivity_check", categories=(), schema=_CHECK_SCHEMA, schema_name="connectivity_check",
        instructions='This is an automated connectivity check. Answer with {"ok": true}.',
        messages=[Message("user", "Connectivity check. There is no data in this request.")],
        max_output_tokens=200, entity_type="ai_configuration")
    return ok({"ok": out.data.get("ok") is True, "call_id": out.call_id, "model": out.model,
               "policy_revision": out.policy_revision, "latency_ms": out.latency_ms, "attempts": out.attempts,
               "usage": out.usage.as_dict()})


# ------------------------------------------------------------------------------ Q1: drafts
@bp.post("/v2/ai/drafts")
@jwt_required()
@ai_permission_required(AI_ASSIST)
def create_draft():
    """Generate a draft. Not @idempotent: the Idempotency-Key is kept on the draft row itself, so the
    draft's content is never copied into idempotency_keys (services/ai/drafts.py)."""
    draft, replayed = drafts_svc.create(json_body())
    resp, status = created(draft, "AI draft generated; it is a draft only and nothing was changed")
    if replayed:
        resp.headers["Idempotent-Replayed"] = "true"
    return resp, status


@bp.get("/v2/ai/drafts")
@jwt_required()
@ai_permission_required(AI_ASSIST)
def list_drafts():
    page, size, _ = page_args()
    return ok(drafts_svc.list_drafts(request.args, page, size))


@bp.get("/v2/ai/drafts/<uuid:draft_id>")
@jwt_required()
@ai_permission_required(AI_ASSIST)
def get_draft(draft_id):
    return ok(drafts_svc.get(draft_id))


@bp.patch("/v2/ai/drafts/<uuid:draft_id>")
@jwt_required()
@ai_permission_required(AI_ASSIST)
@idempotent()
def edit_draft(draft_id):
    body = json_body()
    return ok(drafts_svc.edit(draft_id, body, expected_revision_from_request()), "draft updated")


@bp.post("/v2/ai/drafts/<uuid:draft_id>/save-as-note")
@jwt_required()
@ai_permission_required(AI_ASSIST)
@idempotent()
def save_draft_as_note(draft_id):
    body = json_body()
    return created(drafts_svc.save_as_note(draft_id, body, expected_revision_from_request()),
                   "the reviewed text was saved as a case note")


@bp.post("/v2/ai/drafts/<uuid:draft_id>/discard")
@jwt_required()
@ai_permission_required(AI_ASSIST)
@idempotent()
def discard_draft(draft_id):
    body = json_body(required=False)
    return ok(drafts_svc.discard(draft_id, body, expected_revision_from_request()), "draft discarded")
