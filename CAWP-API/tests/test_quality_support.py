"""Shared fixtures for the Phase 6 Q2 quality-workspace tests (tests/test_quality_*.py):
`from test_quality_support import *`. One smoke test of its own.

* `qdb`      tests/fixtures/seed_quality.sql on top of seed.sql (+ the Q0 AI seed);
* `qc_app`   a Flask app with the real `api.quality` blueprint (no full app.py);
* `qc`       a QClient (JSON / multipart helpers, Idempotency-Key per mutation);
* `storage`  a temporary QUALITY_STORAGE_DIR for every test (never the real uploads dir);
* `fake`     the Q0 FakeTransport installed as the provider transport (no real provider call ever);
* audio builders: `wav_bytes()` (the `wave` module, unique random samples, a few KB) and
  `mp3_bytes()` (synthetic MPEG-1 Layer III frames: headers are real, payload is zeros).

Run: docker compose --profile test run --rm -e TEST_DB_PREFIX=cawp_test_ai2 api-test tests/test_quality_*.py
"""
from __future__ import annotations

import io
import os
import uuid
import wave
from pathlib import Path

import pytest

from conftest import MAIN_DB, db_url, run_sql_file
from test_ai_helpers import (FakeTransport, Sleeps, ai, ai_env, configure, connect, insert_policy, q1, qall,  # noqa: F401
                             reset_ai_state, responses_ok, run, transcription_ok)

SEED = Path(__file__).resolve().parent / "fixtures" / "seed_quality.sql"


class Q:
    role_rev = "a2a2a2a2-0000-4000-8000-000000000201"
    role_app = "a2a2a2a2-0000-4000-8000-000000000202"
    rev_a = "a2a2a2a2-0000-4000-8000-000000000501"
    app_a = "a2a2a2a2-0000-4000-8000-000000000502"
    rev_b = "a2a2a2a2-0000-4000-8000-000000000503"
    none = "a2a2a2a2-0000-4000-8000-000000000504"
    col_a = "a2a2a2a2-0000-4000-8000-000000000505"
    contract_a = "a2a2a2a2-0000-4000-8000-000000000301"
    contract_b = "a2a2a2a2-0000-4000-8000-000000000302"
    KA1 = "a2a2a2a2-0000-4000-8000-00000000c001"
    KB1 = "a2a2a2a2-0000-4000-8000-00000000c002"
    KA1_NAME = "خالد عبدالله العتيبي"
    KA1_ID = "1076543219"
    KA1_PHONE = "0551234567"
    KA1_ACCOUNT = "Q2ACC-7788990011"


CRITERIA = ["intro", "identity", "debt", "affordability", "listening", "alternatives", "next_steps", "notes",
            "action", "delivery"]


# ------------------------------------------------------------------------------ audio builders
def wav_bytes(seconds: float = 1.0, rate: int = 8000, channels: int = 1, sampwidth: int = 2) -> bytes:
    frames = int(seconds * rate)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(sampwidth)
        w.setframerate(rate)
        w.writeframes(os.urandom(frames * channels * sampwidth))
    return buf.getvalue()


def mp3_bytes(frames: int = 40, id3: bool = True, mono: bool = True) -> bytes:
    """MPEG-1 Layer III, 128 kbps, 44.1 kHz: 417-byte frames (1152 samples each)."""
    header = bytes([0xFF, 0xFB, 0x90, 0xC4 if mono else 0x04])
    body = bytearray()
    if id3:
        body += b"ID3\x04\x00\x00\x00\x00\x00\x0a" + b"\x00" * 10
    marker = uuid.uuid4().bytes
    for i in range(frames):
        payload = bytearray(417 - 4)
        payload[:16] = marker
        payload[16] = i % 256
        body += header + payload
    return bytes(body)


# ------------------------------------------------------------------------------ fixtures
@pytest.fixture(scope="session")
def qdb(ai):
    run_sql_file(MAIN_DB, SEED)
    return True


@pytest.fixture(scope="session")
def qc_app(qdb):
    from flask import Flask
    from sqlalchemy.pool import NullPool

    from core.serialization import ORJSONProvider
    from extensions import db, jwt

    app = Flask("cawp_test_quality")
    app.config.update(TESTING=True, SQLALCHEMY_DATABASE_URI=db_url(MAIN_DB, "psycopg2"),
                      SQLALCHEMY_ENGINE_OPTIONS={"poolclass": NullPool}, SQLALCHEMY_TRACK_MODIFICATIONS=False,
                      JWT_SECRET_KEY=os.environ["JWT_SECRET_KEY"])
    app.json = ORJSONProvider(app)
    db.init_app(app)
    jwt.init_app(app)
    import api.quality
    app.register_blueprint(api.quality.bp)
    return app


@pytest.fixture(autouse=True)
def storage(tmp_path, monkeypatch):
    root = tmp_path / "private" / "quality"
    monkeypatch.setenv("QUALITY_STORAGE_DIR", str(root))
    return root


@pytest.fixture
def pgq(qdb):
    conn = connect()
    yield conn
    conn.close()


@pytest.fixture
def fake(qc_app):
    t, sleeps = FakeTransport(), Sleeps()
    qc_app.config["AI_PROVIDER_TRANSPORT"] = t
    qc_app.config["AI_PROVIDER_SLEEP"] = sleeps
    yield t
    qc_app.config.pop("AI_PROVIDER_TRANSPORT", None)
    qc_app.config.pop("AI_PROVIDER_SLEEP", None)


class QClient:
    def __init__(self, app):
        self.app = app
        self.c = app.test_client()

    def headers(self, user, idem=True, **extra):
        from flask_jwt_extended import create_access_token
        with self.app.app_context():
            token = create_access_token(identity=str(user))
        h = {"Authorization": f"Bearer {token}", **extra}
        if idem:
            h["Idempotency-Key"] = idem if isinstance(idem, str) else str(uuid.uuid4())
        return h

    def get(self, path, user, **headers):
        return self.c.get(path, headers=self.headers(user, idem=False, **headers))

    def head(self, path, user, **headers):
        return self.c.head(path, headers=self.headers(user, idem=False, **headers))

    def send(self, method, path, user, body=None, idem=True):
        return getattr(self.c, method)(path, json=body if body is not None else {}, headers=self.headers(user, idem))

    def post(self, path, user, body=None, idem=True):
        return self.send("post", path, user, body, idem)

    def put(self, path, user, body=None, idem=True):
        return self.send("put", path, user, body, idem)

    def patch(self, path, user, body=None, idem=True):
        return self.send("patch", path, user, body, idem)

    def upload(self, user, data: bytes, filename="recording.wav", idem=True, **form):
        payload = {"file": (io.BytesIO(data), filename), **form}
        return self.c.post("/v2/quality/recordings", data=payload, content_type="multipart/form-data",
                           headers=self.headers(user, idem))

    # conveniences ------------------------------------------------------------------
    def new_recording(self, user=Q.rev_a, data=None, **form) -> dict:
        r = self.upload(user, data or wav_bytes(), **form)
        assert r.status_code == 201, r.get_json()
        return r.get_json()["data"]["recording"]

    def detail(self, rec_id, user=Q.rev_a) -> dict:
        r = self.get(f"/v2/quality/recordings/{rec_id}", user)
        assert r.status_code == 200, r.get_json()
        return r.get_json()["data"]

    def evidence(self, rec_id, user=Q.rev_a, **fields) -> dict:
        rev = self.detail(rec_id, user)["revision"]
        r = self.patch(f"/v2/quality/recordings/{rec_id}", user, {"expected_revision": rev, **fields})
        assert r.status_code == 200, r.get_json()
        return r.get_json()["data"]

    def transcript(self, rec_id, user=Q.rev_a, expect=200, **body) -> dict:
        rev = self.detail(rec_id, user)["revision"]
        r = self.put(f"/v2/quality/recordings/{rec_id}/transcript", user, {"expected_revision": rev, **body})
        assert r.status_code == expect, r.get_json()
        return r.get_json()["data"] if expect == 200 else r.get_json()

    def review(self, rec_id, results, user=Q.rev_a, expect=200, **body) -> dict:
        rev = self.detail(rec_id, user)["revision"]
        r = self.put(f"/v2/quality/recordings/{rec_id}/review", user,
                     {"expected_revision": rev, "results": results, **body})
        assert r.status_code == expect, r.get_json()
        return r.get_json()["data"] if expect == 200 else r.get_json()

    def approve(self, rec_id, user=Q.app_a, expect=200, **body) -> dict:
        rev = self.detail(rec_id, user)["revision"]
        r = self.post(f"/v2/quality/recordings/{rec_id}/approve", user,
                      {"expected_revision": rev, "confirmed": True, **body})
        assert r.status_code == expect, r.get_json()
        return r.get_json()["data"] if expect == 200 else r.get_json()


@pytest.fixture
def qc(qc_app, pgq):
    from core import lookups
    from core.auth import invalidate_permission_maps
    invalidate_permission_maps()
    lookups.invalidate()
    reset_ai_state(pgq)
    return QClient(qc_app)


TRANSCRIPT = [
    {"start": 0.0, "end": 2.0, "speaker": "A", "text": "السلام عليكم، معك سارة من شركة المثال للتحصيل."},
    {"start": 2.0, "end": 3.5, "speaker": "B", "text": "وعليكم السلام."},
    {"start": 3.5, "end": 6.0, "speaker": "A", "text": "هل أتحدث مع خالد عبدالله العتيبي؟ رقمك 0551234567."},
    {"start": 6.0, "end": 7.0, "speaker": "B", "text": "نعم، تفضلي."},
    {"start": 7.0, "end": 9.0, "speaker": "B", "text": "أستطيع السداد يوم الأحد القادم."},
]


def all_results(result="insufficient_evidence", reason="No evidence either way in this call.", **per) -> list:
    """One item per Collections v1 criterion; `per` overrides by criterion id."""
    out = []
    for cid in CRITERIA:
        item = {"criterion_id": cid, "result": result, "reason": reason, "evidence": []}
        item.update(per.get(cid, {}))
        out.append(item)
    return out


def quote(text, *segment_ids) -> dict:
    return {"type": "quote", "text": text, "segment_ids": list(segment_ids)}


def audio_ts(start, end=None, reviewed=True) -> dict:
    return {"type": "audio", "start": start, "end": end, "human_reviewed": reviewed}


def ready_recording(qc, *, user=Q.rev_a, notes=None, action=None, **form) -> dict:
    """Imported, template chosen, manual diarized transcript saved."""
    rec = qc.new_recording(user=user, data=wav_bytes(seconds=10.0), **form)
    fields = {"template": {"key": "collections-v1", "version": 1}}
    if notes is not None:
        fields["notes"] = notes
    if action is not None:
        fields["recorded_action"] = action
    qc.evidence(rec["id"], user, **fields)
    return qc.transcript(rec["id"], user, segments=TRANSCRIPT, language="ar")


__all__ = ["CRITERIA", "FakeTransport", "Q", "QClient", "TRANSCRIPT", "ai", "ai_env", "all_results", "audio_ts",
           "configure", "connect", "fake", "insert_policy", "mp3_bytes", "pgq", "q1", "qall", "qc", "qc_app", "qdb",
           "quote", "ready_recording", "reset_ai_state", "responses_ok", "run", "storage", "transcription_ok",
           "wav_bytes"]


def test_quality_seed_and_routes(qc, pgq):
    assert q1(pgq, "SELECT count(*) FROM users WHERE user_name LIKE 'q2.%%'")[0] == 5
    tpl = q1(pgq, "SELECT criteria FROM qc_templates WHERE template_key = 'collections-v1' AND version = 1")[0]
    assert [c["id"] for c in tpl] == CRITERIA
    rules = {r.rule for r in qc.app.url_map.iter_rules()}
    assert "/v2/quality/recordings/<uuid:rec_id>/approve" in rules and "/v2/quality/dashboard" in rules
