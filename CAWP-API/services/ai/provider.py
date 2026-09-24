"""The AI provider interface. OpenAI is the only implementation (services/ai/openai_provider.py);
another provider implements the same three capabilities and is selected by `AI_PROVIDER`.

Callers in routes do not use a provider directly: they go through services/ai/gateway.py, which
adds readiness, the data policy, masking and the provider-call audit. A provider:

* never returns a fabricated or partial result: anything it cannot fully parse raises
  `ProviderError`;
* retries only an HTTP 429 or 5xx answer, a bounded number of times, inside the same call;
  a timeout or connection failure is not retried (the provider may have received the request),
  and nothing is ever replayed after a restart;
* never logs or raises with the key, the request body or the provider's error text.
"""
from __future__ import annotations

import abc
import dataclasses

MESSAGE_ROLES = ("system", "developer", "user", "assistant")


@dataclasses.dataclass(frozen=True)
class Message:
    role: str
    content: str = dataclasses.field(repr=False)

    def __post_init__(self):
        if self.role not in MESSAGE_ROLES:
            raise ValueError(f"message role must be one of {MESSAGE_ROLES}")
        if not isinstance(self.content, str):
            raise ValueError("message content must be a string")


@dataclasses.dataclass(frozen=True)
class Usage:
    """Unknown stays unknown: a count the provider did not report is None, never 0."""
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    audio_seconds: float | None = None

    def as_dict(self) -> dict:
        return dataclasses.asdict(self)


@dataclasses.dataclass(frozen=True)
class CallMeta:
    """What the audit row needs from one provider call (no content)."""
    model: str
    attempts: int
    latency_ms: int
    http_status: int | None
    provider_request_id: str | None
    usage: Usage
    input_bytes: int


@dataclasses.dataclass(frozen=True)
class StructuredResult:
    data: dict = dataclasses.field(repr=False)
    meta: CallMeta


@dataclasses.dataclass(frozen=True)
class Segment:
    start: float
    end: float
    speaker: str | None          # the provider's anonymous label ("A", "speaker_0"); None when not diarized
    text: str = dataclasses.field(repr=False)

    def as_dict(self) -> dict:
        return {"start": self.start, "end": self.end, "speaker": self.speaker, "text": self.text}


@dataclasses.dataclass(frozen=True)
class TranscriptionResult:
    text: str = dataclasses.field(repr=False)
    segments: tuple = dataclasses.field(repr=False, default=())
    diarized: bool = False
    language: str | None = None
    duration_seconds: float | None = None
    meta: CallMeta | None = None


@dataclasses.dataclass(frozen=True)
class RealtimeSession:
    client_secret: str = dataclasses.field(repr=False)   # ephemeral; for the browser only, never stored
    expires_at: int | None = None                         # unix seconds, as the provider reported it
    session_id: str | None = None
    meta: CallMeta | None = None


class ProviderError(Exception):
    """A provider call failed. `code` is stable (see CODES); no content, no key in the message.

    `retryable` says a client may try again later (never done automatically across requests).
    `attempts` and `http_status` feed the audit row.
    """

    CODES = {
        "provider_timeout": "the provider did not answer in time",
        "provider_connection_failed": "the provider could not be reached",
        "provider_rate_limited": "the provider is rate limiting requests",
        "provider_quota_exceeded": "the provider account has no remaining quota",
        "provider_unavailable": "the provider answered with a server error",
        "provider_auth_failed": "the provider refused the server's credentials",
        "provider_rejected": "the provider rejected the request",
        "provider_invalid_response": "the provider's answer could not be used",
        "provider_incomplete": "the provider's answer was incomplete",
        "provider_refused": "the model refused to answer",
        "input_too_large": "the input is larger than the configured limit",
        "audio_too_large": "the audio is larger than the configured limit",
    }

    def __init__(self, code: str, *, http_status: int | None = None, attempts: int = 0,
                 provider_error_type: str | None = None, retryable: bool = False,
                 latency_ms: int | None = None, provider_request_id: str | None = None,
                 model: str | None = None):
        super().__init__(self.CODES.get(code, code))
        self.code = code
        self.http_status = http_status
        self.attempts = attempts
        self.provider_error_type = provider_error_type
        self.retryable = retryable
        self.latency_ms = latency_ms
        self.provider_request_id = provider_request_id
        self.model = model


class AIProvider(abc.ABC):
    """One provider. Construct with an AIConfig; `transport` and `sleep` are test seams."""

    name: str = ""

    @abc.abstractmethod
    def generate_structured(self, *, schema: dict, schema_name: str, messages: list,
                            instructions: str | None = None,
                            max_output_tokens: int | None = None) -> StructuredResult:
        """JSON output that matches `schema` (a strict JSON schema). `messages` are Message."""

    @abc.abstractmethod
    def transcribe(self, *, audio: bytes, filename: str | None = None, content_type: str = "audio/wav",
                   language: str | None = None, diarize: bool = False) -> TranscriptionResult:
        """Speech to text. With `diarize` and a diarizing model: anonymous speaker segments.
        Without a diarizing model: text only (`diarized=False`), never invented speakers.
        `filename` is ignored by design: the provider only ever sees a generic name."""

    @abc.abstractmethod
    def mint_realtime_session(self, *, instructions: str, voice: str | None = None,
                              expires_in_seconds: int = 60) -> RealtimeSession:
        """An ephemeral client secret for one browser Realtime session."""
