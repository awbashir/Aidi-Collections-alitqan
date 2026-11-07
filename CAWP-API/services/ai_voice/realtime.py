"""The OpenAI Realtime side of an AI test call (0092): one server-side WebSocket per call, opened with
the short-lived client secret the gateway minted (approved policy, quota, audit row); the permanent
key is never used here.

Audio is G.711 µ-law both ways (`audio/pcmu`), exactly what the PBX sends, so nothing is transcoded.
Server VAD decides the turns; when the client starts speaking the agent's pending audio is dropped
(barge-in). The model can end the call with the `end_call` tool.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
from urllib.parse import quote

log = logging.getLogger("cawp.ai_voice.realtime")

END_CALL_TOOL = {
    "type": "function", "name": "end_call",
    "description": "End the phone call after saying a short goodbye, when the conversation is over or the person asks to stop.",
    "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
}


# what OpenAI says when it refuses the connection (WebSocket close reason) -> our reason code
CLOSE_REASONS = (("insufficient_quota", "ai_credit_exhausted"), ("rate_limit", "ai_rate_limited"),
                 ("invalid_api_key", "ai_key_rejected"), ("401", "ai_key_rejected"))


def connect_failure_code(exc: BaseException) -> str:
    """A refused or dropped Realtime connection -> a reason code the page can explain."""
    rcvd = getattr(exc, "rcvd", None)
    text_ = f"{getattr(rcvd, 'reason', '') or ''} {exc}".lower()
    return next((code for needle, code in CLOSE_REASONS if needle in text_), "realtime_connect_failed")


def ws_url(base_url: str, model: str) -> str:
    """https://api.openai.com/v1 -> wss://api.openai.com/v1/realtime?model=<model> (http -> ws for tests)."""
    base = base_url.rstrip("/")
    if base.startswith("https://"):
        base = "wss://" + base[len("https://"):]
    elif base.startswith("http://"):
        base = "ws://" + base[len("http://"):]
    return f"{base}/realtime?model={quote(model, safe='')}"


def session_update(*, instructions: str, voice: str, transcribe_model: str | None) -> dict:
    audio_in = {"format": {"type": "audio/pcmu"},
                "turn_detection": {"type": "server_vad", "silence_duration_ms": 600, "prefix_padding_ms": 300}}
    if transcribe_model:
        audio_in["transcription"] = {"model": transcribe_model, "language": "ar"}
    return {"type": "session.update", "session": {
        "type": "realtime", "instructions": instructions, "tools": [END_CALL_TOOL], "tool_choice": "auto",
        "audio": {"input": audio_in, "output": {"format": {"type": "audio/pcmu"}, "voice": voice}}}}


def live_url(base_url: str) -> str:
    """https://api.openai.com/v1 -> wss://api.openai.com/v1/live/sessions (http -> ws for tests)."""
    base = base_url.rstrip("/")
    if base.startswith("https://"):
        base = "wss://" + base[len("https://"):]
    elif base.startswith("http://"):
        base = "ws://" + base[len("http://"):]
    return f"{base}/live/sessions"


class LiveLink:
    """OpenAI's Live API (gpt-live-1, 2026-11-05): a full-duplex voice model on its own WebSocket
    (`session.start`, `session.input_audio.append`, `session.output_audio.delta`, transcript deltas,
    `session.close`). G.711 µ-law 8 kHz both ways, like the Realtime link. Differences the call
    sees: the model takes turns and interruptions itself (no speech-started event, so queued audio
    is not dropped), there is no end_call tool, and transcripts arrive as deltas: a side's text becomes
    one line once that side pauses (PAUSE seconds without a delta), so short overlaps where both
    talk at once do not split words. Same callbacks as RealtimeLink."""

    PAUSE = 0.9

    def __init__(self, *, url: str, api_key: str, model: str, instructions: str, voice: str,
                 on_audio, on_barge, on_transcript, on_end_call, on_error, connect=None, **_ignored):
        self.url, self.api_key, self.model = url, api_key, model
        self.instructions, self.voice = instructions, voice
        self.on_audio, self.on_transcript, self.on_error = on_audio, on_transcript, on_error
        self._connect = connect
        self.ws = None
        self._reader = None
        self.closed = asyncio.Event()
        self.started = asyncio.Event()
        self._text = {"client": "", "agent": ""}
        self._last = {"client": 0.0, "agent": 0.0}
        self._flusher = None
        self._closing = False

    async def open(self, timeout: float = 10.0):
        connect = self._connect
        if connect is None:
            from websockets.asyncio.client import connect as ws_connect
            connect = ws_connect
        self.ws = await asyncio.wait_for(
            connect(self.url, additional_headers={"Authorization": f"Bearer {self.api_key}"}, max_size=2 ** 22,
                    open_timeout=timeout), timeout + 1)
        await self.send({"type": "session.start", "session": {
            "model": self.model, "instructions": self.instructions,
            "audio": {"format": {"type": "audio/pcmu", "rate": 8000}, "output": {"voice": self.voice}}}})
        self._reader = asyncio.create_task(self._read())
        self._flusher = asyncio.create_task(self._flush_paused())
        done = asyncio.ensure_future(self.started.wait())
        gone = asyncio.ensure_future(self.closed.wait())
        try:
            await asyncio.wait_for(asyncio.wait({done, gone}, return_when=asyncio.FIRST_COMPLETED), timeout)
        finally:
            done.cancel()
            gone.cancel()
        if not self.started.is_set():
            raise ConnectionError(getattr(self, "_close_reason", None) or "live_session_not_started")

    async def send(self, event: dict):
        if self.ws is not None and not self.closed.is_set():
            await self.ws.send(json.dumps(event, ensure_ascii=False))

    async def append_audio(self, ulaw: bytes):
        if ulaw:
            await self.send({"type": "session.input_audio.append", "audio": base64.b64encode(ulaw).decode()})

    async def greet(self):
        """The person just answered: ask the model to open the conversation (Live API greeting:
        `session.commentary.append` with an event_id and `content`; verified 2026-11-05 that this one makes
        the model speak while audio streams, `session.instructions.append` only adds context)."""
        self._seq = getattr(self, "_seq", 0) + 1
        await self.send({"type": "session.commentary.append", "event_id": f"greeting_{self._seq}", "delegation_id": None,
                         "content": "The person just answered the phone. Greet them now in Arabic and introduce "
                                    "yourself as your instructions say, then pause and listen."})

    def _flush(self, role: str):
        txt, self._text[role] = self._text[role].strip(), ""
        if txt:
            self.on_transcript(role, txt)

    def _add(self, role: str, delta: str):
        other = "agent" if role == "client" else "client"
        now = asyncio.get_running_loop().time()
        if self._text[other] and now - self._last[other] >= self.PAUSE:
            self._flush(other)                  # keeps the order of turns when the other side had paused
        self._text[role] += delta
        self._last[role] = now

    async def _flush_paused(self):
        while not self.closed.is_set():
            await asyncio.sleep(0.3)
            now = asyncio.get_running_loop().time()
            for role in sorted(self._text, key=lambda r: self._last[r]):
                if self._text[role] and now - self._last[role] >= self.PAUSE:
                    self._flush(role)

    async def _read(self):
        try:
            async for raw in self.ws:
                try:
                    ev = json.loads(raw)
                except (TypeError, ValueError):
                    continue
                t = ev.get("type") or ""
                if t == "session.started":
                    self.started.set()
                elif t == "session.output_audio.delta":
                    try:
                        self.on_audio(base64.b64decode(ev.get("delta") or ""))
                    except (ValueError, TypeError):
                        pass
                elif t == "session.input_transcript.delta":
                    self._add("client", ev.get("delta") or "")
                elif t == "session.output_transcript.delta":
                    self._add("agent", ev.get("delta") or "")
                elif t == "error":
                    err = ev.get("error") or {}
                    code = str(err.get("code") or err.get("type") or "live_error")
                    log.warning("live error: %s", code)
                    self.on_error(code)
        except Exception as e:  # noqa: BLE001 - the socket closing ends the link
            self._close_reason = f"{getattr(getattr(e, 'rcvd', None), 'reason', '') or ''} {e}"
            if not self.closed.is_set() and not self._closing:
                log.info("live link closed: %s", type(e).__name__)
        finally:
            for role in sorted(self._text, key=lambda r: self._last[r]):
                self._flush(role)
            self.closed.set()

    async def close(self):
        self._closing = True
        if self.ws is not None and not self.closed.is_set():
            try:
                await self.send({"type": "session.close"})
                await asyncio.wait_for(self.closed.wait(), 5)
            except Exception:  # noqa: BLE001
                pass
        self.closed.set()
        if self.ws is not None:
            try:
                await asyncio.wait_for(self.ws.close(), 3)
            except Exception:  # noqa: BLE001
                pass
        for task in (self._reader, self._flusher):
            if task is not None:
                task.cancel()


class RealtimeLink:
    """Callbacks (all called on the event loop): on_audio(bytes), on_barge(), on_transcript(role, text),
    on_end_call(), on_error(code)."""

    def __init__(self, *, url: str, secret: str, instructions: str, voice: str, transcribe_model: str | None,
                 on_audio, on_barge, on_transcript, on_end_call, on_error, connect=None):
        self.url, self.secret = url, secret
        self.instructions, self.voice, self.transcribe_model = instructions, voice, transcribe_model
        self.on_audio, self.on_barge, self.on_transcript = on_audio, on_barge, on_transcript
        self.on_end_call, self.on_error = on_end_call, on_error
        self._connect = connect
        self.ws = None
        self._reader = None
        self.closed = asyncio.Event()

    async def open(self, timeout: float = 10.0):
        connect = self._connect
        if connect is None:
            from websockets.asyncio.client import connect as ws_connect
            connect = ws_connect
        self.ws = await asyncio.wait_for(
            connect(self.url, additional_headers={"Authorization": f"Bearer {self.secret}"}, max_size=2 ** 22,
                    open_timeout=timeout), timeout + 1)
        await self.send(session_update(instructions=self.instructions, voice=self.voice,
                                       transcribe_model=self.transcribe_model))
        self._reader = asyncio.create_task(self._read())

    async def send(self, event: dict):
        if self.ws is not None and not self.closed.is_set():
            await self.ws.send(json.dumps(event, ensure_ascii=False))

    async def append_audio(self, ulaw: bytes):
        if ulaw:
            await self.send({"type": "input_audio_buffer.append", "audio": base64.b64encode(ulaw).decode()})

    async def greet(self):
        """The agent speaks first (the person just answered)."""
        await self.send({"type": "response.create"})

    async def _read(self):
        try:
            async for raw in self.ws:
                try:
                    ev = json.loads(raw)
                except (TypeError, ValueError):
                    continue
                self._dispatch(ev)
        except Exception as e:  # noqa: BLE001 - the socket closing ends the link
            if not self.closed.is_set():
                log.info("realtime link closed: %s", type(e).__name__)
        finally:
            self.closed.set()

    def _dispatch(self, ev: dict):
        t = ev.get("type") or ""
        if t in ("response.output_audio.delta", "response.audio.delta"):
            try:
                self.on_audio(base64.b64decode(ev.get("delta") or ""))
            except (ValueError, TypeError):
                pass
        elif t == "input_audio_buffer.speech_started":
            self.on_barge()
        elif t == "conversation.item.input_audio_transcription.completed":
            self.on_transcript("client", ev.get("transcript") or "")
        elif t in ("response.output_audio_transcript.done", "response.audio_transcript.done"):
            self.on_transcript("agent", ev.get("transcript") or "")
        elif t == "response.done":
            for item in ((ev.get("response") or {}).get("output") or []):
                if item.get("type") == "function_call" and item.get("name") == "end_call":
                    self.on_end_call()
        elif t == "error":
            err = ev.get("error") or {}
            log.warning("realtime error: %s", err.get("code") or err.get("type"))
            self.on_error(str(err.get("code") or err.get("type") or "realtime_error"))

    async def close(self):
        self.closed.set()
        if self.ws is not None:
            try:
                await asyncio.wait_for(self.ws.close(), 3)
            except Exception:  # noqa: BLE001
                pass
        if self._reader is not None:
            self._reader.cancel()
