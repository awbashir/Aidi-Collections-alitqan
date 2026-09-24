"""Phase 5 P3: SIP credentials for the in-app softphone (review/phase5-softphone.md).

    POST /v2/me/sip-credentials          issue a short-lived credential for MY extension   calls_make
    GET  /v2/me/sip-credentials          my credential status (never the secret)          calls_make
    POST /v2/me/sip-credentials/revoke   revoke my current credential (softphone off)     calls_make

Only the active owner of an extension (`user_extensions`) gets a credential. The password is in
the POST response body only: it is not stored (the database keeps HA1 only), not logged, not
audited, and the POST is deliberately NOT @idempotent, because the idempotency store keeps the
response body. A repeated POST issues a new credential and supersedes the previous one, so a
retry is harmless; an Idempotency-Key header is accepted and ignored. Every issue and revoke is
audited (credential id, extension, expiry). Responses are `Cache-Control: no-store`.

Registered by the lead in api/__init__.py (MODULES: "api.calling_sip").
"""
from __future__ import annotations

from flask import Blueprint
from flask_jwt_extended import jwt_required

from core.api_response import ok, register_api_errors
from services.calling import sip as sip_svc
from services.calling.common import CALLS_MAKE, me, permission_required, sess

bp = Blueprint("calling_sip", __name__)
register_api_errors(bp)
URL_PREFIX = None


def _no_store(result):
    body, status = result
    body.headers["Cache-Control"] = "no-store, max-age=0"
    body.headers["Pragma"] = "no-cache"
    return body, status


@bp.post("/v2/me/sip-credentials")
@jwt_required()
@permission_required(CALLS_MAKE)
def issue_sip_credentials():
    try:
        data = sip_svc.issue(me())
        sess().commit()
    except Exception:
        sess().rollback()
        raise
    return _no_store(ok(data, "credential issued; keep it in memory only", 201))


@bp.get("/v2/me/sip-credentials")
@jwt_required()
@permission_required(CALLS_MAKE)
def sip_credentials_status():
    return _no_store(ok(sip_svc.status(me())))


@bp.post("/v2/me/sip-credentials/revoke")
@jwt_required()
@permission_required(CALLS_MAKE)
def revoke_sip_credentials():
    try:
        data = sip_svc.revoke_mine(me())
        sess().commit()
    except Exception:
        sess().rollback()
        raise
    return _no_store(ok(data, "credential revoked"))
