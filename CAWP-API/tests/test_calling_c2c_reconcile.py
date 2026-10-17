"""Phase 5 P1: uncertainty and reconciliation (services/calling/reconcile.py, cdr_lookup.py).

Observation loss (AMI disconnect, PBX epoch change, missing events) makes an attempt
`uncertain` with a reason and never releases its reservation. Reconciliation resolves it from
the channel inventory (same epoch), the stored evidence and a CDR lookup, or keeps it for
human review; a supervisor can resolve it with a reason.
"""
from __future__ import annotations

import types
import uuid

import pytest

from services.calling import cdr_lookup
from services.calling import reconcile as RC
from services.calling.ami import PbxIdentity
from test_calling_c2c_helpers import (FakeSession, c2c, cl, cl_app, cl_client, fake_ctx, hdr, in_app, key,  # noqa: F401
                                      pgc, place, post, q1, qall, reservation, rev, run, state, transport)
from test_calling_c2c_projection import feed, make_rig, recording


class FakeCdr:
    name = "fake"

    def __init__(self, **summary):
        self.summary = summary
        self.calls = []

    def lookup(self, attempt_id, *, around=None, session=None):
        assert around is not None and session is not None           # always a date window + the resolver
        self.calls.append(attempt_id)
        if not self.summary:
            return cdr_lookup.unavailable("fake", "none")
        return {"source": "fake", "available": True, "found": 1, **self.summary}


def ctx_for(rig, session=None):
    c = types.SimpleNamespace(session=session or FakeSession(), identity=rig.ctx.identity, epoch=rig.ctx.epoch,
                              previous=None, pbx_restarted=False)
    return c


def until(frames, pred):
    out = []
    for f in frames:
        out.append(f)
        if pred(f):
            return out
    return out


def reconcile(cl_app, rig, ctx, inventory, cdr=None):
    return in_app(cl_app, lambda s: RC.reconcile_attempt(s, rig.attempt, ctx, inventory, cdr))


@pytest.fixture(autouse=True)
def no_settle(monkeypatch):
    monkeypatch.setattr(RC, "SETTLE_SECONDS", 0)


def bridged_then_lost(cl_app, pgc):
    rig = make_rig(cl_app, pgc)
    frames = recording("answered_customer_hangs_up", rig.attempt)
    feed(rig, until(frames, lambda f: f.event == "BridgeEnter" and f.get("Uniqueid") == f"cawp-{rig.attempt}"))
    assert state(pgc, rig.attempt) == ("bridged", None)
    rig.cons.on_session_end(rig.ctx, "ami_connection_lost")
    return rig


# --------------------------------------------------------------------------- uncertainty
def test_disconnect_makes_it_uncertain_and_keeps_the_reservation(cl_app, pgc):
    rig = bridged_then_lost(cl_app, pgc)
    assert state(pgc, rig.attempt) == ("uncertain", "ami_connection_lost")
    assert reservation(pgc, rig.attempt) == (True, None)
    # later evidence on the same epoch is recorded as facts, never moves an uncertain attempt
    frames = recording("answered_customer_hangs_up", rig.attempt)
    feed(rig, frames[-3:])
    assert state(pgc, rig.attempt) == ("uncertain", "ami_connection_lost")


def test_epoch_change_makes_it_uncertain(cl_app, pgc):
    rig = make_rig(cl_app, pgc)
    frames = recording("answered_customer_hangs_up", rig.attempt)
    feed(rig, until(frames, lambda f: f.event == "BridgeEnter" and f.get("Uniqueid") == f"cawp-{rig.attempt}"))
    ident = PbxIdentity(str(uuid.uuid4()), "2026-09-24T07:00:00")
    new = types.SimpleNamespace(session=types.SimpleNamespace(session_id=uuid.uuid4().hex[:12]), identity=ident,
                                previous=rig.ctx.identity, epoch=ident.epoch, pbx_restarted=True)
    rig.cons.on_session_start(new)                                        # the PBX restarted, AMI reconnected
    assert state(pgc, rig.attempt) == ("uncertain", "pbx_epoch_changed")
    assert reservation(pgc, rig.attempt) == (True, None)
    out = reconcile(cl_app, rig, fake_ctx(new.epoch), inventory=None)     # another epoch: its channels are gone
    assert out == {"decision": "ended", "code": "bridged_then_lost", "released": False}
    assert q1(pgc, "SELECT state, reason_code, bridged_at IS NOT NULL, duration_seconds, hangup_cause "
                   "FROM call_attempts WHERE id = %s", rig.attempt) == ("ended", "pbx_epoch_changed", True, None, None)
    assert reservation(pgc, rig.attempt) == (True, None)                  # the wrap-up releases it


# --------------------------------------------------------------------------- reconciliation
def test_live_channels_keep_it_uncertain_and_block_a_supervisor_resolution(cl_app, pgc, cl_client, hdr):
    rig = bridged_then_lost(cl_app, pgc)
    ctx = ctx_for(rig)
    inv = [{"Uniqueid": f"cawp-{rig.attempt}", "Linkedid": f"cawp-{rig.attempt}", "ChannelState": "6",
            "ChannelStateDesc": "Up", "BridgeId": "b"}, {"Uniqueid": "unrelated.1", "Linkedid": "unrelated.1"}]
    assert reconcile(cl_app, rig, ctx, inv) == {"decision": "kept_uncertain", "code": "channels_present"}
    assert reconcile(cl_app, rig, ctx, inv)["code"] == "channels_present"
    rows = qall(pgc, "SELECT decision, code, inventory->>'related_channels' FROM call_reconciliations "
                     "WHERE attempt_id = %s", rig.attempt)
    assert rows == [("kept_uncertain", "channels_present", "1")]         # logged once, not every pass
    from test_calling_helpers import new_supervisor
    sup = new_supervisor(pgc, users=[rig.user])
    st, body = post(cl_client, hdr(sup, key=key()), f"/v2/calls/{rig.attempt}/resolve",
                    {"expected_revision": rev(pgc, rig.attempt), "resolution": "ended", "reason": "looked done"})
    assert (st, body["code"]) == (409, "call_still_live")
    # the channels are gone: evidence of the bridge resolves it
    assert reconcile(cl_app, rig, ctx, [])["code"] == "bridged_then_lost"
    assert state(pgc, rig.attempt)[0] == "ended"


def test_no_customer_contact_resolves_to_failed_and_releases(cl_app, pgc):
    rig = make_rig(cl_app, pgc)
    frames = recording("agent_no_answer", rig.attempt)
    feed(rig, frames[:3])                                                 # the agent's phone was ringing
    rig.cons.on_session_end(rig.ctx, "ami_connection_lost")
    assert state(pgc, rig.attempt) == ("uncertain", "ami_connection_lost")
    # an unexpired one-shot token could still let an unacknowledged Originate reach the gate: wait
    run(pgc, "INSERT INTO call_dispatch_tokens (attempt_id, token_sha256, channel_id, context, destination, extension, "
             "max_call_seconds, expires_at) VALUES (%s, md5(random()::text) || md5(random()::text), %s, "
             "'collections-human-outbound', '+966500000004', '7201', 600, now() + interval '30 seconds')",
        rig.attempt, f"cawp-{rig.attempt}")
    assert reconcile(cl_app, rig, ctx_for(rig), []) is None
    run(pgc, "SET session_replication_role = replica")
    try:
        run(pgc, "UPDATE call_dispatch_tokens SET created_at = now() - interval '2 minutes', "
                 "expires_at = now() - interval '1 minute' WHERE attempt_id = %s", rig.attempt)
    finally:
        run(pgc, "SET session_replication_role = origin")
    out = reconcile(cl_app, rig, ctx_for(rig), [])
    assert out == {"decision": "failed", "code": "no_customer_contact", "released": True}
    assert reservation(pgc, rig.attempt) == (False, "no_customer_contact")


def _answered_without_bridge(cl_app, pgc):
    rig = make_rig(cl_app, pgc)
    frames = recording("answered_customer_hangs_up", rig.attempt)
    feed(rig, until(frames, lambda f: f.event == "UserEvent" and f.get("State") == "customer_answered"))
    rig.cons.on_session_end(rig.ctx, "ami_connection_lost")
    return rig


def test_cdr_answered_without_bridge_evidence_needs_a_human(cl_app, pgc, cl_client, hdr):
    rig = _answered_without_bridge(cl_app, pgc)
    cdr = FakeCdr(customer_dialed=True, customer_answered=True, disposition="ANSWERED", billsec=40)
    out = reconcile(cl_app, rig, ctx_for(rig), [], cdr)
    assert out == {"decision": "kept_uncertain", "code": "conversation_unproven"} and cdr.calls == [rig.attempt]
    assert qall(pgc, "SELECT kind, reason FROM call_review_flags WHERE attempt_id = %s AND resolved_at IS NULL",
                rig.attempt) == [("reconciliation_unresolved", "conversation_unproven")]
    from test_calling_helpers import new_supervisor
    sup = new_supervisor(pgc, users=[rig.user])
    base = {"expected_revision": rev(pgc, rig.attempt), "resolution": "ended"}
    st, body = post(cl_client, hdr(sup, key=key()), f"/v2/calls/{rig.attempt}/resolve", base)
    assert (st, body["code"]) == (422, "validation_failed")               # a reason is required
    st, body = post(cl_client, hdr(rig.user, key=key()), f"/v2/calls/{rig.attempt}/resolve",
                    {**base, "reason": "x"})
    assert st == 403                                                      # collectors cannot resolve
    st, body = post(cl_client, hdr(sup, key=key()), f"/v2/calls/{rig.attempt}/resolve",
                    {**base, "reason": "listened to the recording: they talked", "release_reservation": True})
    assert st == 200, body
    d = body["data"]
    assert d["state"] == "ended" and d["reservation"]["release_source"] == "reconciled"
    assert d["reconciliations"][0]["source"] == "supervisor" and d["flags"] == []
    assert d["timeline"]["bridged_at"] is None                            # still no bridge evidence: no
    assert d["actions"]["wrapup_outcomes"] == ["no_answer", "busy", "wrong_number", "do_not_call"]   # no conversation outcome
    assert q1(pgc, "SELECT action, reason FROM audit_events WHERE entity_id = %s AND action = 'call.resolve'",
              rig.attempt) == ("call.resolve", "listened to the recording: they talked")
    st, body = post(cl_client, hdr(sup, key=key()), f"/v2/calls/{rig.attempt}/resolve",
                    {**base, "expected_revision": rev(pgc, rig.attempt), "reason": "again"})
    assert (st, body["code"]) == (409, "not_uncertain")


def test_cdr_not_answered_ends_it(cl_app, pgc):
    rig = make_rig(cl_app, pgc)
    frames = recording("busy_customer", rig.attempt)
    feed(rig, until(frames, lambda f: f.event == "DialBegin" and f.get("Uniqueid") == f"cawp-{rig.attempt}"))
    rig.cons.on_session_end(rig.ctx, "ami_connection_lost")
    out = reconcile(cl_app, rig, ctx_for(rig), [], FakeCdr(customer_dialed=True, customer_answered=False,
                                                            disposition="BUSY"))
    assert out == {"decision": "ended", "code": "cdr_not_answered", "released": False}
    assert reservation(pgc, rig.attempt) == (True, None)


def test_without_cdr_it_stays_uncertain_then_is_flagged(cl_app, pgc):
    rig = make_rig(cl_app, pgc)
    frames = recording("busy_customer", rig.attempt)
    feed(rig, until(frames, lambda f: f.event == "DialBegin" and f.get("Uniqueid") == f"cawp-{rig.attempt}"))
    rig.cons.on_session_end(rig.ctx, "ami_connection_lost")
    assert reconcile(cl_app, rig, ctx_for(rig), [], FakeCdr()) == \
        {"decision": "kept_uncertain", "code": "evidence_incomplete"}
    assert q1(pgc, "SELECT count(*) FROM call_review_flags WHERE attempt_id = %s", rig.attempt)[0] == 0
    run(pgc, "SET session_replication_role = replica")
    try:
        run(pgc, "UPDATE call_attempts SET updated_at = now() - interval '10 minutes' WHERE id = %s", rig.attempt)
    finally:
        run(pgc, "SET session_replication_role = origin")
    reconcile(cl_app, rig, ctx_for(rig), [], FakeCdr())
    assert qall(pgc, "SELECT kind, reason FROM call_review_flags WHERE attempt_id = %s", rig.attempt) == \
        [("reconciliation_unresolved", "evidence_incomplete")]
    assert state(pgc, rig.attempt)[0] == "uncertain" and reservation(pgc, rig.attempt) == (True, None)


def test_complete_evidence_after_reconnect_ends_it(cl_app, pgc):
    """Lost while the customer rang busy; the rest arrives after the reconnect (same epoch)."""
    rig = make_rig(cl_app, pgc)
    frames = recording("busy_customer", rig.attempt)
    cut = frames.index(next(f for f in frames if f.event == "DialBegin" and f.get("Uniqueid") == f"cawp-{rig.attempt}"))
    feed(rig, frames[:cut + 1])
    rig.cons.on_session_end(rig.ctx, "ami_connection_lost")
    feed(rig, frames[cut + 1:])
    assert state(pgc, rig.attempt)[0] == "uncertain"
    assert reconcile(cl_app, rig, ctx_for(rig), [])["code"] == "evidence_complete"
    assert q1(pgc, "SELECT state, hangup_cause FROM call_attempts WHERE id = %s", rig.attempt) == ("ended", 17)


# --------------------------------------------------------------------------- missing events
def _dispatched(cl_app, cl_client, hdr, c2c, pgc):
    from services.calling import dispatch as D
    st, body = place(cl_client, hdr, c2c.c1, c2c.case, c2c.contact)
    assert st == 201
    a = body["data"]["id"]
    ctx = fake_ctx(c2c.transport["epoch"])
    d = D.Dispatcher(cl_app, lambda: ctx, "test-worker")
    for _ in range(100):
        if not d.run_once():
            break
    assert state(pgc, a) == ("dispatched", None)
    return a, ctx


def test_missing_evidence_after_dispatch_is_uncertain(cl_app, cl_client, hdr, c2c, pgc):
    a, ctx = _dispatched(cl_app, cl_client, hdr, c2c, pgc)
    assert a not in in_app(cl_app, lambda s: RC.mark_missing(s, ctx.epoch))       # too early
    run(pgc, "SET session_replication_role = replica")
    try:
        run(pgc, "UPDATE call_attempts SET dispatched_at = now() - interval '2 minutes' WHERE id = %s", a)
    finally:
        run(pgc, "SET session_replication_role = origin")
    assert a in in_app(cl_app, lambda s: RC.mark_missing(s, ctx.epoch))
    assert state(pgc, a) == ("uncertain", "evidence_missing") and reservation(pgc, a) == (True, None)


def test_a_send_that_never_finished_is_uncertain(cl_app, cl_client, hdr, c2c, pgc):
    st, body = place(cl_client, hdr, c2c.c1, c2c.case, c2c.contact)
    a = body["data"]["id"]
    from services.calling import dispatch as D
    got = in_app(cl_app, lambda s: D.claim(s, fake_ctx(c2c.transport["epoch"]), "crashed-worker"))
    assert isinstance(got, D.Claim)                                       # claimed, then the process died
    run(pgc, "SET session_replication_role = replica")
    try:
        run(pgc, "UPDATE call_dispatch_queue SET claimed_at = now() - interval '2 minutes' WHERE attempt_id = %s", a)
    finally:
        run(pgc, "SET session_replication_role = origin")
    in_app(cl_app, lambda s: RC.mark_missing(s, None))
    assert state(pgc, a) == ("uncertain", "dispatch_outcome_unknown")
    assert q1(pgc, "SELECT state FROM call_dispatch_queue WHERE attempt_id = %s", a)[0] == "unacknowledged"
    assert reservation(pgc, a) == (True, None)


def test_reconciler_pass_with_a_channel_inventory(cl_app, pgc, monkeypatch):
    rig = bridged_then_lost(cl_app, pgc)
    session = FakeSession(channels=[{"Uniqueid": f"cawp-{rig.attempt}", "Linkedid": f"cawp-{rig.attempt}"}])
    ctx = ctx_for(rig, session)
    r = RC.Reconciler(cl_app, lambda: ctx, None, interval=1)

    def decision():                           # other tests' uncertain attempts may share the pages
        for _ in range(100):
            out = r.run_once()
            if rig.attempt in out["decisions"]:
                return out["decisions"][rig.attempt]
        raise AssertionError("the attempt was never reconciled")
    assert decision()["code"] == "channels_present"
    session.channels = []
    assert decision()["code"] == "bridged_then_lost" and r.stats["resolved"] >= 1


# --------------------------------------------------------------------------- CDR
def _cdr_line(uniqueid, userfield, *, start="2026-09-24 05:56:37", dstchannel="PJSIP/cawp-test-trunk-00000015",
              lastapp="Dial", disposition="ANSWERED", billsec=3):
    return ('"","","+966500000004","collections-human-outbound",""""" <>","PJSIP/7201-00000013",'
            f'"{dstchannel}","{lastapp}","x","{start}","{start}","{start}",{billsec},{billsec},"{disposition}",'
            f'"DOCUMENTATION","{uniqueid}","{userfield}"')


def test_csv_cdr_lookup(tmp_path, cl_app, pgc):
    """userfield first; a truncated uniqueid (Issabel's varchar(32)) only when it names exactly one
    attempt; always inside the date window around the attempt."""
    import datetime as dt
    from test_calling_c2c_helpers import in_app
    rig = make_rig(cl_app, pgc)
    a = rig.attempt
    created = q1(pgc, "SELECT created_at FROM call_attempts WHERE id = %s", a)[0]
    local = (created + dt.timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S")
    long_ago = (created - dt.timedelta(days=3)).strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        _cdr_line(f"cawp-{a}"[:32], f"cawp:{a}", start=local),                     # Issabel-style: userfield
        _cdr_line(f"cawp-{a}", "", start=local, dstchannel="", lastapp="Return", billsec=0),   # full uniqueid (CSV)
        _cdr_line(f"cawp-{a}"[:32], "", start=local, disposition="BUSY"),          # truncated only
        _cdr_line(f"cawp-{a}", f"cawp:{a}", start=long_ago),                       # outside the window
        _cdr_line("1790229397.30", "", start=local),                               # somebody else's call
    ]
    f = tmp_path / "Master.csv"
    f.write_text("\n".join(lines) + "\n")
    src = cdr_lookup.CsvCdrSource(str(f))
    got = in_app(cl_app, lambda s: src.lookup(a, around=created, session=s))
    assert got["found"] == 3 and got["matched_via"] == ["uniqueid", "uniqueid_prefix", "userfield"]
    assert (got["customer_dialed"], got["customer_answered"], got["disposition"], got["billsec"]) == \
        (True, True, "ANSWERED", 3)
    # a truncated uniqueid that could name two attempts is never used
    twin = a[:-4] + ("0000" if not a.endswith("0000") else "1111")
    run(pgc, "INSERT INTO call_attempts (id, mode, state, case_id, collector_user_id, created_by) "
             "VALUES (%s, 'click_to_call', 'prepared', %s, %s, %s)", twin, rig.case, rig.user, rig.user)
    got = in_app(cl_app, lambda s: src.lookup(a, around=created, session=s))
    assert got["matched_via"] == ["uniqueid", "userfield"] and got["found"] == 2
    assert cdr_lookup.match(a, uniqueid=f"cawp-{a}"[:32]) is None                  # no resolver, no prefix match
    assert src.lookup(str(uuid.uuid4()), around=created)["found"] == 0
    assert cdr_lookup.CsvCdrSource(str(tmp_path / "missing.csv")).lookup(a)["available"] is False
    assert src.lookup("not-a-uuid")["available"] is False
    with pytest.raises(ValueError):
        cdr_lookup.MysqlCdrSource(host="10.10.0.220", database="x", user="u", password="p")
    assert cdr_lookup.from_env({"CALLING_CDR_SOURCE": "none"}) is None
    assert isinstance(cdr_lookup.from_env({"CALLING_CDR_SOURCE": "csv", "CALLING_CDR_CSV": str(f)}),
                      cdr_lookup.CsvCdrSource)


def test_decide_is_pure():
    from services.calling.projection import Facts
    f = Facts(agent="x")
    assert RC.decide({"bridged_at": None}, f, None) == ("failed", "no_customer_contact")
    f.agi_allow = True
    assert RC.decide({"bridged_at": None}, f, None) == ("kept_uncertain", "evidence_incomplete")
    assert RC.decide({"bridged_at": None}, f, {"available": True, "found": 1, "customer_dialed": True,
                                                "customer_answered": False}) == ("ended", "cdr_not_answered")
    f.ambiguity = "multiple_customer_legs"
    assert RC.decide({"bridged_at": "x"}, f, None) == ("kept_uncertain", "ambiguous_topology")
