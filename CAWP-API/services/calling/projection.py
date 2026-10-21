"""Evidence -> attempt state projection (Phase 5 P1).

`derive()` is a pure function of an attempt's stored AMI evidence (call_events of the attempt's
own PBX epoch, ordered by the PBX `Timestamp`, never by arrival) plus its FastAGI decisions.
`project()` applies the result to call_attempts under the attempt row lock. The projection is
recomputed from the whole stored history on every new event, so the final result does not
depend on the order in which events arrived.

Facts and rules (docs/phase5-spec.md "Evidence, not inference"):

* the AGENT leg is the app-issued channel `cawp-<attempt>` (the Originate's ChannelId); on
  Asterisk <= 11 (CALLING_PBX_EVENTS=legacy, which ignores ChannelId) the leg bound by the
  Originate's ActionID or the FastAGI hook (call_legs role agent; services/calling/legs.py);
* a CUSTOMER leg is the destination of a DialBegin whose caller is the agent leg. Anything
  else linked to the attempt is `other`; a second customer leg or a bridge with more than
  the two legs is an ambiguous topology -> `uncertain`. Exception, sequential trunk failover
  (dialplan policy `trunks`): several customer legs are fine when they did not overlap in time
  and every earlier one ended unanswered with DialStatus CHANUNAVAIL or CONGESTION; the last one
  is the call's customer leg;
* legacy events (services/calling/legacy_events.py): a `Dial` End has no DestUniqueid, so it
  is the status of the agent's latest customer dial and never by itself an answer (the customer
  answered = its own Newstate Up); a `Bridge` Link is a two-member bridge "<uidA>|<uidB>", and a
  Link of a leg with a channel that is neither leg is an ambiguous topology (unless the call was
  handed over by call control);
* agent ringing / answered: agent Newstate 4-5 / 6 (or OriginateResponse Success);
* customer answered: the customer leg's Newstate 6 or DialEnd ANSWER for it;
* BRIDGED (a conversation) needs ALL of: the attempt passed the gate (an AGI `allow` or the
  trusted `UserEvent CollectionsCall State: authorized`), a customer leg dialled by the agent
  leg, the customer answered, and BOTH legs in the SAME bridge at the same time (BridgeEnter
  of both before either BridgeLeave/Hangup). Two answered legs alone are not a conversation;
* the call is over when the agent leg hung up and every customer leg hung up.
    - no customer leg and no AGI allow: `failed` (the customer was never dialled: the
      restricted dialplan dials only after an allow committed in the database). Its
      reservation is released with source `no_customer_contact` (the database guard re-checks).
    - a customer leg: `ended`, with the reason from the evidence (customer_busy,
      customer_no_answer, not_bridged, customer_hung_up, ...);
  hangup cause: the customer leg's (else the agent's); duration: the bridged time (from the
  bridge/answer to the first leave or hangup), NULL when not bridged or not fully observed.
* while an attempt is `uncertain`, facts (step timestamps) are still recorded but its state is
  changed only by reconciliation. After a wrap-up nothing on the attempt changes: new evidence
  is attached (call_events) and flagged `late_evidence` for review. A failed attempt that
  later shows a customer leg is flagged `evidence_conflict`.
* call control (2026-10-20, services/calling/call_control.py): the cawp-callctl dialplan reports
  `transfer_blind` / `transferred` / `customer_on_hold` / `conference` on the attempt. The
  conversation (`bridged`) is still the agent's Dial bridge with the customer. Once the agent's
  leg is gone and the customer was handed over (transferred, or left in a room with someone), the
  collector's attempt is `ended` without waiting for the customer leg: reason `transferred`
  (blind or attended transfer) or `agent_hung_up` (the agent left a conference), duration = the
  agent's time from the bridge to its hangup. Evidence after the wrap-up of such an attempt
  (the customer still talking to the target) is expected and not flagged.
"""
from __future__ import annotations

import dataclasses
import datetime as _dt
import decimal
import logging

from sqlalchemy import text

log = logging.getLogger("cawp.calling.projection")

ALLOWED = {
    "prepared": ("authorized", "cancelled", "failed", "uncertain"),
    "authorized": ("dispatched", "cancelled", "failed", "uncertain"),
    "dispatched": ("agent_ringing", "agent_answered", "ended", "failed", "uncertain"),
    "agent_ringing": ("agent_answered", "ended", "failed", "uncertain"),
    "agent_answered": ("customer_dialing", "ended", "failed", "uncertain"),
    "customer_dialing": ("customer_answered", "ended", "failed", "uncertain"),
    "customer_answered": ("bridged", "ended", "uncertain"),
    "bridged": ("ended", "uncertain"),
    "uncertain": ("ended", "failed"),
}
STEPS = ("dispatched", "agent_ringing", "agent_answered", "customer_dialing", "customer_answered", "bridged")
STEP_COLUMN = {"dispatched": "dispatched_at", "agent_ringing": "agent_ringing_at",
               "agent_answered": "agent_answered_at", "customer_dialing": "customer_dialing_at",
               "customer_answered": "customer_answered_at", "bridged": "bridged_at"}
TERMINAL = ("ended", "failed", "cancelled")
DIAL_STATUS_REASON = {"BUSY": "customer_busy", "NOANSWER": "customer_no_answer",
                      "CONGESTION": "customer_congestion", "CHANUNAVAIL": "customer_unavailable",
                      "CANCEL": "agent_hung_up_while_ringing", "ANSWER": "not_bridged"}
# Hangup cause of the AGENT leg when it never answered (Q.850)
AGENT_CAUSE_REASON = {17: "agent_busy", 18: "agent_no_answer", 19: "agent_no_answer", 21: "agent_rejected"}
# OriginateResponse Reason (Asterisk AST_CONTROL_*): 1 hangup, 3 ring timeout, 5 busy, 8 congestion
ORIGINATE_REASON = {1: "agent_rejected", 3: "agent_no_answer", 5: "agent_busy", 8: "agent_unavailable"}
# DialStatus of a customer dial after which the failover dialplan tries the next trunk
FAILOVER_STATUSES = frozenset({"CHANUNAVAIL", "CONGESTION"})
# CollectionsCall states of the call-control dialplan (services/calling/callctl_dialplan.py)
TRANSFER_STATES = frozenset({"transfer_blind", "transferred"})
HANDOVER_STATES = frozenset({"transfer_blind", "transferred", "customer_on_hold", "conference"})


def _num(v):
    if v is None:
        return None
    try:
        return decimal.Decimal(str(v))
    except (decimal.InvalidOperation, ValueError):
        return None


def _int(v):
    return int(v) if isinstance(v, str) and v.isdigit() and len(v) <= 9 else (v if isinstance(v, int) else None)


@dataclasses.dataclass
class Leg:
    uniqueid: str
    dial_ts: decimal.Decimal | None = None
    created: decimal.Decimal | None = None
    answered: decimal.Decimal | None = None
    hangup: decimal.Decimal | None = None
    cause: int | None = None
    hangup_request: decimal.Decimal | None = None              # this leg itself hung up (not a soft request)
    dial_status: str | None = None                              # the agent's DialEnd for this customer dial
    enters: list = dataclasses.field(default_factory=list)      # (bridge_id, ts, members)
    leaves: list = dataclasses.field(default_factory=list)      # (bridge_id, ts)


@dataclasses.dataclass
class Facts:
    agent: str
    first_ts: decimal.Decimal | None = None
    agent_leg: Leg | None = None
    agent_ringing: decimal.Decimal | None = None
    agent_answered: decimal.Decimal | None = None
    originate: tuple | None = None              # (response, reason int|None, ts)
    customer: dict = dataclasses.field(default_factory=dict)   # uniqueid -> Leg
    other: set = dataclasses.field(default_factory=set)
    other_bridges: set = dataclasses.field(default_factory=set)   # bridges entered by an `other` channel
    user_states: dict = dataclasses.field(default_factory=dict)  # state -> (ts, payload)
    dial_status: str | None = None
    agi_allow: bool = False
    agi_deny: str | None = None
    gate_passed: bool = False
    customer_dialing: decimal.Decimal | None = None
    customer_answered: decimal.Decimal | None = None
    bridged: decimal.Decimal | None = None
    bridge_id: str | None = None
    bridge_end: decimal.Decimal | None = None
    end_ts: decimal.Decimal | None = None
    terminal: str | None = None
    reason: str | None = None
    hangup_cause: int | None = None
    duration: int | None = None
    ambiguity: str | None = None

    @property
    def furthest(self) -> str:
        if self.bridged is not None:
            return "bridged"
        if self.customer_answered is not None:
            return "customer_answered"
        if self.customer_dialing is not None:
            return "customer_dialing"
        if self.agent_answered is not None:
            return "agent_answered"
        if self.agent_ringing is not None:
            return "agent_ringing"
        return "dispatched"

    def summary(self) -> dict:
        f = lambda v: None if v is None else format(v, "f")  # noqa: E731
        return {"furthest": self.furthest, "terminal": self.terminal, "reason": self.reason,
                "ambiguity": self.ambiguity, "customer_legs": len(self.customer), "gate_passed": self.gate_passed,
                "customer_answered": f(self.customer_answered), "bridged": f(self.bridged),
                "bridge_end": f(self.bridge_end), "end": f(self.end_ts), "dial_status": self.dial_status,
                "hangup_cause": self.hangup_cause, "duration": self.duration}


def _ts(e):
    ts = _num(e.get("pbx_ts"))
    if ts is None and e.get("received_at") is not None:          # value only; never used to order
        ts = decimal.Decimal(str(e["received_at"].timestamp()))
    return ts


def _order(e):
    ts = _num(e.get("pbx_ts"))
    return (ts is None, ts or 0, e.get("id") or 0)


def derive(attempt_id: str, events, agi=(), agent: str | None = None, *, agent_conflict: bool = False) -> Facts:
    """Pure: stored events (dicts: id, event, state, pbx_ts, uniqueid, linkedid, payload
    [, received_at]) + AGI decisions (dicts: decision, reason) -> Facts. `agent` is the agent
    leg's Uniqueid (default the app-issued `cawp-<attempt>`; Asterisk 11: the bound leg);
    `agent_conflict`: more than one agent leg was bound (ambiguous)."""
    agent = agent or f"cawp-{attempt_id}"
    facts = Facts(agent=agent)
    evs = sorted((dict(e) for e in events), key=_order)
    for e in evs:
        e["payload"] = e.get("payload") or {}
    # pass 1: which channels are customer legs (DialBegin from the agent leg), and which customer
    # dial each DialEnd of the agent leg closes (Asterisk 11: no DestUniqueid -> the latest open one)
    last_dial = None
    for e in evs:
        p = e["payload"]
        if e["event"] == "DialBegin" and e.get("uniqueid") == agent:
            dest = p.get("DestUniqueid")
            if dest and dest != agent and dest not in facts.customer:
                facts.customer[dest] = Leg(dest, dial_ts=_ts(e))
                last_dial = dest
        elif e["event"] == "DialEnd" and e.get("uniqueid") == agent:
            status = (p.get("DialStatus") or "").upper() or None
            dest = p.get("DestUniqueid") or last_dial
            if dest in facts.customer and facts.customer[dest].dial_status is None:
                facts.customer[dest].dial_status = status
            if not p.get("DestUniqueid"):
                last_dial = None
    facts.agent_leg = Leg(agent)
    legs = {agent: facts.agent_leg, **facts.customer}
    for d in agi:
        if d.get("decision") == "allow":
            facts.agi_allow = True
        elif d.get("decision") == "deny" and d.get("reason") != "allow_not_published":
            facts.agi_deny = facts.agi_deny or d.get("reason")
    # pass 2: facts in PBX-time order
    for e in evs:
        ev, p, uid, ts = e["event"], e["payload"], e.get("uniqueid"), _ts(e)
        if ts is not None and facts.first_ts is None:
            facts.first_ts = ts
        if uid and uid not in legs and ev in ("Newchannel", "Newstate", "Hangup", "BridgeEnter", "BridgeLeave"):
            facts.other.add(uid)
        leg = legs.get(uid)
        if ev == "Newchannel" and leg is not None:
            leg.created = leg.created or ts
        elif ev == "Newstate" and leg is not None:
            st = _int(p.get("ChannelState"))
            if leg is facts.agent_leg:
                if st in (4, 5) and facts.agent_ringing is None:
                    facts.agent_ringing = ts
                elif st == 6 and facts.agent_answered is None:
                    facts.agent_answered = ts
            elif st == 6 and leg.answered is None:
                leg.answered = ts
        elif ev == "DialEnd" and uid == agent:
            dest, status = p.get("DestUniqueid"), (p.get("DialStatus") or "").upper() or None
            if status:
                facts.dial_status = status
            # an explicit DestUniqueid only (Asterisk 12+): an Asterisk 11 Dial End names no leg
            if dest in facts.customer and status == "ANSWER" and facts.customer[dest].answered is None:
                facts.customer[dest].answered = ts
        elif ev == "BridgeEnter" and leg is not None:
            leg.enters.append((p.get("BridgeUniqueid"), ts, _int(p.get("BridgeNumChannels"))))
        elif ev == "BridgeEnter":
            facts.other.add(uid or "?")
            if p.get("BridgeUniqueid"):
                facts.other_bridges.add(p["BridgeUniqueid"])
        elif ev == "BridgeLeave" and leg is not None:
            leg.leaves.append((p.get("BridgeUniqueid"), ts))
        elif ev == "HangupRequest" and leg is not None:
            if leg.hangup_request is None:
                leg.hangup_request = ts
        elif ev == "Hangup" and leg is not None:
            if leg.hangup is None:
                leg.hangup, leg.cause = ts, _int(p.get("Cause"))
        elif ev == "OriginateResponse" and p.get("correlated_by") == "action_id":
            # Asterisk 11: attributed by the Originate's ActionID (legacy_correlation.py); a failure
            # names no channel (Uniqueid <null>)
            if (p.get("binding") or "").startswith("refused:"):
                facts.ambiguity = facts.ambiguity or p["binding"][len("refused:"):]
            elif p.get("Response") == "Success" and uid != agent:
                facts.ambiguity = facts.ambiguity or "agent_leg_conflict"
            elif facts.originate is None:
                facts.originate = (p.get("Response"), _int(p.get("Reason")), ts)
                if p.get("Response") == "Success" and facts.agent_answered is None:
                    facts.agent_answered = ts
        elif ev == "OriginateResponse" and uid == agent and facts.originate is None:
            facts.originate = (p.get("Response"), _int(p.get("Reason")), ts)
            if p.get("Response") == "Success" and facts.agent_answered is None:
                facts.agent_answered = ts
        elif ev == "UserEvent" and e.get("state"):
            state = e["state"]
            facts.user_states.setdefault(state, (ts, p))
            if state == "agent_answered" and facts.agent_answered is None:
                facts.agent_answered = ts
            if state in ("dial_end", "agent_ended") and p.get("DialStatus"):
                facts.dial_status = facts.dial_status or p["DialStatus"].upper()
            if state == "customer_ended" and uid in facts.customer and facts.customer[uid].hangup is None:
                facts.customer[uid].hangup = ts
                facts.customer[uid].cause = _int(p.get("Cause"))
            if state == "agent_ended" and facts.agent_leg.hangup is None:
                facts.agent_leg.hangup = ts
                facts.agent_leg.cause = _int(p.get("Cause"))
    facts.gate_passed = facts.agi_allow or "authorized" in facts.user_states
    if agent_conflict:
        facts.ambiguity = facts.ambiguity or "agent_leg_conflict"
    _conclude(facts)
    return facts


def _customer_leg(f: Facts):
    """The call's customer leg: the only one, or the last of a proven sequential failover
    (every earlier dial ended unanswered with CHANUNAVAIL/CONGESTION before the next began).
    None = several legs that are not such a sequence (ambiguous)."""
    legs = list(f.customer.values())
    if len(legs) == 1:
        return legs[0]
    if any(leg.dial_ts is None for leg in legs):
        return None
    legs.sort(key=lambda leg: leg.dial_ts)
    for earlier, nxt in zip(legs, legs[1:]):
        if earlier.answered is not None or earlier.dial_status not in FAILOVER_STATUSES:
            return None
        if earlier.hangup is None or earlier.hangup > nxt.dial_ts or earlier.enters:
            return None
    return legs[-1]


def _foreign_legacy_bridge(f: Facts, c: Leg | None) -> bool:
    """An Asterisk 11 two-party bridge ("<uidA>|<uidB>") of the agent or customer leg with a
    channel that is neither leg (call control's hand-over excepted)."""
    if HANDOVER_STATES & set(f.user_states):
        return False
    mine = {bid for bid, _t, _m in f.agent_leg.enters} | ({bid for bid, _t, _m in c.enters} if c else set())
    return any(bid and "|" in bid and bid in f.other_bridges for bid in mine)


def _bridge_overlap(a: Leg, c: Leg):
    """-> (bridge_id, start, end|None, members_max) of the first time both legs were in one bridge."""
    def intervals(leg):
        out = []
        for bid, t, members in leg.enters:
            leave = min((lt for lb, lt in leg.leaves if lb == bid and lt is not None and (t is None or lt >= t)),
                        default=None)
            end = min([x for x in (leave, leg.hangup) if x is not None], default=None)
            out.append((bid, t, end, members or 0))
        return out
    best = None
    for bid, at, aend, am in intervals(a):
        for cbid, ct, cend, cm in intervals(c):
            if bid is None or bid != cbid or at is None or ct is None:
                continue
            start = max(at, ct)
            ends = [x for x in (aend, cend) if x is not None]
            end = min(ends) if ends else None
            if end is not None and end <= start:
                continue
            cand = (bid, start, end, max(am, cm))
            if best is None or start < best[1]:
                best = cand
    return best


def _first_to_hang_up(c: Leg, a: Leg) -> str:
    """'c' or 'a': the PBX reports a HangupRequest for the leg that hung up itself (the other leg
    only gets a SoftHangupRequest from the bridge); then the first to leave the bridge; then the
    first hangup."""
    for pick in (lambda leg: leg.hangup_request,
                 lambda leg: min((t for _b, t in leg.leaves if t is not None), default=None),
                 lambda leg: leg.hangup):
        tc, ta = pick(c), pick(a)
        if tc is not None and (ta is None or tc < ta):
            return "c"
        if ta is not None and (tc is None or ta < tc):
            return "a"
    return "c"


def _conclude(f: Facts) -> None:
    a = f.agent_leg
    c = _customer_leg(f) if f.customer else None
    if f.customer and c is None:
        f.ambiguity = "multiple_customer_legs"
        c = next(iter(f.customer.values()))
    if _foreign_legacy_bridge(f, c):
        f.ambiguity = f.ambiguity or "bridge_topology_ambiguous"
    if f.customer:
        dials = [leg.dial_ts for leg in f.customer.values() if leg.dial_ts is not None]
        f.customer_dialing = min(dials) if len(f.customer) > 1 and dials else c.dial_ts
        f.customer_answered = c.answered
        overlap = _bridge_overlap(a, c)
        if overlap is not None:
            bid, start, end, members = overlap
            if members > 2 or bid in f.other_bridges:
                f.ambiguity = f.ambiguity or "bridge_topology_ambiguous"
            elif f.gate_passed and c.answered is not None and (end is None or c.answered < end):
                f.bridged = max(start, c.answered)
                f.bridge_id = bid
                f.bridge_end = end
    elif "customer_dialing" in f.user_states and f.gate_passed:
        f.customer_dialing = f.user_states["customer_dialing"][0]

    agent_gone = a.hangup is not None
    if f.ambiguity:
        return
    if not f.customer:
        failed_originate = f.originate is not None and f.originate[0] == "Failure"
        if failed_originate and f.agent_answered is None:
            f.terminal = "failed"
            f.reason = (AGENT_CAUSE_REASON.get(a.cause) if a.cause is not None else None) \
                or ORIGINATE_REASON.get(f.originate[1], "agent_unavailable")
            f.end_ts = a.hangup or f.originate[2]
            f.hangup_cause = a.cause
            return
        if not agent_gone:
            return
        f.end_ts = a.hangup
        f.hangup_cause = a.cause
        if "denied" in f.user_states or (f.agi_deny and not f.agi_allow):
            f.terminal, f.reason = "failed", "authorization_denied"
        elif f.gate_passed:
            # The gate allowed a customer dial. Without a customer leg the call is over only when
            # the agent's own hangup handler says no Dial ran (empty DIALSTATUS, no customer_dialing).
            # Otherwise wait: more evidence may still arrive (arrival order is not PBX order); if
            # none does, the stale-evidence check makes the attempt uncertain for reconciliation.
            ended = f.user_states.get("agent_ended")
            if "customer_dialing" in f.user_states or ended is None or (ended[1] or {}).get("DialStatus"):
                f.end_ts = f.hangup_cause = None
                return
            f.terminal, f.reason = "failed", "agent_hung_up_before_dial"
        elif f.agent_answered is not None:
            f.terminal, f.reason = "failed", "agent_hung_up"
        else:
            f.terminal = "failed"
            f.reason = AGENT_CAUSE_REASON.get(a.cause, "agent_unavailable")
        return
    handed_over = bool(HANDOVER_STATES & set(f.user_states)) and f.bridged is not None
    if handed_over and agent_gone and (c.hangup is None or a.hangup <= c.hangup):
        # call control: the collector's part ends with the agent's leg (the customer may talk on)
        f.terminal = "ended"
        f.end_ts = a.hangup
        f.hangup_cause = a.cause
        f.reason = "transferred" if TRANSFER_STATES & set(f.user_states) else "agent_hung_up"
        f.duration = max(0, int((a.hangup - f.bridged).to_integral_value(rounding=decimal.ROUND_HALF_UP)))
        return
    if not agent_gone or c.hangup is None:
        return
    f.terminal = "ended"
    f.end_ts = max(x for x in (a.hangup, c.hangup) if x is not None)
    f.hangup_cause = c.cause if c.cause is not None else a.cause
    if f.bridged is not None:
        f.reason = "customer_hung_up" if _first_to_hang_up(c, a) == "c" else "agent_hung_up"
        # after call control the conversation went on outside the first bridge (room / hold)
        end = min(a.hangup, c.hangup) if handed_over else (f.bridge_end or min(a.hangup, c.hangup))
        f.duration = max(0, int((end - f.bridged).to_integral_value(rounding=decimal.ROUND_HALF_UP)))
    elif c.answered is not None:
        f.reason = "not_bridged"
    else:
        f.reason = DIAL_STATUS_REASON.get(f.dial_status or "", "customer_not_answered")
        if f.reason == "not_bridged":                 # DialStatus ANSWER without an answer fact
            f.reason = "customer_answer_not_observed"


# --------------------------------------------------------------------------- database side
def _when(ts):
    return None if ts is None else _dt.datetime.fromtimestamp(float(ts), tz=_dt.timezone.utc)


def load(session, attempt_id, epoch):
    events = session.execute(text("""
        SELECT id, event, state, pbx_ts, uniqueid, linkedid, payload, received_at
          FROM call_events WHERE attempt_id = CAST(:id AS uuid) AND pbx_epoch = :ep AND source = 'ami'
         ORDER BY pbx_ts NULLS LAST, id"""), {"id": str(attempt_id), "ep": epoch}).mappings().all()
    agi = session.execute(text("""
        SELECT decision, reason FROM call_agi_decisions WHERE attempt_id = CAST(:id AS uuid) ORDER BY id"""),
        {"id": str(attempt_id)}).mappings().all()
    return [dict(e) for e in events], [dict(d) for d in agi]


def facts_for(session, attempt_id, epoch) -> Facts:
    from services.calling.legs import agent_uids
    events, agi = load(session, attempt_id, epoch)
    agents = agent_uids(session, attempt_id, epoch)
    return derive(str(attempt_id), events, agi, agents[0] if agents else None, agent_conflict=len(agents) > 1)


def walk(src: str, dst: str) -> list[str] | None:
    """Shortest forward path of allowed transitions from src to dst (excluding src)."""
    if src == dst:
        return []
    frontier, seen = [(src, [])], {src}
    while frontier:
        nxt = []
        for state, path in frontier:
            for n in ALLOWED.get(state, ()):
                if n in seen:
                    continue
                if n == dst:
                    return path + [n]
                # only walk through the forward business steps
                if n in STEPS:
                    seen.add(n)
                    nxt.append((n, path + [n]))
        frontier = nxt
    return None


def flag(session, attempt, kind: str, reason: str, *, detail=None, event_id=None, blocks_calling=False) -> bool:
    import json
    row = session.execute(text("""
        INSERT INTO call_review_flags (attempt_id, case_id, kind, reason, detail, event_id, blocks_calling)
        VALUES (CAST(:a AS uuid), CAST(:c AS uuid), :kind, :reason, CAST(:detail AS jsonb), :eid, :blocks)
        ON CONFLICT (attempt_id, kind) WHERE resolved_at IS NULL DO NOTHING
        RETURNING id"""), {"a": str(attempt["id"]), "c": str(attempt["case_id"]), "kind": kind, "reason": reason,
                           "detail": json.dumps(detail or {}, default=str, sort_keys=True), "eid": event_id,
                           "blocks": blocks_calling}).first()
    return row is not None


def _audit(session, attempt, action, after):
    from core import audit
    audit.record(action, "call_attempt", str(attempt["id"]), case_id=str(attempt["case_id"]), after=after,
                 actor_user_id=None, request_id=None, idempotency_key=None, source="calling", session=session)


def release_no_customer_contact(session, attempt) -> bool:
    """Release the reservation of a failed attempt that provably never reached the customer.
    The database guard re-checks (no AGI allow, no customer leg, no customer dialing)."""
    try:
        with session.begin_nested():
            row = session.execute(text("""
                UPDATE call_reservations
                   SET released_at = now(), release_source = 'no_customer_contact',
                       release_reason = :why
                 WHERE attempt_id = CAST(:id AS uuid) AND released_at IS NULL RETURNING id"""),
                {"id": str(attempt["id"]), "why": f"failed before any customer contact ({attempt.get('reason_code')})"}
            ).first()
    except Exception:  # noqa: BLE001 - the guard refused: keep the reservation
        log.info("reservation of %s kept (no_customer_contact not provable)", attempt["id"])
        return False
    return row is not None


def _update(session, attempt_id, sets: dict, state: str | None):
    parts, params = [], {"id": str(attempt_id)}
    for k, v in sets.items():
        parts.append(f"{k} = :{k}")
        params[k] = v
    if state is not None:
        parts.append("state = :state")
        params["state"] = state
    if not parts:
        return
    session.execute(text(f"UPDATE call_attempts SET {', '.join(parts)} WHERE id = CAST(:id AS uuid)"), params)


def project(session, attempt_id, *, trigger_event_id=None) -> dict | None:
    """Apply the evidence of `attempt_id` to its row (lock held until the caller commits)."""
    a = session.execute(text("SELECT * FROM call_attempts WHERE id = CAST(:id AS uuid) FOR UPDATE"),
                        {"id": str(attempt_id)}).mappings().first()
    if a is None or a["mode"] == "manual" or a["pbx_epoch"] is None:
        return None
    a = dict(a)
    facts = facts_for(session, attempt_id, a["pbx_epoch"])
    out = {"attempt_id": str(attempt_id), "from": a["state"], "to": a["state"], "facts": facts.summary(),
           "flagged": None, "released": False}
    has_evidence = facts.first_ts is not None or facts.originate is not None or facts.user_states
    if a["wrapped_up_at"] is not None:
        if HANDOVER_STATES & set(facts.user_states) and facts.terminal == "ended":
            return out                                   # the handed-over customer talks on: expected
        if trigger_event_id is not None and flag(session, a, "late_evidence", "evidence_after_wrapup",
                                                 detail={"facts": facts.summary(), "state": a["state"]},
                                                 event_id=trigger_event_id):
            out["flagged"] = "late_evidence"
            _audit(session, a, "call.evidence.late", {"event_id": trigger_event_id, "state": a["state"]})
        return out
    if not has_evidence and a["wrapped_up_at"] is None:
        return out                                       # nothing observed on the attempt's own epoch
    if a["state"] in ("prepared", "cancelled"):
        if trigger_event_id is not None and a["state"] == "cancelled":
            flag(session, a, "evidence_conflict", "evidence_after_cancel", event_id=trigger_event_id)
            out["flagged"] = "evidence_conflict"
        return out

    sets = {}
    first = _when(facts.first_ts)
    if a["dispatched_at"] is None and first is not None:
        sets["dispatched_at"] = first
    if a["started_at"] is None and (a["dispatched_at"] or first) is not None:
        sets["started_at"] = a["dispatched_at"] or first
    for step, ts in (("agent_ringing", facts.agent_ringing), ("agent_answered", facts.agent_answered),
                     ("customer_dialing", facts.customer_dialing), ("customer_answered", facts.customer_answered),
                     ("bridged", facts.bridged)):
        col = STEP_COLUMN[step]
        if ts is not None and a[col] is None:
            sets[col] = _when(ts)

    if a["state"] == "failed":
        if facts.customer or facts.agi_allow:
            if flag(session, a, "evidence_conflict", "customer_evidence_after_failure",
                    detail={"facts": facts.summary()}, event_id=trigger_event_id):
                out["flagged"] = "evidence_conflict"
        elif facts.terminal == "failed":                  # later evidence completes the facts
            fix = {}
            if _refinable(a["reason_code"], facts.reason):
                fix["reason_code"] = facts.reason
            if a["hangup_cause"] is None and facts.hangup_cause is not None:
                fix["hangup_cause"] = facts.hangup_cause
            if a["ended_at"] is None and facts.end_ts is not None:
                fix["ended_at"] = _when(facts.end_ts)
            _update(session, attempt_id, {**sets, **fix}, None)
        return out
    if a["state"] == "ended":
        if facts.terminal not in (None, "ended") or facts.ambiguity:
            if flag(session, a, "evidence_conflict", "evidence_contradicts_end",
                    detail={"facts": facts.summary()}, event_id=trigger_event_id):
                out["flagged"] = "evidence_conflict"
            return out
        if facts.terminal == "ended":                     # out-of-order evidence completes the facts
            if a["hangup_cause"] is None and facts.hangup_cause is not None:
                sets["hangup_cause"] = facts.hangup_cause
            if a["duration_seconds"] is None and facts.duration is not None:
                sets.update(duration_seconds=facts.duration, duration_source="pbx")
            if _refinable(a["reason_code"], facts.reason):
                sets["reason_code"] = facts.reason
            if a["ended_at"] is None and facts.end_ts is not None:
                sets["ended_at"] = _when(facts.end_ts)
        _update(session, attempt_id, sets, None)
        return out
    if a["state"] == "uncertain":
        _update(session, attempt_id, sets, None)          # facts only; reconciliation decides
        return out

    # a live attempt: advance
    if facts.ambiguity:
        target, reason = "uncertain", facts.ambiguity
    elif facts.terminal:
        target, reason = facts.terminal, facts.reason
    else:
        target, reason = facts.furthest, None
    current = STEPS.index(a["state"]) if a["state"] in STEPS else -1
    if target in STEPS and STEPS.index(target) <= current:
        _update(session, attempt_id, sets, None)
        return out
    path = walk(a["state"], target)
    if path is None and target == "failed":           # past customer_answered a failure is an end
        target = "ended"
        path = walk(a["state"], target)
    if path is None:
        log.warning("attempt %s: no transition %s -> %s", attempt_id, a["state"], target)
        _update(session, attempt_id, sets, None)
        return out
    now = _dt.datetime.now(_dt.timezone.utc)
    for step in path:                                  # every step reached gets its (set-once) time
        col = STEP_COLUMN.get(step)
        if col and a.get(col) is None and col not in sets:
            sets[col] = now
    final = {}
    if target in ("ended", "failed", "uncertain"):
        final["reason_code"] = reason
    if target in ("ended", "failed"):
        final["ended_at"] = _when(facts.end_ts) or now
        if facts.hangup_cause is not None and a["hangup_cause"] is None:
            final["hangup_cause"] = facts.hangup_cause
        if facts.duration is not None and a["duration_seconds"] is None:
            final.update(duration_seconds=facts.duration, duration_source="pbx")
    _update(session, attempt_id, sets, None)
    for i, state in enumerate(path):
        _update(session, attempt_id, final if i == len(path) - 1 else {}, state)
    out["to"] = target
    a["state"], a["reason_code"] = target, reason
    if target in ("ended", "failed", "uncertain"):
        _audit(session, a, f"call.{target}", {"reason_code": reason, "facts": facts.summary()})
    if target == "failed" and not facts.customer and not facts.agi_allow:
        out["released"] = release_no_customer_contact(session, a)
    return out


PROJECTION_REASONS = frozenset({
    *DIAL_STATUS_REASON.values(), *AGENT_CAUSE_REASON.values(), *ORIGINATE_REASON.values(),
    "agent_unavailable", "authorization_denied", "agent_hung_up_before_dial", "agent_hung_up",
    "customer_hung_up", "customer_not_answered", "customer_answer_not_observed", "transferred"})


def _refinable(current, new) -> bool:
    """A reason the projection itself set may be refined by later (out-of-order) evidence; a
    reconciliation or uncertainty reason is never overwritten."""
    return bool(new) and new != current and (current is None or current in PROJECTION_REASONS)
