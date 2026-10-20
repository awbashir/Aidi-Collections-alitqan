"""Server-controlled call control (customer request 2026-10-20; migration 0075).

    transfer_blind     the agent's peer (the customer) is sent to another app user's extension
    transfer_attended  the customer waits on hold while the agent consults the target, then the
                       agent completes (customer + target, agent leaves) or cancels (agent + customer)
    conference         agent + customer move into a ConfBridge room, another extension is invited;
                       the agent can remove the participant again, or leave (hang up)
    spy                a supervisor listens to an agent's live call (ChanSpy), whispers to the agent
                       or barges in (everyone hears)

Why server-controlled: every PJSIP endpoint keeps `allow_transfer = no` (the click-to-call gate
refuses to dial from an endpoint that can REFER, and a browser REFER would bypass the app). The
app decides who may do what; the PBX only receives AMI actions from the calling service, into
the cawp-callctl-* contexts (services/calling/callctl_dialplan.py). Targets are app users'
internal extensions only (an external number would bypass caller-ID rotation, the calling
window and the case rules).

Handoff (the API never touches AMI; the calling service is the only AMI owner), like
services/calling/dispatch.py:

    API      validates the request against the live channel snapshot (pbx_live_channels; stale =
             503 `live_calls_unknown`, never a guess), writes call_controls + a call_control_ops
             row for the current PBX epoch (15 s to claim) and an audit event. 503
             transport_not_connected without a connected calling service.
    service  ControlWorker (poll 0.25 s): claims the oldest queued op FOR UPDATE SKIP LOCKED,
             reads the PBX channel list fresh (CoreShowChannels), re-checks that the call is still
             the one the API saw (same channels, same bridge), marks the op `sending`, COMMITs,
             then sends the planned AMI actions in order (stop at the first Error):
                 all Success -> `done`        (SUBMITTED to the PBX, not proven)
                 an Error    -> `rejected`
                 lost answer -> `unacknowledged` (the PBX state is unknown; nothing is re-sent)
             Every 3 s, while controls are open, it reads the channel list again (sweep):
                 * a consultation whose target is gone (declined, no answer, hung up) returns the
                   customer to the agent (an automatic `cancel`);
                 * a conference whose participant is gone is `completed` (participant_gone);
                 * a customer left alone in a room is released (`release`: post-call context);
                 * a listening supervisor whose channel is gone is `ended`;
                 * a control none of whose channels is left gets `ended_at` (state `ended` if it
                   was still active).

PBX mechanics (Asterisk 18; docs/issabel-commissioning.md §3b):
    blind      Setvar CAWP_CTL_ID/CAWP_CTL_FROM on the peer, CAWP_CTL_ID on the agent; ONE Redirect
               of both (dual redirect: the bridge never hangs up the other leg): the peer to
               cawp-callctl-xfer,<target>,1 (Dial with the X-CAWP-Control header, the agent rung
               back once when the target does not answer), the agent to cawp-callctl-done (Hangup).
    attended   Setvar room/holder; Redirect peer -> cawp-callctl-hold (music on hold), agent ->
               room; Originate the target's extension into the room (ChannelId cawpt-<id>,
               X-CAWP-Control header). complete: Redirect peer -> room, agent -> done.
               cancel: Hangup the target leg, Redirect peer -> room (agent + customer again).
    conference Setvar room on both; Redirect both into the room; Originate the invitee into it.
               remove/cancel: Hangup the invitee leg.
    spy        Originate the SUPERVISOR's own extension into cawp-callctl-spy,<mode> (ChanSpy of the
               exact agent channel, options q d E u [+w|B], ChannelId cawps-<id>, X-CAWP-Control
               header: the softphone auto-answers its own listen call). mode: PlayDTMF Receive
               4/5/6 on the supervisor's channel (ChanSpy's d option). stop: Hangup that channel.

Click-to-call attempts: the ORIGINAL collector keeps the attempt and its wrap-up. The dialplan
emits CollectionsCall UserEvents on the attempt (transfer_blind, transferred, customer_on_hold,
conference); the projection ends the attempt (`transferred` / `agent_hung_up`) once the agent's
leg is gone and the customer was handed over, so the collector can wrap up while the target
talks. The receiving user sees the case link (when the case is in their scope) on the ringing
line, and makes their own notes on the case.

Permissions and scope: transfers and conferences need calls_make and act only on the caller's
own call (their extension's channel). Listening needs calls_supervise or calls_manage, the
caller's own extension, and the agent's extension in the monitor scope (SuperAdmin every
extension, a supervisor their team's: services/calling/monitor.scope); never one's own call.
Every request, command and service action is audited.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
import uuid

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from core import audit
from core.api_response import NOT_FOUND, VALIDATION_FAILED, ApiError
from services.calling.common import (CALLS_MAKE, SUPERVISE_KEYS, USER_NAME_SQL, iso, me, parse_uuid, sess,
                                     valid_uuid)

log = logging.getLogger("cawp.calling.control")

KINDS = ("transfer_blind", "transfer_attended", "conference", "spy")
MODES = ("listen", "whisper", "barge")
SPY_DIGIT = {"listen": "4", "whisper": "5", "barge": "6"}
OPEN_STATES = ("queued", "active")
COMMANDS = {                      # op -> kinds it applies to (the requester only)
    "complete": ("transfer_attended",),
    "cancel": ("transfer_attended", "conference"),
    "remove": ("conference",),
    "mode": ("spy",),
    "stop": ("spy",),
}
HEADER = "X-CAWP-Control"
OP_TTL_SECONDS = 15
INVITE_RING_SECONDS = 30
SNAPSHOT_FRESH_SECONDS = 15
EXT_RE = re.compile(r"^[0-9]{2,6}$")
HEX_RE = re.compile(r"^[0-9a-f]{32}$")
AGENT_CHANNEL_RE = re.compile(r"^PJSIP/([0-9]{2,6})-[0-9a-f]{8}$")
CTX = {"xfer": "cawp-callctl-xfer", "done": "cawp-callctl-done", "hold": "cawp-callctl-hold",
       "room": "cawp-callctl-room", "spy": "cawp-callctl-spy", "post": "cawp-callctl-post"}


class Refused(Exception):
    """A plan cannot be sent (the call changed); `code` is stored as the op result."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def hexid(control_id) -> str:
    return uuid.UUID(str(control_id)).hex


def parse_tag(value) -> str | None:
    """The control id of an X-CAWP-Control value (32 hex or a UUID), or None."""
    if not isinstance(value, str):
        return None
    v = value.strip().lower()
    if HEX_RE.match(v):
        return str(uuid.UUID(v))
    return valid_uuid(v) if re.fullmatch(r"[0-9a-f-]{36}", v) else None


def room_of(control_id) -> str:
    return f"cawp-{hexid(control_id)}"


def leg_id(kind: str, control_id) -> str:
    """ChannelId of the leg the service originates (never `cawp-<uuid>`: not an attempt channel)."""
    return f"{'cawps' if kind == 'spy' else 'cawpt'}-{hexid(control_id)}"


def tag_field(control_id) -> tuple[str, str]:
    return ("Variable", f"PJSIP_HEADER(add,{HEADER})={hexid(control_id)}")


# --------------------------------------------------------------------------- channel lists
def channel_map(frames) -> dict[str, dict]:
    """CoreShowChannel frames (or dicts) -> {uniqueid: channel facts}."""
    out = {}
    for f in frames or ():
        get = f.get
        uid = (get("Uniqueid") or "").strip()
        if not uid or uid in out:
            continue
        out[uid] = {
            "uniqueid": uid, "channel": get("Channel") or "", "linkedid": get("Linkedid") or uid,
            "bridge_id": get("BridgeId") or None, "application": get("Application") or "",
            "appdata": get("ApplicationData") or "", "state": get("ChannelStateDesc") or "",
            "context": get("Context") or "", "exten": get("Exten") or ""}
    return out


def in_room(ch: dict | None, room: str | None) -> bool:
    return bool(ch and room and ch["application"] == "ConfBridge" and ch["appdata"].split(",")[0] == room)


# --------------------------------------------------------------------------- plans (pure)
def _setvar(channel, name, value):
    return ("Setvar", [("Channel", channel), ("Variable", name), ("Value", value)])


def _redirect(channel, context, exten, extra=None):
    fields = [("Channel", channel), ("Context", context), ("Exten", exten), ("Priority", "1")]
    if extra:
        fields += [("ExtraChannel", extra[0]), ("ExtraContext", extra[1]), ("ExtraExten", extra[2]),
                   ("ExtraPriority", "1")]
    return ("Redirect", fields)


def _hangup(channel):
    return ("Hangup", [("Channel", channel), ("Cause", "16")])


def _invite(c: dict):
    """Originate the target's own extension into the control's room."""
    return ("Originate", [
        ("Channel", f"PJSIP/{c['target_extension']}"), ("Context", CTX["room"]), ("Exten", "invitee"),
        ("Priority", "1"), ("Async", "true"), ("Timeout", str(INVITE_RING_SECONDS * 1000)),
        ("ChannelId", c["target_uniqueid"]), ("CallerID", f"\"{c['from_extension']}\" <{c['from_extension']}>"),
        ("Variable", f"CAWP_CTL_ROOM={c['room']}"), ("Variable", f"CAWP_CTL_ID={hexid(c['id'])}"),
        tag_field(c["id"])])


def _call_legs(c: dict, chans: dict) -> tuple[dict, dict]:
    """The agent's channel and its peer, still as the API saw them (same bridge)."""
    agent, peer = chans.get(c["agent_uniqueid"] or ""), chans.get(c["customer_uniqueid"] or "")
    if agent is None:
        raise Refused("agent_gone")
    m = AGENT_CHANNEL_RE.match(agent["channel"])
    if not m or m.group(1) != c["from_extension"]:
        raise Refused("call_changed")
    if peer is None:
        raise Refused("customer_gone")
    if not agent["bridge_id"] or peer["bridge_id"] != agent["bridge_id"]:
        raise Refused("call_changed")
    return agent, peer


def plan(c: dict, op: dict, chans: dict) -> tuple[list, dict]:
    """(AMI actions, control changes on success) for one op; Refused when the call changed."""
    kind, name, cid = c["kind"], op["op"], hexid(c["id"])
    target = chans.get(c["target_uniqueid"] or "")
    if name == "start" and kind == "spy":
        agent = chans.get(c["agent_uniqueid"] or "")
        if agent is None:
            raise Refused("agent_gone")
        m = AGENT_CHANNEL_RE.match(agent["channel"])
        if not m or m.group(1) != c["target_extension"]:
            raise Refused("call_changed")
        if not agent["bridge_id"]:
            raise Refused("call_not_connected")
        return [("Originate", [
            ("Channel", f"PJSIP/{c['from_extension']}"), ("Context", CTX["spy"]), ("Exten", c["mode"]),
            ("Priority", "1"), ("Async", "true"), ("Timeout", str(INVITE_RING_SECONDS * 1000)),
            ("ChannelId", c["target_uniqueid"]), ("CallerID", f"\"{c['target_extension']}\" <{c['target_extension']}>"),
            ("Variable", f"CAWP_SPY_TARGET={agent['channel']}"), ("Variable", f"CAWP_CTL_ID={cid}"),
            tag_field(c["id"])])], {"state": "active", "agent_channel": agent["channel"]}
    if name == "start":
        agent, peer = _call_legs(c, chans)
        names = {"agent_channel": agent["channel"], "customer_channel": peer["channel"]}
        if kind == "transfer_blind":
            return [_setvar(peer["channel"], "CAWP_CTL_ID", cid),
                    _setvar(peer["channel"], "CAWP_CTL_FROM", c["from_extension"]),
                    _setvar(agent["channel"], "CAWP_CTL_ID", cid),
                    _redirect(peer["channel"], CTX["xfer"], c["target_extension"],
                              (agent["channel"], CTX["done"], "s"))], \
                {"state": "completed", "result_code": "submitted", **names}
        if kind == "transfer_attended":
            return [_setvar(peer["channel"], "CAWP_CTL_ID", cid),
                    _setvar(peer["channel"], "CAWP_CTL_ROOM", c["room"]),
                    _setvar(peer["channel"], "CAWP_CTL_HOLDER", agent["uniqueid"]),
                    _setvar(agent["channel"], "CAWP_CTL_ID", cid),
                    _setvar(agent["channel"], "CAWP_CTL_ROOM", c["room"]),
                    _redirect(peer["channel"], CTX["hold"], "s", (agent["channel"], CTX["room"], "agent")),
                    _invite(c)], {"state": "active", **names}
        if kind == "conference":
            return [_setvar(peer["channel"], "CAWP_CTL_ID", cid),
                    _setvar(peer["channel"], "CAWP_CTL_ROOM", c["room"]),
                    _setvar(agent["channel"], "CAWP_CTL_ID", cid),
                    _setvar(agent["channel"], "CAWP_CTL_ROOM", c["room"]),
                    _redirect(peer["channel"], CTX["room"], "customer", (agent["channel"], CTX["room"], "agent")),
                    _invite(c)], {"state": "active", **names}
    agent = chans.get(c["agent_uniqueid"] or "")
    peer = chans.get(c["customer_uniqueid"] or "")
    if kind == "transfer_attended" and name == "complete":
        if agent is None:
            raise Refused("agent_gone")
        if peer is None:
            raise Refused("customer_gone")
        if peer["context"] != CTX["hold"]:
            raise Refused("call_changed")
        if target is None or target["state"] != "Up" or not in_room(target, c["room"]):
            raise Refused("target_not_answered")
        return [_redirect(peer["channel"], CTX["room"], "customer", (agent["channel"], CTX["done"], "s"))], \
            {"state": "completed", "result_code": "completed"}
    if kind == "transfer_attended" and name == "cancel":
        actions = [_hangup(target["channel"])] if target is not None else []
        if peer is not None and agent is not None and peer["context"] == CTX["hold"]:
            actions.append(_redirect(peer["channel"], CTX["room"], "customer"))
        return actions, {"state": "cancelled", "result_code": "cancelled" if op.get("requested_by") else "target_gone"}
    if kind == "conference" and name in ("remove", "cancel"):
        return ([_hangup(target["channel"])] if target is not None else []), \
            {"state": "completed", "result_code": "participant_removed"}
    if kind == "spy" and name == "mode":
        if target is None:
            raise Refused("listener_gone")
        if target["application"] != "ChanSpy":
            raise Refused("not_listening_yet")
        return [("PlayDTMF", [("Channel", target["channel"]), ("Digit", SPY_DIGIT[op["mode"]]), ("Receive", "yes")])], \
            {"mode": op["mode"]}
    if kind == "spy" and name == "stop":
        return ([_hangup(target["channel"])] if target is not None else []), \
            {"state": "ended", "result_code": "stopped"}
    if name == "release":
        if peer is None or not in_room(peer, c["room"]):
            raise Refused("customer_not_alone")
        return [_redirect(peer["channel"], CTX["post"], "s")], {}
    raise Refused("op_not_applicable")


def sweep_decisions(c: dict, chans: dict, absent: dict, age: float) -> list[tuple[str, str | None]]:
    """Pure: what the sweep does for one open control. `absent` counts consecutive sweeps a leg
    was missing (key -> n, updated here). Returns [("op", name) | ("set", result) | ("ended", None)]."""
    out = []
    agent = chans.get(c["agent_uniqueid"] or "")
    peer = chans.get(c["customer_uniqueid"] or "")
    target = chans.get(c["target_uniqueid"] or "")

    def missing(key, gone) -> bool:
        absent[key] = absent.get(key, 0) + 1 if gone else 0
        return absent[key] >= 2

    present = [x for x in (agent, peer, target) if x is not None]
    if missing("all", not present) and age > 5:
        return [("ended", None)]
    if c["state"] == "active" and age > 3:
        if c["kind"] == "transfer_attended" and agent is None and in_room(peer, c.get("room")) \
                and missing("agent", True):
            # the agent hung up while consulting and the target was in the room: the customer joined it
            out.append(("set", "agent_left_consultation"))
        elif c["kind"] == "transfer_attended" and missing("target", target is None):
            if peer is not None and agent is not None and peer["context"] == CTX["hold"]:
                out.append(("op", "cancel"))
            else:
                out.append(("set", "target_gone"))
        elif c["kind"] == "conference" and missing("target", target is None):
            out.append(("set", "participant_gone"))
        elif c["kind"] == "spy" and missing("target", target is None):
            out.append(("ended", None))
    if c.get("room") and peer is not None and in_room(peer, c["room"]):
        others = [x for x in chans.values() if x["uniqueid"] != peer["uniqueid"] and in_room(x, c["room"])]
        ringing = c["state"] == "active" and target is not None and not in_room(target, c["room"])
        if missing("alone", not others and not ringing):
            out.append(("op", "release"))
    return out


# --------------------------------------------------------------------------- API: reads
def _snapshot() -> tuple[bool, list[dict]]:
    snap = sess().execute(text("""
        SELECT ok, extract(epoch FROM now() - captured_at) AS age FROM pbx_live_snapshot WHERE id = 1""")).first()
    fresh = snap is not None and bool(snap[0]) and float(snap[1]) <= SNAPSHOT_FRESH_SECONDS
    rows = [dict(r) for r in sess().execute(text("SELECT * FROM pbx_live_channels")).mappings()] if fresh else []
    return fresh, rows


def _require_snapshot():
    fresh, rows = _snapshot()
    if not fresh:
        raise ApiError(503, "live_calls_unknown", "the PBX channel list is not current; try again in a moment")
    return rows


def _transport():
    from services.calling import rules
    status = rules.transport_status()
    if status is None:
        raise ApiError(503, "transport_not_connected", "the calling service is not connected to the PBX")
    return status


def _my_extension(user_id=None) -> str | None:
    from services.calling import extensions as X
    row = X.active_for_user(user_id or me())
    return row["extension"] if row is not None else None


def _user_ext_owner(ext: str):
    return sess().execute(text(f"""
        SELECT x.user_id::text AS user_id, {USER_NAME_SQL.format(a='u')} AS name, u.is_active
          FROM user_extensions x JOIN users u ON u.id = x.user_id
         WHERE x.extension = :e AND x.released_at IS NULL"""), {"e": ext}).mappings().first()


def _presence(rows) -> dict[str, str]:
    out: dict[str, str] = {}
    for r in rows:
        ext = r.get("extension")
        if not ext:
            continue
        if r.get("bridge_id"):
            out[ext] = "on_call"
        elif (r.get("state") or "") in ("Ring", "Ringing"):
            out.setdefault(ext, "ringing")
        else:
            out.setdefault(ext, "busy")
    return out


def capabilities() -> dict:
    from core.auth import has_permission
    ext = _my_extension()
    make = has_permission(CALLS_MAKE)
    supervise = any(has_permission(k) for k in SUPERVISE_KEYS)
    return {"extension": ext, "transfer": bool(make and ext), "conference": bool(make and ext),
            "listen": bool(supervise and ext), "modes": list(MODES) if supervise and ext else [],
            "targets": "app_user_extensions_only"}


def targets(args) -> dict:
    """App users with an active extension (not me), with presence from the live snapshot."""
    q = (args.get("q") or "").strip()[:80]
    fresh, rows = _snapshot()
    presence = _presence(rows)
    mine = _my_extension()
    sql = f"""
        SELECT x.extension, x.user_id::text AS user_id, {USER_NAME_SQL.format(a='u')} AS name, r.name AS role
          FROM user_extensions x JOIN users u ON u.id = x.user_id LEFT JOIN roles r ON r.id = u.role_id
         WHERE x.released_at IS NULL AND u.is_active AND x.user_id <> CAST(:me AS uuid)"""
    params = {"me": me()}
    if q:
        sql += f" AND (x.extension LIKE :qp OR {USER_NAME_SQL.format(a='u')} ILIKE :ql)"
        params.update(qp=q + "%", ql="%" + q.replace("%", "").replace("_", "") + "%")
    sql += " ORDER BY length(x.extension), x.extension LIMIT 200"
    items = [{"extension": r["extension"], "user_id": r["user_id"], "name": r["name"], "role": r["role"],
              "presence": (presence.get(r["extension"], "free") if fresh else "unknown")}
             for r in sess().execute(text(sql), params).mappings()]
    return {"items": [i for i in items if i["extension"] != mine], "presence_known": fresh,
            "note": "internal extensions of app users only; presence comes from the PBX channel list"}


def _peer(rows, agent) -> list[dict]:
    if not agent.get("bridge_id"):
        return []
    return [r for r in rows if r["uniqueid"] != agent["uniqueid"] and r.get("bridge_id") == agent["bridge_id"]]


def _digits(v) -> str:
    return re.sub(r"\D", "", v or "")


def find_my_call(ext: str, rows: list[dict], *, attempt_id=None, remote=None) -> tuple[dict, dict]:
    """The caller's own connected call: (agent channel row, peer row). One peer only."""
    mine = [r for r in rows if r.get("extension") == ext and r.get("bridge_id")
            and AGENT_CHANNEL_RE.match(r.get("channel") or "")]
    if attempt_id:
        mine = [r for r in mine if r["uniqueid"] == f"cawp-{attempt_id}"]
    if len(mine) > 1 and remote:
        want = _digits(remote)[-9:]
        by_remote = []
        for r in mine:
            nums = {_digits(p.get("caller_num"))[-9:] for p in _peer(rows, r)} | \
                   {_digits(p.get("connected_num"))[-9:] for p in _peer(rows, r)} | {_digits(r.get("connected_num"))[-9:]}
            if want and want in nums:
                by_remote.append(r)
        mine = by_remote or mine
    if not mine:
        raise ApiError(409, "no_connected_call", "you have no connected call on your extension right now")
    if len(mine) > 1:
        raise ApiError(409, "ambiguous_call", "you have more than one connected call; end or switch the others first")
    agent = mine[0]
    peers = _peer(rows, agent)
    if len(peers) != 1:
        raise ApiError(409, "call_not_two_party", "call control works on a two-party call only",
                       {"parties": len(peers) + 1})
    return agent, peers[0]


def _attempt_of(agent_uniqueid: str):
    m = re.fullmatch(r"cawp-([0-9a-f-]{36})", agent_uniqueid or "")
    if not m:
        return None
    return sess().execute(text("""
        SELECT id::text AS id, case_id::text AS case_id, collector_user_id::text AS collector
          FROM call_attempts WHERE id = CAST(:a AS uuid)"""), {"a": m.group(1)}).mappings().first()


def _target(body, own_ext: str) -> tuple[str, dict]:
    ext = body.get("target_extension")
    if not isinstance(ext, str) or not EXT_RE.match(ext):
        raise ApiError(422, VALIDATION_FAILED, "target_extension must be an internal extension (2-6 digits)",
                       {"target_extension": "invalid"})
    if ext == own_ext:
        raise ApiError(422, VALIDATION_FAILED, "that is your own extension", {"target_extension": "own_extension"})
    owner = _user_ext_owner(ext)
    if owner is None or not owner["is_active"]:
        raise ApiError(422, "target_not_app_user", "only the extension of an active app user can be a target",
                       {"target_extension": "not_app_user"})
    return ext, owner


def _unknown(body, allowed):
    unknown = set(body) - set(allowed)
    if unknown:
        raise ApiError(422, VALIDATION_FAILED, f"unknown fields: {', '.join(sorted(unknown))}",
                       {k: "unknown" for k in unknown})


def _insert_control(row: dict) -> str:
    cols = list(row)
    try:
        with sess().begin_nested():
            return str(sess().execute(text(f"""
                INSERT INTO call_controls ({', '.join(cols)})
                VALUES ({', '.join(('CAST(:' + k + ' AS uuid)') if k in ('id', 'requested_by', 'target_user_id', 'attempt_id', 'case_id') else ':' + k for k in cols)})
                RETURNING id"""), row).scalar())
    except IntegrityError:
        raise ApiError(409, "control_in_progress",
                       "you already have a transfer, conference or listening in progress; finish it first") from None


def _insert_op(control_id, op: str, epoch: str, *, mode=None, by=None, session=None) -> str:
    s = session or sess()
    try:
        with s.begin_nested():
            return str(s.execute(text("""
                INSERT INTO call_control_ops (control_id, op, mode, requested_by, expected_epoch, expires_at)
                VALUES (CAST(:c AS uuid), :op, :mode, CAST(:by AS uuid), :ep, now() + make_interval(secs => :ttl))
                RETURNING id"""), {"c": str(control_id), "op": op, "mode": mode, "by": by, "ep": epoch,
                                   "ttl": OP_TTL_SECONDS}).scalar())
    except IntegrityError:
        raise ApiError(409, "control_busy", "the previous command of this call control is still being sent") from None


def request_call_control(kind: str, body: dict) -> str:
    """POST /v2/calling/control/transfers | /conferences: the caller's own connected call."""
    _unknown(body, ("mode", "target_extension", "attempt_id", "remote") if kind != "conference"
             else ("target_extension", "attempt_id", "remote"))
    if kind != "conference":
        mode = body.get("mode")
        if mode not in ("blind", "attended"):
            raise ApiError(422, VALIDATION_FAILED, "mode must be blind or attended", {"mode": "invalid"})
        kind = f"transfer_{mode}"
    attempt_id = parse_uuid(body["attempt_id"], "attempt_id") if body.get("attempt_id") else None
    remote = body.get("remote") if isinstance(body.get("remote"), str) else None
    own = _my_extension()
    if own is None:
        raise ApiError(409, "no_extension", "you have no extension")
    ext, owner = _target(body, own)
    status = _transport()
    rows = _require_snapshot()
    agent, peer = find_my_call(own, rows, attempt_id=attempt_id, remote=remote)
    attempt = _attempt_of(agent["uniqueid"])
    if attempt is not None and attempt["collector"] != me():
        raise ApiError(409, "not_your_call", "this call belongs to another collector's attempt")
    cid = str(uuid.uuid4())
    row = {"id": cid, "kind": kind, "requested_by": me(), "from_extension": own, "target_extension": ext,
           "target_user_id": owner["user_id"], "attempt_id": attempt["id"] if attempt else None,
           "case_id": attempt["case_id"] if attempt else None, "linkedid": agent.get("linkedid"),
           "agent_uniqueid": agent["uniqueid"], "agent_channel": agent["channel"],
           "customer_uniqueid": peer["uniqueid"], "customer_channel": peer["channel"],
           "target_uniqueid": leg_id(kind, cid) if kind != "transfer_blind" else None,
           "room": room_of(cid) if kind != "transfer_blind" else None, "pbx_epoch": status["epoch"]}
    _insert_control(row)
    op = _insert_op(cid, "start", status["epoch"], by=me())
    audit.record(f"call.control.{kind}.request", "call_control", cid, case_id=row["case_id"], after={
        "kind": kind, "from_extension": own, "target_extension": ext, "target_user_id": owner["user_id"],
        "attempt_id": row["attempt_id"], "linkedid": row["linkedid"], "agent_channel": agent["channel"],
        "peer_channel": peer["channel"], "op_id": op, "pbx_epoch": status["epoch"]})
    return cid


def request_spy(body: dict) -> str:
    """POST /v2/calling/control/spy {call_id, extension, mode}: listen to an agent in scope."""
    from services.calling import monitor
    _unknown(body, ("call_id", "extension", "mode"))
    mode = body.get("mode") or "listen"
    if mode not in MODES:
        raise ApiError(422, VALIDATION_FAILED, "mode must be listen, whisper or barge", {"mode": "invalid"})
    ext = body.get("extension")
    if not isinstance(ext, str) or not EXT_RE.match(ext):
        raise ApiError(422, VALIDATION_FAILED, "extension must be the agent's extension", {"extension": "invalid"})
    call_id = body.get("call_id")
    if not isinstance(call_id, str) or not 1 <= len(call_id) <= 150:
        raise ApiError(422, VALIDATION_FAILED, "call_id must be a live call id", {"call_id": "invalid"})
    own = _my_extension()
    if own is None:
        raise ApiError(409, "no_extension", "listening rings your own extension: you have none")
    if ext == own:
        raise ApiError(422, VALIDATION_FAILED, "that is your own call", {"extension": "own_extension"})
    sc = monitor.scope(me())
    if sc["extensions"] is not None and ext not in sc["extensions"]:
        raise ApiError(403, "not_in_scope", "this extension is not in your scope", {"extension": "not_in_scope"})
    owner = _user_ext_owner(ext)
    status = _transport()
    rows = _require_snapshot()
    agent = next((r for r in rows if r.get("extension") == ext and (r.get("linkedid") or r["uniqueid"]) == call_id
                  and AGENT_CHANNEL_RE.match(r.get("channel") or "")), None)
    if agent is None:
        raise ApiError(409, "call_not_live", "this call is no longer live on that extension")
    if not agent.get("bridge_id"):
        raise ApiError(409, "call_not_connected", "the agent is not connected to anyone yet")
    attempt = _attempt_of(agent["uniqueid"])
    cid = str(uuid.uuid4())
    row = {"id": cid, "kind": "spy", "mode": mode, "requested_by": me(), "from_extension": own,
           "target_extension": ext, "target_user_id": owner["user_id"] if owner else None,
           "attempt_id": attempt["id"] if attempt else None, "case_id": attempt["case_id"] if attempt else None,
           "linkedid": agent.get("linkedid"), "agent_uniqueid": agent["uniqueid"], "agent_channel": agent["channel"],
           "target_uniqueid": leg_id("spy", cid), "pbx_epoch": status["epoch"]}
    _insert_control(row)
    op = _insert_op(cid, "start", status["epoch"], mode=mode, by=me())
    audit.record("call.control.spy.request", "call_control", cid, case_id=row["case_id"], after={
        "mode": mode, "supervisor_extension": own, "agent_extension": ext, "agent_user_id": row["target_user_id"],
        "linkedid": row["linkedid"], "agent_channel": agent["channel"], "attempt_id": row["attempt_id"],
        "scope": sc["kind"], "op_id": op, "pbx_epoch": status["epoch"]})
    return cid


def _lock_control(control_id):
    r = sess().execute(text("SELECT * FROM call_controls WHERE id = CAST(:id AS uuid) FOR UPDATE"),
                       {"id": str(control_id)}).mappings().first()
    if r is None:
        raise ApiError(404, NOT_FOUND, "call control not found", {"control_id": str(control_id)})
    return dict(r)


def command(control_id, op: str, body: dict) -> str:
    """POST /v2/calling/control/<id>/<op>: complete | cancel | remove | mode | stop (requester only)."""
    _unknown(body, ("mode",) if op == "mode" else ())
    if op not in COMMANDS:
        raise ApiError(404, NOT_FOUND, "unknown command", {"op": op})
    c = _lock_control(control_id)
    if str(c["requested_by"]) != me():
        raise ApiError(404, NOT_FOUND, "call control not found", {"control_id": str(control_id)})
    if c["kind"] not in COMMANDS[op]:
        raise ApiError(409, "op_not_applicable", f"{op} does not apply to a {c['kind']}", {"kind": c["kind"]})
    if c["state"] != "active":
        raise ApiError(409, "control_not_active", "this call control is not active any more", {"state": c["state"]})
    mode = None
    if op == "mode":
        mode = body.get("mode")
        if mode not in MODES:
            raise ApiError(422, VALIDATION_FAILED, "mode must be listen, whisper or barge", {"mode": "invalid"})
    status = _transport()
    op_id = _insert_op(c["id"], op, status["epoch"], mode=mode, by=me())
    audit.record(f"call.control.{c['kind']}.{op}", "call_control", str(c["id"]),
                 case_id=str(c["case_id"]) if c["case_id"] else None,
                 before={"state": c["state"], "mode": c["mode"]},
                 after={"op": op, "op_id": op_id, "mode": mode, "target_extension": c["target_extension"]})
    return str(c["id"])


def _phase(c: dict, rows: list[dict] | None) -> dict:
    """What the PBX shows now for the control's legs (unknown without a current snapshot)."""
    if rows is None:
        return {"known": False}
    by = {r["uniqueid"]: r for r in rows}

    def leg(uid):
        r = by.get(uid or "")
        if r is None:
            return "gone"
        if (r.get("state") or "") in ("Ring", "Ringing"):
            return "ringing"
        return "up"
    out = {"known": True, "agent": leg(c["agent_uniqueid"])}
    if c["kind"] != "spy":
        cust = by.get(c["customer_uniqueid"] or "")
        out["customer"] = ("gone" if cust is None else "on_hold" if (cust.get("context") or "") == CTX["hold"]
                           else "in_room" if (cust.get("application") or "") == "ConfBridge"
                           else "transferring" if (cust.get("context") or "") == CTX["xfer"] else "up")
    if c["target_uniqueid"]:
        out["target"] = leg(c["target_uniqueid"])
    return out


def _view(c: dict, rows=None, *, include_case=True) -> dict:
    names = dict(sess().execute(text(f"""
        SELECT u.id::text, {USER_NAME_SQL.format(a='u')} FROM users u
         WHERE u.id = ANY(CAST(:ids AS uuid[]))"""), {"ids": [str(x) for x in (c["requested_by"], c["target_user_id"]) if x]}).all())
    pending = sess().execute(text("""
        SELECT op, state, result_code, created_at FROM call_control_ops
         WHERE control_id = CAST(:c AS uuid) ORDER BY created_at DESC LIMIT 1"""), {"c": str(c["id"])}).mappings().first()
    case = None
    if include_case and c["case_id"]:
        case = _case_for_me(str(c["case_id"]))
    mine = str(c["requested_by"]) == me()
    actions = []
    if mine and c["state"] == "active" and not (pending and pending["state"] in ("queued", "sending")):
        actions = [op for op, kinds in COMMANDS.items() if c["kind"] in kinds]
        if c["kind"] == "conference":
            actions = ["remove"]
    return {
        "id": str(c["id"]), "tag": hexid(c["id"]), "kind": c["kind"], "state": c["state"], "mode": c["mode"],
        "result_code": c["result_code"],
        "from": {"extension": c["from_extension"], "user_id": str(c["requested_by"]),
                 "name": names.get(str(c["requested_by"]))},
        "target": {"extension": c["target_extension"], "user_id": s_(c["target_user_id"]),
                   "name": names.get(s_(c["target_user_id"]))},
        "attempt_id": s_(c["attempt_id"]), "case": case,
        "created_at": iso(c["created_at"]), "started_at": iso(c["started_at"]),
        "completed_at": iso(c["completed_at"]), "ended_at": iso(c["ended_at"]),
        "last_op": ({"op": pending["op"], "state": pending["state"], "result_code": pending["result_code"],
                     "at": iso(pending["created_at"])} if pending else None),
        "phase": _phase(c, rows), "mine": mine, "actions": actions,
        "note": "submitted = the PBX accepted the command; what happened on the call comes from the live channel list",
    }


def s_(v):
    return None if v is None else str(v)


def _case_for_me(case_id: str) -> dict | None:
    from services.calling.common import case_ref, scope_sql
    cond, params = scope_sql("c")
    visible = sess().execute(text(f"SELECT 1 FROM clints c WHERE c.id = CAST(:id AS uuid) AND {cond}"),
                             {**params, "id": case_id}).first() is not None
    if not visible:
        return {"id": None, "in_scope": False}
    ref = case_ref(case_id)
    if ref is None:
        return None
    return {"id": ref["id"], "name": ref["name"], "account_number": ref["account_number"], "in_scope": True}


def _visible_control(control_id) -> dict:
    from services.calling.common import can_supervise
    cid = parse_uuid(str(control_id), "control_id")
    c = sess().execute(text("SELECT * FROM call_controls WHERE id = CAST(:id AS uuid)"), {"id": cid}).mappings().first()
    if c is None:
        raise ApiError(404, NOT_FOUND, "call control not found", {"control_id": cid})
    c = dict(c)
    if me() not in (str(c["requested_by"]), s_(c["target_user_id"])) and not (can_supervise() and c["kind"] != "spy"):
        raise ApiError(404, NOT_FOUND, "call control not found", {"control_id": cid})
    if c["kind"] == "spy" and str(c["requested_by"]) != me():
        raise ApiError(404, NOT_FOUND, "call control not found", {"control_id": cid})   # the agent is not told
    return c


def view(control_id) -> dict:
    c = _visible_control(control_id)
    fresh, rows = _snapshot()
    return _view(c, rows if fresh else None)


def incoming(tag: str) -> dict:
    """GET /v2/calling/control/incoming/<tag>: what the softphone shows for a ringing call that
    carries X-CAWP-Control, and whether it may answer it by itself (only the caller's own
    listening call, just requested, to the caller's own extension)."""
    cid = parse_tag(tag)
    if cid is None:
        raise ApiError(404, NOT_FOUND, "call control not found", {"tag": "invalid"})
    c = sess().execute(text("""
        SELECT *, created_at > now() - interval '60 seconds' AS recent FROM call_controls WHERE id = CAST(:id AS uuid)"""),
        {"id": cid}).mappings().first()
    if c is None:
        raise ApiError(404, NOT_FOUND, "call control not found", {"control_id": cid})
    c = dict(c)
    own = _my_extension()
    is_requester, is_target = str(c["requested_by"]) == me(), s_(c["target_user_id"]) == me()
    if c["kind"] == "spy":
        if not is_requester:
            raise ApiError(404, NOT_FOUND, "call control not found", {"control_id": cid})
        role = "listen"
    elif is_target and own == c["target_extension"]:
        role = "transfer" if c["kind"].startswith("transfer") else "invite"
        if c["kind"] == "transfer_attended":
            role = "consult"
    elif is_requester and own == c["from_extension"] and c["kind"] == "transfer_blind":
        role = "recall"                       # the target did not answer: the transfer rings back
    else:
        raise ApiError(404, NOT_FOUND, "call control not found", {"control_id": cid})
    auto = bool(role == "listen" and own == c["from_extension"] and c["state"] in OPEN_STATES and c["recent"]
                and c["ended_at"] is None)
    out = _view(c, None, include_case=c["kind"] != "spy")
    out.update(role=role, auto_answer=auto)
    return out


def mine() -> dict:
    """GET /v2/me/call-controls: capabilities, my open controls and controls ringing me."""
    fresh, rows = _snapshot()
    rs = sess().execute(text("""
        SELECT * FROM call_controls
         WHERE ended_at IS NULL AND created_at > now() - interval '4 hours'
           AND (requested_by = CAST(:me AS uuid) OR (target_user_id = CAST(:me AS uuid) AND kind <> 'spy'))
         ORDER BY created_at DESC LIMIT 20"""), {"me": me()}).mappings().all()
    items = [_view(dict(r), rows if fresh else None) for r in rs]
    return {"capabilities": capabilities(), "items": items, "presence_known": fresh}


def history(args) -> dict:
    """GET /v2/calling/controls: the call-control log (who, from, to, when, attempt) in the
    monitor scope (SuperAdmin every extension, a supervisor their team's)."""
    from services.calling import monitor
    sc = monitor.scope(me())
    try:
        size = max(1, min(200, int(args.get("page_size") or 50)))
        page = max(1, int(args.get("page") or 1))
    except ValueError:
        raise ApiError(422, VALIDATION_FAILED, "page and page_size must be numbers") from None
    where, params = ["TRUE"], {"lim": size, "off": (page - 1) * size}
    if sc["extensions"] is not None:
        where.append("(c.from_extension = ANY(:exts) OR c.target_extension = ANY(:exts))")
        params["exts"] = sorted(sc["extensions"])
    kind = args.get("kind")
    if kind:
        if kind not in KINDS:
            raise ApiError(422, VALIDATION_FAILED, "unknown kind", {"kind": "invalid"})
        where.append("c.kind = :kind")
        params["kind"] = kind
    total = sess().execute(text(f"SELECT count(*) FROM call_controls c WHERE {' AND '.join(where)}"), params).scalar()
    rs = sess().execute(text(f"""
        SELECT c.*, {USER_NAME_SQL.format(a='u')} AS requested_by_name, {USER_NAME_SQL.format(a='t')} AS target_name
          FROM call_controls c LEFT JOIN users u ON u.id = c.requested_by LEFT JOIN users t ON t.id = c.target_user_id
         WHERE {' AND '.join(where)} ORDER BY c.created_at DESC LIMIT :lim OFFSET :off"""), params).mappings().all()
    return {"scope": sc["kind"], "total": int(total), "page": page, "page_size": size, "items": [{
        "id": str(r["id"]), "kind": r["kind"], "state": r["state"], "mode": r["mode"], "result_code": r["result_code"],
        "from": {"extension": r["from_extension"], "user_id": str(r["requested_by"]), "name": r["requested_by_name"]},
        "target": {"extension": r["target_extension"], "user_id": s_(r["target_user_id"]), "name": r["target_name"]},
        "attempt_id": s_(r["attempt_id"]), "case_id": s_(r["case_id"]), "linkedid": r["linkedid"],
        "created_at": iso(r["created_at"]), "started_at": iso(r["started_at"]), "completed_at": iso(r["completed_at"]),
        "ended_at": iso(r["ended_at"])} for r in rs]}


# --------------------------------------------------------------------------- service side
def _svc_audit(session, c, action, after):
    audit.record(action, "call_control", str(c["id"]), case_id=s_(c.get("case_id")), after=after,
                 actor_user_id=None, request_id=None, idempotency_key=None, source="calling", session=session)


def _apply(session, control_id, changes: dict):
    if not changes:
        return
    sets = ", ".join(f"{k} = :{k}" for k in changes)
    stamps = []
    st = changes.get("state")
    if st in ("active", "completed"):
        stamps.append("started_at = coalesce(started_at, now())")
    if st in ("completed", "cancelled"):
        stamps.append("completed_at = coalesce(completed_at, now())")
    if st in ("ended", "failed"):
        stamps.append("ended_at = coalesce(ended_at, now())")
    session.execute(text(f"""
        UPDATE call_controls SET {sets}{''.join(', ' + x for x in stamps)}, updated_at = now()
         WHERE id = CAST(:_id AS uuid)"""), {**changes, "_id": str(control_id)})


class Claim:
    def __init__(self, op_id, control, op, actions, changes):
        self.op_id, self.control, self.op, self.actions, self.changes = op_id, control, op, actions, changes


def _finish_op(session, op_id, state, code, ami=None):
    session.execute(text("""
        UPDATE call_control_ops SET state = :st, result_code = :code, finished_at = now(),
               ami = CASE WHEN :ami IS NULL THEN ami ELSE CAST(:ami AS jsonb) END
         WHERE id = CAST(:id AS uuid)"""), {"st": state, "code": code, "id": str(op_id),
                                             "ami": json.dumps(ami) if ami is not None else None})


def claim(session, ctx, worker_id, list_channels) -> Claim | str | None:
    """One queued op -> a Claim (committed before sending), a handled code, or None."""
    connected = ctx is not None and ctx.session.ready
    q = session.execute(text("""
        SELECT o.*, (o.expires_at <= clock_timestamp()) AS expired FROM call_control_ops o
         WHERE o.state = 'queued' AND (o.expires_at <= clock_timestamp() OR o.expected_epoch = :ep)
         ORDER BY o.created_at FOR UPDATE SKIP LOCKED LIMIT 1"""),
        {"ep": ctx.epoch if connected else None}).mappings().first()
    if q is None:
        return None
    q = dict(q)
    c = dict(session.execute(text("SELECT * FROM call_controls WHERE id = CAST(:id AS uuid) FOR UPDATE"),
                             {"id": str(q["control_id"])}).mappings().one())
    if q["expired"]:
        _finish_op(session, q["id"], "expired", "pbx_epoch_changed" if connected and ctx.epoch != q["expected_epoch"]
                   else "not_claimed_in_time")
        if q["op"] == "start":
            _apply(session, c["id"], {"state": "failed", "result_code": "not_sent"})
        _svc_audit(session, c, f"call.control.{c['kind']}.{q['op']}.expired", {"op_id": str(q["id"])})
        return "expired"
    if not connected or ctx.epoch != q["expected_epoch"]:
        return None
    if q["op"] != "start" and c["state"] not in ("active", "completed", "cancelled"):
        _finish_op(session, q["id"], "refused", "control_not_active")
        return "refused:control_not_active"
    try:
        chans = channel_map(list_channels())
    except Exception as e:  # noqa: BLE001 - AmiError: the channel list is unknown -> do not act blindly
        _finish_op(session, q["id"], "refused", "channels_unknown")
        if q["op"] == "start":
            _apply(session, c["id"], {"state": "failed", "result_code": "channels_unknown"})
        log.info("control %s: channel list unavailable (%s)", c["id"], getattr(e, "code", type(e).__name__))
        return "refused:channels_unknown"
    try:
        actions, changes = plan(c, q, chans)
    except Refused as r:
        _finish_op(session, q["id"], "refused", r.code)
        if q["op"] == "start":
            _apply(session, c["id"], {"state": "failed", "result_code": r.code})
        _svc_audit(session, c, f"call.control.{c['kind']}.{q['op']}.refused", {"op_id": str(q["id"]), "code": r.code})
        return f"refused:{r.code}"
    session.execute(text("""UPDATE call_control_ops SET state = 'sending', claimed_at = now(), claimed_by = :w
                             WHERE id = CAST(:id AS uuid)"""), {"w": worker_id[:200], "id": str(q["id"])})
    return Claim(str(q["id"]), c, q, actions, changes)


def record_outcome(session, cl: Claim, outcome: str, code: str, ami: list) -> None:
    c, op = cl.control, cl.op
    _finish_op(session, cl.op_id, outcome, code, ami)
    if outcome == "done":
        _apply(session, c["id"], cl.changes)
    elif op["op"] == "start":
        # rejected: nothing (or only harmless variables) changed; unacknowledged: the PBX state is
        # unknown, the sweep follows the channels from here
        _apply(session, c["id"], {"state": "failed", "result_code": "start_rejected"} if outcome == "rejected"
               else {"state": "active", "result_code": "start_unacknowledged"})
    _svc_audit(session, c, f"call.control.{c['kind']}.{op['op']}.{outcome}", {
        "op_id": cl.op_id, "code": code, "actions": [a["action"] for a in ami],
        "mode": op.get("mode"), "changes": {k: v for k, v in cl.changes.items() if k in ("state", "mode", "result_code")}
        if outcome == "done" else None})


def send_actions(session_ami, actions, timeout) -> tuple[str, str, list]:
    """Send in order; stop at the first non-Success. -> (outcome, code, [{action, response}])."""
    from services.calling.ami import AmiError
    log_ = []
    for action, fields in actions:
        try:
            frame = session_ami.send(action, fields, timeout=timeout)
        except AmiError as e:
            log_.append({"action": action, "response": None, "error": e.code})
            return "unacknowledged", e.code, log_
        log_.append({"action": action, "response": frame.response})
        if frame.response != "Success":
            return "rejected", f"{action.lower()}_rejected", log_
    return "done", "submitted", log_


class ControlWorker(threading.Thread):
    """Claims queued call-control ops and sends their AMI actions once; sweeps open controls."""

    def __init__(self, app, current_ctx, worker_id: str, *, poll: float = 0.25, sweep_every: float = 3.0,
                 send_timeout: float = 5.0):
        super().__init__(name="call-control", daemon=True)
        self.app, self.current_ctx, self.worker_id = app, current_ctx, worker_id
        self.poll, self.sweep_every, self.send_timeout = poll, sweep_every, send_timeout
        self.stopping = threading.Event()
        self.stats = {"claimed": 0, "done": 0, "rejected": 0, "unacknowledged": 0, "not_sent": 0, "sweeps": 0,
                      "errors": 0}
        self.absent: dict[str, dict] = {}
        self._last_sweep = 0.0

    def stop(self, timeout=3.0):
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
        failing_since = None
        while not self.stopping.is_set():
            try:
                busy = self.run_once()
                if time.monotonic() - self._last_sweep >= self.sweep_every:
                    self._last_sweep = time.monotonic()
                    self.sweep()
                failing_since = None
            except Exception:  # noqa: BLE001 - the database may be down: retry, log once a minute
                self.stats["errors"] += 1
                now = time.monotonic()
                if failing_since is None or now - failing_since > 60:
                    log.exception("call-control cycle failed (retrying; logged once a minute)")
                    failing_since = now
                busy = False
                self.stopping.wait(1.0)
            if not busy:
                self.stopping.wait(self.poll)

    def run_once(self) -> bool:
        ctx = self.current_ctx()
        got = self._tx(lambda s: claim(s, ctx, self.worker_id, lambda: ctx.session.list_channels()))
        if got is None:
            return False
        if isinstance(got, str):
            self.stats["not_sent"] += 1
            log.info("call-control op not sent: %s", got)
            return True
        self.stats["claimed"] += 1
        outcome, code, ami = send_actions(ctx.session, got.actions, self.send_timeout)
        got.actions = None
        self.stats[outcome] += 1
        log.info("control %s %s: %s (%s)", got.control["id"], got.op["op"], outcome, code)
        for i in range(20):                                  # never lose the outcome on a database blip
            try:
                self._tx(lambda s: record_outcome(s, got, outcome, code, ami))
                break
            except Exception:  # noqa: BLE001
                log.exception("could not record the outcome of control op %s (try %d)", got.op_id, i + 1)
                if self.stopping.wait(min(5.0, 0.2 * (i + 1))):
                    break
        return True

    def sweep(self) -> int:
        """Follow open controls on the channel list (one CoreShowChannels when any is open)."""
        ctx = self.current_ctx()
        if ctx is None or not ctx.session.ready:
            return 0
        open_ = self._tx(lambda s: [dict(r) for r in s.execute(text("""
            SELECT c.*, extract(epoch FROM now() - coalesce(c.started_at, c.created_at)) AS age,
                   EXISTS (SELECT 1 FROM call_control_ops o WHERE o.control_id = c.id
                            AND o.state IN ('queued', 'sending')) AS op_open
              FROM call_controls c
             WHERE c.ended_at IS NULL AND c.state IN ('active', 'completed', 'cancelled')
               AND c.pbx_epoch = :ep""" ), {"ep": ctx.epoch}).mappings()])
        if not open_:
            self.absent.clear()
            return 0
        from services.calling.ami import AmiError
        try:
            chans = channel_map(ctx.session.list_channels())
        except AmiError:
            return 0
        self.stats["sweeps"] += 1
        acted = 0
        for c in open_:
            if c["op_open"]:
                continue
            decisions = sweep_decisions(c, chans, self.absent.setdefault(str(c["id"]), {}), float(c["age"] or 0))
            for kind, value in decisions:
                acted += 1
                self._tx(lambda s, c=c, kind=kind, value=value: self._decide(s, c, kind, value, ctx.epoch))
        live = {str(c["id"]) for c in open_}
        for k in list(self.absent):
            if k not in live:
                self.absent.pop(k, None)
        return acted

    def _decide(self, session, c, kind, value, epoch):
        cur = session.execute(text("SELECT state, ended_at FROM call_controls WHERE id = CAST(:id AS uuid) FOR UPDATE"),
                              {"id": str(c["id"])}).first()
        if cur is None or cur[1] is not None:
            return
        if kind == "ended":
            changes = {"state": "ended"} if cur[0] == "active" else {}
            if c["kind"] == "spy" and cur[0] == "active" and not c.get("result_code"):
                changes["result_code"] = "listener_gone"       # hung up, or the phone never answered
            _apply(session, c["id"], changes)
            session.execute(text("UPDATE call_controls SET ended_at = coalesce(ended_at, now()) WHERE id = CAST(:id AS uuid)"),
                            {"id": str(c["id"])})
            _svc_audit(session, c, f"call.control.{c['kind']}.ended", {"state": cur[0]})
        elif kind == "set":
            if cur[0] == "active":
                _apply(session, c["id"], {"state": "completed", "result_code": value})
                _svc_audit(session, c, f"call.control.{c['kind']}.{value}", {})
        elif kind == "op":
            try:
                with session.begin_nested():
                    session.execute(text("""
                        INSERT INTO call_control_ops (control_id, op, expected_epoch, expires_at)
                        VALUES (CAST(:c AS uuid), :op, :ep, now() + make_interval(secs => :ttl))"""),
                        {"c": str(c["id"]), "op": value, "ep": epoch, "ttl": OP_TTL_SECONDS})
            except IntegrityError:
                return                                         # a command is already in flight
            _svc_audit(session, c, f"call.control.{c['kind']}.{value}.auto", {})
