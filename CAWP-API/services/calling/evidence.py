"""AMI evidence consumer: PBX events -> call_events (raw) / call_legs (projected) / pbx_sessions.

Runs inside the `calling` service on the AMI supervisor's ordered consumer thread.

What is kept (everything else on the PBX is ignored, so other calls' data never lands here):
* `UserEvent` with `UserEvent: CollectionsCall` - attributed to its AttemptId only when the
  event's own Linkedid is the app-issued `cawp-<AttemptId>` channel (a dialplan variable
  alone is not trusted; otherwise it is kept unattributed with `claimed_attempt_id`);
* Newchannel / Newstate / Hangup / SoftHangupRequest / HangupRequest / DialBegin / DialEnd /
  BridgeEnter / BridgeLeave / OriginateResponse of channels whose Uniqueid or Linkedid (or,
  for dial events, DestUniqueid/DestLinkedid) is an app-issued `cawp-<uuid>` channel;
* PBX lifecycle: FullyBooted, Shutdown, Reload.
Fields are allowlisted (no caller-id names/numbers, no raw frames). The PBX `Timestamp` is kept
as `pbx_ts`; receipt time is never used for ordering.

Projection (call_legs) records facts only: the agent leg is the app-issued channel; a
customer leg is the destination of a DialBegin whose caller is the agent leg; everything
else linked to the attempt is `unknown`. The attempt's business state is NOT advanced here
(P1's projection does that, requiring bridge membership of both legs - two answered legs are
not a conversation).

Durability: each event commits in its own transaction. If the database is unavailable the
event is kept in a bounded in-memory buffer and retried on the next event/heartbeat; if the
buffer overflows, an `EvidenceGap` marker records the lost window once the database is back.
Session end and PBX restarts record `EvidenceGap` markers and mark in-flight attempts of the
affected epoch `uncertain` (reservations are never released).
"""
from __future__ import annotations

import collections
import decimal
import hashlib
import json
import logging
import re
import threading
import time

from sqlalchemy import text

log = logging.getLogger("cawp.calling.evidence")

ATTEMPT_CHANNEL_RE = re.compile(r"^cawp-([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$")
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
STATE_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
CHANNEL_EVENTS = {"Newchannel", "Newstate", "Hangup", "HangupRequest", "SoftHangupRequest", "DialBegin",
                  "DialEnd", "BridgeEnter", "BridgeLeave", "OriginateResponse"}
LIFECYCLE_EVENTS = {"FullyBooted", "Shutdown", "Reload"}
KEEP_FIELDS = (
    "ChannelState", "ChannelStateDesc", "Context", "Exten", "Priority", "Cause",
    "DestChannel", "DestUniqueid", "DestLinkedid", "DestChannelState", "DestChannelStateDesc", "DestContext",
    "DestExten", "DialString", "DialStatus", "BridgeUniqueid", "BridgeType", "BridgeTechnology",
    "BridgeNumChannels", "Response", "Reason", "ActionID", "UserEvent", "AttemptId", "State", "DialStatus",
    "Recording", "Uptime", "LastReload", "Status", "Shutdown", "Restart", "Module",
)
MAX_BUFFER = 5000


def attempt_of_channel(value) -> str | None:
    m = ATTEMPT_CHANNEL_RE.match(value or "")
    return m.group(1) if m else None


def _pbx_ts(value):
    if value is None:
        return None
    try:
        d = decimal.Decimal(value)
    except (decimal.InvalidOperation, ValueError):
        return None
    return d if d.is_finite() and 0 < d < decimal.Decimal("100000000000") else None


def _int(value):
    return int(value) if isinstance(value, str) and value.isdigit() and len(value) <= 9 else None


def classify(frame, epoch: str) -> dict | None:
    """Pure: an AMI frame -> the call_events row to store, or None (not ours / not kept)."""
    try:
        event = frame.event
    except Exception:  # noqa: BLE001 - duplicated routing key
        return None
    if not event:
        return None
    try:
        get = frame.get
        uniqueid, linkedid = get("Uniqueid"), get("Linkedid")
        attempt, claimed, state = None, None, None
        if event == "UserEvent":
            if get("UserEvent") != "CollectionsCall":
                return None
            claimed = (get("AttemptId") or "").strip().lower() or None
            raw_state = (get("State") or "").strip()
            state = raw_state if STATE_RE.match(raw_state) else None
            owner = attempt_of_channel(linkedid)
            if claimed and UUID_RE.match(claimed) and owner == claimed:
                attempt = claimed
        elif event in CHANNEL_EVENTS:
            attempt = (attempt_of_channel(linkedid) or attempt_of_channel(uniqueid)
                       or attempt_of_channel(get("DestLinkedid")) or attempt_of_channel(get("DestUniqueid")))
            if attempt is None:
                return None
        elif event in LIFECYCLE_EVENTS:
            pass
        else:
            return None
        payload = {}
        for k in KEEP_FIELDS:
            vals = frame.values(k)
            if vals:
                payload[k] = vals[0][:200]
        if event == "UserEvent" and attempt is None and claimed:
            payload["claimed_attempt_id"] = claimed[:64]
            payload["untrusted"] = True
        ts = _pbx_ts(get("Timestamp"))
        if ts is None:
            payload["pbx_timestamp_missing"] = True
        channel = get("Channel") or get("DestChannel")
    except Exception:  # noqa: BLE001 - malformed/duplicated fields: not attributable evidence
        return None
    canonical = json.dumps({"e": event, "u": uniqueid, "l": linkedid, "t": str(ts), "p": payload},
                           sort_keys=True, separators=(",", ":"))
    return {
        "event": event, "state": state, "attempt_id": attempt, "pbx_ts": ts,
        "uniqueid": (uniqueid or None) and uniqueid[:150], "linkedid": (linkedid or None) and linkedid[:150],
        "channel": (channel or None) and channel[:200], "payload": payload,
        "dedupe_key": "ami:" + hashlib.sha256(f"{epoch}|{canonical}".encode("utf-8")).hexdigest(),
    }


_INSERT_EVENT = text("""
    INSERT INTO call_events (source, pbx_session_id, pbx_epoch, attempt_id, event, state, pbx_ts, uniqueid,
                             linkedid, channel, payload, dedupe_key)
    VALUES (:source, CAST(:sid AS uuid), :epoch, CAST(:attempt AS uuid), :event, :state, :ts, :uniqueid,
            :linkedid, :channel, CAST(:payload AS jsonb), :dedupe)
    ON CONFLICT (dedupe_key) DO NOTHING
    RETURNING id
""")


def insert_event(session, row: dict, *, source: str, pbx_session_id, epoch) -> int | None:
    r = session.execute(_INSERT_EVENT, {
        "source": source, "sid": pbx_session_id, "epoch": epoch, "attempt": row.get("attempt_id"),
        "event": row["event"], "state": row.get("state"), "ts": row.get("pbx_ts"),
        "uniqueid": row.get("uniqueid"), "linkedid": row.get("linkedid"), "channel": row.get("channel"),
        "payload": json.dumps(row.get("payload") or {}, sort_keys=True), "dedupe": row["dedupe_key"]}).first()
    return r[0] if r else None


def project_leg(session, row: dict, event_id: int, epoch: str) -> None:
    """Upsert set-once facts on call_legs for one stored channel event."""
    attempt, event, p = row["attempt_id"], row["event"], row["payload"]
    if attempt is None:
        return
    agent_channel = f"cawp-{attempt}"
    ts = row["pbx_ts"]
    targets = []                                  # (uniqueid, role, facts)
    if event == "Newchannel":
        targets.append((row["uniqueid"], "agent" if row["uniqueid"] == agent_channel else "unknown",
                        {"created_pbx_ts": ts, "channel": row["channel"]}))
    elif event == "Newstate":
        st = _int(p.get("ChannelState"))
        facts = {"channel": row["channel"]}
        if st in (4, 5):
            facts["ringing_pbx_ts"] = ts
        elif st == 6:
            facts["answered_pbx_ts"] = ts
        targets.append((row["uniqueid"], "agent" if row["uniqueid"] == agent_channel else "unknown", facts))
    elif event == "DialBegin":
        dest = p.get("DestUniqueid")
        if dest and row["uniqueid"] == agent_channel and dest != agent_channel:
            targets.append((dest, "customer", {"channel": p.get("DestChannel")}))
        elif dest == agent_channel:
            targets.append((dest, "agent", {"channel": p.get("DestChannel")}))
    elif event == "Hangup":
        targets.append((row["uniqueid"], "agent" if row["uniqueid"] == agent_channel else "unknown",
                        {"hungup_pbx_ts": ts, "hangup_cause": _int(p.get("Cause"))}))
    elif event == "BridgeEnter":
        targets.append((row["uniqueid"], "agent" if row["uniqueid"] == agent_channel else "unknown",
                        {"bridged_pbx_ts": ts, "bridge_id": (p.get("BridgeUniqueid") or None)}))
    for uniqueid, role, facts in targets:
        if not uniqueid:
            continue
        cols = {k: v for k, v in facts.items() if v is not None}
        sets = ", ".join(f"{k} = coalesce(call_legs.{k}, EXCLUDED.{k})" for k in cols)
        role_set = ("role = CASE WHEN call_legs.role = 'unknown' THEN EXCLUDED.role ELSE call_legs.role END")
        session.execute(text(f"""
            INSERT INTO call_legs (pbx_epoch, uniqueid, linkedid, attempt_id, role, first_event_id, last_event_id
                                   {''.join(', ' + k for k in cols)})
            VALUES (:epoch, :uid, :lid, CAST(:attempt AS uuid), :role, :eid, :eid
                    {''.join(', :' + k for k in cols)})
            ON CONFLICT (pbx_epoch, uniqueid) DO UPDATE
               SET {role_set}, attempt_id = coalesce(call_legs.attempt_id, EXCLUDED.attempt_id),
                   linkedid = coalesce(call_legs.linkedid, EXCLUDED.linkedid),
                   last_event_id = greatest(call_legs.last_event_id, EXCLUDED.last_event_id)
                   {(', ' + sets) if sets else ''}
        """), {"epoch": epoch, "uid": uniqueid[:150], "lid": row.get("linkedid"), "attempt": attempt,
               "role": role, "eid": event_id, **cols})


class EvidenceConsumer:
    """Callbacks for services.calling.ami.AmiSupervisor. `app` is a Flask app (db bound)."""

    def __init__(self, app, *, host_label: str, worker_id: str, max_buffer: int = MAX_BUFFER):
        self.app, self.host_label, self.worker_id, self.max_buffer = app, host_label, worker_id, max_buffer
        self.buffer: collections.deque = collections.deque()
        self.lost = 0                     # events dropped since the last recorded gap
        self.lost_since = None
        self.pbx_session_ids: dict[str, str] = {}
        self.pending_sessions: dict = {}
        self.stats = collections.Counter()
        self.stopping = False             # set by the service before a deliberate shutdown
        self._failing, self._last_fail_log = False, 0.0
        self._lock = threading.Lock()

    # ---- helpers --------------------------------------------------------------------
    def _run(self, fn):
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

    def _gap_row(self, epoch, reason, extra=None):
        payload = {"reason": reason, **(extra or {})}
        key = "gap:" + hashlib.sha256(f"{epoch}|{reason}|{time.time_ns()}".encode()).hexdigest()
        return {"event": "EvidenceGap", "state": None, "attempt_id": None, "pbx_ts": None, "uniqueid": None,
                "linkedid": None, "channel": None, "payload": payload, "dedupe_key": key}

    # ---- supervisor callbacks -------------------------------------------------------
    def on_session_start(self, ctx):
        """Record the session and its PBX epoch. A database outage does not drop the AMI
        session: the record (and the epoch-change check) is retried before any buffered event."""
        with self._lock:
            self.pending_sessions[ctx.session.session_id] = ctx
        self.flush()
        ident = ctx.identity
        log.info("AMI session %s: PBX epoch %s (%s)%s", ctx.session.session_id, ident.epoch, ident.strength,
                 " - PBX RESTARTED" if ctx.pbx_restarted else "")

    def _record_session(self, ctx):
        ident = ctx.identity

        def work(session):
            sid = session.execute(text("""
                INSERT INTO pbx_sessions (ami_session_id, host, epoch, epoch_uuid, startup, epoch_strength,
                                          previous_epoch, pbx_restarted, ami_version, worker_id)
                VALUES (:sid, :host, :epoch, :uuid, :startup, :strength, :prev, :restarted, :ver, :worker)
                RETURNING id"""), {
                "sid": ctx.session.session_id, "host": self.host_label, "epoch": ident.epoch,
                "uuid": ident.epoch_uuid, "startup": ident.startup, "strength": ident.strength,
                "prev": ctx.previous.epoch if ctx.previous else None, "restarted": ctx.pbx_restarted,
                "ver": ident.ami_version, "worker": self.worker_id}).scalar()
            from services.calling.attempts import mark_in_flight_uncertain
            # Anything dispatched on another PBX process can no longer be observed.
            changed = mark_in_flight_uncertain("pbx_epoch_changed", other_than_epoch=ident.epoch)
            if ctx.pbx_restarted:
                insert_event(session, self._gap_row(ctx.previous.epoch, "pbx_restarted",
                                                    {"new_epoch": ident.epoch}),
                             source="consumer", pbx_session_id=str(sid), epoch=ctx.previous.epoch)
            return str(sid), changed

        sid, changed = self._run(work)
        self.pbx_session_ids[ctx.session.session_id] = sid
        if changed:
            log.warning("PBX epoch changed: %d in-flight attempt(s) marked uncertain", len(changed))

    def on_event(self, frame, ctx):
        row = classify(frame, ctx.epoch)
        if row is None:
            return
        self.stats["classified"] += 1
        with self._lock:
            self.buffer.append((row, ctx.epoch, ctx.session.session_id))
            if len(self.buffer) > self.max_buffer:
                self.buffer.popleft()
                self.lost += 1
                self.lost_since = self.lost_since or time.time()
        self.flush()

    def on_heartbeat(self, ctx):
        self.flush()

    def _db_failed(self, message):
        """Log the first failure of a streak with its traceback, then one line per minute."""
        self.stats["db_failures"] += 1
        now = time.monotonic()
        if not self._failing:
            log.warning(message, exc_info=True)
        elif now - self._last_fail_log > 60:
            log.warning("%s (still failing)", message)
        else:
            return
        self._failing, self._last_fail_log = True, now

    def flush(self) -> int:
        """Persist buffered events in order. Stops at the first database failure (retried later)."""
        done = 0
        while True:
            with self._lock:
                pending = next(iter(self.pending_sessions.values()), None)
            if pending is not None:
                try:
                    self._record_session(pending)
                except Exception:  # noqa: BLE001 - database unavailable: retried on the next event/heartbeat
                    self._db_failed("could not record the AMI session yet; evidence stays buffered")
                    return done
                with self._lock:
                    self.pending_sessions.pop(pending.session.session_id, None)
                continue
            with self._lock:
                if not self.buffer and not self.lost:
                    return done
                item = self.buffer[0] if self.buffer else None
                lost, lost_since = self.lost, self.lost_since
            try:
                if lost:
                    epoch = item[1] if item else None

                    def gap(session, epoch=epoch, lost=lost, since=lost_since):
                        insert_event(session, self._gap_row(epoch, "consumer_buffer_overflow",
                                                            {"lost_events": lost, "since_epoch_s": int(since or 0)}),
                                     source="consumer", pbx_session_id=None, epoch=epoch)
                        from services.calling.attempts import mark_in_flight_uncertain
                        mark_in_flight_uncertain("evidence_lost", epoch=epoch)
                    self._run(gap)
                    with self._lock:
                        self.lost -= lost
                        if not self.lost:
                            self.lost_since = None
                    continue
                row, epoch, ami_sid = item
                sid = self.pbx_session_ids.get(ami_sid)

                def store(session, row=row, epoch=epoch, sid=sid):
                    eid = insert_event(session, row, source="ami", pbx_session_id=sid, epoch=epoch)
                    if eid is not None:
                        project_leg(session, row, eid, epoch)
                    return eid
                eid = self._run(store)
                if self._failing:
                    log.info("evidence writes recovered")
                    self._failing = False
                self.stats["stored" if eid else "duplicates"] += 1
                with self._lock:
                    if self.buffer and self.buffer[0] is item:
                        self.buffer.popleft()
                done += 1
            except Exception:  # noqa: BLE001 - database unavailable: keep the event, retry later
                self._db_failed(f"evidence write failed; {len(self.buffer)} event(s) buffered")
                return done

    def on_session_end(self, ctx, code: str):
        reason = "calling_service_stopped" if self.stopping else "ami_connection_lost"
        self.flush()
        with self._lock:
            if self.pending_sessions.pop(ctx.session.session_id, None) is not None:
                log.warning("AMI session %s ended before it could be recorded", ctx.session.session_id)
        sid = self.pbx_session_ids.get(ctx.session.session_id)

        def work(session):
            if sid:
                session.execute(text("""
                    UPDATE pbx_sessions SET disconnected_at = now(), disconnect_code = :code
                     WHERE id = CAST(:id AS uuid) AND disconnected_at IS NULL"""),
                    {"code": (code or "ami_closed")[:64], "id": sid})
            insert_event(session, self._gap_row(ctx.epoch, reason, {"code": code}), source="consumer",
                         pbx_session_id=sid, epoch=ctx.epoch)
            from services.calling.attempts import mark_in_flight_uncertain
            return mark_in_flight_uncertain(reason, epoch=ctx.epoch)
        try:
            changed = self._run(work)
            if changed:
                log.warning("AMI session ended (%s): %d in-flight attempt(s) marked uncertain", code, len(changed))
        except Exception:  # noqa: BLE001 - recorded on the next successful start instead
            log.exception("could not record the end of AMI session %s", ctx.session.session_id)
