"""Click-to-call dispatch (Phase 5 P1): authorization in the API, submission in the calling service.

Handoff (the API never touches AMI; the calling service is the only AMI owner):

    API  POST /v2/calls  (request-bound, one transaction, @idempotent commits)
         rules.evaluate() -> attempt (prepared -> authorized) -> reservation (collector, case,
         extension) -> call_dispatch_queue row (expected PBX epoch, context, limits, expiry)
         -> audit. Refuses with 503 transport_not_connected unless a calling service reported a
         connected AMI session within 15 s.

    calling service  Dispatcher thread (poll 0.25 s)
         claim: SELECT ... FOR UPDATE SKIP LOCKED the oldest queued row enqueued for THIS PBX
         epoch (a row for another epoch - the PBX restarted after the API authorized it - is
         never sent); an expired row -> `expired` (attempt failed `dispatch_expired`, or
         `pbx_epoch_changed` when the epoch moved; reservation released: never sent); rules.evaluate()
         again (the final recheck) -> `refused` with the rule code; otherwise issue the one-shot
         token (plaintext only in this process's memory, SHA-256 in the database, bound to the
         epoch UUID, the extension, the destination, the context and the channel id), bind the
         channel id + epoch to the attempt, queue row -> `sending`; COMMIT; then send ONE
         Originate (services/calling/originate.py) on the live session:
             Response Success -> `submitted`, attempt `dispatched` (SUBMITTED, never connected)
             Response Error   -> `rejected`, attempt failed `originate_rejected` (never queued)
             no/lost answer   -> `unacknowledged`, attempt `uncertain` (originate_unacknowledged)
         Nothing is ever retried or re-sent.

    cancel  POST /v2/calls/<id>/cancel: only while the queue row is still `queued` (the claim
         holds the row lock, so cancel and claim serialize); the attempt becomes `cancelled`
         and the reservation is released (`cancel_unsent`).

    campaigns (P2)  services/calling/campaign_dispatch.py authorizes a campaign call with the SAME
         `authorize()` (mode='campaign', campaign_id set); the claim below adds one campaign
         recheck (campaign still running, member still ready with a live lease). Click-to-call
         behaviour is unchanged.
"""
from __future__ import annotations

import logging
import os
import threading
import time

from sqlalchemy import text

from core import audit
from core.api_response import VALIDATION_FAILED, ApiError
from core.revisions import RevisionConflict, parse_revision
from services.calling import attempts as A
from services.calling import rules as R
from services.calling.common import CALLS_MANAGE, advisory_lock, me, parse_uuid, sess

log = logging.getLogger("cawp.calling.dispatch")


def _env_int(name, default, lo, hi):
    try:
        v = int(os.environ.get(name) or default)
    except ValueError:
        v = default
    return max(lo, min(hi, v))


def settings() -> dict:
    """Deployment settings of a click-to-call (the API side; the service reads them from the
    queue row). The maximum must not exceed the installed dialplan's max_call_seconds."""
    return {
        "context": (os.environ.get("CALLING_CONTEXT") or "collections-human-outbound").strip(),
        "driver": (os.environ.get("CALLING_DRIVER") or "PJSIP").strip(),
        "agent_ring_seconds": _env_int("CALLING_AGENT_RING_SECONDS", 30, 5, 60),
        "max_call_seconds": _env_int("CALLING_MAX_CALL_SECONDS", 600, 30, 14400),
        "dispatch_ttl_seconds": _env_int("CALLING_DISPATCH_TTL_SECONDS", 30, 5, 300),
    }


# --------------------------------------------------------------------------- API side
def authorize_call(body: dict) -> str:
    """POST /v2/calls: rules, attempt, reservation, queue row, audit. Returns the attempt id."""
    unknown = set(body) - {"case_id", "contact_id"}
    if unknown:
        raise ApiError(422, VALIDATION_FAILED, f"unknown fields: {', '.join(sorted(unknown))}",
                       {k: "unknown" for k in unknown})
    case_id = parse_uuid(body.get("case_id"), "case_id")
    contact_id = parse_uuid(body.get("contact_id"), "contact_id")
    try:
        return authorize(me(), case_id, contact_id, transport=R.transport_status)
    except R.RuleRefused as refused:
        raise refused.api_error() from None


def authorize(actor, case_id, contact_id, *, transport, mode: str = "click_to_call", campaign_id=None,
              audit_after: dict | None = None, audit_kw: dict | None = None) -> str:
    """The ONE dispatch authorization (click-to-call and campaigns, P2): serialize on the
    collector and the case, run every rule, check the transport, then write the attempt
    (prepared -> authorized), the reservation (collector, case, extension) and the dispatch
    queue row the calling service claims, plus the audit row. Nothing commits here.

    `transport` is a callable returning the calling-service status ({"epoch": ...}) or None;
    it is called after the rules (as POST /v2/calls always did). Raises R.RuleRefused (the
    caller turns it into its API error) or ApiError 409 calling_reserved on a reservation race."""
    for k in sorted((f"user:{actor}", f"case:{case_id}")):      # serialize concurrent clicks
        advisory_lock("calling_dispatch", k)
    plan = R.evaluate(actor_id=actor, case_id=case_id, contact_id=contact_id)
    status = transport()
    if status is None:
        raise R.RuleRefused("transport_not_connected")
    cfg = settings()
    attempt_id = A.create_attempt(mode=mode, state="prepared", case_id=case_id, collector_user_id=actor,
                                  created_by=actor, contact_id=contact_id, destination=plan.destination,
                                  extension=plan.extension, campaign_id=campaign_id)
    A.reserve(attempt_id)                                         # 409 calling_reserved on a race
    A.transition(attempt_id, "authorized")
    queue_id = sess().execute(text("""
        INSERT INTO call_dispatch_queue (attempt_id, expected_epoch, context, driver, agent_ring_seconds,
                                         max_call_seconds, created_by, expires_at)
        VALUES (CAST(:a AS uuid), :ep, :ctx, :drv, :ring, :lim, CAST(:by AS uuid),
                now() + make_interval(secs => :ttl))
        RETURNING id"""), {"a": attempt_id, "ep": status["epoch"], "ctx": cfg["context"], "drv": cfg["driver"],
                           "ring": cfg["agent_ring_seconds"], "lim": cfg["max_call_seconds"], "by": actor,
                           "ttl": cfg["dispatch_ttl_seconds"]}).scalar()
    audit.record("call.dispatch.authorize", "call_attempt", attempt_id, case_id=case_id, after={
        "contact_id": contact_id, "extension": plan.extension, "queue_id": str(queue_id),
        "expected_epoch": status["epoch"], "checked_at": plan.checked_at, "outstanding": plan.outstanding,
        "rules": "all passed", **(audit_after or {})}, **(audit_kw or {}))
    return attempt_id


def cancel(attempt_id, body: dict, expected_revision) -> str:
    """POST /v2/calls/<id>/cancel: before the dispatcher claimed the queue row only."""
    from core.auth import has_permission
    from services.calling import queries
    unknown = set(body) - {"expected_revision", "reason"}
    if unknown:
        raise ApiError(422, VALIDATION_FAILED, f"unknown fields: {', '.join(sorted(unknown))}",
                       {k: "unknown" for k in unknown})
    reason = body.get("reason")
    if reason is not None and (not isinstance(reason, str) or len(reason) > 500):
        raise ApiError(422, VALIDATION_FAILED, "reason must be text of at most 500 characters", {"reason": "invalid"})
    queries.visible_or_404(attempt_id)
    q = sess().execute(text("SELECT id, state FROM call_dispatch_queue WHERE attempt_id = CAST(:a AS uuid) FOR UPDATE"),
                       {"a": str(attempt_id)}).mappings().first()
    a = A.lock_attempt(attempt_id)
    if str(a["collector_user_id"]) != me() and not has_permission(CALLS_MANAGE):
        raise ApiError(403, "forbidden", "only the collector who placed the call (or calling admin) can cancel it",
                       {"required_any": [CALLS_MANAGE]})
    expected = parse_revision(expected_revision)
    if int(a["revision"]) != expected:
        raise RevisionConflict("call_attempt", attempt_id, expected, int(a["revision"]))
    if a["state"] not in ("prepared", "authorized") or (q is not None and q["state"] != "queued"):
        raise ApiError(409, "already_dispatching",
                       "the call was already handed to the PBX; it can no longer be cancelled",
                       {"state": a["state"], "dispatch": q["state"] if q else None})
    if q is not None:
        sess().execute(text("""UPDATE call_dispatch_queue SET state = 'cancelled', finished_at = now(),
                                      result_code = 'cancelled_by_user' WHERE id = CAST(:id AS uuid)"""),
                       {"id": str(q["id"])})
    A.transition(attempt_id, "cancelled", reason_code="cancelled_by_user")
    released = A.release_reservation(attempt_id, "cancel_unsent", by=me(), reason=(reason or "cancelled before dispatch"))
    audit.record("call.cancel", "call_attempt", attempt_id, case_id=str(a["case_id"]),
                 before={"state": a["state"]}, after={"state": "cancelled", "reservation_released": released},
                 reason=reason)
    return str(attempt_id)


# --------------------------------------------------------------------------- service side
def _finish_queue(session, qid, state, code, *, ami=None):
    session.execute(text("""
        UPDATE call_dispatch_queue SET state = :st, result_code = :code, finished_at = now(),
               ami_response = coalesce(ami_response, :ami)
         WHERE id = CAST(:id AS uuid)"""), {"st": state, "code": code, "ami": ami, "id": str(qid)})


def _svc_audit(session, action, attempt_id, case_id, after, reason=None):
    audit.record(action, "call_attempt", str(attempt_id), case_id=str(case_id), after=after, reason=reason,
                 actor_user_id=None, request_id=None, idempotency_key=None, source="calling", session=session)


def _never_sent(session, attempt, qid, queue_state, code):
    """The queue row ends without an Originate: the attempt fails and, having provably never
    reached a customer, releases its reservation."""
    from services.calling.projection import release_no_customer_contact
    _finish_queue(session, qid, queue_state, code)
    A.transition(attempt["id"], "failed", reason_code=code)
    released = release_no_customer_contact(session, {**attempt, "reason_code": code})
    _svc_audit(session, "call.dispatch.not_sent", attempt["id"], attempt["case_id"],
               {"queue_state": queue_state, "reason_code": code, "reservation_released": released})


class Claim:
    def __init__(self, attempt_id, queue_id, fields, case_id):
        self.attempt_id, self.queue_id, self.fields, self.case_id = attempt_id, queue_id, fields, case_id


def claim(session, ctx, worker_id) -> Claim | str | None:
    """One queued row -> a Claim (committed by the caller before sending), a handled code
    ('expired', 'refused:<code>', ...) or None (nothing to do / not connected)."""
    from services.calling.originate import extension_first_fields
    connected = ctx is not None and ctx.session.ready
    # Only rows enqueued for THIS PBX process are sent; a row for another epoch (the PBX restarted
    # after the API authorized it) is never sent and ends when it expires (pbx_epoch_changed).
    q = session.execute(text("""
        SELECT q.*, (q.expires_at <= clock_timestamp()) AS expired
          FROM call_dispatch_queue q
         WHERE q.state = 'queued' AND (q.expires_at <= clock_timestamp() OR q.expected_epoch = :ep)
         ORDER BY q.created_at FOR UPDATE SKIP LOCKED LIMIT 1"""),
        {"ep": ctx.epoch if connected else None}).mappings().first()
    if q is None:
        return None
    a = A.lock_attempt(q["attempt_id"])
    a = dict(a)
    if a["state"] != "authorized":
        _finish_queue(session, q["id"], "refused", "attempt_not_authorized")
        return "refused:attempt_not_authorized"
    if q["expired"]:
        code = "pbx_epoch_changed" if connected and ctx.epoch != q["expected_epoch"] else "dispatch_expired"
        _never_sent(session, a, q["id"], "expired", code)
        return f"expired:{code}"
    if not connected or ctx.epoch != q["expected_epoch"]:
        return None                                   # defensive: never send on another epoch
    try:
        plan = R.evaluate(actor_id=a["collector_user_id"], case_id=a["case_id"], contact_id=a["contact_id"],
                          own_attempt=a["id"], extension=a["extension"])
    except R.RuleRefused as refused:
        _never_sent(session, a, q["id"], "refused", refused.code)
        return f"refused:{refused.code}"
    if plan.destination != a["destination"]:
        _never_sent(session, a, q["id"], "refused", "destination_changed")
        return "refused:destination_changed"
    if a["mode"] == "campaign":
        # P2: the campaign is still running and the member still ready with a live lease
        from services.calling.campaign_dispatch import claim_recheck
        code = claim_recheck(session, a)
        if code:
            _never_sent(session, a, q["id"], "refused", code)
            return f"refused:{code}"
    token, token_id = A.issue_dispatch_token(
        a["id"], context=q["context"], destination=a["destination"], extension=a["extension"],
        max_call_seconds=q["max_call_seconds"], pbx_epoch_uuid=ctx.identity.epoch_uuid,
        ttl_seconds=q["agent_ring_seconds"] + 30)
    A.bind_channel(a["id"], ctx.epoch)
    session.execute(text("""UPDATE call_dispatch_queue SET state = 'sending', claimed_at = now(), claimed_by = :w
                             WHERE id = CAST(:id AS uuid)"""), {"w": worker_id[:200], "id": str(q["id"])})
    _svc_audit(session, "call.dispatch.claim", a["id"], a["case_id"], {
        "queue_id": str(q["id"]), "token_id": token_id, "pbx_epoch": ctx.epoch, "worker": worker_id,
        "rules_rechecked_at": plan.checked_at})
    fields = extension_first_fields(attempt_id=str(a["id"]), extension=a["extension"], destination=a["destination"],
                                    token=token, context=q["context"], driver=q["driver"],
                                    agent_ring_seconds=q["agent_ring_seconds"])
    return Claim(str(a["id"]), str(q["id"]), fields, str(a["case_id"]))


def record_outcome(session, c: Claim, outcome: str, code: str) -> None:
    """After the Originate: submitted | rejected | unacknowledged."""
    from services.calling.projection import release_no_customer_contact
    session.execute(text("SELECT id FROM call_dispatch_queue WHERE id = CAST(:id AS uuid) FOR UPDATE"),
                    {"id": c.queue_id})
    a = dict(A.lock_attempt(c.attempt_id))
    if outcome == "submitted":
        _finish_queue(session, c.queue_id, "submitted", "submitted", ami="Success")
        sets = "dispatched_at = coalesce(dispatched_at, now()), started_at = coalesce(started_at, now())"
        session.execute(text(f"UPDATE call_attempts SET {sets} WHERE id = CAST(:id AS uuid)"), {"id": c.attempt_id})
        if a["state"] == "authorized":
            A.transition(c.attempt_id, "dispatched")
    elif outcome == "rejected":
        _finish_queue(session, c.queue_id, "rejected", "originate_rejected", ami="Error")
        if a["state"] == "authorized":
            A.transition(c.attempt_id, "failed", reason_code="originate_rejected")
            release_no_customer_contact(session, {**a, "reason_code": "originate_rejected"})
    else:
        _finish_queue(session, c.queue_id, "unacknowledged", code)
        if a["state"] in ("authorized", "dispatched", "agent_ringing", "agent_answered", "customer_dialing",
                          "customer_answered", "bridged"):
            A.transition(c.attempt_id, "uncertain", reason_code="originate_unacknowledged")
    _svc_audit(session, f"call.dispatch.{outcome}", c.attempt_id, c.case_id, {"queue_id": c.queue_id, "code": code})


class Dispatcher(threading.Thread):
    """Claims queued click-to-calls and sends each Originate exactly once on the current session."""

    def __init__(self, app, current_ctx, worker_id: str, *, poll: float = 0.25, send_timeout: float = 5.0):
        super().__init__(name="dispatcher", daemon=True)
        self.app, self.current_ctx, self.worker_id = app, current_ctx, worker_id
        self.poll, self.send_timeout = poll, send_timeout
        self.stopping = threading.Event()
        self.stats = {"claimed": 0, "submitted": 0, "rejected": 0, "unacknowledged": 0, "not_sent": 0, "errors": 0}

    def stop(self, timeout=3.0):
        self.stopping.set()
        if self.is_alive() and threading.current_thread() is not self:
            self.join(timeout)

    def run(self):
        failing_since = None
        while not self.stopping.is_set():
            try:
                busy = self.run_once()
                if failing_since is not None:
                    log.info("dispatcher recovered")
                failing_since = None
            except Exception:  # noqa: BLE001 - e.g. the database is down: retry, log once a minute
                self.stats["errors"] += 1
                now = time.monotonic()
                if failing_since is None or now - failing_since > 60:
                    log.exception("dispatcher cycle failed (retrying; logged once a minute)")
                    failing_since = now
                busy = False
                self.stopping.wait(1.0)
            if not busy:
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

    def run_once(self) -> bool:
        from services.calling.ami import AmiError
        ctx = self.current_ctx()
        got = self._tx(lambda s: claim(s, ctx, self.worker_id))
        if got is None:
            return False
        if isinstance(got, str):
            self.stats["not_sent"] += 1
            log.info("dispatch not sent: %s", got)
            return True
        self.stats["claimed"] += 1
        started = time.monotonic()
        try:
            frame = ctx.session.send("Originate", got.fields, timeout=self.send_timeout)
            outcome = "submitted" if frame.response == "Success" else "rejected"
            code = outcome
        except AmiError as e:
            outcome, code = "unacknowledged", e.code
        got.fields = None                                    # drop the token from memory
        self.stats[outcome] += 1
        log.info("attempt %s: Originate %s (%.0f ms)", got.attempt_id, outcome, (time.monotonic() - started) * 1000)
        for i in range(20):                                  # the database may blip: never lose the outcome
            try:
                self._tx(lambda s: record_outcome(s, got, outcome, code))
                break
            except Exception:  # noqa: BLE001
                log.exception("could not record the Originate outcome of %s (try %d)", got.attempt_id, i + 1)
                if self.stopping.wait(min(5.0, 0.2 * (i + 1))):
                    break
        return True
