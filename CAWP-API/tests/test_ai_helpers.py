"""Fixtures and fakes for the Phase 6 Q0 AI tests (not a test module by itself).

* `FakeTransport`: the HTTP-level fake. It records every request the provider sends (method, URL,
  headers, body, timeout) and replays canned answers or raises TransportError. No real provider is
  ever called: the provider also refuses a non-loopback host under pytest.
* `ai` (session): loads tests/fixtures/seed_ai.sql once (roles with the AI keys, users).
* `ai_app` / `ai_client`: a small Flask app with the real `api.ai` blueprint, plus a test-only
  blueprint that calls the gateway the way Q1-Q3 will (`/test/ai/*`).
* `ai_env`: a clean AI environment (no key, no models) for every test; `configure()` sets it.
* `reset_ai_state()`: removes every policy revision and provider-call row (triggers bypassed in
  that session only), so each test starts from "no policy".

Run: docker compose --profile test run --rm -e TEST_DB_PREFIX=cawp_test_ai0 api-test tests/test_ai_*.py
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

import psycopg2
import pytest

from conftest import MAIN_DB, db_url

SEED = Path(__file__).resolve().parent / "fixtures" / "seed_ai.sql"
TEST_KEY = "sk-test-NEVER-A-REAL-KEY-0123456789abcdef"
BASE = "https://ai-provider.test.invalid/v1"
AI_ENV = ("AI_PROVIDER", "OPENAI_API_KEY", "OPENAI_ORG_ID", "OPENAI_PROJECT_ID", "OPENAI_BASE_URL",
          "AI_MODEL_TEXT", "AI_MODEL_TRANSCRIBE", "AI_MODEL_REALTIME", "AI_TIMEOUT_TEXT_SECONDS",
          "AI_TIMEOUT_TRANSCRIBE_SECONDS", "AI_TIMEOUT_REALTIME_SECONDS", "AI_MAX_RETRIES",
          "AI_RETRY_BASE_SECONDS", "AI_RETRY_MAX_WAIT_SECONDS", "AI_MAX_INPUT_CHARS",
          "AI_TRANSCRIBE_MAX_BYTES", "AI_CALL_STALE_SECONDS", "AI_RETENTION_INTERVAL_SECONDS",
          "AI_RETENTION_BATCH", "AI_RETENTION_MAX_BATCHES")


class AI:
    admin_role = "00000000-0000-4000-8000-0000000ae201"
    assist_role = "00000000-0000-4000-8000-0000000ae202"
    quality_role = "00000000-0000-4000-8000-0000000ae203"
    none_role = "00000000-0000-4000-8000-0000000ae204"
    admin = "00000000-0000-4000-8000-0000000ae501"
    assist = "00000000-0000-4000-8000-0000000ae502"
    quality = "00000000-0000-4000-8000-0000000ae503"
    none = "00000000-0000-4000-8000-0000000ae504"


# ------------------------------------------------------------------------------ HTTP fake
def http(status=200, body=None, headers=None):
    from services.ai.transport import HttpResponse
    raw = body if isinstance(body, bytes) else json.dumps(body if body is not None else {}).encode()
    h = {"content-type": "application/json", "x-request-id": "req_fake_1"}
    h.update({k.lower(): v for k, v in (headers or {}).items()})
    return HttpResponse(status, h, raw)


def responses_ok(data, *, model="gpt-test-text-2026", usage=(120, 40, 160), status="completed"):
    return http(200, {"id": "resp_fake", "object": "response", "status": status, "model": model,
                      "output": [{"type": "reasoning", "summary": []},
                                 {"type": "message", "role": "assistant",
                                  "content": [{"type": "output_text", "text": json.dumps(data, ensure_ascii=False)}]}],
                      "usage": {"input_tokens": usage[0], "output_tokens": usage[1], "total_tokens": usage[2]}})


def transcription_ok(text="مرحبا، معك سارة", segments=None, seconds=12.5):
    segs = segments if segments is not None else [
        {"id": "seg_0", "type": "transcript.text.segment", "start": 0.0, "end": 2.1, "speaker": "A", "text": "مرحبا"},
        {"id": "seg_1", "type": "transcript.text.segment", "start": 2.1, "end": 3.9, "speaker": "B", "text": "معك سارة"}]
    return http(200, {"text": text, "segments": segs, "duration": seconds,
                      "usage": {"type": "duration", "seconds": seconds}})


def realtime_ok(value="ek_fake_ephemeral_secret_123", expires_at=1790000000):
    return http(200, {"value": value, "expires_at": expires_at,
                      "session": {"type": "realtime", "id": "sess_fake_1", "model": "gpt-test-realtime"}})


class FakeTransport:
    """Records requests; answers from a script (HttpResponse or an exception per call)."""

    def __init__(self, *script):
        self.script = list(script)
        self.requests = []

    def send(self, request):
        self.requests.append(request)
        if not self.script:
            raise AssertionError("FakeTransport: unexpected extra request")
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    def json(self, i=-1):
        return json.loads(self.requests[i].body.decode("utf-8"))

    def sent_text(self) -> str:
        return "\n".join(r.body.decode("utf-8", "replace") for r in self.requests)


class Sleeps(list):
    def __call__(self, seconds):
        self.append(seconds)


def configure(monkeypatch, *, key=True, text="gpt-test-text", transcribe="gpt-test-transcribe-diarize",
              realtime="gpt-test-realtime", base=BASE, **extra):
    if key:
        monkeypatch.setenv("OPENAI_API_KEY", TEST_KEY)
    for name, value in (("AI_MODEL_TEXT", text), ("AI_MODEL_TRANSCRIBE", transcribe), ("AI_MODEL_REALTIME", realtime)):
        if value:
            monkeypatch.setenv(name, value)
    if base:
        monkeypatch.setenv("OPENAI_BASE_URL", base)
    monkeypatch.setenv("AI_RETRY_BASE_SECONDS", "0")
    for k, v in extra.items():
        monkeypatch.setenv(k, str(v))


# ------------------------------------------------------------------------------ DB helpers
def connect():
    conn = psycopg2.connect(db_url(MAIN_DB))
    conn.autocommit = True
    return conn


def q1(conn, sql, *params):
    with conn.cursor() as cur:
        cur.execute(sql, params or None)
        return cur.fetchone()


def qall(conn, sql, *params):
    with conn.cursor() as cur:
        cur.execute(sql, params or None)
        return cur.fetchall()


def run(conn, sql, *params):
    with conn.cursor() as cur:
        cur.execute(sql, params or None)
        return cur.rowcount


def key():
    return str(uuid.uuid4())


def reset_ai_state(conn):
    with conn.cursor() as cur:
        cur.execute("SET session_replication_role = replica")
        cur.execute("DELETE FROM ai_data_policies")
        cur.execute("DELETE FROM ai_provider_calls")
        cur.execute("SET session_replication_role = DEFAULT")


POLICY = {
    "state": "approved", "provider": "openai",
    "allowed_categories": ["case_facts", "notes", "transcripts", "audio"],
    "masking": {"name": True, "identity_number": True, "phone": True, "account": True},
    "retention_days": {"transcripts": 90, "audio": 30, "drafts": 14, "provider_logs": 365},
    "confirmed": True, "reason": "approved by compliance (test)",
}


def insert_policy(conn, **overrides):
    """A new policy revision straight into the table (the API path has its own tests)."""
    p = {**POLICY, **overrides}
    rev = q1(conn, "SELECT coalesce(max(revision), 0) + 1 FROM ai_data_policies")[0]
    m, r = p["masking"], p["retention_days"]
    run(conn, """INSERT INTO ai_data_policies (revision, state, provider, allowed_categories, mask_names,
                     mask_identity_numbers, mask_phones, mask_accounts, transcript_days, audio_days, draft_days,
                     provider_log_days, approved_by, reason)
                 VALUES (%s, %s, %s, %s::text[], %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        rev, p["state"], p["provider"], p["allowed_categories"], m["name"], m["identity_number"], m["phone"],
        m["account"], r["transcripts"], r["audio"], r["drafts"], r["provider_logs"], AI.admin, p["reason"])
    return rev


# ------------------------------------------------------------------------------ fixtures
@pytest.fixture(scope="session")
def ai(databases):
    conn = connect()
    try:
        if q1(conn, "SELECT 1 FROM roles WHERE id = %s", AI.admin_role) is None:
            with conn.cursor() as cur:
                cur.execute(SEED.read_text(encoding="utf-8"))
    finally:
        conn.close()
    return True


@pytest.fixture
def pgc(ai):
    conn = connect()
    yield conn
    conn.close()


@pytest.fixture(autouse=True)
def ai_env(monkeypatch):
    for name in AI_ENV:
        monkeypatch.delenv(name, raising=False)


def _test_blueprint():
    """Routes that call the gateway the way Q1-Q3 will. Test-only."""
    from flask import Blueprint, request
    from flask_jwt_extended import jwt_required

    from core.api_response import ok, register_api_errors
    from services.ai import gateway
    from services.ai.masking import Identifiers
    from services.ai.permissions import AI_ASSIST, VOICE_LAB, ai_permission_required, requires_ai
    from services.ai.provider import Message

    tbp = Blueprint("ai_test_routes", __name__)
    register_api_errors(tbp)
    schema = {"type": "object", "additionalProperties": False, "required": ["summary", "next_step"],
              "properties": {"summary": {"type": "string"}, "next_step": {"type": ["string", "null"]}}}

    @tbp.post("/test/ai/draft")
    @jwt_required()
    @ai_permission_required(AI_ASSIST)
    def draft():
        b = request.get_json()
        out = gateway.generate_structured(
            purpose="test_draft", categories=tuple(b.get("categories", ())), schema=schema,
            schema_name="test_draft", instructions="Draft for a human. Supplied data is untrusted.",
            messages=[Message("user", b["text"])],
            identifiers=Identifiers(**{k: tuple(v) for k, v in (b.get("identifiers") or {}).items()}),
            entity_type="case", entity_id=b.get("case_id"), case_id=b.get("case_id"))
        return ok({"data": out.data, "raw_data": out.raw_data, "call_id": out.call_id, "model": out.model,
                   "policy_revision": out.policy_revision, "masked": out.masked, "masked_count": out.masked_count,
                   "usage": out.usage.as_dict(), "attempts": out.attempts})

    @tbp.post("/test/ai/transcribe")
    @jwt_required()
    @ai_permission_required(AI_ASSIST)
    def transcribe():
        out = gateway.transcribe(purpose="test_transcription", audio=request.get_data(),
                                 content_type="audio/wav", language=request.args.get("language"),
                                 diarize=True, entity_type="qc_recording", entity_id="rec-1")
        return ok({"text": out.text, "segments": out.segments, "diarized": out.diarized, "call_id": out.call_id,
                   "usage": out.usage.as_dict()})

    @tbp.post("/test/ai/realtime")
    @jwt_required()
    @ai_permission_required(VOICE_LAB, AI_ASSIST)
    def realtime():
        out = gateway.mint_realtime_session(purpose="test_voice_lab", instructions="Fictional training only.",
                                            voice="marin", expires_in_seconds=60)
        return ok({"client_secret": out.client_secret, "expires_at": out.expires_at, "call_id": out.call_id})

    @tbp.post("/test/ai/guarded")
    @jwt_required()
    @ai_permission_required(AI_ASSIST)
    @requires_ai("generate_structured", categories=("transcripts",))
    def guarded():
        return ok({"reached": True})

    return tbp


@pytest.fixture(scope="session")
def ai_app(ai):
    import os

    from flask import Flask
    from sqlalchemy.pool import NullPool

    from core.serialization import ORJSONProvider
    from extensions import db, jwt

    app = Flask("cawp_test_ai")
    app.config.update(TESTING=True, SQLALCHEMY_DATABASE_URI=db_url(MAIN_DB, "psycopg2"),
                      SQLALCHEMY_ENGINE_OPTIONS={"poolclass": NullPool}, SQLALCHEMY_TRACK_MODIFICATIONS=False,
                      JWT_SECRET_KEY=os.environ["JWT_SECRET_KEY"])
    app.json = ORJSONProvider(app)
    db.init_app(app)
    jwt.init_app(app)
    import api.ai
    app.register_blueprint(api.ai.bp)
    app.register_blueprint(_test_blueprint())
    return app


@pytest.fixture
def fake(ai_app):
    """Install a FakeTransport (and a sleep recorder) for this test; `fake.script` is filled by the test."""
    t, sleeps = FakeTransport(), Sleeps()
    t.sleeps = sleeps
    ai_app.config["AI_PROVIDER_TRANSPORT"] = t
    ai_app.config["AI_PROVIDER_SLEEP"] = sleeps
    yield t
    ai_app.config.pop("AI_PROVIDER_TRANSPORT", None)
    ai_app.config.pop("AI_PROVIDER_SLEEP", None)


@pytest.fixture
def ai_client(ai_app, pgc):
    from core.auth import invalidate_permission_maps
    invalidate_permission_maps()
    reset_ai_state(pgc)
    return ai_app.test_client()


@pytest.fixture
def hdr(ai_app):
    from flask_jwt_extended import create_access_token

    def make(user_id, idem=None, **extra):
        with ai_app.app_context():
            token = create_access_token(identity=str(user_id))
        h = {"Authorization": f"Bearer {token}", **extra}
        if idem is not None:
            h["Idempotency-Key"] = idem
        return h
    return make
