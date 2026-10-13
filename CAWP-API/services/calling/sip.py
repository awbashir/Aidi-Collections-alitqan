"""Short-lived SIP credentials for the in-app softphone (Phase 5 P3, migration 0056).

Design (review/phase5-softphone.md §2):

* Only the ACTIVE owner of an extension (`user_extensions`, the source of truth) is issued a
  credential, and only with `calls_make`. The legacy `users.extension` never authorizes.
* Each issue generates a fresh random password (192 bits, URL-safe), returns it ONCE in the
  response body and keeps it nowhere: the database stores only HA1 = MD5(ext:realm:password)
  in `sip_credentials`, the audit event carries the credential id, extension and expiry only,
  and the route is deliberately not @idempotent (the idempotency store would keep the body).
* The PBX reads the credential through its realtime PJSIP auth backend from the view
  `pbx_ps_auths`, which returns a row only while the credential is current, unexpired (database
  clock), still owned and the user active. Expiry is therefore enforced by the PBX lookup, and
  a new issue for the extension supersedes (revokes) the previous one at once.
* Issuing also clears the HA1 of every expired credential (nothing password-equivalent is kept
  longer than needed) and is rate limited per user.

Nothing here talks to the PBX. Nothing here logs the password or the HA1.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time

from sqlalchemy import text

from core import audit
from core.api_response import ApiError
from services.calling import extensions as ext_svc
from services.calling import settings as settings_svc
from services.calling.common import advisory_lock, iso, sess, user_row
from services.calling.sip_config import SipSettings, load
from services.calling.sip_tag import ATTEMPT_HEADER

NO_EXTENSION = "no_extension"
TOO_MANY = "too_many_credentials"
PASSWORD_BYTES = 24                      # 192 bits -> 32 URL-safe characters


def ha1(username: str, realm: str, password: str) -> str:
    """RFC 2617 HA1 for MD5 digest: MD5("username:realm:password"), lowercase hex."""
    return hashlib.md5(f"{username}:{realm}:{password}".encode("utf-8")).hexdigest()  # noqa: S324 - SIP digest


def new_password() -> str:
    return secrets.token_urlsafe(PASSWORD_BYTES)


def turn_credentials(settings: SipSettings, user_id: str, now: float | None = None) -> list[dict]:
    """ICE servers for the browser: STUN as configured, TURN with a short-lived REST credential
    (coturn `use-auth-secret`: username "<expiry>:<id>", credential base64(HMAC-SHA1(secret, username)))."""
    servers: list[dict] = []
    if settings.stun_urls:
        servers.append({"urls": list(settings.stun_urls)})
    if settings.turn_urls and settings.turn_secret:
        expiry = int(now if now is not None else time.time()) + settings.ttl_seconds
        username = f"{expiry}:{str(user_id).replace('-', '')[:16]}"
        digest = hmac.new(settings.turn_secret.encode("utf-8"), username.encode("utf-8"), hashlib.sha1).digest()
        servers.append({"urls": list(settings.turn_urls), "username": username,
                        "credential": base64.b64encode(digest).decode("ascii")})
    return servers


def _dial_policy(settings: SipSettings) -> dict:
    policy = {"internal_pattern": settings.internal_dial_pattern,
              "echo_test_extension": settings.echo_test_extension,
              "external": "click_to_call_only"}
    if settings.external_dial_pattern:   # opt-in (SIP_EXTERNAL_DIAL_PATTERN): dialpad may call outside numbers
        policy.update(external="dialpad", external_pattern=settings.external_dial_pattern)
    return policy


def _current_row(user_id):
    return sess().execute(text("""
        SELECT id, extension, issued_at, expires_at, (expires_at > now()) AS unexpired
          FROM sip_credentials
         WHERE user_id = CAST(:u AS uuid) AND revoked_at IS NULL
         ORDER BY issued_at DESC LIMIT 1"""), {"u": str(user_id)}).mappings().first()


def status(user_id) -> dict:
    """What the caller may see about their credential: never the secret."""
    extension = ext_svc.my_extension(user_id)
    try:
        settings = load()
        configured, reason = True, None
    except ApiError as e:
        settings, configured, reason = None, False, e.code
    cur = _current_row(user_id)
    current = None
    if cur is not None:
        current = {"credential_id": str(cur["id"]), "extension": cur["extension"],
                   "issued_at": iso(cur["issued_at"]), "expires_at": iso(cur["expires_at"]),
                   "active": bool(cur["unexpired"]) and extension is not None
                   and extension["extension"] == cur["extension"]}
    return {"configured": configured, "reason": reason if not configured else (None if extension else NO_EXTENSION),
            "extension": extension["extension"] if extension else None,
            "wss_url": settings.wss_url if settings else None,
            "ttl_seconds": settings.ttl_seconds if settings else None,
            "current": current}


def issue(user_id) -> dict:
    """Issue a new credential to the caller (the route commits). Raises ApiError when refused."""
    settings = load()
    user_id = str(user_id)
    advisory_lock("sip-credentials-user", user_id)
    owner = ext_svc.active_for_user(user_id, lock=True)
    if owner is None:
        raise ApiError(409, NO_EXTENSION, "you have no extension assigned; ask a calling administrator",
                       {"rule": NO_EXTENSION})
    user = user_row(user_id)
    if user is None or not user["is_active"]:
        raise ApiError(403, "actor_inactive", "your account is not active", {"rule": "actor_inactive"})
    extension = owner["extension"]
    advisory_lock("sip-credentials-extension", extension)

    recent = sess().execute(text("""
        SELECT count(*) FROM sip_credentials
         WHERE user_id = CAST(:u AS uuid) AND issued_at > now() - interval '10 minutes'"""),
        {"u": user_id}).scalar_one()
    if recent >= settings.issue_limit:
        raise ApiError(429, TOO_MANY, "too many softphone credentials were issued; wait a few minutes",
                       {"limit": settings.issue_limit, "window_seconds": 600})

    # Nothing password-equivalent outlives its use: expired HA1s go now (any user's).
    purged = sess().execute(text("""
        UPDATE sip_credentials SET ha1 = NULL
         WHERE ha1 IS NOT NULL AND revoked_at IS NULL AND expires_at <= now()""")).rowcount
    # The previous credential of this extension (whoever held it) and any other current one of
    # this user stop working now: the PBX view only returns the current, unexpired row.
    superseded = sess().execute(text("""
        UPDATE sip_credentials SET revoked_at = now(), revoke_reason = 'superseded', ha1 = NULL
         WHERE revoked_at IS NULL AND (extension = :e OR user_id = CAST(:u AS uuid))
         RETURNING id"""), {"e": extension, "u": user_id}).scalars().all()

    password = new_password()
    row = sess().execute(text("""
        INSERT INTO sip_credentials (user_id, extension, user_extension_id, realm, ha1, expires_at)
        VALUES (CAST(:u AS uuid), :e, CAST(:x AS uuid), :realm, :ha1,
                now() + make_interval(secs => :ttl))
        RETURNING id, issued_at, expires_at"""),
        {"u": user_id, "e": extension, "x": str(owner["id"]), "realm": settings.realm,
         "ha1": ha1(extension, settings.realm, password), "ttl": settings.ttl_seconds}).mappings().one()

    audit.record("calling.sip_credentials.issue", "sip_credential", str(row["id"]),
                 after={"extension": extension, "realm": settings.realm, "expires_at": row["expires_at"],
                        "ttl_seconds": settings.ttl_seconds,
                        "superseded": [str(s) for s in superseded], "expired_ha1_cleared": purged})

    prefs = settings_svc.get_preferences(user_id)
    return {
        "credential_id": str(row["id"]),
        "extension": extension,
        "sip_uri": f"sip:{extension}@{settings.domain}",
        "authorization_user": extension,
        "realm": settings.realm,
        "password": password,
        "display_name": user["name"] or extension,
        "wss_url": settings.wss_url,
        "ice_servers": turn_credentials(settings, user_id),
        "issued_at": iso(row["issued_at"]),
        "expires_at": iso(row["expires_at"]),
        "ttl_seconds": settings.ttl_seconds,
        # refresh well before expiry; the PBX refuses the old password once a new one is issued
        "refresh_after_seconds": max(30, int(settings.ttl_seconds * 0.7)),
        "register_expires": settings.register_expires,
        "auto_answer": {"header": ATTEMPT_HEADER, "enabled": bool(prefs["auto_answer_attempts"])},
        "dial_policy": _dial_policy(settings),
        "storage": "memory_only",
    }


def revoke_mine(user_id, reason: str = "user_revoked") -> dict:
    """Revoke the caller's current credential(s) (sign-out / softphone off). The route commits."""
    user_id = str(user_id)
    advisory_lock("sip-credentials-user", user_id)
    ids = sess().execute(text("""
        UPDATE sip_credentials SET revoked_at = now(), revoke_reason = :r, ha1 = NULL
         WHERE user_id = CAST(:u AS uuid) AND revoked_at IS NULL
         RETURNING id, extension"""), {"u": user_id, "r": reason}).mappings().all()
    for r in ids:
        audit.record("calling.sip_credentials.revoke", "sip_credential", str(r["id"]),
                     after={"extension": r["extension"], "reason": reason})
    return {"revoked": len(ids)}
