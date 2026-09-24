"""Live smoke against the LOCAL test PBX (docker compose profile `pbx-test`). Never the real PBX.

    docker compose --profile pbx-test up -d pbx-test
    CALLING_ENABLED=1 CALLING_DATABASE_URL=postgresql+psycopg2://postgres:postgres@test-db:5432/cawp_test_call0 \\
        docker compose --profile pbx-test up -d calling                    # only for the AGI end-to-end tests
    docker compose --profile test run --rm -e TEST_DB_PREFIX=cawp_test_call0 api-test tests/test_calling_live_pbx.py

Every test skips with a message when the test PBX is not reachable. The AGI end-to-end tests
also skip unless the `calling` service is up, connected and writing into THIS test database.
PBX_TEST_HOST (default pbx-test) refuses 10.10.0.220.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import socket
import ssl
import tempfile
import threading
import time
import urllib.request
import uuid

import pytest

from services.calling import evidence as E
from services.calling.ami import AmiConfig, AmiDecoder, AmiSession, encode_action, read_identity
from services.calling.originate import extension_first_fields
from test_calling_helpers import cl  # noqa: F401  (seeds roles for the AGI end-to-end tests)

HOST = os.environ.get("PBX_TEST_HOST", "pbx-test")
USER = os.environ.get("PBX_TEST_AMI_USER", "cawp-calling")
SECRET = os.environ.get("PBX_TEST_AMI_SECRET", "pbx-test-ami-only")
CALLING_STATUS = os.environ.get("CALLING_STATUS_URL", "http://calling:8791/status")
CONTEXT = "collections-human-outbound"

if HOST.strip() in ("10.10.0.220",):
    raise RuntimeError("the live calling tests never contact the production PBX")


def _reachable(port=5038) -> bool:
    try:
        socket.create_connection((HOST, port), timeout=1.5).close()
        return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(not _reachable(), reason=(
    f"test PBX not reachable at {HOST}:5038 - start it with `docker compose --profile pbx-test up -d pbx-test`"))


class Collector:
    def __init__(self):
        self.frames = []
        self.lock = threading.Lock()

    def __call__(self, frame, _session):
        with self.lock:
            self.frames.append(frame)

    def of(self, uniqueid=None, linkedid=None, event=None):
        with self.lock:
            out = list(self.frames)
        return [f for f in out if (uniqueid is None or f.get("Uniqueid") == uniqueid)
                and (linkedid is None or f.get("Linkedid") == linkedid) and (event is None or f.event == event)]

    def wait(self, pred, timeout=15.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if pred():
                return True
            time.sleep(0.1)
        return False


@pytest.fixture
def ami():
    col = Collector()
    s = AmiSession(AmiConfig(HOST, 5038, USER, SECRET, tls=False, allow_plaintext_hosts=(HOST,)), col,
                   heartbeat=2).connect()
    s.col = col
    yield s
    s.close()


def originate(ami, *, extension, exten="+966500000001", attempt=None, token=None, timeout_s=10):
    attempt = attempt or str(uuid.uuid4())
    fields = [("Channel", f"PJSIP/{extension}"), ("Context", CONTEXT), ("Exten", exten), ("Priority", "1"),
              ("Async", "true"), ("Timeout", str(timeout_s * 1000)), ("ChannelId", f"cawp-{attempt}"),
              ("Variable", f"__COLLECTIONS_ATTEMPT={attempt}")]
    if token:
        fields.append(("Variable", f"COLLECTIONS_TOKEN={token}"))
    ack = ami.send("Originate", fields)
    return attempt, ack


def user_states(ami, attempt):
    return [f.get("State") for f in ami.col.of(linkedid=f"cawp-{attempt}", event="UserEvent")]


def gone(ami, attempt):
    return any(f.get("Uniqueid") == f"cawp-{attempt}" for f in ami.col.of(event="Hangup"))


# --------------------------------------------------------------------------- AMI
def test_ami_login_epoch_heartbeat_and_tls(ami):
    ident = read_identity(ami)
    assert ident.strength == "uuid+startup" and len(ident.epoch_uuid) == 36
    assert ident.ami_version.startswith("Asterisk Call Manager/")
    time.sleep(2.5)
    assert ami.ready and ami.stats["heartbeats"] >= 1
    # AMI over TLS with the test PBX's self-signed certificate as the only trust anchor
    pem = ssl.get_server_certificate((HOST, 5039))
    with tempfile.NamedTemporaryFile("w", suffix=".pem", delete=False) as ca:
        ca.write(pem)
    tls = AmiSession(AmiConfig(HOST, 5039, USER, SECRET, tls=True, ca_file=ca.name, server_name="pbx-test"),
                     lambda f, s: None, heartbeat=0).connect()
    assert read_identity(tls).epoch == ident.epoch                        # same PBX process, same epoch
    tls.close()


def test_ami_account_has_minimal_privileges():
    """Raw frames (our client refuses these actions itself): the PBX must refuse them too."""
    s = socket.create_connection((HOST, 5038), timeout=5)
    dec, frames = AmiDecoder(), []

    def exchange(fields):
        s.sendall(encode_action(fields))
        aid = dict(fields)["ActionID"]
        end = time.monotonic() + 5
        while time.monotonic() < end:
            for fr in [x for x in frames if hasattr(x, "fields") and x.get("ActionID") == aid and x.event is None]:
                return fr
            frames.extend(dec.feed(s.recv(65536)))
        raise AssertionError("no response")

    try:
        frames.extend(dec.feed(s.recv(4096)))
        assert exchange([("Action", "Login"), ("ActionID", "l"), ("Username", USER), ("Secret", SECRET),
                         ("Events", "off")]).response == "Success"
        for fields in ([("Action", "Command"), ("ActionID", "c"), ("Command", "core show version")],
                       [("Action", "Setvar"), ("ActionID", "v"), ("Variable", "COLLECTIONS_AGI_URL"),
                        ("Value", "agi://evil:4573/x")],
                       [("Action", "Reload"), ("ActionID", "r")],
                       [("Action", "Redirect"), ("ActionID", "d"), ("Channel", "PJSIP/x"), ("Exten", "1"),
                        ("Context", "default"), ("Priority", "1")],
                       [("Action", "Originate"), ("ActionID", "o"), ("Channel", "PJSIP/7201"),
                        ("Application", "System"), ("Data", "id")]):
            r = exchange(fields)
            assert r.response == "Error", fields[0]
        assert exchange([("Action", "Getvar"), ("ActionID", "g"),
                         ("Variable", "GLOBAL(COLLECTIONS_HUMAN_PBX_EPOCH)")]).response == "Success"
    finally:
        s.close()


def test_wrong_secret_is_refused():
    from services.calling.ami import AmiError
    with pytest.raises(AmiError) as e:
        AmiSession(AmiConfig(HOST, 5038, USER, "wrong-secret", tls=False, allow_plaintext_hosts=(HOST,)),
                   lambda f, s: None, heartbeat=0).connect()
    assert e.value.code == "ami_authentication_failed"


# --------------------------------------------------------------------------- extension-first, no token
def test_originate_to_simulated_agent_without_token_never_dials_the_customer(ami):
    epoch = read_identity(ami).epoch
    attempt, ack = originate(ami, extension="7201")
    assert ack.response == "Success"                                      # queued: nothing more
    assert ami.col.wait(lambda: gone(ami, attempt))
    agent = f"cawp-{attempt}"
    states = user_states(ami, attempt)
    assert states[:1] == ["agent_answered"] and "denied" in states and "agent_ended" in states
    assert "customer_dialing" not in states and "authorized" not in states
    # no customer leg: the only DialBegin is the Originate ringing the agent
    assert not [f for f in ami.col.of(event="DialBegin") if f.get("Uniqueid") == agent]
    resp = [f for f in ami.col.of(event="OriginateResponse") if f.get("Uniqueid") == agent]
    assert resp and resp[0].get("Response") == "Success"                   # agent answered: still not a call
    ups = [f for f in ami.col.of(uniqueid=agent, event="Newstate") if f.get("ChannelState") == "6"]
    assert ups and ups[0].get("Timestamp")                                   # PBX time on every event
    # every one of those events is attributed to the attempt by the evidence classifier
    rows = [E.classify(f, epoch) for f in ami.col.of(linkedid=agent)]
    assert rows and all(r is not None and r["attempt_id"] == attempt for r in rows)


def test_busy_and_unanswered_agent_phones(ami):
    busy, _ = originate(ami, extension="7202", timeout_s=5)
    silent, _ = originate(ami, extension="7203", timeout_s=4)

    def result(att):
        r = [f for f in ami.col.of(event="OriginateResponse") if f.get("Uniqueid") == f"cawp-{att}"]
        return r[0] if r else None
    assert ami.col.wait(lambda: result(busy) and result(silent), 20)
    assert result(busy).get("Response") == "Failure" and result(silent).get("Response") == "Failure"
    assert user_states(ami, busy) == [] and user_states(ami, silent) == []   # the context never ran


def test_registered_pjsua_client_answers(ami):
    attempt, _ = originate(ami, extension="7101", timeout_s=8)
    agent = f"cawp-{attempt}"

    def resp():
        r = [f for f in ami.col.of(event="OriginateResponse") if f.get("Uniqueid") == agent]
        return r[0] if r else None
    assert ami.col.wait(lambda: resp() is not None, 15)
    if resp().get("Response") != "Success":
        pytest.skip("the bundled pjsua client (7101) is not registered yet; retry after the PBX settles")
    assert ami.col.wait(lambda: gone(ami, attempt))
    assert "agent_answered" in user_states(ami, attempt)                  # a real SIP UA answered over UDP


# --------------------------------------------------------------------------- AGI end-to-end
def _calling_status():
    try:
        with urllib.request.urlopen(CALLING_STATUS, timeout=2) as r:
            return json.loads(r.read())
    except Exception:  # noqa: BLE001
        return None


@pytest.fixture
def calling_on_this_db(databases):
    from conftest import MAIN_DB
    st = _calling_status()
    if not st or not st.get("enabled"):
        pytest.skip("the `calling` service is not running with CALLING_ENABLED=1 (see this module's docstring)")
    if not str(st.get("database", "")).endswith("/" + MAIN_DB):
        pytest.skip(f"the `calling` service writes to {st.get('database')!r}, not this test database {MAIN_DB!r}")
    if not (st.get("ami") or {}).get("state") == "connected":
        pytest.skip("the `calling` service is not connected to the test PBX")
    return st


@pytest.fixture
def conn(cl):
    from test_calling_helpers import connect
    c = connect()
    yield c
    c.close()


def _attempt_with_token(conn, destination, extension, epoch_uuid, lim=30):
    from test_calling_helpers import insert_attempt, make_case, new_contract, new_user, run
    _c, pid = new_contract(conn, "live")
    u = new_user(conn)
    case = make_case(conn, u, pid)
    a = insert_attempt(conn, case, u, state="dispatched", extension=extension, destination=destination)
    token = secrets.token_hex(32)
    run(conn, "INSERT INTO call_dispatch_tokens (attempt_id, token_sha256, channel_id, context, destination, "
              "extension, max_call_seconds, pbx_epoch_uuid, expires_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, "
              "now() + interval '60 seconds')",
        a, hashlib.sha256(token.encode()).hexdigest(), f"cawp-{a}", CONTEXT, destination, extension, lim, epoch_uuid)
    return a, token


def _q(conn, sql, *p):
    with conn.cursor() as cur:
        cur.execute(sql, p)
        return cur.fetchall()


def test_agi_allows_once_customer_answers_bridge_and_replay_is_denied(ami, calling_on_this_db, conn):
    ident = read_identity(ami)
    dest = "+966500000004"                              # answers, then hangs up after 3 s
    a, token = _attempt_with_token(conn, dest, "7201", ident.epoch_uuid)
    originate(ami, extension="7201", exten=dest, attempt=a, token=token)
    assert ami.col.wait(lambda: "agent_ended" in user_states(ami, a), 25), user_states(ami, a)
    states = user_states(ami, a)
    for s in ("agent_answered", "authorized", "customer_dialing", "customer_created", "customer_answered",
              "customer_ended", "agent_ended"):
        assert s in states, (s, states)
    # the customer hung up an answered call: Dial does not return, the agent's handler reports it
    ended = [f for f in ami.col.of(linkedid=f"cawp-{a}", event="UserEvent") if f.get("State") == "agent_ended"][0]
    assert ended.get("DialStatus") == "ANSWER" and ended.get("Cause") == "16"
    rec = [f for f in ami.col.of(linkedid=f"cawp-{a}", event="UserEvent") if f.get("State") == "customer_dialing"][0]
    assert rec.get("Recording", "").endswith(f"/cawp-{a}.wav")
    bridges = [f for f in ami.col.of(linkedid=f"cawp-{a}", event="BridgeEnter")]
    assert len({f.get("Uniqueid") for f in bridges}) == 2 and len({f.get("BridgeUniqueid") for f in bridges}) == 1
    # the database side: one durable allow, consumed token, evidence written by the calling service
    assert _q(conn, "SELECT decision, reason FROM call_agi_decisions WHERE attempt_id = %s", a) == [("allow", "allowed")]
    assert _q(conn, "SELECT consumed_at IS NOT NULL FROM call_dispatch_tokens WHERE attempt_id = %s", a) == [(True,)]
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        legs = _q(conn, "SELECT role, bridged_pbx_ts IS NOT NULL, hungup_pbx_ts IS NOT NULL FROM call_legs "
                        "WHERE attempt_id = %s ORDER BY role", a)
        if len(legs) == 2 and all(l[2] for l in legs):
            break
        time.sleep(0.3)
    assert legs == [("agent", True, True), ("customer", True, True)]
    stored = {r[0] for r in _q(conn, "SELECT state FROM call_events WHERE attempt_id = %s AND event = 'UserEvent'", a)}
    assert {"agent_answered", "customer_answered", "agent_ended"} <= stored
    assert _q(conn, "SELECT state FROM call_attempts WHERE id = %s", a) == [("dispatched",)]   # P0: evidence only

    # replay the same token on the same app-issued channel id: denied, no second customer Dial
    before = sum(1 for s in user_states(ami, a) if s == "customer_dialing")
    originate(ami, extension="7201", exten=dest, attempt=a, token=token)
    assert ami.col.wait(lambda: user_states(ami, a).count("agent_ended") >= 2, 20)
    assert sum(1 for s in user_states(ami, a) if s == "customer_dialing") == before
    assert _q(conn, "SELECT decision, reason FROM call_agi_decisions WHERE attempt_id = %s ORDER BY id", a)[-1] == \
        ("deny", "token_consumed")


def test_agi_busy_customer(ami, calling_on_this_db, conn):
    ident = read_identity(ami)
    a, token = _attempt_with_token(conn, "+966500000002", "7201", ident.epoch_uuid)
    originate(ami, extension="7201", exten="+966500000002", attempt=a, token=token)
    assert ami.col.wait(lambda: "agent_ended" in user_states(ami, a), 25)
    dial_end = [f for f in ami.col.of(linkedid=f"cawp-{a}", event="UserEvent") if f.get("State") == "dial_end"]
    assert dial_end and dial_end[0].get("DialStatus") == "BUSY"
    assert "customer_answered" not in user_states(ami, a)
    assert not [f for f in ami.col.of(linkedid=f"cawp-{a}", event="BridgeEnter")]


def test_agi_denies_a_token_bound_to_another_pbx_epoch(ami, calling_on_this_db, conn):
    a, token = _attempt_with_token(conn, "+966500000001", "7201", str(uuid.uuid4()))
    originate(ami, extension="7201", exten="+966500000001", attempt=a, token=token)
    assert ami.col.wait(lambda: "agent_ended" in user_states(ami, a), 20)
    assert "denied" in user_states(ami, a) and "customer_dialing" not in user_states(ami, a)
    assert _q(conn, "SELECT decision, reason FROM call_agi_decisions WHERE attempt_id = %s", a) == \
        [("deny", "epoch_mismatch")]


def test_extension_first_builder_matches_what_the_pbx_accepts(ami, calling_on_this_db, conn):
    """The P1 builder's exact fields drive a full allowed call on the test PBX."""
    ident = read_identity(ami)
    a, token = _attempt_with_token(conn, "+966500000004", "7201", ident.epoch_uuid)
    fields = extension_first_fields(attempt_id=a, extension="7201", destination="+966500000004", token=token,
                                    context=CONTEXT, agent_ring_seconds=10)
    assert ami.send("Originate", fields).response == "Success"
    assert ami.col.wait(lambda: "agent_ended" in user_states(ami, a), 25)
    assert "customer_answered" in user_states(ami, a)
