"""Per-user switch for the in-app softphone (migration 0082, Alitqan 2026-10-21).

The users call from their own VoIP desk phones: click-to-call and campaigns ring the user's
extension wherever it is registered, so they need no switch. The browser softphone is OFF for
every user until a calling administrator switches it on for that user (no row = disabled).

* `is_enabled` gates the SIP credential issue (services/calling/sip.py); the PBX realtime auth
  view `pbx_ps_auths` joins the same table, so an older credential never registers either.
* Switching it off revokes the user's current SIP credentials in the same transaction.
* Every switch is audited with its reason. Nothing here talks to the PBX. Never commits (the
  @idempotent route does).
"""
from __future__ import annotations

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
                 before={"enabled": before["enabled"]}, after={"enabled": enabled, "credentials_revoked": revoked})
    return {**get(user_id), "credentials_revoked": revoked}
