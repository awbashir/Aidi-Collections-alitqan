"""One AI test call on the PBX (0092): the SIP dialog, the RTP stream and the Realtime link.

The app server starts everything (the PBX cannot open a connection to it): the INVITE and our RTP
leave from the ports the PBX answers to, so the network lets the replies back. RTP is sent from the
moment the PBX's media address is known (silence until the AI speaks), which also keeps the path
open. Every state change is written through the `db` callbacks (run in a worker thread).
"""
from __future__ import annotations

import asyncio
import logging
import os
import random
import struct
import time
from dataclasses import dataclass

from services.ai_voice import sip

log = logging.getLogger("cawp.ai_voice.call")

FRAME = 160                       # 20 ms of 8 kHz µ-law
SILENCE = b"\xff" * FRAME
APPEND_EVERY = 5                  # send the client's audio to the AI every 100 ms
RESULT_BY_STATUS = {486: "busy", 600: "busy", 480: "no_answer", 408: "no_answer", 487: "cancelled",
                    503: "congestion", 500: "congestion", 404: "unavailable", 403: "denied", 603: "denied"}


@dataclass
class VoiceConfig:
    pbx_host: str
    pbx_port: int
    local_ip: str
    sip_port: int
    sip_user: str
    sip_secret: str
    rtp_min: int
    rtp_max: int
    transcribe_model: str | None
    max_seconds: int
    ring_timeout: int = 75

    @classmethod
    def from_env(cls, env=None) -> "VoiceConfig":
        e = os.environ if env is None else env

        def i(name, default, lo, hi):
            try:
                return min(max(int(e.get(name, default)), lo), hi)
            except (TypeError, ValueError):
                return default
        from services.ai.test_calls import limits
        return cls(pbx_host=(e.get("AI_VOICE_PBX_HOST") or "").strip(), pbx_port=i("AI_VOICE_PBX_PORT", 5060, 1, 65535),
                   local_ip=(e.get("AI_VOICE_LOCAL_IP") or "").strip(), sip_port=i("AI_VOICE_SIP_PORT", 5070, 1024, 65535),
                   sip_user=(e.get("AI_VOICE_SIP_USER") or "").strip(), sip_secret=e.get("AI_VOICE_SIP_SECRET") or "",
                   rtp_min=i("AI_VOICE_RTP_MIN", 40000, 1024, 65000), rtp_max=i("AI_VOICE_RTP_MAX", 40199, 1024, 65535),
                   transcribe_model=(e.get("AI_VOICE_TRANSCRIBE_MODEL", "gpt-4o-mini-transcribe") or "").strip() or None,
                   max_seconds=limits()["max_seconds"])

    def missing(self) -> list:
        return [n for n, v in (("AI_VOICE_PBX_HOST", self.pbx_host), ("AI_VOICE_LOCAL_IP", self.local_ip),
                               ("AI_VOICE_SIP_USER", self.sip_user), ("AI_VOICE_SIP_SECRET", self.sip_secret)) if not v]


# --------------------------------------------------------------------------- SIP transport (shared)
class SipTransport(asyncio.DatagramProtocol):
    """One UDP socket for every dialog; messages are routed by Call-ID."""

    def __init__(self, cfg: VoiceConfig):
        self.cfg = cfg
        self.transport = None
        self.dialogs: dict = {}

    def connection_made(self, transport):
        self.transport = transport

    def send(self, msg: sip.Message):
        if self.transport is not None:
            self.transport.sendto(msg.to_bytes(), (self.cfg.pbx_host, self.cfg.pbx_port))

    def datagram_received(self, data, addr):
        msg = sip.parse(data)
        if msg is None:
            return
        d = self.dialogs.get(msg.get("Call-ID") or "")
        if d is not None:
            d.on_message(msg)
        elif not msg.is_response:
            if msg.method == "OPTIONS":
                self.send(sip.response_to(msg, 200, "OK", to_tag=sip.rand(4)))
            elif msg.method not in ("ACK",):
                self.send(sip.response_to(msg, 481, "Call/Transaction Does Not Exist", to_tag=sip.rand(4)))

    def keepalive(self):
        """Out-of-dialog OPTIONS: keeps the network's UDP path from the PBX to us open."""
        call_id = f"ka-{sip.rand(8)}@{self.cfg.local_ip}"
        self.send(sip.Message(f"OPTIONS sip:{self.cfg.pbx_host}:{self.cfg.pbx_port} SIP/2.0", [
            ("Via", f"SIP/2.0/UDP {self.cfg.local_ip}:{self.cfg.sip_port};branch=z9hG4bK{sip.rand(8)};rport"),
            ("Max-Forwards", "70"), ("From", f"<sip:{self.cfg.sip_user}@{self.cfg.pbx_host}>;tag={sip.rand(4)}"),
            ("To", f"<sip:{self.cfg.pbx_host}>"), ("Call-ID", call_id), ("CSeq", "1 OPTIONS"),
            ("User-Agent", "cawp-ai-voice")]))


# --------------------------------------------------------------------------- RTP
class RtpEndpoint(asyncio.DatagramProtocol):
    def __init__(self, on_audio):
        self.on_audio = on_audio
        self.transport = None
        self.remote = None
        self.out = bytearray()
        self.seq, self.ts, self.ssrc = random.randint(0, 65535), random.randint(0, 2 ** 31), random.randint(1, 2 ** 31)
        self.first = True
        self.received = 0
        self._sender = None

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        if len(data) < 12 or (data[0] >> 6) != 2:
            return
        pt = data[1] & 0x7F
        if pt != 0:                                  # PCMU only (telephone-event and others ignored)
            return
        cc = data[0] & 0x0F
        off = 12 + 4 * cc
        if data[0] & 0x10 and len(data) >= off + 4:  # header extension
            off += 4 + 4 * struct.unpack("!H", data[off + 2:off + 4])[0]
        if len(data) > off:
            self.received += 1
            self.on_audio(bytes(data[off:]))

    def start(self, remote):
        self.remote = remote
        if self._sender is None:
            self._sender = asyncio.create_task(self._send_loop())

    def queue(self, ulaw: bytes):
        self.out.extend(ulaw)

    def clear(self):
        self.out.clear()

    @property
    def playing(self) -> bool:
        return len(self.out) > 0

    async def _send_loop(self):
        loop = asyncio.get_running_loop()
        next_at = loop.time()
        while True:
            chunk = bytes(self.out[:FRAME]) if self.out else SILENCE
            del self.out[:FRAME]
            if len(chunk) < FRAME:
                chunk += b"\xff" * (FRAME - len(chunk))
            hdr = struct.pack("!BBHII", 0x80, (0x80 if self.first else 0) | 0, self.seq & 0xFFFF,
                              self.ts & 0xFFFFFFFF, self.ssrc)
            self.first = False
            if self.transport is not None and self.remote:
                self.transport.sendto(hdr + chunk, self.remote)
            self.seq += 1
            self.ts += FRAME
            next_at += 0.02
            await asyncio.sleep(max(0.0, next_at - loop.time()))

    def close(self):
        if self._sender is not None:
            self._sender.cancel()
        if self.transport is not None:
            self.transport.close()


# --------------------------------------------------------------------------- one call
class AiCall:
    """`db` = object with sync methods (run in a thread): mint(row) -> (secret, model, ai_call_id),
    issue_token(call_id) -> token, mark(call_id, **kw), transcript(call_id, role, text),
    stop_requested(call_id) -> bool. `realtime_factory(**kw)` -> a RealtimeLink-like object."""

    def __init__(self, row: dict, cfg: VoiceConfig, transport: SipTransport, db, realtime_factory, rtp_port: int):
        self.row, self.cfg, self.sip, self.db = row, cfg, transport, db
        self.realtime_factory = realtime_factory
        self.rtp_port = rtp_port
        self.call_id = str(row["id"])
        self.sip_call_id = f"{sip.rand(12)}@{cfg.local_ip}"
        self.local_tag = sip.rand(6)
        self.cseq = 1
        self.branch = None
        self.invite = None
        self.remote_tag = None
        self.remote_target = None
        self.final = asyncio.Event()
        self.provisional = asyncio.Event()
        self.answered = False
        self.final_status = None
        self.ended = asyncio.Event()
        self.bye_from_pbx = False
        self.end_requested = False
        self.ai_error = None
        self.link = None
        self.rtp = None
        self._in_buf = bytearray()
        self._in_frames = 0
        self.auth_tried = False
        self._challenge = None
        self._bye_answered = asyncio.Event()
        self.shutdown = False                      # the service is stopping: end the call now

    # ------------------------------------------------------------- helpers
    async def _db(self, fn, *a, **kw):
        return await asyncio.to_thread(fn, *a, **kw)

    def _uri(self) -> str:
        return f"sip:cawp-ai-agent@{self.cfg.pbx_host}:{self.cfg.pbx_port}"

    def _base_headers(self, method: str, branch: str, cseq: int, to_tag: str | None = None) -> list:
        to = f"<sip:cawp-ai-agent@{self.cfg.pbx_host}>" + (f";tag={to_tag}" if to_tag else "")
        return [("Via", f"SIP/2.0/UDP {self.cfg.local_ip}:{self.cfg.sip_port};branch={branch};rport"),
                ("Max-Forwards", "70"),
                ("From", f'"AI test" <sip:{self.cfg.sip_user}@{self.cfg.pbx_host}>;tag={self.local_tag}'),
                ("To", to), ("Call-ID", self.sip_call_id), ("CSeq", f"{cseq} {method}"),
                ("User-Agent", "cawp-ai-voice")]

    def _sdp(self) -> str:
        return sip.sdp_offer(self.cfg.local_ip, self.rtp_port, int(time.time()))

    def _build_invite(self, token: str, auth: tuple[str, str] | None = None) -> sip.Message:
        self.branch = f"z9hG4bK{sip.rand(10)}"
        heads = self._base_headers("INVITE", self.branch, self.cseq) + [
            ("Contact", f"<sip:{self.cfg.sip_user}@{self.cfg.local_ip}:{self.cfg.sip_port}>"),
            ("Allow", "INVITE, ACK, CANCEL, BYE, OPTIONS, UPDATE, INFO"),
            ("X-CAWP-Call", self.call_id), ("X-CAWP-Token", token), ("Content-Type", "application/sdp")]
        if auth:
            heads.append(auth)
        return sip.Message(f"INVITE {self._uri()} SIP/2.0", heads, self._sdp())

    def _ack_for(self, resp: sip.Message, *, two_xx: bool) -> sip.Message:
        cseq_n = resp.cseq[0]
        if two_xx:
            branch, uri = f"z9hG4bK{sip.rand(10)}", self.remote_target or self._uri()
        else:
            branch, uri = self.branch, self._uri()
        return sip.Message(f"ACK {uri} SIP/2.0",
                           self._base_headers("ACK", branch, cseq_n, sip.tag_of(resp.get("To"))))

    # ------------------------------------------------------------- incoming SIP
    def on_message(self, msg: sip.Message):
        if msg.is_response:
            self._on_response(msg)
        else:
            self._on_request(msg)

    def _on_response(self, resp: sip.Message):
        n, method = resp.cseq
        st = resp.status or 0
        if method == "INVITE":
            if st < 200:
                self.provisional.set()
                if st in (180, 183):
                    self._remote_media(resp)
                    asyncio.get_running_loop().create_task(self._mark(state="ringing"))
                return
            if 200 <= st < 300:
                self.remote_tag = sip.tag_of(resp.get("To"))
                self.remote_target = sip.uri_of(resp.get("Contact")) or self._uri()
                self.sip.send(self._ack_for(resp, two_xx=True))      # also answers a retransmitted 200
                if not self.answered:
                    self.answered = True
                    self._remote_media(resp)
                    self.final_status = st
                    self.final.set()
                return
            self.sip.send(self._ack_for(resp, two_xx=False))
            if st in (401, 407) and not self.auth_tried and n == self.cseq:
                self._challenge = resp
                self.final_status = st
                self.final.set()
                return
            if self.final_status is None or self.final_status in (401, 407):
                self.final_status = st
            self.final.set()
        elif method == "BYE":
            self._bye_answered.set()

    def _on_request(self, req: sip.Message):
        m = req.method
        if m == "BYE":
            self.sip.send(sip.response_to(req, 200, "OK"))
            self.bye_from_pbx = True
            self.ended.set()
        elif m in ("INVITE", "UPDATE"):
            self.sip.send(sip.response_to(req, 200, "OK", extra=[
                ("Contact", f"<sip:{self.cfg.sip_user}@{self.cfg.local_ip}:{self.cfg.sip_port}>"),
                ("Content-Type", "application/sdp")], body=self._sdp()))
        elif m == "ACK":
            return
        else:
            self.sip.send(sip.response_to(req, 200, "OK"))

    def _remote_media(self, resp: sip.Message):
        media = sip.sdp_remote(resp.body)
        if media and self.rtp is not None:
            self.rtp.start((media[0], media[1]))

    # ------------------------------------------------------------- AI callbacks
    def _ai_audio(self, ulaw: bytes):
        if self.rtp is not None and self.answered:
            self.rtp.queue(ulaw)

    def _ai_barge(self):
        if self.rtp is not None:
            self.rtp.clear()

    def _ai_transcript(self, role: str, text_: str):
        if text_.strip():
            asyncio.get_running_loop().create_task(self._db(self.db.transcript, self.call_id, role, text_))

    def _ai_end_call(self):
        self.end_requested = True

    def _ai_error(self, code: str):
        self.ai_error = code

    def _client_audio(self, ulaw: bytes):
        if not self.answered or self.link is None:
            return
        self._in_buf.extend(ulaw)
        self._in_frames += 1
        if self._in_frames >= APPEND_EVERY:
            data, self._in_frames = bytes(self._in_buf), 0
            self._in_buf.clear()
            asyncio.get_running_loop().create_task(self.link.append_audio(data))

    async def _mark(self, **kw):
        try:
            await self._db(self.db.mark, self.call_id, **kw)
        except Exception:  # noqa: BLE001
            log.exception("ai call %s: mark failed", self.call_id)

    # ------------------------------------------------------------- the call
    async def run(self):
        loop = asyncio.get_running_loop()
        self.sip.dialogs[self.sip_call_id] = self
        try:
            await self._run(loop)
        except Exception:  # noqa: BLE001 - any failure ends the row honestly
            log.exception("ai call %s failed", self.call_id)
            await self._mark(state="failed", result="sip_failed", reason="service_error", ended=True)
        finally:
            self.sip.dialogs.pop(self.sip_call_id, None)
            if self.rtp is not None:
                self.rtp.close()
            if self.link is not None:
                await self.link.close()

    async def _run(self, loop):
        row = self.row
        try:
            secret, model, ai_call_id = await self._db(self.db.mint, row)
        except Exception as e:  # noqa: BLE001 - AIRefused / provider failure
            code = getattr(e, "code", None) or type(e).__name__
            refused = "refused" in type(e).__name__.lower() or code in ("ai_refused", "ai_not_configured")
            await self._mark(state="failed", result="ai_refused" if refused else "ai_failed",
                             reason=str(code)[:60].lower() if str(code).replace("_", "").isalnum() else "ai_failed",
                             ended=True)
            return
        await self._mark(model=model, ai_call_id=ai_call_id)
        from services.ai.test_calls import full_instructions
        self.link = self.realtime_factory(secret=secret, model=model, instructions=full_instructions(row["instructions"]),
                                          voice=row["voice"], transcribe_model=self.cfg.transcribe_model,
                                          on_audio=self._ai_audio, on_barge=self._ai_barge,
                                          on_transcript=self._ai_transcript, on_end_call=self._ai_end_call,
                                          on_error=self._ai_error)
        try:
            await self.link.open()
        except Exception:  # noqa: BLE001
            log.exception("ai call %s: realtime connect failed", self.call_id)
            await self._mark(state="failed", result="ai_failed", reason="realtime_connect_failed", ended=True)
            return
        _t, self.rtp = await loop.create_datagram_endpoint(lambda: RtpEndpoint(self._client_audio),
                                                           local_addr=(self.cfg.local_ip, self.rtp_port))
        token = await self._db(self.db.issue_token, self.call_id)
        status = await self._invite(token)
        if status in (401, 407):
            self.auth_tried = True
            ch = self._challenge
            hdr = "Proxy-Authenticate" if status == 407 else "WWW-Authenticate"
            chal = sip.parse_challenge(ch.get(hdr) or "")
            auth = sip.digest(method="INVITE", uri=self._uri(), user=self.cfg.sip_user, password=self.cfg.sip_secret,
                              challenge=chal)
            self.cseq += 1
            self.final.clear()
            self.provisional.clear()
            self.final_status = None
            status = await self._invite(token, ("Proxy-Authorization" if status == 407 else "Authorization", auth))
        if status is None or not (200 <= status < 300):
            result = "cancelled" if status == 487 or await self._db(self.db.stop_requested, self.call_id) else \
                RESULT_BY_STATUS.get(status or 0, "sip_failed" if status else "no_answer")
            await self._mark(state="ended" if result in ("busy", "no_answer", "cancelled") else "failed",
                             result=result, sip_status=status, ended=True)
            return
        await self._mark(state="answered", answered=True, sip_status=status)
        await self.link.greet()
        await self._talk()

    async def _invite(self, token: str, auth=None) -> int | None:
        """Send the INVITE (retransmitted until any answer) and wait for its final status; CANCEL when
        stop is pressed or nobody answers in ring_timeout."""
        self.invite = self._build_invite(token, auth)
        started = time.monotonic()
        deadline = started + self.cfg.ring_timeout
        wait = 0.5
        while not self.provisional.is_set() and not self.final.is_set():
            self.sip.send(self.invite)
            try:
                await asyncio.wait_for(self._first_answer(), wait)
            except asyncio.TimeoutError:
                wait = min(wait * 2, 4.0)
                if time.monotonic() - started > 32:      # RFC 3261 timer B: the PBX never answered
                    return None
        cancelled = False
        while not self.final.is_set():
            try:
                await asyncio.wait_for(self.final.wait(), 1.0)
            except asyncio.TimeoutError:
                if not cancelled and (time.monotonic() > deadline or await self._db(self.db.stop_requested, self.call_id)):
                    cancelled = True
                    self.sip.send(sip.Message(f"CANCEL {self._uri()} SIP/2.0",
                                              self._base_headers("CANCEL", self.branch, self.cseq)))
                if time.monotonic() > deadline + 10:
                    return 487 if cancelled else None
        return self.final_status

    async def _first_answer(self):
        done = asyncio.ensure_future(self.provisional.wait())
        fin = asyncio.ensure_future(self.final.wait())
        try:
            await asyncio.wait({done, fin}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            done.cancel()
            fin.cancel()

    async def _talk(self):
        started = time.monotonic()
        result = "completed"
        while not self.ended.is_set():
            try:
                await asyncio.wait_for(self.ended.wait(), 0.5)
                break
            except asyncio.TimeoutError:
                pass
            if self.end_requested and not self.rtp.playing:
                await asyncio.sleep(0.6)                 # the goodbye's last syllable
                break
            if time.monotonic() - started > self.cfg.max_seconds:
                result = "time_limit"
                break
            if self.shutdown:
                result = "service_stopped"
                break
            if self.link.closed.is_set() and self.ai_error:
                result = "completed"
                break
            if await self._db(self.db.stop_requested, self.call_id):
                break
        if not self.bye_from_pbx:
            await self._bye()
        await self._mark(state="ended", result=result, ended=True)

    async def _bye(self):
        self.cseq += 1
        bye = sip.Message(f"BYE {self.remote_target or self._uri()} SIP/2.0",
                          self._base_headers("BYE", f"z9hG4bK{sip.rand(10)}", self.cseq, self.remote_tag))
        wait = 0.5
        for _ in range(6):
            self.sip.send(bye)
            try:
                await asyncio.wait_for(self._bye_answered.wait(), wait)
                return
            except asyncio.TimeoutError:
                wait = min(wait * 2, 4.0)
