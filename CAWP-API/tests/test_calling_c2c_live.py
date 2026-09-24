"""Phase 5 P1 end to end against the LOCAL test PBX (never the real one), from the attempt API
to the final state: POST /v2/calls -> the `calling` service (dispatcher, AMI, FastAGI, evidence,
projection, reconciliation) -> GET /v2/calls/<id>/live -> wrap-up.

    docker compose --profile pbx-test up -d pbx-test
    CALLING_ENABLED=1 CALLING_RECONCILE_SECONDS=3 \\
    CALLING_DATABASE_URL=postgresql+psycopg2://postgres:postgres@test-db:5432/cawp_test_call1 \\
        docker compose --profile pbx-test up -d calling
    docker compose --profile test run --rm -e TEST_DB_PREFIX=cawp_test_call1 api-test tests/test_calling_c2c_live.py

Skips unless the test PBX answers on pbx-test:5038 and the calling service is enabled, connected
and writing into THIS test database. The test PBX's simulated parties: agents 7201 answers,
7202 busy, 7203 never answers, 7204 rings 3 s then answers; customers by the last digit:
1 answers (echo), 2 busy, 3 never answers, 4 answers then hangs up after 3 s, 6 rings 3 s,
answers, hangs up after 20 s. The restart test uses the test PBX's control port (5099).

RECORD_AMI_FIXTURES=1 writes the AMI frames of the answered/busy/agent-no-answer calls to
tests/fixtures/ami/ (the recorded sequences replayed by tests/test_calling_c2c_projection.py).
"""
from __future__ import annotations

import json
import os
import socket
import threading
import time
import urllib.request
from pathlib import Path

import pytest

from test_calling_c2c_helpers import (add_contact, cl, cl_app, cl_client, hdr, key, make_case, new_contract,  # noqa: F401
                                      new_supervisor, new_user, note_body, number, own_extension, pgc, q1, qall, run,
                                      set_windows)

HOST = os.environ.get("PBX_TEST_HOST", "pbx-test")
AMI_USER = os.environ.get("PBX_TEST_AMI_USER", "cawp-calling")
AMI_SECRET = os.environ.get("PBX_TEST_AMI_SECRET", "pbx-test-ami-only")
CONTROL_KEY = os.environ.get("PBX_TEST_CONTROL_KEY", "pbx-test-control-only")
CALLING_STATUS = os.environ.get("CALLING_STATUS_URL", "http://calling:8791/status")
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "ami"
LIVE_EXTENSIONS = ("7201", "7202", "7203", "7204")

if HOST.strip() in ("10.10.0.220",):
    raise RuntimeError("the live calling tests never contact the production PBX")


def _reachable(port=5038) -> bool:
    try:
        socket.create_connection((HOST, port), timeout=1.5).close()
        return True
    except OSError:
        return False


def _service_status():
    try:
        with urllib.request.urlopen(CALLING_STATUS, timeout=2) as r:
            return json.loads(r.read())
    except Exception:  # noqa: BLE001
        return None


pytestmark = pytest.mark.skipif(not _reachable(), reason=(
    f"test PBX not reachable at {HOST}:5038 - start it with `docker compose --profile pbx-test up -d pbx-test`"))


# --------------------------------------------------------------------------- fixtures
@pytest.fixture(scope="module")
def service(databases):
    from conftest import MAIN_DB
    st = _service_status()
    if not st or not st.get("enabled"):
        pytest.skip("the `calling` service is not running with CALLING_ENABLED=1 (see this module's docstring)")
    if not str(st.get("database", "")).endswith("/" + MAIN_DB):
        pytest.skip(f"the `calling` service writes to {st.get('database')!r}, not this test database {MAIN_DB!r}")
    if not (st.get("dispatcher") or {}).get("running"):
        pytest.skip("the calling service runs without the click-to-call dispatcher")
    return st


def _fresh_transport(pgc, timeout=40):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        r = q1(pgc, "SELECT epoch FROM calling_service_status WHERE stopped_at IS NULL AND ami_connected "
                    "AND dispatcher AND heartbeat_at > clock_timestamp() - interval '10 seconds' "
                    "ORDER BY heartbeat_at DESC LIMIT 1")
        if r:
            return r[0]
        time.sleep(0.5)
    pytest.fail("the calling service did not report a connected AMI session into this database")


@pytest.fixture
def live(service, pgc, cl):
    set_windows(pgc)
    epoch = _fresh_transport(pgc)
    cid, pid = new_contract(pgc, "c2c live")
    sup = new_supervisor(pgc, contracts=[cid], name="Live Supervisor")
    made = {"attempts": []}

    def agent(ext):
        u = new_user(pgc, name=f"Live {ext}")
        own_extension(pgc, u, ext)
        return u

    def cleanup():
        run(pgc, "UPDATE call_reservations SET released_at = now(), release_source = 'reconciled', released_by = %s, "
                 "release_reason = 'live test cleanup' WHERE released_at IS NULL AND extension = ANY(%s)",
            sup, list(LIVE_EXTENSIONS))
    cleanup()
    yield {"portfolio": pid, "supervisor": sup, "agent": agent, "epoch": epoch, "made": made}
    cleanup()


class Ami:
    """The test's own AMI observer on the test PBX (plaintext 5038, compose network only)."""

    def __init__(self):
        from services.calling.ami import AmiConfig, AmiSession
        self.frames, self.lock = [], threading.Lock()
        self.session = AmiSession(AmiConfig(HOST, 5038, AMI_USER, AMI_SECRET, tls=False,
                                            allow_plaintext_hosts=(HOST,)), self._on, heartbeat=5).connect()

    def _on(self, frame, _s):
        with self.lock:
            self.frames.append(frame)

    def of_attempt(self, attempt):
        agent = f"cawp-{attempt}"
        with self.lock:
            return [f for f in self.frames if agent in (f.get("Uniqueid"), f.get("Linkedid"), f.get("DestUniqueid"),
                                                          f.get("DestLinkedid"))]

    def close(self):
        self.session.close()


@pytest.fixture
def ami(service):
    a = Ami()
    yield a
    a.close()


SANITIZE_DROP = {"CallerIDNum", "CallerIDName", "ConnectedLineNum", "ConnectedLineName", "DestCallerIDNum",
                 "DestCallerIDName", "DestConnectedLineNum", "DestConnectedLineName", "AccountCode",
                 "DestAccountCode", "Language", "DestLanguage", "Privilege", "SystemName"}


def record_fixture(name, ami, attempt):
    if os.environ.get("RECORD_AMI_FIXTURES") != "1":
        return
    FIXTURES.mkdir(parents=True, exist_ok=True)
    frames = [[[k, v] for k, v in f.fields if k not in SANITIZE_DROP] for f in ami.of_attempt(attempt)]
    (FIXTURES / f"{name}.json").write_text(json.dumps(
        {"attempt_id": attempt, "note": "recorded from the local test PBX (synthetic parties only)",
         "frames": frames}, indent=1))


# --------------------------------------------------------------------------- helpers
def place(cl_client, hdr, pgc, live, ext, last_digit):
    user = live["agent"](ext)
    case = make_case(pgc, user, live["portfolio"])
    contact = add_contact(pgc, case, number(last_digit), default=True)
    r = cl_client.post("/v2/calls", headers=hdr(user, key=key()), json={"case_id": case, "contact_id": contact})
    body = r.get_json()
    assert r.status_code == 201, body
    assert body["data"]["state"] == "authorized" and body["data"]["dispatch"]["state"] == "queued"
    live["made"]["attempts"].append(body["data"]["id"])
    return user, case, body["data"]["id"]


def panel(cl_client, hdr, user, attempt):
    r = cl_client.get(f"/v2/calls/{attempt}/live", headers=hdr(user))
    assert r.status_code == 200, r.get_json()
    return r.get_json()["data"]


def wait_for(cl_client, hdr, user, attempt, pred, timeout, what):
    end, seen = time.monotonic() + timeout, []
    while time.monotonic() < end:
        d = panel(cl_client, hdr, user, attempt)
        if not seen or seen[-1] != d["state"]:
            seen.append(d["state"])
        if pred(d):
            return d, seen
        time.sleep(0.4)
    d = panel(cl_client, hdr, user, attempt)
    pytest.fail(f"timed out waiting for {what}: states {seen}, now {d['state']} "
                f"({d['reason_code']}), dispatch {d['dispatch']}, events {[e['kind'] for e in d['events']]}")


def wrap(cl_client, hdr, pgc, user, attempt, outcome, **extra):
    revision = q1(pgc, "SELECT revision FROM call_attempts WHERE id = %s", attempt)[0]
    r = cl_client.post(f"/v2/calls/{attempt}/wrapup", headers=hdr(user, key=key()),
                       json={"expected_revision": revision, "outcome": outcome, "note": note_body(f"live {outcome}"),
                             **extra})
    return r.status_code, r.get_json()


def ended(d):
    return d["state"] in ("ended", "failed")


# --------------------------------------------------------------------------- tests
def test_answered_call_bridged_then_ended_then_wrapped_up_connected(cl_client, hdr, pgc, live, ami):
    user, case, a = place(cl_client, hdr, pgc, live, "7201", 6)      # rings 3 s, answers, talks 20 s
    d, seen = wait_for(cl_client, hdr, user, a, lambda d: d["state"] == "bridged", 25, "bridged")
    assert d["conversation"]["evidenced"] is True and d["live"] is True and d["poll_after_ms"] == 1000
    assert d["dispatch"]["state"] == "submitted" and d["dispatch"]["ami_response"] == "Success"
    assert d["actions"]["can_wrapup"] is False and d["actions"]["can_cancel"] is False
    kinds = [e["kind"] for e in d["events"]]
    for k in ("agent_channel_created", "agent_answered", "agi_allow", "dialplan_authorized", "customer_dial_started",
              "customer_answered", "bridge_enter"):
        assert k in kinds, (k, kinds)
    d, seen2 = wait_for(cl_client, hdr, user, a, ended, 40, "the end of the call")
    assert d["state"] == "ended" and d["reason_code"] == "customer_hung_up" and d["hangup_cause"] == 16, \
        [(e["kind"], e["leg"], e["at_pbx"], e["detail"]) for e in d["events"]][-14:]
    assert 15 <= d["duration_seconds"] <= 25 and d["duration_source"] == "pbx" and d["duration_known"] is True
    assert all(d["timeline"][k] for k in ("authorized_at", "dispatched_at", "agent_answered_at", "customer_dialing_at",
                                         "customer_answered_at", "bridged_at", "ended_at"))
    assert d["wrapup_pending"] is True and d["reservation"]["active"] is True
    assert d["actions"]["can_wrapup"] is True and "connected" in d["actions"]["wrapup_outcomes"]
    assert d["poll_after_ms"] is None and d["live"] is False
    legs = qall(pgc, "SELECT role, bridge_id FROM call_legs WHERE attempt_id = %s ORDER BY role", a)
    assert [l[0] for l in legs] == ["agent", "customer"] and legs[0][1] == legs[1][1] is not None
    assert qall(pgc, "SELECT decision, reason FROM call_agi_decisions WHERE attempt_id = %s", a) == [("allow", "allowed")]
    record_fixture("answered_customer_hangs_up", ami, a)
    # ETag: nothing changed -> 304
    r = cl_client.get(f"/v2/calls/{a}/live", headers=hdr(user))
    r2 = cl_client.get(f"/v2/calls/{a}/live", headers={**hdr(user), "If-None-Match": r.headers["ETag"]})
    assert r2.status_code == 304
    st, body = wrap(cl_client, hdr, pgc, user, a, "connected")
    assert st == 200, body
    assert body["data"]["wrapup"]["outcome"] == "connected" and body["data"]["reservation"]["release_source"] == "wrapup"
    assert body["data"]["wrapup"]["note_id"] is not None


def test_busy_customer(cl_client, hdr, pgc, live, ami):
    user, case, a = place(cl_client, hdr, pgc, live, "7201", 2)
    d, seen = wait_for(cl_client, hdr, user, a, ended, 25, "the end of the call")
    assert (d["state"], d["reason_code"], d["hangup_cause"]) == ("ended", "customer_busy", 17)
    assert d["timeline"]["bridged_at"] is None and d["conversation"]["evidenced"] is False
    assert d["duration_seconds"] is None and d["duration_known"] is False            # unknown, never 0
    assert d["actions"]["wrapup_outcomes"] == ["no_answer", "busy", "do_not_call"]
    record_fixture("busy_customer", ami, a)
    st, body = wrap(cl_client, hdr, pgc, user, a, "connected")
    assert (st, body["code"]) == (409, "conversation_not_evidenced")
    st, body = wrap(cl_client, hdr, pgc, user, a, "busy")
    assert st == 200 and body["data"]["reservation"]["release_source"] == "wrapup"


def test_customer_does_not_answer(cl_client, hdr, pgc, live):
    user, case, a = place(cl_client, hdr, pgc, live, "7201", 3)                     # 20 s ring timeout
    d, seen = wait_for(cl_client, hdr, user, a, ended, 45, "the ring timeout")
    assert (d["state"], d["reason_code"]) == ("ended", "customer_no_answer")
    assert d["timeline"]["customer_dialing_at"] and d["timeline"]["customer_answered_at"] is None
    assert "customer_dialing" in seen or d["timeline"]["customer_dialing_at"]
    st, body = wrap(cl_client, hdr, pgc, user, a, "no_answer")
    assert st == 200


def test_customer_hangs_up(cl_client, hdr, pgc, live):
    user, case, a = place(cl_client, hdr, pgc, live, "7201", 4)                     # answers, hangs up after 3 s
    d, seen = wait_for(cl_client, hdr, user, a, ended, 25, "the end of the call")
    assert (d["state"], d["reason_code"], d["hangup_cause"]) == ("ended", "customer_hung_up", 16)
    assert d["timeline"]["bridged_at"] is not None and 1 <= d["duration_seconds"] <= 6


def test_agent_does_not_answer_or_is_busy(cl_client, hdr, pgc, live, ami, monkeypatch):
    monkeypatch.setenv("CALLING_AGENT_RING_SECONDS", "6")
    user, case, a = place(cl_client, hdr, pgc, live, "7203", 1)
    d, seen = wait_for(cl_client, hdr, user, a, ended, 25, "the agent ring timeout")
    assert (d["state"], d["reason_code"]) == ("failed", "agent_no_answer")
    assert d["timeline"]["agent_answered_at"] is None and d["timeline"]["customer_dialing_at"] is None
    assert d["reservation"]["active"] is False and d["reservation"]["release_source"] == "no_customer_contact"
    assert d["wrapup_pending"] is False
    assert q1(pgc, "SELECT count(*) FROM call_agi_decisions WHERE attempt_id = %s", a)[0] == 0
    assert q1(pgc, "SELECT count(*) FROM call_legs WHERE attempt_id = %s AND role = 'customer'", a)[0] == 0
    record_fixture("agent_no_answer", ami, a)
    user2, case2, b = place(cl_client, hdr, pgc, live, "7202", 1)
    d, seen = wait_for(cl_client, hdr, user2, b, ended, 25, "the busy agent")
    assert (d["state"], d["reason_code"]) == ("failed", "agent_busy")
    assert d["reservation"]["release_source"] == "no_customer_contact"


def test_replayed_token_is_refused_and_flagged_as_late_evidence(cl_client, hdr, pgc, live, ami):
    """An AMI observer copies the token while the agent's phone rings, then replays the Originate
    after the call ended and was wrapped up: the AGI refuses it (token_consumed), no second
    customer Dial happens, the outcome is unchanged, and the late evidence is flagged."""
    user, case, a = place(cl_client, hdr, pgc, live, "7204", 4)                     # the agent rings 3 s
    agent = f"cawp-{a}"
    end, token, channel = time.monotonic() + 15, None, None
    while time.monotonic() < end and token is None:
        news = [f for f in ami.of_attempt(a) if f.event == "Newchannel" and f.get("Uniqueid") == agent]
        if news:
            channel = news[0].get("Channel")
            got = ami.session.send("Getvar", [("Channel", channel), ("Variable", "COLLECTIONS_TOKEN")])
            token = (got.get("Value") or "").strip() or None
        time.sleep(0.1)
    assert token and len(token) == 64, "could not observe the token while the agent rang"
    d, seen = wait_for(cl_client, hdr, user, a, ended, 25, "the end of the call")
    assert d["state"] == "ended" and d["timeline"]["agent_ringing_at"] is not None     # 7204 rang first
    st, body = wrap(cl_client, hdr, pgc, user, a, "connected")
    assert st == 200, body
    before = q1(pgc, "SELECT state, revision, reason_code FROM call_attempts WHERE id = %s", a)
    dials_before = len([f for f in ami.of_attempt(a) if f.event == "DialBegin" and f.get("Uniqueid") == agent])
    fields = [("Channel", "PJSIP/7204"), ("Context", "collections-human-outbound"), ("Exten", d["destination"]),
              ("Priority", "1"), ("Async", "true"), ("Timeout", "10000"), ("ChannelId", agent),
              ("Variable", f"__COLLECTIONS_ATTEMPT={a}"), ("Variable", f"COLLECTIONS_TOKEN={token}")]
    assert ami.session.send("Originate", fields).response == "Success"             # the PBX queues it...
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if q1(pgc, "SELECT count(*) FROM call_agi_decisions WHERE attempt_id = %s AND decision = 'deny'", a)[0]:
            break
        time.sleep(0.3)
    assert qall(pgc, "SELECT decision, reason FROM call_agi_decisions WHERE attempt_id = %s ORDER BY id", a) == \
        [("allow", "allowed"), ("deny", "token_consumed")]                         # ...the gate refuses it
    time.sleep(2)
    dials_after = len([f for f in ami.of_attempt(a) if f.event == "DialBegin" and f.get("Uniqueid") == agent])
    assert dials_after == dials_before                                            # no second customer Dial
    assert q1(pgc, "SELECT state, revision, reason_code FROM call_attempts WHERE id = %s", a) == before
    flags = qall(pgc, "SELECT kind, reason FROM call_review_flags WHERE attempt_id = %s AND resolved_at IS NULL", a)
    assert flags == [("late_evidence", "evidence_after_wrapup")]
    d = panel(cl_client, hdr, user, a)
    assert d["wrapup"]["outcome"] == "connected" and [f["kind"] for f in d["flags"]] == ["late_evidence"]


def _control(command):
    with socket.create_connection((HOST, 5099), timeout=10) as s:
        s.sendall(f"{CONTROL_KEY} {command}\n".encode())
        s.settimeout(15)
        reply = s.recv(200).decode().strip()
    assert reply.startswith("ok "), reply
    return reply[3:]


def _new_epoch(pgc, old, timeout=40):
    """Wait until the calling service reports a connected session on another PBX epoch."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        ep = _fresh_transport(pgc)
        if ep != old:
            return ep
        time.sleep(0.5)
    pytest.fail("the calling service did not reconnect to the restarted PBX")


def test_pbx_crash_mid_call_is_uncertain_then_reconciled(cl_client, hdr, pgc, live):
    """Asterisk is killed (SIGKILL) while the call is bridged: no hangup evidence ever arrives.
    The attempt becomes uncertain (observation lost), keeps its reservation, cannot be wrapped
    up; after the PBX came back with a new epoch, reconciliation resolves it from the evidence
    it has (both legs were in one bridge) -> ended; the wrap-up then releases the reservation."""
    user, case, a = place(cl_client, hdr, pgc, live, "7201", 1)                     # answers and stays (echo)
    wait_for(cl_client, hdr, user, a, lambda d: d["state"] == "bridged", 25, "bridged")
    old_epoch = q1(pgc, "SELECT pbx_epoch FROM call_attempts WHERE id = %s", a)[0]
    _control("crash")
    d, seen = wait_for(cl_client, hdr, user, a, lambda d: d["state"] != "bridged", 20, "the loss of observation")
    assert d["state"] == "uncertain" and d["reason_code"] in ("ami_connection_lost", "pbx_epoch_changed")
    assert d["uncertain"]["reason"] == d["reason_code"] and d["poll_after_ms"] == 5000
    assert d["reservation"]["active"] is True                                    # never released by a restart
    st, body = wrap(cl_client, hdr, pgc, user, a, "connected")
    assert (st, body["code"]) == (409, "wrapup_not_allowed")
    new_epoch = _new_epoch(pgc, old_epoch)
    d, seen = wait_for(cl_client, hdr, user, a, lambda d: d["state"] != "uncertain", 60, "reconciliation")
    assert d["state"] == "ended" and d["timeline"]["bridged_at"] is not None
    assert d["duration_seconds"] is None and d["hangup_cause"] is None           # the end was never observed
    rec = d["reconciliations"][0]
    assert (rec["source"], rec["decision"], rec["code"]) == ("service", "ended", "bridged_then_lost")
    assert rec["inventory"]["same_epoch"] is False
    assert d["reservation"]["active"] is True                                    # the wrap-up releases it
    st, body = wrap(cl_client, hdr, pgc, user, a, "connected")
    assert st == 200, body
    # the next click-to-call works on the new epoch
    user2, case2, b = place(cl_client, hdr, pgc, live, "7201", 4)
    d, seen = wait_for(cl_client, hdr, user2, b, ended, 30, "a call after the restart")
    assert d["state"] == "ended" and q1(pgc, "SELECT pbx_epoch FROM call_attempts WHERE id = %s", b)[0] == new_epoch


def test_pbx_crash_while_the_agent_rings_fails_without_customer_contact(cl_client, hdr, pgc, live, monkeypatch):
    monkeypatch.setenv("CALLING_AGENT_RING_SECONDS", "20")
    user, case, a = place(cl_client, hdr, pgc, live, "7203", 1)                     # rings, never answers
    wait_for(cl_client, hdr, user, a, lambda d: d["state"] in ("dispatched", "agent_ringing"), 15, "dispatch")
    time.sleep(1.5)
    old_epoch = q1(pgc, "SELECT pbx_epoch FROM call_attempts WHERE id = %s", a)[0]
    _control("crash")
    d, seen = wait_for(cl_client, hdr, user, a, lambda d: d["state"] == "uncertain", 20, "uncertain")
    assert d["reservation"]["active"] is True
    _new_epoch(pgc, old_epoch)
    d, seen = wait_for(cl_client, hdr, user, a, lambda d: d["state"] != "uncertain", 60, "reconciliation")
    assert (d["state"], d["reconciliations"][0]["code"]) == ("failed", "no_customer_contact")
    assert d["reservation"]["release_source"] == "no_customer_contact"            # proven: no AGI allow, no leg


def test_graceful_pbx_restart_mid_call_ends_by_the_pbx_evidence(cl_client, hdr, pgc, live):
    """`core restart now` hangs up every channel and reports it before closing AMI: the call's
    end is evidenced (both legs' Hangup), so it ends without uncertainty."""
    user, case, a = place(cl_client, hdr, pgc, live, "7201", 1)
    wait_for(cl_client, hdr, user, a, lambda d: d["state"] == "bridged", 25, "bridged")
    old_epoch = q1(pgc, "SELECT pbx_epoch FROM call_attempts WHERE id = %s", a)[0]
    _control("restart")
    d, seen = wait_for(cl_client, hdr, user, a, lambda d: d["state"] != "bridged", 20, "the end")
    assert d["state"] == "ended" and d["timeline"]["bridged_at"] is not None and d["duration_seconds"] is not None
    assert "uncertain" not in seen
    _new_epoch(pgc, old_epoch)
