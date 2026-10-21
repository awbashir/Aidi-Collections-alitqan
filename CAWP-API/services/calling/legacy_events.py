"""Asterisk <= 11 AMI events -> the Asterisk 12+ shapes the evidence code reads (pure).

CALLING_PBX_EVENTS=legacy (services/calling/common.pbx_events): the Alitqan Issabel 4 runs
Asterisk 11.25 (AMI 1.3, chan_sip). Its call events differ from Asterisk 12+:

    Asterisk 11                                    normalised to
    Dial  SubEvent: Begin  Channel, UniqueID,      DialBegin  Channel, Uniqueid, DestChannel,
          Destination, DestUniqueID, Dialstring               DestUniqueid, DialString
    Dial  SubEvent: End    Channel, UniqueID,      DialEnd    Channel, Uniqueid, DialStatus
          DialStatus  (no DestUniqueID)                       (no DestUniqueid: the projection ties
                                                              it to the agent's latest dial)
    Bridge Bridgestate: Link | Unlink              TWO frames BridgeEnter | BridgeLeave, one per
          Channel1/2, Uniqueid1/2, Bridgetype      channel, BridgeUniqueid "<uidA>|<uidB>" (sorted,
                                                   the same id live_monitor.legacy_link gives a
                                                   two-party bridge), BridgeNumChannels 2,
                                                   BridgePeerUniqueid / BridgePeerChannel = the
                                                   other channel
    any other event                                unchanged

Values `<null>` (e.g. the Uniqueid of a failed OriginateResponse) become absent. Every converted
frame keeps its provenance in `LegacyEvent` ("Dial/Begin", "Dial/End", "Bridge/Link",
"Bridge/Unlink") and the PBX `Timestamp` when the PBX sends one. Nothing here attributes an event
to a call: that is services/calling/legacy_correlation.py.
"""
from __future__ import annotations

from services.calling.ami import Frame

NULL = "<null>"
BRIDGE_MEMBERS = "2"


def _v(frame, key):
    try:
        value = frame.get(key)
    except Exception:  # noqa: BLE001 - a duplicated key: not usable
        return None
    if value is None:
        return None
    value = value.strip()
    return None if value in ("", NULL) else value


def _frame(pairs) -> Frame:
    return Frame([(k, v) for k, v in pairs if v is not None])


def _strip_null(frame) -> Frame:
    if not any(v.strip() == NULL for _k, v in frame.fields):
        return frame
    return Frame([(k, v) for k, v in frame.fields if v.strip() != NULL])


def bridge_id(uid_a: str, uid_b: str) -> str:
    return "|".join(sorted((uid_a, uid_b)))


def normalise(frame) -> list:
    """One AMI frame -> 0..2 frames in the Asterisk 12+ shape (see the module docstring)."""
    try:
        event = frame.event
    except Exception:  # noqa: BLE001 - duplicated routing key
        return []
    if not event:
        return []
    ts = _v(frame, "Timestamp")
    if event == "Dial":
        sub = _v(frame, "SubEvent")
        uid = _v(frame, "UniqueID")
        if not uid:
            return []
        if sub == "Begin":
            return [_frame([("Event", "DialBegin"), ("Timestamp", ts), ("Channel", _v(frame, "Channel")),
                            ("Uniqueid", uid), ("DestChannel", _v(frame, "Destination")),
                            ("DestUniqueid", _v(frame, "DestUniqueID")), ("DialString", _v(frame, "Dialstring")),
                            ("LegacyEvent", "Dial/Begin")])]
        if sub == "End":
            return [_frame([("Event", "DialEnd"), ("Timestamp", ts), ("Channel", _v(frame, "Channel")),
                            ("Uniqueid", uid), ("DialStatus", _v(frame, "DialStatus")),
                            ("LegacyEvent", "Dial/End")])]
        return []
    if event == "Bridge":
        state = _v(frame, "Bridgestate")
        if state not in ("Link", "Unlink"):
            return []
        u1, u2 = _v(frame, "Uniqueid1"), _v(frame, "Uniqueid2")
        c1, c2 = _v(frame, "Channel1"), _v(frame, "Channel2")
        if not u1 or not u2 or u1 == u2:
            return []
        name = "BridgeEnter" if state == "Link" else "BridgeLeave"
        bid = bridge_id(u1, u2)
        out = []
        for uid, chan, peer, peer_chan in ((u1, c1, u2, c2), (u2, c2, u1, c1)):
            out.append(_frame([("Event", name), ("Timestamp", ts), ("Channel", chan), ("Uniqueid", uid),
                               ("BridgeUniqueid", bid), ("BridgeType", _v(frame, "Bridgetype")),
                               ("BridgeNumChannels", BRIDGE_MEMBERS), ("BridgePeerUniqueid", peer),
                               ("BridgePeerChannel", peer_chan), ("LegacyEvent", f"Bridge/{state}")]))
        return out
    return [_strip_null(frame)]
