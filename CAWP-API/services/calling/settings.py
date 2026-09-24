"""Calling preferences (per user) and the deployment calling-window policy (migration 0048).

Preferences never hold a SIP secret: only auto-answer for attempt-tagged calls and the
browser's chosen device ids. The window policy is one reviewed JSON document with a revision
and an append-only history. Neither ever starts, ends or authorizes a call by itself.
"""
from __future__ import annotations

import json

from sqlalchemy import text

from core import audit, riyadh
from core.api_response import VALIDATION_FAILED, ApiError
from core.revisions import RevisionConflict
from services.calling import windows as W
from services.calling.common import USER_NAME_SQL, bool_arg, iso, me, required_text, sess, user_ref

DEVICE_KEYS = ("microphone", "speaker", "ringer")
DEFAULTS = {"auto_answer_attempts": True, "devices": {k: None for k in DEVICE_KEYS}}


# --------------------------------------------------------------------------- preferences
def get_preferences(user_id) -> dict:
    r = sess().execute(text("""SELECT auto_answer_attempts, devices, revision, updated_at
                                 FROM calling_preferences WHERE user_id = CAST(:u AS uuid)"""),
                       {"u": str(user_id)}).mappings().first()
    if r is None:
        return {**DEFAULTS, "devices": dict(DEFAULTS["devices"]), "revision": 0, "saved": False, "updated_at": None}
    devices = {k: (r["devices"] or {}).get(k) for k in DEVICE_KEYS}
    return {"auto_answer_attempts": r["auto_answer_attempts"], "devices": devices,
            "revision": int(r["revision"]), "saved": True, "updated_at": iso(r["updated_at"])}


def _devices(value):
    if value is None:
        return None
    if not isinstance(value, dict) or not set(value) <= set(DEVICE_KEYS):
        raise ApiError(422, VALIDATION_FAILED, f"devices may only contain {', '.join(DEVICE_KEYS)}",
                       {"devices": "invalid"})
    out = {}
    for k in DEVICE_KEYS:
        v = value.get(k)
        if v is not None and (not isinstance(v, str) or len(v) > 200):
            raise ApiError(422, VALIDATION_FAILED, f"devices.{k} must be text of at most 200 characters",
                           {f"devices.{k}": "invalid"})
        out[k] = (v.strip() or None) if isinstance(v, str) else None
    return out


def save_preferences(user_id, body, expected_revision) -> dict:
    current = get_preferences(user_id)
    auto = bool_arg(body, "auto_answer_attempts", current["auto_answer_attempts"])
    devices = _devices(body.get("devices")) if "devices" in body else current["devices"]
    unknown = set(body) - {"auto_answer_attempts", "devices", "expected_revision"}
    if unknown:
        raise ApiError(422, VALIDATION_FAILED, f"unknown fields: {', '.join(sorted(unknown))}",
                       {k: "unknown" for k in unknown})
    params = {"u": str(user_id), "auto": auto, "dev": json.dumps(devices), "by": me()}
    if not current["saved"]:
        if expected_revision != 0:
            raise RevisionConflict("calling_preferences", user_id, expected_revision, 0)
        row = sess().execute(text("""
            INSERT INTO calling_preferences (user_id, auto_answer_attempts, devices, updated_by, revision)
            VALUES (CAST(:u AS uuid), :auto, CAST(:dev AS jsonb), CAST(:by AS uuid), 1)
            ON CONFLICT (user_id) DO NOTHING RETURNING revision"""), params).first()
        if row is None:                                  # a concurrent first save won
            raise RevisionConflict("calling_preferences", user_id, expected_revision,
                                   get_preferences(user_id)["revision"])
    else:
        row = sess().execute(text("""
            UPDATE calling_preferences
               SET auto_answer_attempts = :auto, devices = CAST(:dev AS jsonb), updated_at = now(),
                   updated_by = CAST(:by AS uuid)
             WHERE user_id = CAST(:u AS uuid) AND revision = :rev RETURNING revision"""),
            {**params, "rev": expected_revision}).first()
        if row is None:
            raise RevisionConflict("calling_preferences", user_id, expected_revision,
                                   get_preferences(user_id)["revision"])
    audit.record("calling.preferences.save", "calling_preferences", str(user_id),
                 before={"auto_answer_attempts": current["auto_answer_attempts"], "devices": current["devices"]},
                 after={"auto_answer_attempts": auto, "devices": devices})
    return get_preferences(user_id)


# --------------------------------------------------------------------------- windows
def get_window_policy() -> dict | None:
    r = sess().execute(text(f"""
        SELECT p.windows, p.holidays, p.revision, p.updated_at, p.updated_by,
               {USER_NAME_SQL.format(a='u')} AS updated_by_name, p.reason
          FROM calling_window_policy p LEFT JOIN users u ON u.id = p.updated_by WHERE p.id = 1""")).mappings().first()
    if r is None:
        return None
    return {"windows": r["windows"], "holidays": r["holidays"], "revision": int(r["revision"]),
            "updated_at": iso(r["updated_at"]), "updated_by": user_ref(r["updated_by"], r["updated_by_name"]),
            "reason": r["reason"]}


def window_status(at=None) -> dict:
    """Advisory status of the policy now (Asia/Riyadh). P1 re-checks at dispatch."""
    return W.status(get_window_policy(), at)


def windows_view(at=None) -> dict:
    policy = get_window_policy()
    history = sess().execute(text(f"""
        SELECT h.revision, h.changed_at, h.changed_by, {USER_NAME_SQL.format(a='u')} AS changed_by_name, h.reason
          FROM calling_window_policy_history h LEFT JOIN users u ON u.id = h.changed_by
         ORDER BY h.revision DESC LIMIT 50""")).mappings().all()
    return {"policy": policy, "status": W.status(policy, at),
            "history": [{"revision": h["revision"], "changed_at": iso(h["changed_at"]),
                         "changed_by": user_ref(h["changed_by"], h["changed_by_name"]), "reason": h["reason"]}
                        for h in history]}


def save_window_policy(body, expected_revision) -> dict:
    reason = required_text(body, "reason", max_len=1000)
    unknown = set(body) - {"windows", "holidays", "reason", "expected_revision"}
    if unknown:
        raise ApiError(422, VALIDATION_FAILED, f"unknown fields: {', '.join(sorted(unknown))}",
                       {k: "unknown" for k in unknown})
    try:
        windows = W.validate_windows(body.get("windows"))
        holidays = W.validate_holidays(body.get("holidays", []))
    except W.WindowError as e:
        raise ApiError(422, VALIDATION_FAILED, str(e), {e.field: e.problem}) from None
    sess().execute(text("SELECT pg_advisory_xact_lock(hashtextextended('calling_window_policy', 0))"))
    current = get_window_policy()
    current_rev = current["revision"] if current else 0
    if expected_revision != current_rev:
        raise RevisionConflict("calling_window_policy", 1, expected_revision, current_rev)
    new_rev = current_rev + 1
    params = {"w": json.dumps(windows), "h": json.dumps(holidays), "rev": new_rev, "by": me(), "why": reason}
    if current is None:
        sess().execute(text("""
            INSERT INTO calling_window_policy (id, windows, holidays, revision, updated_by, reason)
            VALUES (1, CAST(:w AS jsonb), CAST(:h AS jsonb), :rev, CAST(:by AS uuid), :why)"""), params)
    else:
        sess().execute(text("""
            UPDATE calling_window_policy
               SET windows = CAST(:w AS jsonb), holidays = CAST(:h AS jsonb), revision = :rev,
                   updated_at = now(), updated_by = CAST(:by AS uuid), reason = :why
             WHERE id = 1"""), params)
    sess().execute(text("""
        INSERT INTO calling_window_policy_history (revision, windows, holidays, changed_by, reason)
        VALUES (:rev, CAST(:w AS jsonb), CAST(:h AS jsonb), CAST(:by AS uuid), :why)"""), params)
    audit.record("calling.windows.save", "calling_window_policy", "1",
                 before=({"windows": current["windows"], "holidays": current["holidays"],
                          "revision": current_rev} if current else None),
                 after={"windows": windows, "holidays": holidays, "revision": new_rev}, reason=reason)
    return windows_view()


def riyadh_now():
    return riyadh.now()
