"""Phase 5 P1: evidence -> attempt state projection (services/calling/projection.py), replaying
AMI event sequences RECORDED from the local test PBX (tests/fixtures/ami/*.json, written by
tests/test_calling_c2c_live.py with RECORD_AMI_FIXTURES=1) through the real evidence consumer.

The recordings are re-keyed to a fresh attempt id and a fresh PBX epoch per test.
"""
from __future__ import annotations

import json
import random
import types
import uuid
from pathlib import Path

import pytest

from services.calling import evidence as E
from services.calling import projection as P
from services.calling.ami import Frame, PbxIdentity
from test_calling_c2c_helpers import (add_contact, cl, cl_app, cl_client, free_extension, hdr, key, make_case,  # noqa: F401
                                      new_contract, new_user, note_body, number, pgc, q1, qall, reservation, rev, run, state)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "ami"


def recording(name, attempt_id):
    d = json.loads((FIXTURES / f"{name}.json").read_text())
    old = d["attempt_id"]
    return [Frame([(k, v.replace(old, attempt_id)) for k, v in f]) for f in d["frames"]]


def ctx():
    ident = PbxIdentity(str(uuid.uuid4()), "2026-09-24T05:00:00", None, "Asterisk Call Manager/9.0.0")
    c = types.SimpleNamespace(session=types.SimpleNamespace(session_id=uuid.uuid4().hex[:12]), identity=ident,
                              previous=None, pbx_restarted=False)
    c.epoch = ident.epoch
    return c


@pytest.fixture
def rig(cl_app, pgc):
    return make_rig(cl_app, pgc)


def make_rig(cl_app, pgc):
    """An authorized click-to-call attempt bound to a fresh epoch, with its reservation."""
    _c, pid = new_contract(pgc, "proj")
    user = new_user(pgc, name="Proj")
    case = make_case(pgc, user, pid)
    contact = add_contact(pgc, case, number(4), default=True)
    c = ctx()
    aid = str(uuid.uuid4())
    ext = free_extension(pgc)
    run(pgc, "INSERT INTO call_attempts (id, mode, state, case_id, collector_user_id, created_by, extension, contact_id, "
             "destination) VALUES (%s, 'click_to_call', 'prepared', %s, %s, %s, %s, %s, '+966500000004')",
        aid, case, user, user, ext, contact)
    run(pgc, "UPDATE call_attempts SET state = 'authorized', authorized_at = now() WHERE id = %s", aid)
    run(pgc, "UPDATE call_attempts SET pbx_epoch = %s, pbx_channel_id = %s WHERE id = %s", c.epoch, f"cawp-{aid}", aid)
    run(pgc, "INSERT INTO call_reservations (attempt_id, collector_user_id, case_id, extension) "
             "VALUES (%s, %s, %s, %s)", aid, user, case, ext)
    cons = E.EvidenceConsumer(cl_app, host_label="pbx-test", worker_id="t")
    cons.on_session_start(c)
    return types.SimpleNamespace(attempt=aid, user=user, case=case, ctx=c, cons=cons)


def feed(rig, frames):
    # A gated call has the FastAGI allow in the database before any customer event exists (the
    # hook commits it before the dialplan dials); a replay recreates it with the recording.
    if not getattr(rig, "gate", False) and any(f.event == "UserEvent" and f.get("State") == "authorized"
                                               for f in frames):
        from test_calling_c2c_helpers import connect_run
        connect_run("INSERT INTO call_agi_decisions (decision, reason, attempt_id, token_id, max_call_seconds) "
                    "VALUES ('allow', 'allowed', %s, gen_random_uuid(), 600)", rig.attempt)
        rig.gate = True
    for f in frames:
        rig.cons.on_event(f, rig.ctx)


FINAL_COLS = ("state, reason_code, hangup_cause, duration_seconds, duration_source, agent_answered_at, "
              "customer_dialing_at, customer_answered_at, bridged_at, ended_at")


def final(pgc, a):
    return q1(pgc, f"SELECT {FINAL_COLS} FROM call_attempts WHERE id = %s", a)


# --------------------------------------------------------------------------- recorded sequences
def test_recorded_answered_call_projects_to_bridged_then_ended(rig, pgc):
    frames = recording("answered_customer_hangs_up", rig.attempt)
    bridge_at = next(i for i, f in enumerate(frames) if f.event == "BridgeEnter" and f.get("Uniqueid").startswith("cawp-"))
    feed(rig, frames[:bridge_at + 1])
    assert state(pgc, rig.attempt) == ("bridged", None)                   # both legs are in one bridge
    feed(rig, frames[bridge_at + 1:])
    st = final(pgc, rig.attempt)
    assert st[:3] == ("ended", "customer_hung_up", 16) and st[4] == "pbx"
    assert 19 <= st[3] <= 22                                              # ...6 talks 20 s, then hangs up
    assert all(st[5:])
    assert reservation(pgc, rig.attempt) == (True, None)                  # the wrap-up releases it
    audit = qall(pgc, "SELECT action FROM audit_events WHERE entity_id = %s ORDER BY id", rig.attempt)
    assert [r[0] for r in audit] == ["call.ended"]


def test_recorded_busy_and_agent_no_answer(cl_app, pgc):
    for name, expect, released in (("busy_customer", ("ended", "customer_busy", 17), (True, None)),
                                   ("agent_no_answer", ("failed", "agent_no_answer", 0),
                                    (False, "no_customer_contact"))):
        r = make_rig(cl_app, pgc)
        feed(r, recording(name, r.attempt))
        st = final(pgc, r.attempt)
        assert st[:3] == expect, name
        assert st[3] is None and st[4] is None and st[8] is None           # unknown duration, never bridged
        assert reservation(pgc, r.attempt) == released, name


def test_out_of_order_arrival_gives_the_same_result(cl_app, pgc):
    in_order = make_rig(cl_app, pgc)
    feed(in_order, recording("answered_customer_hangs_up", in_order.attempt))
    expected = final(pgc, in_order.attempt)
    for seed in (1, 2, 3, "reversed"):
        r = make_rig(cl_app, pgc)
        frames = recording("answered_customer_hangs_up", r.attempt)
        if seed == "reversed":
            frames = frames[::-1]                                         # hangups arrive before the bridge
        else:
            random.Random(seed).shuffle(frames)
        feed(r, frames)
        got = final(pgc, r.attempt)
        assert got[:5] == expected[:5], seed
        # the same PBX facts (PBX Timestamp), whatever the arrival order
        assert [x is not None for x in got[5:]] == [x is not None for x in expected[5:]], seed
        assert got[8] - got[7] == expected[8] - expected[7], seed          # answer -> bridge from PBX time


def test_missing_bridge_is_not_a_conversation(rig, pgc, cl_client, hdr):
    frames = [f for f in recording("answered_customer_hangs_up", rig.attempt)
              if f.event not in ("BridgeEnter", "BridgeLeave")]
    feed(rig, frames)
    st = final(pgc, rig.attempt)
    assert st[:3] == ("ended", "not_bridged", 16)                         # both legs answered, never bridged
    assert st[7] is not None and st[8] is None and st[3] is None
    r = cl_client.post(f"/v2/calls/{rig.attempt}/wrapup", headers=hdr(rig.user, key=key()),
                       json={"expected_revision": rev(pgc, rig.attempt), "outcome": "connected",
                             "note": note_body()})
    assert (r.status_code, r.get_json()["code"]) == (409, "conversation_not_evidenced")


def test_bridge_left_before_the_other_leg_joined_is_not_a_conversation(rig, pgc):
    frames = recording("answered_customer_hangs_up", rig.attempt)
    agent = f"cawp-{rig.attempt}"
    out = []
    for f in frames:                                                      # the customer leaves before the agent enters
        if f.event == "BridgeEnter" and f.get("Uniqueid") == agent:
            enter = f
            continue
        out.append(f)
        if f.event == "BridgeLeave" and f.get("Uniqueid") != agent:
            fields = [(k, v) for k, v in enter.fields if k != "Timestamp"] + [("Timestamp", str(float(f.get("Timestamp")) + 0.001))]
            out.append(Frame(fields))
    feed(rig, out)
    assert final(pgc, rig.attempt)[:2] == ("ended", "not_bridged")


def test_duplicate_user_event_changes_nothing(rig, pgc):
    frames = recording("answered_customer_hangs_up", rig.attempt)
    ue = next(f for f in frames if f.event == "UserEvent" and f.get("State") == "customer_answered")
    doubled = []
    for f in frames:
        doubled.append(f)
        if f is ue:
            doubled.append(f)                                             # redelivered verbatim
            later = Frame([(k, v) for k, v in f.fields if k != "Timestamp"] +
                          [("Timestamp", str(float(f.get("Timestamp")) + 1.5))])
            doubled.append(later)                                         # the same state again, later
    feed(rig, doubled)
    assert rig.cons.stats["duplicates"] == 1
    assert q1(pgc, "SELECT count(*) FROM call_events WHERE attempt_id = %s AND state = 'customer_answered'",
              rig.attempt)[0] == 2
    assert final(pgc, rig.attempt)[:3] == ("ended", "customer_hung_up", 16)


def test_forged_user_event_is_not_attributed(rig, pgc):
    feed(rig, [Frame([("Event", "UserEvent"), ("UserEvent", "CollectionsCall"), ("AttemptId", rig.attempt),
                      ("State", "customer_answered"), ("Uniqueid", "999.1"), ("Linkedid", "999.1"),
                      ("Timestamp", "1790231951.5")])])
    assert state(pgc, rig.attempt) == ("authorized", None)
    assert q1(pgc, "SELECT count(*) FROM call_events WHERE attempt_id = %s", rig.attempt)[0] == 0


def test_events_of_another_pbx_epoch_are_never_mixed_in(rig, pgc):
    other = ctx()
    for f in recording("answered_customer_hangs_up", rig.attempt):
        rig.cons.on_event(f, other)                                       # same channel ids, another PBX process
    assert state(pgc, rig.attempt) == ("authorized", None)
    assert q1(pgc, "SELECT count(*) FROM call_events WHERE attempt_id = %s AND pbx_epoch = %s",
              rig.attempt, other.epoch)[0] > 0                            # kept as evidence, not projected


def test_second_customer_leg_is_an_ambiguous_topology(rig, pgc):
    frames = recording("answered_customer_hangs_up", rig.attempt)
    agent = f"cawp-{rig.attempt}"
    dial = next(f for f in frames if f.event == "DialBegin" and f.get("Uniqueid") == agent)
    extra = Frame([(k, ("777.7" if k == "DestUniqueid" else v)) for k, v in dial.fields])
    feed(rig, frames[:frames.index(dial) + 1] + [extra])
    assert state(pgc, rig.attempt) == ("uncertain", "multiple_customer_legs")
    assert reservation(pgc, rig.attempt) == (True, None)


def test_a_third_bridge_member_is_an_ambiguous_topology(rig, pgc):
    frames = recording("answered_customer_hangs_up", rig.attempt)
    out = [Frame([(k, ("3" if k == "BridgeNumChannels" and f.event == "BridgeEnter" else v)) for k, v in f.fields])
           for f in frames]
    feed(rig, out)
    assert state(pgc, rig.attempt) == ("uncertain", "bridge_topology_ambiguous")


def test_truncated_evidence_stays_live(rig, pgc):
    frames = recording("answered_customer_hangs_up", rig.attempt)
    feed(rig, [f for f in frames if f.event != "Hangup" and f.get("State") not in ("customer_ended", "agent_ended")])
    assert state(pgc, rig.attempt) == ("bridged", None)                   # no end without hangup evidence


def test_late_evidence_after_wrapup_is_attached_and_flagged(rig, pgc, cl_client, hdr):
    frames = recording("answered_customer_hangs_up", rig.attempt)
    feed(rig, frames)
    r = cl_client.post(f"/v2/calls/{rig.attempt}/wrapup", headers=hdr(rig.user, key=key()),
                       json={"expected_revision": rev(pgc, rig.attempt), "outcome": "connected", "note": note_body()})
    assert r.status_code == 200, r.get_json()
    before = q1(pgc, f"SELECT {FINAL_COLS}, revision FROM call_attempts WHERE id = %s", rig.attempt)
    late = Frame([("Event", "Newchannel"), ("Uniqueid", f"cawp-{rig.attempt}"), ("Linkedid", f"cawp-{rig.attempt}"),
                  ("Channel", "PJSIP/7201-00000099"), ("ChannelState", "0"), ("Timestamp", "1790240000.0")])
    feed(rig, [late])
    assert q1(pgc, f"SELECT {FINAL_COLS}, revision FROM call_attempts WHERE id = %s", rig.attempt) == before
    flags = qall(pgc, "SELECT kind, reason, event_id IS NOT NULL FROM call_review_flags WHERE attempt_id = %s",
                 rig.attempt)
    assert flags == [("late_evidence", "evidence_after_wrapup", True)]
    assert q1(pgc, "SELECT outcome FROM call_wrapups WHERE attempt_id = %s", rig.attempt)[0] == "connected"
    feed(rig, [Frame([(k, v) for k, v in late.fields if k != "Timestamp"] + [("Timestamp", "1790240001.0")])])
    assert q1(pgc, "SELECT count(*) FROM call_review_flags WHERE attempt_id = %s", rig.attempt)[0] == 1   # one open flag


def test_customer_evidence_after_a_failure_is_a_flagged_conflict(rig, pgc):
    frames = recording("agent_no_answer", rig.attempt)
    feed(rig, frames)
    assert final(pgc, rig.attempt)[:2] == ("failed", "agent_no_answer")
    ans = recording("answered_customer_hangs_up", rig.attempt)
    dial = next(f for f in ans if f.event == "DialBegin" and f.get("Uniqueid") == f"cawp-{rig.attempt}")
    feed(rig, [dial])
    assert final(pgc, rig.attempt)[:2] == ("failed", "agent_no_answer")    # never changed silently
    assert qall(pgc, "SELECT kind, reason FROM call_review_flags WHERE attempt_id = %s", rig.attempt) == \
        [("evidence_conflict", "customer_evidence_after_failure")]


# --------------------------------------------------------------------------- pure derive()
def _ev(i, event, ts, uid=None, state=None, **payload):
    return {"id": i, "event": event, "state": state, "pbx_ts": ts, "uniqueid": uid, "payload": payload}


def test_derive_needs_the_gate_for_a_conversation():
    a = "11111111-2222-4333-8444-555555555555"
    ag, cu = f"cawp-{a}", "9.1"
    evs = [_ev(1, "Newstate", "1.0", ag, ChannelState="6"),
           _ev(2, "DialBegin", "2.0", ag, DestUniqueid=cu),
           _ev(3, "Newstate", "3.0", cu, ChannelState="6"),
           _ev(4, "BridgeEnter", "3.1", ag, BridgeUniqueid="b", BridgeNumChannels="1"),
           _ev(5, "BridgeEnter", "3.2", cu, BridgeUniqueid="b", BridgeNumChannels="2")]
    assert P.derive(a, evs).bridged is None                                 # no allow, no authorized UserEvent
    f = P.derive(a, evs, [{"decision": "allow", "reason": "allowed"}])
    assert f.bridged is not None and f.furthest == "bridged" and f.terminal is None
    evs2 = evs + [_ev(6, "Hangup", "9.0", cu, Cause="16"), _ev(7, "Hangup", "9.5", ag, Cause="16")]
    f = P.derive(a, evs2, [{"decision": "allow", "reason": "allowed"}])
    assert (f.terminal, f.reason, f.hangup_cause, f.duration) == ("ended", "customer_hung_up", 16, 6)
    # the agent hung up (its own HangupRequest), even if the customer's Hangup event is stamped first
    evs3 = evs + [_ev(6, "HangupRequest", "8.9", ag), _ev(7, "SoftHangupRequest", "8.95", cu),
                  _ev(8, "Hangup", "9.0", cu, Cause="16"), _ev(9, "Hangup", "9.1", ag, Cause="16")]
    f = P.derive(a, evs3, [{"decision": "allow", "reason": "allowed"}])
    assert (f.terminal, f.reason) == ("ended", "agent_hung_up")


def test_derive_agi_deny_and_agent_hangup_before_the_gate():
    a = "11111111-2222-4333-8444-555555555555"
    ag = f"cawp-{a}"
    base = [_ev(1, "Newstate", "1.0", ag, ChannelState="6")]
    f = P.derive(a, base + [_ev(2, "UserEvent", "1.5", ag, state="denied"), _ev(3, "Hangup", "2.0", ag, Cause="21")],
                 [{"decision": "deny", "reason": "token_consumed"}])
    assert (f.terminal, f.reason) == ("failed", "authorization_denied")
    f = P.derive(a, base + [_ev(2, "Hangup", "2.0", ag, Cause="16")])
    assert (f.terminal, f.reason) == ("failed", "agent_hung_up")
    assert P.walk("authorized", "bridged") == ["dispatched", "agent_answered", "customer_dialing",
                                               "customer_answered", "bridged"]
    assert P.walk("customer_answered", "failed") is None                    # past the customer answer: an end
