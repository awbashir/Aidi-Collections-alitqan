"""The calling service's evidence consumer and restart rules (database, no PBX).

* correlated AMI events -> call_events (sanitized, deduplicated) and call_legs (agent/customer);
* forged or unrelated events are not attributed;
* a new AMI session records the PBX epoch; a changed epoch, a lost session, a stopped or
  restarted service mark in-flight attempts `uncertain` - and NEVER release a reservation;
* database outages buffer evidence in order; overflow records an EvidenceGap.
"""
from __future__ import annotations

import types
import uuid

import pytest

from calling.config import load
from calling.main import CallingService, healthy_status
from services.calling import evidence as E
from services.calling.ami import Frame, PbxIdentity
from test_calling_helpers import (cl, cl_app, insert_attempt, make_case, new_contract, new_user, pgc, q1, qall,  # noqa: F401
                                  reserve, run)

UUID_A = "11111111-2222-4333-8444-555555555555"


def ctx(epoch_uuid=None, startup="2026-09-24T05:00:00", previous=None, sid=None):
    ident = PbxIdentity(epoch_uuid or str(uuid.uuid4()), startup, startup, "Asterisk Call Manager/9.0.0")
    c = types.SimpleNamespace(session=types.SimpleNamespace(session_id=sid or uuid.uuid4().hex[:12]),
                              identity=ident, previous=previous)
    c.epoch = ident.epoch
    c.pbx_restarted = previous is not None and not ident.same_process(previous)
    return c


def f(**fields):
    return Frame(list(fields.items()))


@pytest.fixture
def world(pgc):
    _c, pid = new_contract(pgc, "svc")
    u1, u2, u3 = new_user(pgc), new_user(pgc), new_user(pgc)
    return {"u": [u1, u2, u3], "cases": [make_case(pgc, u, pid) for u in (u1, u2, u3)]}


# --------------------------------------------------------------------------- classification
def test_classify_correlation_rules():
    agent = f"cawp-{UUID_A}"
    ue = E.classify(f(Event="UserEvent", UserEvent="CollectionsCall", AttemptId=UUID_A, State="agent_answered",
                      Uniqueid=agent, Linkedid=agent, Timestamp="1790226558.130531", CallerIDName="Somebody",
                      CallerIDNum="0555"), "ep")
    assert ue["attempt_id"] == UUID_A and ue["state"] == "agent_answered"
    assert str(ue["pbx_ts"]) == "1790226558.130531"
    assert "CallerIDName" not in ue["payload"] and "CallerIDNum" not in ue["payload"]
    # a UserEvent whose own channel is not the app-issued one is kept but NOT attributed
    forged = E.classify(f(Event="UserEvent", UserEvent="CollectionsCall", AttemptId=UUID_A, State="customer_answered",
                          Uniqueid="1790.9", Linkedid="1790.9", Timestamp="1.0"), "ep")
    assert forged["attempt_id"] is None and forged["payload"]["untrusted"] is True
    assert forged["payload"]["claimed_attempt_id"] == UUID_A
    # customer channel: its Linkedid is the agent channel
    cust = E.classify(f(Event="Newchannel", Uniqueid="1790.10", Linkedid=agent, Channel="PJSIP/trunk-01",
                        Timestamp="2.0"), "ep")
    assert cust["attempt_id"] == UUID_A
    # other calls on the PBX and other UserEvents are ignored; lifecycle kept
    assert E.classify(f(Event="Newchannel", Uniqueid="1790.11", Linkedid="1790.11"), "ep") is None
    assert E.classify(f(Event="UserEvent", UserEvent="Other", Linkedid=agent), "ep") is None
    assert E.classify(f(Event="VarSet", Uniqueid=agent, Linkedid=agent), "ep") is None
    boot = E.classify(f(Event="FullyBooted", Uptime="5", Status="Fully Booted"), "ep")
    assert boot["attempt_id"] is None and boot["payload"]["pbx_timestamp_missing"] is True
    # identity is per epoch: the same frame on another PBX process is a different event
    assert E.classify(f(Event="Hangup", Uniqueid=agent, Linkedid=agent, Timestamp="3.0"), "ep1")["dedupe_key"] != \
        E.classify(f(Event="Hangup", Uniqueid=agent, Linkedid=agent, Timestamp="3.0"), "ep2")["dedupe_key"]


# --------------------------------------------------------------------------- consumer
def test_consumer_session_events_and_legs(cl_app, world, pgc):
    a = insert_attempt(pgc, world["cases"][0], world["u"][0], state="dispatched", channel=True)
    c = ctx()
    run(pgc, "UPDATE call_attempts SET pbx_epoch = %s WHERE id = %s", c.epoch, a)
    cons = E.EvidenceConsumer(cl_app, host_label="pbx-test", worker_id="t")
    cons.on_session_start(c)
    sess_row = q1(pgc, "SELECT epoch, epoch_strength, pbx_restarted FROM pbx_sessions WHERE ami_session_id = %s",
                  c.session.session_id)
    assert sess_row == (c.epoch, "uuid+startup", False)
    agent, cust = f"cawp-{a}", "1790226600.7"
    frames = [
        f(Event="Newchannel", Uniqueid=agent, Linkedid=agent, Channel="PJSIP/7201-0001", ChannelState="0",
          Timestamp="100.000001"),
        f(Event="Newstate", Uniqueid=agent, Linkedid=agent, ChannelState="5", Timestamp="100.5"),
        f(Event="Newstate", Uniqueid=agent, Linkedid=agent, ChannelState="6", Timestamp="101.0"),
        f(Event="DialBegin", Uniqueid=agent, Linkedid=agent, DestUniqueid=cust, DestLinkedid=agent,
          DestChannel="PJSIP/cawp-test-trunk-0002", DialString="+966500000004@cawp-test-trunk", Timestamp="102.0"),
        f(Event="Newchannel", Uniqueid=cust, Linkedid=agent, Channel="PJSIP/cawp-test-trunk-0002", Timestamp="102.0"),
        f(Event="Newstate", Uniqueid=cust, Linkedid=agent, ChannelState="6", Timestamp="103.0"),
        f(Event="BridgeEnter", Uniqueid=agent, Linkedid=agent, BridgeUniqueid="b1", BridgeNumChannels="1",
          Timestamp="103.1"),
        f(Event="BridgeEnter", Uniqueid=cust, Linkedid=agent, BridgeUniqueid="b1", BridgeNumChannels="2",
          Timestamp="103.2"),
        f(Event="Hangup", Uniqueid=cust, Linkedid=agent, Cause="16", Timestamp="106.0"),
        f(Event="Hangup", Uniqueid=agent, Linkedid=agent, Cause="16", Timestamp="106.1"),
        f(Event="Newchannel", Uniqueid="unrelated.1", Linkedid="unrelated.1", Timestamp="104.0"),
    ]
    for fr in frames:
        cons.on_event(fr, c)
    cons.on_event(frames[0], c)                                         # redelivery is a duplicate
    assert cons.stats["stored"] == 10 and cons.stats["duplicates"] == 1
    assert q1(pgc, "SELECT count(*) FROM call_events WHERE attempt_id = %s", a)[0] == 10
    legs = {r[0]: r[1:] for r in qall(pgc, "SELECT uniqueid, role, ringing_pbx_ts IS NOT NULL, answered_pbx_ts, "
                                           "bridge_id, hangup_cause FROM call_legs WHERE attempt_id = %s", a)}
    assert legs[agent][0] == "agent" and legs[agent][1] is True and str(legs[agent][2]) == "101.000000"
    assert legs[cust][0] == "customer" and legs[cust][3] == "b1" and legs[cust][4] == 16
    # P1 projection: both legs answered and shared a bridge, but this attempt never passed the gate
    # (no AGI allow, no `authorized` UserEvent), so it is not a conversation: ended, not bridged
    assert q1(pgc, "SELECT state, reason_code, bridged_at FROM call_attempts WHERE id = %s", a) == \
        ("ended", "not_bridged", None)


def test_lost_session_marks_in_flight_uncertain_and_keeps_reservations(cl_app, world, pgc):
    c = ctx()
    a = insert_attempt(pgc, world["cases"][0], world["u"][0], state="bridged", channel=True, epoch=c.epoch)
    done = insert_attempt(pgc, world["cases"][1], world["u"][1], state="ended", channel=True, epoch=c.epoch)
    reserve(pgc, a)
    reserve(pgc, done)
    cons = E.EvidenceConsumer(cl_app, host_label="pbx-test", worker_id="t")
    cons.on_session_start(c)
    cons.on_session_end(c, "ami_connection_lost")
    assert q1(pgc, "SELECT state, reason_code FROM call_attempts WHERE id = %s", a) == ("uncertain", "ami_connection_lost")
    assert q1(pgc, "SELECT state FROM call_attempts WHERE id = %s", done)[0] == "ended"
    assert q1(pgc, "SELECT count(*) FROM call_reservations WHERE attempt_id = ANY(%s::uuid[]) AND released_at IS NULL",
              [a, done])[0] == 2
    assert q1(pgc, "SELECT disconnect_code FROM pbx_sessions WHERE ami_session_id = %s", c.session.session_id)[0] == \
        "ami_connection_lost"
    gap = q1(pgc, "SELECT payload->>'reason' FROM call_events WHERE event = 'EvidenceGap' AND pbx_epoch = %s "
                  "ORDER BY id DESC LIMIT 1", c.epoch)
    assert gap[0] == "ami_connection_lost"


def test_pbx_restart_marks_old_epoch_attempts_uncertain(cl_app, world, pgc):
    old = ctx(startup="2026-09-24T05:00:00")
    a = insert_attempt(pgc, world["cases"][0], world["u"][0], state="agent_answered", channel=True, epoch=old.epoch)
    reserve(pgc, a)
    new = ctx(startup="2026-09-24T06:00:00", previous=old.identity)
    assert new.pbx_restarted
    cons = E.EvidenceConsumer(cl_app, host_label="pbx-test", worker_id="t")
    cons.on_session_start(new)
    assert q1(pgc, "SELECT state, reason_code FROM call_attempts WHERE id = %s", a) == ("uncertain", "pbx_epoch_changed")
    assert q1(pgc, "SELECT pbx_restarted, previous_epoch FROM pbx_sessions WHERE ami_session_id = %s",
              new.session.session_id) == (True, old.epoch)
    assert q1(pgc, "SELECT released_at FROM call_reservations WHERE attempt_id = %s", a)[0] is None


def test_database_outage_buffers_in_order_then_overflow_records_a_gap(cl_app, world, pgc):
    c = ctx()
    a = insert_attempt(pgc, world["cases"][0], world["u"][0], state="dispatched", channel=True, epoch=c.epoch)
    cons = E.EvidenceConsumer(cl_app, host_label="pbx-test", worker_id="t", max_buffer=3)
    cons.on_session_start(c)
    real_run = cons._run
    cons._run = lambda fn: (_ for _ in ()).throw(RuntimeError("db down"))
    agent = f"cawp-{a}"
    for i in range(5):
        cons.on_event(f(Event="Newstate", Uniqueid=agent, Linkedid=agent, ChannelState="6", Timestamp=f"20{i}.0"), c)
    assert len(cons.buffer) == 3 and cons.lost == 2 and cons.stats["db_failures"] >= 5
    cons._run = real_run
    cons.on_heartbeat(c)
    assert len(cons.buffer) == 0 and cons.lost == 0
    stored = [str(r[0]) for r in qall(pgc, "SELECT pbx_ts FROM call_events WHERE attempt_id = %s ORDER BY id", a)]
    assert stored == ["202.000000", "203.000000", "204.000000"]              # the newest kept, in order
    gap = q1(pgc, "SELECT payload FROM call_events WHERE event = 'EvidenceGap' AND pbx_epoch = %s "
                  "AND payload->>'reason' = 'consumer_buffer_overflow'", c.epoch)[0]
    assert gap["lost_events"] == 2
    assert q1(pgc, "SELECT state, reason_code FROM call_attempts WHERE id = %s", a) == ("uncertain", "evidence_lost")


# --------------------------------------------------------------------------- service
def test_restart_never_releases_reservations(cl_app, world, pgc):
    live = insert_attempt(pgc, world["cases"][0], world["u"][0], state="customer_dialing", channel=True, epoch="e")
    prepared = insert_attempt(pgc, world["cases"][1], world["u"][1], state="prepared")
    waiting = insert_attempt(pgc, world["cases"][2], world["u"][2], state="ended", channel=True, epoch="e")
    for a in (live, prepared, waiting):
        reserve(pgc, a)
    before = q1(pgc, "SELECT count(*) FROM call_reservations WHERE released_at IS NULL")[0]
    cfg = load({"CALLING_ENABLED": "0"})
    svc = CallingService(cfg, cl_app)
    for _ in range(2):                                                         # restarted twice
        out = svc.startup_recovery()
        assert out["ok"]
    assert q1(pgc, "SELECT state, reason_code FROM call_attempts WHERE id = %s", live) == \
        ("uncertain", "calling_service_restarted")
    assert q1(pgc, "SELECT state FROM call_attempts WHERE id = %s", prepared)[0] == "prepared"   # never sent
    assert q1(pgc, "SELECT state FROM call_attempts WHERE id = %s", waiting)[0] == "ended"       # awaiting wrap-up
    assert q1(pgc, "SELECT count(*) FROM call_reservations WHERE released_at IS NULL")[0] == before
    assert q1(pgc, "SELECT count(*) FROM call_reservations WHERE attempt_id = ANY(%s::uuid[]) AND released_at IS NULL",
              [live, prepared, waiting])[0] == 3


def test_disabled_service_opens_nothing_and_is_healthy(tmp_path):
    cfg = load({"CALLING_ENABLED": "0", "CALLING_STATUS_PORT": "0", "CALLING_HEALTH_FILE": str(tmp_path / "h")})
    svc = CallingService(cfg, None)
    svc.start()
    assert svc.supervisor is None and svc.agi is None
    svc.touch()
    assert (tmp_path / "h").is_file()
    assert healthy_status(svc.status(), cfg) == (True, "disabled (idle)")
    svc.shutdown()


def test_health_reports_a_long_ami_outage():
    cfg = load({"CALLING_ENABLED": "1", "CALLING_AMI_HOST": "pbx-test", "CALLING_AMI_USERNAME": "u",
                "CALLING_AMI_SECRET": "s", "CALLING_AGI_SECRET": "pbx-test-agi-only", "CALLING_HEALTH_AMI_GRACE": "10"})
    import time
    ok, _ = healthy_status({"enabled": True, "ami_down_since": time.time() - 5, "agi": {"listening": True}}, cfg)
    assert ok
    ok, why = healthy_status({"enabled": True, "ami_down_since": time.time() - 60, "agi": {"listening": True}}, cfg)
    assert not ok and "AMI" in why
    ok, why = healthy_status({"enabled": True, "ami_down_since": None, "agi": {"listening": False}}, cfg)
    assert not ok and "FastAGI" in why


def test_database_down_at_session_start_keeps_the_session_and_catches_up(cl_app, world, pgc):
    c = ctx()
    a = insert_attempt(pgc, world["cases"][0], world["u"][0], state="dispatched", channel=True, epoch=c.epoch)
    cons = E.EvidenceConsumer(cl_app, host_label="pbx-test", worker_id="t")
    real_run = cons._run
    cons._run = lambda fn: (_ for _ in ()).throw(RuntimeError("db down"))
    cons.on_session_start(c)                                  # must not raise (the AMI session stays up)
    agent = f"cawp-{a}"
    cons.on_event(f(Event="Newchannel", Uniqueid=agent, Linkedid=agent, Timestamp="300.0"), c)
    assert c.session.session_id in cons.pending_sessions and len(cons.buffer) == 1
    assert q1(pgc, "SELECT count(*) FROM pbx_sessions WHERE ami_session_id = %s", c.session.session_id)[0] == 0
    cons._run = real_run
    cons.on_heartbeat(c)
    sid = q1(pgc, "SELECT id::text FROM pbx_sessions WHERE ami_session_id = %s", c.session.session_id)[0]
    assert q1(pgc, "SELECT pbx_session_id::text FROM call_events WHERE attempt_id = %s", a)[0] == sid
    assert not cons.pending_sessions and not cons.buffer
