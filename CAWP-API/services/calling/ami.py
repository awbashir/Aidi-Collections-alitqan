"""Asterisk Manager Interface (AMI) client: wire codec, persistent session, supervisor.

Ported from the rebuild's `ami-protocol.js` / `ami-session.js` designs into Python threads
(concepts only). Nothing here decides anything about a call: an AMI `Response: Success` to
an Originate means *submitted*, never connected, and heartbeat success proves only that the
session is responsive.

Codec (AmiDecoder / encode_action)
    * the stream starts with the greeting `Asterisk Call Manager/X.Y[.Z]`;
    * a frame is `Key: Value` lines ended by CRLF, terminated by an empty line; keys are
      case-insensitive and may repeat (ordered pairs are kept, e.g. `Variable`);
    * routing keys (ActionID, Event, Response, Action) must not repeat: a frame that
      carries two of them is rejected instead of silently picking one;
    * limits: 64 KiB per frame, 8 KiB per line, 256 fields; framing violations poison the
      decoder (the session closes). Values that are not UTF-8 are decoded with
      replacement characters (PBX caller names can be Latin-1); routing values are ASCII.
    * outbound actions need exactly one Action and one ActionID and no Event/Response key,
      and no control characters anywhere, so a value can never inject a header or action.

Session (AmiSession)
    * TLS 1.2+ with certificate and host-name verification (CA file or the system store), or
      plaintext only when explicitly allowed (loopback, or a named private test host);
    * waits for the greeting, then `Login` with `Events: on`; failure closes the session;
    * every action gets a unique `<session>:<n>` ActionID and waits for its own response;
      unknown or duplicate responses cannot settle another action; `OriginateResponse` is
      an *event* (it carries `Response:` too) and goes to the event consumer;
    * events are delivered in order by one consumer thread from a bounded queue (overflow,
      a consumer failure or a consumer stalled longer than `event_timeout` closes the session);
    * `Ping` every `heartbeat` seconds; a missing Pong closes the session;
    * an action timeout closes the session (a lost acknowledgement leaves the PBX state
      unknown), and nothing is ever replayed on this or another connection;
    * list actions (`CoreShowChannels`) are collected under their ActionID until the
      matching `...Complete` event with the exact item count.

Supervisor (AmiSupervisor)
    * keeps one session up with exponential backoff; after every login it resolves the
      **PBX identity** (the epoch): the global `COLLECTIONS_HUMAN_PBX_EPOCH` UUID written by
      the PBX pre-start hook (Getvar) plus the process start time (CoreStatus
      CoreStartupDate/Time). Either changing means a new PBX process; evidence from a
      previous process is never mixed with the new one (`on_session_start` receives both the
      new and the previous identity);
    * events are held until the identity is known, so every event is attributed to exactly
      one epoch.
"""
from __future__ import annotations

import collections
import dataclasses
import ipaddress
import itertools
import logging
import queue
import re
import socket
import ssl
import threading
import time
import uuid

log = logging.getLogger("cawp.calling.ami")

MAX_FRAME = 65536
MAX_LINE = 8192
MAX_FIELDS = 256
MAX_CHUNK = 1048576
KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,79}$")
# Asterisk <= 11 writes manager.conf `channelvars` as "ChanVariable(<channel>): NAME=value" (the
# Alitqan Issabel: channelvars=DIALERID,DIALERVAR); 12+ writes "ChanVariable: NAME=value". Only
# this exact shape is accepted, and the key is normalised to "ChanVariable".
LEGACY_CHANVAR_RE = re.compile(r"^ChanVariable\([A-Za-z0-9_./+@;=-]{1,150}\)$")
GREETING_RE = re.compile(r"^Asterisk Call Manager/\d+\.\d+(?:\.\d+)?$")
CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
ROUTING_KEYS = ("ActionID", "Event", "Response", "Action")
ALLOWED_ACTIONS = frozenset({"Ping", "Originate", "Hangup", "CoreStatus", "CoreShowChannels",
                             "Getvar", "Status", "BridgeInfo",
                             # call control (2026-10-20, services/calling/call_control.py): only with
                             # the fields checked by call_control_fields_ok() below
                             "Setvar", "Redirect", "PlayDTMF"})
# Call control needs the `call` write class, which would also allow a GLOBAL Setvar (e.g. the AGI
# URL) or a Redirect into any context. This client refuses both, whatever the caller passes:
# Setvar only on a named channel and only CAWP_CTL_* / CAWP_SPY_* variables with plain values;
# Redirect only into the cawp-callctl-* contexts; PlayDTMF only ChanSpy's mode digits 4/5/6.
CALL_CONTROL_VARIABLE_RE = re.compile(r"^CAWP_(?:CTL|SPY)_[A-Z]{1,20}$")
CALL_CONTROL_VALUE_RE = re.compile(r"^[A-Za-z0-9_./+-]{0,150}$")
CALL_CONTROL_CONTEXT_RE = re.compile(r"^cawp-callctl-[a-z]{1,20}$")
CALL_CONTROL_CHANNEL_RE = re.compile(r"^[A-Za-z0-9_./+@:-]{1,150}$")
CALL_CONTROL_EXTEN_RE = re.compile(r"^(?:[0-9]{2,6}|[a-z]{1,20})$")


def call_control_fields_ok(action: str, fields) -> bool:
    """Field allowlist of the call-control actions (other actions: True)."""
    if action not in ("Setvar", "Redirect", "PlayDTMF"):
        return True
    d: dict[str, list[str]] = {}
    for k, v in fields:
        d.setdefault(k.lower(), []).append(v)
    if any(len(v) > 1 for v in d.values()):
        return False
    one = {k: v[0] for k, v in d.items()}
    chan = one.get("channel", "")
    if not CALL_CONTROL_CHANNEL_RE.match(chan):
        return False
    if action == "Setvar":
        return (set(one) == {"channel", "variable", "value"} and bool(CALL_CONTROL_VARIABLE_RE.match(one["variable"]))
                and bool(CALL_CONTROL_VALUE_RE.match(one["value"])))
    if action == "PlayDTMF":
        return set(one) == {"channel", "digit", "receive"} and one["digit"] in ("4", "5", "6") and one["receive"] == "yes"
    allowed = {"channel", "context", "exten", "priority", "extrachannel", "extracontext", "extraexten", "extrapriority"}
    if not {"channel", "context", "exten", "priority"} <= set(one) or set(one) - allowed:
        return False
    if not CALL_CONTROL_CONTEXT_RE.match(one["context"]) or not CALL_CONTROL_EXTEN_RE.match(one["exten"]) \
            or one["priority"] != "1":
        return False
    if "extrachannel" in one:
        if not CALL_CONTROL_CHANNEL_RE.match(one["extrachannel"]) \
                or not CALL_CONTROL_CONTEXT_RE.match(one.get("extracontext", "")) \
                or not CALL_CONTROL_EXTEN_RE.match(one.get("extraexten", "")) or one.get("extrapriority") != "1":
            return False
    elif {"extracontext", "extraexten", "extrapriority"} & set(one):
        return False
    return True
UUID4_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
EPOCH_VARIABLE = "COLLECTIONS_HUMAN_PBX_EPOCH"


class AmiError(Exception):
    """Fixed-text failure with a stable `code`; never carries frame values or credentials."""

    def __init__(self, code: str, message: str = "AMI session unavailable"):
        super().__init__(message)
        self.code = code


def _invalid():
    return AmiError("ami_protocol_invalid", "Invalid AMI protocol data")


# --------------------------------------------------------------------------- frames
class Frame:
    """An ordered AMI message. `get` is for single-valued keys; `values` for repeated ones."""

    __slots__ = ("fields",)

    def __init__(self, fields):
        self.fields = tuple(fields)

    def values(self, key: str) -> list[str]:
        k = key.lower()
        return [v for name, v in self.fields if name.lower() == k]

    def get(self, key: str, default=None, *, required: bool = False):
        vals = self.values(key)
        if len(vals) > 1:
            raise _invalid()
        if not vals:
            if required:
                raise _invalid()
            return default
        if required and vals[0] == "":
            raise _invalid()
        return vals[0]

    @property
    def event(self):
        return self.get("Event")

    @property
    def response(self):
        return self.get("Response")

    def to_dict(self) -> dict:
        """First value per key (for logs/tests only; never for routing)."""
        out = {}
        for k, v in self.fields:
            out.setdefault(k, v)
        return out

    def __repr__(self):  # never print values (credentials, customer data)
        return f"<Frame {self.event or self.response or '?'} {len(self.fields)} fields>"


@dataclasses.dataclass(frozen=True)
class Greeting:
    banner: str


def _parse_frame(raw: bytes) -> Frame:
    text = raw.decode("utf-8", errors="replace")
    lines = text.split("\r\n")
    if not lines or len(lines) > MAX_FIELDS:
        raise _invalid()
    fields = []
    for line in lines:
        colon = line.find(":")
        key = line[:colon] if colon >= 1 else ""
        if not KEY_RE.match(key):
            # "ChanVariable(SIP/401-0000ab12): DIALERID=..." (Asterisk 11)
            if not LEGACY_CHANVAR_RE.match(key):
                raise _invalid()
            key = "ChanVariable"
        rest = line[colon + 1:]
        value = rest[1:] if rest.startswith(" ") else rest       # "Key:" and "Key:value" occur too
        if CONTROL_RE.search(value):
            raise _invalid()
        fields.append((key, value))
    frame = Frame(fields)
    for key in ROUTING_KEYS:
        frame.get(key)                                             # raises on duplicates
    return frame


class AmiDecoder:
    """Incremental CRLF decoder. `feed(bytes)` -> [Greeting | Frame]. Poisoned after an error."""

    def __init__(self):
        self._buf = bytearray()
        self._greeting = False
        self._failed = False
        self._ended = False
        self._line = 0

    def _fail(self):
        self._failed = True
        self._buf.clear()
        raise _invalid()

    def feed(self, chunk: bytes) -> list:
        if self._failed or self._ended or not isinstance(chunk, (bytes, bytearray)) or len(chunk) > MAX_CHUNK:
            self._fail()
        out = []
        buf = self._buf
        start, n = 0, len(chunk)
        while start < n:
            nl = chunk.find(b"\n", start)
            end = n if nl < 0 else nl + 1
            piece = chunk[start:end]
            start = end
            buf.extend(piece)
            self._line += len(piece)
            if len(buf) > MAX_FRAME or self._line > MAX_LINE:
                self._fail()
            if nl < 0:
                break
            self._line = 0
            if len(buf) < 2 or buf[-2] != 13:                       # bare LF
                self._fail()
            if not self._greeting:
                banner = bytes(buf[:-2])
                if any(b > 127 for b in banner) or not GREETING_RE.match(banner.decode("ascii")):
                    self._fail()
                self._greeting = True
                out.append(Greeting(banner.decode("ascii")))
                buf.clear()
            elif len(buf) == 2:                                    # empty line between frames
                buf.clear()
            elif buf.endswith(b"\r\n\r\n"):
                try:
                    out.append(_parse_frame(bytes(buf[:-4])))
                except AmiError:
                    self._fail()
                buf.clear()
        return out

    def end(self):
        if self._failed or self._ended or not self._greeting or self._buf:
            self._fail()
        self._ended = True


def encode_action(fields) -> bytes:
    """[(key, value), ...] -> wire bytes. One Action + one ActionID, no Event/Response,
    no control characters (so no header or action injection through a value)."""
    if not isinstance(fields, (list, tuple)) or not fields or len(fields) > MAX_FIELDS:
        raise _invalid()
    norm = []
    for pair in fields:
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            raise _invalid()
        k, v = pair
        if not isinstance(k, str) or not KEY_RE.match(k) or not isinstance(v, str) or CONTROL_RE.search(v):
            raise _invalid()
        if len((k + ": " + v + "\r\n").encode("utf-8")) > MAX_LINE:
            raise _invalid()
        norm.append((k, v))
    frame = Frame(norm)
    frame.get("Action", required=True)
    frame.get("ActionID", required=True)
    if frame.values("Event") or frame.values("Response"):
        raise _invalid()
    out = ("".join(f"{k}: {v}\r\n" for k, v in norm) + "\r\n").encode("utf-8")
    if len(out) > MAX_FRAME:
        raise _invalid()
    return out


# --------------------------------------------------------------------------- config
@dataclasses.dataclass(frozen=True)
class AmiConfig:
    host: str
    port: int
    username: str
    secret: str = dataclasses.field(repr=False)
    tls: bool = True
    ca_file: str | None = None
    server_name: str | None = None
    allow_plaintext_hosts: tuple = ()           # explicit private test hosts, e.g. ("pbx-test",)

    def validate(self):
        bad = AmiError("ami_configuration_invalid", "AMI configuration invalid")
        if not isinstance(self.host, str) or not self.host or re.search(r"[\s/\x00-\x1f\x7f]", self.host):
            raise bad
        if isinstance(self.port, bool) or not isinstance(self.port, int) or not 1 <= self.port <= 65535:
            raise bad
        for v in (self.username, self.secret):
            if not isinstance(v, str) or not v.strip() or len(v) > 4096 or CONTROL_RE.search(v):
                raise bad
        if self.server_name is not None and (not self.tls or not re.fullmatch(r"[A-Za-z0-9.-]{1,253}", self.server_name)):
            raise bad
        if not self.tls and not (_is_loopback(self.host) or self.host in self.allow_plaintext_hosts):
            raise AmiError("ami_plaintext_refused", "plaintext AMI is only allowed to loopback or an allowed test host")
        return self


def _is_loopback(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host == "localhost"


# --------------------------------------------------------------------------- session
class _Pending:
    __slots__ = ("event", "frame", "error", "deadline", "collector")

    def __init__(self, deadline, collector=None):
        self.event = threading.Event()
        self.frame = None
        self.error = None
        self.deadline = deadline
        self.collector = collector


class _ListCollector:
    """Rows of an EventList action (CoreShowChannels) until the matching Complete event."""

    def __init__(self, item_event: str, complete_event: str, max_items: int):
        self.item_event, self.complete_event, self.max_items = item_event, complete_event, max_items
        self.acknowledged = False
        self.rows: list[Frame] = []

    def acknowledge(self, frame: Frame):
        if frame.response != "Success" or (frame.get("EventList") or "").lower() != "start":
            raise AmiError("ami_list_refused", "list action refused")
        self.acknowledged = True

    def event(self, frame: Frame):
        """Returns the rows when complete, else None. Raises on protocol violations."""
        if not self.acknowledged:
            raise _invalid()
        name = frame.event
        if name == self.item_event:
            self.rows.append(frame)
            if len(self.rows) > self.max_items:
                raise AmiError("ami_list_overflow", "list larger than allowed")
            return None
        if name == self.complete_event:
            count = frame.get("ListItems")
            if count is None or not count.isdigit() or int(count) != len(self.rows):
                raise _invalid()
            return self.rows
        raise _invalid()


class AmiSession:
    """One authenticated AMI connection. Thread-safe `send`. See the module docstring."""

    _ids = itertools.count(1)

    def __init__(self, config: AmiConfig, on_event, *, on_heartbeat=None, on_close=None,
                 connect_timeout: float = 5.0, action_timeout: float = 5.0, event_timeout: float = 10.0,
                 heartbeat: float = 10.0, max_pending: int = 64, max_events: int = 4096,
                 hold_events: bool = False):
        self.config = config.validate()
        if not callable(on_event):
            raise AmiError("ami_configuration_invalid", "AMI configuration invalid")
        self.on_event, self.on_heartbeat, self.on_close = on_event, on_heartbeat, on_close
        self.connect_timeout, self.action_timeout = connect_timeout, action_timeout
        self.event_timeout, self.heartbeat_interval = event_timeout, heartbeat
        self.max_pending, self.max_events = max_pending, max_events
        self.session_id = f"{uuid.uuid4().hex[:12]}-{next(self._ids)}"
        self.banner = None
        self.closed = threading.Event()
        self.close_code = None
        self.connected_at = None
        self._sock = None
        self._decoder = AmiDecoder()
        self._lock = threading.RLock()
        self._write_lock = threading.Lock()
        self._pending: dict[str, _Pending] = {}
        self._seq = itertools.count(1)
        self._events: queue.Queue = queue.Queue()
        self._queued = 0
        self._events_released = threading.Event()
        if not hold_events:
            self._events_released.set()
        self._consumer_busy_since = None
        self._state = "new"
        self._threads: list[threading.Thread] = []
        self.stats = collections.Counter()

    # ---- lifecycle ------------------------------------------------------------------
    def connect(self) -> "AmiSession":
        cfg = self.config
        with self._lock:
            if self._state != "new":
                raise AmiError("ami_not_ready")
            self._state = "connecting"
        try:
            raw = socket.create_connection((cfg.host, cfg.port), timeout=self.connect_timeout)
            raw.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            if cfg.tls:
                ctx = ssl.create_default_context(cafile=cfg.ca_file) if cfg.ca_file else ssl.create_default_context()
                ctx.minimum_version = ssl.TLSVersion.TLSv1_2
                ctx.check_hostname = True
                ctx.verify_mode = ssl.CERT_REQUIRED
                server_name = cfg.server_name or (None if _is_ip(cfg.host) else cfg.host)
                raw = ctx.wrap_socket(raw, server_hostname=server_name or cfg.host)
            raw.settimeout(0.5)
            self._sock = raw
        except ssl.SSLError:
            self._finish("ami_tls_failed")
            raise AmiError("ami_tls_failed") from None
        except OSError:
            self._finish("ami_connection_failed")
            raise AmiError("ami_connection_failed") from None

        deadline = time.monotonic() + self.connect_timeout
        # greeting, then Login: both handled by the reader thread (_dispatch)
        login = _Pending(deadline)
        self._login_pending = login
        self._start_thread(self._reader, "ami-reader")
        while not login.event.wait(0.05):
            if self.closed.is_set():
                raise AmiError(self.close_code or "ami_connection_lost")
            if time.monotonic() > deadline:
                self._finish("ami_connect_timeout")
                raise AmiError("ami_connect_timeout")
        if login.error is not None:
            raise login.error
        with self._lock:
            self._state = "ready"
            self.connected_at = time.time()
        self._start_thread(self._consumer, "ami-events")
        if self.heartbeat_interval:
            self._start_thread(self._heartbeat, "ami-heartbeat")
        return self

    def release_events(self):
        """Start delivering events (sessions created with hold_events=True buffer them)."""
        self._events_released.set()

    def close(self, code: str = "ami_closed"):
        if self._state == "ready" and code == "ami_closed":
            try:                                                   # best effort, never waited on
                self._write(encode_action([("Action", "Logoff"), ("ActionID", f"{self.session_id}:logoff")]))
            except Exception:  # noqa: BLE001
                pass
        self._finish(code)

    @property
    def ready(self) -> bool:
        return self._state == "ready" and not self.closed.is_set()

    def status(self) -> dict:
        return {"session_id": self.session_id, "state": "closed" if self.closed.is_set() else self._state,
                "close_code": self.close_code, "pending": len(self._pending), "queued_events": self._queued,
                "events": self.stats["events"], "connected_at": self.connected_at}

    # ---- actions --------------------------------------------------------------------
    def send(self, action: str, fields=(), *, timeout: float | None = None, action_id: str | None = None) -> Frame:
        """Send an allowed action and wait for its response frame (any Response value).
        `action_id`: one this session issued with reserve_action_id() (else a new one)."""
        pending, action_id = self._issue(action, fields, timeout=timeout, action_id=action_id)
        return self._wait(pending, action_id)

    def reserve_action_id(self) -> str:
        """A fresh `<session>:<n>` ActionID, for an action whose ActionID must be recorded BEFORE it is
        sent (Asterisk <= 11: the Originate's OriginateResponse is correlated by its ActionID only,
        services/calling/legacy_correlation.py). Pass it to send(action_id=...) once."""
        with self._lock:
            return f"{self.session_id}:{next(self._seq)}"

    def list_channels(self, max_channels: int = 4096) -> list[Frame]:
        if not isinstance(max_channels, int) or not 1 <= max_channels <= 4096:
            raise AmiError("ami_action_invalid")
        collector = _ListCollector("CoreShowChannel", "CoreShowChannelsComplete", max_channels)
        pending, action_id = self._issue("CoreShowChannels", (), collector=collector)
        return self._wait(pending, action_id)

    def _issue(self, action, fields, *, internal=False, timeout=None, collector=None, action_id=None):
        if action not in ALLOWED_ACTIONS and not internal:
            raise AmiError("ami_action_invalid", "action not allowed")
        fields = list(fields)
        if any(not isinstance(p, (list, tuple)) or len(p) != 2 or not isinstance(p[0], str)
               or p[0].lower() in ("action", "actionid", "event", "response") for p in fields):
            raise AmiError("ami_action_invalid", "invalid action fields")
        if not internal and not call_control_fields_ok(action, fields):
            raise AmiError("ami_action_invalid", "invalid action fields")
        with self._lock:
            if self.closed.is_set() or (self._state != "ready" and not internal):
                raise AmiError("ami_not_ready")
            if len(self._pending) >= self.max_pending:
                raise AmiError("ami_backpressure")
            if action_id is None:
                action_id = f"{self.session_id}:{next(self._seq)}"
            elif (not isinstance(action_id, str) or not action_id.startswith(f"{self.session_id}:")
                  or not action_id[len(self.session_id) + 1:].isdigit() or action_id in self._pending):
                raise AmiError("ami_action_invalid", "invalid action id")
            data = encode_action([("Action", action), ("ActionID", action_id), *fields])
            pending = _Pending(time.monotonic() + (timeout or self.action_timeout), collector)
            self._pending[action_id] = pending
        try:
            self._write(data)
        except OSError:
            self._finish("ami_connection_lost")
            raise AmiError("ami_connection_lost") from None
        return pending, action_id

    def _wait(self, pending: _Pending, action_id: str, timeout_code: str = "ami_action_timeout"):
        while not pending.event.wait(0.05):
            if self.closed.is_set():
                raise AmiError(self.close_code or "ami_connection_lost")
            if time.monotonic() > pending.deadline:
                # A lost acknowledgement leaves the PBX state unknown: close, never retry.
                self._finish(timeout_code)
                raise AmiError(timeout_code)
        if pending.error is not None:
            raise pending.error
        return pending.frame

    def _write(self, data: bytes):
        with self._write_lock:
            self._sock.sendall(data)

    # ---- threads --------------------------------------------------------------------
    def _start_thread(self, target, name):
        t = threading.Thread(target=target, name=f"{name}-{self.session_id}", daemon=True)
        self._threads.append(t)
        t.start()

    def _reader(self):
        sock = self._sock
        while not self.closed.is_set():
            try:
                chunk = sock.recv(65536)
            except (socket.timeout, TimeoutError):
                continue
            except ssl.SSLWantReadError:
                continue
            except OSError:
                self._finish("ami_connection_lost")
                return
            if not chunk:
                try:
                    self._decoder.end()
                    self._finish("ami_connection_lost")
                except AmiError:
                    self._finish("ami_protocol_invalid" if self._decoder._greeting else "ami_connection_lost")
                return
            try:
                messages = self._decoder.feed(chunk)
            except AmiError:
                self._finish("ami_protocol_invalid")
                return
            for msg in messages:
                if self.closed.is_set():
                    return
                try:
                    self._dispatch(msg)
                except AmiError as e:
                    self._finish(e.code)
                    return

    def _dispatch(self, msg):
        if isinstance(msg, Greeting):
            if self._state != "connecting" or self.banner is not None:
                raise _invalid()
            self.banner = msg.banner
            cfg = self.config
            pending, action_id = self._issue("Login", [("Username", cfg.username), ("Secret", cfg.secret),
                                                        ("Events", "on")], internal=True)
            self._login_id = action_id
            return
        response, event = msg.response, msg.event
        if response is None and event is None:
            raise _invalid()
        if response is not None and event is None:
            action_id = msg.get("ActionID", required=True)
            with self._lock:
                pending = self._pending.get(action_id)
                if pending is None:
                    self.stats["unknown_responses"] += 1        # cannot settle another action
                    return
                if pending.collector is not None and not pending.collector.acknowledged:
                    try:
                        pending.collector.acknowledge(msg)
                    except AmiError as e:
                        self._pending.pop(action_id, None)
                        pending.error = e
                        pending.event.set()
                    return
                self._pending.pop(action_id, None)
            if action_id == getattr(self, "_login_id", None):
                login = self._login_pending
                if response != "Success":
                    login.error = AmiError("ami_authentication_failed")
                    login.event.set()
                    self._finish("ami_authentication_failed")
                    return
                login.frame = msg
                login.event.set()
                return
            pending.frame = msg
            pending.event.set()
            return
        # an event (OriginateResponse included: it has Response but it is an Event)
        action_id = msg.get("ActionID")
        if action_id:
            with self._lock:
                pending = self._pending.get(action_id)
            if pending is not None and pending.collector is not None:
                rows = pending.collector.event(msg)            # raises on violations
                if rows is not None:
                    # Resolve through the ordered queue: every earlier event is consumed
                    # first (a receive-order fence, not an atomic PBX snapshot).
                    def finish_list(p=pending, aid=action_id, r=rows):
                        with self._lock:
                            self._pending.pop(aid, None)
                        p.frame = r
                        p.event.set()
                    self._enqueue(("call", finish_list))
                return
        if self._state not in ("connecting", "ready"):
            return
        self._enqueue(("event", msg))

    def _enqueue(self, item):
        with self._lock:
            if self._queued >= self.max_events:
                overflow = True
            else:
                overflow = False
                self._queued += 1
        if overflow:
            self._finish("ami_event_overflow")
            return
        self._events.put(item)

    def _consumer(self):
        while not self.closed.is_set():
            if not self._events_released.wait(0.1):
                continue
            try:
                kind, payload = self._events.get(timeout=0.1)
            except queue.Empty:
                continue
            with self._lock:
                self._queued -= 1
            self._consumer_busy_since = time.monotonic()
            try:
                if kind == "event":
                    self.stats["events"] += 1
                    self.on_event(payload, self)
                elif kind == "heartbeat":
                    if self.on_heartbeat is not None:
                        self.on_heartbeat(self)
                else:
                    payload()
            except Exception:  # noqa: BLE001 - a failing consumer ends the session (no silent loss)
                log.exception("AMI event consumer failed; closing session %s", self.session_id)
                self._finish("ami_event_sink_failed")
                return
            finally:
                self._consumer_busy_since = None

    def _heartbeat(self):
        next_ping = time.monotonic() + self.heartbeat_interval
        while not self.closed.wait(0.2):
            busy = self._consumer_busy_since
            if busy is not None and time.monotonic() - busy > self.event_timeout:
                self._finish("ami_event_sink_failed")
                return
            if time.monotonic() < next_ping:
                continue
            try:
                pending, action_id = self._issue("Ping", ())
                frame = self._wait(pending, action_id, timeout_code="ami_heartbeat_failed")
                if frame.response != "Success" or (frame.get("Ping") or "").lower() != "pong":
                    self._finish("ami_heartbeat_failed")
                    return
            except AmiError:
                if not self.closed.is_set():
                    self._finish("ami_heartbeat_failed")
                return
            self.stats["heartbeats"] += 1
            if self.on_heartbeat is not None:
                self._enqueue(("heartbeat", None))
            next_ping = time.monotonic() + self.heartbeat_interval

    def _finish(self, code: str):
        with self._lock:
            if self.closed.is_set():
                return
            self.close_code = code
            self._state = "closed"
            self.closed.set()
            pending = list(self._pending.values())
            self._pending.clear()
        for p in pending:
            p.error = AmiError(code)
            p.event.set()
        login = getattr(self, "_login_pending", None)
        if login is not None and not login.event.is_set():
            login.error = AmiError(code)
            login.event.set()
        try:
            if self._sock is not None:
                self._sock.close()
        except OSError:
            pass
        if self.on_close is not None:
            try:
                self.on_close(self, code)
            except Exception:  # noqa: BLE001
                log.exception("AMI on_close callback failed")


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


# --------------------------------------------------------------------------- PBX identity
def ami_major(banner: str | None) -> int | None:
    """The AMI protocol major version of a greeting (`Asterisk Call Manager/1.3` -> 1), else None.
    AMI 1.x = Asterisk <= 11 (CALLING_PBX_EVENTS=legacy); 2.x and later = Asterisk 12+ (modern)."""
    m = re.fullmatch(r"Asterisk Call Manager/(\d+)\.\d+(?:\.\d+)?", (banner or "").strip())
    return int(m.group(1)) if m else None


def version_mismatch(banner: str | None, mode: str) -> bool:
    """True when the greeting definitely disagrees with the event model `mode` (modern | legacy);
    an unknown greeting is not a mismatch (it proves nothing either way)."""
    major = ami_major(banner)
    if major is None:
        return False
    return (major <= 1) != (mode == "legacy")


@dataclasses.dataclass(frozen=True)
class PbxIdentity:
    """Which Asterisk *process* this session talks to.

    `epoch_uuid` is the global COLLECTIONS_HUMAN_PBX_EPOCH (new on every process start, from
    the pre-start hook); `startup` is CoreStatus CoreStartupDate + CoreStartupTime (to the
    second, PBX local time). Reloads and reconnects keep both; a restart changes both (a
    `core restart` or a crash restart by safe_asterisk changes at least `startup`)."""

    epoch_uuid: str | None
    startup: str | None
    reload: str | None = None
    ami_version: str | None = None

    @property
    def epoch(self) -> str:
        return f"{self.epoch_uuid or 'none'}@{self.startup or 'unknown'}"

    @property
    def strength(self) -> str:
        if self.epoch_uuid and self.startup:
            return "uuid+startup"
        return "startup_only" if self.startup else "unknown"

    def same_process(self, other: "PbxIdentity | None") -> bool:
        return other is not None and self.epoch == other.epoch and self.strength != "unknown"


MIN_STARTUP_YEAR = 2000


def read_identity(session: AmiSession) -> PbxIdentity:
    """Getvar of the epoch global + CoreStatus. A missing/malformed epoch UUID is recorded
    as missing (strength `startup_only`), never invented.

    2026-10-25: Asterisk sets its startup time only when it is FULLY BOOTED (it reports
    1970-01-01 before that, and the dialplan globals are not loaded yet). A session opened while
    the PBX is still booting (the calling service reconnected a minute before Asterisk was ready
    after a reboot and kept the epoch `none@1970-01-01T03:00:00` for the day) is refused with
    `pbx_not_booted`, so the supervisor reconnects with backoff until the PBX is ready."""
    epoch_uuid = None
    frame = session.send("Getvar", [("Variable", f"GLOBAL({EPOCH_VARIABLE})")])
    if frame.response == "Success":
        value = (frame.get("Value") or "").strip().lower()
        if UUID4_RE.match(value):
            epoch_uuid = value
    status = session.send("CoreStatus")
    startup = reload = None
    if status.response == "Success":
        d, t = status.get("CoreStartupDate"), status.get("CoreStartupTime")
        if d and t and re.fullmatch(r"\d{4}-\d{2}-\d{2}", d) and re.fullmatch(r"\d{2}:\d{2}:\d{2}", t):
            startup = f"{d}T{t}"
            if int(d[:4]) < MIN_STARTUP_YEAR:
                raise AmiError("pbx_not_booted", "The PBX is still starting")
        rd, rt = status.get("CoreReloadDate"), status.get("CoreReloadTime")
        if rd and rt:
            reload = f"{rd}T{rt}"
    return PbxIdentity(epoch_uuid, startup, reload, session.banner)


# --------------------------------------------------------------------------- supervisor
@dataclasses.dataclass
class SessionContext:
    session: AmiSession
    identity: PbxIdentity
    previous: PbxIdentity | None
    connected_at: float

    @property
    def epoch(self) -> str:
        return self.identity.epoch

    @property
    def pbx_restarted(self) -> bool:
        return self.previous is not None and not self.identity.same_process(self.previous)


class AmiSupervisor(threading.Thread):
    """Keeps one AMI session up. Callbacks run on the session's consumer thread in order:

        on_session_start(ctx)          after login + identity, before any event
        on_event(frame, ctx)           every event of that session, in receive order
        on_heartbeat(ctx)              after each Pong, ordered after earlier events
        on_session_end(ctx, code)      after the session closed (from the supervisor thread)

    A callback exception closes the session (evidence is never silently dropped); the
    supervisor then reconnects with backoff. Nothing is replayed."""

    def __init__(self, config: AmiConfig, *, on_event, on_session_start=None, on_session_end=None,
                 on_heartbeat=None, session_options: dict | None = None,
                 backoff_initial: float = 1.0, backoff_max: float = 30.0, name: str = "ami-supervisor"):
        super().__init__(name=name, daemon=True)
        self.config = config.validate()
        self.on_event, self.on_session_start = on_event, on_session_start
        self.on_session_end, self.on_heartbeat = on_session_end, on_heartbeat
        self.session_options = dict(session_options or {})
        self.backoff_initial, self.backoff_max = backoff_initial, backoff_max
        self.stopping = threading.Event()
        self.current: SessionContext | None = None
        self.last_identity: PbxIdentity | None = None
        self.last_error: str | None = None
        self.sessions = 0
        self._lock = threading.Lock()

    def status(self) -> dict:
        ctx = self.current
        return {
            "state": "connected" if ctx and ctx.session.ready else ("stopping" if self.stopping.is_set() else "reconnecting"),
            "epoch": ctx.epoch if ctx else (self.last_identity.epoch if self.last_identity else None),
            "epoch_strength": (ctx.identity if ctx else self.last_identity).strength
            if (ctx or self.last_identity) else None,
            "sessions": self.sessions, "last_error": self.last_error,
            "session": ctx.session.status() if ctx else None,
        }

    def stop(self, timeout: float = 5.0):
        self.stopping.set()
        ctx = self.current
        if ctx is not None:
            ctx.session.close("ami_closed")
        if self.is_alive() and threading.current_thread() is not self:
            self.join(timeout)

    def run(self):
        delay = self.backoff_initial
        while not self.stopping.is_set():
            ctx = None
            holder = {}

            def on_event(frame, _session, h=holder):
                self.on_event(frame, h["ctx"])

            def on_heartbeat(_session, h=holder):
                if self.on_heartbeat is not None:
                    self.on_heartbeat(h["ctx"])

            session = AmiSession(self.config, on_event, on_heartbeat=on_heartbeat, hold_events=True,
                                 **self.session_options)
            try:
                session.connect()
                identity = read_identity(session)
                ctx = SessionContext(session, identity, self.last_identity, time.time())
                holder["ctx"] = ctx
                if self.on_session_start is not None:
                    self.on_session_start(ctx)
                with self._lock:
                    self.current = ctx
                    self.last_identity = identity
                    self.sessions += 1
                self.last_error = None
                session.release_events()
                delay = self.backoff_initial
                while not session.closed.wait(0.25):
                    if self.stopping.is_set():
                        session.close("ami_closed")
                code = session.close_code
            except AmiError as e:
                code = e.code
                session.close(code if code != "ami_closed" else "ami_setup_failed")
            except Exception:  # noqa: BLE001 - e.g. on_session_start could not persist the epoch
                log.exception("AMI session setup failed")
                code = "ami_setup_failed"
                session.close(code)
            self.last_error = None if code == "ami_closed" else code
            with self._lock:
                self.current = None
            if ctx is not None and self.on_session_end is not None:
                try:
                    self.on_session_end(ctx, code)
                except Exception:  # noqa: BLE001
                    log.exception("on_session_end failed")
            if self.stopping.is_set():
                break
            log.warning("AMI session ended (%s); reconnecting in %.1fs", code, delay)
            self.stopping.wait(delay)
            delay = min(self.backoff_max, delay * 2)
