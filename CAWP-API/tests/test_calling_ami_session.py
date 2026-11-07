"""AMI session and supervisor against a scripted fake AMI server (no PBX, no database).

The fake speaks the AMI wire protocol on 127.0.0.1: greeting, Login, Ping/Pong, Getvar of the
epoch global, CoreStatus, CoreShowChannels lists, and whatever each test scripts (out-of-order
responses, unknown ActionIDs, events, silence, garbage, drops, a PBX "restart" with a new epoch).
"""
from __future__ import annotations

import datetime as dt
import socket
import ssl
import threading
import time
import uuid

import pytest

from services.calling.ami import (AmiConfig, AmiError, AmiSession, AmiSupervisor, PbxIdentity, encode_action,
                                  read_identity)

pytestmark = pytest.mark.no_db

GREETING = b"Asterisk Call Manager/9.0.0\r\n"


def frame_bytes(fields) -> bytes:
    return ("".join(f"{k}: {v}\r\n" for k, v in fields) + "\r\n").encode()


class FakeAmi:
    """A scripted AMI server. `handlers[action](conn, frame_dict)` may answer or return False."""

    def __init__(self, *, secret="fake-secret-1", epoch=None, startup=("2026-09-24", "05:00:00"), tls=None,
                 ping=True, greeting=GREETING):
        self.secret, self.startup, self.ping, self.greeting = secret, startup, ping, greeting
        self.epoch = epoch or str(uuid.uuid4())
        self.handlers = {}
        self.actions = []
        self.conns = []
        self.tls = tls
        self.sock = socket.create_server(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        self.stop = threading.Event()
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        self.sock.settimeout(0.2)
        while not self.stop.is_set():
            try:
                conn, _ = self.sock.accept()
            except OSError:
                continue
            if self.tls is not None:
                try:
                    conn = self.tls.wrap_socket(conn, server_side=True)
                except (ssl.SSLError, OSError):
                    conn.close()
                    continue
            self.conns.append(conn)
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn):
        try:
            conn.sendall(self.greeting)
            buf = b""
            while not self.stop.is_set():
                data = conn.recv(65536)
                if not data:
                    return
                buf += data
                while b"\r\n\r\n" in buf:
                    raw, buf = buf.split(b"\r\n\r\n", 1)
                    fields = [tuple(line.split(": ", 1)) if ": " in line else (line.rstrip(":"), "")
                              for line in raw.decode().split("\r\n")]
                    self._handle(conn, fields)
        except OSError:
            return

    def send(self, conn, fields):
        try:
            conn.sendall(frame_bytes(fields))
        except OSError:
            pass

    def emit(self, fields):
        for c in list(self.conns):
            self.send(c, fields)

    def drop(self):
        for c in list(self.conns):
            try:
                c.shutdown(socket.SHUT_RDWR)
                c.close()
            except OSError:
                pass
        self.conns.clear()

    def close(self):
        self.stop.set()
        self.drop()
        self.sock.close()

    def _handle(self, conn, fields):
        d = dict(fields)
        action, aid = d.get("Action"), d.get("ActionID")
        self.actions.append(fields)
        h = self.handlers.get(action)
        if h is not None and h(conn, d) is not False:
            return
        if action == "Login":
            ok = d.get("Secret") == self.secret
            self.send(conn, [("Response", "Success" if ok else "Error"), ("ActionID", aid),
                             ("Message", "Authentication accepted" if ok else "Authentication failed")])
        elif action == "Ping":
            if self.ping:
                self.send(conn, [("Response", "Success"), ("ActionID", aid), ("Ping", "Pong"),
                                 ("Timestamp", f"{time.time():.6f}")])
        elif action == "Getvar":
            if self.epoch == "missing":
                self.send(conn, [("Response", "Success"), ("ActionID", aid), ("Variable", d.get("Variable")),
                                 ("Value", "")])
            else:
                self.send(conn, [("Response", "Success"), ("ActionID", aid), ("Variable", d.get("Variable")),
                                 ("Value", self.epoch)])
        elif action == "CoreStatus":
            self.send(conn, [("Response", "Success"), ("ActionID", aid), ("CoreStartupDate", self.startup[0]),
                             ("CoreStartupTime", self.startup[1]), ("CoreReloadDate", self.startup[0]),
                             ("CoreReloadTime", self.startup[1]), ("CoreCurrentCalls", "0")])
        elif action == "Logoff":
            self.send(conn, [("Response", "Goodbye"), ("ActionID", aid)])
        else:
            self.send(conn, [("Response", "Error"), ("ActionID", aid), ("Message", "Invalid/unknown command")])


@pytest.fixture
def fake():
    f = FakeAmi()
    yield f
    f.close()


def cfg(fake, **over):
    base = dict(host="127.0.0.1", port=fake.port, username="cawp", secret=fake.secret, tls=False)
    base.update(over)
    return AmiConfig(**base)


def session(fake, on_event=None, **opts):
    opts.setdefault("heartbeat", 0)
    s = AmiSession(cfg(fake), on_event or (lambda f, s: None), **opts)
    return s.connect()


def wait_for(pred, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.02)
    return False


# --------------------------------------------------------------------------- login / actions
def test_login_and_ping(fake):
    s = session(fake)
    assert s.ready and s.banner == "Asterisk Call Manager/9.0.0"
    assert s.send("Ping").get("Ping") == "Pong"
    login = fake.actions[0]
    assert dict(login)["Events"] == "on" and dict(login)["Username"] == "cawp"
    s.close()
    assert s.closed.is_set() and s.close_code == "ami_closed"


def test_bad_secret_is_refused(fake):
    s = AmiSession(cfg(fake, secret="wrong-secret"), lambda f, s: None, heartbeat=0)
    with pytest.raises(AmiError) as e:
        s.connect()
    assert e.value.code == "ami_authentication_failed" and s.closed.is_set()


def test_responses_correlate_by_action_id_even_out_of_order(fake):
    held = {}

    def getvar(conn, d):
        if d["Variable"] == "FIRST":
            held["first"] = (conn, d["ActionID"])              # answered only after the second
            return True
        fake.send(conn, [("Response", "Success"), ("ActionID", "someone-else:9"), ("Value", "stray")])
        fake.send(conn, [("Response", "Success"), ("ActionID", d["ActionID"]), ("Value", "second")])
        c, aid = held["first"]
        fake.send(c, [("Response", "Success"), ("ActionID", aid), ("Value", "first")])
        return True

    fake.handlers["Getvar"] = getvar
    s = session(fake)
    results = {}
    t = threading.Thread(target=lambda: results.setdefault("a", s.send("Getvar", [("Variable", "FIRST")])))
    t.start()
    assert wait_for(lambda: "first" in held)
    results["b"] = s.send("Getvar", [("Variable", "SECOND")])
    t.join(2)
    assert results["a"].get("Value") == "first" and results["b"].get("Value") == "second"
    assert s.stats["unknown_responses"] == 1
    s.close()


def test_disallowed_actions_are_never_sent(fake):
    s = session(fake)
    for action in ("Command", "Setvar", "Reload", "Redirect", "Login"):
        with pytest.raises(AmiError) as e:
            s.send(action, [("Command", "core restart now")])
        assert e.value.code == "ami_action_invalid"
    with pytest.raises(AmiError):
        s.send("Ping", [("ActionID", "forged")])
    assert [dict(a)["Action"] for a in fake.actions] == ["Login"]
    s.close()


def test_action_timeout_closes_the_session_and_nothing_is_replayed(fake):
    fake.handlers["Originate"] = lambda conn, d: True                   # swallowed: no response
    s = session(fake, action_timeout=0.3)
    with pytest.raises(AmiError) as e:
        s.send("Originate", [("Channel", "PJSIP/7201"), ("Context", "collections-human-outbound"),
                             ("Exten", "+966500000001"), ("Priority", "1")])
    assert e.value.code == "ami_action_timeout" and s.close_code == "ami_action_timeout"
    with pytest.raises(AmiError) as e:
        s.send("Ping")
    assert e.value.code == "ami_not_ready"
    assert sum(1 for a in fake.actions if dict(a)["Action"] == "Originate") == 1


# --------------------------------------------------------------------------- events
def test_events_in_order_and_originate_response_is_an_event(fake):
    got = []

    def originate(conn, d):
        fake.send(conn, [("Response", "Success"), ("ActionID", d["ActionID"]),
                         ("Message", "Originate successfully queued")])
        for i in range(20):
            fake.send(conn, [("Event", "Newstate"), ("Uniqueid", f"u{i}"), ("Timestamp", "1.000001")])
        fake.send(conn, [("Event", "OriginateResponse"), ("ActionID", d["ActionID"]), ("Response", "Failure"),
                         ("Reason", "0"), ("Uniqueid", "cawp-x")])
        return True

    fake.handlers["Originate"] = originate
    s = session(fake, on_event=lambda f, sess: got.append(f))
    ack = s.send("Originate", [("Channel", "PJSIP/7201")])
    assert ack.response == "Success"                                      # submitted, nothing more
    assert wait_for(lambda: len(got) >= 21)
    assert [f.get("Uniqueid") for f in got[:20]] == [f"u{i}" for i in range(20)]
    assert got[20].event == "OriginateResponse" and got[20].response == "Failure"
    s.close()


def test_consumer_failure_closes_the_session(fake):
    def boom(f, s):
        raise RuntimeError("database down")
    s = session(fake, on_event=boom)
    fake.emit([("Event", "Newchannel"), ("Uniqueid", "x")])
    assert wait_for(lambda: s.closed.is_set())
    assert s.close_code == "ami_event_sink_failed"


def test_stalled_consumer_closes_the_session(fake):
    s = session(fake, on_event=lambda f, sess: time.sleep(3), heartbeat=0.2, event_timeout=0.5)
    fake.emit([("Event", "Newchannel"), ("Uniqueid", "x")])
    assert wait_for(lambda: s.closed.is_set(), 4)
    assert s.close_code == "ami_event_sink_failed"


def test_event_overflow_closes_the_session(fake):
    gate = threading.Event()
    s = session(fake, on_event=lambda f, sess: gate.wait(5), max_events=5)
    for i in range(20):
        fake.emit([("Event", "Newchannel"), ("Uniqueid", f"x{i}")])
    assert wait_for(lambda: s.closed.is_set())
    assert s.close_code == "ami_event_overflow"
    gate.set()


def test_garbage_from_the_server_closes_the_session(fake):
    s = session(fake)
    for c in fake.conns:
        c.sendall(b"Event: X\r\nEvent: Y\r\n\r\n")
    assert wait_for(lambda: s.closed.is_set())
    assert s.close_code == "ami_protocol_invalid"


def test_wrong_greeting_is_refused():
    f = FakeAmi(greeting=b"SSH-2.0-OpenSSH\r\n")
    try:
        s = AmiSession(cfg(f), lambda fr, se: None, heartbeat=0)
        with pytest.raises(AmiError):
            s.connect()
        assert s.close_code == "ami_protocol_invalid"
    finally:
        f.close()


def test_connection_drop_fails_pending_actions(fake):
    fake.handlers["Getvar"] = lambda conn, d: (fake.drop(), True)[1]
    s = session(fake)
    with pytest.raises(AmiError) as e:
        s.send("Getvar", [("Variable", "X")])
    assert e.value.code == "ami_connection_lost" and s.closed.is_set()


# --------------------------------------------------------------------------- heartbeat
def test_heartbeat_pongs_keep_the_session(fake):
    beats = []
    s = AmiSession(cfg(fake), lambda f, se: None, on_heartbeat=lambda se: beats.append(1), heartbeat=0.2).connect()
    assert wait_for(lambda: len(beats) >= 3)
    assert s.ready
    s.close()


def test_missing_pong_closes_the_session():
    f = FakeAmi(ping=False)
    try:
        s = AmiSession(cfg(f), lambda fr, se: None, heartbeat=0.2, action_timeout=0.4).connect()
        assert wait_for(lambda: s.closed.is_set(), 3)
        assert s.close_code == "ami_heartbeat_failed"
    finally:
        f.close()


def test_wrong_pong_closes_the_session(fake):
    fake.handlers["Ping"] = lambda conn, d: fake.send(conn, [("Response", "Success"), ("ActionID", d["ActionID"]),
                                                            ("Ping", "Nope")]) or True
    s = AmiSession(cfg(fake), lambda fr, se: None, heartbeat=0.2).connect()
    assert wait_for(lambda: s.closed.is_set(), 3)
    assert s.close_code == "ami_heartbeat_failed"


# --------------------------------------------------------------------------- lists / identity
def channel_list(rows, count=None):
    def handler(conn, d):
        aid = d["ActionID"]
        fake_send = handler.fake.send
        fake_send(conn, [("Response", "Success"), ("ActionID", aid), ("EventList", "start"),
                         ("Message", "Channels will follow")])
        for r in rows:
            fake_send(conn, [("Event", "CoreShowChannel"), ("ActionID", aid), ("Uniqueid", r)])
        fake_send(conn, [("Event", "CoreShowChannelsComplete"), ("ActionID", aid), ("EventList", "Complete"),
                         ("ListItems", str(len(rows) if count is None else count))])
        return True
    return handler


def test_channel_inventory(fake):
    h = channel_list(["a", "b"])
    h.fake = fake
    fake.handlers["CoreShowChannels"] = h
    s = session(fake)
    rows = s.list_channels()
    assert [r.get("Uniqueid") for r in rows] == ["a", "b"]
    s.close()


def test_channel_inventory_count_mismatch_is_invalid(fake):
    h = channel_list(["a"], count=2)
    h.fake = fake
    fake.handlers["CoreShowChannels"] = h
    s = session(fake)
    with pytest.raises(AmiError):
        s.list_channels()
    assert s.close_code == "ami_protocol_invalid"


def test_read_identity(fake):
    s = session(fake)
    ident = read_identity(s)
    assert ident.epoch_uuid == fake.epoch and ident.startup == "2026-09-24T05:00:00"
    assert ident.strength == "uuid+startup" and ident.epoch == f"{fake.epoch}@2026-09-24T05:00:00"
    s.close()
    f2 = FakeAmi(epoch="missing")
    try:
        s2 = session(f2)
        ident2 = read_identity(s2)
        assert ident2.epoch_uuid is None and ident2.strength == "startup_only"    # never invented
        s2.close()
    finally:
        f2.close()


def test_identity_refused_while_asterisk_is_still_booting():
    """2026-10-25: before Asterisk is fully booted CoreStatus reports a 1970 startup time; such a
    session must not record the epoch `none@1970-...` (the supervisor reconnects instead)."""
    f = FakeAmi(epoch="missing", startup=("1970-01-01", "03:00:00"))
    try:
        s = session(f)
        with pytest.raises(AmiError) as e:
            read_identity(s)
        assert e.value.code == "pbx_not_booted"
        s.close()
    finally:
        f.close()


def test_identity_comparison():
    a = PbxIdentity("11111111-1111-4111-8111-111111111111", "2026-09-24T05:00:00")
    assert a.same_process(PbxIdentity("11111111-1111-4111-8111-111111111111", "2026-09-24T05:00:00", "later"))
    assert not a.same_process(PbxIdentity("11111111-1111-4111-8111-111111111111", "2026-09-24T06:00:00"))
    assert not a.same_process(PbxIdentity("22222222-2222-4222-8222-222222222222", "2026-09-24T05:00:00"))
    assert not PbxIdentity(None, None).same_process(PbxIdentity(None, None))


# --------------------------------------------------------------------------- TLS
@pytest.fixture(scope="module")
def tls_material(tmp_path_factory):
    crypto = pytest.importorskip("cryptography")  # noqa: F841
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "fake-pbx")])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - dt.timedelta(minutes=5))
            .not_valid_after(now + dt.timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName("fake-pbx")]), critical=False)
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .sign(key, hashes.SHA256()))
    d = tmp_path_factory.mktemp("tls")
    (d / "cert.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    (d / "key.pem").write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                  serialization.NoEncryption()))
    return d


def test_tls_verified_and_hostname_checked(tls_material):
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(tls_material / "cert.pem", tls_material / "key.pem")
    f = FakeAmi(tls=ctx)
    try:
        good = AmiConfig("127.0.0.1", f.port, "cawp", f.secret, tls=True, ca_file=str(tls_material / "cert.pem"),
                         server_name="fake-pbx")
        s = AmiSession(good, lambda fr, se: None, heartbeat=0).connect()
        assert s.send("Ping").get("Ping") == "Pong"
        s.close()
        wrong_name = AmiConfig("127.0.0.1", f.port, "cawp", f.secret, tls=True,
                               ca_file=str(tls_material / "cert.pem"), server_name="other-pbx")
        with pytest.raises(AmiError) as e:
            AmiSession(wrong_name, lambda fr, se: None, heartbeat=0).connect()
        assert e.value.code == "ami_tls_failed"
        untrusted = AmiConfig("127.0.0.1", f.port, "cawp", f.secret, tls=True, server_name="fake-pbx")
        with pytest.raises(AmiError) as e:
            AmiSession(untrusted, lambda fr, se: None, heartbeat=0).connect()
        assert e.value.code == "ami_tls_failed"
    finally:
        f.close()


def test_plaintext_only_to_loopback_or_an_allowed_test_host():
    with pytest.raises(AmiError) as e:
        AmiConfig("10.10.0.220", 5038, "u", "s", tls=False).validate()
    assert e.value.code == "ami_plaintext_refused"
    AmiConfig("pbx-test", 5038, "u", "s", tls=False, allow_plaintext_hosts=("pbx-test",)).validate()
    AmiConfig("127.0.0.1", 5038, "u", "s", tls=False).validate()
    with pytest.raises(AmiError):
        AmiConfig("pbx test", 5038, "u", "s").validate()
    with pytest.raises(AmiError):
        AmiConfig("pbx-test", 5038, "u", "s\r\nAction: Command").validate()


# --------------------------------------------------------------------------- supervisor
def test_supervisor_epoch_reconnect_and_restart():
    f = FakeAmi()
    starts, ends, events = [], [], []
    order = []

    def on_start(ctx):
        order.append("start")
        time.sleep(0.2)                                   # events must wait for this
        starts.append(ctx)

    def on_event(frame, ctx):
        order.append("event")
        events.append((frame.get("Uniqueid"), ctx.epoch))

    sup = AmiSupervisor(AmiConfig("127.0.0.1", f.port, "cawp", f.secret, tls=False), on_event=on_event,
                        on_session_start=on_start, on_session_end=lambda ctx, code: ends.append(code),
                        session_options={"heartbeat": 0.3}, backoff_initial=0.1, backoff_max=0.2)

    def login_then_event(conn, d):
        ok = d.get("Secret") == f.secret
        f.send(conn, [("Response", "Success" if ok else "Error"), ("ActionID", d["ActionID"])])
        f.send(conn, [("Event", "FullyBooted"), ("Uptime", "5"), ("Uniqueid", "boot")])
        return True
    f.handlers["Login"] = login_then_event
    sup.start()
    try:
        assert wait_for(lambda: len(starts) == 1 and events)
        assert order.index("start") < order.index("event")          # held until the epoch is known
        first = starts[0]
        assert first.previous is None and not first.pbx_restarted
        assert events[0] == ("boot", first.epoch)

        f.drop()                                                     # same PBX process, new connection
        assert wait_for(lambda: len(starts) == 2, 5)
        assert ends[0] == "ami_connection_lost"
        assert starts[1].previous.epoch == first.epoch and not starts[1].pbx_restarted

        f.epoch, f.startup = str(uuid.uuid4()), ("2026-09-24", "07:00:00")     # the PBX restarted
        f.drop()
        assert wait_for(lambda: len(starts) == 3, 5)
        assert starts[2].pbx_restarted and starts[2].epoch != first.epoch
        assert sup.status()["state"] == "connected"
    finally:
        sup.stop()
        f.close()
    assert ends[-1] == "ami_closed"


def test_supervisor_retries_when_the_pbx_is_down():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()                                                       # nothing listens there
    sup = AmiSupervisor(AmiConfig("127.0.0.1", port, "cawp", "x", tls=False), on_event=lambda f, c: None,
                        backoff_initial=0.05, backoff_max=0.1)
    sup.start()
    try:
        assert wait_for(lambda: sup.last_error == "ami_connection_failed")
        assert sup.status()["state"] == "reconnecting"
    finally:
        sup.stop()


def test_encode_matches_what_the_fake_receives(fake):
    s = session(fake)
    s.send("Getvar", [("Variable", "GLOBAL(COLLECTIONS_HUMAN_PBX_EPOCH)")])
    last = dict(fake.actions[-1])
    assert last["Action"] == "Getvar" and last["ActionID"].startswith(s.session_id + ":")
    assert encode_action(list(fake.actions[-1])).endswith(b"\r\n\r\n")
    s.close()
