"""OpenAI implementation of the provider interface.

Documented APIs used (built against the public API reference; not verified live from this
environment, which has no key):

* **Structured generation**: `POST {base}/responses` with
  `{"model", "store": false, "instructions", "input": [{"role", "content"}...],
    "text": {"format": {"type": "json_schema", "name", "schema", "strict": true}},
    "max_output_tokens"}`.
  `store: false` is always sent, so the response is not retained for later retrieval. The
  answer's `output[].content[]` items of type `output_text` hold the JSON; a `refusal` item means
  the model refused; `status: "incomplete"` (e.g. max_output_tokens) is an error, never a partial
  result. `usage.input_tokens / output_tokens / total_tokens` feed the audit.
* **Transcription**: `POST {base}/audio/transcriptions`, multipart `file`, `model`,
  `response_format` (`diarized_json` when diarization is asked for and the model is a diarizing
  one, e.g. `gpt-4o-transcribe-diarize`; `json` otherwise), `chunking_strategy=auto` for
  diarization, optional `language`. The file is always sent as `audio.<ext>`, never the original
  filename. Speakers stay the provider's anonymous labels.
* **Realtime**: `POST {base}/realtime/client_secrets` with
  `{"expires_after": {"anchor": "created_at", "seconds"}, "session": {"type": "realtime",
    "model", "instructions", "audio": {"output": {"voice"}}}}` -> `{"value", "expires_at",
  "session"}`. The ephemeral `value` is returned to the caller for the browser only.

Retries: a 429 or 5xx answer is retried up to AI_MAX_RETRIES times with exponential backoff
(Retry-After honoured, capped), except `insufficient_quota`. Timeouts and connection failures are
not retried.
"""
from __future__ import annotations

import json
import logging
import math
import os
import random
import re
import time
import uuid
from urllib.parse import urlsplit

from services.ai import schema as schema_mod
from services.ai.config import GENERATE_STRUCTURED, REALTIME_SESSION, TRANSCRIBE, AIConfig, _is_loopback
from services.ai.provider import (AIProvider, CallMeta, Message, ProviderError, RealtimeSession, Segment,
                                  StructuredResult, TranscriptionResult, Usage)
from services.ai.transport import HttpRequest, HttpResponse, TransportError, UrllibTransport

log = logging.getLogger("cawp.ai.openai")

_SAFE_TYPE = re.compile(r"^[a-z0-9_.]{1,64}$")
_SCHEMA_NAME = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
_LANG = re.compile(r"^[a-z]{2}$")
_VOICE = re.compile(r"^[a-z0-9_-]{1,32}$")
_AUDIO_EXT = {"audio/wav": "wav", "audio/x-wav": "wav", "audio/wave": "wav", "audio/mpeg": "mp3",
              "audio/mp3": "mp3", "audio/mp4": "m4a", "audio/m4a": "m4a", "audio/x-m4a": "m4a",
              "audio/webm": "webm", "audio/ogg": "ogg", "audio/flac": "flac"}
MAX_RESPONSE_BYTES = 20 * 1024 * 1024


def _safe_type(value) -> str | None:
    if not isinstance(value, str):
        return None
    v = value.strip().lower()
    return v if _SAFE_TYPE.match(v) else None


def _count(value) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _seconds(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        return None
    return float(value)


def is_diarizing_model(model: str) -> bool:
    return "diarize" in (model or "").lower()


class OpenAIProvider(AIProvider):
    name = "openai"

    def __init__(self, config: AIConfig, *, transport=None, sleep=None, rng=None):
        self.cfg = config
        self.transport = transport if transport is not None else UrllibTransport()
        self.sleep = sleep if sleep is not None else time.sleep
        self.rng = rng if rng is not None else random.Random()

    # ------------------------------------------------------------------ HTTP
    def _headers(self, content_type: str) -> dict:
        h = {"Authorization": f"Bearer {self.cfg.api_key}", "Content-Type": content_type,
             "Accept": "application/json", "User-Agent": "cawp-api"}
        if self.cfg.organization:
            h["OpenAI-Organization"] = self.cfg.organization
        if self.cfg.project:
            h["OpenAI-Project"] = self.cfg.project
        return h

    def _guard_tests(self, url: str) -> None:
        """Belt and braces: under pytest the real transport may only reach a loopback host."""
        if os.environ.get("PYTEST_CURRENT_TEST") and isinstance(self.transport, UrllibTransport):
            if not _is_loopback(urlsplit(url).hostname):
                raise RuntimeError("a real AI provider call was attempted under pytest")

    def _wait_seconds(self, attempt: int, resp: HttpResponse) -> float:
        cap = self.cfg.retry_max_wait_seconds
        hinted = None
        for name, scale in (("retry-after-ms", 0.001), ("retry-after", 1.0)):
            raw = resp.header(name)
            if raw:
                try:
                    hinted = float(raw) * scale
                    break
                except ValueError:
                    continue
        if hinted is not None and hinted >= 0:
            return min(cap, hinted)
        base = self.cfg.retry_base_seconds
        return min(cap, base * (2 ** (attempt - 1)) + self.rng.uniform(0, base))

    @staticmethod
    def _error_type(resp: HttpResponse) -> str | None:
        try:
            err = json.loads(resp.body[:65536].decode("utf-8")).get("error") or {}
        except (ValueError, AttributeError, UnicodeDecodeError):
            return None
        if not isinstance(err, dict):
            return None
        return _safe_type(err.get("code")) or _safe_type(err.get("type"))

    def _send(self, capability: str, path: str, content_type: str, body: bytes):
        """-> (response, attempts, latency_ms). Raises ProviderError; retries 429/5xx only."""
        url = self.cfg.base_url + path
        self._guard_tests(url)
        model = self.cfg.model(capability)
        request = HttpRequest("POST", url, self._headers(content_type), body, self.cfg.timeouts[capability])
        started = time.monotonic()
        attempt = 0

        def elapsed():
            return int((time.monotonic() - started) * 1000)

        while True:
            attempt += 1
            try:
                resp = self.transport.send(request)
            except TransportError as e:
                code = "provider_timeout" if e.kind == "timeout" else "provider_connection_failed"
                raise ProviderError(code, attempts=attempt, retryable=True, latency_ms=elapsed(),
                                    model=model) from None
            status = resp.status
            if status == 429 or 500 <= status <= 599:
                etype = self._error_type(resp)
                if status == 429 and etype == "insufficient_quota":
                    raise ProviderError("provider_quota_exceeded", http_status=status, attempts=attempt,
                                        provider_error_type=etype, latency_ms=elapsed(), model=model,
                                        provider_request_id=resp.header("x-request-id"))
                if attempt <= self.cfg.max_retries:
                    wait = self._wait_seconds(attempt, resp)
                    log.warning("AI provider %s answered %s (attempt %s); retrying in %.2fs",
                                capability, status, attempt, wait)
                    self.sleep(wait)
                    continue
                raise ProviderError("provider_rate_limited" if status == 429 else "provider_unavailable",
                                    http_status=status, attempts=attempt, provider_error_type=etype,
                                    retryable=True, latency_ms=elapsed(), model=model,
                                    provider_request_id=resp.header("x-request-id"))
            if not 200 <= status < 300:
                code = "provider_auth_failed" if status in (401, 403) else "provider_rejected"
                raise ProviderError(code, http_status=status, attempts=attempt,
                                    provider_error_type=self._error_type(resp), latency_ms=elapsed(),
                                    model=model, provider_request_id=resp.header("x-request-id"))
            return resp, attempt, elapsed()

    def _json(self, resp: HttpResponse, attempts: int, latency: int, model: str) -> dict:
        def bad():
            return ProviderError("provider_invalid_response", http_status=resp.status, attempts=attempts,
                                 latency_ms=latency, model=model, provider_request_id=resp.header("x-request-id"))
        if len(resp.body) > MAX_RESPONSE_BYTES:
            raise bad()
        try:
            data = json.loads(resp.body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise bad() from None
        if not isinstance(data, dict):
            raise bad()
        return data

    # ------------------------------------------------------------------ capabilities
    def build_responses_body(self, *, schema, schema_name, messages, instructions, max_output_tokens) -> dict:
        body = {
            "model": self.cfg.model(GENERATE_STRUCTURED),
            "store": False,
            "input": [{"role": m.role, "content": m.content} for m in messages],
            "text": {"format": {"type": "json_schema", "name": schema_name, "schema": schema, "strict": True}},
        }
        if instructions:
            body["instructions"] = instructions
        if max_output_tokens:
            body["max_output_tokens"] = int(max_output_tokens)
        return body

    def generate_structured(self, *, schema, schema_name, messages, instructions=None, max_output_tokens=None):
        if not _SCHEMA_NAME.match(schema_name or ""):
            raise ValueError("schema_name must be 1-64 letters, digits, '_' or '-'")
        schema_mod.check_strict(schema)
        if not messages or not all(isinstance(m, Message) for m in messages):
            raise ValueError("messages must be a non-empty list of Message")
        if max_output_tokens is not None and not (isinstance(max_output_tokens, int) and 16 <= max_output_tokens <= 128_000):
            raise ValueError("max_output_tokens must be 16-128000")
        model = self.cfg.model(GENERATE_STRUCTURED)
        chars = sum(len(m.content) for m in messages) + len(instructions or "")
        if chars > self.cfg.max_input_chars:
            raise ProviderError("input_too_large", model=model)
        body = json.dumps(self.build_responses_body(schema=schema, schema_name=schema_name, messages=messages,
                                                    instructions=instructions, max_output_tokens=max_output_tokens),
                          ensure_ascii=False).encode("utf-8")
        resp, attempts, latency = self._send(GENERATE_STRUCTURED, "/responses", "application/json", body)
        data = self._json(resp, attempts, latency, model)
        reported_model = data.get("model") if isinstance(data.get("model"), str) else model
        usage_raw = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        usage = Usage(_count(usage_raw.get("input_tokens")), _count(usage_raw.get("output_tokens")),
                      _count(usage_raw.get("total_tokens")))
        common = dict(http_status=resp.status, attempts=attempts, latency_ms=latency, model=reported_model,
                      provider_request_id=resp.header("x-request-id"))
        status = data.get("status")
        if status == "incomplete":
            details = data.get("incomplete_details") if isinstance(data.get("incomplete_details"), dict) else {}
            raise ProviderError("provider_incomplete", provider_error_type=_safe_type(details.get("reason")), **common)
        if status in ("failed", "cancelled"):
            err = data.get("error") if isinstance(data.get("error"), dict) else {}
            raise ProviderError("provider_invalid_response", provider_error_type=_safe_type(err.get("code")), **common)
        texts, refused = [], False
        for item in data.get("output") or []:
            if not isinstance(item, dict) or item.get("type") != "message":
                continue
            for part in item.get("content") or []:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "output_text" and isinstance(part.get("text"), str):
                    texts.append(part["text"])
                elif part.get("type") == "refusal":
                    refused = True
        if refused and not texts:
            raise ProviderError("provider_refused", **common)
        try:
            parsed = json.loads("".join(texts))
        except ValueError:
            raise ProviderError("provider_invalid_response", **common) from None
        if not isinstance(parsed, dict):
            raise ProviderError("provider_invalid_response", **common)
        try:
            schema_mod.validate(parsed, schema)
        except schema_mod.SchemaError:
            raise ProviderError("provider_invalid_response", provider_error_type="schema_mismatch", **common) from None
        meta = CallMeta(model=reported_model, attempts=attempts, latency_ms=latency, http_status=resp.status,
                        provider_request_id=resp.header("x-request-id"), usage=usage, input_bytes=len(body))
        return StructuredResult(data=parsed, meta=meta)

    @staticmethod
    def multipart(fields: list) -> tuple[bytes, str]:
        """fields: [(name, value:str)] or [(name, (filename, content_type, bytes))] -> (body, content type)."""
        boundary = "cawp-" + uuid.uuid4().hex
        out = bytearray()
        for name, value in fields:
            out += f"--{boundary}\r\n".encode()
            if isinstance(value, tuple):
                filename, ctype, data = value
                out += (f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
                        f"Content-Type: {ctype}\r\n\r\n").encode()
                out += data
            else:
                out += f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode()
                out += str(value).encode("utf-8")
            out += b"\r\n"
        out += f"--{boundary}--\r\n".encode()
        return bytes(out), f"multipart/form-data; boundary={boundary}"

    def transcribe(self, *, audio, filename=None, content_type="audio/wav", language=None, diarize=False):
        model = self.cfg.model(TRANSCRIBE)
        if not isinstance(audio, (bytes, bytearray)) or not audio:
            raise ValueError("audio must be non-empty bytes")
        if language is not None and not _LANG.match(language):
            raise ValueError("language must be a two-letter ISO-639-1 code")
        ctype = (content_type or "").split(";")[0].strip().lower()
        ext = _AUDIO_EXT.get(ctype)
        if ext is None:
            raise ValueError(f"unsupported audio content type {ctype!r}")
        if len(audio) > self.cfg.transcribe_max_bytes:
            raise ProviderError("audio_too_large", model=model)
        diarized = bool(diarize) and is_diarizing_model(model)
        fields = [("file", (f"audio.{ext}", ctype, bytes(audio))), ("model", model),
                  ("response_format", "diarized_json" if diarized else "json")]
        if diarized:
            fields.append(("chunking_strategy", "auto"))
        if language:
            fields.append(("language", language))
        body, mp_type = self.multipart(fields)
        resp, attempts, latency = self._send(TRANSCRIBE, "/audio/transcriptions", mp_type, body)
        data = self._json(resp, attempts, latency, model)
        common = dict(http_status=resp.status, attempts=attempts, latency_ms=latency, model=model,
                      provider_request_id=resp.header("x-request-id"))
        text = data.get("text")
        if not isinstance(text, str):
            raise ProviderError("provider_invalid_response", **common)
        segments = []
        if diarized:
            raw_segments = data.get("segments")
            if not isinstance(raw_segments, list):
                raise ProviderError("provider_invalid_response", **common)
            for seg in raw_segments:
                if not isinstance(seg, dict):
                    raise ProviderError("provider_invalid_response", **common)
                start, end = _seconds(seg.get("start")), _seconds(seg.get("end"))
                if start is None or end is None or end < start or not isinstance(seg.get("text"), str):
                    raise ProviderError("provider_invalid_response", **common)
                speaker = seg.get("speaker")
                speaker = str(speaker)[:40] if speaker not in (None, "") else None
                segments.append(Segment(start=start, end=end, speaker=speaker, text=seg["text"]))
        usage_raw = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        if usage_raw.get("type") == "duration":
            usage = Usage(audio_seconds=_seconds(usage_raw.get("seconds")))
        else:
            usage = Usage(_count(usage_raw.get("input_tokens")), _count(usage_raw.get("output_tokens")),
                          _count(usage_raw.get("total_tokens")))
        meta = CallMeta(model=model, attempts=attempts, latency_ms=latency, http_status=resp.status,
                        provider_request_id=resp.header("x-request-id"), usage=usage, input_bytes=len(body))
        lang = data.get("language") if isinstance(data.get("language"), str) else language
        return TranscriptionResult(text=text, segments=tuple(segments), diarized=diarized, language=lang,
                                   duration_seconds=_seconds(data.get("duration")), meta=meta)

    def build_realtime_body(self, *, instructions, voice, expires_in_seconds) -> dict:
        session = {"type": "realtime", "model": self.cfg.model(REALTIME_SESSION), "instructions": instructions}
        if voice:
            session["audio"] = {"output": {"voice": voice}}
        return {"expires_after": {"anchor": "created_at", "seconds": int(expires_in_seconds)}, "session": session}

    def mint_realtime_session(self, *, instructions, voice=None, expires_in_seconds=60):
        model = self.cfg.model(REALTIME_SESSION)
        if not isinstance(instructions, str) or not instructions.strip():
            raise ValueError("instructions are required")
        if voice is not None and not _VOICE.match(voice):
            raise ValueError("invalid voice name")
        if not (isinstance(expires_in_seconds, int) and 10 <= expires_in_seconds <= 7200):
            raise ValueError("expires_in_seconds must be 10-7200")
        body = json.dumps(self.build_realtime_body(instructions=instructions, voice=voice,
                                                   expires_in_seconds=expires_in_seconds),
                          ensure_ascii=False).encode("utf-8")
        resp, attempts, latency = self._send(REALTIME_SESSION, "/realtime/client_secrets", "application/json", body)
        data = self._json(resp, attempts, latency, model)
        common = dict(http_status=resp.status, attempts=attempts, latency_ms=latency, model=model,
                      provider_request_id=resp.header("x-request-id"))
        value = data.get("value")
        if not isinstance(value, str) or not value.strip():
            raise ProviderError("provider_invalid_response", **common)
        expires_at = data.get("expires_at")
        expires_at = expires_at if isinstance(expires_at, int) and not isinstance(expires_at, bool) else None
        session = data.get("session") if isinstance(data.get("session"), dict) else {}
        session_id = session.get("id") if isinstance(session.get("id"), str) else None
        meta = CallMeta(model=model, attempts=attempts, latency_ms=latency, http_status=resp.status,
                        provider_request_id=resp.header("x-request-id"), usage=Usage(), input_bytes=len(body))
        return RealtimeSession(client_secret=value, expires_at=expires_at, session_id=session_id, meta=meta)
