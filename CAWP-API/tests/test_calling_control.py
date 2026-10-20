"""Call control (services/calling/call_control.py, api/calling_control.py, migration 0075):
transfers (blind / attended / cancel), conference add/remove, supervisor listen/whisper/barge,
permission and scope gates, audit rows, the AMI field allowlist, the dialplan generator and the
projection's handover rule. The PBX is a fake AMI session (no socket)."""
from __future__ import annotations

import decimal
import types
import uuid

import pytest

from services.calling import call_control as cc
from services.calling import live_monitor
from services.calling.ami import AmiError, Frame, call_control_fields_ok
from test_calling_c2c_helpers import (CL, FakeSession, add_contact, c2c, cl, fake_ctx, in_app, key, make_case,  # noqa: F401
                                      new_contract, new_supervisor, new_user, number, own_extension, pgc, q1, qall,
                                      run, set_windows, transport)
from test_calling_helpers import insert_attempt


@pytest.fixture(scope="module")
def ctl_app(cl):
    import os

    from flask import Flask
    from sqlalchemy.pool import NullPool

    import api.calling_control
    from core.serialization import ORJSONProvider
    from extensions import db, jwt
    from test_calling_helpers import MAIN_DB, db_url

    app = Flask("cawp_test_calling_control")
    app.config.update(TESTING=True, SQLALCHEMY_DATABASE_URI=db_url(MAIN_DB, "psycopg2"),
                      SQLALCHEMY_ENGINE_OPTIONS={"poolclass": NullPool}, SQLALCHEMY_TRACK_MODIFICATIONS=False,
                      JWT_SECRET_KEY=os.environ["JWT_SECRET_KEY"])
    app.json = ORJSONProvider(app)
    db.init_app(app)
    jwt.init_app(app)
    app.register_blueprint(api.calling_control.bp)
    return app


@pytest.fixture(autouse=True)
def no_leftover_ops(cl):
    """Ops another test left queued would be claimed (as expired) by this test's worker."""
    from test_calling_c2c_helpers import connect_run
    connect_run("UPDATE call_control_ops SET state = 'expired', finished_at = now() WHERE state IN ('queued', 'sending')")
    yield


@pytest.fixture
def client(ctl_app):
    from test_calling_helpers import invalidate_caches
    invalidate_caches()
    return ctl_app.test_client()


def hdr(app, user, k=True):
    from flask_jwt_extended import create_access_token
    with app.app_context():
        h = {"Authorization": f"Bearer {create_access_token(identity=str(user))}"}
    if k:
        h["Idempotency-Key"] = key()
    return h


def chan(uid, channel, *, linked=None, bridge="", state="Up", app="Dial", data="", context="from-internal",
         caller="", connected=""):
    return {"Uniqueid": uid, "Linkedid": linked or uid, "Channel": channel, "ChannelStateDesc": state,
            "BridgeId": bridge, "Application": app, "ApplicationData": data, "Context": context, "Exten": "s",
            "CallerIDNum": caller, "ConnectedLineNum": connected, "Duration": "00:00:40"}


def snapshot(app, frames, *, age=0, ok=True):
    from extensions import db

    def work(s):
        live_monitor.write(s, live_monitor.parse(frames) if ok else None, None if ok else "ami_disconnected")
        s.execute(live_monitor.text("UPDATE pbx_live_snapshot SET captured_at = now() - make_interval(secs => :a)"),
                  {"a": age})
    with app.app_context():
        try:
            work(db.session)
            db.session.commit()
        finally:
            db.session.remove()


class ScriptedSession(FakeSession):
    """Responses per action name (default Success) and a mutable channel list."""

    def __init__(self, channels=(), responses=None, errors=None):
        super().__init__(channels=channels)
        self.responses, self.errors = dict(responses or {}), dict(errors or {})

    def send(self, action, fields, timeout=None):
        assert call_control_fields_ok(action, fields), (action, fields)
        self.sent.append((action, list(fields)))
        if action in self.errors:
            raise AmiError(self.errors[action])
        return Frame([("Response", self.responses.get(action, "Success")), ("ActionID", "x:1")])


def worker(app, transport, session):
    ctx = fake_ctx(transport["epoch"], session=session)
    return cc.ControlWorker(app, lambda: ctx, "test-worker"), ctx


def drain(w):
    n = 0
    while w.run_once():
        n += 1
        assert n < 20
    return n


def control(pgc, cid):
    return q1(pgc, "SELECT state, result_code, mode, kind, target_extension, attempt_id, case_id FROM call_controls "
                   "WHERE id = %s", cid)


def ops(pgc, cid):
    return qall(pgc, "SELECT op, state, result_code FROM call_control_ops WHERE control_id = %s ORDER BY created_at", cid)


def audits(pgc, cid):
    return [r[0] for r in qall(pgc, "SELECT action FROM audit_events WHERE entity_type = 'call_control' "
                                    "AND entity_id = %s ORDER BY id", cid)]


@pytest.fixture
def call(pgc, c2c, ctl_app):
    """c1 (ext1) on a bridged click-to-call with a customer through the trunk."""
    attempt = insert_attempt(pgc, c2c.case, c2c.c1, state="bridged", extension=c2c.ext1, contact_id=c2c.contact,
                             destination="+966500000004", epoch=c2c.transport["epoch"], channel=True)
    agent = f"cawp-{attempt}"
    bridge = str(uuid.uuid4())
    frames = [chan(agent, f"PJSIP/{c2c.ext1}-0000000a", bridge=bridge),
              chan("1760000000.11", "SIP/ITC_SIP_Trunk-0000000b", linked=agent, bridge=bridge, app="AppDial",
                   connected="966500000004")]
    snapshot(ctl_app, frames)
    return types.SimpleNamespace(attempt=attempt, agent=agent, bridge=bridge, frames=frames,
                                 customer="1760000000.11")


# --------------------------------------------------------------------------- pure parts
def test_ami_field_allowlist_refuses_global_setvar_foreign_context_and_digits():
    ok = call_control_fields_ok
    assert ok("Setvar", [("Channel", "PJSIP/101-0000000a"), ("Variable", "CAWP_CTL_ID"), ("Value", "ab" * 16)])
    assert not ok("Setvar", [("Variable", "COLLECTIONS_AGI_URL"), ("Value", "agi://evil")])           # global
    assert not ok("Setvar", [("Channel", "PJSIP/101-0000000a"), ("Variable", "COLLECTIONS_TOKEN"), ("Value", "x")])
    assert not ok("Setvar", [("Channel", "PJSIP/101-0000000a"), ("Variable", "CAWP_CTL_ID"), ("Value", "a;b")])
    assert ok("Redirect", [("Channel", "SIP/x-1"), ("Context", "cawp-callctl-xfer"), ("Exten", "105"), ("Priority", "1")])
    assert not ok("Redirect", [("Channel", "SIP/x-1"), ("Context", "from-internal"), ("Exten", "105"), ("Priority", "1")])
    assert not ok("Redirect", [("Channel", "SIP/x-1"), ("Context", "cawp-callctl-xfer"), ("Exten", "+966500000000"),
                               ("Priority", "1")])
    assert not ok("Redirect", [("Channel", "SIP/x-1"), ("Context", "cawp-callctl-room"), ("Exten", "agent"),
                               ("Priority", "1"), ("ExtraChannel", "PJSIP/1-0"), ("ExtraContext", "default"),
                               ("ExtraExten", "s"), ("ExtraPriority", "1")])
    assert ok("PlayDTMF", [("Channel", "PJSIP/101-0000000a"), ("Digit", "5"), ("Receive", "yes")])
    assert not ok("PlayDTMF", [("Channel", "PJSIP/101-0000000a"), ("Digit", "1"), ("Receive", "yes")])
    assert ok("Originate", [("Channel", "anything")])                     # other actions: unchanged rules


def test_dialplan_render_has_every_context_and_validates_the_policy():
    from services.calling import callctl_dialplan as D
    out = D.render({"post_call_context": "cawp-survey"})
    for ctx in ("[cawp-callctl-xfer]", "[cawp-callctl-done]", "[cawp-callctl-hold]", "[cawp-callctl-room]",
                "[cawp-callctl-spy]", "[cawp-callctl-post]", "[cawp-callctl-tag]"):
        assert ctx in out
    assert "exten => s,1,Goto(cawp-survey,s,1)" in out
    assert "ChanSpy(${CAWP_SPY_TARGET},${CAWP_SPY_OPTS})" in out and "qdEuw" in out and "qdEuB" in out
    assert "PJSIP_HEADER(add,X-CAWP-Control)" in out and "MixMonitor(${CAWP_CTL_REC},b)" in out
    assert "exten => s,1,Hangup()" in D.render({})                       # no post-call context
    assert "MixMonitor" not in D.render({"record": False})
    with pytest.raises(D.CallControlPolicyError):
        D.render({"trunk": "x"})
    with pytest.raises(D.CallControlPolicyError):
        D.render({"post_call_context": "Bad Context"})
    assert "marked = yes" in D.CONFBRIDGE_PROFILES and "end_marked = yes" in D.CONFBRIDGE_PROFILES


def test_plan_refuses_when_the_call_changed():
    cid = str(uuid.uuid4())
    c = {"id": cid, "kind": "transfer_blind", "from_extension": "101", "target_extension": "102",
         "agent_uniqueid": "a", "customer_uniqueid": "c", "target_uniqueid": None, "room": None}
    chans = cc.channel_map([chan("a", "PJSIP/101-0000000a", bridge="b1"), chan("c", "SIP/t-1", bridge="b2")])
    with pytest.raises(cc.Refused) as e:
        cc.plan(c, {"op": "start"}, chans)
    assert e.value.code == "call_changed"
    with pytest.raises(cc.Refused) as e:
        cc.plan(c, {"op": "start"}, cc.channel_map([chan("c", "SIP/t-1", bridge="b2")]))
    assert e.value.code == "agent_gone"


def test_sweep_decisions():
    cid = str(uuid.uuid4())
    room = cc.room_of(cid)
    c = {"id": cid, "kind": "transfer_attended", "state": "active", "agent_uniqueid": "a", "customer_uniqueid": "c",
         "target_uniqueid": "t", "room": room}
    chans = cc.channel_map([chan("a", "PJSIP/101-0000000a", app="ConfBridge", data=f"{room},cawp_room,cawp_member",
                                 bridge="r"),
                            chan("c", "SIP/t-1", context="cawp-callctl-hold", app="Wait")])
    absent = {}
    assert cc.sweep_decisions(c, chans, absent, 10) == []                 # first miss: wait
    assert cc.sweep_decisions(c, chans, absent, 13) == [("op", "cancel")]  # target gone twice: return the customer
    conf = {**c, "kind": "conference", "state": "completed"}
    alone = cc.channel_map([chan("c", "SIP/t-1", app="ConfBridge", data=f"{room},cawp_room,cawp_customer", bridge="r")])
    absent = {}
    cc.sweep_decisions(conf, alone, absent, 10)
    assert ("op", "release") in cc.sweep_decisions(conf, alone, absent, 13)
    spy = {"id": cid, "kind": "spy", "state": "active", "agent_uniqueid": "a", "customer_uniqueid": None,
           "target_uniqueid": "s", "room": None}
    absent = {}
    one = cc.channel_map([chan("a", "PJSIP/101-0000000a", bridge="b")])
    cc.sweep_decisions(spy, one, absent, 10)
    assert cc.sweep_decisions(spy, one, absent, 13) == [("ended", None)]
    absent = {}
    cc.sweep_decisions(spy, {}, absent, 10)
    assert cc.sweep_decisions(spy, {}, absent, 13) == [("ended", None)]


def test_projection_ends_a_transferred_attempt_with_the_agent_leg():
    from services.calling import projection as P
    aid = str(uuid.uuid4())
    agent, cust = f"cawp-{aid}", "1760000000.5"

    def ev(i, event, ts, uid=agent, state=None, **p):
        return {"id": i, "event": event, "state": state, "pbx_ts": decimal.Decimal(ts), "uniqueid": uid,
                "linkedid": agent, "payload": p}
    base = [ev(1, "UserEvent", "100", state="authorized"),
            ev(2, "DialBegin", "101", DestUniqueid=cust),
            ev(3, "Newstate", "105", uid=cust, ChannelState="6"),
            ev(4, "BridgeEnter", "105.1", BridgeUniqueid="b1", BridgeNumChannels="1"),
            ev(5, "BridgeEnter", "105.2", uid=cust, BridgeUniqueid="b1", BridgeNumChannels="2")]
    talking = P.derive(aid, base)
    assert talking.bridged is not None and talking.terminal is None
    transferred = base + [ev(6, "UserEvent", "160", uid=cust, state="transfer_blind"),
                          ev(7, "BridgeLeave", "160.1", BridgeUniqueid="b1"),
                          ev(8, "BridgeLeave", "160.1", uid=cust, BridgeUniqueid="b1"),
                          ev(9, "UserEvent", "160.2", state="transferred"),
                          ev(10, "Hangup", "160.3", Cause="16")]
    f = P.derive(aid, transferred)
    assert (f.terminal, f.reason, f.duration) == ("ended", "transferred", 55)
    later = P.derive(aid, transferred + [ev(11, "Hangup", "400", uid=cust, Cause="16")])
    assert (later.terminal, later.reason, later.duration) == ("ended", "transferred", 55)
    # a conference the customer hangs up: the normal end, duration over the whole conversation
    conf = base + [ev(6, "UserEvent", "160", uid=cust, state="conference"),
                   ev(7, "BridgeLeave", "160.1", BridgeUniqueid="b1"),
                   ev(8, "HangupRequest", "300", uid=cust), ev(9, "Hangup", "300", uid=cust, Cause="16"),
                   ev(10, "Hangup", "300.5", Cause="16")]
    f = P.derive(aid, conf)
    assert (f.terminal, f.reason, f.duration) == ("ended", "customer_hung_up", 195)


# --------------------------------------------------------------------------- transfers
def test_blind_transfer_end_to_end(client, ctl_app, pgc, c2c, call):
    r = client.post("/v2/calling/control/transfers", headers=hdr(ctl_app, c2c.c1),
                    json={"mode": "blind", "target_extension": c2c.ext2, "attempt_id": call.attempt})
    assert r.status_code == 202, r.get_json()
    data = r.get_json()["data"]
    cid = data["id"]
    assert data["kind"] == "transfer_blind" and data["state"] == "queued" and data["target"]["extension"] == c2c.ext2
    st = control(pgc, cid)
    assert str(st[5]) == call.attempt and str(st[6]) == c2c.case
    assert ops(pgc, cid) == [("start", "queued", None)]
    session = ScriptedSession(call.frames)
    w, _ = worker(ctl_app, c2c.transport, session)
    assert drain(w) == 1
    actions = [a for a, _f in session.sent]
    assert actions == ["Setvar", "Setvar", "Setvar", "Redirect"]
    redirect = dict(session.sent[-1][1])
    assert redirect["Channel"] == "SIP/ITC_SIP_Trunk-0000000b" and redirect["Context"] == "cawp-callctl-xfer"
    assert redirect["Exten"] == c2c.ext2 and redirect["ExtraChannel"] == f"PJSIP/{c2c.ext1}-0000000a"
    assert redirect["ExtraContext"] == "cawp-callctl-done"
    assert control(pgc, cid)[:2] == ("completed", "submitted")
    assert ops(pgc, cid) == [("start", "done", "submitted")]
    assert audits(pgc, cid) == ["call.control.transfer_blind.request", "call.control.transfer_blind.start.done"]
    # the customer talks on, then hangs up: the sweep ends the record
    session.channels = []
    w.sweep()
    w.sweep()
    run(pgc, "UPDATE call_controls SET started_at = now() - interval '1 minute' WHERE id = %s", cid)
    w.sweep()
    assert q1(pgc, "SELECT ended_at IS NOT NULL, state FROM call_controls WHERE id = %s", cid) == (True, "completed")


def test_transfer_targets_are_app_users_only(client, ctl_app, pgc, c2c, call):
    h = lambda: hdr(ctl_app, c2c.c1)  # noqa: E731
    for target, code in (("0501234567", "validation_failed"), ("+966501234567", "validation_failed"),
                         ("999", "target_not_app_user"), (c2c.ext1, "validation_failed")):
        r = client.post("/v2/calling/control/transfers", headers=h(), json={"mode": "blind", "target_extension": target})
        assert r.status_code == 422 and r.get_json()["code"] == code, target
    r = client.post("/v2/calling/control/transfers", headers=h(), json={"mode": "sideways", "target_extension": c2c.ext2})
    assert r.status_code == 422
    t = client.get("/v2/calling/control/targets", headers=hdr(ctl_app, c2c.c1, k=False))
    items = t.get_json()["data"]["items"]
    assert any(i["extension"] == c2c.ext2 for i in items) and not any(i["extension"] == c2c.ext1 for i in items)
    assert t.get_json()["data"]["presence_known"] is True


def test_transfer_gates_snapshot_call_and_one_at_a_time(client, ctl_app, pgc, c2c, call):
    body = {"mode": "blind", "target_extension": c2c.ext2}
    snapshot(ctl_app, call.frames, age=60)
    r = client.post("/v2/calling/control/transfers", headers=hdr(ctl_app, c2c.c1), json=body)
    assert r.status_code == 503 and r.get_json()["code"] == "live_calls_unknown"
    snapshot(ctl_app, [])
    r = client.post("/v2/calling/control/transfers", headers=hdr(ctl_app, c2c.c1), json=body)
    assert r.status_code == 409 and r.get_json()["code"] == "no_connected_call"
    snapshot(ctl_app, call.frames)
    c2c.transport["down"]()
    r = client.post("/v2/calling/control/transfers", headers=hdr(ctl_app, c2c.c1), json=body)
    assert r.status_code == 503 and r.get_json()["code"] == "transport_not_connected"


def test_one_open_control_per_agent(client, ctl_app, pgc, c2c, call):
    body = {"mode": "attended", "target_extension": c2c.ext2}
    assert client.post("/v2/calling/control/transfers", headers=hdr(ctl_app, c2c.c1), json=body).status_code == 202
    r = client.post("/v2/calling/control/conferences", headers=hdr(ctl_app, c2c.c1), json={"target_extension": c2c.ext2})
    assert r.status_code == 409 and r.get_json()["code"] == "control_in_progress"


def test_a_three_party_call_is_refused(client, ctl_app, pgc, c2c, call):
    snapshot(ctl_app, call.frames + [chan("x1", "PJSIP/777-0000000c", bridge=call.bridge)])
    r = client.post("/v2/calling/control/transfers", headers=hdr(ctl_app, c2c.c1),
                    json={"mode": "blind", "target_extension": c2c.ext2})
    assert r.status_code == 409 and r.get_json()["code"] == "call_not_two_party"


def _consult_frames(call, cid, c2c, *, target_state="Up", target=True, customer_on_hold=True):
    room = cc.room_of(cid)
    frames = [chan(call.agent, f"PJSIP/{c2c.ext1}-0000000a", app="ConfBridge", data=f"{room},cawp_room,cawp_member",
                   bridge="room-bridge"),
              chan(call.customer, "SIP/ITC_SIP_Trunk-0000000b", linked=call.agent, app="Wait",
                   context="cawp-callctl-hold" if customer_on_hold else "cawp-callctl-room")]
    if target:
        frames.append(chan(cc.leg_id("transfer_attended", cid), f"PJSIP/{c2c.ext2}-0000000d", state=target_state,
                           app="ConfBridge" if target_state == "Up" else "AppDial",
                           data=f"{room},cawp_room,cawp_member" if target_state == "Up" else "",
                           bridge="room-bridge" if target_state == "Up" else ""))
    return frames


def test_attended_transfer_complete(client, ctl_app, pgc, c2c, call):
    r = client.post("/v2/calling/control/transfers", headers=hdr(ctl_app, c2c.c1),
                    json={"mode": "attended", "target_extension": c2c.ext2})
    cid = r.get_json()["data"]["id"]
    session = ScriptedSession(call.frames)
    w, _ = worker(ctl_app, c2c.transport, session)
    drain(w)
    names = [a for a, _f in session.sent]
    assert names == ["Setvar"] * 5 + ["Redirect", "Originate"]
    redirect, orig = dict(session.sent[5][1]), session.sent[6][1]
    assert redirect["Context"] == "cawp-callctl-hold" and redirect["ExtraContext"] == "cawp-callctl-room"
    assert redirect["ExtraExten"] == "agent"
    o = dict(orig)
    assert o["Channel"] == f"PJSIP/{c2c.ext2}" and o["Exten"] == "invitee" and o["ChannelId"] == f"cawpt-{uuid.UUID(cid).hex}"
    assert ("Variable", f"PJSIP_HEADER(add,X-CAWP-Control)={uuid.UUID(cid).hex}") in orig
    assert ("Variable", f"CAWP_CTL_ROOM=cawp-{uuid.UUID(cid).hex}") in orig
    assert control(pgc, cid)[0] == "active"
    view = client.get(f"/v2/calling/control/{cid}", headers=hdr(ctl_app, c2c.c1, k=False)).get_json()["data"]
    assert set(view["actions"]) == {"complete", "cancel"}
    # the target still rings: complete is refused by the service (the call is not touched)
    session.channels = _consult_frames(call, cid, c2c, target_state="Ringing")
    assert client.post(f"/v2/calling/control/{cid}/complete", headers=hdr(ctl_app, c2c.c1), json={}).status_code == 202
    session.sent.clear()
    drain(w)
    assert session.sent == [] and ops(pgc, cid)[-1] == ("complete", "refused", "target_not_answered")
    assert control(pgc, cid)[0] == "active"
    # answered: the customer joins the target, the agent leaves
    session.channels = _consult_frames(call, cid, c2c)
    assert client.post(f"/v2/calling/control/{cid}/complete", headers=hdr(ctl_app, c2c.c1), json={}).status_code == 202
    drain(w)
    (action, fields), = session.sent
    f = dict(fields)
    assert action == "Redirect" and f["Context"] == "cawp-callctl-room" and f["Exten"] == "customer"
    assert f["ExtraContext"] == "cawp-callctl-done"
    assert control(pgc, cid)[:2] == ("completed", "completed")
    assert "call.control.transfer_attended.complete" in audits(pgc, cid)
    # someone else cannot drive it
    r = client.post(f"/v2/calling/control/{cid}/cancel", headers=hdr(ctl_app, c2c.c2), json={})
    assert r.status_code == 404


def test_attended_transfer_cancel_and_auto_return(client, ctl_app, pgc, c2c, call):
    r = client.post("/v2/calling/control/transfers", headers=hdr(ctl_app, c2c.c1),
                    json={"mode": "attended", "target_extension": c2c.ext2})
    cid = r.get_json()["data"]["id"]
    session = ScriptedSession(call.frames)
    w, _ = worker(ctl_app, c2c.transport, session)
    drain(w)
    session.channels = _consult_frames(call, cid, c2c)
    session.sent.clear()
    assert client.post(f"/v2/calling/control/{cid}/cancel", headers=hdr(ctl_app, c2c.c1), json={}).status_code == 202
    drain(w)
    assert [a for a, _f in session.sent] == ["Hangup", "Redirect"]
    assert dict(session.sent[0][1])["Channel"] == f"PJSIP/{c2c.ext2}-0000000d"
    assert dict(session.sent[1][1])["Exten"] == "customer"
    assert control(pgc, cid)[:2] == ("cancelled", "cancelled")

    # a second consultation whose target declines: the sweep returns the customer by itself
    r = client.post("/v2/calling/control/transfers", headers=hdr(ctl_app, c2c.c1),
                    json={"mode": "attended", "target_extension": c2c.ext2})
    assert r.status_code == 202
    cid2 = r.get_json()["data"]["id"]
    session.channels = call.frames
    drain(w)
    session.channels = _consult_frames(call, cid2, c2c, target=False)
    run(pgc, "UPDATE call_controls SET started_at = now() - interval '10 seconds' WHERE id = %s", cid2)
    session.sent.clear()
    w.sweep()
    w.sweep()
    drain(w)
    assert [a for a, _f in session.sent] == ["Redirect"]
    assert control(pgc, cid2)[:2] == ("cancelled", "target_gone")
    assert "call.control.transfer_attended.cancel.auto" in audits(pgc, cid2)


def test_rejected_and_unacknowledged_start(client, ctl_app, pgc, c2c, call):
    body = {"mode": "blind", "target_extension": c2c.ext2}
    cid = client.post("/v2/calling/control/transfers", headers=hdr(ctl_app, c2c.c1), json=body).get_json()["data"]["id"]
    w, _ = worker(ctl_app, c2c.transport, ScriptedSession(call.frames, responses={"Redirect": "Error"}))
    drain(w)
    assert control(pgc, cid)[:2] == ("failed", "start_rejected")
    assert ops(pgc, cid) == [("start", "rejected", "redirect_rejected")]
    cid = client.post("/v2/calling/control/transfers", headers=hdr(ctl_app, c2c.c1), json=body).get_json()["data"]["id"]
    w, _ = worker(ctl_app, c2c.transport, ScriptedSession(call.frames, errors={"Redirect": "ami_action_timeout"}))
    drain(w)
    assert control(pgc, cid)[:2] == ("active", "start_unacknowledged")
    assert ops(pgc, cid) == [("start", "unacknowledged", "ami_action_timeout")]


def test_op_for_another_epoch_expires_unsent(client, ctl_app, pgc, c2c, call):
    cid = client.post("/v2/calling/control/transfers", headers=hdr(ctl_app, c2c.c1),
                      json={"mode": "blind", "target_extension": c2c.ext2}).get_json()["data"]["id"]
    session = ScriptedSession(call.frames)
    ctx = fake_ctx(None, session=session)                  # another PBX process
    w = cc.ControlWorker(ctl_app, lambda: ctx, "w")
    assert w.run_once() is False
    run(pgc, "UPDATE call_control_ops SET expires_at = now() - interval '1 second' WHERE control_id = %s", cid)
    assert w.run_once() is True
    assert session.sent == [] and control(pgc, cid)[:2] == ("failed", "not_sent")
    assert ops(pgc, cid)[0][1] == "expired"


# --------------------------------------------------------------------------- conference
def test_conference_add_and_remove(client, ctl_app, pgc, c2c, call):
    r = client.post("/v2/calling/control/conferences", headers=hdr(ctl_app, c2c.c1),
                    json={"target_extension": c2c.ext2, "remote": "0500000004"})
    assert r.status_code == 202, r.get_json()
    cid = r.get_json()["data"]["id"]
    session = ScriptedSession(call.frames)
    w, _ = worker(ctl_app, c2c.transport, session)
    drain(w)
    names = [a for a, _f in session.sent]
    assert names == ["Setvar"] * 4 + ["Redirect", "Originate"]
    redirect = dict(session.sent[4][1])
    assert (redirect["Context"], redirect["Exten"], redirect["ExtraExten"]) == ("cawp-callctl-room", "customer", "agent")
    assert control(pgc, cid)[0] == "active"
    room = cc.room_of(cid)
    session.channels = [
        chan(call.agent, f"PJSIP/{c2c.ext1}-0000000a", app="ConfBridge", data=f"{room},cawp_room,cawp_member", bridge="rb"),
        chan(call.customer, "SIP/ITC_SIP_Trunk-0000000b", app="ConfBridge", data=f"{room},cawp_room,cawp_customer", bridge="rb"),
        chan(cc.leg_id("conference", cid), f"PJSIP/{c2c.ext2}-0000000e", app="ConfBridge",
             data=f"{room},cawp_room,cawp_member", bridge="rb")]
    view = client.get(f"/v2/calling/control/{cid}", headers=hdr(ctl_app, c2c.c1, k=False)).get_json()["data"]
    assert view["actions"] == ["remove"]
    session.sent.clear()
    assert client.post(f"/v2/calling/control/{cid}/remove", headers=hdr(ctl_app, c2c.c1), json={}).status_code == 202
    drain(w)
    assert session.sent == [("Hangup", [("Channel", f"PJSIP/{c2c.ext2}-0000000e"), ("Cause", "16")])]
    assert control(pgc, cid)[:2] == ("completed", "participant_removed")
    # the agent then leaves: the customer alone in the room is released after two sweeps
    session.channels = [session.channels[1]]
    session.sent.clear()
    w.sweep()
    w.sweep()
    drain(w)
    assert session.sent == [("Redirect", [("Channel", "SIP/ITC_SIP_Trunk-0000000b"), ("Context", "cawp-callctl-post"),
                                          ("Exten", "s"), ("Priority", "1")])]
    assert "call.control.conference.release.auto" in audits(pgc, cid)


# --------------------------------------------------------------------------- listening
@pytest.fixture
def sup_ext(pgc, c2c):
    return own_extension(pgc, c2c.supervisor)


def test_supervisor_listens_whispers_barges_and_stops(client, ctl_app, pgc, c2c, call, sup_ext):
    r = client.post("/v2/calling/control/spy", headers=hdr(ctl_app, c2c.supervisor),
                    json={"call_id": call.agent, "extension": c2c.ext1, "mode": "listen"})
    assert r.status_code == 202, r.get_json()
    cid = r.get_json()["data"]["id"]
    session = ScriptedSession(call.frames)
    w, _ = worker(ctl_app, c2c.transport, session)
    drain(w)
    (action, fields), = session.sent
    f = dict(fields)
    assert action == "Originate" and f["Channel"] == f"PJSIP/{sup_ext}" and f["Context"] == "cawp-callctl-spy"
    assert f["Exten"] == "listen" and f["ChannelId"] == f"cawps-{uuid.UUID(cid).hex}"
    assert ("Variable", f"CAWP_SPY_TARGET=PJSIP/{c2c.ext1}-0000000a") in fields
    assert control(pgc, cid)[:3] == ("active", None, "listen")
    # the softphone may answer its own listening call by itself; the agent cannot see it
    inc = client.get(f"/v2/calling/control/incoming/{uuid.UUID(cid).hex}", headers=hdr(ctl_app, c2c.supervisor, k=False))
    assert inc.status_code == 200 and inc.get_json()["data"]["auto_answer"] is True
    assert inc.get_json()["data"]["role"] == "listen"
    assert client.get(f"/v2/calling/control/incoming/{uuid.UUID(cid).hex}",
                      headers=hdr(ctl_app, c2c.c1, k=False)).status_code == 404
    assert client.get(f"/v2/calling/control/{cid}", headers=hdr(ctl_app, c2c.c1, k=False)).status_code == 404
    spy_leg = chan(cc.leg_id("spy", cid), f"PJSIP/{sup_ext}-0000000f", app="ChanSpy")
    session.channels = call.frames + [spy_leg]
    for mode, digit in (("whisper", "5"), ("barge", "6"), ("listen", "4")):
        session.sent.clear()
        r = client.post(f"/v2/calling/control/{cid}/mode", headers=hdr(ctl_app, c2c.supervisor), json={"mode": mode})
        assert r.status_code == 202
        drain(w)
        assert session.sent == [("PlayDTMF", [("Channel", f"PJSIP/{sup_ext}-0000000f"), ("Digit", digit),
                                              ("Receive", "yes")])]
        assert control(pgc, cid)[2] == mode
    session.sent.clear()
    assert client.post(f"/v2/calling/control/{cid}/stop", headers=hdr(ctl_app, c2c.supervisor), json={}).status_code == 202
    drain(w)
    assert [a for a, _f in session.sent] == ["Hangup"] and control(pgc, cid)[0] == "ended"
    trail = audits(pgc, cid)
    assert trail[0] == "call.control.spy.request" and trail.count("call.control.spy.mode") == 3
    assert "call.control.spy.stop.done" in trail
    detail = q1(pgc, "SELECT after FROM audit_events WHERE entity_id = %s AND action = 'call.control.spy.request'", cid)[0]
    assert detail["agent_extension"] == c2c.ext1 and detail["supervisor_extension"] == sup_ext and detail["mode"] == "listen"


def test_listening_scope_and_permission_gates(client, ctl_app, pgc, c2c, call, sup_ext):
    body = {"call_id": call.agent, "extension": c2c.ext1, "mode": "barge"}
    # a collector (no calls_supervise / calls_manage)
    own_extension(pgc, c2c.c2)
    assert client.post("/v2/calling/control/spy", headers=hdr(ctl_app, c2c.c2), json=body).status_code == 403
    # a supervisor of another team
    other = new_supervisor(pgc, users=[c2c.outsider])
    own_extension(pgc, other)
    r = client.post("/v2/calling/control/spy", headers=hdr(ctl_app, other), json=body)
    assert r.status_code == 403 and r.get_json()["code"] == "not_in_scope"
    # a supervisor without an extension
    bare = new_supervisor(pgc, users=[c2c.c1])
    r = client.post("/v2/calling/control/spy", headers=hdr(ctl_app, bare), json=body)
    assert r.status_code == 409 and r.get_json()["code"] == "no_extension"
    # not the live call
    r = client.post("/v2/calling/control/spy", headers=hdr(ctl_app, c2c.supervisor), json={**body, "call_id": "nope"})
    assert r.status_code == 409 and r.get_json()["code"] == "call_not_live"
    # SuperAdmin: every extension
    own_extension(pgc, CL.super)
    r = client.post("/v2/calling/control/spy", headers=hdr(ctl_app, CL.super), json=body)
    assert r.status_code == 202, r.get_json()
    run(pgc, "UPDATE call_controls SET state = 'ended', ended_at = now() WHERE requested_by = %s AND state = 'queued'",
        CL.super)
    run(pgc, "UPDATE user_extensions SET released_at = now(), release_reason = 'control test' "
             "WHERE user_id = %s AND released_at IS NULL", CL.super)


def test_transfer_target_sees_the_case_link(client, ctl_app, pgc, c2c, call):
    cid = client.post("/v2/calling/control/transfers", headers=hdr(ctl_app, c2c.c1),
                      json={"mode": "blind", "target_extension": c2c.ext2}).get_json()["data"]["id"]
    inc = client.get(f"/v2/calling/control/incoming/{uuid.UUID(cid).hex}", headers=hdr(ctl_app, c2c.c2, k=False))
    data = inc.get_json()["data"]
    assert inc.status_code == 200 and data["role"] == "transfer" and data["auto_answer"] is False
    # c2 is a collector: c1's case is outside their scope -> no case details
    assert data["case"] == {"id": None, "in_scope": False}
    sup = client.get(f"/v2/calling/control/{cid}", headers=hdr(ctl_app, c2c.supervisor, k=False)).get_json()["data"]
    assert sup["case"]["id"] == c2c.case and sup["case"]["in_scope"] is True
    log = client.get("/v2/calling/controls", headers=hdr(ctl_app, c2c.supervisor, k=False)).get_json()["data"]
    assert any(i["id"] == cid and i["from"]["extension"] == c2c.ext1 for i in log["items"])
    assert client.get("/v2/calling/controls", headers=hdr(ctl_app, c2c.c1, k=False)).status_code == 403
    me = client.get("/v2/me/call-controls", headers=hdr(ctl_app, c2c.c1, k=False)).get_json()["data"]
    assert me["capabilities"]["transfer"] is True and me["capabilities"]["listen"] is False
    assert any(i["id"] == cid for i in me["items"])
