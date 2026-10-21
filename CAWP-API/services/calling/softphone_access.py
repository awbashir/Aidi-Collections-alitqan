"""Per-user switch for the in-app softphone (migration 0082, Alitqan 2026-10-21).

The users call from their own VoIP desk phones: click-to-call and campaigns ring the user's
extension wherever it is registered, so they need no switch. The browser softphone is OFF for
every user until a calling administrator switches it on for that user (no row = disabled).

* `is_enabled` gates the SIP credential issue (services/calling/sip.py); the PBX realtime auth
  view `pbx_ps_auths` joins the same table, so an older credential never registers either.
* Switching it off revokes the user's current SIP credentials in the same transaction.
* SIP_CREDENTIAL_MODE=static (chan_sip PBX, 0083): switching on also converts the user's extension
  to a WebRTC extension on the PBX (its desk phone stops working meanwhile) and keeps the new
  WebRTC password encrypted (sip_static); switching off restores the desk-phone settings the
  extension had (its original password included) and deletes the stored password. The PBX is
  asked BEFORE anything is written here; a refusal or an unreachable PBX changes nothing.
* Every switch is audited with its reason. Nothing here talks to the PBX. Never commits (the
  @idempotent route does).
"""
from __future__ import annotations

import os

from sqlalchemy import text

from core import audit
from core.api_response import NOT_FOUND, VALIDATION_FAILED, ApiError
from services.calling.common import (USER_NAME_SQL, advisory_lock, bool_arg, iso, me, parse_uuid, required_text,
                                     sess, user_ref, user_row)

DISABLED = "softphone_disabled"


def is_enabled(user_id) -> bool:
    return bool(sess().execute(text("""SELECT enabled FROM user_softphone_access
                                         WHERE user_id = CAST(:u AS uuid)"""), {"u": str(user_id)}).scalar())


def enabled_map(user_ids) -> dict[str, bool]:
    ids = sorted({str(u) for u in user_ids if u})
    if not ids:
        return {}
    rows = sess().execute(text("""SELECT user_id, enabled FROM user_softphone_access
                                   WHERE user_id = ANY(CAST(:ids AS uuid[]))"""), {"ids": ids}).all()
    found = {str(r[0]): bool(r[1]) for r in rows}
    return {u: found.get(u, False) for u in ids}


def get(user_id) -> dict:
    r = sess().execute(text(f"""
        SELECT a.enabled, a.reason, a.revision, a.updated_at, a.updated_by,
               {USER_NAME_SQL.format(a='b')} AS updated_by_name
          FROM user_softphone_access a LEFT JOIN users b ON b.id = a.updated_by
         WHERE a.user_id = CAST(:u AS uuid)"""), {"u": str(user_id)}).mappings().first()
    if r is None:
        return {"user_id": str(user_id), "enabled": False, "reason": None, "revision": 0,
                "updated_at": None, "updated_by": None}
    return {"user_id": str(user_id), "enabled": bool(r["enabled"]), "reason": r["reason"],
            "revision": int(r["revision"]), "updated_at": iso(r["updated_at"]),
            "updated_by": user_ref(r["updated_by"], r["updated_by_name"]) if r["updated_by"] else None}


def set_enabled(user_id, body) -> dict:
    """{enabled, reason}: switch one user's softphone on or off (calls_manage)."""
    user_id = parse_uuid(user_id, "user_id")
    enabled = bool_arg(body, "enabled")
    if enabled is None:
        raise ApiError(422, VALIDATION_FAILED, "enabled is required (true or false)", {"enabled": "required"})
    reason = required_text(body, "reason", max_len=1000)
    unknown = set(body) - {"enabled", "reason"}
    if unknown:
        raise ApiError(422, VALIDATION_FAILED, f"unknown fields: {', '.join(sorted(unknown))}",
                       {k: "unknown" for k in unknown})
    if user_row(user_id) is None:
        raise ApiError(404, NOT_FOUND, "user not found")
    advisory_lock("sip-credentials-user", user_id)
    before = get(user_id)
    pbx_change = _pbx_switch(user_id, enabled) if static_mode() else None
    sess().execute(text("""
        INSERT INTO user_softphone_access (user_id, enabled, reason, updated_by)
        VALUES (CAST(:u AS uuid), :on, :r, CAST(:by AS uuid))
        ON CONFLICT (user_id) DO UPDATE
           SET enabled = EXCLUDED.enabled, reason = EXCLUDED.reason, updated_at = now(),
               updated_by = EXCLUDED.updated_by"""),
        {"u": user_id, "on": enabled, "r": reason, "by": me()})
    revoked = 0
    if not enabled:
        from services.calling import sip as sip_svc      # sip imports this module
        revoked = sip_svc.revoke_mine(user_id, reason=DISABLED)["revoked"]
    audit.record("calling.softphone_access.set", "user_softphone_access", user_id, reason=reason,
                 before={"enabled": before["enabled"]},
                 after={"enabled": enabled, "credentials_revoked": revoked, "pbx": pbx_change})
    return {**get(user_id), "credentials_revoked": revoked, "pbx": pbx_change}


def static_mode() -> bool:
    return (os.getenv("SIP_CREDENTIAL_MODE") or "").strip().lower() == "static"


def _pbx_switch(user_id: str, enabled: bool) -> dict | None:
    """chan_sip PBX: convert / restore the user's extension (see the module docstring). Returns
    {extension, webrtc} for the response and the audit (never the password), or None when nothing
    had to change on the PBX."""
    from services.calling import extension_provisioning as prov
    from services.calling import extensions as ext_svc
    from services.calling import pbx_provision as pbx
    from services.calling import sip_static
    owner = ext_svc.active_for_user(user_id)
    if enabled:
        if owner is None:
            raise ApiError(409, "no_extension", "the user has no extension: assign one before enabling the softphone",
                           {"rule": "no_extension"})
        ext = owner["extension"]
        stored = sip_static.row(ext)
        if stored is not None and str(stored["user_id"]) == str(user_id):
            return None                                  # already a WebRTC extension of this user
        sip_static._key()                                # 503 now if the key is missing, before the PBX changes
        try:
            res = pbx.webrtc(ext, True)
        except pbx.ProvisionUnavailable as e:
            raise ApiError(503, "pbx_unavailable", "the PBX provisioning API could not be reached",
                           {"reason": e.reason}) from None
        except pbx.ProvisionRefused as e:
            raise prov._refusal(e, ext) from None
        secret = res.get("sip_secret")
        if not isinstance(secret, str) or not secret:
            raise ApiError(502, "pbx_bad_answer", "the PBX did not return the WebRTC password", {"extension": ext})
        sip_static.store(ext, user_id, secret)
        return {"extension": ext, "webrtc": True}
    rows = sess().execute(text("SELECT extension FROM sip_static_secrets WHERE user_id = CAST(:u AS uuid)"),
                          {"u": user_id}).scalars().all()
    exts = set(rows) | ({owner["extension"]} if owner is not None and sip_static.row(owner["extension"]) else set())
    if not exts:
        return None
    for ext in sorted(exts):
        try:
            pbx.webrtc(ext, False)
        except pbx.ProvisionUnavailable as e:
            raise ApiError(503, "pbx_unavailable", "the PBX provisioning API could not be reached",
                           {"reason": e.reason}) from None
        except pbx.ProvisionRefused as e:
            if e.code != "not_webrtc":                   # already a desk phone: nothing to restore
                raise prov._refusal(e, ext) from None
        sip_static.delete(ext)
    return {"extension": ",".join(sorted(exts)), "webrtc": False}
