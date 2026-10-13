"""FastAGI authorization hook: the PBX asks before any customer leg is dialled.

The generated dialplan (services/calling/dialplan.py) runs `AGI(${GLOBAL(COLLECTIONS_AGI_URL)})`
on the AGENT channel once the agent answered. This server answers ALLOW only when a one-shot
dispatch token (call_dispatch_tokens, migration 0049) is consumed for exactly this channel.
P1: the dispatcher issues tokens only after every dispatch rule passed (services/calling/
rules.py); this hook re-checks the case/contact safety rules (locked, archived, do-not-call,
a blocking review, a blocked contact) before consuming the token.

Protocol (Asterisk FastAGI over TCP, line based):
    <- agi_network: yes / agi_network_script / agi_request / agi_channel / agi_type /
       agi_uniqueid / agi_context / agi_extension / ... then an empty line
    -> SET VARIABLE COLLECTIONS_GATE ""            (clear every gate output first)
    -> GET FULL VARIABLE "${GLOBAL(COLLECTIONS_AGI_KEY)}"          shared secret (constant-time)
    -> GET VARIABLE COLLECTIONS_ATTEMPT / COLLECTIONS_TOKEN
    -> GET FULL VARIABLE "${GLOBAL(COLLECTIONS_HUMAN_PBX_EPOCH)}"  the PBX process epoch
       (database: lock the token row, check, consume, record the decision; commit)
    -> SET VARIABLE COLLECTIONS_GATE_DESTINATION / _EXTENSION / _LIMIT / _EXPIRES
    -> SET VARIABLE COLLECTIONS_GATE "ALLOW"       (last, only after the commit)
Globals are read with GLOBAL() so a channel variable cannot stand in for them.

Hardening: peer allowlist (CIDRs or host names), bounded concurrent connections, a whole-
exchange deadline (5 s), bounded line length/count, fixed command set, fixed decision reasons.
Any failure or doubt denies (the dialplan hangs up without dialling). A lost reply after the
commit leaves the token consumed: it is never granted twice. Every decision is appended to
call_agi_decisions; tokens, keys and raw protocol lines are never logged.
"""
from __future__ import annotations

import dataclasses
import hmac
import ipaddress
import logging
import re
import socket
import threading
import time

from sqlalchemy import text

log = logging.getLogger("cawp.calling.agi")

SCRIPT = "collections-authorize"
SURVEY_SCRIPTS = ("collections-survey-offer", "collections-survey-result")
MAX_LINE = 4096
MAX_HEADERS = 64
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
TOKEN_RE = re.compile(r"^[0-9a-f]{64}$")
E164_RE = re.compile(r"^\+[1-9][0-9]{7,14}$")
CONTEXT_RE = re.compile(r"^collections-human-[a-z0-9_-]{1,50}$")
GATE_VARS = ("COLLECTIONS_GATE", "COLLECTIONS_GATE_DESTINATION", "COLLECTIONS_GATE_EXTENSION",
             "COLLECTIONS_GATE_LIMIT", "COLLECTIONS_GATE_EXPIRES")


class AgiDeny(Exception):
    def __init__(self, reason: str, **ctx):
        super().__init__(reason)
        self.reason, self.ctx = reason, ctx


@dataclasses.dataclass(frozen=True)
class Decision:
    allow: bool
    reason: str
    destination: str | None = None
    extension: str | None = None
    max_call_seconds: int | None = None
    expires_epoch: int | None = None


# --------------------------------------------------------------------------- peers
class PeerAllowlist:
    """CIDRs and host names (resolved on use, cached 30 s). Empty = nobody."""

    def __init__(self, entries):
        self.networks, self.hosts = [], []
        for e in entries:
            e = e.strip()
            if not e:
                continue
            try:
                self.networks.append(ipaddress.ip_network(e, strict=False))
            except ValueError:
                if not re.fullmatch(r"[A-Za-z0-9.-]{1,253}", e):
                    raise ValueError("invalid AGI peer allowlist entry") from None
                self.hosts.append(e)
        self._cache: dict[str, tuple[float, set]] = {}

    def _resolve(self, host):
        hit = self._cache.get(host)
        if hit and hit[0] > time.monotonic():
            return hit[1]
        try:
            addrs = {ai[4][0] for ai in socket.getaddrinfo(host, None)}
        except OSError:
            addrs = set()
        self._cache[host] = (time.monotonic() + 30, addrs)
        return addrs

    def allows(self, peer_ip: str) -> bool:
        try:
            ip = ipaddress.ip_address(peer_ip)
        except ValueError:
            return False
        if any(ip in n for n in self.networks):
            return True
        return any(peer_ip in self._resolve(h) for h in self.hosts)


# --------------------------------------------------------------------------- protocol
class AgiChannel:
    """Line I/O with one deadline for the whole exchange."""

    def __init__(self, sock: socket.socket, deadline: float):
        self.sock, self.deadline, self.buf = sock, deadline, b""

    def readline(self) -> str:
        while b"\n" not in self.buf:
            left = self.deadline - time.monotonic()
            if left <= 0:
                raise AgiDeny("agi_timeout")
            self.sock.settimeout(left)
            try:
                chunk = self.sock.recv(4096)
            except (socket.timeout, TimeoutError):
                raise AgiDeny("agi_timeout") from None
            except OSError:
                raise AgiDeny("agi_connection_lost") from None
            if not chunk:
                raise AgiDeny("agi_connection_lost")
            self.buf += chunk
            if len(self.buf) > MAX_LINE and b"\n" not in self.buf:
                raise AgiDeny("agi_protocol_invalid")
        line, self.buf = self.buf.split(b"\n", 1)
        if len(line) > MAX_LINE:
            raise AgiDeny("agi_protocol_invalid")
        try:
            out = line.rstrip(b"\r").decode("utf-8")
        except UnicodeDecodeError:
            raise AgiDeny("agi_protocol_invalid") from None
        if re.search(r"[\x00-\x08\x0b-\x1f\x7f]", out):
            raise AgiDeny("agi_protocol_invalid")
        return out

    def command(self, cmd: str) -> str:
        try:
            self.sock.sendall((cmd + "\n").encode("utf-8"))
        except OSError:
            raise AgiDeny("agi_connection_lost") from None
        reply = self.readline()
        if reply.startswith("HANGUP"):                       # channel hung up meanwhile
            raise AgiDeny("channel_hangup")
        return reply

    def set_var(self, name: str, value: str):
        if not re.fullmatch(r"[A-Z_]{1,40}", name) or not re.fullmatch(r"[A-Za-z0-9+_-]{0,64}", value):
            raise AgiDeny("agi_internal_error")
        if self.command(f'SET VARIABLE {name} "{value}"') != "200 result=1":
            raise AgiDeny("agi_set_failed")

    def get_var(self, name: str) -> str | None:
        reply = self.command(f"GET VARIABLE {name}")
        return _value(reply)

    def get_global(self, name: str) -> str | None:
        reply = self.command('GET FULL VARIABLE "${GLOBAL(' + name + ')}"')
        return _value(reply)


def _value(reply: str) -> str | None:
    if reply == "200 result=0":
        return None
    m = re.fullmatch(r"200 result=1 \(([^()\r\n]*)\)", reply)
    if not m:
        raise AgiDeny("agi_protocol_invalid")
    return m.group(1)


def read_environment(ch: AgiChannel) -> dict:
    env, seen = {}, set()
    for _ in range(MAX_HEADERS + 1):
        line = ch.readline()
        if line == "":
            return env
        m = re.fullmatch(r"(agi_[a-z0-9_]{1,40}): ?(.*)", line)
        if not m or m.group(1) in seen:
            raise AgiDeny("agi_protocol_invalid")
        seen.add(m.group(1))
        env[m.group(1)] = m.group(2)
    raise AgiDeny("agi_protocol_invalid")


# --------------------------------------------------------------------------- decision
@dataclasses.dataclass
class AgiRequest:
    peer: str
    channel_id: str | None = None
    channel: str | None = None
    driver: str | None = None
    context: str | None = None
    destination: str | None = None
    attempt_id: str | None = None
    token: str | None = None
    epoch_uuid: str | None = None


def validate_environment(env: dict, req: AgiRequest):
    req.channel_id = env.get("agi_uniqueid")
    req.channel = env.get("agi_channel")
    req.driver = env.get("agi_type")
    req.context = env.get("agi_context")
    req.destination = env.get("agi_extension")
    if env.get("agi_network") != "yes":
        raise AgiDeny("not_fastagi")
    script = (env.get("agi_network_script") or "").strip("/")
    if script != SCRIPT:
        raise AgiDeny("wrong_script")
    if req.driver not in ("PJSIP", "SIP"):
        raise AgiDeny("wrong_channel_driver")
    if not req.context or not CONTEXT_RE.match(req.context):
        raise AgiDeny("wrong_context")
    if not req.destination or not E164_RE.match(req.destination):
        raise AgiDeny("bad_destination")
    if not req.channel_id or not re.fullmatch(r"cawp-[0-9a-f-]{36}", req.channel_id) \
            or not UUID_RE.match(req.channel_id[5:]):
        raise AgiDeny("bad_channel_id")


_LOCK_TOKEN = text("""
    SELECT t.id, t.attempt_id, t.channel_id, t.context, t.destination, t.extension, t.max_call_seconds,
           t.pbx_epoch_uuid, t.expires_at, t.consumed_at, (t.expires_at <= now()) AS expired,
           floor(extract(epoch FROM t.expires_at))::bigint AS expires_epoch, a.state AS attempt_state
      FROM call_dispatch_tokens t JOIN call_attempts a ON a.id = t.attempt_id
     WHERE t.token_sha256 = :h
       FOR UPDATE OF t
""")
_RECORD = text("""
    INSERT INTO call_agi_decisions (decision, reason, attempt_id, token_id, peer, channel_id, context, destination,
                                    pbx_epoch_uuid, max_call_seconds, expires_at)
    VALUES (:decision, :reason, CAST(:attempt AS uuid), CAST(:token AS uuid), :peer, :channel, :context, :dest,
            :epoch, :lim, :exp)
""")


def record_decision(session, decision: str, reason: str, req: AgiRequest, *, token_id=None, lim=None, exp=None):
    session.execute(_RECORD, {
        "decision": decision, "reason": reason, "attempt": req.attempt_id if req.attempt_id and UUID_RE.match(req.attempt_id) else None,
        "token": token_id, "peer": (req.peer or "")[:64], "channel": (req.channel_id or "")[:150] or None,
        "context": (req.context or "")[:80] or None, "dest": (req.destination or "")[:50] or None,
        "epoch": (req.epoch_uuid or "")[:100] or None, "lim": lim, "exp": exp})


def authorize(session, req: AgiRequest) -> Decision:
    """Consume the one-shot token for this exact channel, or deny. Records the decision in the
    caller's transaction (the caller commits BEFORE publishing ALLOW)."""
    from services.calling.attempts import token_hash
    if not req.token or not TOKEN_RE.match(req.token):
        raise AgiDeny("token_missing")
    if not req.attempt_id or not UUID_RE.match(req.attempt_id):
        raise AgiDeny("attempt_missing")
    if req.channel_id != f"cawp-{req.attempt_id}":
        raise AgiDeny("attempt_mismatch")
    t = session.execute(_LOCK_TOKEN, {"h": token_hash(req.token)}).mappings().first()
    if t is None:
        raise AgiDeny("token_unknown")
    tid = str(t["id"])
    if t["consumed_at"] is not None:
        raise AgiDeny("token_consumed", token_id=tid)
    if t["expired"]:
        raise AgiDeny("token_expired", token_id=tid)
    if str(t["attempt_id"]) != req.attempt_id or t["channel_id"] != req.channel_id:
        raise AgiDeny("token_mismatch", token_id=tid)
    if t["context"] != req.context or t["destination"] != req.destination:
        raise AgiDeny("token_mismatch", token_id=tid)
    if not req.channel or not re.fullmatch(rf"{req.driver}/{re.escape(t['extension'])}-[0-9a-fA-F]+", req.channel):
        raise AgiDeny("extension_mismatch", token_id=tid)
    if t["pbx_epoch_uuid"] is not None and t["pbx_epoch_uuid"] != (req.epoch_uuid or ""):
        raise AgiDeny("epoch_mismatch", token_id=tid)
    if t["attempt_state"] in ("ended", "failed", "cancelled", "uncertain", "prepared"):
        raise AgiDeny("attempt_not_dispatchable", token_id=tid)
    # P1: the case/contact safety rules can change while the agent's phone rings (a supervisor
    # locks the case, a wrap-up marks do-not-call or a wrong number): recheck them here, the
    # last point before the customer is dialled (services/calling/rules.py).
    from services.calling.rules import agi_recheck
    refused = agi_recheck(session, req.attempt_id)
    if refused is not None:
        raise AgiDeny(refused, token_id=tid)
    session.execute(text("UPDATE call_dispatch_tokens SET consumed_at = now() WHERE id = CAST(:id AS uuid)"),
                    {"id": tid})
    record_decision(session, "allow", "allowed", req, token_id=tid, lim=t["max_call_seconds"], exp=t["expires_at"])
    return Decision(True, "allowed", t["destination"], t["extension"], int(t["max_call_seconds"]),
                    int(t["expires_epoch"]))


class FastAgiServer(threading.Thread):
    """Threaded FastAGI listener. `app` is a Flask app bound to the database."""

    def __init__(self, app, *, host: str = "0.0.0.0", port: int = 4573, secret: str,
                 allowed_peers: PeerAllowlist, max_connections: int = 32, exchange_timeout: float = 5.0):
        super().__init__(name="fastagi", daemon=True)
        if not isinstance(secret, str) or len(secret) < 8:
            raise ValueError("CALLING_AGI_SECRET must be set (8+ characters)")
        self.app, self.secret = app, secret
        self.allowed = allowed_peers
        self.exchange_timeout = exchange_timeout
        self.slots = threading.BoundedSemaphore(max_connections)
        self.stopping = threading.Event()
        self.sock = socket.create_server((host, port), reuse_port=False, backlog=64)
        self.sock.settimeout(0.5)
        self.address = self.sock.getsockname()
        self.stats = {"allow": 0, "deny": 0, "refused": 0}
        self._active = 0
        self._active_lock = threading.Lock()

    def run(self):
        while not self.stopping.is_set():
            try:
                conn, addr = self.sock.accept()
            except (socket.timeout, TimeoutError):
                continue
            except OSError:
                if self.stopping.is_set():
                    break
                continue
            if not self.slots.acquire(blocking=False):
                self.stats["refused"] += 1
                conn.close()
                continue
            threading.Thread(target=self._serve, args=(conn, addr[0]), name="fastagi-conn", daemon=True).start()
        try:
            self.sock.close()
        except OSError:
            pass

    def stop(self, timeout: float = 6.0):
        """Stop accepting; wait (bounded) for in-flight exchanges to finish."""
        self.stopping.set()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._active_lock:
                if self._active == 0:
                    break
            time.sleep(0.05)
        self.join(timeout=1)

    def _serve(self, conn: socket.socket, peer: str):
        with self._active_lock:
            self._active += 1
        try:
            self.handle(conn, peer)
        finally:
            with self._active_lock:
                self._active -= 1
            self.slots.release()
            try:
                conn.close()
            except OSError:
                pass

    def handle(self, conn: socket.socket, peer: str) -> Decision:
        from extensions import db
        req = AgiRequest(peer=peer)
        ch = AgiChannel(conn, time.monotonic() + self.exchange_timeout)
        decision = None
        with self.app.app_context():
            try:
                if not self.allowed.allows(peer):
                    raise AgiDeny("peer_not_allowed")
                env = read_environment(ch)
                script = (env.get("agi_network_script") or "").strip("/")
                if script in SURVEY_SCRIPTS:                     # post-call survey (services/calling/survey.py)
                    return self._survey(ch, env, peer, script)
                validate_environment(env, req)
                for name in GATE_VARS:                           # clear every output first
                    ch.set_var(name, "")
                key = ch.get_global("COLLECTIONS_AGI_KEY") or ""
                if not hmac.compare_digest(key.encode(), self.secret.encode()):
                    raise AgiDeny("agi_key_mismatch")
                req.attempt_id = (ch.get_var("COLLECTIONS_ATTEMPT") or "").strip().lower()
                req.token = (ch.get_var("COLLECTIONS_TOKEN") or "").strip().lower()
                req.epoch_uuid = (ch.get_global("COLLECTIONS_HUMAN_PBX_EPOCH") or "").strip().lower() or None
                try:
                    decision = authorize(db.session, req)
                    db.session.commit()                       # durable BEFORE anything is published
                except AgiDeny:
                    db.session.rollback()
                    raise
                except Exception:
                    db.session.rollback()
                    raise AgiDeny("agi_internal_error") from None
                if time.monotonic() > ch.deadline:
                    raise AgiDeny("agi_timeout")
                # the epoch must not have changed while we were deciding
                if (ch.get_global("COLLECTIONS_HUMAN_PBX_EPOCH") or "").strip().lower() != (req.epoch_uuid or ""):
                    raise AgiDeny("epoch_changed")
                ch.set_var("COLLECTIONS_GATE_DESTINATION", decision.destination)
                ch.set_var("COLLECTIONS_GATE_EXTENSION", decision.extension)
                ch.set_var("COLLECTIONS_GATE_LIMIT", str(decision.max_call_seconds))
                ch.set_var("COLLECTIONS_GATE_EXPIRES", str(decision.expires_epoch))
                if decision.expires_epoch <= time.time():
                    raise AgiDeny("token_expired")
                ch.set_var("COLLECTIONS_GATE", "ALLOW")          # last
                self.stats["allow"] += 1
                log.info("AGI allow attempt=%s peer=%s", req.attempt_id, peer)
                return decision
            except AgiDeny as deny:
                self.stats["deny"] += 1
                # After a committed allow the token stays consumed (never granted twice); the
                # failure to publish it is recorded as its own deny row.
                reason = deny.reason if decision is None else "allow_not_published"
                try:
                    record_decision(db.session, "deny", reason, req, token_id=deny.ctx.get("token_id"))
                    db.session.commit()
                except Exception:  # noqa: BLE001
                    db.session.rollback()
                    log.exception("could not record an AGI deny")
                log.info("AGI deny reason=%s attempt=%s peer=%s", deny.reason,
                         req.attempt_id if req.attempt_id and UUID_RE.match(req.attempt_id) else None, peer)
                return Decision(False, deny.reason)
            finally:
                db.session.remove()

    def _survey(self, ch: AgiChannel, env: dict, peer: str, script: str) -> Decision:
        """collections-survey-offer / -result: same peer allowlist and shared key as the gate."""
        from extensions import db
        from services.calling import survey
        try:
            if env.get("agi_network") != "yes":
                raise AgiDeny("not_fastagi")
            channel_id = (env.get("agi_uniqueid") or "").strip()
            if script == survey.OFFER_SCRIPT:
                ch.set_var("COLLECTIONS_SURVEY", "0")
                ch.set_var("COLLECTIONS_SURVEY_ID", "")
            key = ch.get_global("COLLECTIONS_AGI_KEY") or ""
            if not hmac.compare_digest(key.encode(), self.secret.encode()):
                raise AgiDeny("agi_key_mismatch")
            if script == survey.OFFER_SCRIPT:
                sid, reason = survey.offer(
                    db.session, channel_id=channel_id,
                    attempt_id=(ch.get_var("COLLECTIONS_ATTEMPT") or "").strip().lower() or None,
                    caller=(ch.get_var("CAWP_SURVEY_CALLER") or "").strip() or None,
                    agent_extension=(ch.get_var("CAWP_SURVEY_AGENT") or "").strip() or None,
                    language=ch.get_var("CAWP_LANG"))
                db.session.commit()
                if sid:
                    ch.set_var("COLLECTIONS_SURVEY_ID", sid)
                    ch.set_var("COLLECTIONS_SURVEY", "1")
                log.info("AGI survey offer reason=%s peer=%s", reason, peer)
                return Decision(bool(sid), reason)
            status = survey.record_result(db.session, channel_id=channel_id,
                                          survey_id=(ch.get_var("COLLECTIONS_SURVEY_ID") or "").strip().lower() or None,
                                          digits=ch.get_var("CAWP_SURVEY_SCORE"))
            db.session.commit()
            log.info("AGI survey result status=%s peer=%s", status, peer)
            return Decision(status in ("answered", "no_input", "invalid"), status)
        except AgiDeny as deny:
            db.session.rollback()
            log.info("AGI survey deny reason=%s peer=%s", deny.reason, peer)
            return Decision(False, deny.reason)
        except Exception:  # noqa: BLE001 - never let a survey break the call
            db.session.rollback()
            log.exception("AGI survey failed")
            return Decision(False, "agi_internal_error")
