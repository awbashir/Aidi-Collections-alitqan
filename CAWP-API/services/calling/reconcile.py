"""Uncertainty and reconciliation (Phase 5 P1). Runs in the calling service (the AMI owner),
on its own thread (never on the event consumer thread: the channel inventory waits on it).

Every pass (CALLING_RECONCILE_SECONDS, default 10 s):

1. **Catch-up.** In-flight and uncertain attempts of the current PBX epoch are re-projected
   from their stored evidence (a projection that failed next to a stored event is retried).
2. **Missing events -> uncertain.** A dispatch row stuck in `sending` for 60 s becomes
   `unacknowledged` and its attempt `uncertain` (`dispatch_outcome_unknown`). An in-flight
   attempt with no evidence at all `agent_ring + 30` s after its dispatch
   (`evidence_missing`), or none for `max_call + agent_ring + 60` s (`evidence_stale`),
   becomes `uncertain`. Reservations are never touched.
3. **Reconcile uncertain attempts.** 50 per pass, with a rotating cursor (by id) so attempts that
   stay uncertain never starve newer ones. For each:
   * attempts of the CURRENT epoch are checked against one `CoreShowChannels` inventory: any
     channel related to the attempt (its agent channel id, a channel linked to it, a known
     leg) means the call may still be live -> kept uncertain (`channels_present`). Evidence
     younger than 5 s -> wait (settle). A complete list is not an atomic snapshot, so an
     empty one only says "nothing of this call is visible now".
   * attempts of ANOTHER epoch: that PBX process is gone, and every channel with it.
   Then the stored evidence and the CDR (by attempt id, once the call is over) decide:
       bridge evidence (both legs in one bridge)             -> ended   (bridged_then_lost /
                                                                         evidence_complete)
       no AGI allow and no customer leg                      -> failed  (no_customer_contact;
                                                                         reservation released,
                                                                         the DB guard re-checks)
       an ambiguous topology                                 -> kept    (ambiguous_topology) + flag
       CDR: the customer answered, no bridge evidence        -> kept    (conversation_unproven) + flag
       CDR: the customer was dialled, did not answer         -> ended   (cdr_not_answered)
       complete terminal evidence (dial status, hangups)      -> ended   (evidence_complete)
       otherwise (no CDR yet / none)                         -> kept    (evidence_incomplete); a
                                                                review flag after 5 minutes
   A decision is appended to call_reconciliations (a kept decision only when it changes).
   Reconciliation never releases a reservation except the proven no-customer-contact case;
   an ended attempt is wrapped up by the collector (wrap-up releases), and a supervisor can
   resolve anything that stays uncertain (POST /v2/calls/<id>/resolve, reason required).
"""
from __future__ import annotations

import datetime as _dt
import json
import logging
import threading

from sqlalchemy import text

from services.calling import projection as P

log = logging.getLogger("cawp.calling.reconcile")

IN_FLIGHT = ("authorized", "dispatched", "agent_ringing", "agent_answered", "customer_dialing",
             "customer_answered", "bridged")
SETTLE_SECONDS = 5
UNRESOLVED_FLAG_AFTER = _dt.timedelta(minutes=5)
NEEDS_HUMAN = ("ambiguous_topology", "conversation_unproven")
ZERO_UUID = "00000000-0000-0000-0000-000000000000"


def _audit(session, attempt, action, after, reason=None, actor=None):
    from core import audit
    audit.record(action, "call_attempt", str(attempt["id"]), case_id=str(attempt["case_id"]), after=after,
                 reason=reason, actor_user_id=actor, request_id=None, idempotency_key=None,
                 source="calling" if actor is None else "api", session=session)


# --------------------------------------------------------------------------- 1 + 2
def catch_up(session, epoch: str | None) -> int:
    if epoch is None:
        return 0
    ids = [r[0] for r in session.execute(text("""
        SELECT id FROM call_attempts
         WHERE pbx_epoch = :ep AND mode <> 'manual' AND wrapped_up_at IS NULL
           AND state = ANY(CAST(:st AS text[]))
         ORDER BY created_at LIMIT 200"""), {"ep": epoch, "st": list(IN_FLIGHT) + ["uncertain"]})]
    for aid in ids:
        try:
            with session.begin_nested():
                P.project(session, aid)
        except Exception:  # noqa: BLE001
            log.exception("re-projection of %s failed", aid)
    return len(ids)


def mark_missing(session, epoch: str | None) -> list[str]:
    changed = []
    stuck = session.execute(text("""
        SELECT q.id, q.attempt_id FROM call_dispatch_queue q
         WHERE q.state = 'sending' AND q.claimed_at < now() - interval '60 seconds'
         FOR UPDATE SKIP LOCKED""")).mappings().all()
    for q in stuck:
        session.execute(text("""UPDATE call_dispatch_queue SET state = 'unacknowledged', finished_at = now(),
                                       result_code = 'dispatch_outcome_unknown' WHERE id = CAST(:id AS uuid)"""),
                        {"id": str(q["id"])})
        row = session.execute(text("""
            UPDATE call_attempts SET state = 'uncertain', reason_code = 'dispatch_outcome_unknown'
             WHERE id = CAST(:id AS uuid) AND state = ANY(CAST(:st AS text[])) RETURNING id, case_id"""),
            {"id": str(q["attempt_id"]), "st": list(IN_FLIGHT)}).mappings().first()
        if row:
            changed.append(str(row["id"]))
            _audit(session, row, "call.uncertain", {"reason_code": "dispatch_outcome_unknown"})
    if epoch is None:
        return changed
    rows = session.execute(text("""
        SELECT a.id, a.case_id, a.dispatched_at, q.agent_ring_seconds, q.max_call_seconds,
               (SELECT max(e.received_at) FROM call_events e WHERE e.attempt_id = a.id AND e.source = 'ami') AS last_ev
          FROM call_attempts a JOIN call_dispatch_queue q ON q.attempt_id = a.id
         WHERE a.pbx_epoch = :ep AND a.state = ANY(CAST(:st AS text[])) AND a.dispatched_at IS NOT NULL
           AND a.state <> 'authorized'
         FOR UPDATE OF a SKIP LOCKED"""), {"ep": epoch, "st": list(IN_FLIGHT)}).mappings().all()
    now = session.execute(text("SELECT now()")).scalar()
    for r in rows:
        reason = None
        if r["last_ev"] is None and now - r["dispatched_at"] > _dt.timedelta(seconds=r["agent_ring_seconds"] + 30):
            reason = "evidence_missing"
        elif r["last_ev"] is not None and now - r["last_ev"] > _dt.timedelta(
                seconds=r["max_call_seconds"] + r["agent_ring_seconds"] + 60):
            reason = "evidence_stale"
        if reason:
            session.execute(text("UPDATE call_attempts SET state = 'uncertain', reason_code = :r "
                                 "WHERE id = CAST(:id AS uuid)"), {"r": reason, "id": str(r["id"])})
            changed.append(str(r["id"]))
            _audit(session, r, "call.uncertain", {"reason_code": reason})
    return changed


# --------------------------------------------------------------------------- 3
def related_channels(attempt_id, legs: set, inventory) -> list[dict]:
    agent = f"cawp-{attempt_id}"
    out = []
    for f in inventory or ():
        uid, lid = f.get("Uniqueid"), f.get("Linkedid")
        if uid == agent or lid == agent or uid in legs:
            out.append({"uniqueid": uid, "state": f.get("ChannelStateDesc") or f.get("ChannelState"),
                        "bridge": bool(f.get("BridgeId"))})
    return out


def decide(attempt: dict, facts: P.Facts, cdr: dict | None) -> tuple[str, str]:
    """Pure: (decision, code) for an uncertain attempt whose channels are gone."""
    if facts.ambiguity:
        return "kept_uncertain", "ambiguous_topology"
    if attempt.get("bridged_at") is not None or facts.bridged is not None:
        return "ended", ("evidence_complete" if facts.terminal == "ended" else "bridged_then_lost")
    if not facts.agi_allow and not facts.customer and "authorized" not in facts.user_states:
        return "failed", "no_customer_contact"
    if cdr and cdr.get("available") and cdr.get("found"):
        if cdr.get("customer_answered"):
            return "kept_uncertain", "conversation_unproven"
        if cdr.get("customer_dialed"):
            return "ended", "cdr_not_answered"
    if facts.customer_answered is not None:
        return "kept_uncertain", "conversation_unproven"
    if facts.terminal == "ended" and facts.dial_status and facts.dial_status != "ANSWER":
        return "ended", "evidence_complete"
    if facts.terminal == "failed" and "denied" in facts.user_states:
        return "failed", "authorization_denied"
    return "kept_uncertain", "evidence_incomplete"


def _last(session, attempt_id):
    return session.execute(text("""
        SELECT decision, code FROM call_reconciliations WHERE attempt_id = CAST(:a AS uuid) ORDER BY id DESC LIMIT 1"""),
        {"a": str(attempt_id)}).mappings().first()


def record(session, attempt, decision, code, *, source="service", actor=None, reason=None, inventory=None,
           cdr=None, evidence=None, released=False) -> int:
    return session.execute(text("""
        INSERT INTO call_reconciliations (attempt_id, source, actor_user_id, prior_state, prior_reason, decision, code,
                                          reason, inventory, cdr, evidence, reservation_released)
        VALUES (CAST(:a AS uuid), :src, CAST(:actor AS uuid), :ps, :pr, :d, :c, :why, CAST(:inv AS jsonb),
                CAST(:cdr AS jsonb), CAST(:ev AS jsonb), :rel)
        RETURNING id"""), {"a": str(attempt["id"]), "src": source, "actor": actor, "ps": attempt["state"],
                           "pr": attempt.get("reason_code"), "d": decision, "c": code, "why": reason,
                           "inv": json.dumps(inventory, default=str) if inventory is not None else None,
                           "cdr": json.dumps(cdr, default=str) if cdr is not None else None,
                           "ev": json.dumps(evidence, default=str) if evidence is not None else None,
                           "rel": released}).scalar()


def resolve_to(session, attempt: dict, facts: P.Facts, decision: str, cdr: dict | None) -> dict:
    """uncertain -> ended|failed with the facts that are known (unknown stays NULL)."""
    sets = {"state": decision}
    if attempt.get("ended_at") is None and facts.end_ts is not None:
        sets["ended_at"] = P._when(facts.end_ts)
    if attempt.get("hangup_cause") is None and facts.hangup_cause is not None:
        sets["hangup_cause"] = facts.hangup_cause
    if attempt.get("duration_seconds") is None:
        if facts.duration is not None:
            sets.update(duration_seconds=facts.duration, duration_source="pbx")
        elif decision == "ended" and (attempt.get("bridged_at") or facts.bridged) and cdr and cdr.get("customer_answered") \
                and isinstance(cdr.get("billsec"), int):
            sets.update(duration_seconds=cdr["billsec"], duration_source="pbx")
    cols = ", ".join(f"{k} = :{k}" for k in sets)
    session.execute(text(f"UPDATE call_attempts SET {cols} WHERE id = CAST(:id AS uuid)"),
                    {**sets, "id": str(attempt["id"])})
    return sets


def reconcile_attempt(session, attempt_id, ctx, inventory, cdr_source, *, force_code=None) -> dict | None:
    a = session.execute(text("SELECT * FROM call_attempts WHERE id = CAST(:id AS uuid) FOR UPDATE SKIP LOCKED"),
                        {"id": str(attempt_id)}).mappings().first()
    if a is None or a["state"] != "uncertain" or a["pbx_epoch"] is None:
        return None
    a = dict(a)
    facts = P.facts_for(session, a["id"], a["pbx_epoch"])
    same_epoch = ctx is not None and a["pbx_epoch"] == ctx.epoch
    legs = {r[0] for r in session.execute(text("SELECT uniqueid FROM call_legs WHERE attempt_id = CAST(:a AS uuid) "
                                               "AND pbx_epoch = :ep"), {"a": str(a["id"]), "ep": a["pbx_epoch"]})}
    inv_summary = {"current_epoch": ctx.epoch if ctx else None, "attempt_epoch": a["pbx_epoch"],
                   "same_epoch": same_epoch, "checked": inventory is not None or not same_epoch}
    last = _last(session, a["id"])
    if same_epoch:
        if inventory is None:
            return None                                              # no inventory this pass
        rel = related_channels(a["id"], legs | {f"cawp-{a['id']}"}, inventory)
        inv_summary.update(related_channels=len(rel), channels=rel[:8], total_channels=len(inventory))
        if rel:
            if not last or (last["decision"], last["code"]) != ("kept_uncertain", "channels_present"):
                record(session, a, "kept_uncertain", "channels_present", inventory=inv_summary,
                       evidence=facts.summary())
            return {"decision": "kept_uncertain", "code": "channels_present"}
        young = session.execute(text("""
            SELECT max(received_at) > now() - make_interval(secs => :s) FROM call_events
             WHERE attempt_id = CAST(:a AS uuid)"""), {"a": str(a["id"]), "s": SETTLE_SECONDS}).scalar()
        if young:
            return None
    cdr = (cdr_source.lookup(str(a["id"]), around=a["created_at"], session=session) if cdr_source is not None
           else {"source": "none", "available": False})
    decision, code = decide(a, facts, cdr)
    if decision == "failed" and code == "no_customer_contact" and same_epoch:
        # An Originate whose acknowledgement was lost may still create its channel later: only
        # once the one-shot token has expired can no customer leg ever be authorized.
        live_token = session.execute(text("""
            SELECT EXISTS (SELECT 1 FROM call_dispatch_tokens WHERE attempt_id = CAST(:a AS uuid)
                              AND expires_at > now())"""), {"a": str(a["id"])}).scalar()
        if live_token:
            return None
    if decision == "kept_uncertain":
        if not last or (last["decision"], last["code"]) != (decision, code):
            record(session, a, decision, code, inventory=inv_summary, cdr=cdr, evidence=facts.summary())
        age = session.execute(text("SELECT now() - updated_at FROM call_attempts WHERE id = CAST(:a AS uuid)"),
                              {"a": str(a["id"])}).scalar()
        if code in NEEDS_HUMAN or age > UNRESOLVED_FLAG_AFTER:
            P.flag(session, a, "reconciliation_unresolved", code, detail={"facts": facts.summary(), "cdr": cdr})
        return {"decision": decision, "code": code}
    sets = resolve_to(session, a, facts, decision, cdr)
    released = False
    if decision == "failed" and code == "no_customer_contact":
        released = P.release_no_customer_contact(session, {**a, "reason_code": a["reason_code"]})
    record(session, a, decision, code, inventory=inv_summary, cdr=cdr, evidence=facts.summary(), released=released)
    _audit(session, a, "call.reconciled", {"decision": decision, "code": code, "prior_reason": a["reason_code"],
                                            "reservation_released": released,
                                            "set": {k: v for k, v in sets.items() if k != "state"}})
    return {"decision": decision, "code": code, "released": released}


class Reconciler(threading.Thread):
    def __init__(self, app, current_ctx, cdr_source=None, *, interval: float = 10.0):
        super().__init__(name="reconciler", daemon=True)
        self.app, self.current_ctx, self.cdr_source, self.interval = app, current_ctx, cdr_source, interval
        self.stopping = threading.Event()
        self.stats = {"passes": 0, "resolved": 0, "kept": 0, "marked_uncertain": 0, "inventory_failed": 0,
                      "errors": 0}
        self.last_pass_at = None
        self.cursor, self.page = ZERO_UUID, 50

    def stop(self, timeout=5.0):
        self.stopping.set()
        if self.is_alive() and threading.current_thread() is not self:
            self.join(timeout)

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

    def run(self):
        import time
        last_log = None
        while not self.stopping.wait(self.interval):
            try:
                self.run_once()
                last_log = None
            except Exception:  # noqa: BLE001 - e.g. the database is down: next pass; log once a minute
                self.stats["errors"] += 1
                if last_log is None or time.monotonic() - last_log > 60:
                    log.exception("reconciliation pass failed (retrying)")
                    last_log = time.monotonic()

    def run_once(self) -> dict:
        from services.calling.ami import AmiError
        ctx = self.current_ctx()
        if ctx is not None and not ctx.session.ready:
            ctx = None
        epoch = ctx.epoch if ctx else None
        self._tx(lambda s: catch_up(s, epoch))
        marked = self._tx(lambda s: mark_missing(s, epoch))
        self.stats["marked_uncertain"] += len(marked)
        # a rotating cursor over the uncertain attempts (50 per pass): attempts that stay uncertain
        # can never starve newer ones
        pending = self._tx(lambda s: [dict(r) for r in s.execute(text("""
            SELECT id, pbx_epoch FROM call_attempts
             WHERE state = 'uncertain' AND pbx_epoch IS NOT NULL AND id > CAST(:after AS uuid)
             ORDER BY id LIMIT :lim"""), {"after": self.cursor, "lim": self.page}).mappings()])
        self.cursor = str(pending[-1]["id"]) if len(pending) == self.page else ZERO_UUID
        out = {"marked": marked, "decisions": {}}
        if not pending:
            self.stats["passes"] += 1
            return out
        inventory = None
        if ctx is not None and any(p["pbx_epoch"] == epoch for p in pending):
            try:
                inventory = ctx.session.list_channels()
            except AmiError as e:
                self.stats["inventory_failed"] += 1
                log.warning("channel inventory unavailable (%s)", e.code)
        for p in pending:
            if ctx is None:
                break                                   # the PBX identity is unknown: wait for a session
            try:
                res = self._tx(lambda s, p=p: reconcile_attempt(s, p["id"], ctx, inventory, self.cdr_source))
            except Exception:  # noqa: BLE001
                self.stats["errors"] += 1
                log.exception("reconciliation of %s failed", p["id"])
                continue
            if res:
                out["decisions"][str(p["id"])] = res
                self.stats["kept" if res["decision"] == "kept_uncertain" else "resolved"] += 1
        self.stats["passes"] += 1
        self.last_pass_at = _dt.datetime.now(_dt.timezone.utc).isoformat()
        return out
