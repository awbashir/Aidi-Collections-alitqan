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
