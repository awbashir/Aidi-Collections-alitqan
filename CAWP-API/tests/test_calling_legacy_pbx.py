"""Click-to-call on Asterisk 11 / chan_sip (CALLING_PBX_EVENTS=legacy; the Alitqan Issabel 4).

The frames below use the key sets captured read-only on the production PBX (Asterisk 11.25.3,
AMI 1.3): OriginateResponse, Dial Begin/End, Newchannel (no Linkedid), Newstate, Hangup,
(Soft)HangupRequest, Bridge Link/Unlink; no Timestamp header (timestampevents is off there).
UserEvents carry `ChanUniqueid: ${UNIQUEID}` as the legacy dialplan generates them.

Pure: the normaliser (services/calling/legacy_events.py), derive() on legacy shapes, the dialplan
policy (dial_format national_sa, trunks selection, pbx_events legacy), calling.verify's dialplan
check, the correlator's held-row TTL, the mode helpers.
Database: ActionID binding through the real evidence consumer, held events, a second customer leg,
masquerades, Issabel-dialer traffic, OriginateResponse failures, the FastAGI hook's legacy
accept/deny (only the leg the OriginateResponse bound), the dispatcher's ActionID registry and the
version fail-safe.
"""
from __future__ import annotations

import random
import types
import uuid

import pytest

from services.calling import evidence as E
from services.calling import legacy_events as LE
from services.calling import projection as P
from services.calling.ami import Frame, PbxIdentity
from test_calling_agi import EPOCH, FakeAsterisk, agi_server, decisions, token_for  # noqa: F401
from test_calling_c2c_helpers import (add_contact, c2c, cl, cl_app, cl_client, connect_run, free_extension, hdr,  # noqa: F401
                                      key, make_case, new_contract, new_user, number, pgc, place, q1, qall, queue,
                                      reservation, run, state, transport)
from test_calling_helpers import insert_attempt

CONTEXT = "collections-human-outbound"
DEST = "+966500000004"


# --------------------------------------------------------------------------- Asterisk 11 frames
def F(*pairs):
    return Frame([(k, v) for k, v in pairs if v is not None])


def originate_response(action_id, ext, uid, *, ok=True, reason="4", exten=DEST, context=CONTEXT, chan_suffix="0000001a"):
    return F(("Event", "OriginateResponse"), ("Privilege", "call,all"), ("ActionID", action_id),
             ("Response", "Success" if ok else "Failure"),
             ("Channel", f"SIP/{ext}-{chan_suffix}" if ok else f"SIP/{ext}"), ("Context", context), ("Exten", exten),
             ("Reason", reason), ("Uniqueid", uid if ok else "<null>"), ("CallerIDNum", "<unknown>"),
             ("CallerIDName", "<unknown>"))


def newchannel(chan, uid, *, exten="", context="from-internal", ts=None):
    return F(("Event", "Newchannel"), ("Privilege", "call,all"), ("Timestamp", ts), ("Channel", chan),
             ("ChannelState", "0"), ("ChannelStateDesc", "Down"), ("CallerIDNum", ""), ("CallerIDName", ""),
             ("AccountCode", ""), ("Exten", exten), ("Context", context), ("Uniqueid", uid))


STATE_DESC = {"4": "Ring", "5": "Ringing", "6": "Up"}


def newstate(chan, uid, st, ts=None):
    return F(("Event", "Newstate"), ("Privilege", "call,all"), ("Timestamp", ts), ("Channel", chan),
             ("ChannelState", st), ("ChannelStateDesc", STATE_DESC[st]), ("CallerIDNum", "401"),
             ("CallerIDName", "Agent"), ("ConnectedLineNum", ""), ("ConnectedLineName", ""), ("Uniqueid", uid))


def dial_begin(chan, uid, dest_chan, dest_uid, ts=None):
    trunk, number_ = dest_chan.split("/")[1].rsplit("-", 1)[0], "0500000004"
    return F(("Event", "Dial"), ("Privilege", "call,all"), ("Timestamp", ts), ("SubEvent", "Begin"), ("Channel", chan),
             ("Destination", dest_chan), ("CallerIDNum", "401"), ("CallerIDName", "Agent"),
             ("ConnectedLineNum", ""), ("ConnectedLineName", ""), ("UniqueID", uid), ("DestUniqueID", dest_uid),
             ("Dialstring", f"{trunk}/{number_}"))


def dial_end(chan, uid, status, ts=None):
    return F(("Event", "Dial"), ("Privilege", "call,all"), ("Timestamp", ts), ("SubEvent", "End"), ("Channel", chan),
             ("UniqueID", uid), ("DialStatus", status))


def bridge(st, c1, u1, c2, u2, ts=None):
    return F(("Event", "Bridge"), ("Privilege", "call,all"), ("Timestamp", ts), ("Bridgestate", st),
             ("Bridgetype", "core"), ("Channel1", c1), ("Channel2", c2), ("Uniqueid1", u1), ("Uniqueid2", u2),
             ("CallerID1", "401"), ("CallerID2", "0500000004"))


def hangup(chan, uid, cause="16", ts=None):
    return F(("Event", "Hangup"), ("Privilege", "call,all"), ("Timestamp", ts), ("Channel", chan), ("Uniqueid", uid),
             ("CallerIDNum", ""), ("CallerIDName", ""), ("ConnectedLineNum", ""), ("ConnectedLineName", ""),
             ("AccountCode", ""), ("Cause", cause), ("Cause-txt", "Normal Clearing"))


def hangup_request(chan, uid, *, soft=False, ts=None):
    return F(("Event", "SoftHangupRequest" if soft else "HangupRequest"), ("Privilege", "call,all"),
             ("Timestamp", ts), ("Channel", chan), ("Uniqueid", uid), ("Cause", "16"))


def user_event(attempt, st, chan_uid, *, top_uid=None, ts=None, **extra):
    return F(("Event", "UserEvent"), ("Privilege", "user,all"), ("Timestamp", ts), ("UserEvent", "CollectionsCall"),
             ("Uniqueid", top_uid), ("AttemptId", attempt), ("ChanUniqueid", chan_uid), ("State", st),
             *extra.items())


def masquerade(clone, original, ts=None):
    return F(("Event", "Masquerade"), ("Privilege", "call,all"), ("Timestamp", ts), ("Clone", clone),
             ("CloneState", "Up"), ("Original", original), ("OriginalState", "Up"))


def rename(chan, newname, uid, ts=None):
    return F(("Event", "Rename"), ("Privilege", "call,all"), ("Timestamp", ts), ("Channel", chan),
             ("Newname", newname), ("Uniqueid", uid))


def uids(n=4):
    base = random.randint(1792600000, 1792699999)
    return [f"{base}.{random.randint(1000, 999999) + i}" for i in range(n)]


# --------------------------------------------------------------------------- normaliser (pure)
def test_dial_begin_and_end_become_dialbegin_and_dialend():
    a, c = "1792601562.101", "1792601562.102"
    [b] = LE.normalise(dial_begin("SIP/401-0000001a", a, "SIP/Mobily18-0000001b", c))
    assert b.event == "DialBegin" and b.get("Uniqueid") == a and b.get("DestUniqueid") == c
    assert b.get("DestChannel") == "SIP/Mobily18-0000001b" and b.get("DialString") == "Mobily18/0500000004"
    assert b.get("LegacyEvent") == "Dial/Begin" and b.get("Timestamp") is None
    [e] = LE.normalise(dial_end("SIP/401-0000001a", a, "CONGESTION"))
    assert e.event == "DialEnd" and e.get("Uniqueid") == a and e.get("DialStatus") == "CONGESTION"
    assert e.get("DestUniqueid") is None and e.get("LegacyEvent") == "Dial/End"
    assert LE.normalise(F(("Event", "Dial"), ("SubEvent", "Other"), ("UniqueID", a))) == []


def test_bridge_link_and_unlink_become_two_enters_and_leaves_with_one_sorted_id():
    a, c = "1792601562.101", "1792601562.99"
    frames = LE.normalise(bridge("Link", "SIP/401-0000001a", a, "SIP/Mobily18-0000001b", c, ts="1792601570.5"))
    assert [f.event for f in frames] == ["BridgeEnter", "BridgeEnter"]
    assert {f.get("Uniqueid") for f in frames} == {a, c}
    assert {f.get("BridgeUniqueid") for f in frames} == {"|".join(sorted((a, c)))}
    assert all(f.get("BridgeNumChannels") == "2" and f.get("LegacyEvent") == "Bridge/Link" for f in frames)
    assert {(f.get("Uniqueid"), f.get("BridgePeerUniqueid")) for f in frames} == {(a, c), (c, a)}
    assert all(f.get("Timestamp") == "1792601570.5" for f in frames)
    leaves = LE.normalise(bridge("Unlink", "SIP/401-0000001a", a, "SIP/Mobily18-0000001b", c))
    assert [f.event for f in leaves] == ["BridgeLeave", "BridgeLeave"]
    # the same bridge id live_monitor.legacy_link gives a two-party bridge in the channel list
    from services.calling.live_monitor import legacy_link
    assert legacy_link({"BridgedUniqueID": c}.get, a)[1] == frames[0].get("BridgeUniqueid")
    assert LE.normalise(bridge("Link", "SIP/401-0000001a", a, "SIP/401-0000001a", a)) == []


def test_zombie_hangup_is_not_a_hangup_and_masquerade_events_pass():
    [z] = LE.normalise(hangup("SIP/401-0000001a<ZOMBIE>", "1792601562.101"))
    assert z.event == "MasqueradeZombie" and z.get("Uniqueid") == "1792601562.101"
    assert z.get("LegacyEvent") == "Hangup/ZOMBIE" and z.get("Channel") == "SIP/401-0000001a<ZOMBIE>"
    m = masquerade("SIP/401-0000001a", "AsyncGoto/SIP/401-0000001a")
    assert LE.normalise(m) == [m]


def test_null_values_are_dropped_and_other_events_pass_unchanged():
    [o] = LE.normalise(originate_response("s:1", "401", None, ok=False, reason="3"))
    assert o.event == "OriginateResponse" and o.get("Uniqueid") is None and o.get("Response") == "Failure"
    h = hangup("SIP/401-0000001a", "1792601562.101")
    assert LE.normalise(h) == [h]


# --------------------------------------------------------------------------- derive (pure)
A_ID = "11111111-2222-4333-8444-555555555555"
AG, CU, CU2, X = "1792601562.101", "1792601562.102", "1792601562.103", "1792601562.199"
ALLOW = [{"decision": "allow", "reason": "allowed"}]


def _ev(i, event, ts, uid=None, state=None, **payload):
    return {"id": i, "event": event, "state": state, "pbx_ts": ts, "uniqueid": uid, "payload": payload}


def _legacy_call(link=True, peer=CU):
    bid = "|".join(sorted((AG, peer)))
    evs = [_ev(1, "OriginateResponse", "1.0", AG, Response="Success", Reason="4", correlated_by="action_id"),
           _ev(2, "UserEvent", "1.1", AG, state="authorized"),
           _ev(3, "DialBegin", "2.0", AG, DestUniqueid=CU),
           _ev(4, "Newstate", "3.0", CU, ChannelState="6")]
    if link:
        evs += [_ev(5, "BridgeEnter", "3.1", AG, BridgeUniqueid=bid, BridgeNumChannels="2"),
                _ev(6, "BridgeEnter", "3.1", peer, BridgeUniqueid=bid, BridgeNumChannels="2")]
    return evs


def test_derive_legacy_link_is_the_conversation():
    f = P.derive(A_ID, _legacy_call(), ALLOW, AG)
    assert f.bridged is not None and f.furthest == "bridged" and f.agent_answered is not None
    bid = "|".join(sorted((AG, CU)))
    end = [_ev(7, "HangupRequest", "9.0", CU), _ev(8, "BridgeLeave", "9.0", AG, BridgeUniqueid=bid),
           _ev(9, "BridgeLeave", "9.0", CU, BridgeUniqueid=bid), _ev(10, "Hangup", "9.1", CU, Cause="16"),
           _ev(11, "DialEnd", "9.2", AG, DialStatus="ANSWER"), _ev(12, "Hangup", "9.3", AG, Cause="16")]
    f = P.derive(A_ID, _legacy_call() + end, ALLOW, AG)
    assert (f.terminal, f.reason, f.hangup_cause, f.duration) == ("ended", "customer_hung_up", 16, 6)


def test_derive_two_answered_legs_without_a_link_are_not_a_conversation():
    evs = _legacy_call(link=False) + [_ev(7, "DialEnd", "9.0", AG, DialStatus="ANSWER"),
                                      _ev(8, "Hangup", "9.1", CU, Cause="16"), _ev(9, "Hangup", "9.2", AG, Cause="16")]
    f = P.derive(A_ID, evs, ALLOW, AG)
    assert f.bridged is None and (f.terminal, f.reason) == ("ended", "not_bridged")
    # an Asterisk 11 Dial End ANSWER names no leg: alone it is not even a customer answer
    no_up = [e for e in evs if not (e["event"] == "Newstate" and e["uniqueid"] == CU)]
    f = P.derive(A_ID, no_up, ALLOW, AG)
    assert f.customer_answered is None and (f.terminal, f.reason) == ("ended", "customer_answer_not_observed")


@pytest.mark.parametrize("status,reason", [("BUSY", "customer_busy"), ("NOANSWER", "customer_no_answer"),
                                           ("CONGESTION", "customer_congestion"), ("CHANUNAVAIL", "customer_unavailable")])
def test_derive_legacy_dial_end_status_is_the_end_reason(status, reason):
    """Campaign pacing reads these reasons: an Asterisk 11 Dial End status ends the call like a DialEnd."""
    evs = _legacy_call(link=False)[:3] + [_ev(4, "Hangup", "5.0", CU, Cause="17"),
                                          _ev(5, "DialEnd", "5.1", AG, DialStatus=status),
                                          _ev(6, "Hangup", "5.2", AG, Cause="16")]
    f = P.derive(A_ID, evs, ALLOW, AG)
    assert (f.terminal, f.reason, f.hangup_cause) == ("ended", reason, 17)


def test_derive_a_link_with_a_foreign_channel_is_ambiguous():
    f = P.derive(A_ID, _legacy_call(peer=X), ALLOW, AG)
    assert f.bridged is None and f.ambiguity == "bridge_topology_ambiguous"


@pytest.mark.parametrize("reason,code", [("3", "agent_no_answer"), ("5", "agent_busy"), ("8", "agent_unavailable"),
                                         ("1", "agent_rejected"), ("0", "agent_unavailable")])
def test_derive_originate_failure_reasons(reason, code):
    evs = [_ev(1, "OriginateResponse", "1.0", None, Response="Failure", Reason=reason, correlated_by="action_id")]
    f = P.derive(A_ID, evs, (), None)
    assert (f.terminal, f.reason) == ("failed", code) and not f.customer


def test_derive_refused_binding_and_conflicting_agent_are_ambiguous():
    evs = [_ev(1, "OriginateResponse", "1.0", AG, Response="Success", Reason="4", correlated_by="action_id",
               binding="refused:originate_response_mismatch")]
    assert P.derive(A_ID, evs, (), None).ambiguity == "originate_response_mismatch"
    evs = [_ev(1, "OriginateResponse", "1.0", X, Response="Success", Reason="4", correlated_by="action_id")]
    assert P.derive(A_ID, evs, (), AG).ambiguity == "agent_leg_conflict"
    assert P.derive(A_ID, _legacy_call(), ALLOW, AG, agent_conflict=True).ambiguity == "agent_leg_conflict"


def _failover(first_status="CONGESTION", first_hangup="2.5", second_dial="3.0"):
    bid = "|".join(sorted((AG, CU2)))
    return [_ev(1, "OriginateResponse", "1.0", AG, Response="Success", Reason="4", correlated_by="action_id"),
            _ev(2, "UserEvent", "1.1", AG, state="authorized"),
            _ev(3, "DialBegin", "2.0", AG, DestUniqueid=CU),
            *([_ev(4, "Hangup", first_hangup, CU, Cause="34")] if first_hangup else []),
            *([_ev(5, "DialEnd", "2.6", AG, DialStatus=first_status)] if first_status else []),
            _ev(6, "DialBegin", second_dial, AG, DestUniqueid=CU2),
            _ev(7, "Newstate", "4.0", CU2, ChannelState="6"),
            _ev(8, "BridgeEnter", "4.1", AG, BridgeUniqueid=bid, BridgeNumChannels="2"),
            _ev(9, "BridgeEnter", "4.1", CU2, BridgeUniqueid=bid, BridgeNumChannels="2"),
            _ev(10, "Hangup", "9.0", CU2, Cause="16"), _ev(11, "Hangup", "9.1", AG, Cause="16")]


@pytest.mark.parametrize("kw", [{}, {"first_status": "CHANUNAVAIL"}, {"first_status": "BUSY"},
                                {"first_status": "NOANSWER"}, {"first_status": None}, {"first_hangup": None},
                                {"first_hangup": "3.5"}])
def test_derive_a_second_customer_leg_is_always_ambiguous(kw):
    """One attempt dials the customer once: even a clean CONGESTION/CHANUNAVAIL then a second dial
    (what a "failover" would be) never looks like one clean call."""
    f = P.derive(A_ID, _failover(**kw), ALLOW, AG)
    assert f.ambiguity == "multiple_customer_legs" and f.bridged is None and f.terminal is None


def _unlinked(with_link):
    bid = "|".join(sorted((AG, CU)))
    evs = _legacy_call(link=with_link) + [
        _ev(7, "HangupRequest", "9.0", CU), _ev(8, "BridgeLeave", "9.0", AG, BridgeUniqueid=bid),
        _ev(9, "BridgeLeave", "9.0", CU, BridgeUniqueid=bid), _ev(10, "Hangup", "9.1", CU, Cause="16"),
        _ev(11, "DialEnd", "9.2", AG, DialStatus="ANSWER"), _ev(12, "Hangup", "9.3", AG, Cause="16")]
    return evs


def test_derive_unlink_without_link_is_unobserved_not_unbridged():
    f = P.derive(A_ID, _unlinked(with_link=False), ALLOW, AG)
    assert f.ambiguity == "bridge_unobserved" and f.terminal is None and f.bridged is None
    f = P.derive(A_ID, _unlinked(with_link=True), ALLOW, AG)                 # with the Link: a conversation
    assert f.ambiguity is None and (f.terminal, f.reason, f.duration) == ("ended", "customer_hung_up", 6)


def test_derive_masquerade_or_zombie_of_a_leg_is_ambiguous():
    bid = "|".join(sorted((AG, CU)))
    for ev in (_ev(7, "MasqueradeZombie", "5.0", AG, Cause="16"), _ev(7, "Rename", "5.0", AG),
               _ev(7, "Masquerade", "5.0", AG)):
        evs = _legacy_call() + [ev, _ev(8, "BridgeLeave", "9.0", AG, BridgeUniqueid=bid),
                                _ev(9, "BridgeLeave", "9.0", CU, BridgeUniqueid=bid),
                                _ev(10, "Hangup", "9.1", CU, Cause="16"), _ev(11, "Hangup", "9.3", AG, Cause="16")]
        f = P.derive(A_ID, evs, ALLOW, AG)
        assert f.ambiguity == "leg_masqueraded" and f.terminal is None and f.duration is None


def test_derive_no_trunk_fails_without_a_customer_leg():
    evs = [_ev(1, "OriginateResponse", "1.0", AG, Response="Success", Reason="4", correlated_by="action_id"),
           _ev(2, "UserEvent", "1.1", AG, state="authorized"), _ev(3, "UserEvent", "1.2", AG, state="customer_dialing"),
           _ev(4, "UserEvent", "1.3", AG, state="no_trunk"),
           _ev(5, "UserEvent", "1.4", AG, state="agent_ended", Cause="34", DialStatus=""),
           _ev(6, "Hangup", "1.5", AG, Cause="34")]
    f = P.derive(A_ID, evs, ALLOW, AG)
    assert (f.terminal, f.reason, f.hangup_cause) == ("failed", "no_trunk_available", 34) and not f.customer


# --------------------------------------------------------------------------- correlator (pure)
def test_held_rows_expire_by_their_own_age_whatever_the_key_order(monkeypatch):
    from services.calling import legacy_correlation as LC
    now = [1000.0]
    monkeypatch.setattr(LC.time, "monotonic", lambda: now[0])
    c = LC.LegacyCorrelator(pending_ttl=10)
    row = lambda uid, k: {"uniqueid": uid, "dedupe_key": k}  # noqa: E731
    c.hold("ep", row("1.1", "a1"))                     # t=1000
    now[0] = 1008.0
    c.hold("ep", row("1.2", "b1"))                     # t=1008
    now[0] = 1009.0
    c.hold("ep", row("1.1", "a2"))                     # t=1009: the key keeps its first-hold place
    assert list(c.pending) == [("ep", "1.1"), ("ep", "1.2")]
    now[0] = 1012.0
    assert [r["dedupe_key"] for _k, r in c.expired()] == ["a1", "a2"]
    # a key whose place says "young" is still checked: every key is
    c.pending.move_to_end(("ep", "1.1"))
    assert [r["dedupe_key"] for _k, r in c.expired()] == ["a1", "a2"]
    now[0] = 1019.0
    assert sorted(r["dedupe_key"] for _k, r in c.expired()) == ["a1", "a2", "b1"]
    # the size bound still evicts the earliest held first
    c2 = LC.LegacyCorrelator(pending_ttl=1000, max_pending=1)
    c2.hold("ep", row("2.1", "x")), c2.hold("ep", row("2.2", "y"))
    assert [r["dedupe_key"] for _k, r in c2.expired()] == ["x"]


# --------------------------------------------------------------------------- modes, config, AMI (pure)
def test_pbx_events_setting_and_version_mismatch(monkeypatch):
    from services.calling import common
    monkeypatch.delenv("CALLING_PBX_EVENTS", raising=False)
    assert common.pbx_events() == "modern"
    monkeypatch.setenv("CALLING_PBX_EVENTS", " Legacy ")
    assert common.pbx_events() == "legacy"
    monkeypatch.setenv("CALLING_PBX_EVENTS", "asterisk11")
    with pytest.raises(ValueError):
        common.pbx_events()
    assert common.ami_major("Asterisk Call Manager/1.3") == 1 and common.ami_major("Asterisk Call Manager/9.0.0") == 9
    assert common.pbx_version_mismatch("Asterisk Call Manager/1.3", "modern")
    assert common.pbx_version_mismatch("Asterisk Call Manager/2.10.3", "legacy")
    assert not common.pbx_version_mismatch("Asterisk Call Manager/1.3", "legacy")
    assert not common.pbx_version_mismatch("Asterisk Call Manager/9.0.0", "modern")
    assert not common.pbx_version_mismatch(None, "legacy")                 # unknown proves nothing


def test_config_refuses_an_unknown_event_model():
    from calling.config import ConfigError, load
    with pytest.raises(ConfigError):
        load({"CALLING_PBX_EVENTS": "eleven"})
    assert load({"CALLING_PBX_EVENTS": "legacy"}).enabled is False


def test_reserved_action_id_is_used_once_and_only_by_its_session():
    from services.calling.ami import AmiConfig, AmiError, AmiSession
    s = AmiSession(AmiConfig("127.0.0.1", 5038, "u", "secret", tls=False), lambda *_a: None)
    sent = []
    s._state, s._write = "ready", sent.append
    aid = s.reserve_action_id()
    assert aid.startswith(s.session_id + ":")
    _pending, used = s._issue("Ping", [], action_id=aid)
    assert used == aid and f"ActionID: {aid}\r\n".encode() in sent[0]
    with pytest.raises(AmiError):
        s._issue("Ping", [], action_id=aid)                                # still pending: never twice
    with pytest.raises(AmiError):
        s._issue("Ping", [], action_id="othersession-1:7")


def test_cdr_billsec_of_the_agent_leg_is_never_a_duration_in_legacy_mode():
    from services.calling.cdr_lookup import summarize
    rows = [{"dstchannel": "SIP/Mobily18-0000001b", "disposition": "ANSWERED", "billsec": 95, "matched_via": "userfield"}]
    assert summarize("mysql", rows, legacy=False)["billsec"] == 95
    s = summarize("mysql", rows, legacy=True)
    assert s["billsec"] is None and s["agent_billsec"] == 95 and s["billsec_source"] == "agent_leg"
    assert s["customer_answered"] is True                     # still only "may have talked" -> human review


def test_related_channels_follow_bridged_uniqueid():
    from services.calling.reconcile import related_channels
    inv = [{"Uniqueid": "9.1", "ChannelStateDesc": "Up", "BridgedUniqueID": AG},
           {"Uniqueid": "9.2", "ChannelStateDesc": "Up"}]
    rel = related_channels(A_ID, {AG}, inv)
    assert rel == [{"uniqueid": "9.1", "state": "Up", "bridge": True}]


# --------------------------------------------------------------------------- dialplan (pure)
WIN = [{"days": [0, 1, 2, 3, 4], "start": "09:00", "end": "21:00"}]
BASE = {"context": CONTEXT, "driver": "SIP", "trunk": "Mobily18", "max_call_seconds": 600, "ring_seconds": 30,
        "record": True, "calling_windows": WIN}


TRUNK_SELECTION = [
    " same => n,Set(COLLECTIONS_TRUNK=Mobily18)",
    ' same => n,GotoIf($["${SIPPEER(Mobily18,status):0:2}" = "OK"]?trunk)',
    " same => n,Set(COLLECTIONS_TRUNK=Mobily19)",
    ' same => n,GotoIf($["${SIPPEER(Mobily19,status):0:2}" = "OK"]?trunk)',
    " same => n,Set(COLLECTIONS_TRUNK=Mobily20)",
    ' same => n,GotoIf($["${SIPPEER(Mobily20,status):0:2}" = "OK"]?trunk)',
    " same => n,Set(COLLECTIONS_TRUNK=)",
    " same => n,Goto(notrunk)",
    " same => n(trunk),Dial(SIP/${COLLECTIONS_TRUNK}/${COLLECTIONS_DIAL_NUMBER},30,ib(collections-customer-created^s^1)"
    "U(collections-customer-answered)L($[${COLLECTIONS_GATE_LIMIT} * 1000]))",
    " same => n,UserEvent(CollectionsCall,AttemptId: ${COLLECTIONS_ATTEMPT},ChanUniqueid: ${UNIQUEID},State: dial_end,"
    "DialStatus: ${DIALSTATUS})",
    " same => n,Hangup()",
    " same => n(notrunk),UserEvent(CollectionsCall,AttemptId: ${COLLECTIONS_ATTEMPT},ChanUniqueid: ${UNIQUEID},"
    "State: no_trunk)",
    " same => n,Hangup(34)",
    " same => n(deny),UserEvent(CollectionsCall,AttemptId: ${COLLECTIONS_ATTEMPT},ChanUniqueid: ${UNIQUEID},State: denied)",
]


def test_dialplan_national_sa_with_trunk_selection_and_legacy_events():
    from services.calling import dialplan
    trunks = ["Mobily18", "Mobily19", "Mobily20"]
    text = dialplan.render({**BASE, "trunks": trunks, "dial_format": "national_sa", "pbx_events": "legacy"})
    lines = text.splitlines()
    # ONE Dial, of the first trunk whose chan_sip status is OK; after it, whatever DIALSTATUS: Hangup
    assert [ln for ln in lines if "Dial(" in ln] == [TRUNK_SELECTION[8]]
    at = lines.index(TRUNK_SELECTION[0])
    assert lines[at:at + len(TRUNK_SELECTION)] == TRUNK_SELECTION
    assert '"${DIALSTATUS}" !=' not in text and "CHANUNAVAIL" not in text and "(done)" not in text
    # selection right before the Dial: after the national number is built and checked
    assert lines.index(" same => n,Set(COLLECTIONS_DIAL_NUMBER=0${COLLECTIONS_GATE_DESTINATION:4})") < at
    assert ' same => n,GotoIf($[${REGEX("^[+]966[0-9]{9}$" ${COLLECTIONS_GATE_DESTINATION})} != 1]?deny)' in lines
    # the gate itself is unchanged: AGI before any Dial, E.164 checks, the destination is the Exten
    assert lines.index(" same => n,AGI(${GLOBAL(COLLECTIONS_AGI_URL)})") < at
    assert ' same => n,GotoIf($["${COLLECTIONS_GATE_DESTINATION}" != "${EXTEN}"]?deny)' in lines
    # every UserEvent names its own channel (Asterisk 11 has no Linkedid)
    events = [ln for ln in lines if "UserEvent(CollectionsCall" in ln]
    assert len(events) == 10 and all("AttemptId: ${COLLECTIONS_ATTEMPT},ChanUniqueid: ${UNIQUEID}," in e for e in events)
    assert "PJSIP_ENDPOINT" not in text
    assert "trunks=Mobily18,Mobily19,Mobily20 (first with SIPPEER status OK, one Dial, no retry)" in lines[2]


def _apps(text):
    """The rendered context as ShowDialPlan lists it: [(application, data)]."""
    import re
    out = []
    for ln in text.split("\n\n[collections-customer-created]")[0].splitlines():
        m = re.match(r"^(?:exten => _\+X\.,1|\s*same => n(?:\([a-z]+\))?),([A-Za-z]+)\((.*)\)$", ln)
        if m:
            out.append((m.group(1), m.group(2)))
    return out


def test_verify_checks_the_trunk_selection_and_the_single_dial():
    from calling.verify import dialplan_problems
    from services.calling import dialplan
    trunks = ["Mobily18", "Mobily19", "Mobily20"]
    text = dialplan.render({**BASE, "trunks": trunks, "dial_format": "national_sa", "pbx_events": "legacy"})
    assert dialplan_problems(_apps(text), ",".join(trunks)) == []
    assert dialplan_problems(_apps(text), "Mobily19,Mobily18,Mobily20")                  # wrong order
    assert dialplan_problems(_apps(dialplan.render(BASE)), "Mobily18") == []              # single trunk unchanged
    assert dialplan_problems(_apps(dialplan.render(BASE)), "Mobily18,Mobily19")           # not the selection


def test_dialplan_new_policy_keys_default_to_the_unchanged_output():
    from services.calling import dialplan
    for driver in ("PJSIP", "SIP"):
        plain = dialplan.render({**BASE, "driver": driver})
        assert dialplan.render({**BASE, "driver": driver, "trunks": ["Mobily18"], "dial_format": "e164",
                                "pbx_events": "modern"}) == plain
        assert "ChanUniqueid" not in plain and "COLLECTIONS_DIAL_NUMBER" not in plain and "(done)" not in plain
    with pytest.raises(dialplan.DialplanPolicyError):            # the selection needs chan_sip's SIPPEER
        dialplan.render({**BASE, "driver": "PJSIP", "trunks": ["Mobily18", "Mobily19"]})


@pytest.mark.parametrize("bad", [{"trunks": ["Mobily19", "Mobily18"]}, {"trunks": []}, {"trunks": ["Mobily18", "Mobily18"]},
                                 {"trunks": "Mobily18"}, {"trunks": ["Mobily18", "bad trunk"]},
                                 {"dial_format": "national"}, {"pbx_events": "11"}])
def test_dialplan_new_policy_validation(bad):
    from services.calling import dialplan
    with pytest.raises(dialplan.DialplanPolicyError):
        dialplan.render({**BASE, **bad})


# --------------------------------------------------------------------------- database rig
@pytest.fixture
def legacy_env(monkeypatch):
    monkeypatch.setenv("CALLING_PBX_EVENTS", "legacy")
    monkeypatch.setenv("CALLING_DRIVER", "SIP")


def legacy_ctx(epoch_uuid=None, banner="Asterisk Call Manager/1.3", session=None):
    ident = PbxIdentity(epoch_uuid or str(uuid.uuid4()), "2026-10-21T05:00:00", None, banner)
    c = types.SimpleNamespace(session=session or types.SimpleNamespace(session_id=uuid.uuid4().hex[:12], ready=True),
                              identity=ident, previous=None, pbx_restarted=False)
    c.epoch = ident.epoch
    return c


def make_rig(cl_app, pgc):
    """A dispatched click-to-call on a fresh epoch whose Originate ActionID is registered."""
    _c, pid = new_contract(pgc, "legacy")
    user = new_user(pgc, name="Legacy")
    case = make_case(pgc, user, pid)
    contact = add_contact(pgc, case, number(4), default=True)
    c = legacy_ctx()
    aid, ext = str(uuid.uuid4()), free_extension(pgc)
    action_id = f"{c.session.session_id}:{random.randint(1, 10**6)}"
    run(pgc, "INSERT INTO call_attempts (id, mode, state, case_id, collector_user_id, created_by, extension, contact_id, "
             "destination) VALUES (%s, 'click_to_call', 'prepared', %s, %s, %s, %s, %s, %s)",
        aid, case, user, user, ext, contact, DEST)
    run(pgc, "UPDATE call_attempts SET state = 'authorized', authorized_at = now() WHERE id = %s", aid)
    run(pgc, "UPDATE call_attempts SET pbx_epoch = %s, pbx_channel_id = %s WHERE id = %s", c.epoch, f"cawp-{aid}", aid)
    run(pgc, "INSERT INTO call_reservations (attempt_id, collector_user_id, case_id, extension) VALUES (%s, %s, %s, %s)",
        aid, user, case, ext)
    run(pgc, "INSERT INTO call_dispatch_queue (attempt_id, expected_epoch, context, driver, agent_ring_seconds, "
             "max_call_seconds, created_by, expires_at) VALUES (%s, %s, %s, 'SIP', 30, 600, %s, now() + interval '1 minute')",
        aid, c.epoch, CONTEXT, user)
    run(pgc, "UPDATE call_dispatch_queue SET state = 'sending', claimed_at = now(), claimed_by = 't', ami_action_id = %s "
             "WHERE attempt_id = %s", action_id, aid)
    run(pgc, "UPDATE call_dispatch_queue SET state = 'submitted', finished_at = now(), result_code = 'submitted', "
             "ami_response = 'Success' WHERE attempt_id = %s", aid)
    run(pgc, "UPDATE call_attempts SET state = 'dispatched', dispatched_at = now(), started_at = now() WHERE id = %s", aid)
    cons = E.EvidenceConsumer(cl_app, host_label="issabel-test", worker_id="t")
    cons.correlator.refresh_seconds = 0
    cons.on_session_start(c)
    a, cu, cu2, x = uids()
    return types.SimpleNamespace(attempt=aid, user=user, case=case, ext=ext, ctx=c, cons=cons, action_id=action_id,
                                 A=a, C=cu, C2=cu2, X=x, achan=f"SIP/{ext}-0000001a", cchan="SIP/Mobily18-0000001b",
                                 c2chan="SIP/Mobily19-0000001c", gate=False)


@pytest.fixture
def rig(cl_app, pgc, legacy_env):
    return make_rig(cl_app, pgc)


def feed(rig, frames):
    for f in frames:
        if not rig.gate and f.event == "UserEvent" and f.get("State") == "authorized":
            connect_run("INSERT INTO call_agi_decisions (decision, reason, attempt_id, token_id, max_call_seconds) "
                        "VALUES ('allow', 'allowed', %s, gen_random_uuid(), 600)", rig.attempt)
            rig.gate = True
        rig.cons.on_event(f, rig.ctx)


def agent_answers(r, ts=None):
    """Ringing, answer and the dialplan's first event BEFORE the OriginateResponse (held, then bound)."""
    t = (lambda k: None) if ts is None else (lambda k: str(ts + k))
    return [newchannel(r.achan, r.A, ts=t(0)), newstate(r.achan, r.A, "5", ts=t(0.1)),
            newstate(r.achan, r.A, "6", ts=t(2)), user_event(r.attempt, "agent_answered", r.A, ts=t(2.1)),
            originate_response(r.action_id, r.ext, r.A), user_event(r.attempt, "authorized", r.A, ts=t(2.3)),
            user_event(r.attempt, "customer_dialing", r.A, ts=t(2.4))]


def customer_answers(r, cu=None, cchan=None, ts=None):
    cu, cchan = cu or r.C, cchan or r.cchan
    t = (lambda k: None) if ts is None else (lambda k: str(ts + k))
    return [newchannel(cchan, cu, context="from-trunk", ts=t(0)),                  # before Dial Begin: held
            user_event(r.attempt, "customer_created", cu, ts=t(0.01)),
            dial_begin(r.achan, r.A, cchan, cu, ts=t(0.02)),
            newstate(cchan, cu, "5", ts=t(1)), newstate(cchan, cu, "6", ts=t(5)),
            user_event(r.attempt, "customer_answered", cu, ts=t(5.01))]


def customer_hangs_up(r, cu=None, cchan=None, link=True, ts=None):
    cu, cchan = cu or r.C, cchan or r.cchan
    t = (lambda k: None) if ts is None else (lambda k: str(ts + k))
    out = [hangup_request(cchan, cu, ts=t(0)), hangup_request(r.achan, r.A, soft=True, ts=t(0.01))]
    if link:
        out.append(bridge("Unlink", r.achan, r.A, cchan, cu, ts=t(0.02)))
    out += [hangup(cchan, cu, ts=t(0.03)), user_event(r.attempt, "customer_ended", cu, ts=t(0.04), Cause="16"),
            dial_end(r.achan, r.A, "ANSWER", ts=t(0.05)), hangup(r.achan, r.A, ts=t(0.06)),
            user_event(r.attempt, "agent_ended", r.A, ts=t(0.07), Cause="16", DialStatus="ANSWER")]
    return out


FINAL = ("state, reason_code, hangup_cause, duration_seconds, duration_source, agent_ringing_at, agent_answered_at, "
         "customer_dialing_at, customer_answered_at, bridged_at, ended_at")


def final(pgc, a):
    return q1(pgc, f"SELECT {FINAL} FROM call_attempts WHERE id = %s", a)


def legs(pgc, a):
    return dict(qall(pgc, "SELECT uniqueid, role FROM call_legs WHERE attempt_id = %s", a))


# --------------------------------------------------------------------------- consumer + projection
def test_action_id_binds_the_agent_leg_and_a_link_makes_the_conversation(rig, pgc):
    feed(rig, agent_answers(rig))
    assert legs(pgc, rig.attempt) == {rig.A: "agent"}
    assert state(pgc, rig.attempt) == ("customer_dialing", None)            # gate passed, Dial not seen yet
    feed(rig, customer_answers(rig))
    assert legs(pgc, rig.attempt) == {rig.A: "agent", rig.C: "customer"}
    assert state(pgc, rig.attempt) == ("customer_answered", None)            # answered, not yet bridged
    feed(rig, [bridge("Link", rig.achan, rig.A, rig.cchan, rig.C)])
    assert state(pgc, rig.attempt) == ("bridged", None)
    feed(rig, customer_hangs_up(rig))
    st = final(pgc, rig.attempt)
    assert st[:3] == ("ended", "customer_hung_up", 16) and st[3] is not None and st[4] == "pbx"
    assert all(st[5:])                                                     # ringing too: the held events were bound
    # events held before the binding were stored once, attributed, ordered by receipt
    rows = qall(pgc, "SELECT event, state, uniqueid, payload->>'pbx_ts_source' FROM call_events "
                     "WHERE attempt_id = %s ORDER BY pbx_ts", rig.attempt)
    assert rows[0][:3] == ("Newchannel", None, rig.A) and rows[3][:2] == ("UserEvent", "agent_answered")
    assert rows[4][0] == "OriginateResponse" and all(r[3] == "receipt" for r in rows)
    assert sum(1 for r in rows if r[1] == "customer_created") == 1
    assert q1(pgc, "SELECT count(*) FROM call_events WHERE pbx_epoch = %s AND attempt_id IS NULL", rig.ctx.epoch)[0] == 0
    assert reservation(pgc, rig.attempt) == (True, None)                   # the wrap-up releases it


def test_pbx_timestamps_are_used_when_the_pbx_sends_them(rig, pgc):
    feed(rig, agent_answers(rig, ts=1792601000) + customer_answers(rig, ts=1792601003)
         + [bridge("Link", rig.achan, rig.A, rig.cchan, rig.C, ts="1792601008.050")]
         + customer_hangs_up(rig, ts=1792601050.05))
    st = final(pgc, rig.attempt)
    assert st[:4] == ("ended", "customer_hung_up", 16, 42)                 # Link -> Unlink, not billsec


def test_two_answered_legs_without_a_link_are_not_a_conversation(rig, pgc):
    feed(rig, agent_answers(rig) + customer_answers(rig) + customer_hangs_up(rig, link=False))
    st = final(pgc, rig.attempt)
    assert st[:4] == ("ended", "not_bridged", 16, None) and st[9] is None


def test_a_link_with_a_foreign_channel_is_ambiguous(rig, pgc):
    feed(rig, agent_answers(rig) + customer_answers(rig)
         + [bridge("Link", rig.achan, rig.A, "SIP/402-0000009f", rig.X)])
    assert state(pgc, rig.attempt) == ("uncertain", "bridge_topology_ambiguous")
    assert reservation(pgc, rig.attempt) == (True, None)


@pytest.mark.parametrize("reason,code", [("3", "agent_no_answer"), ("5", "agent_busy"), ("8", "agent_unavailable"),
                                         ("0", "agent_unavailable")])
def test_originate_failure_fails_the_attempt_with_the_reason(rig, pgc, reason, code):
    # the agent phone rang (its events are never bound: the failure names no channel)
    feed(rig, [newchannel(rig.achan, rig.A), newstate(rig.achan, rig.A, "5"), hangup(rig.achan, rig.A, "19"),
               originate_response(rig.action_id, rig.ext, None, ok=False, reason=reason)])
    assert state(pgc, rig.attempt) == ("failed", code)
    assert reservation(pgc, rig.attempt) == (False, "no_customer_contact")
    assert legs(pgc, rig.attempt) == {}


def test_a_response_that_does_not_match_the_dispatch_binds_nothing(rig, pgc):
    feed(rig, [originate_response(rig.action_id, rig.ext, rig.A, exten="+966500000009")])
    assert state(pgc, rig.attempt) == ("uncertain", "originate_response_mismatch")
    assert legs(pgc, rig.attempt) == {}
    feed(rig, [originate_response(f"{rig.ctx.session.session_id}:999999999", rig.ext, rig.X)])   # unknown ActionID
    assert q1(pgc, "SELECT count(*) FROM call_events WHERE uniqueid = %s", rig.X)[0] == 0


def test_another_extensions_channel_is_not_bound(rig, pgc):
    r = originate_response(rig.action_id, "4999", rig.A)                    # SIP/4999-...: not the attempt's
    feed(rig, [r])
    assert state(pgc, rig.attempt) == ("uncertain", "originate_response_mismatch")


def test_a_second_customer_leg_after_congestion_is_never_a_clean_call(rig, pgc):
    """The dialplan never re-dials (trunks = selection before ONE Dial); if a second customer leg
    shows up anyway, the attempt goes to review instead of looking like one clean call."""
    feed(rig, agent_answers(rig)
         + [newchannel(rig.cchan, rig.C), dial_begin(rig.achan, rig.A, rig.cchan, rig.C),
            hangup(rig.cchan, rig.C, "34"), dial_end(rig.achan, rig.A, "CONGESTION"),
            user_event(rig.attempt, "dial_end", rig.A, DialStatus="CONGESTION")]
         + customer_answers(rig, cu=rig.C2, cchan=rig.c2chan)
         + [bridge("Link", rig.achan, rig.A, rig.c2chan, rig.C2)])
    assert state(pgc, rig.attempt) == ("uncertain", "multiple_customer_legs")
    feed(rig, customer_hangs_up(rig, cu=rig.C2, cchan=rig.c2chan))
    assert final(pgc, rig.attempt)[:4] == ("uncertain", "multiple_customer_legs", None, None)
    assert legs(pgc, rig.attempt) == {rig.A: "agent", rig.C: "customer", rig.C2: "customer"}


def test_no_trunk_fails_the_attempt_without_a_dial(rig, pgc):
    feed(rig, agent_answers(rig) + [user_event(rig.attempt, "no_trunk", rig.A),
                                    user_event(rig.attempt, "agent_ended", rig.A, Cause="34", DialStatus=""),
                                    hangup(rig.achan, rig.A, "34")])
    st = final(pgc, rig.attempt)
    assert st[:3] == ("failed", "no_trunk_available", 34)
    assert legs(pgc, rig.attempt) == {rig.A: "agent"}


def hold_redirect(r, new_uid):
    """Asterisk 11 AMI Redirect of the bridged agent channel (hold): AsyncGoto masquerade, the
    leg's own structure becomes `<name><ZOMBIE>` and hangs up; the call goes on in `new_uid`."""
    tmp = f"AsyncGoto/{r.achan}"
    return [newchannel(tmp, new_uid), masquerade(r.achan, tmp), rename(r.achan, f"{r.achan}<ZOMBIE>", r.A),
            rename(tmp, r.achan, new_uid), bridge("Unlink", r.achan, r.A, r.cchan, r.C),
            hangup(f"{r.achan}<ZOMBIE>", r.A)]


def test_hold_redirect_masquerade_never_ends_with_a_wrong_duration(rig, pgc):
    feed(rig, agent_answers(rig) + customer_answers(rig) + [bridge("Link", rig.achan, rig.A, rig.cchan, rig.C)])
    assert state(pgc, rig.attempt) == ("bridged", None)
    feed(rig, hold_redirect(rig, rig.X))
    assert state(pgc, rig.attempt) == ("uncertain", "leg_masqueraded")
    # the call goes on: re-Link of the surviving channel, then the agent hangs up
    feed(rig, [bridge("Link", rig.achan, rig.X, rig.cchan, rig.C), hangup_request(rig.achan, rig.X),
               hangup_request(rig.cchan, rig.C, soft=True), bridge("Unlink", rig.achan, rig.X, rig.cchan, rig.C),
               hangup(rig.achan, rig.X), hangup(rig.cchan, rig.C),
               user_event(rig.attempt, "customer_ended", rig.C, Cause="16")])
    st = final(pgc, rig.attempt)
    assert st[:4] == ("uncertain", "leg_masqueraded", None, None)      # reconciliation / review decide
    # the zombie's Hangup is stored as what it is, never as the agent leg's hangup
    assert q1(pgc, "SELECT hungup_pbx_ts FROM call_legs WHERE pbx_epoch = %s AND uniqueid = %s",
              rig.ctx.epoch, rig.A)[0] is None
    assert q1(pgc, "SELECT count(*) FROM call_events WHERE attempt_id = %s AND event = 'MasqueradeZombie'",
              rig.attempt)[0] == 1


@pytest.mark.parametrize("which", ["masquerade_customer", "masquerade_agent", "rename", "zombie"])
def test_any_masquerade_evidence_of_a_leg_is_ambiguous(rig, pgc, which):
    feed(rig, agent_answers(rig) + customer_answers(rig) + [bridge("Link", rig.achan, rig.A, rig.cchan, rig.C)])
    frame = {"masquerade_customer": masquerade(rig.cchan, f"AsyncGoto/{rig.cchan}"),
             "masquerade_agent": masquerade("Local/9@park-0000001;2", f"AsyncGoto/{rig.achan}"),
             "rename": rename(rig.achan, f"{rig.achan}<ZOMBIE>", rig.A),
             "zombie": hangup(f"{rig.cchan}<ZOMBIE>", rig.C)}[which]
    feed(rig, [frame])
    assert state(pgc, rig.attempt) == ("uncertain", "leg_masqueraded")


def test_issabel_dialer_traffic_interleaved_with_ours_is_ignored(rig, pgc):
    """Issabel's own predictive dialer shares the AMI stream: its OriginateResponses (its ActionIDs)
    and its Local/...@from-queue Dial and Bridge events never touch our attempt."""
    d1, d2, d3 = uids(3)
    loc1, loc2 = "Local/0500000009@from-queue-0000002a;1", "Local/0500000009@from-queue-0000002a;2"
    foreign = [
        originate_response("1729000001", "403", d1, exten="0500000009", context="from-internal"),
        F(("Event", "OriginateResponse"), ("Privilege", "call,all"), ("ActionID", "dialer-77"), ("Response", "Success"),
          ("Channel", loc1), ("Context", "from-queue"), ("Exten", "0500000009"), ("Reason", "4"), ("Uniqueid", d1)),
        newchannel(loc1, d1, context="from-queue"), newchannel(loc2, d2, context="from-queue"),
        dial_begin(loc2, d2, "SIP/Mobily20-0000002c", d3), newstate("SIP/Mobily20-0000002c", d3, "6"),
        bridge("Link", loc2, d2, "SIP/Mobily20-0000002c", d3), masquerade(loc1, f"AsyncGoto/{loc1}"),
        dial_end(loc2, d2, "ANSWER"), hangup(f"{loc1}<ZOMBIE>", d1), hangup("SIP/Mobily20-0000002c", d3)]
    ours = agent_answers(rig) + customer_answers(rig) + [bridge("Link", rig.achan, rig.A, rig.cchan, rig.C)]
    mixed = [f for pair in zip(ours, foreign + [None] * len(ours)) for f in pair if f is not None]
    feed(rig, mixed)
    assert state(pgc, rig.attempt) == ("bridged", None)
    assert legs(pgc, rig.attempt) == {rig.A: "agent", rig.C: "customer"}
    assert q1(pgc, "SELECT count(*) FROM call_events WHERE uniqueid = ANY(%s)", [d1, d2, d3])[0] == 0
    assert q1(pgc, "SELECT count(*) FROM call_legs WHERE uniqueid = ANY(%s)", [d1, d2, d3])[0] == 0
    feed(rig, customer_hangs_up(rig))
    assert final(pgc, rig.attempt)[:2] == ("ended", "customer_hung_up")


def test_a_duplicate_link_is_harmless(rig, pgc):
    feed(rig, agent_answers(rig, ts=1792601000) + customer_answers(rig, ts=1792601003)
         + [bridge("Link", rig.achan, rig.A, rig.cchan, rig.C, ts="1792601008.050"),
            bridge("Link", rig.achan, rig.A, rig.cchan, rig.C, ts="1792601008.050"),
            bridge("Link", rig.achan, rig.A, rig.cchan, rig.C, ts="1792601009.000")]
         + customer_hangs_up(rig, ts=1792601050.05))
    assert final(pgc, rig.attempt)[:4] == ("ended", "customer_hung_up", 16, 42)


def test_an_unlink_without_its_link_is_uncertain_not_unbridged(rig, pgc):
    feed(rig, agent_answers(rig) + customer_answers(rig) + customer_hangs_up(rig, link=True))
    assert final(pgc, rig.attempt)[:4] == ("uncertain", "bridge_unobserved", None, None)


def test_overlapping_customer_legs_are_ambiguous(rig, pgc):
    feed(rig, agent_answers(rig) + [dial_begin(rig.achan, rig.A, rig.cchan, rig.C),
                                    dial_begin(rig.achan, rig.A, rig.c2chan, rig.C2)])
    assert state(pgc, rig.attempt) == ("uncertain", "multiple_customer_legs")


def test_agi_bound_leg_attributes_events_before_the_originate_response(rig, pgc):
    # what the FastAGI hook writes when it allows (tested with the FastAGI server below)
    connect_run("INSERT INTO call_legs (pbx_epoch, uniqueid, attempt_id, role, channel) "
                "VALUES (%s, %s, %s, 'agent', %s)", rig.ctx.epoch, rig.A, rig.attempt, rig.achan)
    feed(rig, [user_event(rig.attempt, "agent_answered", rig.A), user_event(rig.attempt, "authorized", rig.A),
               *customer_answers(rig), bridge("Link", rig.achan, rig.A, rig.cchan, rig.C)])
    assert state(pgc, rig.attempt) == ("bridged", None)
    # an OriginateResponse naming ANOTHER channel later is a conflict, never a second agent leg
    feed(rig, [originate_response(rig.action_id, rig.ext, rig.X, chan_suffix="000000ff")])
    assert state(pgc, rig.attempt) == ("uncertain", "agent_leg_conflict")
    assert legs(pgc, rig.attempt)[rig.A] == "agent" and rig.X not in legs(pgc, rig.attempt)


def test_user_events_of_unbound_or_contradicting_channels_stay_untrusted(rig, pgc):
    feed(rig, agent_answers(rig))
    rig.gate = True
    # body and header disagree: never attributed
    feed(rig, [user_event(rig.attempt, "customer_answered", rig.A, top_uid=rig.X)])
    # a claim from a channel that never binds: held, then stored unattributed once it expires
    feed(rig, [user_event(rig.attempt, "authorized", rig.X)])
    rig.cons.correlator.pending_ttl = 0
    feed(rig, [newstate(rig.achan, rig.A, "6")])
    rows = qall(pgc, "SELECT state, payload->>'claimed_attempt_id', payload->>'untrusted' FROM call_events "
                     "WHERE pbx_epoch = %s AND attempt_id IS NULL AND event = 'UserEvent' ORDER BY id", rig.ctx.epoch)
    assert rows == [("customer_answered", rig.attempt, "true"), ("authorized", rig.attempt, "true")]
    assert state(pgc, rig.attempt) == ("customer_dialing", None)


def test_modern_mode_ignores_legacy_channels(cl_app, pgc, monkeypatch):
    monkeypatch.delenv("CALLING_PBX_EVENTS", raising=False)
    r = make_rig(cl_app, pgc)
    feed(r, agent_answers(r))
    assert q1(pgc, "SELECT count(*) FROM call_events WHERE attempt_id = %s", r.attempt)[0] == 0
    assert state(pgc, r.attempt) == ("dispatched", None)


# --------------------------------------------------------------------------- FastAGI (legacy)
@pytest.fixture
def gate(pgc, legacy_env):
    _c, pid = new_contract(pgc, "agi legacy")
    u = new_user(pgc)
    case = make_case(pgc, u, pid)
    contact = add_contact(pgc, case, "966500000001", default=True)
    epoch = f"{EPOCH}@2026-10-21T05:00:00"

    def attempt():
        a = insert_attempt(pgc, case, u, state="agent_answered", extension="7201", contact_id=contact,
                           destination="+966500000001", epoch=epoch, channel=True)
        return a, token_for(pgc, a, epoch=EPOCH)
    return types.SimpleNamespace(attempt=attempt, epoch=epoch)


def legacy_env_of(uid, chan="SIP/7201-0000004d"):
    return {"env": {"agi_uniqueid": uid, "agi_type": "SIP", "agi_channel": chan}}


@pytest.fixture(scope="module")
def agi_server5(cl_app):
    """The production exchange deadline (5 s): room for the 2.5 s wait for the binding."""
    from services.calling.agi import FastAgiServer, PeerAllowlist
    from test_calling_agi import AGI_KEY
    srv = FastAgiServer(cl_app, host="127.0.0.1", port=0, secret=AGI_KEY,
                        allowed_peers=PeerAllowlist(["127.0.0.1/32"]), exchange_timeout=5.0)
    srv.start()
    yield srv
    srv.stop()


def originate_bound(epoch, attempt, uid, chan="SIP/7201-0000004d", *, refused=None, ok=True):
    """What the evidence consumer commits for the attempt's OriginateResponse (by its ActionID):
    the event, and (Success, not refused) the agent leg."""
    import json
    payload = {"Response": "Success" if ok else "Failure", "Reason": "4" if ok else "3", "correlated_by": "action_id",
               "Context": CONTEXT, "pbx_ts_source": "receipt", **({"binding": f"refused:{refused}"} if refused else {})}
    connect_run("INSERT INTO call_events (source, pbx_epoch, attempt_id, event, pbx_ts, uniqueid, channel, payload, "
                "dedupe_key) VALUES ('ami', %s, %s, 'OriginateResponse', extract(epoch FROM now()), %s, %s, %s, %s)",
                epoch, attempt, uid if ok else None, chan if ok else None, json.dumps(payload),
                f"ami:test-{uuid.uuid4().hex}")
    if ok and not refused:
        connect_run("INSERT INTO call_legs (pbx_epoch, uniqueid, attempt_id, role, channel) VALUES (%s, %s, %s, 'agent', %s)",
                    epoch, uid, attempt, chan)


def test_agi_accepts_the_asterisk_11_uniqueid_bound_by_the_originate_response(agi_server, gate, pgc):
    a, token = gate.attempt()
    uid = uids(1)[0]
    originate_bound(gate.epoch, a, uid)
    fake = FakeAsterisk(agi_server, attempt=a, token=token, **legacy_env_of(uid)).run()
    assert fake.allowed and decisions(pgc, a) == [("allow", "allowed")]
    assert q1(pgc, "SELECT bound_uniqueid, consumed_at IS NOT NULL FROM call_dispatch_tokens WHERE attempt_id = %s",
              a) == (uid, True)
    assert q1(pgc, "SELECT role, channel FROM call_legs WHERE attempt_id = %s AND uniqueid = %s", a, uid) == \
        ("agent", "SIP/7201-0000004d")


def test_agi_waits_for_the_binding_and_allows_when_it_lands_in_time(agi_server5, gate, pgc):
    import threading
    import time
    a, token = gate.attempt()
    uid = uids(1)[0]
    late = threading.Timer(0.8, originate_bound, args=(gate.epoch, a, uid))     # the consumer lags 0.8 s
    late.start()
    t0 = time.monotonic()
    fake = FakeAsterisk(agi_server5, attempt=a, token=token, **legacy_env_of(uid)).run()
    late.join()
    assert fake.allowed and decisions(pgc, a) == [("allow", "allowed")]
    assert 0.7 < time.monotonic() - t0 < 2.6


def test_agi_denies_when_the_binding_never_lands(agi_server5, gate, pgc):
    import time
    a, token = gate.attempt()
    t0 = time.monotonic()
    fake = FakeAsterisk(agi_server5, attempt=a, token=token, **legacy_env_of(uids(1)[0])).run()
    waited = time.monotonic() - t0
    assert not fake.allowed and decisions(pgc, a) == [("deny", "agent_leg_unbound")]
    assert 2.3 < waited < 4.5                                                # waited, inside the 5 s deadline
    assert q1(pgc, "SELECT consumed_at, bound_uniqueid FROM call_dispatch_tokens WHERE attempt_id = %s", a) == (None, None)
    assert q1(pgc, "SELECT count(*) FROM call_legs WHERE attempt_id = %s", a)[0] == 0


@pytest.mark.parametrize("kind,reason", [("refused", "agent_leg_refused"), ("failure", "originate_failed")])
def test_agi_denies_after_a_refused_or_failed_originate_response(agi_server, gate, pgc, kind, reason):
    a, token = gate.attempt()
    uid = uids(1)[0]
    if kind == "refused":
        originate_bound(gate.epoch, a, uid, refused="originate_response_mismatch")
    else:
        originate_bound(gate.epoch, a, uid, ok=False)
    fake = FakeAsterisk(agi_server, attempt=a, token=token, **legacy_env_of(uid)).run()
    assert not fake.allowed and decisions(pgc, a) == [("deny", reason)]
    assert q1(pgc, "SELECT consumed_at FROM call_dispatch_tokens WHERE attempt_id = %s", a)[0] is None


def test_agi_denies_another_channel_presenting_the_right_token(agi_server, gate, pgc):
    """The token leaked to another SIP/<ext> channel of the same extension: not the bound leg."""
    a, token = gate.attempt()
    bound, other = uids(2)
    originate_bound(gate.epoch, a, bound)
    fake = FakeAsterisk(agi_server, attempt=a, token=token, **legacy_env_of(other, chan="SIP/7201-0000009e")).run()
    assert not fake.allowed and decisions(pgc, a) == [("deny", "agent_channel_mismatch")]
    assert q1(pgc, "SELECT consumed_at, bound_uniqueid FROM call_dispatch_tokens WHERE attempt_id = %s", a) == (None, None)
    # the bound leg itself is allowed
    fake = FakeAsterisk(agi_server, attempt=a, token=token, **legacy_env_of(bound)).run()
    assert fake.allowed


def test_agi_denies_a_channel_bound_only_by_a_previous_hook_call(agi_server, gate, pgc):
    """An agent leg row without the attempt's OriginateResponse proves nothing."""
    a, token = gate.attempt()
    uid = uids(1)[0]
    run(pgc, "INSERT INTO call_legs (pbx_epoch, uniqueid, attempt_id, role) VALUES (%s, %s, %s, 'agent')",
        gate.epoch, uid, a)
    fake = FakeAsterisk(agi_server, attempt=a, token=token, **legacy_env_of(uid)).run()
    assert not fake.allowed and decisions(pgc, a) == [("deny", "agent_leg_unbound")]


@pytest.mark.parametrize("env,reason", [
    ({"agi_uniqueid": "cawp-{a}", "agi_type": "SIP", "agi_channel": "SIP/7201-0000004d"}, "bad_channel_id"),
    ({"agi_uniqueid": "1792601562.77", "agi_type": "SIP", "agi_channel": "SIP/7299-0000004d"}, "extension_mismatch"),
    ({"agi_uniqueid": "1792601562.78", "agi_type": "SIP", "agi_channel": "SIP/7201-0000004d",
      "agi_extension": "+966500000009"}, "token_mismatch"),
])
def test_agi_legacy_keeps_every_other_check(agi_server, gate, pgc, env, reason):
    a, token = gate.attempt()
    env = {k: v.replace("{a}", a) for k, v in env.items()}
    fake = FakeAsterisk(agi_server, attempt=a, token=token, env=env).run()
    assert not fake.allowed
    rows = qall(pgc, "SELECT decision, reason FROM call_agi_decisions WHERE channel_id = %s OR attempt_id = %s "
                     "ORDER BY id DESC LIMIT 1", env["agi_uniqueid"], a)
    assert rows == [("deny", reason)]
    assert q1(pgc, "SELECT consumed_at FROM call_dispatch_tokens WHERE attempt_id = %s", a)[0] is None


# --------------------------------------------------------------------------- dispatcher (legacy)
class LegacySession:
    """The part of AmiSession the dispatcher uses, with reserved ActionIDs."""

    def __init__(self, response="Success"):
        self.session_id, self.ready, self.sent, self.response, self._n = uuid.uuid4().hex[:12] + "-1", True, [], response, 0

    def reserve_action_id(self):
        self._n += 1
        return f"{self.session_id}:{self._n}"

    def send(self, action, fields, timeout=None, action_id=None):
        self.sent.append((action, list(fields), action_id))
        return Frame([("Response", self.response), ("ActionID", action_id or "x:1")])


def _placed(cl_client, hdr, c2c):
    st, body = place(cl_client, hdr, c2c.c1, c2c.case, c2c.contact)
    assert st == 201, body
    return body["data"]["id"]


def _drain(cl_app, ctx):
    from services.calling import dispatch as D
    d = D.Dispatcher(cl_app, lambda: ctx, "test-worker")
    for _ in range(500):
        if not d.run_once():
            return


def test_dispatcher_records_the_originate_action_id_before_sending(cl_app, cl_client, hdr, c2c, pgc, legacy_env):
    import psycopg2
    a = _placed(cl_client, hdr, c2c)
    sess_ = LegacySession()
    ctx = types.SimpleNamespace(session=sess_, identity=PbxIdentity(c2c.transport["epoch_uuid"], "2026-09-24T05:00:00",
                                                                    None, "Asterisk Call Manager/1.3"))
    ctx.epoch = ctx.identity.epoch
    assert ctx.epoch == c2c.transport["epoch"]
    _drain(cl_app, ctx)
    assert len(sess_.sent) == 1
    action, fields, action_id = sess_.sent[0]
    assert action == "Originate" and dict(fields)["Channel"] == f"SIP/{c2c.ext1}"
    assert f"__COLLECTIONS_ATTEMPT={a}" in [v for k, v in fields if k == "Variable"]
    assert action_id and action_id.startswith(sess_.session_id + ":")
    assert q1(pgc, "SELECT ami_action_id, state FROM call_dispatch_queue WHERE attempt_id = %s", a) == (action_id, "submitted")
    assert state(pgc, a) == ("dispatched", None)
    with pytest.raises(psycopg2.Error):                                    # recorded once
        run(pgc, "UPDATE call_dispatch_queue SET ami_action_id = 'x-1:2' WHERE attempt_id = %s", a)


@pytest.mark.parametrize("mode,banner", [("modern", "Asterisk Call Manager/1.3"), ("legacy", "Asterisk Call Manager/9.0.0")])
def test_dispatcher_refuses_when_the_pbx_version_contradicts_the_mode(cl_app, cl_client, hdr, c2c, pgc, monkeypatch,
                                                                       mode, banner):
    monkeypatch.setenv("CALLING_PBX_EVENTS", mode)
    a = _placed(cl_client, hdr, c2c)
    sess_ = LegacySession()
    ctx = types.SimpleNamespace(session=sess_, identity=PbxIdentity(c2c.transport["epoch_uuid"], "2026-09-24T05:00:00",
                                                                    None, banner))
    ctx.epoch = ctx.identity.epoch
    _drain(cl_app, ctx)
    assert sess_.sent == []
    assert state(pgc, a) == ("failed", "pbx_version_mismatch")
    assert queue(pgc, a)[:2] == ("refused", "pbx_version_mismatch")
    assert reservation(pgc, a) == (False, "no_customer_contact")


def test_dialplan_issabel_caller_id_before_the_one_dial():
    """issabel_trunk_ids: the caller ID IssabelPBX's own route would send (the Alitqan gateways answer
    the agent's bare extension with SIP 604), chosen for the selected trunk, right before the Dial."""
    from calling.verify import dialplan_problems
    from services.calling import dialplan
    trunks = ["Mobily18", "Mobily19", "Mobily20"]
    policy = {**BASE, "trunks": trunks, "dial_format": "national_sa", "pbx_events": "legacy",
              "issabel_trunk_ids": {"Mobily18": 2, "Mobily19": 3, "Mobily20": 4}}
    text = dialplan.render(policy)
    lines = text.splitlines()
    for t, n in policy["issabel_trunk_ids"].items():
        at = lines.index(f" same => n,Set(COLLECTIONS_TRUNK={t})")
        assert lines[at + 1] == f" same => n,Set(COLLECTIONS_TRUNK_ID={n})"
        assert "SIPPEER(" in lines[at + 2]
    cid = lines.index(" same => n(trunk),Set(COLLECTIONS_CID=${DB(AMPUSER/${COLLECTIONS_GATE_EXTENSION}/outboundcid)})")
    dial = next(i for i, ln in enumerate(lines) if "Dial(" in ln)
    assert lines[cid + 1:dial] == [
        " same => n,ExecIf($[${LEN(${COLLECTIONS_CID})} = 0]?Set(COLLECTIONS_CID=${GLOBAL(OUTCID_${COLLECTIONS_TRUNK_ID})}))",
        " same => n,ExecIf($[${LEN(${GLOBAL(FORCEDOUTCID_${COLLECTIONS_TRUNK_ID})})} != 0]"
        "?Set(COLLECTIONS_CID=${GLOBAL(FORCEDOUTCID_${COLLECTIONS_TRUNK_ID})}))",
        " same => n,ExecIf($[${LEN(${COLLECTIONS_CID})} != 0]?Set(CALLERID(all)=${COLLECTIONS_CID}))",
        " same => n,Set(CDR(outbound_cnum)=${CALLERID(num)})",
    ]
    assert lines[dial].startswith(" same => n,Dial(SIP/${COLLECTIONS_TRUNK}/${COLLECTIONS_DIAL_NUMBER},30,")
    assert dialplan_problems(_apps(text), ",".join(trunks)) == []
    assert "issabel_trunk_ids=Mobily18:2,Mobily19:3,Mobily20:4" in lines[2]
    # the gate is unchanged: the caller ID is set only after ALLOW and every re-check
    assert lines.index(' same => n,GotoIf($["${COLLECTIONS_GATE}" != "ALLOW"]?deny)') < cid
    # single trunk: same block, then the fixed Dial
    one = dialplan.render({**BASE, "driver": "SIP", "issabel_trunk_ids": {"Mobily18": 2}}).splitlines()
    i = one.index(" same => n,Set(COLLECTIONS_TRUNK_ID=2)")
    assert one[i + 1].startswith(" same => n,Set(COLLECTIONS_CID=") and "Dial(SIP/Mobily18/" in one[i + 6]


@pytest.mark.parametrize("bad", [{"issabel_trunk_ids": {"Mobily18": 2}},                     # Mobily19 missing
                                 {"issabel_trunk_ids": {"Mobily18": 2, "Mobily19": "3"}},
                                 {"issabel_trunk_ids": {"Mobily18": 2, "Mobily19": True}},
                                 {"issabel_trunk_ids": {"Mobily18": 2, "Mobily19": 0}},
                                 {"issabel_trunk_ids": {"Mobily18": 2, "Mobily19": 3, "X": 4}},
                                 {"issabel_trunk_ids": [2, 3]}])
def test_dialplan_issabel_trunk_ids_validation(bad):
    from services.calling import dialplan
    with pytest.raises(dialplan.DialplanPolicyError):
        dialplan.render({**BASE, "driver": "SIP", "trunks": ["Mobily18", "Mobily19"], **bad})
    with pytest.raises(dialplan.DialplanPolicyError):          # PJSIP has no IssabelPBX chan_sip trunks
        dialplan.render({**BASE, "driver": "PJSIP", "issabel_trunk_ids": {"Mobily18": 2}})
