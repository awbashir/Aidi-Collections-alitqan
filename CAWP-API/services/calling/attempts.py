"""Call attempts, reservations and one-shot dispatch tokens (migration 0049).

Not request-bound: the calling service (evidence consumer, FastAGI) and the P1 dispatcher use
these with an explicit actor. Nothing here commits.

Rules enforced here AND by the database guards:
* the lifecycle only moves forward (calling_attempt_allowed_next); step timestamps are set once;
* `uncertain` always carries a reason; reservations are released only by a saved wrap-up,
  by cancelling a never-dispatched attempt, or by a named reconciliation (P1). A restart,
  a lost AMI session or a timer never releases one: `mark_in_flight_uncertain` changes the
  attempt state only and leaves every reservation in place.
* a dispatch token is random (256 bits), stored as SHA-256 only, one per attempt, consumed
  once by the FastAGI hook, and expires within minutes.
"""
from __future__ import annotations

import hashlib
import secrets

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from core.api_response import ApiError
from services.calling.common import E164_RE, EXTENSION_RE, IN_FLIGHT, sess

STEP_COLUMN = {
    "authorized": "authorized_at", "dispatched": "dispatched_at", "agent_ringing": "agent_ringing_at",
    "agent_answered": "agent_answered_at", "customer_dialing": "customer_dialing_at",
    "customer_answered": "customer_answered_at", "bridged": "bridged_at", "ended": "ended_at", "failed": "ended_at",
}
CHANNEL_PREFIX = "cawp-"


def channel_id_for(attempt_id) -> str:
    """The app-issued AMI ChannelId (= the agent channel's Uniqueid) of an attempt."""
    return f"{CHANNEL_PREFIX}{attempt_id}"


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_attempt(*, mode, case_id, collector_user_id, created_by, state, contact_id=None, destination=None,
                   extension=None, campaign_id=None, started_at=None, ended_at=None, duration_seconds=None,
                   duration_source=None, transcript=None, direction="outbound") -> str:
    return str(sess().execute(text("""
        INSERT INTO call_attempts (mode, direction, state, case_id, contact_id, destination, collector_user_id,
                                   extension, campaign_id, created_by, started_at, ended_at, duration_seconds,
                                   duration_source, transcript)
        VALUES (:mode, :dir, :state, CAST(:case AS uuid), CAST(:contact AS uuid), :dest, CAST(:coll AS uuid),
                :ext, CAST(:camp AS uuid), CAST(:by AS uuid), :started, :ended, :dur, :dsrc, :transcript)
        RETURNING id"""), {
        "mode": mode, "dir": direction, "state": state, "case": str(case_id),
        "contact": str(contact_id) if contact_id else None, "dest": destination, "coll": str(collector_user_id),
        "ext": extension, "camp": str(campaign_id) if campaign_id else None, "by": str(created_by),
        "started": started_at, "ended": ended_at, "dur": duration_seconds, "dsrc": duration_source,
        "transcript": transcript}).scalar())


def lock_attempt(attempt_id):
    return sess().execute(text("SELECT * FROM call_attempts WHERE id = CAST(:id AS uuid) FOR UPDATE"),
                          {"id": str(attempt_id)}).mappings().first()


def bind_channel(attempt_id, pbx_epoch: str) -> None:
    """Record the app-issued channel id and the PBX epoch of the dispatch (set once)."""
    sess().execute(text("""
        UPDATE call_attempts SET pbx_channel_id = :ch, pbx_epoch = :ep
         WHERE id = CAST(:id AS uuid) AND pbx_channel_id IS NULL"""),
        {"ch": channel_id_for(attempt_id), "ep": pbx_epoch, "id": str(attempt_id)})


def transition(attempt_id, to_state: str, *, reason_code: str | None = None, at=None) -> dict:
    """Move an attempt forward. The database guard rejects anything not allowed."""
    col = STEP_COLUMN.get(to_state)
    sets = ["state = :state"]
    params = {"state": to_state, "id": str(attempt_id), "at": at, "reason": reason_code}
    if col:
        sets.append(f"{col} = coalesce({col}, coalesce(CAST(:at AS timestamptz), now()))")
    if reason_code is not None:
        sets.append("reason_code = :reason")
    row = sess().execute(text(f"UPDATE call_attempts SET {', '.join(sets)} WHERE id = CAST(:id AS uuid) "
                              "RETURNING id, state, revision"), params).mappings().first()
    if row is None:
        raise ApiError(404, "call_not_found", "call attempt not found", {"attempt_id": str(attempt_id)})
    return dict(row)


# --------------------------------------------------------------------------- reservations
_RESERVATION_INDEX = {
    "ux_call_reservations_active_collector": "collector",
    "ux_call_reservations_active_case": "case",
    "ux_call_reservations_active_extension": "extension",
}


def reserve(attempt_id) -> str:
    """One active reservation per collector, case and extension (P1/P2 call this at prepare)."""
    try:
        with sess().begin_nested():
            return str(sess().execute(text("""
                INSERT INTO call_reservations (attempt_id, collector_user_id, case_id, extension)
                SELECT id, collector_user_id, case_id, extension FROM call_attempts WHERE id = CAST(:id AS uuid)
                RETURNING id"""), {"id": str(attempt_id)}).scalar())
    except IntegrityError as e:
        name = getattr(getattr(e.orig, "diag", None), "constraint_name", None)
        which = _RESERVATION_INDEX.get(name)
        if which is None:
            raise
        raise ApiError(409, "calling_reserved", f"an active call already reserves this {which}",
                       {"reserved": which}) from None


def release_reservation(attempt_id, source: str, *, by=None, reason=None) -> bool:
    row = sess().execute(text("""
        UPDATE call_reservations
           SET released_at = now(), release_source = :src, released_by = CAST(:by AS uuid), release_reason = :why
         WHERE attempt_id = CAST(:id AS uuid) AND released_at IS NULL RETURNING id"""),
        {"src": source, "by": str(by) if by else None, "why": reason, "id": str(attempt_id)}).first()
    return row is not None


def active_reservations() -> list[dict]:
    return [dict(r) for r in sess().execute(text("""
        SELECT r.id, r.attempt_id, r.collector_user_id, r.case_id, r.extension, r.reserved_at, a.state
          FROM call_reservations r JOIN call_attempts a ON a.id = r.attempt_id
         WHERE r.released_at IS NULL ORDER BY r.reserved_at""")).mappings()]


# --------------------------------------------------------------------------- tokens
def issue_dispatch_token(attempt_id, *, context: str, destination: str, extension: str, max_call_seconds: int,
                         pbx_epoch_uuid: str | None = None, ttl_seconds: int = 45) -> tuple[str, str]:
    """-> (plaintext token, token row id). The plaintext goes only into the Originate
    variables of the one dispatch; only its hash is stored. P1 calls this at dispatch."""
    if not E164_RE.match(destination or "") or not EXTENSION_RE.match(extension or ""):
        raise ValueError("destination must be E.164 and extension 2-6 digits")
    if not 5 <= int(ttl_seconds) <= 600:
        raise ValueError("ttl_seconds must be 5..600")
    token = secrets.token_hex(32)
    row_id = sess().execute(text("""
        INSERT INTO call_dispatch_tokens (attempt_id, token_sha256, channel_id, context, destination, extension,
                                          max_call_seconds, pbx_epoch_uuid, expires_at)
        VALUES (CAST(:a AS uuid), :h, :ch, :ctx, :dest, :ext, :lim, :ep, now() + make_interval(secs => :ttl))
        RETURNING id"""), {"a": str(attempt_id), "h": token_hash(token), "ch": channel_id_for(attempt_id),
                           "ctx": context, "dest": destination, "ext": extension, "lim": int(max_call_seconds),
                           "ep": pbx_epoch_uuid, "ttl": int(ttl_seconds)}).scalar()
    return token, str(row_id)


# --------------------------------------------------------------------------- observation loss
def mark_in_flight_uncertain(reason: str, *, epoch: str | None = None, other_than_epoch: str | None = None) -> list[str]:
    """Observation was lost (AMI session closed, PBX restarted, calling service stopped):
    every in-flight attempt dispatched on that epoch becomes `uncertain` with `reason`.
    Reservations are NOT touched. `epoch` limits to attempts dispatched on that epoch; `other_than_epoch`
    selects attempts dispatched on any other epoch (a PBX restart)."""
    # Only attempts bound to a PBX process (pbx_epoch set at dispatch) were ever observed;
    # an attempt that was never sent keeps its state.
    where = ["state = ANY(CAST(:states AS text[]))", "pbx_epoch IS NOT NULL"]
    params = {"states": list(IN_FLIGHT), "reason": reason}
    if epoch is not None:
        where.append("pbx_epoch = :ep")
        params["ep"] = epoch
    if other_than_epoch is not None:
        where.append("pbx_epoch <> :other")
        params["other"] = other_than_epoch
    rows = sess().execute(text(f"""
        UPDATE call_attempts SET state = 'uncertain', reason_code = :reason
         WHERE {' AND '.join(where)} AND mode <> 'manual'
        RETURNING id"""), params).all()
    return [str(r[0]) for r in rows]
