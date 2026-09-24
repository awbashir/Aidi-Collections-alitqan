"""FastAGI authorization hook (services/calling/agi.py) against a scripted fake Asterisk.

The fake plays the PBX side of FastAGI: it sends the agi_* environment, then answers SET/GET
VARIABLE and GET FULL VARIABLE from its own channel variables and globals. The server decides
from the one-shot token table in the test database."""
from __future__ import annotations

import hashlib
import secrets
import socket
import threading
import time
import uuid

import pytest

from services.calling.agi import FastAgiServer, PeerAllowlist
from test_calling_helpers import cl, cl_app, insert_attempt, make_case, new_contract, new_user, pgc, q1, qall, run  # noqa: F401

AGI_KEY = "unit-test-agi-key-0123"
EPOCH = "11111111-2222-4333-8444-555555555555"
CONTEXT = "collections-human-outbound"


@pytest.fixture(scope="module")
def agi_server(cl_app):
    srv = FastAgiServer(cl_app, host="127.0.0.1", port=0, secret=AGI_KEY,
                        allowed_peers=PeerAllowlist(["127.0.0.1/32"]), exchange_timeout=1.5)
    srv.start()
    yield srv
    srv.stop()


@pytest.fixture
def world(pgc):
    _c, pid = new_contract(pgc, "agi")
    u = new_user(pgc)
    return {"user": u, "case": make_case(pgc, u, pid)}


def token_for(pgc, attempt, *, destination="+966500000001", extension="7201", epoch=None, expired=False, lim=600):
    token = secrets.token_hex(32)
    created = "now() - interval '2 minutes'" if expired else "now()"
    expires = "now() - interval '1 minute'" if expired else "now() + interval '30 seconds'"
    run(pgc, f"INSERT INTO call_dispatch_tokens (attempt_id, token_sha256, channel_id, context, destination, extension, "
             f"max_call_seconds, pbx_epoch_uuid, created_at, expires_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, "
             f"{created}, {expires})",
        attempt, hashlib.sha256(token.encode()).hexdigest(), f"cawp-{attempt}", CONTEXT, destination, extension, lim,
        epoch)
    return token


class FakeAsterisk:
    def __init__(self, server, *, attempt, token, env=None, channel_vars=None, globals_=None, hangup_on=None,
                 stall_after_env=False):
        self.addr = server.address[:2]
        self.env = {"agi_network": "yes", "agi_network_script": "collections-authorize",
                    "agi_request": "agi://calling:4573/collections-authorize",
                    "agi_channel": "PJSIP/7201-00000001", "agi_type": "PJSIP", "agi_uniqueid": f"cawp-{attempt}",
                    "agi_context": CONTEXT, "agi_extension": "+966500000001", "agi_priority": "8"}
        self.env.update(env or {})
        self.vars = {"COLLECTIONS_ATTEMPT": attempt, "COLLECTIONS_TOKEN": token, **(channel_vars or {})}
        self.globals = {"COLLECTIONS_AGI_KEY": AGI_KEY, "COLLECTIONS_HUMAN_PBX_EPOCH": EPOCH, **(globals_ or {})}
        self.commands = []
        self.hangup_on, self.stall = hangup_on, stall_after_env

    def run(self):
        s = socket.create_connection(self.addr, timeout=5)
        f = s.makefile("rwb")
        try:
            return self._run(f)
        except OSError:                                   # the server closed on us (denied peer, timeout)
            return self
        finally:
            s.close()

    def _run(self, f):
        for k, v in self.env.items():
            f.write(f"{k}: {v}\n".encode())
        if self.stall:
            f.flush()
            time.sleep(2.5)
            return self
        f.write(b"\n")
        f.flush()
        while True:
            line = f.readline()
            if not line:
                return self
            cmd = line.decode().strip()
            self.commands.append(cmd)
            if self.hangup_on and cmd.startswith(self.hangup_on):
                f.write(b"HANGUP\n")
                f.flush()
                continue
            f.write((self.reply(cmd) + "\n").encode())
            f.flush()

    def reply(self, cmd):
        if cmd.startswith("SET VARIABLE "):
            name, value = cmd[len("SET VARIABLE "):].split(" ", 1)
            self.vars[name] = value.strip('"')
            return "200 result=1"
        if cmd.startswith("GET VARIABLE "):
            v = self.vars.get(cmd[len("GET VARIABLE "):])
            return "200 result=0" if v in (None, "") else f"200 result=1 ({v})"
        if cmd.startswith("GET FULL VARIABLE "):
            expr = cmd[len("GET FULL VARIABLE "):].strip('"')
            name = expr[len("${GLOBAL("):-2]
            v = self.globals.get(name)
            return "200 result=0" if v in (None, "") else f"200 result=1 ({v})"
        return "510 Invalid or unknown command"

    @property
    def allowed(self):
        return self.vars.get("COLLECTIONS_GATE") == "ALLOW"


def decisions(pgc, attempt):
    return qall(pgc, "SELECT decision, reason FROM call_agi_decisions WHERE attempt_id = %s ORDER BY id", attempt)


def test_allow_publishes_gate_last_after_a_durable_decision(agi_server, world, pgc):
    a = insert_attempt(pgc, world["case"], world["user"], state="dispatched", extension="7201")
    tok = token_for(pgc, a, epoch=EPOCH, lim=120)
    fake = FakeAsterisk(agi_server, attempt=a, token=tok).run()
    assert fake.allowed
    assert fake.vars["COLLECTIONS_GATE_DESTINATION"] == "+966500000001"
    assert fake.vars["COLLECTIONS_GATE_EXTENSION"] == "7201" and fake.vars["COLLECTIONS_GATE_LIMIT"] == "120"
    assert int(fake.vars["COLLECTIONS_GATE_EXPIRES"]) > time.time()
    sets = [c for c in fake.commands if c.startswith("SET VARIABLE")]
    assert [c.split()[2] for c in sets[:5]] == ["COLLECTIONS_GATE", "COLLECTIONS_GATE_DESTINATION",
                                                "COLLECTIONS_GATE_EXTENSION", "COLLECTIONS_GATE_LIMIT",
                                                "COLLECTIONS_GATE_EXPIRES"]           # cleared first
    assert all(c.endswith('""') for c in sets[:5])
    assert sets[-1] == 'SET VARIABLE COLLECTIONS_GATE "ALLOW"'                          # ALLOW last
    assert 'GET FULL VARIABLE "${GLOBAL(COLLECTIONS_AGI_KEY)}"' in fake.commands
    assert decisions(pgc, a) == [("allow", "allowed")]
    assert q1(pgc, "SELECT consumed_at IS NOT NULL FROM call_dispatch_tokens WHERE attempt_id = %s", a)[0]


def test_token_is_one_shot(agi_server, world, pgc):
    a = insert_attempt(pgc, world["case"], world["user"], state="dispatched", extension="7201")
    tok = token_for(pgc, a)
    assert FakeAsterisk(agi_server, attempt=a, token=tok).run().allowed
    again = FakeAsterisk(agi_server, attempt=a, token=tok).run()
    assert not again.allowed and "COLLECTIONS_GATE_DESTINATION" not in [c.split()[2] for c in again.commands
                                                                           if c.startswith("SET") and '""' not in c]
    assert decisions(pgc, a) == [("allow", "allowed"), ("deny", "token_consumed")]


def test_concurrent_requests_allow_exactly_once(agi_server, world, pgc):
    a = insert_attempt(pgc, world["case"], world["user"], state="dispatched", extension="7201")
    tok = token_for(pgc, a)
    fakes = [FakeAsterisk(agi_server, attempt=a, token=tok) for _ in range(4)]
    threads = [threading.Thread(target=f.run) for f in fakes]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert sum(f.allowed for f in fakes) == 1
    assert q1(pgc, "SELECT count(*) FROM call_agi_decisions WHERE attempt_id = %s AND decision = 'allow'", a)[0] == 1


@pytest.mark.parametrize("case", [
    ("expired", {}, "token_expired"),
    ("destination", {"env": {"agi_extension": "+966500000009"}}, "token_mismatch"),
    ("uniqueid", {"env": {"agi_uniqueid": "1790226558.2"}}, "bad_channel_id"),
    ("other_attempt_channel", {"env": {"agi_uniqueid": f"cawp-{uuid.uuid4()}"}}, "attempt_mismatch"),
    ("extension", {"env": {"agi_channel": "PJSIP/7999-00000001"}}, "extension_mismatch"),
    ("key", {"globals_": {"COLLECTIONS_AGI_KEY": "wrong-key-wrong-key"}}, "agi_key_mismatch"),
    ("epoch", {"globals_": {"COLLECTIONS_HUMAN_PBX_EPOCH": "99999999-2222-4333-8444-555555555555"}}, "epoch_mismatch"),
    ("no_token", {"channel_vars": {"COLLECTIONS_TOKEN": ""}}, "token_missing"),
    ("unknown_token", {"channel_vars": {"COLLECTIONS_TOKEN": "f" * 64}}, "token_unknown"),
    ("not_fastagi", {"env": {"agi_network": "no"}}, "not_fastagi"),
    ("script", {"env": {"agi_network_script": "something-else"}}, "wrong_script"),
    ("context", {"env": {"agi_context": "from-internal"}}, "wrong_context"),
    ("driver", {"env": {"agi_type": "Local"}}, "wrong_channel_driver"),
    ("hangup", {"hangup_on": "GET VARIABLE COLLECTIONS_TOKEN"}, "channel_hangup"),
], ids=lambda c: c[0] if isinstance(c, tuple) else None)
def test_denials(agi_server, world, pgc, case):
    name, kwargs, reason = case
    a = insert_attempt(pgc, world["case"], world["user"], state="dispatched", extension="7201")
    tok = token_for(pgc, a, epoch=EPOCH, expired=(name == "expired"))
    fake = FakeAsterisk(agi_server, attempt=a, token=tok, **kwargs).run()
    assert not fake.allowed
    rows = qall(pgc, "SELECT decision, reason FROM call_agi_decisions WHERE channel_id = %s OR attempt_id = %s "
                     "ORDER BY id DESC LIMIT 1", fake.env["agi_uniqueid"], a)
    assert rows == [("deny", reason)], name
    if name != "unknown_token":
        assert q1(pgc, "SELECT consumed_at FROM call_dispatch_tokens WHERE attempt_id = %s", a)[0] is None


def test_terminal_attempt_is_not_dispatchable(agi_server, world, pgc):
    a = insert_attempt(pgc, world["case"], world["user"], state="failed", extension="7201")
    tok = token_for(pgc, a)
    assert not FakeAsterisk(agi_server, attempt=a, token=tok).run().allowed
    assert decisions(pgc, a) == [("deny", "attempt_not_dispatchable")]


def test_stalled_client_times_out(agi_server, world, pgc):
    a = insert_attempt(pgc, world["case"], world["user"], state="dispatched", extension="7201")
    tok = token_for(pgc, a)
    fake = FakeAsterisk(agi_server, attempt=a, token=tok, stall_after_env=True).run()
    assert not fake.allowed
    time.sleep(0.3)
    # the environment never completed, so the decision is recorded without a channel
    assert q1(pgc, "SELECT reason, channel_id FROM call_agi_decisions ORDER BY id DESC LIMIT 1") == ("agi_timeout", None)


def test_peer_allowlist(cl_app, world, pgc):
    srv = FastAgiServer(cl_app, host="127.0.0.1", port=0, secret=AGI_KEY,
                        allowed_peers=PeerAllowlist(["10.99.0.0/16", "no-such-host.invalid"]), exchange_timeout=1)
    srv.start()
    try:
        a = insert_attempt(pgc, world["case"], world["user"], state="dispatched", extension="7201")
        tok = token_for(pgc, a)
        fake = FakeAsterisk(srv, attempt=a, token=tok).run()
        assert not fake.allowed and fake.commands == []
        time.sleep(0.2)
        assert q1(pgc, "SELECT reason, peer FROM call_agi_decisions ORDER BY id DESC LIMIT 1") == \
            ("peer_not_allowed", "127.0.0.1")
    finally:
        srv.stop()
    with pytest.raises(ValueError):
        PeerAllowlist(["bad host!"])
    with pytest.raises(ValueError):
        FastAgiServer(cl_app, host="127.0.0.1", port=0, secret="", allowed_peers=PeerAllowlist([]))
