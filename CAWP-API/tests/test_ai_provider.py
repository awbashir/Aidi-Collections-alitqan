"""Phase 6 Q0: configuration and the OpenAI provider through an HTTP-level fake (no DB, no network).

The FakeTransport records the exact request we send (URL, headers, body, timeout) and replays
canned answers. One test runs the real urllib transport against a local loopback HTTP server with
the base-URL override. No real provider is ever contacted.

Run: docker compose --profile test run --rm -e TEST_DB_PREFIX=cawp_test_ai0 api-test tests/test_ai_provider.py
"""
from __future__ import annotations

import json
import logging
import threading
import time

import pytest

from test_ai_helpers import (BASE, TEST_KEY, FakeTransport, Sleeps, ai_env, configure, http, realtime_ok, responses_ok,  # noqa: F401
                             transcription_ok)

pytestmark = pytest.mark.no_db

SCHEMA = {"type": "object", "additionalProperties": False, "required": ["summary", "items"],
          "properties": {"summary": {"type": "string"},
                         "items": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                                              "required": ["id", "status"],
                                                              "properties": {"id": {"type": "string"},
                                                                             "status": {"type": "string", "enum": ["met", "not_met"]}}}}}}
GOOD = {"summary": "ok", "items": [{"id": "c1", "status": "met"}]}


def provider(monkeypatch, *script, **env):
    from services.ai import config
    from services.ai.openai_provider import OpenAIProvider
    configure(monkeypatch, **env)
    t, sleeps = FakeTransport(*script), Sleeps()
    return OpenAIProvider(config.load(), transport=t, sleep=sleeps), t, sleeps


def msgs(text="hello"):
    from services.ai.provider import Message
    return [Message("user", text)]


# ------------------------------------------------------------------------------ configuration
def test_nothing_is_configured_by_default(monkeypatch):
    from services.ai import config
    cfg = config.load()
    assert cfg.provider == "openai" and not cfg.key_configured
    assert cfg.models == {"generate_structured": None, "transcribe": None, "realtime_session": None}
    for cap in config.CAPABILITIES:
        assert cfg.not_configured_reasons(cap) == ["ai_not_configured", "model_not_set"]
    # a key alone does not imply a model: there is no default model
    monkeypatch.setenv("OPENAI_API_KEY", TEST_KEY)
    assert config.load().not_configured_reasons("generate_structured") == ["model_not_set"]
    monkeypatch.setenv("AI_MODEL_TEXT", "gpt-test-text")
    assert config.load().not_configured_reasons("generate_structured") == []
    assert config.load().not_configured_reasons("transcribe") == ["model_not_set"]


def test_unknown_provider_and_unsafe_base_url_mean_not_configured(monkeypatch):
    from services.ai import config
    configure(monkeypatch)
    monkeypatch.setenv("AI_PROVIDER", "someone-else")
    assert "ai_not_configured" in config.load().not_configured_reasons("generate_structured")
    monkeypatch.delenv("AI_PROVIDER")
    for url, problem in (("http://ai.example.com/v1", "base_url_insecure"),
                         ("https://user:pw@ai.example.com/v1", "base_url_invalid"),
                         ("ftp://ai.example.com", "base_url_invalid")):
        monkeypatch.setenv("OPENAI_BASE_URL", url)
        cfg = config.load()
        assert cfg.base_url is None and cfg.base_url_problem == problem
        assert "ai_not_configured" in cfg.not_configured_reasons("generate_structured")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://127.0.0.1:9/v1/")
    assert config.load().base_url == "http://127.0.0.1:9/v1"
    monkeypatch.delenv("OPENAI_BASE_URL")
    assert config.load().base_url == "https://api.openai.com/v1"


def test_the_key_is_never_in_repr_or_details(monkeypatch):
    from services.ai import config
    configure(monkeypatch, OPENAI_ORG_ID="org-secret-1", OPENAI_PROJECT_ID="proj-secret-1")
    cfg = config.load()
    blob = repr(cfg) + json.dumps(cfg.public_details())
    for secret in (TEST_KEY, "org-secret-1", "proj-secret-1"):
        assert secret not in blob
    assert cfg.public_details()["key_configured"] is True


# ------------------------------------------------------------------------------ structured generation
def test_responses_request_shape(monkeypatch):
    p, t, _ = provider(monkeypatch, responses_ok(GOOD), AI_TIMEOUT_TEXT_SECONDS="42",
                       OPENAI_ORG_ID="org-1", OPENAI_PROJECT_ID="proj-1")
    from services.ai.provider import Message
    out = p.generate_structured(schema=SCHEMA, schema_name="qc_draft", instructions="Be careful.",
                                messages=[Message("developer", "rules"), Message("user", "مرحبا")],
                                max_output_tokens=900)
    req = t.requests[0]
    assert (req.method, req.url, req.timeout) == ("POST", BASE + "/responses", 42.0)
    assert req.headers["Authorization"] == f"Bearer {TEST_KEY}"
    assert req.headers["Content-Type"] == "application/json"
    assert req.headers["OpenAI-Organization"] == "org-1" and req.headers["OpenAI-Project"] == "proj-1"
    body = t.json()
    assert body == {"model": "gpt-test-text", "store": False, "instructions": "Be careful.",
                    "input": [{"role": "developer", "content": "rules"}, {"role": "user", "content": "مرحبا"}],
                    "text": {"format": {"type": "json_schema", "name": "qc_draft", "schema": SCHEMA, "strict": True}},
                    "max_output_tokens": 900}
    assert "مرحبا" in req.body.decode("utf-8")          # UTF-8, not \u escapes
    assert out.data == GOOD
    m = out.meta
    assert (m.model, m.attempts, m.http_status, m.provider_request_id) == ("gpt-test-text-2026", 1, 200, "req_fake_1")
    assert (m.usage.input_tokens, m.usage.output_tokens, m.usage.total_tokens) == (120, 40, 160)
    assert m.input_bytes == len(req.body) and m.latency_ms >= 0


def test_a_non_strict_schema_is_refused_before_any_request(monkeypatch):
    from services.ai.schema import SchemaError
    p, t, _ = provider(monkeypatch)
    loose = {"type": "object", "properties": {"a": {"type": "string"}}, "required": []}
    with pytest.raises(SchemaError):
        p.generate_structured(schema=loose, schema_name="x", messages=msgs())
    with pytest.raises(ValueError):
        p.generate_structured(schema=SCHEMA, schema_name="bad name!", messages=msgs())
    assert t.requests == []


def test_429_then_success_honours_retry_after(monkeypatch):
    p, t, sleeps = provider(monkeypatch, http(429, {"error": {"code": "rate_limit_exceeded"}}, {"Retry-After": "3"}),
                            responses_ok(GOOD), AI_RETRY_MAX_WAIT_SECONDS="2")
    out = p.generate_structured(schema=SCHEMA, schema_name="x", messages=msgs())
    assert out.meta.attempts == 2 and len(t.requests) == 2
    assert sleeps == [2.0]                                  # Retry-After 3 s capped at 2 s
    assert t.requests[0].body == t.requests[1].body        # the same request, not a new one


@pytest.mark.parametrize("statuses,code", [((500, 502, 503), "provider_unavailable"),
                                           ((429, 429, 429), "provider_rate_limited")])
def test_retries_are_bounded_then_fail(monkeypatch, statuses, code):
    from services.ai.provider import ProviderError
    p, t, sleeps = provider(monkeypatch, *(http(s, {"error": {"type": "server_error"}}) for s in statuses))
    with pytest.raises(ProviderError) as e:
        p.generate_structured(schema=SCHEMA, schema_name="x", messages=msgs())
    assert e.value.code == code and e.value.attempts == 3 and e.value.retryable
    assert e.value.http_status == statuses[-1]
    assert len(t.requests) == 3 and len(sleeps) == 2        # AI_MAX_RETRIES default 2


def test_max_retries_zero_means_one_attempt(monkeypatch):
    from services.ai.provider import ProviderError
    p, t, sleeps = provider(monkeypatch, http(503), AI_MAX_RETRIES="0")
    with pytest.raises(ProviderError):
        p.generate_structured(schema=SCHEMA, schema_name="x", messages=msgs())
    assert len(t.requests) == 1 and sleeps == []


@pytest.mark.parametrize("answer,code,ptype", [
    (http(429, {"error": {"code": "insufficient_quota", "message": "private text"}}), "provider_quota_exceeded", "insufficient_quota"),
    (http(400, {"error": {"code": "invalid_json_schema", "message": "echo of the prompt"}}), "provider_rejected", "invalid_json_schema"),
    (http(401, {"error": {"code": "invalid_api_key"}}), "provider_auth_failed", "invalid_api_key"),
    (http(404, {"error": {"code": "model_not_found"}}), "provider_rejected", "model_not_found"),
    (http(400, {"error": {"code": "Weird Code With Spaces <script>"}}), "provider_rejected", None),
])
def test_other_errors_are_not_retried(monkeypatch, answer, code, ptype):
    from services.ai.provider import ProviderError
    p, t, sleeps = provider(monkeypatch, answer)
    with pytest.raises(ProviderError) as e:
        p.generate_structured(schema=SCHEMA, schema_name="x", messages=msgs())
    assert (e.value.code, e.value.provider_error_type, e.value.attempts) == (code, ptype, 1)
    assert len(t.requests) == 1 and sleeps == []
    assert "private text" not in str(e.value) and "echo" not in str(e.value)


@pytest.mark.parametrize("kind,code", [("timeout", "provider_timeout"), ("connection", "provider_connection_failed")])
def test_timeouts_and_connection_failures_are_not_retried(monkeypatch, kind, code):
    from services.ai.provider import ProviderError
    from services.ai.transport import TransportError
    p, t, sleeps = provider(monkeypatch, TransportError(kind))
    with pytest.raises(ProviderError) as e:
        p.generate_structured(schema=SCHEMA, schema_name="x", messages=msgs())
    assert e.value.code == code and e.value.attempts == 1 and len(t.requests) == 1 and sleeps == []


def _message(*parts, status="completed"):
    return http(200, {"status": status, "model": "m", "output": [{"type": "message", "content": list(parts)}],
                      "incomplete_details": {"reason": "max_output_tokens"} if status == "incomplete" else None})


@pytest.mark.parametrize("answer,code", [
    (_message({"type": "output_text", "text": '{"summary": "cut'}, status="incomplete"), "provider_incomplete"),
    (_message({"type": "refusal", "refusal": "I can't help with that."}), "provider_refused"),
    (_message({"type": "output_text", "text": "not json"}), "provider_invalid_response"),
    (_message({"type": "output_text", "text": "[1, 2]"}), "provider_invalid_response"),
    (_message({"type": "output_text", "text": json.dumps({"summary": "x", "items": [{"id": "c1", "status": "maybe"}]})}),
     "provider_invalid_response"),
    (_message({"type": "output_text", "text": json.dumps({"summary": "x"})}), "provider_invalid_response"),
    (http(200, b"<html>gateway</html>"), "provider_invalid_response"),
    (http(200, {"status": "completed", "output": []}), "provider_invalid_response"),
])
def test_unusable_answers_are_errors_never_partial_results(monkeypatch, answer, code):
    from services.ai.provider import ProviderError
    p, _t, _s = provider(monkeypatch, answer)
    with pytest.raises(ProviderError) as e:
        p.generate_structured(schema=SCHEMA, schema_name="x", messages=msgs())
    assert e.value.code == code


def test_input_limit(monkeypatch):
    from services.ai.provider import ProviderError
    p, t, _ = provider(monkeypatch, AI_MAX_INPUT_CHARS="1000")
    with pytest.raises(ProviderError) as e:
        p.generate_structured(schema=SCHEMA, schema_name="x", messages=msgs("x" * 1001))
    assert e.value.code == "input_too_large" and t.requests == []


# ------------------------------------------------------------------------------ transcription
def _multipart_fields(req):
    ctype = req.headers["Content-Type"]
    assert ctype.startswith("multipart/form-data; boundary=")
    boundary = ctype.split("boundary=")[1].encode()
    fields = {}
    for part in req.body.split(b"--" + boundary)[1:-1]:
        head, _, value = part.strip(b"\r\n").partition(b"\r\n\r\n")
        name = head.split(b'name="')[1].split(b'"')[0].decode()
        filename = head.split(b'filename="')[1].split(b'"')[0].decode() if b"filename=" in head else None
        fields[name] = (filename, value)
    return fields


def test_transcription_request_shape_and_diarized_segments(monkeypatch):
    p, t, _ = provider(monkeypatch, transcription_ok(), AI_TIMEOUT_TRANSCRIBE_SECONDS="200")
    out = p.transcribe(audio=b"RIFF....WAVEfake", filename="0551234567-private.wav", content_type="audio/wav",
                       language="ar", diarize=True)
    req = t.requests[0]
    assert (req.url, req.timeout) == (BASE + "/audio/transcriptions", 200.0)
    f = _multipart_fields(req)
    assert f["file"] == ("audio.wav", b"RIFF....WAVEfake")          # never the original filename
    assert f["model"][1] == b"gpt-test-transcribe-diarize"
    assert f["response_format"][1] == b"diarized_json" and f["chunking_strategy"][1] == b"auto"
    assert f["language"][1] == b"ar"
    assert b"0551234567" not in req.body
    assert out.diarized and out.text == "مرحبا، معك سارة"
    assert [s.as_dict() for s in out.segments] == [
        {"start": 0.0, "end": 2.1, "speaker": "A", "text": "مرحبا"},
        {"start": 2.1, "end": 3.9, "speaker": "B", "text": "معك سارة"}]
    assert out.meta.usage.audio_seconds == 12.5 and out.duration_seconds == 12.5


def test_a_non_diarizing_model_gives_text_without_invented_speakers(monkeypatch):
    p, t, _ = provider(monkeypatch, http(200, {"text": "hello", "usage": {"type": "tokens", "input_tokens": 50,
                                                                          "output_tokens": 5, "total_tokens": 55}}),
                       transcribe="gpt-test-transcribe")
    out = p.transcribe(audio=b"x" * 10, content_type="audio/mpeg", diarize=True)
    f = _multipart_fields(t.requests[0])
    assert f["response_format"][1] == b"json" and "chunking_strategy" not in f and "language" not in f
    assert f["file"][0] == "audio.mp3"
    assert out.diarized is False and out.segments == () and out.meta.usage.total_tokens == 55


@pytest.mark.parametrize("segments", [[{"start": 2, "end": 1, "speaker": "A", "text": "x"}],
                                      [{"start": -1, "end": 1, "speaker": "A", "text": "x"}],
                                      [{"start": 0, "end": 1, "speaker": "A"}], "nope"])
def test_invalid_transcript_timing_is_an_error(monkeypatch, segments):
    from services.ai.provider import ProviderError
    p, _t, _s = provider(monkeypatch, http(200, {"text": "x", "segments": segments}))
    with pytest.raises(ProviderError) as e:
        p.transcribe(audio=b"x", content_type="audio/wav", diarize=True)
    assert e.value.code == "provider_invalid_response"


def test_audio_limits(monkeypatch):
    from services.ai.provider import ProviderError
    p, t, _ = provider(monkeypatch, AI_TRANSCRIBE_MAX_BYTES="1024")
    with pytest.raises(ProviderError) as e:
        p.transcribe(audio=b"x" * 1025, content_type="audio/wav")
    assert e.value.code == "audio_too_large"
    with pytest.raises(ValueError):
        p.transcribe(audio=b"x", content_type="video/mp4")
    assert t.requests == []


# ------------------------------------------------------------------------------ realtime
def test_realtime_client_secret_request_shape(monkeypatch):
    p, t, _ = provider(monkeypatch, realtime_ok(), AI_TIMEOUT_REALTIME_SECONDS="9")
    out = p.mint_realtime_session(instructions="Fictional training only.", voice="marin", expires_in_seconds=60)
    req = t.requests[0]
    assert (req.url, req.timeout) == (BASE + "/realtime/client_secrets", 9.0)
    assert t.json() == {"expires_after": {"anchor": "created_at", "seconds": 60},
                        "session": {"type": "realtime", "model": "gpt-test-realtime",
                                    "instructions": "Fictional training only.",
                                    "audio": {"output": {"voice": "marin"}}}}
    assert (out.client_secret, out.expires_at, out.session_id) == ("ek_fake_ephemeral_secret_123", 1790000000, "sess_fake_1")
    assert "ek_fake" not in repr(out)


def test_realtime_without_a_secret_is_an_error(monkeypatch):
    from services.ai.provider import ProviderError
    p, _t, _s = provider(monkeypatch, http(200, {"expires_at": 1}))
    with pytest.raises(ProviderError) as e:
        p.mint_realtime_session(instructions="x")
    assert e.value.code == "provider_invalid_response"


# ------------------------------------------------------------------------------ secrecy and the network guard
def test_the_key_never_reaches_logs_or_errors(monkeypatch, caplog):
    from services.ai.provider import ProviderError
    caplog.set_level(logging.DEBUG)
    p, t, _ = provider(monkeypatch, http(503), http(503), http(503))
    with pytest.raises(ProviderError) as e:
        p.generate_structured(schema=SCHEMA, schema_name="x", messages=msgs("customer secret text"))
    everything = caplog.text + str(e.value) + repr(e.value) + repr(t.requests[0])
    assert TEST_KEY not in everything and "customer secret text" not in everything
    assert "answered 503" in caplog.text


def test_the_real_transport_refuses_a_non_loopback_host_under_pytest(monkeypatch):
    from services.ai import config
    from services.ai.openai_provider import OpenAIProvider
    configure(monkeypatch, base=None)                     # the default https://api.openai.com/v1
    p = OpenAIProvider(config.load())                     # real UrllibTransport
    with pytest.raises(RuntimeError, match="under pytest"):
        p.generate_structured(schema=SCHEMA, schema_name="x", messages=msgs())


def test_urllib_transport_against_a_loopback_server(monkeypatch):
    """The real transport, base-URL override to 127.0.0.1: exact bytes on the wire, 5xx retry, timeout."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from services.ai import config
    from services.ai.openai_provider import OpenAIProvider
    from services.ai.provider import ProviderError

    seen, plan = [], []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            seen.append((self.path, dict(self.headers), body))
            action = plan.pop(0)
            if action == "sleep":
                time.sleep(1.5)
                return
            status, payload = action
            raw = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("x-request-id", "req_local")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        configure(monkeypatch, base=f"http://127.0.0.1:{server.server_address[1]}/v1")
        cfg = config.load()
        plan[:] = [(500, {"error": {"type": "server_error"}}),
                   (200, json.loads(responses_ok(GOOD).body))]
        p = OpenAIProvider(cfg, sleep=lambda s: None)
        out = p.generate_structured(schema=SCHEMA, schema_name="x", messages=msgs("مرحبا"))
        assert out.data == GOOD and out.meta.attempts == 2 and out.meta.provider_request_id == "req_local"
        path, headers, body = seen[-1]
        assert path == "/v1/responses"
        assert headers["Authorization"] == f"Bearer {TEST_KEY}" and headers["Content-Type"] == "application/json"
        sent = json.loads(body)
        assert sent["store"] is False and sent["model"] == "gpt-test-text"
        assert sent["text"]["format"]["type"] == "json_schema" and sent["text"]["format"]["strict"] is True
        # a socket timeout is reported, and not retried
        import dataclasses
        short = dataclasses.replace(cfg, timeouts={**cfg.timeouts, "generate_structured": 0.3})
        plan[:] = ["sleep"]
        n = len(seen)
        with pytest.raises(ProviderError) as e:
            OpenAIProvider(short, sleep=lambda s: None).generate_structured(schema=SCHEMA, schema_name="x",
                                                                            messages=msgs())
        assert e.value.code == "provider_timeout" and len(seen) == n + 1
    finally:
        server.shutdown()
        server.server_close()
