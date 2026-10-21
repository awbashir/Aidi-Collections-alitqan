"""Attributing Asterisk <= 11 events to call attempts (CALLING_PBX_EVENTS=legacy).

Asterisk 11 ignores the Originate `ChannelId` and its events carry no `Linkedid`, so the modern
rule (services/calling/evidence.classify: "the channel is `cawp-<attempt>` or linked to it")
cannot apply. Here a channel belongs to an attempt only when the PBX itself tied it to one:

A. ActionID registry. The dispatcher records the Originate's ActionID on the queue row
   (call_dispatch_queue.ami_action_id, migration 0084) and commits BEFORE sending it. An
   `OriginateResponse` with that ActionID, for the attempt's own epoch, whose Context and Exten are
   the queued ones, is the attempt's: `Response: Success` with `Channel: <driver>/<ext>-<hex>` and an
   "<epoch>.<seq>" Uniqueid binds that Uniqueid as the AGENT leg (call_legs role agent);
   `Response: Failure` (Uniqueid `<null>`) is the agent's failure (projection ORIGINATE_REASON).
   A response that does not match is kept on the attempt with `binding: refused:<why>`
   (the projection makes the attempt uncertain); nothing is bound.
B. FastAGI (services/calling/agi.py): the hook accepts the 11-style Uniqueid; on allow it binds it
   (call_dispatch_tokens.bound_uniqueid + call_legs), and refuses `agent_channel_mismatch` when
   the attempt already has another agent leg. This thread learns those bindings from call_legs.
C. Customer legs: a `Dial` Begin (-> DialBegin) whose Uniqueid is the agent leg names a customer leg
   in DestUniqueID (the projection decides whether several, sequential failover dials, are fine).
D. Everything else is attributed only by its Uniqueid being one of the attempt's legs (a cache,
   seeded and refreshed from call_legs); a Bridge Link/Unlink also by the peer channel (so a
   foreign channel bridged with a leg is visible: the projection calls that ambiguous).
   CollectionsCall UserEvents name their own channel (`ChanUniqueid: ${UNIQUEID}` in the legacy
   dialplan; else a top-level Uniqueid; both present and different = untrusted) and must claim
   the attempt that channel belongs to. Events of a channel that is not bound YET (the agent's
   ringing, `agent_answered`, the customer's pre-dial event) are held in memory (bounded, 180 s)
   and stored as soon as their channel binds; a held UserEvent that never binds is stored
   unattributed (`untrusted`), like a forged one.

Ordering. Asterisk 11 sends no `Timestamp` unless manager.conf has `timestampevents=yes`. Then the
consumer's receipt time (strictly increasing, in the AMI stream's order, which is the PBX's
emission order) is used as `pbx_ts` and marked `pbx_ts_source: receipt`, so an event stored late
(held, then bound) still sorts where the PBX sent it.

Forgery: a modern UserEvent is trusted through its Linkedid, which a dialplan cannot set. In legacy
mode the channel id comes from the event body (`${UNIQUEID}`), so trust rests on nobody but the
operator being able to write dialplan on the PBX (docs/issabel-commissioning.md §15).
"""
from __future__ import annotations

import collections
import decimal
import hashlib
import json
import logging
import re
import time

from sqlalchemy import text

from services.calling.evidence import (CHANNEL_EVENTS, KEEP_FIELDS, LIFECYCLE_EVENTS, STATE_RE, UUID_RE,
                                       _pbx_ts)

log = logging.getLogger("cawp.calling.legacy")

UID_RE = re.compile(r"^[0-9]{1,12}\.[0-9]{1,9}$")            # Asterisk <= 11 "<epoch>.<seq>"
LEGACY_KEEP = ("ChanUniqueid", "BridgePeerUniqueid", "LegacyEvent")
TRACK_STATES = ("authorized", "dispatched", "agent_ringing", "agent_answered", "customer_dialing",
                "customer_answered", "bridged", "uncertain", "ended", "failed")
PENDING_TTL = 180.0
MAX_PENDING_UIDS = 4000
MAX_PENDING_PER_UID = 64
MAX_CACHE = 50000
REFRESH_SECONDS = 1.0


# --------------------------------------------------------------------------- classify (pure)
def classify(frame, epoch: str, rx_ts: decimal.Decimal) -> dict | None:
    """A NORMALISED frame (services/calling/legacy_events.normalise) -> an unattributed row with its
    correlation hints, or None (not kept)."""
    try:
        event = frame.event
    except Exception:  # noqa: BLE001
        return None
    if not event:
        return None
    try:
        get = frame.get
        uniqueid, claimed, state, conflict = get("Uniqueid"), None, None, False
        if event == "UserEvent":
            if get("UserEvent") != "CollectionsCall":
                return None
            claimed = (get("AttemptId") or "").strip().lower() or None
            raw_state = (get("State") or "").strip()
            state = raw_state if STATE_RE.match(raw_state) else None
            body_uid = (get("ChanUniqueid") or "").strip() or None
            if body_uid and uniqueid and body_uid != uniqueid:
                conflict = True
            uniqueid = body_uid or uniqueid
        elif event in CHANNEL_EVENTS:
            pass
        elif event in LIFECYCLE_EVENTS:
            uniqueid = None
        else:
            return None
        payload = {}
        for k in (*KEEP_FIELDS, *LEGACY_KEEP):
            vals = frame.values(k)
            if vals and k not in payload:
                payload[k] = vals[0][:200]
        ts = _pbx_ts(get("Timestamp"))
        if ts is None:
            ts = rx_ts
            payload["pbx_ts_source"] = "receipt"
        channel = get("Channel") or get("DestChannel")
        action_id = get("ActionID")
    except Exception:  # noqa: BLE001
        return None
    canonical = json.dumps({"e": event, "u": uniqueid, "t": str(ts), "r": str(rx_ts), "p": payload},
                           sort_keys=True, separators=(",", ":"))
    return {
        "event": event, "state": state, "attempt_id": None, "pbx_ts": ts,
        "uniqueid": (uniqueid or None) and uniqueid[:150], "linkedid": None,
        "channel": (channel or None) and channel[:200], "payload": payload,
        "dedupe_key": "ami:" + hashlib.sha256(f"{epoch}|legacy|{canonical}".encode("utf-8")).hexdigest(),
        "legacy": True, "claimed": claimed, "uid_conflict": conflict, "action_id": action_id,
        "dest_uid": payload.get("DestUniqueid"), "peer_uid": payload.get("BridgePeerUniqueid"),
    }


def untrusted(row: dict) -> dict:
    """A UserEvent that cannot be attributed: stored without attempt, flagged (modern parity)."""
    out = {**row, "attempt_id": None, "payload": dict(row["payload"])}
    if row.get("claimed"):
        out["payload"]["claimed_attempt_id"] = row["claimed"][:64]
    out["payload"]["untrusted"] = True
    return out


# --------------------------------------------------------------------------- database helpers
def registry_entry(session, action_id: str | None):
    """The dispatch an ActionID was recorded for (call_dispatch_queue.ami_action_id), or None."""
    if not action_id:
        return None
    r = session.execute(text("""
        SELECT q.attempt_id::text AS attempt_id, q.context, q.driver, a.extension, a.destination, a.pbx_epoch
          FROM call_dispatch_queue q JOIN call_attempts a ON a.id = q.attempt_id
         WHERE q.ami_action_id = :aid"""), {"aid": action_id[:200]}).mappings().first()
    return dict(r) if r else None


def check_originate_response(entry: dict, row: dict) -> str | None:
    """Why an OriginateResponse with a registered ActionID is NOT the attempt's agent leg (None = it is)."""
    p = row["payload"]
    if p.get("Context") != entry["context"] or p.get("Exten") != entry["destination"]:
        return "originate_response_mismatch"
    if p.get("Response") == "Success":
        chan = row.get("channel") or ""
        if not re.fullmatch(rf"{re.escape(entry['driver'])}/{re.escape(entry['extension'] or '')}-[0-9a-fA-F]+", chan):
            return "originate_response_mismatch"
        if not UID_RE.match(row.get("uniqueid") or ""):
            return "originate_response_mismatch"
    elif p.get("Response") != "Failure":
        return "originate_response_mismatch"
    return None


def leg_owner(session, epoch, uniqueid):
    r = session.execute(text("SELECT attempt_id::text AS attempt_id, role FROM call_legs "
                             "WHERE pbx_epoch = :ep AND uniqueid = :u"), {"ep": epoch, "u": uniqueid}).mappings().first()
    return dict(r) if r else None


def agent_conflict(session, epoch, attempt_id, uniqueid) -> bool:
    """True when binding `uniqueid` as the agent leg of `attempt_id` would contradict what is known:
    the attempt has another agent leg, or the channel already belongs to something else."""
    from services.calling.legs import agent_uids
    others = [u for u in agent_uids(session, attempt_id, epoch) if u != uniqueid]
    if others:
        return True
    owner = leg_owner(session, epoch, uniqueid)
    if owner is None:
        return False
    if owner["attempt_id"] is not None and owner["attempt_id"] != str(attempt_id):
        return True
    return owner["role"] not in ("unknown", "agent")


def bind_agent_leg(session, epoch, uniqueid, attempt_id, *, channel=None, event_id=None) -> None:
    """Record `uniqueid` as the attempt's agent leg (idempotent; call agent_conflict() first)."""
    session.execute(text("""
        INSERT INTO call_legs (pbx_epoch, uniqueid, attempt_id, role, channel, first_event_id, last_event_id)
        VALUES (:ep, :u, CAST(:a AS uuid), 'agent', :ch, :eid, :eid)
        ON CONFLICT (pbx_epoch, uniqueid) DO UPDATE
           SET role = CASE WHEN call_legs.role = 'unknown' THEN 'agent' ELSE call_legs.role END,
               attempt_id = coalesce(call_legs.attempt_id, EXCLUDED.attempt_id),
               channel = coalesce(call_legs.channel, EXCLUDED.channel),
               first_event_id = coalesce(call_legs.first_event_id, EXCLUDED.first_event_id),
               last_event_id = greatest(call_legs.last_event_id, EXCLUDED.last_event_id)"""),
        {"ep": epoch, "u": uniqueid[:150], "a": str(attempt_id), "ch": (channel or None) and channel[:200],
         "eid": event_id})


def agi_agent_check(session, attempt_id, epoch, uniqueid) -> str | None:
    """FastAGI (legacy): the deny reason when the AGI channel cannot be the attempt's agent leg."""
    if not epoch or not UID_RE.match(uniqueid or ""):
        return "agent_channel_mismatch"
    return "agent_channel_mismatch" if agent_conflict(session, epoch, attempt_id, uniqueid) else None


# --------------------------------------------------------------------------- correlator
class LegacyCorrelator:
    """In-memory attribution state of one evidence consumer (its thread only)."""

    def __init__(self, *, pending_ttl=PENDING_TTL, max_pending=MAX_PENDING_UIDS, refresh_seconds=REFRESH_SECONDS):
        self.legs: collections.OrderedDict = collections.OrderedDict()   # (epoch, uid) -> (attempt, role)
        self.agents: dict = {}                                            # (epoch, attempt) -> agent uid
        self.pending: collections.OrderedDict = collections.OrderedDict()  # (epoch, uid) -> [(rx, row)]
        self.pending_ttl, self.max_pending, self.refresh_seconds = pending_ttl, max_pending, refresh_seconds
        self.dirty = False
        self._refreshed_at: dict = {}
        self._last_rx = decimal.Decimal(0)
        self.stats = collections.Counter()

    # ---- receipt clock ---------------------------------------------------------------
    def receipt_ts(self) -> decimal.Decimal:
        """Strictly increasing receipt time (µs), in the order frames are received."""
        now = decimal.Decimal(time.time_ns() // 1000) / decimal.Decimal(1000000)
        if now <= self._last_rx:
            now = self._last_rx + decimal.Decimal("0.000001")
        self._last_rx = now
        return now

    # ---- cache -----------------------------------------------------------------------
    def known(self, epoch, uid):
        return self.legs.get((epoch, uid)) if uid else None

    def learn(self, epoch, uid, attempt, role):
        if not uid or not attempt:
            return
        key = (epoch, uid)
        prev = self.legs.get(key)
        if prev is not None and prev[0] != attempt:
            return                                     # never re-assign a channel
        if prev is None or (prev[1] == "unknown" and role != "unknown"):
            self.legs[key] = (attempt, role)
            self.dirty = True
        if role == "agent":
            self.agents.setdefault((epoch, attempt), uid)
        while len(self.legs) > MAX_CACHE:
            (old_ep, old_uid), (old_a, old_role) = self.legs.popitem(last=False)
            if old_role == "agent" and self.agents.get((old_ep, old_a)) == old_uid:
                self.agents.pop((old_ep, old_a), None)

    def agent_of(self, epoch, attempt):
        return self.agents.get((epoch, attempt))

    def refresh(self, session, epoch, *, force=False) -> None:
        """Seed / refresh the cache from call_legs (bindings the FastAGI hook wrote, a restart)."""
        now = time.monotonic()
        if not force and now - self._refreshed_at.get(epoch, -1e9) < self.refresh_seconds:
            return
        self._refreshed_at[epoch] = now
        rows = session.execute(text("""
            SELECT l.uniqueid, l.attempt_id::text AS attempt_id, l.role
              FROM call_legs l JOIN call_attempts a ON a.id = l.attempt_id AND a.pbx_epoch = l.pbx_epoch
             WHERE l.pbx_epoch = :ep AND l.role IN ('agent', 'customer') AND a.mode <> 'manual'
               AND a.state = ANY(CAST(:st AS text[])) AND a.created_at > now() - interval '12 hours'
             ORDER BY l.first_event_id NULLS FIRST
             LIMIT 20000"""), {"ep": epoch, "st": list(TRACK_STATES)}).mappings().all()
        for r in rows:
            self.learn(epoch, r["uniqueid"], r["attempt_id"], r["role"])
        self.stats["refreshes"] += 1

    # ---- pending ---------------------------------------------------------------------
    def hold(self, epoch, row) -> None:
        uid = row.get("uniqueid")
        if not uid:
            return
        key = (epoch, uid)
        items = self.pending.setdefault(key, [])
        if any(r["dedupe_key"] == row["dedupe_key"] for _t, r in items):
            return
        if len(items) >= MAX_PENDING_PER_UID:
            self.stats["pending_dropped"] += 1
            return
        items.append((time.monotonic(), row))
        self.pending.move_to_end(key)
        self.stats["held"] += 1

    def due(self, epoch) -> list:
        """Held rows whose channel is now known (in receipt order), with their keys."""
        if not self.dirty:
            return []
        out = []
        for key, items in self.pending.items():
            if key[0] == epoch and key in self.legs:
                out.extend((key, r) for _t, r in items)
        out.sort(key=lambda kr: (kr[1]["pbx_ts"] is None, kr[1]["pbx_ts"] or 0))
        return out

    def expired(self) -> list:
        """Held rows past the TTL (or beyond the size bound), oldest first, with their keys."""
        now = time.monotonic()
        out = []
        over = len(self.pending) - self.max_pending
        for i, (key, items) in enumerate(self.pending.items()):
            if i < over or (items and now - items[0][0] > self.pending_ttl):
                out.extend((key, r) for _t, r in items)
            elif i >= over:
                break
        return out

    def drop(self, keys_rows) -> None:
        """After a commit: forget the held rows that were stored (or expired)."""
        for key, row in keys_rows:
            items = self.pending.get(key)
            if not items:
                continue
            items[:] = [(t, r) for t, r in items if r["dedupe_key"] != row["dedupe_key"]]
            if not items:
                self.pending.pop(key, None)

    # ---- attribution -----------------------------------------------------------------
    def attribute(self, session, row, epoch) -> tuple[str, dict | None]:
        """-> ("store", row-with-attempt) | ("untrusted", row) | ("hold", None) | ("drop", None).
        May bind the agent leg (OriginateResponse) or learn a customer leg (DialBegin)."""
        event, uid = row["event"], row.get("uniqueid")
        if event in LIFECYCLE_EVENTS:
            return "store", row
        if event == "OriginateResponse":
            entry = registry_entry(session, row.get("action_id"))
            if entry is None or entry["pbx_epoch"] != epoch:
                return "drop", None
            attempt = entry["attempt_id"]
            out = {**row, "attempt_id": attempt, "payload": {**row["payload"], "correlated_by": "action_id"}}
            why = check_originate_response(entry, row)
            if why is None and row["payload"].get("Response") == "Success":
                known = self.known(epoch, uid)
                if (known is not None and known != (attempt, "agent")) \
                        or agent_conflict(session, epoch, attempt, uid):
                    why = "agent_leg_conflict"
            if why is not None:
                out["payload"]["binding"] = f"refused:{why}"
                self.stats["binding_refused"] += 1
            elif row["payload"].get("Response") == "Success":
                out["bind_agent"] = uid
                self.learn(epoch, uid, attempt, "agent")
            out["agent_uid"] = self.agent_of(epoch, attempt)
            return "store", out
        if event == "UserEvent":
            claimed = row.get("claimed")
            if row.get("uid_conflict") or not claimed or not UUID_RE.match(claimed) or not UID_RE.match(uid or ""):
                return "untrusted", untrusted(row)
            known = self.known(epoch, uid)
            if known is None:
                return "hold", None
            if known[0] != claimed:
                return "untrusted", untrusted(row)
            return "store", {**row, "attempt_id": claimed, "agent_uid": self.agent_of(epoch, claimed)}
        if event not in CHANNEL_EVENTS:
            return "drop", None
        known = self.known(epoch, uid)
        if event == "DialBegin":
            dest = row.get("dest_uid")
            if known is not None and known[1] == "agent" and dest and dest != uid:
                self.learn(epoch, dest, known[0], "customer")
            elif known is None and dest:
                known = self.known(epoch, dest)
        elif event in ("BridgeEnter", "BridgeLeave") and known is None:
            known = self.known(epoch, row.get("peer_uid"))
        if known is None:
            return "hold", None
        return "store", {**row, "attempt_id": known[0], "agent_uid": self.agent_of(epoch, known[0])}
