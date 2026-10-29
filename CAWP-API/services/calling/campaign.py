"""Progressive calling campaigns (Phase 5 P2): model, lifecycle, roster, readiness, views.
Request-bound (JWT identity). The dispatch itself is services/calling/campaign_dispatch.py; the
pool is services/calling/campaign_pool.py. review/phase5-campaigns.md has the full contract.

Permissions: `campaigns` (see my campaigns, heartbeat, next), `campaigns_manage` (create, edit,
roster, start/pause/drain/stop, live view, pool). Visibility: a manager sees the campaigns they
created and those created by, or with members in, their team (supervised users); SuperAdmin sees
all; a member sees the campaigns they are on the roster of. Anything else answers 404.

Lifecycle (database-guarded, campaigns_guard):
    draft -> running (start) | stopped (stop: discarded)
    running -> paused (pause) | draining (drain) | stopped (stop)
    paused -> running (start = resume) | draining | stopped
    draining -> stopped (automatic once every campaign call ended and was wrapped up; or stop)
Pause, drain and stop cancel campaign calls that are still QUEUED (never handed to the PBX:
attempt cancelled, reservation released `cancel_unsent`); a call already sent continues to its
end and still needs its wrap-up. Nothing ever cuts a live call.

Readiness (per member, POST /v2/campaigns/<id>/heartbeat): declared state ready | paused |
wrap_up (`wrap-up` accepted) | offline plus a lease of `lease_seconds`. A heartbeat without a
state only renews the lease. The member's EFFECTIVE state (what the views show) is derived:

    removed      the member was deactivated
    on_call      the member's reservation holds a live (or uncertain) call
    wrap_up      the call ended and waits for its wrap-up (reason wrapup_pending), or the wrap-up
                 delay after the last campaign call is running (reason wrapup_delay, ready_at)
    offline      declared offline, never heartbeated, or the lease lapsed (reason lease_expired)
    paused       declared paused (reason agent_did_not_answer when the dispatcher paused them)
    waiting_next manual mode: ready, waiting for the member's "next"
    ready        eligible; `reason` says why no call is being placed right now, if known
                 (campaign_paused, transport_not_connected, calling_window_closed, no_eligible_case...)
"""
from __future__ import annotations

import datetime as _dt
import json
import time

from sqlalchemy import text

from core import audit
from core.api_response import NOT_FOUND, VALIDATION_FAILED, ApiError
from core.auth import has_permission
from core.revisions import RevisionConflict, parse_revision
from services.calling import campaign_dispatch as CD
from services.calling import campaign_pool as P
from services.calling.common import USER_NAME_SQL, iso, me, s, sess, user_ref

CAMPAIGNS = "campaigns"
CAMPAIGNS_MANAGE = "campaigns_manage"
STATUSES = ("draft", "running", "paused", "draining", "stopped")
READINESS = ("ready", "paused", "wrap_up", "offline")
SETTINGS = {
    "next_mode": ("auto", "manual"),
    "wrapup_delay_seconds": (0, 600),
    "max_attempts_per_day": (1, 50),
    "cooldown_minutes": (0, 10080),
    "lease_seconds": (10, 300),
}
DEFAULTS = {"next_mode": "auto", "wrapup_delay_seconds": 10, "max_attempts_per_day": 3, "cooldown_minutes": 60,
            "lease_seconds": 30}
TRANSITIONS = {
    "start": ({"draft", "paused"}, "running"),
    "pause": ({"running"}, "paused"),
    "drain": ({"running", "paused"}, "draining"),
    "stop": ({"draft", "running", "paused", "draining"}, "stopped"),
}
MAX_MEMBERS = 200
BLOCK_FRESH_SECONDS = 30
# The pool summary classifies every case of the pool (about 4.7 s for a 75k-case contract on the
# full local copy), so the live view, polled every 2 s, reuses it for this long per process.
SUMMARY_TTL_SECONDS = 15
_summary_cache: dict = {}


def _err(status, code, message, error=None):
    return ApiError(status, code, message, error)


def _unknown(body: dict, allowed: set):
    extra = set(body) - allowed
    if extra:
        raise _err(422, VALIDATION_FAILED, f"unknown fields: {', '.join(sorted(extra))}", {k: "unknown" for k in extra})


def _reason(body, required=False) -> str | None:
    v = body.get("reason")
    if v is None or (isinstance(v, str) and not v.strip()):
        if required:
            raise _err(422, VALIDATION_FAILED, "reason is required", {"reason": "required"})
        return None
    if not isinstance(v, str) or len(v) > 500:
        raise _err(422, VALIDATION_FAILED, "reason must be text of at most 500 characters", {"reason": "invalid"})
    return v.strip()


def _settings(raw, base: dict) -> dict:
    if raw is None:
        return dict(base)
    if not isinstance(raw, dict):
        raise _err(422, VALIDATION_FAILED, "settings must be an object", {"settings": "invalid"})
    _unknown({f"settings.{k}": v for k, v in raw.items()}, {f"settings.{k}" for k in SETTINGS})
    out = dict(base)
    for k, v in raw.items():
        spec = SETTINGS[k]
        if k == "next_mode":
            if v not in spec:
                raise _err(422, VALIDATION_FAILED, "settings.next_mode must be auto or manual",
                           {"settings.next_mode": "invalid"})
        else:
            if isinstance(v, bool) or not isinstance(v, int) or not spec[0] <= v <= spec[1]:
                raise _err(422, VALIDATION_FAILED, f"settings.{k} must be an integer {spec[0]}..{spec[1]}",
                           {f"settings.{k}": "invalid"})
        out[k] = v
    return out


MAX_ENDS_AT_DAYS = 366


def _ends_at(body):
    """2026-10-25 (0087): `ends_at` = an ISO-8601 date-time with an offset in the future (at most
    MAX_ENDS_AT_DAYS ahead), or null (no limit)."""
    raw = body.get("ends_at")
    if raw is None:
        return None
    try:
        v = _dt.datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        v = None
    if v is None or v.tzinfo is None:
        raise _err(422, VALIDATION_FAILED, "ends_at must be an ISO-8601 date-time with an offset", {"ends_at": "invalid"})
    now = _dt.datetime.now(_dt.timezone.utc)
    if v <= now:
        raise _err(422, VALIDATION_FAILED, "ends_at must be in the future", {"ends_at": "past"})
    if v > now + _dt.timedelta(days=MAX_ENDS_AT_DAYS):
        raise _err(422, VALIDATION_FAILED, f"ends_at must be within {MAX_ENDS_AT_DAYS} days", {"ends_at": "too_far"})
    return v


MESSAGE_KEYS = ("max_concurrent", "message_transfer", "message_notes", "message_plays")


def _message_fields(body, *, kind: str, base: dict | None = None) -> dict:
    """2026-10-25 (0088): max_concurrent (1..20), message_transfer (bool), message_notes (see
    services/calling/message_campaign.validate_notes); 2026-10-29 (0089): message_plays (1..5);
    only for kind `message`."""
    from services.calling import message_campaign as MC
    base = base or {"max_concurrent": 5, "message_transfer": True, "message_notes": None, "message_plays": 2}
    keys = set(MESSAGE_KEYS) & set(body)
    if kind != "message":
        if keys:
            raise _err(422, VALIDATION_FAILED, f"{', '.join(sorted(keys))} apply to message campaigns only",
                       {k: "not_applicable" for k in keys})
        return dict(base)
    out = dict(base)
    if "max_concurrent" in body:
        v = body["max_concurrent"]
        if isinstance(v, bool) or not isinstance(v, int) or not 1 <= v <= 20:
            raise _err(422, VALIDATION_FAILED, "max_concurrent must be an integer 1..20", {"max_concurrent": "invalid"})
        out["max_concurrent"] = v
    if "message_transfer" in body:
        if not isinstance(body["message_transfer"], bool):
            raise _err(422, VALIDATION_FAILED, "message_transfer must be true or false", {"message_transfer": "invalid"})
        out["message_transfer"] = body["message_transfer"]
    if "message_plays" in body:
        v = body["message_plays"]
        if isinstance(v, bool) or not isinstance(v, int) or not 1 <= v <= 5:
            raise _err(422, VALIDATION_FAILED, "message_plays must be an integer 1..5", {"message_plays": "invalid"})
        out["message_plays"] = v
    if "message_notes" in body:
        out["message_notes"] = MC.validate_notes(body["message_notes"])
    return out


def _fill_random(body) -> bool:
    v = body.get("fill_random", False)
    if not isinstance(v, bool):
        raise _err(422, VALIDATION_FAILED, "fill_random must be true or false", {"fill_random": "invalid"})
    return v


def _name(body) -> str:
    v = body.get("name")
    if not isinstance(v, str) or not v.strip():
        raise _err(422, VALIDATION_FAILED, "name is required", {"name": "required"})
    if len(v.strip()) > 200:
        raise _err(422, VALIDATION_FAILED, "name is too long (max 200)", {"name": "too_long"})
    return v.strip()


def _user_ids(raw, field="member_ids") -> list[str]:
    if raw is None:
        return []
    if not isinstance(raw, list) or len(raw) > MAX_MEMBERS:
        raise _err(422, VALIDATION_FAILED, f"{field} must be a list of at most {MAX_MEMBERS} user ids", {field: "invalid"})
    from services.calling.common import valid_uuid
    out = []
    for v in raw:
        u = valid_uuid(v)
        if u is None:
            raise _err(422, VALIDATION_FAILED, f"{field} contains an invalid id", {field: "invalid"})
        out.append(u)
    return sorted(set(out))


def _check_members_allowed(user_ids):
    """Every member must be an active user the manager may see (self, their team, or SuperAdmin)."""
    from services.clients_scope import can_view_user, resolve_scope
    scope = resolve_scope(me())
    rows = {str(r[0]): r[1] for r in sess().execute(text(
        "SELECT id, is_active FROM users WHERE id = ANY(CAST(:ids AS uuid[]))"), {"ids": list(user_ids)}).all()}
    for u in user_ids:
        if u not in rows or not can_view_user(scope, u):
            raise _err(422, "member_not_allowed", "a member must be an active user of your team",
                       {"user_id": u, "reason": "not_found"})
        if not rows[u]:
            raise _err(422, "member_not_allowed", "a member must be an active user of your team",
                       {"user_id": u, "reason": "inactive"})


# --------------------------------------------------------------------------- visibility
def _team_sql() -> tuple[str, dict]:
    """SQL over `cp` (campaigns) for the campaigns the caller may see."""
    from services.clients_scope import SUPER, resolve_scope
    uid = me()
    member = ("EXISTS (SELECT 1 FROM campaign_members vm WHERE vm.campaign_id = cp.id "
              "AND vm.user_id = CAST(:v_me AS uuid))")
    params = {"v_me": uid}
    if not has_permission(CAMPAIGNS_MANAGE):
        return member, params
    scope = resolve_scope(uid)
    if scope.kind == SUPER:
        return "TRUE", {}
    team = sorted(set(scope.user_ids or []) | {uid})
    params["v_team"] = team
    return (f"({member} OR cp.created_by = ANY(CAST(:v_team AS uuid[])) OR EXISTS ("
            "SELECT 1 FROM campaign_members tm WHERE tm.campaign_id = cp.id "
            "AND tm.user_id = ANY(CAST(:v_team AS uuid[]))))"), params


def load(campaign_id, *, lock: str | None = None, manage: bool = False) -> dict:
    """The campaign row, 404 unless visible; `manage` also requires campaigns_manage."""
    cond, params = _team_sql()
    if manage and not has_permission(CAMPAIGNS_MANAGE):
        raise _err(403, "forbidden", "managing campaigns needs the campaigns_manage permission",
                   {"required_any": [CAMPAIGNS_MANAGE]})
    visible = sess().execute(text(f"SELECT 1 FROM campaigns cp WHERE cp.id = CAST(:c AS uuid) AND {cond}"),
                             {**params, "c": str(campaign_id)}).first()
    if visible is None:
        raise _err(404, NOT_FOUND, "campaign not found", {"campaign_id": str(campaign_id)})
    return CD.load_campaign(sess(), campaign_id, lock=lock)


def _check_revision(camp, expected):
    exp = parse_revision(expected)
    if int(camp["revision"]) != exp:
        raise RevisionConflict("campaign", camp["id"], exp, int(camp["revision"]))


# --------------------------------------------------------------------------- views
def _settings_of(c) -> dict:
    return {k: c[k] for k in SETTINGS}


def campaign_view(c: dict) -> dict:
    counts = sess().execute(text("""
        SELECT count(*) FILTER (WHERE active) AS active, count(*) AS total FROM campaign_members
         WHERE campaign_id = CAST(:c AS uuid)"""), {"c": str(c["id"])}).mappings().first()
    creator = sess().execute(text(f"SELECT {USER_NAME_SQL.format(a='u')} FROM users u WHERE u.id = CAST(:u AS uuid)"),
                             {"u": str(c["created_by"])}).scalar()
    return {
        "id": s(c["id"]), "name": c["name"], "status": c["status"], "pool": c["pool"], "settings": _settings_of(c),
        "created_by": user_ref(c["created_by"], creator), "created_at": iso(c["created_at"]),
        "updated_at": iso(c["updated_at"]), "started_at": iso(c["started_at"]), "paused_at": iso(c["paused_at"]),
        "drain_requested_at": iso(c["drain_requested_at"]), "stopped_at": iso(c["stopped_at"]),
        "stop_reason": c["stop_reason"], "members": {"active": int(counts["active"]), "total": int(counts["total"])},
        "ends_at": iso(c.get("ends_at")), "fill_random": bool(c.get("fill_random")),   # 2026-10-25 (0087)
        "kind": c.get("kind") or "agent",                                             # 2026-10-25 (0088)
        "message": ({"max_concurrent": int(c["max_concurrent"]), "transfer": bool(c["message_transfer"]),
                     "notes": c.get("message_notes"), "plays": int(c.get("message_plays") or 2)}
                    if c.get("kind") == "message" else None),
        "cases": _case_counts(c["id"]),                 # 2026-10-24: customer list / hand removals (0086)
        "revision": int(c["revision"]),
    }


def _case_counts(campaign_id) -> dict:
    from services.calling.campaign_cases import list_counts
    return list_counts(campaign_id)


def detail(campaign_id) -> dict:
    return campaign_view(load(campaign_id))


def list_campaigns(args, page, size, offset) -> dict:
    cond, params = _team_sql()
    where = [cond]
    statuses = [x for x in (args.get("status") or "").split(",") if x]
    if statuses:
        bad = [x for x in statuses if x not in STATUSES]
        if bad:
            raise _err(422, VALIDATION_FAILED, f"unknown status: {', '.join(bad)}", {"status": "invalid"})
        where.append("cp.status = ANY(CAST(:st AS text[]))")
        params["st"] = statuses
    if args.get("mine") in ("1", "true"):
        where.append("EXISTS (SELECT 1 FROM campaign_members mm WHERE mm.campaign_id = cp.id "
                     "AND mm.user_id = CAST(:v_me2 AS uuid) AND mm.active)")
        params["v_me2"] = me()
    w = " AND ".join(where)
    total = sess().execute(text(f"SELECT count(*) FROM campaigns cp WHERE {w}"), params).scalar()
    rows = sess().execute(text(f"""
        SELECT cp.* FROM campaigns cp WHERE {w}
         ORDER BY (cp.status IN ('running', 'paused', 'draining')) DESC, cp.created_at DESC, cp.id DESC
         LIMIT :lim OFFSET :off"""), {**params, "lim": size, "off": offset}).mappings().all()
    items = []
    for r in rows:
        v = campaign_view(dict(r))
        mine = _readiness_row(r["id"], me())
        v["my_membership"] = _membership(r["id"], me())
        v["my_readiness"] = _readiness_brief(mine) if mine else None
        items.append(v)
    return {"items": items, "total": int(total), "page": page, "page_size": size}


def _membership(campaign_id, user_id):
    r = sess().execute(text("SELECT active FROM campaign_members WHERE campaign_id = CAST(:c AS uuid) "
                            "AND user_id = CAST(:u AS uuid)"), {"c": str(campaign_id), "u": str(user_id)}).first()
    return None if r is None else {"active": bool(r[0])}


def _readiness_row(campaign_id, user_id):
    r = sess().execute(text("""SELECT r.*, (r.lease_until > clock_timestamp()) AS lease_live FROM campaign_readiness r
                                WHERE r.campaign_id = CAST(:c AS uuid) AND r.user_id = CAST(:u AS uuid)"""),
                       {"c": str(campaign_id), "u": str(user_id)}).mappings().first()
    return dict(r) if r else None


def _readiness_brief(r) -> dict:
    return {"state": r["state"], "state_since": iso(r["state_since"]), "state_reason": r["state_reason"],
            "lease_until": iso(r["lease_until"]), "lease_live": bool(r["lease_live"]),
            "heartbeat_at": iso(r["heartbeat_at"])}


# --------------------------------------------------------------------------- create / update
def pool_scope() -> dict:
    """The contracts my current scope reaches (`contract_ids` null = every contract): the campaign
    form offers the general note statuses plus those contracts' own (2026-10-19), as
    campaign_pool.check_pool_statuses then requires."""
    return {"contract_ids": P.scope_contract_ids(me())}


def create(body: dict) -> str:
    _unknown(body, {"name", "pool", "settings", "member_ids", "ends_at", "fill_random", "kind", *MESSAGE_KEYS})
    name = _name(body)
    ends_at, fill = _ends_at(body), _fill_random(body)
    kind = body.get("kind", "agent")
    if kind not in ("agent", "message"):
        raise _err(422, VALIDATION_FAILED, "kind must be agent or message", {"kind": "invalid"})
    if kind == "message" and fill:
        raise _err(422, VALIDATION_FAILED, "random fill applies to agent campaigns only", {"fill_random": "not_applicable"})
    msg = _message_fields(body, kind=kind)
    pool = P.validate_pool(body.get("pool"))
    P.check_pool_statuses(pool, me())
    settings = _settings(body.get("settings"), DEFAULTS)
    members = _user_ids(body.get("member_ids"))
    _check_members_allowed(members)
    cid = str(sess().execute(text("""
        INSERT INTO campaigns (name, pool, next_mode, wrapup_delay_seconds, max_attempts_per_day, cooldown_minutes,
                               lease_seconds, created_by, ends_at, fill_random, kind, max_concurrent, message_transfer,
                               message_notes, message_plays)
        VALUES (:n, CAST(:p AS jsonb), :nm, :wd, :ma, :cd, :ls, CAST(:by AS uuid), :ea, :fr, :kind, :mc, :mt,
                CAST(:mn AS jsonb), :mp) RETURNING id"""),
        {"n": name, "p": json.dumps(pool), "nm": settings["next_mode"], "wd": settings["wrapup_delay_seconds"],
         "ma": settings["max_attempts_per_day"], "cd": settings["cooldown_minutes"], "ls": settings["lease_seconds"],
         "by": me(), "ea": ends_at, "fr": fill, "kind": kind, "mc": msg["max_concurrent"], "mt": msg["message_transfer"],
         "mn": json.dumps(msg["message_notes"]) if msg["message_notes"] is not None else None,
         "mp": msg["message_plays"]}).scalar())
    for u in members:
        sess().execute(text("INSERT INTO campaign_members (campaign_id, user_id, added_by) "
                            "VALUES (CAST(:c AS uuid), CAST(:u AS uuid), CAST(:by AS uuid))"),
                       {"c": cid, "u": u, "by": me()})
    audit.record("campaign.create", "campaign", cid, after={"name": name, "pool": pool, "settings": settings,
                                                             "member_ids": members, "fill_random": fill,
                                                             "kind": kind, "message": msg,
                                                             "ends_at": ends_at.isoformat() if ends_at else None})
    return cid


def update(campaign_id, body: dict, expected) -> str:
    _unknown(body, {"expected_revision", "name", "pool", "settings", "ends_at", "fill_random", *MESSAGE_KEYS})
    camp = load(campaign_id, lock="update", manage=True)
    _check_revision(camp, expected)
    if camp["status"] == "stopped":
        raise _err(409, "campaign_stopped", "a stopped campaign cannot be changed", {"status": camp["status"]})
    sets, params, before, after = [], {"c": str(campaign_id)}, {}, {}
    if "name" in body:
        params["n"] = _name(body)
        sets.append("name = :n")
        before["name"], after["name"] = camp["name"], params["n"]
    if "pool" in body:
        pool = P.validate_pool(body.get("pool"))
        P.check_pool_statuses(pool, me(), keep=P.statuses_of(camp["pool"]))
        params["p"] = json.dumps(pool)
        sets.append("pool = CAST(:p AS jsonb)")
        before["pool"], after["pool"] = camp["pool"], pool
    if "ends_at" in body:
        ea = _ends_at(body)
        if ea != camp.get("ends_at"):
            params["ea"] = ea
            sets.append("ends_at = :ea")
            before["ends_at"], after["ends_at"] = iso(camp.get("ends_at")), iso(ea)
    if set(MESSAGE_KEYS) & set(body):
        cur = {"max_concurrent": camp["max_concurrent"], "message_transfer": camp["message_transfer"],
               "message_notes": camp.get("message_notes"), "message_plays": camp.get("message_plays") or 2}
        new = _message_fields(body, kind=camp.get("kind") or "agent", base=cur)
        for k, col in (("max_concurrent", "max_concurrent"), ("message_transfer", "message_transfer"),
                       ("message_plays", "message_plays")):
            if new[k] != cur[k]:
                params[f"m_{k}"] = new[k]
                sets.append(f"{col} = :m_{k}")
                before[k], after[k] = cur[k], new[k]
        if new["message_notes"] != cur["message_notes"]:
            params["m_notes"] = json.dumps(new["message_notes"]) if new["message_notes"] is not None else None
            sets.append("message_notes = CAST(:m_notes AS jsonb)")
            before["message_notes"], after["message_notes"] = cur["message_notes"], new["message_notes"]
    if "fill_random" in body:
        fr = _fill_random(body)
        if fr and camp.get("kind") == "message":
            raise _err(422, VALIDATION_FAILED, "random fill applies to agent campaigns only",
                       {"fill_random": "not_applicable"})
        if fr != bool(camp.get("fill_random")):
            params["fr"] = fr
            sets.append("fill_random = :fr")
            before["fill_random"], after["fill_random"] = bool(camp.get("fill_random")), fr
    if "settings" in body:
        new = _settings(body.get("settings"), _settings_of(camp))
        for k, v in new.items():
            if v != camp[k]:
                params[f"s_{k}"] = v
                sets.append(f"{k} = :s_{k}")
                before[k], after[k] = camp[k], v
    if sets:
        sess().execute(text(f"UPDATE campaigns SET {', '.join(sets)} WHERE id = CAST(:c AS uuid)"), params)
        audit.record("campaign.update", "campaign", str(campaign_id), before=before, after=after)
    return str(campaign_id)


# --------------------------------------------------------------------------- lifecycle
def _cancel_queued(campaign_id, code: str) -> list[str]:
    """Campaign calls still queued (never handed to the PBX) are cancelled; their reservations
    are released `cancel_unsent`. A row a dispatcher is claiming right now is skipped: it is
    being sent and continues like any live call."""
    from services.calling import attempts as A
    rows = sess().execute(text("""
        SELECT q.id AS queue_id, a.id AS attempt_id, a.case_id FROM call_dispatch_queue q
          JOIN call_attempts a ON a.id = q.attempt_id
         WHERE a.campaign_id = CAST(:c AS uuid) AND q.state = 'queued'
         FOR UPDATE OF q SKIP LOCKED"""), {"c": str(campaign_id)}).mappings().all()
    out = []
    for r in rows:
        a = A.lock_attempt(r["attempt_id"])
        if a["state"] not in ("prepared", "authorized"):
            continue
        sess().execute(text("""UPDATE call_dispatch_queue SET state = 'cancelled', finished_at = now(), result_code = :rc
                                WHERE id = CAST(:id AS uuid)"""), {"rc": code, "id": str(r["queue_id"])})
        A.transition(r["attempt_id"], "cancelled", reason_code=code)
        released = A.release_reservation(r["attempt_id"], "cancel_unsent", by=me(), reason=f"campaign {code}")
        audit.record("call.cancel", "call_attempt", str(r["attempt_id"]), case_id=str(r["case_id"]),
                     before={"state": a["state"]}, after={"state": "cancelled", "reservation_released": released,
                                                          "campaign_id": str(campaign_id)}, reason=code)
        out.append(str(r["attempt_id"]))
    return out


def transition(campaign_id, action: str, body: dict, expected) -> dict:
    _unknown(body, {"expected_revision", "reason"})
    reason = _reason(body)
    camp = load(campaign_id, lock="update", manage=True)
    _check_revision(camp, expected)
    allowed, target = TRANSITIONS[action]
    if camp["status"] not in allowed:
        raise _err(409, "invalid_transition", f"cannot {action} a campaign that is {camp['status']}",
                   {"status": camp["status"], "action": action})
    sets = ["status = :st"]
    params = {"st": target, "c": str(campaign_id)}
    if action == "start":
        active = sess().execute(text("SELECT count(*) FROM campaign_members WHERE campaign_id = CAST(:c AS uuid) "
                                     "AND active"), {"c": str(campaign_id)}).scalar()
        if not active:
            raise _err(409, "no_members", "add at least one active member before starting the campaign")
        if camp.get("ends_at") is not None and camp["ends_at"] <= _dt.datetime.now(_dt.timezone.utc):
            raise _err(409, "campaign_time_limit_passed", "the campaign's end time has passed; change it to start",
                       {"ends_at": iso(camp["ends_at"])})
        if camp.get("kind") == "message":                 # 2026-10-25 (0088): the audio must be on the PBX
            from services.calling import message_campaign as MC
            installed = {s_["kind"] for s_ in MC.sounds(campaign_id) if s_["installed"]}
            need = {"message"} | ({"callback"} if camp["message_transfer"] else set())
            if need - installed:
                raise _err(409, "sounds_missing", "upload the messages (and install them on the PBX) before starting",
                           {"missing": sorted(need - installed)})
        sets += ["started_at = coalesce(started_at, now())", "paused_at = NULL"]
    elif action == "pause":
        sets.append("paused_at = now()")
    elif action == "drain":
        sets.append("drain_requested_at = now()")
    elif action == "stop":
        sets += ["stopped_at = now()", "stopped_by = CAST(:by AS uuid)", "stop_reason = :sr"]
        params.update(by=me(), sr="discarded" if camp["status"] == "draft" else "stopped")
    sess().execute(text(f"UPDATE campaigns SET {', '.join(sets)} WHERE id = CAST(:c AS uuid)"), params)
    cancelled = []
    if action in ("pause", "drain", "stop"):
        cancelled = _cancel_queued(campaign_id, {"pause": "campaign_paused", "drain": "campaign_draining",
                                                 "stop": "campaign_stopped"}[action])
    audit.record(f"campaign.{'resume' if action == 'start' and camp['status'] == 'paused' else action}", "campaign",
                 str(campaign_id), before={"status": camp["status"]},
                 after={"status": target, "cancelled_queued": cancelled}, reason=reason)
    drained = False
    if action == "drain":                        # nothing outstanding: stopped at once (audited campaign.drained)
        drained = CD.finish_drain(sess(), campaign_id, audit_kw={"source": "api"})
    return {"cancelled_queued": cancelled, "drained": drained}


# --------------------------------------------------------------------------- roster
def roster(campaign_id) -> dict:
    load(campaign_id, manage=True)
    rows = sess().execute(text(f"""
        SELECT m.*, {USER_NAME_SQL.format(a='u')} AS name, u.is_active AS user_active, ue.extension,
               {USER_NAME_SQL.format(a='ab')} AS added_by_name, {USER_NAME_SQL.format(a='cb')} AS changed_by_name
          FROM campaign_members m
          LEFT JOIN users u ON u.id = m.user_id
          LEFT JOIN users ab ON ab.id = m.added_by
          LEFT JOIN users cb ON cb.id = m.changed_by
          LEFT JOIN user_extensions ue ON ue.user_id = m.user_id AND ue.released_at IS NULL
         WHERE m.campaign_id = CAST(:c AS uuid)
         ORDER BY m.active DESC, name, m.user_id"""), {"c": str(campaign_id)}).mappings().all()
    return {"items": [{"user": user_ref(r["user_id"], r["name"]), "active": bool(r["active"]),
                       "user_active": bool(r["user_active"]) if r["user_active"] is not None else False,
                       "extension": r["extension"], "added_by": user_ref(r["added_by"], r["added_by_name"]),
                       "added_at": iso(r["added_at"]), "changed_by": user_ref(r["changed_by"], r["changed_by_name"]),
                       "changed_at": iso(r["changed_at"]), "change_reason": r["change_reason"]} for r in rows]}


def _touch_roster(campaign_id):
    sess().execute(text("UPDATE campaigns SET roster_changed_at = clock_timestamp() WHERE id = CAST(:c AS uuid)"),
                   {"c": str(campaign_id)})


def add_members(campaign_id, body: dict, expected) -> dict:
    _unknown(body, {"expected_revision", "user_ids", "reason"})
    reason = _reason(body)
    camp = load(campaign_id, lock="update", manage=True)
    _check_revision(camp, expected)
    if camp["status"] == "stopped":
        raise _err(409, "campaign_stopped", "a stopped campaign cannot be changed", {"status": camp["status"]})
    users = _user_ids(body.get("user_ids"), "user_ids")
    if not users:
        raise _err(422, VALIDATION_FAILED, "user_ids is required", {"user_ids": "required"})
    _check_members_allowed(users)
    added, reactivated = [], []
    for u in users:
        r = sess().execute(text("""
            INSERT INTO campaign_members (campaign_id, user_id, added_by) VALUES (CAST(:c AS uuid), CAST(:u AS uuid),
                   CAST(:by AS uuid))
            ON CONFLICT (campaign_id, user_id) DO UPDATE
               SET active = true, changed_by = CAST(:by AS uuid), changed_at = now(), change_reason = :why
             WHERE NOT campaign_members.active
            RETURNING (xmax = 0) AS inserted"""), {"c": str(campaign_id), "u": u, "by": me(), "why": reason}).first()
        if r is None:
            continue                                     # already an active member
        (added if r[0] else reactivated).append(u)
    if added or reactivated:
        _touch_roster(campaign_id)
        audit.record("campaign.members.add", "campaign", str(campaign_id),
                     after={"added": added, "reactivated": reactivated}, reason=reason)
    return {"added": added, "reactivated": reactivated}


def deactivate_member(campaign_id, user_id, body: dict, expected) -> dict:
    _unknown(body, {"expected_revision", "reason"})
    reason = _reason(body, required=True)
    camp = load(campaign_id, lock="update", manage=True)
    _check_revision(camp, expected)
    if camp["status"] == "stopped":
        raise _err(409, "campaign_stopped", "a stopped campaign cannot be changed", {"status": camp["status"]})
    r = sess().execute(text("""
        UPDATE campaign_members SET active = false, changed_by = CAST(:by AS uuid), changed_at = now(), change_reason = :why
         WHERE campaign_id = CAST(:c AS uuid) AND user_id = CAST(:u AS uuid) AND active RETURNING user_id"""),
        {"c": str(campaign_id), "u": str(user_id), "by": me(), "why": reason}).first()
    if r is None:
        raise _err(404, NOT_FOUND, "no active member with this id", {"user_id": str(user_id)})
    # no new call for them; a live call is NOT touched
    sess().execute(text("""
        UPDATE campaign_readiness SET state = 'offline', state_since = clock_timestamp(), state_reason = 'member_removed',
               lease_until = clock_timestamp(), heartbeat_at = clock_timestamp(), next_requested_at = NULL
         WHERE campaign_id = CAST(:c AS uuid) AND user_id = CAST(:u AS uuid)"""),
        {"c": str(campaign_id), "u": str(user_id)})
    _touch_roster(campaign_id)
    audit.record("campaign.members.deactivate", "campaign", str(campaign_id), after={"user_id": str(user_id)},
                 reason=reason)
    return {"deactivated": str(user_id)}


# --------------------------------------------------------------------------- readiness
def _member_campaign(campaign_id, *, lock=None) -> dict:
    """The campaign for a request by one of its members (404 otherwise, 409 when deactivated)."""
    camp = load(campaign_id, lock=lock)
    m = _membership(campaign_id, me())
    if m is None:
        raise _err(404, NOT_FOUND, "campaign not found", {"campaign_id": str(campaign_id)})
    if not m["active"]:
        raise _err(409, "member_inactive", "you were removed from this campaign's roster")
    return camp


def _state_arg(body) -> str | None:
    v = body.get("state")
    if v is None:
        return None
    if v == "wrap-up":
        v = "wrap_up"
    if v not in READINESS:
        raise _err(422, VALIDATION_FAILED, "state must be ready, paused, wrap_up or offline", {"state": "invalid"})
    return v


def _ready_elsewhere(campaign_id):
    r = sess().execute(text("""
        SELECT r.campaign_id FROM campaign_readiness r JOIN campaigns c ON c.id = r.campaign_id
         WHERE r.user_id = CAST(:u AS uuid) AND r.campaign_id <> CAST(:c AS uuid) AND r.state IN ('ready', 'wrap_up')
           AND r.lease_until > clock_timestamp() AND c.status IN ('draft', 'running', 'paused', 'draining')
         LIMIT 1"""), {"u": me(), "c": str(campaign_id)}).first()
    if r is not None:
        raise _err(409, "ready_in_other_campaign", "you are ready in another campaign; go offline there first",
                   {"campaign_id": str(r[0])})


def _set_readiness(camp, state: str | None, *, next_request=False) -> dict:
    """Upsert my lease (and state). Returns {"changed": bool, "before": state}."""
    cid, uid = str(camp["id"]), me()
    cur = sess().execute(text("""SELECT * FROM campaign_readiness WHERE campaign_id = CAST(:c AS uuid)
                                   AND user_id = CAST(:u AS uuid) FOR UPDATE"""), {"c": cid, "u": uid}).mappings().first()
    if cur is None and state is None:
        raise _err(422, VALIDATION_FAILED, "the first heartbeat needs a state", {"state": "required"})
    new = state or cur["state"]
    lease = "clock_timestamp()" if new == "offline" else "clock_timestamp() + make_interval(secs => :ls)"
    changed = cur is None or cur["state"] != new
    params = {"c": cid, "u": uid, "st": new, "ls": int(camp["lease_seconds"])}
    if cur is None:
        sess().execute(text(f"""
            INSERT INTO campaign_readiness (campaign_id, user_id, state, state_since, lease_until, heartbeat_at,
                                            next_requested_at)
            VALUES (CAST(:c AS uuid), CAST(:u AS uuid), :st, clock_timestamp(), {lease}, clock_timestamp(),
                    CASE WHEN :nx THEN clock_timestamp() END)"""), {**params, "nx": next_request})
    else:
        sess().execute(text(f"""
            UPDATE campaign_readiness
               SET state = :st, lease_until = {lease}, heartbeat_at = clock_timestamp(),
                   state_since = CASE WHEN :chg THEN clock_timestamp() ELSE state_since END,
                   state_reason = CASE WHEN :chg THEN NULL ELSE state_reason END,
                   next_requested_at = CASE WHEN :nx THEN clock_timestamp()
                                            WHEN :st IN ('paused', 'offline') THEN NULL ELSE next_requested_at END,
                   block_reason = CASE WHEN :chg OR :nx THEN NULL ELSE block_reason END,
                   block_detail = CASE WHEN :chg OR :nx THEN NULL ELSE block_detail END,
                   block_checked_at = CASE WHEN :chg OR :nx THEN NULL ELSE block_checked_at END
             WHERE campaign_id = CAST(:c AS uuid) AND user_id = CAST(:u AS uuid)"""),
            {**params, "chg": changed, "nx": next_request})
    return {"changed": changed, "before": cur["state"] if cur else None, "state": new}


def heartbeat(campaign_id, body: dict) -> dict:
    _unknown(body, {"state"})
    state = _state_arg(body)
    camp = _member_campaign(campaign_id)
    if camp["status"] == "stopped" and state in ("ready", "wrap_up"):
        raise _err(409, "campaign_stopped", "the campaign is stopped", {"status": "stopped"})
    if state in ("ready", "wrap_up"):
        _ready_elsewhere(campaign_id)
    out = _set_readiness(camp, state)
    if out["changed"]:
        audit.record(f"campaign.readiness.{out['state']}", "campaign", str(campaign_id),
                     before={"state": out["before"]}, after={"state": out["state"], "user_id": me()})
    return my_status(campaign_id)


def request_next(campaign_id, body: dict) -> dict:
    """The member clicks "next": ready now, skip the rest of the wrap-up delay, and try to
    dispatch at once through the same dispatch_member() as the calling service."""
    from services.calling import rules as R
    _unknown(body, set())
    camp = _member_campaign(campaign_id, lock="share")
    if camp.get("kind") == "message":
        raise _err(409, "message_campaign", "a message campaign dials the clients itself; stay ready to take a transfer")
    if camp["status"] != "running":
        raise _err(409, "campaign_not_running", f"the campaign is {camp['status']}", {"status": camp["status"]})
    active = sess().execute(text("""SELECT attempt_id FROM call_reservations WHERE collector_user_id = CAST(:u AS uuid)
                                      AND released_at IS NULL LIMIT 1"""), {"u": me()}).first()
    if active is not None:
        raise _err(409, "call_in_progress", "finish the current call and its wrap-up first",
                   {"attempt_id": str(active[0])})
    _ready_elsewhere(campaign_id)
    out = _set_readiness(camp, "ready", next_request=True)
    audit.record("campaign.next", "campaign", str(campaign_id), before={"state": out["before"]},
                 after={"state": "ready", "user_id": me()})
    result = None
    member = CD.eligible_member(sess(), camp, user_id=me(), skip_locked=False)
    if member is not None:
        result = CD.dispatch_member(sess(), camp, member, transport=R.transport_status, dispatched_by="api:next",
                                    audit_kw={"source": "api"})
    status = my_status(campaign_id)
    status["dispatch"] = result
    return status


# --------------------------------------------------------------------------- member states
_MEMBER_FACTS = f"""
    SELECT m.user_id, m.active, {USER_NAME_SQL.format(a='u')} AS name, ue.extension,
           r.state, r.state_since, r.state_reason, r.lease_until, r.heartbeat_at, r.next_requested_at,
           r.last_dispatch_at, r.last_attempt_id, r.block_reason, r.block_detail, r.block_checked_at,
           (r.lease_until > clock_timestamp()) AS lease_live, clock_timestamp() AS now,
           res.attempt_id AS active_attempt_id, aa.state AS active_state, aa.reason_code AS active_reason,
           aa.case_id AS active_case_id, aa.mode AS active_mode, aa.campaign_id AS active_campaign_id,
           aa.authorized_at AS active_authorized_at, aa.bridged_at AS active_bridged_at,
           aa.ended_at AS active_ended_at, ac.name AS active_case_name, ac."accountNumber" AS active_case_account,
           aa.contact_id AS active_contact_id, aa.destination AS active_destination, ack.contact AS active_contact_value,
           aca.contact_choice AS active_contact_choice, aca.contact_position AS active_contact_position,
           aca.contact_count AS active_contact_count,
           last.released_at AS last_release_at
      FROM campaign_members m
      LEFT JOIN users u ON u.id = m.user_id
      LEFT JOIN user_extensions ue ON ue.user_id = m.user_id AND ue.released_at IS NULL
      LEFT JOIN campaign_readiness r ON r.campaign_id = m.campaign_id AND r.user_id = m.user_id
      LEFT JOIN call_reservations res ON res.collector_user_id = m.user_id AND res.released_at IS NULL
      LEFT JOIN call_attempts aa ON aa.id = res.attempt_id
      LEFT JOIN clints ac ON ac.id = aa.case_id
      LEFT JOIN "ClintContacts" ack ON ack.id = aa.contact_id
      LEFT JOIN campaign_attempts aca ON aca.attempt_id = aa.id
      LEFT JOIN LATERAL (SELECT cr.released_at FROM campaign_attempts ca
                           JOIN call_reservations cr ON cr.attempt_id = ca.attempt_id
                          WHERE ca.campaign_id = m.campaign_id AND ca.user_id = m.user_id
                          ORDER BY ca.created_at DESC LIMIT 1) last ON true
     WHERE m.campaign_id = CAST(:c AS uuid)"""


def member_facts(campaign_id, user_id=None) -> list[dict]:
    extra = " AND m.user_id = CAST(:u AS uuid)" if user_id else ""
    return [dict(r) for r in sess().execute(text(_MEMBER_FACTS + extra + " ORDER BY m.active DESC, name, m.user_id"),
                                            {"c": str(campaign_id), "u": str(user_id) if user_id else None})
            .mappings().all()]


def effective_state(camp: dict, f: dict, transport_ok: bool) -> dict:
    """-> {"state", "reason", "ready_at"}: see the module docstring."""
    now = f["now"]
    if not f["active"]:
        return {"state": "removed", "reason": "member_inactive", "ready_at": None}
    if f["active_attempt_id"]:
        st = f["active_state"]
        if st in CD.LIVE_STATES:
            return {"state": "on_call", "reason": None, "ready_at": None}
        if st == "uncertain":
            return {"state": "on_call", "reason": "call_uncertain", "ready_at": None}
        return {"state": "wrap_up", "reason": "wrapup_pending", "ready_at": None}
    if f["state"] is None or f["state"] == "offline":
        return {"state": "offline", "reason": f["state_reason"], "ready_at": None}
    if not f["lease_live"]:
        return {"state": "offline", "reason": "lease_expired", "ready_at": None}
    if f["state"] == "paused":
        return {"state": "paused", "reason": f["state_reason"], "ready_at": None}
    delay = int(camp["wrapup_delay_seconds"])
    rel, nxt = f["last_release_at"], f["next_requested_at"]
    if rel is not None and delay and not (nxt is not None and nxt > rel):
        ready_at = rel + _dt.timedelta(seconds=delay)
        if ready_at > now:
            return {"state": "wrap_up", "reason": "wrapup_delay", "ready_at": iso(ready_at)}
    if camp["next_mode"] == "manual":
        live_next = nxt is not None and nxt > now - _dt.timedelta(seconds=CD.NEXT_REQUEST_TTL_SECONDS)
        if not live_next:
            return {"state": "waiting_next", "reason": "waiting_next", "ready_at": None}
    if camp["status"] != "running":
        return {"state": "ready", "reason": f"campaign_{camp['status']}", "ready_at": None}
    if not transport_ok:
        return {"state": "ready", "reason": "transport_not_connected", "ready_at": None}
    if f["block_reason"] and f["block_checked_at"] and f["block_checked_at"] > now - _dt.timedelta(
            seconds=BLOCK_FRESH_SECONDS):
        return {"state": "ready", "reason": f["block_reason"], "ready_at": None}
    return {"state": "ready", "reason": None, "ready_at": None}


def _attempt_brief(f) -> dict | None:
    if not f["active_attempt_id"]:
        return None
    return {"id": s(f["active_attempt_id"]), "state": f["active_state"], "reason_code": f["active_reason"],
            "mode": f["active_mode"], "campaign_id": s(f["active_campaign_id"]),
            "case": {"id": s(f["active_case_id"]), "name": f["active_case_name"],
                     "account_number": f["active_case_account"]},
            "authorized_at": iso(f["active_authorized_at"]), "bridged_at": iso(f["active_bridged_at"]),
            "ended_at": iso(f["active_ended_at"]), "dialed": _dialed(f)}


def _dialed(f) -> dict | None:
    """2026-10-21: the number this call dialled (its rotation place for a campaign call). The
    number itself needs clints_details, as in the call log (services/calling/queries.py)."""
    if not f["active_contact_id"] and not f["active_destination"]:
        return None
    show = has_permission("clints_details")
    return {"contact_id": s(f["active_contact_id"]), "choice": f["active_contact_choice"],
            "number": (f["active_contact_value"] or f["active_destination"]) if show else None,
            "position": f["active_contact_position"], "of": f["active_contact_count"]}


def _today(campaign_id, user_id=None) -> dict:
    """{user_id | '*': counts} of this campaign's calls on the Riyadh day."""
    extra = " AND ca.user_id = CAST(:u AS uuid)" if user_id else ""
    params = {"c": str(campaign_id), "u": str(user_id) if user_id else None}
    rows = sess().execute(text(f"""
        SELECT ca.user_id, count(*) AS attempts,
               count(*) FILTER (WHERE a.bridged_at IS NOT NULL) AS conversations,
               count(*) FILTER (WHERE a.customer_answered_at IS NOT NULL) AS customer_answered,
               count(w.id) AS wrapped_up,
               count(*) FILTER (WHERE a.state = 'failed') AS failed,
               count(*) FILTER (WHERE a.state = 'cancelled') AS cancelled,
               count(*) FILTER (WHERE a.state = 'uncertain') AS uncertain,
               count(*) FILTER (WHERE a.state IN ('prepared', 'authorized', 'dispatched', 'agent_ringing',
                                                  'agent_answered', 'customer_dialing', 'customer_answered',
                                                  'bridged')) AS live
          FROM campaign_attempts ca
          JOIN call_attempts a ON a.id = ca.attempt_id
          LEFT JOIN call_wrapups w ON w.attempt_id = a.id
         WHERE ca.campaign_id = CAST(:c AS uuid) {extra}
           AND ca.created_at >= (date_trunc('day', clock_timestamp() AT TIME ZONE 'Asia/Riyadh')) AT TIME ZONE 'Asia/Riyadh'
         GROUP BY ROLLUP (ca.user_id)"""), params).mappings().all()
    outcomes = sess().execute(text(f"""
        SELECT ca.user_id, w.outcome, count(*) AS n
          FROM campaign_attempts ca JOIN call_wrapups w ON w.attempt_id = ca.attempt_id
         WHERE ca.campaign_id = CAST(:c AS uuid) {extra}
           AND ca.created_at >= (date_trunc('day', clock_timestamp() AT TIME ZONE 'Asia/Riyadh')) AT TIME ZONE 'Asia/Riyadh'
         GROUP BY GROUPING SETS ((ca.user_id, w.outcome), (w.outcome))"""), params).mappings().all()
    # 2026-10-21: unassigned cases this campaign's promises assigned to the caller today
    assigned = sess().execute(text(f"""
        SELECT ca.user_id, count(*) AS n FROM campaign_case_assignments ca
         WHERE ca.campaign_id = CAST(:c AS uuid) {extra}
           AND ca.created_at >= (date_trunc('day', clock_timestamp() AT TIME ZONE 'Asia/Riyadh')) AT TIME ZONE 'Asia/Riyadh'
         GROUP BY ROLLUP (ca.user_id)"""), params).mappings().all()
    out = {}
    for r in rows:
        k = s(r["user_id"]) or "*"
        out[k] = {x: int(r[x]) for x in ("attempts", "conversations", "customer_answered", "wrapped_up", "failed",
                                          "cancelled", "uncertain", "live")}
        out[k]["outcomes"] = {}
    for r in assigned:
        out.setdefault(s(r["user_id"]) or "*", {"outcomes": {}})["assigned_by_promise"] = int(r["n"])
    for r in outcomes:
        if r["outcome"] is None:
            continue
        k = s(r["user_id"]) or "*"
        out.setdefault(k, {"outcomes": {}})["outcomes"][r["outcome"]] = int(r["n"])
    empty = {"attempts": 0, "conversations": 0, "customer_answered": 0, "wrapped_up": 0, "failed": 0, "cancelled": 0,
             "uncertain": 0, "live": 0, "outcomes": {}, "assigned_by_promise": 0}
    return {k: {**empty, **v} for k, v in out.items()} | ({} if out else {"*": dict(empty)})


def _member_view(camp, f, transport_ok, today) -> dict:
    eff = effective_state(camp, f, transport_ok)
    att = _attempt_brief(f)
    if att is not None:
        att["this_campaign"] = att["campaign_id"] == str(camp["id"])
    empty = {"attempts": 0, "conversations": 0, "customer_answered": 0, "wrapped_up": 0, "failed": 0, "cancelled": 0,
             "uncertain": 0, "live": 0, "outcomes": {}, "assigned_by_promise": 0}
    return {
        "user": user_ref(f["user_id"], f["name"]), "active": bool(f["active"]), "extension": f["extension"],
        "state": eff["state"], "reason": eff["reason"], "ready_at": eff["ready_at"],
        "declared": None if f["state"] is None else {
            "state": f["state"], "since": iso(f["state_since"]), "reason": f["state_reason"],
            "lease_until": iso(f["lease_until"]), "lease_live": bool(f["lease_live"]),
            "heartbeat_at": iso(f["heartbeat_at"])},
        "next_requested_at": iso(f["next_requested_at"]), "last_dispatch_at": iso(f["last_dispatch_at"]),
        "blocked": None if not f["block_reason"] else {"reason": f["block_reason"], "detail": f["block_detail"],
                                                        "checked_at": iso(f["block_checked_at"])},
        "current_attempt": att, "today": today.get(str(f["user_id"]), empty),
    }


def my_status(campaign_id) -> dict:
    from services.calling import live as live_svc
    from services.calling import rules as R
    camp = _member_campaign(campaign_id)
    facts = member_facts(campaign_id, me())
    f = facts[0]
    transport_ok = R.transport_status() is not None
    view = _member_view(camp, f, transport_ok, _today(campaign_id, me()))
    attempt = None
    if f["active_attempt_id"]:
        try:
            attempt = live_svc.live_view(f["active_attempt_id"])
        except ApiError:
            attempt = None
    nxt = f["next_requested_at"]
    return {
        "campaign": campaign_view(camp), "member": view, "state": view["state"], "reason": view["reason"],
        "ready_at": view["ready_at"],
        "next": {"mode": camp["next_mode"],
                 "pending": bool(nxt is not None and nxt > f["now"] - _dt.timedelta(seconds=CD.NEXT_REQUEST_TTL_SECONDS)),
                 "requested_at": iso(nxt),
                 "expires_at": iso(nxt + _dt.timedelta(seconds=CD.NEXT_REQUEST_TTL_SECONDS)) if nxt else None},
        "attempt": attempt,
        "message_transfer": _message_transfer(camp),
        "heartbeat_after_ms": max(3000, int(camp["lease_seconds"]) * 1000 // 3),
        "poll_after_ms": 1000 if view["state"] in ("on_call", "ready") else 3000,
    }


def _message_transfer(camp) -> dict | None:
    if camp.get("kind") != "message":
        return None
    from services.calling import message_campaign as MC
    return MC.member_transfer(camp["id"], me())


def _message_summary(camp) -> dict | None:
    if camp.get("kind") != "message":
        return None
    from services.calling import message_campaign as MC
    return MC.summary(camp)


def live_view(campaign_id) -> dict:
    from services.calling import rules as R
    camp = load(campaign_id, manage=True)
    facts = member_facts(campaign_id)
    transport = R.transport_status()
    today = _today(campaign_id)
    members = [_member_view(camp, f, transport is not None, today) for f in facts]
    by_state = {}
    for m in members:
        if m["active"]:
            by_state[m["state"]] = by_state.get(m["state"], 0) + 1
    active_ids = [str(f["user_id"]) for f in facts if f["active"]]
    totals = sess().execute(text("""
        SELECT count(*) AS attempts, count(*) FILTER (WHERE a.state = 'uncertain') AS uncertain,
               count(*) FILTER (WHERE w.id IS NULL AND a.state IN ('ended', 'failed')
                                  AND EXISTS (SELECT 1 FROM call_reservations r WHERE r.attempt_id = a.id
                                                 AND r.released_at IS NULL)) AS awaiting_wrapup
          FROM campaign_attempts ca JOIN call_attempts a ON a.id = ca.attempt_id
          LEFT JOIN call_wrapups w ON w.attempt_id = a.id
         WHERE ca.campaign_id = CAST(:c AS uuid)"""), {"c": str(campaign_id)}).mappings().first()
    return {
        "campaign": campaign_view(camp),
        "transport": {"connected": transport is not None, "epoch": transport["epoch"] if transport else None},
        "members": members, "members_by_state": by_state,
        "pool": _pool_summary(camp, active_ids),
        "today": today.get("*"),
        "all_time": {"attempts": int(totals["attempts"]), "uncertain": int(totals["uncertain"]),
                     "awaiting_wrapup": int(totals["awaiting_wrapup"])},
        "outstanding_calls": CD.outstanding_calls(sess(), campaign_id),
        "messages": _message_summary(camp),               # 2026-10-25 (0088): message campaigns only
        "poll_after_ms": 2000 if camp["status"] in ("running", "draining") else 10000,
    }


def _pool_summary(camp, active_ids) -> dict:
    k = (int(camp["revision"]), tuple(sorted(active_ids)))
    hit = _summary_cache.get(str(camp["id"]))
    now = time.monotonic()
    if hit is not None and hit[0] > now and hit[1] == k:
        return {**hit[2], "cached": True}
    out = _with_progress(camp, P.summary(camp, active_ids))
    if len(_summary_cache) > 500:
        _summary_cache.clear()
    _summary_cache[str(camp["id"])] = (now + SUMMARY_TTL_SECONDS, k, out)
    return {**out, "cached": False}


def pool_view(campaign_id, args, page, size, offset) -> dict:
    camp = load(campaign_id, manage=True)
    active_ids = [str(r[0]) for r in sess().execute(text(
        "SELECT user_id FROM campaign_members WHERE campaign_id = CAST(:c AS uuid) AND active"),
        {"c": str(campaign_id)}).all()]
    reasons = [x for x in (args.get("reason") or "").split(",") if x]
    out = P.listing(camp, active_ids, reason=reasons or None, page=page, size=size, offset=offset,
                    show_numbers=has_permission("clints_details"))
    out["summary"] = _with_progress(camp, P.summary(camp, active_ids))
    return out


def _with_progress(camp, summary: dict) -> dict:
    """2026-10-24: + "called" / "answered" (customers this campaign dialled / that answered) and
    "waiting" (in the pool, not excluded for good, never dialled by this campaign: the queue)."""
    from services.calling.campaign_cases import called_counts
    return {**summary, **called_counts(camp["id"])}

