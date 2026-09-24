"""Permission keys and route helpers for the Phase 6 AI, quality and voice-lab routes.

The keys are registered in core/permissions.py (by the lead). Gates answer with the v2 error shape
(`403 {"code": "forbidden", "error": {"required_any": [...]}}`), like the other v2 blueprints.

    from services.ai.permissions import AI_ASSIST, ai_permission_required, requires_ai

    @bp.post("/v2/cases/<uuid:case_id>/ai/call-script")
    @jwt_required()
    @ai_permission_required(AI_ASSIST)
    @requires_ai("generate_structured", categories=("case_facts", "notes"))   # fail fast: 503/409
    @idempotent()
    def call_script(case_id): ...

`requires_ai` only checks readiness before the route runs (no provider call, nothing written);
the gateway checks again at call time, so a policy change between the two is still honoured.
"""
from __future__ import annotations

from functools import wraps

AI_ASSIST = "ai_assist"
AI_ADMIN = "ai_admin"
QUALITY_REVIEW = "quality_review"
QUALITY_APPROVE = "quality_approve"
VOICE_LAB = "voice_lab"
ANY_AI_KEY = (AI_ASSIST, AI_ADMIN, QUALITY_REVIEW, QUALITY_APPROVE, VOICE_LAB)


def has(key: str) -> bool:
    from core.auth import has_permission
    return has_permission(key)


def require_any(*keys) -> None:
    from services.case_history_common import require_any as _require_any
    _require_any(*keys)


def ai_permission_required(*keys):
    """Any-of gate. Place below @jwt_required() and above @idempotent()."""
    if not keys:
        raise ValueError("ai_permission_required needs at least one key")

    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            require_any(*keys)
            return fn(*args, **kwargs)
        return wrapper
    return decorator


def requires_ai(capability: str, categories=()):
    """Refuse (503 ai_not_configured / model_not_set, 409 ai_policy_required /
    ai_policy_category_denied) before the route does any work."""
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            from services.ai import gateway
            gateway.ensure_ready(capability, categories)
            return fn(*args, **kwargs)
        return wrapper
    return decorator
