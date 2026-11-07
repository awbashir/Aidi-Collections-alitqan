"""Browser softphone passwords on a chan_sip PBX (migration 0083, Alitqan 2026-10-21).

With SIP_CREDENTIAL_MODE=static the PBX cannot check the app's short-lived credentials (that needs
PJSIP realtime auth, services/calling/sip.py). Instead the softphone switch converts the user's
extension to a WebRTC extension on the PBX, which returns a new random SIP password once; it is
kept here encrypted (AES-256-GCM, key SIP_STATIC_SECRET_KEY = 32 bytes, base64, server env only)
and given only to the extension's active owner (sip.issue). Never logged, never in an audit event.
Never commits.
"""
from __future__ import annotations

import base64
import binascii
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import text

from core.api_response import ApiError
from services.calling.common import iso, me, sess

NOT_CONFIGURED = "softphone_not_configured"


def _key() -> bytes:
    raw = (os.getenv("SIP_STATIC_SECRET_KEY") or "").strip()
    try:
        key = base64.b64decode(raw, validate=True) if raw else b""
    except (binascii.Error, ValueError):
        key = b""
    if len(key) != 32:
        raise ApiError(503, NOT_CONFIGURED, "the softphone password key is not configured (SIP_STATIC_SECRET_KEY)",
                       {"setting": "SIP_STATIC_SECRET_KEY"})
    return key


def _aad(extension: str) -> bytes:
    return f"cawp-sip-static:{extension}".encode()       # a row cannot be moved to another extension


def store(extension: str, user_id, password: str) -> None:
    key = _key()
    nonce = os.urandom(12)
    ct = AESGCM(key).encrypt(nonce, password.encode("utf-8"), _aad(extension))
    sess().execute(text("""
        INSERT INTO sip_static_secrets (extension, user_id, ciphertext, nonce, created_by)
        VALUES (:e, CAST(:u AS uuid), :c, :n, CAST(:by AS uuid))
        ON CONFLICT (extension) DO UPDATE
           SET user_id = EXCLUDED.user_id, ciphertext = EXCLUDED.ciphertext, nonce = EXCLUDED.nonce,
               created_at = now(), created_by = EXCLUDED.created_by"""),
        {"e": extension, "u": str(user_id), "c": ct, "n": nonce, "by": me()})


def row(extension: str):
    return sess().execute(text("""SELECT extension, user_id, created_at FROM sip_static_secrets
                                    WHERE extension = :e"""), {"e": extension}).mappings().first()


def password(extension: str) -> str | None:
    r = sess().execute(text("SELECT ciphertext, nonce FROM sip_static_secrets WHERE extension = :e"),
                       {"e": extension}).mappings().first()
    if r is None:
        return None
    return AESGCM(_key()).decrypt(bytes(r["nonce"]), bytes(r["ciphertext"]), _aad(extension)).decode("utf-8")


def delete(extension: str) -> bool:
    return bool(sess().execute(text("DELETE FROM sip_static_secrets WHERE extension = :e"),
                               {"e": extension}).rowcount)


def describe(extension: str) -> dict | None:
    r = row(extension)
    return None if r is None else {"extension": r["extension"], "since": iso(r["created_at"])}
