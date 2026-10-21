"""Progressive campaign dispatch (Phase 5 P2). Progressive only: ONE call per ready collector,
never a predictive or speculative call.

Where it runs: a `CampaignDispatcher` thread in the `calling` service, next to the click-to-call
Dispatcher (services/calling/dispatch.py), and the member's own "next" request in the API
(services/calling/campaign.py). Both call `dispatch_member()`; nothing else creates campaign calls.
Why the calling service and not the worker: it is the only AMI owner, so it knows at once
whether a PBX session is connected and which PBX epoch a call would be sent on (no call is
authorized while it is disconnected); the click-to-call Dispatcher in the same process claims
the queue row within 0.25 s; and readiness leases are seconds-long, which the worker's job
leases are not built for.

One dispatch (one database transaction, committed or not at all):

    campaign row FOR SHARE (a pause/drain/stop waits for it, or it sees the new status)
    -> the member's readiness row FOR UPDATE SKIP LOCKED (one loop per member at a time):
       active member, declared ready (or wrap_up in auto mode) with a LIVE lease, no active
       reservation, the wrap-up delay after the last campaign call passed (unless the member
       clicked "next" since), and in manual mode an unexpired "next" request
    -> member-level checks (calls_make, active, extension, calling window) once
    -> candidates in dispatch order (campaign_pool.candidates), for each (at most 20):
         pg_try_advisory_xact_lock on the case (another loop has it: skip), re-check that its
         latest note does not stop campaigns (0080), choose the contact (the next number of the
         campaign's rotation over the debtor's callable numbers, campaign_pool.choose_contact),
         then in a SAVEPOINT the SAME code as POST /v2/calls: dispatch.authorize() (every rule,
         attempt mode=campaign, reservation, dispatch queue row, audit).
         A case-level refusal -> campaign_pool_state exclusion with a re-check time, next case.
         A member-level refusal -> the member is blocked with that reason, stop.
    -> campaign_attempts row, pool state (last tried, attempts today), readiness (last dispatch,
       "next" consumed), audit campaign.dispatch.

Then the click-to-call Dispatcher claims the queue row exactly as for a click-to-call and rechecks
every rule plus `claim_recheck()` (campaign still running, member still ready with a live lease,
the case's latest note does not stop campaigns) right before the token and the Originate.

Guarantees:
* never more than one outstanding attempt per collector: every campaign call holds a
  reservation until its wrap-up (or an evidence-proven no-contact release), and P0's partial
  unique index allows one active reservation per collector (and per case, per extension);
* restart-safe: the loop keeps no state that matters in memory. A crash before COMMIT leaves
  nothing; after COMMIT the queue row is claimed or expires as any click-to-call. Startup
  recovery (calling/main.py) marks in-flight attempts uncertain and KEEPS their reservations,
  so those members get no new call until a person or the evidence resolves them. No code here
  releases a reservation other than through the guarded paths (cancel_unsent for a queued
  call a pause/stop cancelled, no_customer_contact for a never-sent one);
* concurrent loops (several calling-service processes): the readiness row lock, the case
  advisory try-lock and the reservation unique indexes; tested with two loops;
* a lapsed lease, offline, pause, drain or stop never cut a live call: nothing here ever acts
  on a call in progress (the server cannot hang up anyway, P0 decision 7);
* auto-pause: a member whose last campaign call failed because THEIR extension did not answer
  (agent_no_answer, agent_busy, agent_rejected, agent_unavailable) is set to `paused`
  (state_reason agent_did_not_answer): an unavailable extension is not an available collector.

Fence: each service-loop pass over a campaign takes pg_try_advisory_xact_lock_shared(
'campaign_dispatch:<id>'); whoever holds the EXCLUSIVE lock keeps every calling-service loop away
from that campaign without blocking anything (the test suite uses it to isolate its campaigns from
a calling service running on the same database). A member's own "next" in the API is not fenced;
to stop dispatch for people, pause the campaign.
"""
from __future__ import annotations

import datetime as _dt
import logging
import threading
import time

from sqlalchemy import text

from core import audit
from core.api_response import ApiError
from services.calling import campaign_pool as P

log = logging.getLogger("cawp.calling.campaigns")

CANDIDATES_PER_DISPATCH = 20
BLOCK_BACKOFF_SECONDS = 5            # a blocked member (no case, window closed...) is re-checked after this
NEXT_REQUEST_TTL_SECONDS = 120       # a manual "next" click is valid this long
MEMBER_LEVEL = {"actor_inactive", "forbidden", "no_extension", "calling_windows_missing", "calling_window_closed",
                "calling_holiday", "transport_not_connected"}
AGENT_FAILURES = ("agent_no_answer", "agent_busy", "agent_rejected", "agent_unavailable")
LIVE_STATES = ("prepared", "authorized", "dispatched", "agent_ringing", "agent_answered", "customer_dialing",
               "customer_answered", "bridged")
# how long a case-level refusal keeps the case out of this campaign's candidates (it is always
# re-evaluated by the rules when offered again)
RECHECK_MINUTES = {"case_paid": 30, "status_stops_campaigns": 30, "destination_invalid": 30, "no_callable_contact": 30, "contact_not_found": 5,
                   "contact_deleted": 5, "contact_blocked": 5, "case_archived": 1, "case_locked": 1,
                   "do_not_call": 1, "calling_review_pending": 1}


# audit fields of what the calling service does by itself (no request, no person)
SYSTEM_AUDIT = {"actor_user_id": None, "request_id": None, "idempotency_key": None, "source": "campaign"}


def _stops(session, case_id) -> bool:
    """0080: the case's latest note stops campaigns (services/calling/campaign_rules.py)."""
    from services.calling.campaign_rules import case_stops_campaigns
    return case_stops_campaigns(session, case_id)


def fence_key(campaign_id) -> str:
    return f"campaign_dispatch:{campaign_id}"


def _day(session):
    return session.execute(text("SELECT (clock_timestamp() AT TIME ZONE 'Asia/Riyadh')::date")).scalar()


# --------------------------------------------------------------------------- the claim recheck
def claim_recheck(session, attempt) -> str | None:
    """Called by dispatch.claim() for a campaign attempt, right before the token and the Originate."""
    r = session.execute(text("""
        SELECT c.status, m.active, r.state, (r.lease_until > clock_timestamp()) AS lease_live
          FROM campaigns c
          LEFT JOIN campaign_members m ON m.campaign_id = c.id AND m.user_id = CAST(:u AS uuid)
          LEFT JOIN campaign_readiness r ON r.campaign_id = c.id AND r.user_id = CAST(:u AS uuid)
         WHERE c.id = CAST(:c AS uuid)"""),
        {"c": str(attempt["campaign_id"]), "u": str(attempt["collector_user_id"])}).mappings().first()
    if r is None:
        return "campaign_missing"
    if r["status"] != "running":
        return f"campaign_{r['status']}"
    if not r["active"]:
        return "member_inactive"
    if r["state"] not in ("ready", "wrap_up"):
        return "member_not_ready"
    if not r["lease_live"]:
        return "lease_expired"
    if _stops(session, attempt["case_id"]):
        return "status_stops_campaigns"             # 0080: a stopping status recorded after the dispatch
    return None


# --------------------------------------------------------------------------- members
def eligible_member(session, campaign: dict, *, user_id=None, lock=True, skip_locked=True) -> dict | None:
    """The next member to call for (longest idle first), locked FOR UPDATE [SKIP LOCKED]."""
    delay = int(campaign["wrapup_delay_seconds"])
    where_user = "AND r.user_id = CAST(:u AS uuid)" if user_id else ""
    lock_sql = ("FOR UPDATE OF r SKIP LOCKED" if skip_locked else "FOR UPDATE OF r") if lock else ""
    manual = campaign["next_mode"] == "manual"
    r = session.execute(text(f"""
        SELECT r.*, m.active
          FROM campaign_readiness r
          JOIN campaign_members m ON m.campaign_id = r.campaign_id AND m.user_id = r.user_id AND m.active
         WHERE r.campaign_id = CAST(:c AS uuid) {where_user}
           AND r.lease_until > clock_timestamp()
           AND (r.state = 'ready' OR (r.state = 'wrap_up' AND NOT :manual))
           AND (NOT :manual OR (r.next_requested_at IS NOT NULL
                                AND r.next_requested_at > clock_timestamp() - make_interval(secs => :next_ttl)))
           AND NOT (r.block_reason IS NOT NULL AND r.block_checked_at > clock_timestamp() - make_interval(secs => :backoff))
           AND NOT EXISTS (SELECT 1 FROM call_reservations x WHERE x.collector_user_id = r.user_id
                              AND x.released_at IS NULL)
           AND NOT EXISTS (SELECT 1 FROM call_attempts la WHERE la.id = r.last_attempt_id AND la.state = 'failed'
                              AND la.reason_code = ANY(CAST(:agent_codes AS text[])) AND la.ended_at >= r.state_since)
           AND NOT EXISTS (
                SELECT 1 FROM campaign_attempts ca JOIN call_reservations cr ON cr.attempt_id = ca.attempt_id
                 WHERE ca.campaign_id = r.campaign_id AND ca.user_id = r.user_id
                   AND ca.created_at = (SELECT max(ca2.created_at) FROM campaign_attempts ca2
                                         WHERE ca2.campaign_id = r.campaign_id AND ca2.user_id = r.user_id)
                   AND cr.released_at > clock_timestamp() - make_interval(secs => :delay)
                   AND (r.next_requested_at IS NULL OR r.next_requested_at < cr.released_at))
         ORDER BY r.last_dispatch_at ASC NULLS FIRST, r.user_id
         LIMIT 1 {lock_sql}"""),
        {"c": str(campaign["id"]), "u": str(user_id) if user_id else None, "manual": manual,
         "next_ttl": NEXT_REQUEST_TTL_SECONDS, "backoff": BLOCK_BACKOFF_SECONDS, "delay": delay,
         "agent_codes": list(AGENT_FAILURES)}).mappings().first()
    return dict(r) if r else None


def _member_precheck(user_id) -> tuple[str | None, dict]:
    """The member-level dispatch rules, once per dispatch (they are rerun inside authorize())."""
    from services.calling import extensions as X
    from services.calling import rules as R
    from services.calling import settings as S
    from services.calling import windows as W
    active, granted = R._permission(user_id, R.CALLS_MAKE)
    if not active:
        return "actor_inactive", {}
    if not granted:
        return "forbidden", {"required_any": [R.CALLS_MAKE]}
    if X.active_for_user(user_id) is None:
        return "no_extension", {}
    window = W.status(S.get_window_policy(), R.db_now())
    if not window["open"]:
        return window["reason"], {"window": {"open": False, "reason": window["reason"]}}
    return None, {}


def _block(session, campaign_id, user_id, reason, detail=None):
    import json
    session.execute(text("""
        UPDATE campaign_readiness SET block_reason = :r, block_detail = CAST(:d AS jsonb), block_checked_at = clock_timestamp()
         WHERE campaign_id = CAST(:c AS uuid) AND user_id = CAST(:u AS uuid)"""),
        {"r": reason, "d": json.dumps(detail or {}, default=str), "c": str(campaign_id), "u": str(user_id)})


def _exclude(session, campaign_id, case_id, reason, detail=None, minutes=None, until=None):
    import json
    minutes = RECHECK_MINUTES.get(reason, 5) if minutes is None else minutes
    session.execute(text("""
        INSERT INTO campaign_pool_state (campaign_id, case_id, excluded_reason, excluded_detail, excluded_at, recheck_after)
        VALUES (CAST(:c AS uuid), CAST(:k AS uuid), :r, CAST(:d AS jsonb), clock_timestamp(),
                coalesce(CAST(:until AS timestamptz), clock_timestamp() + make_interval(mins => :m)))
        ON CONFLICT (campaign_id, case_id) DO UPDATE
           SET excluded_reason = EXCLUDED.excluded_reason, excluded_detail = EXCLUDED.excluded_detail,
               excluded_at = EXCLUDED.excluded_at, recheck_after = EXCLUDED.recheck_after, updated_at = now()"""),
        {"c": str(campaign_id), "k": str(case_id), "r": reason, "d": json.dumps(detail or {}, default=str),
         "m": int(minutes), "until": until})


def _try_case_lock(session, case_id) -> bool:
    # the same key dispatch.authorize() takes (re-entrant within this transaction)
    return bool(session.execute(text("SELECT pg_try_advisory_xact_lock(hashtextextended(:k, 0))"),
                                {"k": f"calling_dispatch:case:{case_id}"}).scalar())


def dispatch_member(session, campaign: dict, member: dict, *, transport, dispatched_by: str,
                    audit_kw: dict | None = None) -> dict:
    """Dispatch the member's next case. The member row must be locked by the caller.
    -> {"attempt_id", "case_id", "contact_choice", "priority"} or {"blocked": reason, "detail"}."""
    from services.calling import dispatch as D
    from services.calling import rules as R
    uid = str(member["user_id"])
    cid = str(campaign["id"])
    code, detail = _member_precheck(uid)
    if code is None and transport() is None:
        code = "transport_not_connected"
    if code:
        _block(session, cid, uid, code, detail)
        return {"blocked": code, "detail": detail}
    clock = P._clock()
    tried = set()
    for cand in P.candidates(campaign, uid, limit=CANDIDATES_PER_DISPATCH, clock=clock):
        case_id = str(cand["case_id"])
        tried.add(case_id)
        if not _try_case_lock(session, case_id):
            continue                                       # another loop is on this case right now
        if _stops(session, case_id):                 # 0080: a stopping status recorded since the read
            _exclude(session, cid, case_id, "status_stops_campaigns")
            continue
        contact, choice = P.choose_contact(cand["idn"], campaign_id=cid, case_id=case_id)
        if contact is None:
            _exclude(session, cid, case_id, "no_callable_contact")
            continue
        priority = "callback_due" if cand["callback_due"] else "normal"
        try:
            with session.begin_nested():
                attempt_id = D.authorize(
                    uid, case_id, contact["id"], transport=transport, mode="campaign", campaign_id=cid,
                    audit_after={"campaign_id": cid, "contact_choice": choice, "priority": priority,
                                 "dispatched_by": dispatched_by, "contact_rotation": contact["rotation"]},
                    audit_kw=SYSTEM_AUDIT if audit_kw is None else audit_kw)
        except R.RuleRefused as refused:
            rc = refused.code
            if rc in MEMBER_LEVEL or (rc == "calling_reserved" and refused.detail.get("reserved") != "case"):
                _block(session, cid, uid, rc, refused.detail)
                return {"blocked": rc, "detail": refused.detail}
            if rc in ("not_found", "calling_reserved"):
                continue                                   # this member's scope / a call on the case now
            if rc == "callback_scheduled":
                _exclude(session, cid, case_id, rc, refused.detail, until=refused.detail.get("callable_from"))
            else:
                _exclude(session, cid, case_id, rc, refused.detail)
            continue
        except ApiError as e:
            if e.code == "calling_reserved":
                which = (e.error or {}).get("reserved")
                if which in ("collector", "extension"):
                    _block(session, cid, uid, "calling_reserved", {"reserved": which})
                    return {"blocked": "calling_reserved", "detail": {"reserved": which}}
                continue
            raise
        _record_dispatch(session, campaign, uid, attempt_id, case_id, contact["id"], choice, priority,
                         dispatched_by, audit_kw, rotation=contact["rotation"])
        return {"attempt_id": attempt_id, "case_id": case_id, "contact_choice": choice, "priority": priority,
                "contact_id": contact["id"], "contact_rotation": contact["rotation"]}
    _block(session, cid, uid, "no_eligible_case", {"candidates_checked": len(tried)})
    return {"blocked": "no_eligible_case", "detail": {"candidates_checked": len(tried)}}


def _record_dispatch(session, campaign, uid, attempt_id, case_id, contact_id, choice, priority, dispatched_by,
                     audit_kw, rotation=None):
    cid = str(campaign["id"])
    session.execute(text("""
        INSERT INTO campaign_attempts (attempt_id, campaign_id, user_id, case_id, contact_id, contact_choice,
                                       priority, dispatched_by, contact_position, contact_count)
        VALUES (CAST(:a AS uuid), CAST(:c AS uuid), CAST(:u AS uuid), CAST(:k AS uuid), CAST(:ct AS uuid),
                :ch, :pr, :by, :pos, :cnt)"""),
        {"a": attempt_id, "c": cid, "u": uid, "k": case_id, "ct": contact_id, "ch": choice, "pr": priority,
         "by": dispatched_by[:200], "pos": (rotation or {}).get("position"), "cnt": (rotation or {}).get("of")})
    day = _day(session)
    session.execute(text("""
        INSERT INTO campaign_pool_state (campaign_id, case_id, last_tried_at, last_attempt_id, attempts_day,
                                         attempts_today, total_attempts)
        VALUES (CAST(:c AS uuid), CAST(:k AS uuid), clock_timestamp(), CAST(:a AS uuid), :day, 1, 1)
        ON CONFLICT (campaign_id, case_id) DO UPDATE
           SET last_tried_at = EXCLUDED.last_tried_at, last_attempt_id = EXCLUDED.last_attempt_id,
               attempts_today = CASE WHEN campaign_pool_state.attempts_day = EXCLUDED.attempts_day
                                     THEN campaign_pool_state.attempts_today + 1 ELSE 1 END,
               attempts_day = EXCLUDED.attempts_day, total_attempts = campaign_pool_state.total_attempts + 1,
               excluded_reason = NULL, excluded_detail = NULL, excluded_at = NULL, recheck_after = NULL,
               updated_at = now()"""), {"c": cid, "k": case_id, "a": attempt_id, "day": day})
    session.execute(text("""
        UPDATE campaign_readiness
           SET last_dispatch_at = clock_timestamp(), last_attempt_id = CAST(:a AS uuid), next_requested_at = NULL,
               block_reason = NULL, block_detail = NULL, block_checked_at = clock_timestamp(),
               state_since = CASE WHEN state = 'ready' THEN state_since ELSE clock_timestamp() END,
               state_reason = CASE WHEN state = 'ready' THEN state_reason ELSE 'auto_ready_after_wrapup' END,
               state = 'ready'
         WHERE campaign_id = CAST(:c AS uuid) AND user_id = CAST(:u AS uuid)"""), {"a": attempt_id, "c": cid, "u": uid})
    kw = dict(SYSTEM_AUDIT if audit_kw is None else audit_kw)
    audit.record("campaign.dispatch", "campaign", cid, case_id=case_id, after={
        "attempt_id": attempt_id, "user_id": uid, "contact_choice": choice, "priority": priority,
        "dispatched_by": dispatched_by, "contact_id": contact_id, "contact_rotation": rotation},
        session=session, **kw)


# --------------------------------------------------------------------------- housekeeping
def outstanding_calls(session, campaign_id) -> int:
    return int(session.execute(text("""
        SELECT count(*) FROM call_attempts a
         WHERE a.campaign_id = CAST(:c AS uuid)
           AND (EXISTS (SELECT 1 FROM call_reservations r WHERE r.attempt_id = a.id AND r.released_at IS NULL)
                OR EXISTS (SELECT 1 FROM call_dispatch_queue q WHERE q.attempt_id = a.id
                              AND q.state IN ('queued', 'sending')))"""), {"c": str(campaign_id)}).scalar())


def finish_drain(session, campaign_id, *, audit_kw=None) -> bool:
    """draining -> stopped once every campaign call ended and was wrapped up (or released)."""
    if outstanding_calls(session, campaign_id):
        return False
    row = session.execute(text("""
        UPDATE campaigns SET status = 'stopped', stopped_at = now(), stop_reason = 'drained'
         WHERE id = CAST(:c AS uuid) AND status = 'draining' RETURNING id"""), {"c": str(campaign_id)}).first()
    if row is None:
        return False
    kw = dict(SYSTEM_AUDIT if audit_kw is None else audit_kw)
    audit.record("campaign.drained", "campaign", str(campaign_id), before={"status": "draining"},
                 after={"status": "stopped", "stop_reason": "drained"}, session=session, **kw)
    return True


def auto_pause_absent_agents(session, campaign_id) -> list[str]:
    """Members whose last campaign call failed because their own extension did not answer."""
    rows = session.execute(text("""
        UPDATE campaign_readiness r
           SET state = 'paused', state_since = clock_timestamp(), state_reason = 'agent_did_not_answer',
               next_requested_at = NULL
          FROM call_attempts a
         WHERE (r.campaign_id, r.user_id) IN (
                 SELECT r2.campaign_id, r2.user_id FROM campaign_readiness r2
                  WHERE r2.campaign_id = CAST(:c AS uuid) AND r2.state IN ('ready', 'wrap_up')
                  FOR UPDATE SKIP LOCKED)
           AND a.id = r.last_attempt_id AND a.state = 'failed' AND a.reason_code = ANY(CAST(:codes AS text[]))
           AND a.ended_at >= r.state_since
        RETURNING r.user_id, a.id AS attempt_id, a.reason_code"""),
        {"c": str(campaign_id), "codes": list(AGENT_FAILURES)}).mappings().all()
    for r in rows:
        audit.record("campaign.readiness.auto_paused", "campaign", str(campaign_id), after={
            "user_id": str(r["user_id"]), "state": "paused", "state_reason": "agent_did_not_answer",
            "attempt_id": str(r["attempt_id"]), "attempt_reason": r["reason_code"]},
            session=session, **SYSTEM_AUDIT)
    return [str(r["user_id"]) for r in rows]


def _fence_open(session, campaign_id) -> bool:
    return bool(session.execute(text("SELECT pg_try_advisory_xact_lock_shared(hashtextextended(:k, 0))"),
                                {"k": fence_key(campaign_id)}).scalar())


def load_campaign(session, campaign_id, *, lock: str | None = None) -> dict | None:
    suffix = {"share": " FOR SHARE", "update": " FOR UPDATE", None: ""}[lock]
    r = session.execute(text(f"SELECT * FROM campaigns WHERE id = CAST(:c AS uuid){suffix}"),
                        {"c": str(campaign_id)}).mappings().first()
    return dict(r) if r else None


def housekeeping(session, campaign_id, *, respect_fence=True) -> dict:
    if respect_fence and not _fence_open(session, campaign_id):
        return {"fenced": True}
    camp = load_campaign(session, campaign_id)
    if camp is None:
        return {}
    out = {"auto_paused": auto_pause_absent_agents(session, campaign_id)}
    if camp["status"] == "draining":
        out["drained"] = finish_drain(session, campaign_id)
    return out


def dispatch_one(session, campaign_id, *, transport, worker_id, respect_fence=True) -> dict | None:
    """One member of one campaign (one transaction). None = nobody to dispatch to."""
    if respect_fence and not _fence_open(session, campaign_id):
        return None
    camp = load_campaign(session, campaign_id, lock="share")
    if camp is None or camp["status"] != "running":
        return None
    member = eligible_member(session, camp)
    if member is None:
        return None
    out = dispatch_member(session, camp, member, transport=transport, dispatched_by=f"service:{worker_id}")
    out["user_id"] = str(member["user_id"])
    return out


# --------------------------------------------------------------------------- the service loop
class CampaignDispatcher(threading.Thread):
    """Progressive dispatch for every running campaign, in the calling service."""

    def __init__(self, app, transport, worker_id: str, *, poll: float = 0.5, respect_fence: bool = True,
                 max_per_tick: int = 200):
        super().__init__(name="campaigns", daemon=True)
        self.app, self.transport, self.worker_id = app, transport, worker_id
        self.poll, self.respect_fence, self.max_per_tick = poll, respect_fence, max_per_tick
        self.stopping = threading.Event()
        self.last_tick_at = None
        self.stats = {"ticks": 0, "dispatched": 0, "blocked": 0, "drained": 0, "auto_paused": 0, "errors": 0}

    def stop(self, timeout=3.0):
        self.stopping.set()
        if self.is_alive() and threading.current_thread() is not self:
            self.join(timeout)

    def run(self):
        failing_since = None
        while not self.stopping.is_set():
            try:
                self.run_once()
                if failing_since is not None:
                    log.info("campaign dispatcher recovered")
                failing_since = None
            except Exception:  # noqa: BLE001 - e.g. the database is down: retry, log once a minute
                self.stats["errors"] += 1
                now = time.monotonic()
                if failing_since is None or now - failing_since > 60:
                    log.exception("campaign dispatch cycle failed (retrying; logged once a minute)")
                    failing_since = now
                self.stopping.wait(1.0)
            self.stopping.wait(self.poll)

    def _tx(self, fn):
        from extensions import db
        with self.app.app_context():
            try:
                out = fn(db.session)
                db.session.commit()
                return out
            except Exception:
                db.session.rollback()
                raise
            finally:
                db.session.remove()

    def run_once(self) -> int:
        self.stats["ticks"] += 1
        self.last_tick_at = time.time()
        ids = self._tx(lambda s: [str(r[0]) for r in s.execute(text(
            "SELECT id FROM campaigns WHERE status IN ('running', 'draining') ORDER BY created_at, id")).all()])
        dispatched = 0
        for cid in ids:
            if self.stopping.is_set():
                break
            hk = self._tx(lambda s: housekeeping(s, cid, respect_fence=self.respect_fence))
            self.stats["auto_paused"] += len(hk.get("auto_paused") or [])
            if hk.get("drained"):
                self.stats["drained"] += 1
                log.info("campaign %s drained -> stopped", cid)
            if self.transport() is None:
                continue                              # never authorize a call without a connected PBX session
            for _ in range(self.max_per_tick):
                out = self._tx(lambda s: dispatch_one(s, cid, transport=self.transport, worker_id=self.worker_id,
                                                      respect_fence=self.respect_fence))
                if out is None:
                    break
                if out.get("attempt_id"):
                    dispatched += 1
                    self.stats["dispatched"] += 1
                    log.info("campaign %s: member %s -> attempt %s (%s, %s contact)", cid, out["user_id"],
                             out["attempt_id"], out["priority"], out["contact_choice"])
                else:
                    self.stats["blocked"] += 1
        return dispatched


def riyadh_day_start(session) -> _dt.datetime:
    return session.execute(text(
        "SELECT (date_trunc('day', clock_timestamp() AT TIME ZONE 'Asia/Riyadh')) AT TIME ZONE 'Asia/Riyadh'")).scalar()
