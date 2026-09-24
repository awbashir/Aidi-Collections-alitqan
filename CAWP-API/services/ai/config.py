"""AI configuration from the server environment, read at call time (never cached at import).

| Variable | Default | Meaning |
|---|---|---|
| `AI_PROVIDER` | `openai` | the provider implementation (only `openai` exists) |
| `OPENAI_API_KEY` | none | the key. Missing = `ai_not_configured`. Never logged, returned or stored |
| `OPENAI_ORG_ID` / `OPENAI_PROJECT_ID` | none | optional `OpenAI-Organization` / `OpenAI-Project` headers |
| `OPENAI_BASE_URL` | `https://api.openai.com/v1` | override (tests, a proxy); https, or http to loopback only |
| `AI_MODEL_TEXT` | none | model for structured generation. Missing = `model_not_set` |
| `AI_MODEL_TRANSCRIBE` | none | model for transcription. Missing = `model_not_set` |
| `AI_MODEL_REALTIME` | none | model for Realtime sessions. Missing = `model_not_set` |
| `AI_TIMEOUT_TEXT_SECONDS` | `60` | socket timeout per HTTP attempt (5-300) |
| `AI_TIMEOUT_TRANSCRIBE_SECONDS` | `180` | (5-600) |
| `AI_TIMEOUT_REALTIME_SECONDS` | `15` | (5-120) |
| `AI_MAX_RETRIES` | `2` | retries after a 429/5xx answer, per call (0-5); nothing else is retried |
| `AI_RETRY_BASE_SECONDS` | `0.5` | exponential backoff base (0-10) |
| `AI_RETRY_MAX_WAIT_SECONDS` | `8` | cap of one wait, Retry-After included (0-60) |
| `AI_MAX_INPUT_CHARS` | `200000` | refuse larger structured inputs (1000-2000000) |
| `AI_TRANSCRIBE_MAX_BYTES` | `26214400` | refuse larger audio (25 MB, the provider limit) |
| `AI_CALL_STALE_SECONDS` | `1800` | a `started` provider-call row older than this is `interrupted` (300-86400) |

There is deliberately **no default model**: a default would imply the model is available to the
deployment's account. Readiness says `model_not_set` until an operator sets it.
"""
from __future__ import annotations

import dataclasses
import ipaddress
import logging
import os
from urllib.parse import urlsplit, urlunsplit

log = logging.getLogger("cawp.ai.config")

DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
PROVIDERS = ("openai",)

GENERATE_STRUCTURED = "generate_structured"
TRANSCRIBE = "transcribe"
REALTIME_SESSION = "realtime_session"
CAPABILITIES = (GENERATE_STRUCTURED, TRANSCRIBE, REALTIME_SESSION)

MODEL_ENV = {GENERATE_STRUCTURED: "AI_MODEL_TEXT", TRANSCRIBE: "AI_MODEL_TRANSCRIBE",
             REALTIME_SESSION: "AI_MODEL_REALTIME"}
TIMEOUT_ENV = {GENERATE_STRUCTURED: ("AI_TIMEOUT_TEXT_SECONDS", 60.0, 5.0, 300.0),
               TRANSCRIBE: ("AI_TIMEOUT_TRANSCRIBE_SECONDS", 180.0, 5.0, 600.0),
               REALTIME_SESSION: ("AI_TIMEOUT_REALTIME_SECONDS", 15.0, 5.0, 120.0)}


def _num_env(name, default, lo, hi, cast=float):
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        v = cast(raw.strip())
    except ValueError:
        log.warning("%s is not a number; using %s", name, default)
        return default
    if not lo <= v <= hi:
        log.warning("%s=%s is outside %s-%s; using %s", name, v, lo, hi, default)
        return default
    return v


def _str_env(name) -> str | None:
    raw = os.getenv(name)
    if raw is None:
        return None
    raw = raw.strip()
    return raw or None


def _is_loopback(host: str | None) -> bool:
    if not host:
        return False
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def normalize_base_url(raw: str | None) -> tuple[str | None, str | None]:
    """(base_url, None) or (None, reason). https anywhere; http only to a loopback host.
    User info (credentials) in the URL is refused, so nothing secret can hide there."""
    if raw is None:
        return DEFAULT_OPENAI_BASE_URL, None
    parts = urlsplit(raw.strip())
    if parts.scheme not in ("https", "http") or not parts.hostname:
        return None, "base_url_invalid"
    if parts.username or parts.password or parts.query or parts.fragment:
        return None, "base_url_invalid"
    if parts.scheme == "http" and not _is_loopback(parts.hostname):
        return None, "base_url_insecure"
    return urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"), "", "")), None


@dataclasses.dataclass(frozen=True)
class AIConfig:
    provider: str | None             # None when AI_PROVIDER names an unknown provider
    api_key: str | None = dataclasses.field(repr=False, default=None)
    organization: str | None = dataclasses.field(repr=False, default=None)
    project: str | None = dataclasses.field(repr=False, default=None)
    base_url: str | None = None
    base_url_problem: str | None = None
    models: dict = dataclasses.field(default_factory=dict)
    timeouts: dict = dataclasses.field(default_factory=dict)
    max_retries: int = 2
    retry_base_seconds: float = 0.5
    retry_max_wait_seconds: float = 8.0
    max_input_chars: int = 200_000
    transcribe_max_bytes: int = 25 * 1024 * 1024
    call_stale_seconds: int = 1800

    @property
    def key_configured(self) -> bool:
        return bool(self.api_key)

    @property
    def base_url_is_default(self) -> bool:
        return self.base_url == DEFAULT_OPENAI_BASE_URL

    def model(self, capability: str) -> str | None:
        return self.models.get(capability)

    def not_configured_reasons(self, capability: str) -> list[str]:
        """Configuration reasons only (the policy is checked separately)."""
        reasons = []
        if self.provider is None or not self.api_key or self.base_url is None:
            reasons.append("ai_not_configured")
        if not self.model(capability):
            reasons.append("model_not_set")
        return reasons

    def public_details(self) -> dict:
        """For ai_admin: never the key, org or project values."""
        return {
            "provider": self.provider,
            "key_configured": self.key_configured,
            "organization_configured": bool(self.organization),
            "project_configured": bool(self.project),
            "base_url": self.base_url,
            "base_url_is_default": self.base_url_is_default,
            "base_url_problem": self.base_url_problem,
            "models": {c: self.models.get(c) for c in CAPABILITIES},
            "timeouts_seconds": dict(self.timeouts),
            "max_retries": self.max_retries,
            "max_input_chars": self.max_input_chars,
            "transcribe_max_bytes": self.transcribe_max_bytes,
        }


def load() -> AIConfig:
    provider_raw = (_str_env("AI_PROVIDER") or "openai").lower()
    provider = provider_raw if provider_raw in PROVIDERS else None
    if provider is None:
        log.warning("AI_PROVIDER=%r is not a known provider; AI is not configured", provider_raw)
    base_url, problem = normalize_base_url(_str_env("OPENAI_BASE_URL"))
    if problem:
        log.warning("OPENAI_BASE_URL refused (%s); AI is not configured", problem)
    return AIConfig(
        provider=provider,
        api_key=_str_env("OPENAI_API_KEY"),
        organization=_str_env("OPENAI_ORG_ID"),
        project=_str_env("OPENAI_PROJECT_ID"),
        base_url=base_url,
        base_url_problem=problem,
        models={c: _str_env(env) for c, env in MODEL_ENV.items()},
        timeouts={c: _num_env(*TIMEOUT_ENV[c]) for c in CAPABILITIES},
        max_retries=_num_env("AI_MAX_RETRIES", 2, 0, 5, int),
        retry_base_seconds=_num_env("AI_RETRY_BASE_SECONDS", 0.5, 0.0, 10.0),
        retry_max_wait_seconds=_num_env("AI_RETRY_MAX_WAIT_SECONDS", 8.0, 0.0, 60.0),
        max_input_chars=_num_env("AI_MAX_INPUT_CHARS", 200_000, 1000, 2_000_000, int),
        transcribe_max_bytes=_num_env("AI_TRANSCRIBE_MAX_BYTES", 25 * 1024 * 1024, 1024, 25 * 1024 * 1024, int),
        call_stale_seconds=_num_env("AI_CALL_STALE_SECONDS", 1800, 300, 86400, int),
    )
