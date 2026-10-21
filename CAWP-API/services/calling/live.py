"""The live call view (Phase 5 P1): what the frontend's live-call panel polls.

`GET /v2/calls/<id>/live` returns the attempt (the call-detail shape without the raw evidence
dump) plus: the dispatch handoff state, whether a conversation is EVIDENCED (bridge
membership of both legs, never inferred from two answered legs), the uncertainty (reason),
open review flags, the latest reconciliation decisions, the actions the caller may take now,
a merged human-readable timeline (PBX events in PBX time, AGI decisions in database time) and
a polling hint:

    poll_after_ms = 1000 while the call is live, 5000 while it is uncertain, null when final.

Polling, not long-polling: the API runs sync gunicorn workers, so a held request would pin a
worker per open panel. Every response carries an ETag; `If-None-Match` answers 304 without a
body when nothing changed (revision, evidence, flags, reconciliations, dispatch state).
"""
from __future__ import annotations

from sqlalchemy import text

from services.calling import review
from services.calling import wrapup as W
from services.calling.common import can_supervise, iso, me, sess

LIVE_STATES = ("prepared", "authorized", "dispatched", "agent_ringing", "agent_answered", "customer_dialing",
               "customer_answered", "bridged")
# without bridge evidence (do_not_call only for calls_manage; wrapup.allowed_outcomes decides)
NO_CONVERSATION_OUTCOMES = ("no_answer", "busy", "wrong_number", "do_not_call")
AGENT_STATES = {"4": "agent_ringing", "5": "agent_ringing", "6": "agent_answered"}
CUSTOMER_STATES = {"4": "customer_ringing", "5": "customer_ringing", "6": "customer_answered"}


def _dispatch(attempt_id):
    r = sess().execute(text("""
        SELECT state, result_code, created_at, expires_at, claimed_at, finished_at, ami_response,
               agent_ring_seconds, max_call_seconds
          FROM call_dispatch_queue WHERE attempt_id = CAST(:a AS uuid)"""), {"a": str(attempt_id)}).mappings().first()
    if r is None:
        return None
    return {"state": r["state"], "result_code": r["result_code"], "queued_at": iso(r["created_at"]),
            "expires_at": iso(r["expires_at"]), "claimed_at": iso(r["claimed_at"]),
            "finished_at": iso(r["finished_at"]), "ami_response": r["ami_response"],
            "agent_ring_seconds": r["agent_ring_seconds"], "max_call_seconds": r["max_call_seconds"],
            "note": "submitted = the PBX accepted the Originate: submitted, not connected"}


def _ts(v):
    return None if v is None else format(v, "f")


def timeline(attempt_id, agent: str, limit: int = 300) -> list[dict]:
    events = sess().execute(text("""
        SELECT id, event, state, pbx_ts, uniqueid, payload, received_at, pbx_epoch
          FROM call_events WHERE attempt_id = CAST(:a AS uuid) ORDER BY pbx_ts NULLS LAST, id LIMIT :lim"""),
        {"a": str(attempt_id), "lim": limit}).mappings().all()
    customers = {(e["payload"] or {}).get("DestUniqueid") for e in events
                 if e["event"] == "DialBegin" and e["uniqueid"] == agent}
    customers.discard(None)
    customers.discard(agent)
    out = []
    for e in events:
        p = e["payload"] or {}
        uid = e["uniqueid"]
        leg = "agent" if uid == agent or (not uid and p.get("DestUniqueid") == agent) else (
            "customer" if uid in customers else "other")
        if e["event"] == "OriginateResponse" and p.get("correlated_by") == "action_id":
            leg = "agent"                       # Asterisk 11: by the Originate's ActionID (a failure names no channel)
        kind, detail = None, {}
        ev = e["event"]
        if ev == "Newchannel":
            kind = f"{leg}_channel_created"
        elif ev == "Newstate":
            st = p.get("ChannelState")
            kind = (AGENT_STATES if leg == "agent" else CUSTOMER_STATES).get(st) if leg != "other" else None
            kind = kind or f"{leg}_state"
            detail = {"channel_state": p.get("ChannelStateDesc")}
        elif ev == "DialBegin":
            if uid == agent:
                kind = "customer_dial_started"
            elif p.get("DestUniqueid") == agent:
                kind = "agent_dial_started"
        elif ev == "DialEnd":
            kind, detail = ("customer_dial_ended" if uid == agent else "agent_dial_ended"), {"dial_status": p.get("DialStatus")}
        elif ev in ("BridgeEnter", "BridgeLeave"):
            kind = "bridge_enter" if ev == "BridgeEnter" else "bridge_leave"
            detail = {"leg": leg, "bridge": p.get("BridgeUniqueid"), "members": p.get("BridgeNumChannels")}
        elif ev == "Hangup":
            kind, detail = f"{leg}_hangup", {"cause": p.get("Cause")}
        elif ev == "OriginateResponse":
            kind, detail = "originate_response", {"response": p.get("Response"), "reason": p.get("Reason")}
        elif ev == "UserEvent":
            kind = f"dialplan_{e['state']}" if e["state"] else "dialplan_event"
            detail = {k: p[k] for k in ("DialStatus", "Cause") if p.get(k)}
        if kind is None:
            continue
        out.append({"at_pbx": _ts(e["pbx_ts"]), "at": iso(e["received_at"]), "source": "pbx", "kind": kind,
                    "leg": leg, "event_id": e["id"], "detail": detail})
    for d in sess().execute(text("""SELECT occurred_at, decision, reason FROM call_agi_decisions
                                     WHERE attempt_id = CAST(:a AS uuid) ORDER BY id"""), {"a": str(attempt_id)}).mappings():
        out.append({"at_pbx": None, "at": iso(d["occurred_at"]), "source": "agi", "kind": f"agi_{d['decision']}",
                    "leg": "agent", "event_id": None, "detail": {"reason": d["reason"]},
                    "_t": d["occurred_at"].timestamp()})
    # one time line: PBX time for PBX events, database time for the AGI decisions (both wall clocks)
    for item in out:
        t = item.pop("_t", None)
        item["_k"] = t if t is not None else (float(item["at_pbx"]) if item["at_pbx"] else 0.0)
    out.sort(key=lambda i: i.pop("_k"))
    return out


def _etag(attempt_id, revision) -> str:
    r = sess().execute(text("""
        SELECT (SELECT coalesce(max(id), 0) FROM call_events WHERE attempt_id = CAST(:a AS uuid)) AS ev,
               (SELECT count(*) FROM call_agi_decisions WHERE attempt_id = CAST(:a AS uuid)) AS agi,
               (SELECT coalesce(max(id), 0) FROM call_reconciliations WHERE attempt_id = CAST(:a AS uuid)) AS rec,
               (SELECT count(*) || '-' || count(resolved_at) FROM call_review_flags WHERE attempt_id = CAST(:a AS uuid)) AS fl,
               (SELECT state FROM call_dispatch_queue WHERE attempt_id = CAST(:a AS uuid)) AS dq,
               (SELECT released_at IS NULL FROM call_reservations WHERE attempt_id = CAST(:a AS uuid)) AS res
    """), {"a": str(attempt_id)}).mappings().first()
    return f'W/"{revision}.{r["ev"]}.{r["agi"]}.{r["rec"]}.{r["fl"]}.{r["dq"]}.{r["res"]}"'


def etag(attempt_id) -> str | None:
    from services.calling import queries
    r = queries.visible_or_404(attempt_id)
    return _etag(attempt_id, int(r["revision"]))


def live_view(attempt_id) -> dict:
    from services.calling import queries
    d = queries.call_detail(attempt_id, with_evidence=False)
    state = d["state"]
    from services.calling.legs import agent_uid
    agent = agent_uid(sess(), attempt_id)          # cawp-<attempt>, or the bound Asterisk 11 leg
    raw = sess().execute(text("SELECT bridged_at, customer_answered_at, updated_at FROM call_attempts "
                              "WHERE id = CAST(:a AS uuid)"), {"a": str(attempt_id)}).mappings().first()
    dispatch = _dispatch(attempt_id)
    own = d["collector"] and d["collector"]["id"] == me()
    manager = can_supervise()                     # calls_supervise | calls_manage
    bridged = raw["bridged_at"] is not None
    # the same predicate as the wrap-up itself (wrapup.wrapup_access, 2026-10-17): the attempt's
    # own collector always; others need calls_manage with the case in scope or deleted
    access = W.wrapup_access({"case_id": d["case"]["id"], "collector_user_id": (d["collector"] or {}).get("id")})
    awaiting = state in ("ended", "failed") and d["wrapup"] is None and d["mode"] != "manual"
    can_wrapup = awaiting and access["allowed"]
    d["dispatch"] = dispatch
    d["live"] = state in LIVE_STATES
    d["conversation"] = {
        "evidenced": bridged, "customer_answered": raw["customer_answered_at"] is not None,
        "rule": "a conversation is proven only by the customer leg answering AND both legs in one bridge, "
                "correlated with this attempt; two answered legs alone are not a conversation"}
    d["uncertain"] = ({"reason": d["reason_code"], "since": iso(raw["updated_at"]),
                       "note": "the PBX state of this call is unknown until reconciliation or a supervisor "
                               "resolves it; the reservation stays"} if state == "uncertain" else None)
    d["flags"] = review.flags_of(attempt_id, open_only=True)
    d["reconciliations"] = review._reconciliations(attempt_id, limit=5)
    d["actions"] = {
        "can_cancel": (state in ("prepared", "authorized") and (dispatch is None or dispatch["state"] == "queued")
                       and (own or manager)),
        "can_wrapup": can_wrapup,
        "wrapup_outcomes": W.allowed_outcomes(bridged=bridged) if can_wrapup else [],
        # saved for supervisor review without case changes: case_deleted | case_reassigned |
        # case_locked | case_archived (null = a normal wrap-up)
        "wrapup_review_reason": access["review_reason"] if can_wrapup else None,
        "can_resolve": state == "uncertain" and manager,
        "can_release": bool(awaiting and manager and d["reservation"] and d["reservation"]["active"]),
    }
    d["events"] = timeline(attempt_id, agent)
    d["poll_after_ms"] = 1000 if d["live"] else (5000 if state == "uncertain" else None)
    d["etag"] = _etag(attempt_id, d["revision"])
    return d


def my_active() -> dict:
    """The caller's attempt that still holds a reservation (live, uncertain or awaiting wrap-up)."""
    r = sess().execute(text("""
        SELECT r.attempt_id FROM call_reservations r
         WHERE r.collector_user_id = CAST(:me AS uuid) AND r.released_at IS NULL
         ORDER BY r.reserved_at DESC LIMIT 1"""), {"me": me()}).first()
    if r is None:
        return {"attempt": None}
    return {"attempt": live_view(r[0])}
