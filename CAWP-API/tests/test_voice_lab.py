"""Phase 6 Q3: the voice training lab (api/voice_lab.py, services/ai/voice_lab.py,
services/ai/voice_scenarios.py, migration 0064).

Fictional training only: the catalogue, the minted Realtime request (through the Q0 gateway and
the HTTP-level FakeTransport: no real provider is ever contacted), honest refusals, one open
session per user, rate limits, the 5-minute limit, the end record, and a session log without any
content (no audio, transcript, instructions or secret).

Run: docker compose --profile test run --rm -e TEST_DB_PREFIX=cawp_test_ai3 api-test tests/test_voice_lab.py
"""
from __future__ import annotations

import json
import re

import psycopg2
import pytest

from test_ai_helpers import (AI, BASE, TEST_KEY, FakeTransport, Sleeps, ai, ai_env, configure,  # noqa: F401
                             connect, http, insert_policy, key, pgc, q1, qall, realtime_ok, reset_ai_state, run)


class VL:
    role = "00000000-0000-4000-8000-00000000a3a1"
    user = "00000000-0000-4000-8000-00000000a3b1"
    user2 = "00000000-0000-4000-8000-00000000a3b2"


SEED = f"""
INSERT INTO roles (id, name, permission) VALUES
  ('{VL.role}', 'voice lab trainee',
   '{{"ai_admin": false, "ai_assist": false, "quality_review": false, "quality_approve": false, "voice_lab": true}}');
INSERT INTO users (id, email, frist_name, last_name, mobile, password, user_name, role_id, nationality_id, branch_id)
VALUES
  ('{VL.user}', 'vl.one@test.invalid', 'Voice', 'Trainee', '0500000431', 'not-a-real-hash', 'vl.one', '{VL.role}',
   '00000000-0000-4000-8000-000000000101', '00000000-0000-4000-8000-000000000102'),
  ('{VL.user2}', 'vl.two@test.invalid', 'Voice', 'Second', '0500000432', 'not-a-real-hash', 'vl.two', '{VL.role}',
   '00000000-0000-4000-8000-000000000101', '00000000-0000-4000-8000-000000000102');
"""
SECRET = "ek_fake_ephemeral_secret_123"
SESSION_COLUMNS = {"id", "user_id", "scenario_id", "scenario_version", "language", "voice", "state", "outcome",
                   "error_code", "max_seconds", "started_at", "expires_at", "activated_at", "ended_at",
                   "duration_seconds", "connected_seconds", "provider_call_id", "model", "policy_revision",
                   "request_id"}


# ------------------------------------------------------------------------------ fixtures
@pytest.fixture(scope="session")
def vl_seed(ai):
    conn = connect()
    try:
        if q1(conn, "SELECT 1 FROM roles WHERE id = %s", VL.role) is None:
            with conn.cursor() as cur:
                cur.execute(SEED)
    finally:
        conn.close()
    return True


@pytest.fixture(scope="session")
def vl_app(vl_seed):
    import os

    from flask import Flask
    from sqlalchemy.pool import NullPool

    from conftest import MAIN_DB, db_url
    from core.serialization import ORJSONProvider
    from extensions import db, jwt

    app = Flask("cawp_test_voice_lab")
    app.config.update(TESTING=True, SQLALCHEMY_DATABASE_URI=db_url(MAIN_DB, "psycopg2"),
                      SQLALCHEMY_ENGINE_OPTIONS={"poolclass": NullPool}, SQLALCHEMY_TRACK_MODIFICATIONS=False,
                      JWT_SECRET_KEY=os.environ["JWT_SECRET_KEY"])
    app.json = ORJSONProvider(app)
    db.init_app(app)
    jwt.init_app(app)
    import api.ai
    import api.voice_lab
    app.register_blueprint(api.ai.bp)
    app.register_blueprint(api.voice_lab.bp)
    return app


@pytest.fixture
def fake(vl_app):
    t, sleeps = FakeTransport(), Sleeps()
    vl_app.config["AI_PROVIDER_TRANSPORT"] = t
    vl_app.config["AI_PROVIDER_SLEEP"] = sleeps
    yield t
    vl_app.config.pop("AI_PROVIDER_TRANSPORT", None)
    vl_app.config.pop("AI_PROVIDER_SLEEP", None)


def reset_voice_lab(conn):
    reset_ai_state(conn)
    with conn.cursor() as cur:
        cur.execute("DELETE FROM voice_lab_sessions")


@pytest.fixture
def client(vl_app, pgc):
    from core.auth import invalidate_permission_maps
    invalidate_permission_maps()
    reset_voice_lab(pgc)
    return vl_app.test_client()


@pytest.fixture
def hdr(vl_app):
    from flask_jwt_extended import create_access_token

    def make(user_id, idem=None):
        with vl_app.app_context():
            token = create_access_token(identity=str(user_id))
        h = {"Authorization": f"Bearer {token}"}
        if idem:
            h["Idempotency-Key"] = idem
        return h
    return make


def ready(monkeypatch, pgc, **extra):
    configure(monkeypatch, **extra)
    return insert_policy(pgc, allowed_categories=[])     # the voice lab sends no data category (Q0 D3)


def start(client, hdr, user=VL.user, scenario="cooperative", language="ar", **extra):
    r = client.post("/v2/ai/voice-lab/sessions", json={"scenario_id": scenario, "language": language, **extra},
                    headers=hdr(user, key()))
    return r.status_code, r.get_json(), r


def end(client, hdr, sid, user=VL.user, **body):
    r = client.post(f"/v2/ai/voice-lab/sessions/{sid}/end", json=body or {"outcome": "user_ended"},
                    headers=hdr(user, key()))
    return r.status_code, r.get_json()


def sessions(pgc):
    return qall(pgc, "SELECT state, outcome, error_code, scenario_id, language, user_id::text, "
                     "provider_call_id::text, duration_seconds, connected_seconds FROM voice_lab_sessions "
                     "ORDER BY started_at, id")


def provider_calls(pgc):
    return qall(pgc, "SELECT id::text, outcome, capability, purpose, data_categories, entity_type, entity_id, "
                     "actor_user_id::text, model FROM ai_provider_calls ORDER BY started_at, id")


# ------------------------------------------------------------------------------ catalogue
def test_scenarios_need_the_voice_lab_key(client, hdr):
    for user in (AI.none, AI.assist, AI.admin, AI.quality):      # other AI keys do not open the lab
        r = client.get("/v2/ai/voice-lab/scenarios", headers=hdr(user))
        assert (r.status_code, r.get_json()["code"]) == (403, "forbidden")
        assert r.get_json()["error"]["required_any"] == ["voice_lab"]
    assert client.get("/v2/ai/voice-lab/scenarios", headers=hdr(VL.user)).status_code == 200


def test_scenarios_are_a_fictional_bilingual_catalogue(client, hdr):
    r = client.get("/v2/ai/voice-lab/scenarios", headers=hdr(VL.user))
    d = r.get_json()["data"]
    assert r.headers["Cache-Control"].startswith("no-store")
    ids = [s["id"] for s in d["scenarios"]]
    assert {"cooperative", "hardship", "angry_dispute", "wrong_number", "stop_calling"} <= set(ids)
    assert len(ids) == len(set(ids))
    assert d["languages"] == ["ar", "en"] and d["fictional"] is True
    assert d["limits"] == {"max_seconds": 300, "sessions_per_hour": 6, "sessions_per_day": 20}
    assert d["readiness"] == {"ready": False, "reasons": ["ai_not_configured", "model_not_set", "ai_policy_required"]}
    arabic = re.compile("[؀-ۿ]")
    for s in d["scenarios"]:
        assert set(s) == {"id", "version", "difficulty", "persona_name", "title", "summary", "brief", "goals",
                          "fictional"}                      # no persona / facts / behaviour / instructions
        assert s["fictional"] is True and s["difficulty"] in ("easy", "medium", "hard")
        for field in ("persona_name", "title", "summary", "brief"):
            assert arabic.search(s[field]["ar"]) and s[field]["en"] and not arabic.search(s[field]["en"])
        assert len(s["goals"]["ar"]) == len(s["goals"]["en"]) >= 3
        assert "افتراضي" in s["brief"]["ar"] and "fictional" in s["brief"]["en"]


def test_persona_instructions_are_fictional_and_carry_no_identifiers():
    from services.ai import masking
    from services.ai import voice_scenarios as vs
    assert len(vs.SCENARIOS) >= 5
    for sc in vs.SCENARIOS:
        public = json.dumps(sc.public(), ensure_ascii=False)
        assert sc.persona not in public and not any(f in public for f in sc.facts + sc.behaviour)
        for lang in ("ar", "en"):
            text = vs.instructions(sc, lang)
            assert "FICTIONAL" in text and "TRAINING" in text
            assert "Never ask for, repeat or accept real personal data" in text
            assert "at most 5 minutes" in text
            assert sc.persona_name[lang] in text
            assert ("Speak only Arabic" in text) if lang == "ar" else ("Speak only English" in text)
            assert vs.END_PHRASES[lang] in text
            # no phone, identity, IBAN or account-like number anywhere in what the provider receives
            m = masking.Masker()
            assert m.mask(text) == text and m.count == 0
            assert not re.search(r"\d{5,}", text.replace(",", ""))
            summary = vs.summary(sc, lang)
            assert sc.persona_name[lang] in summary and sc.persona not in summary


# ------------------------------------------------------------------------------ refusals
def test_not_configured_refuses_before_anything(client, hdr, pgc, fake, monkeypatch):
    code, body, _ = start(client, hdr)
    assert (code, body["code"]) == (503, "ai_not_configured")
    assert body["error"]["reasons"] == ["ai_not_configured", "model_not_set", "ai_policy_required"]
    configure(monkeypatch, realtime=None)
    code, body, _ = start(client, hdr)
    assert (code, body["code"], body["error"]["reasons"]) == (503, "model_not_set", ["model_not_set", "ai_policy_required"])
    configure(monkeypatch)
    code, body, _ = start(client, hdr)
    assert (code, body["code"], body["error"]["reasons"]) == (409, "ai_policy_required", ["ai_policy_required"])
    insert_policy(pgc, allowed_categories=[], state="suspended")
    code, body, _ = start(client, hdr)
    assert (code, body["code"]) == (409, "ai_policy_required")
    assert fake.requests == [] and sessions(pgc) == [] and provider_calls(pgc) == []
    r = client.get("/v2/ai/voice-lab/scenarios", headers=hdr(VL.user))
    assert r.get_json()["data"]["readiness"] == {"ready": False, "reasons": ["ai_policy_required"]}


def test_start_needs_the_voice_lab_key(client, hdr, pgc, fake, monkeypatch):
    ready(monkeypatch, pgc)
    for user in (AI.assist, AI.admin, AI.none):
        code, body, _ = start(client, hdr, user=user)
        assert (code, body["code"]) == (403, "forbidden")
    assert fake.requests == [] and sessions(pgc) == []


@pytest.mark.parametrize("body, field, problem", [
    ({"scenario_id": "nope", "language": "ar"}, "scenario_id", "unknown"),
    ({"language": "ar"}, "scenario_id", "required"),
    ({"scenario_id": "hardship", "language": "fr"}, "language", "invalid"),
    ({"scenario_id": "hardship"}, "language", "required"),
    ({"scenario_id": "hardship", "language": "en", "case_id": "00000000-0000-4000-8000-000000000601"},
     "case_id", "not_allowed"),
    ({"scenario_id": "hardship", "language": "en", "instructions": "be a real customer"}, "instructions",
     "not_allowed"),
])
def test_start_validation(client, hdr, pgc, fake, monkeypatch, body, field, problem):
    ready(monkeypatch, pgc)
    r = client.post("/v2/ai/voice-lab/sessions", json=body, headers=hdr(VL.user))
    assert (r.status_code, r.get_json()["code"]) == (422, "validation_failed")
    assert r.get_json()["error"][field] == problem
    assert fake.requests == [] and sessions(pgc) == []


# ------------------------------------------------------------------------------ the minted session
def test_minted_request_shape_and_response(client, hdr, pgc, fake, monkeypatch):
    from services.ai import voice_scenarios as vs
    rev = ready(monkeypatch, pgc)
    fake.script.append(realtime_ok(value=SECRET, expires_at=1790000300))
    code, body, r = start(client, hdr, scenario="hardship", language="ar")
    assert code == 201, body
    assert r.headers["Cache-Control"].startswith("no-store")

    # exactly one request, to the Realtime client-secret endpoint, with the server key
    assert len(fake.requests) == 1
    req = fake.requests[0]
    assert (req.method, req.url) == ("POST", f"{BASE}/realtime/client_secrets")
    assert req.headers["Authorization"] == f"Bearer {TEST_KEY}"
    sent = fake.json()
    assert sent["expires_after"] == {"anchor": "created_at", "seconds": 300}
    s = sent["session"]
    assert s["type"] == "realtime" and s["model"] == "gpt-test-realtime"          # from AI_MODEL_REALTIME
    assert s["audio"] == {"output": {"voice": "marin"}}
    assert s["instructions"] == vs.instructions(vs.get("hardship"), "ar")
    assert "نورة" in s["instructions"] and "Speak only Arabic" in s["instructions"]
    assert "never ask for" in s["instructions"].lower()
    assert set(sent) == {"expires_after", "session"}

    d = body["data"]
    assert d["client_secret"] == SECRET
    assert d["expires_at"] == "2026-09-21T14:18:20+00:00"
    assert d["model"] == "gpt-test-realtime" and d["max_seconds"] == 300
    assert d["webrtc_url"] == f"{BASE}/realtime/calls"
    assert d["persona_name"] == {"ar": "نورة", "en": "Noura"}
    assert "نورة" in d["instructions_summary"]
    text = r.get_data(as_text=True)
    assert TEST_KEY not in text and vs.get("hardship").persona not in text and "# Rules" not in text

    sess = d["session"]
    assert (sess["state"], sess["scenario_id"], sess["language"], sess["user_id"]) == ("active", "hardship", "ar", VL.user)
    assert sess["policy_revision"] == rev and sess["model"] == "gpt-test-realtime"
    calls = provider_calls(pgc)
    assert len(calls) == 1
    call_id, outcome, cap, purpose, cats, etype, eid, actor, model = calls[0]
    assert (outcome, cap, purpose, cats, etype, eid, actor) == (
        "succeeded", "realtime_session", "voice_lab", [], "voice_lab_session", sess["id"], VL.user)
    assert sess["provider_call_id"] == call_id
    assert sessions(pgc) == [("active", None, None, "hardship", "ar", VL.user, call_id, None, None)]


def test_limits_come_from_the_environment_but_never_exceed_five_minutes(client, hdr, pgc, fake, monkeypatch):
    ready(monkeypatch, pgc, VOICE_LAB_MAX_SECONDS=120, VOICE_LAB_VOICE="alloy")
    fake.script.append(realtime_ok())
    code, body, _ = start(client, hdr, scenario="angry_dispute", language="en")
    assert code == 201 and body["data"]["max_seconds"] == 120
    assert fake.json()["expires_after"]["seconds"] == 120
    assert fake.json()["session"]["audio"]["output"]["voice"] == "alloy"
    assert "at most 2 minutes" in fake.json()["session"]["instructions"]
    assert end(client, hdr, body["data"]["session"]["id"])[0] == 200

    monkeypatch.setenv("VOICE_LAB_MAX_SECONDS", "900")         # above the hard limit: ignored
    fake.script.append(realtime_ok())
    code, body, _ = start(client, hdr, scenario="angry_dispute", language="en")
    assert code == 201 and body["data"]["max_seconds"] == 300
    assert fake.json()["expires_after"]["seconds"] == 300


def test_one_open_session_per_user(client, hdr, pgc, fake, monkeypatch):
    ready(monkeypatch, pgc)
    fake.script += [realtime_ok(), realtime_ok(value="ek_second_user"), realtime_ok(value="ek_again")]
    code, body, _ = start(client, hdr)
    assert code == 201
    first = body["data"]["session"]["id"]
    code, body, _ = start(client, hdr, scenario="wrong_number", language="en")
    assert (code, body["code"]) == (409, "voice_lab_session_active")
    assert body["error"]["session_id"] == first and body["error"]["state"] == "active"
    assert len(fake.requests) == 1                               # nothing minted for the refusal
    code, body, _ = start(client, hdr, user=VL.user2)            # another user is independent
    assert code == 201 and body["data"]["client_secret"] == "ek_second_user"
    assert end(client, hdr, first)[0] == 200
    code, body, _ = start(client, hdr)
    assert code == 201 and body["data"]["client_secret"] == "ek_again"


def test_the_slot_is_held_while_the_secret_is_minted(client, hdr, pgc, fake, monkeypatch):
    """A second Start while the first one is still waiting for the provider is refused (the
    `minting` row is committed before the provider call); ending it then abandons the mint and
    the secret is never handed out."""
    import threading
    ready(monkeypatch, pgc)
    gate, entered = threading.Event(), threading.Event()

    class SlowTransport(FakeTransport):
        def send(self, request):
            entered.set()
            assert gate.wait(10)
            return super().send(request)

    slow = SlowTransport(realtime_ok())
    client.application.config["AI_PROVIDER_TRANSPORT"] = slow
    result = {}
    t = threading.Thread(target=lambda: result.update(r=start(client, hdr)))
    t.start()
    assert entered.wait(10)
    code, body, _ = start(client, hdr, scenario="hardship")
    assert (code, body["code"], body["error"]["state"]) == (409, "voice_lab_session_active", "minting")
    sid = body["error"]["session_id"]
    code, body = end(client, hdr, sid, outcome="navigated_away")        # the user left during the mint
    assert code == 200 and body["data"]["session"]["state"] == "failed"
    assert (body["data"]["session"]["outcome"], body["data"]["session"]["error_code"]) == ("mint_failed", "abandoned")
    gate.set()
    t.join(10)
    code, body, _ = result["r"]
    assert (code, body["code"]) == (409, "voice_lab_session_expired")
    assert "client_secret" not in json.dumps(body)
    assert [r[1] for r in provider_calls(pgc)] == ["succeeded"]          # the provider call itself is audited


def test_a_stale_open_session_expires_with_an_unknown_duration(client, hdr, pgc, fake, monkeypatch):
    ready(monkeypatch, pgc)
    fake.script += [realtime_ok(), realtime_ok()]
    first = start(client, hdr)[1]["data"]["session"]["id"]
    with pgc.cursor() as cur:            # the tab was closed: nobody reported the end
        cur.execute("SET session_replication_role = replica")
        cur.execute("UPDATE voice_lab_sessions SET expires_at = now() - interval '1 second', "
                    "started_at = now() - interval '10 minutes' WHERE id = %s", (first,))
        cur.execute("SET session_replication_role = DEFAULT")
    code, body, _ = start(client, hdr)
    assert code == 201
    row = q1(pgc, "SELECT state, outcome, duration_seconds, ended_at = expires_at FROM voice_lab_sessions "
                  "WHERE id = %s", first)
    assert row == ("expired", "expired", None, True)
    code, body = end(client, hdr, first)                          # a late end changes nothing
    assert code == 200 and body["data"]["changed"] is False and body["data"]["session"]["state"] == "expired"


def test_rate_limit_per_hour(client, hdr, pgc, fake, monkeypatch):
    ready(monkeypatch, pgc, VOICE_LAB_SESSIONS_PER_HOUR=2)
    fake.script += [realtime_ok(), realtime_ok()]
    for _ in range(2):
        code, body, _ = start(client, hdr)
        assert code == 201
        assert end(client, hdr, body["data"]["session"]["id"])[0] == 200
    code, body, _ = start(client, hdr)
    assert (code, body["code"]) == (429, "voice_lab_rate_limited")
    e = body["error"]
    assert e["limit"] == "per_hour" and e["last_hour"] == 2 and e["sessions_per_hour"] == 2
    assert 3500 <= e["retry_after_seconds"] <= 3601
    assert len(fake.requests) == 2 and len(sessions(pgc)) == 2


def test_rate_limit_per_day_counts_provider_failures(client, hdr, pgc, fake, monkeypatch):
    ready(monkeypatch, pgc, VOICE_LAB_SESSIONS_PER_DAY=1, AI_MAX_RETRIES=0)
    fake.script.append(http(500, {"error": {"type": "server_error"}}))
    code, body, _ = start(client, hdr)
    assert (code, body["code"]) == (502, "ai_provider_unavailable")
    assert body["error"]["call_id"]
    assert sessions(pgc) == [("failed", "mint_failed", "ai_provider_unavailable", "cooperative", "ar", VL.user,
                              None, 0, None)]
    code, body, _ = start(client, hdr)                           # the failed mint used the day's session
    assert (code, body["code"], body["error"]["limit"]) == (429, "voice_lab_rate_limited", "per_day")
    assert body["error"]["retry_after_seconds"] <= 86401


def test_a_provider_failure_frees_the_slot(client, hdr, pgc, fake, monkeypatch):
    ready(monkeypatch, pgc, AI_MAX_RETRIES=0)
    fake.script += [http(503, {"error": {"type": "server_error"}}), realtime_ok()]
    assert start(client, hdr)[0] == 502
    code, body, _ = start(client, hdr)
    assert code == 201 and body["data"]["session"]["state"] == "active"
    assert [r[1] for r in provider_calls(pgc)] == ["failed", "succeeded"]


def test_mints_count_against_the_gateway_daily_quota(client, hdr, pgc, fake, monkeypatch):
    from services.ai import gateway
    if not hasattr(gateway, "quota_usage"):
        pytest.skip("the gateway has no daily quota yet (Q1)")
    ready(monkeypatch, pgc, AI_DAILY_CALLS_PER_USER=1)
    fake.script.append(realtime_ok())
    code, body, _ = start(client, hdr)
    assert code == 201
    end(client, hdr, body["data"]["session"]["id"])
    code, body, _ = start(client, hdr)                           # fail fast (requires_ai): nothing written
    assert (code, body["code"]) == (429, "ai_quota_exceeded")
    assert len(sessions(pgc)) == 1 and len(fake.requests) == 1
    # a quota refusal at call time (the cap was reached between the check and the call) is a refused
    # mint: the slot is freed and it does not count against the voice lab's own limits
    from services.ai.gateway import readiness
    monkeypatch.setattr(gateway, "ensure_ready", lambda cap, cats=(): readiness(cap, cats))
    code, body, _ = start(client, hdr)
    assert (code, body["code"]) == (429, "ai_quota_exceeded")
    assert sessions(pgc)[-1][:3] == ("failed", "mint_refused", "ai_quota_exceeded")
    u = client.get("/v2/ai/voice-lab/sessions", headers=hdr(VL.user)).get_json()["data"]
    assert u["active"] is None and u["usage"]["last_hour"] == 1
    assert len(fake.requests) == 1


# ------------------------------------------------------------------------------ end
def test_end_records_outcome_and_duration_only(client, hdr, pgc, fake, monkeypatch):
    ready(monkeypatch, pgc)
    fake.script.append(realtime_ok())
    sid = start(client, hdr)[1]["data"]["session"]["id"]
    r = client.post(f"/v2/ai/voice-lab/sessions/{sid}/end", json={"outcome": "user_ended", "transcript": "hello"},
                    headers=hdr(VL.user, key()))
    assert (r.status_code, r.get_json()["error"]) == (422, {"transcript": "not_allowed"})
    for bad in ({"outcome": "great_call"}, {"outcome": "user_ended", "connected_seconds": 99999},
                {"outcome": "user_ended", "connected_seconds": "12"}, {"connected_seconds": True}):
        assert client.post(f"/v2/ai/voice-lab/sessions/{sid}/end", json=bad, headers=hdr(VL.user)).status_code == 422
    code, body = end(client, hdr, sid, user=VL.user2)            # someone else's session
    assert (code, body["code"]) == (404, "not_found")
    code, body = end(client, hdr, sid, outcome="time_limit", connected_seconds=400)
    assert code == 200 and body["data"]["changed"] is True
    s = body["data"]["session"]
    assert (s["state"], s["outcome"], s["connected_seconds"]) == ("ended", "time_limit", 300)   # clamped
    assert 0 <= s["duration_seconds"] <= 300 and s["ended_at"]
    code, body = end(client, hdr, sid, outcome="error")          # idempotent: already final
    assert code == 200 and body["data"]["changed"] is False and body["data"]["session"]["outcome"] == "time_limit"
    # the end without a body (navigation / keepalive) defaults to user_ended
    fake.script.append(realtime_ok())
    sid2 = start(client, hdr)[1]["data"]["session"]["id"]
    r = client.post(f"/v2/ai/voice-lab/sessions/{sid2}/end", headers=hdr(VL.user))
    assert r.status_code == 200 and r.get_json()["data"]["session"]["outcome"] == "user_ended"
    ev = qall(pgc, "SELECT action FROM audit_events WHERE entity_type = 'voice_lab_session' AND entity_id = %s "
                   "ORDER BY occurred_at, id", sid)
    assert [e[0] for e in ev] == ["voice_lab.session.start", "voice_lab.session.end"]


def test_my_sessions_list(client, hdr, pgc, fake, monkeypatch):
    ready(monkeypatch, pgc)
    fake.script += [realtime_ok(), realtime_ok()]
    first = start(client, hdr, scenario="stop_calling", language="en")[1]["data"]["session"]["id"]
    end(client, hdr, first, outcome="navigated_away", connected_seconds=12)
    second = start(client, hdr, scenario="callback_request")[1]["data"]["session"]["id"]
    r = client.get("/v2/ai/voice-lab/sessions?page_size=10", headers=hdr(VL.user))
    d = r.get_json()["data"]
    assert r.headers["Cache-Control"].startswith("no-store")
    assert [i["id"] for i in d["items"]] == [second, first] and d["total"] == 2
    assert d["items"][1]["outcome"] == "navigated_away" and d["items"][1]["connected_seconds"] == 12
    assert d["items"][1]["scenario_title"]["en"] == "Request to stop calling"
    assert d["active"]["id"] == second
    assert d["usage"] == {"last_hour": 2, "today": 2, "sessions_per_hour": 6, "sessions_per_day": 20}
    other = client.get("/v2/ai/voice-lab/sessions", headers=hdr(VL.user2)).get_json()["data"]
    assert other["items"] == [] and other["active"] is None
    assert "client_secret" not in r.get_data(as_text=True)


# ------------------------------------------------------------------------------ no content
def test_the_log_contains_no_content(client, hdr, pgc, fake, monkeypatch):
    from services.ai import voice_scenarios as vs
    ready(monkeypatch, pgc)
    fake.script.append(realtime_ok(value="ek_UNIQUE_VOICE_SECRET_7731"))
    sid = start(client, hdr, scenario="angry_dispute", language="en")[1]["data"]["session"]["id"]
    end(client, hdr, sid, outcome="user_ended", connected_seconds=61)
    cols = {r[0] for r in qall(pgc, "SELECT column_name FROM information_schema.columns "
                                    "WHERE table_name = 'voice_lab_sessions'")}
    assert cols == SESSION_COLUMNS
    dump = json.dumps([
        qall(pgc, "SELECT to_jsonb(v) FROM voice_lab_sessions v"),
        qall(pgc, "SELECT to_jsonb(c) FROM ai_provider_calls c"),
        qall(pgc, "SELECT to_jsonb(a) FROM audit_events a WHERE entity_type = 'voice_lab_session'"),
        qall(pgc, "SELECT to_jsonb(k) FROM idempotency_keys k WHERE route LIKE '/v2/ai/voice-lab/%%'"),
    ], ensure_ascii=False, default=str)
    sc = vs.get("angry_dispute")
    for needle in ("ek_UNIQUE_VOICE_SECRET_7731", TEST_KEY, sc.persona, sc.behaviour[0], "# Rules",
                   "Never ask for"):
        assert needle not in dump, needle
    assert sid in dump and "angry_dispute" in dump            # the metadata is there


# ------------------------------------------------------------------------------ the database guard
def test_db_guard_and_one_open_row(pgc):
    reset_voice_lab(pgc)
    ins = ("INSERT INTO voice_lab_sessions (user_id, scenario_id, scenario_version, language, max_seconds, "
           "expires_at, state) VALUES (%s, 'cooperative', 1, 'ar', 300, now() + interval '6 minutes', %s) RETURNING id")
    with pytest.raises(psycopg2.Error, match="starts as minting"):
        q1(pgc, ins, VL.user, "active")
    sid = q1(pgc, ins, VL.user, "minting")[0]
    with pytest.raises(psycopg2.errors.UniqueViolation):
        q1(pgc, ins, VL.user, "minting")
    with pytest.raises(psycopg2.Error, match="invalid voice lab session transition"):
        run(pgc, "UPDATE voice_lab_sessions SET state = 'ended', outcome = 'user_ended', ended_at = now() "
                 "WHERE id = %s", sid)
    with pytest.raises(psycopg2.Error, match="immutable"):
        run(pgc, "UPDATE voice_lab_sessions SET scenario_id = 'hardship' WHERE id = %s", sid)
    run(pgc, "UPDATE voice_lab_sessions SET state = 'failed', outcome = 'mint_failed', ended_at = now() WHERE id = %s", sid)
    with pytest.raises(psycopg2.Error, match="finished voice lab session is immutable"):
        run(pgc, "UPDATE voice_lab_sessions SET duration_seconds = 5 WHERE id = %s", sid)
    with pytest.raises(psycopg2.errors.CheckViolation):
        q1(pgc, ins.replace("300", "600"), VL.user, "minting")     # above the 5-minute limit
    reset_voice_lab(pgc)


def test_blueprint_is_registered_by_the_api_registry(vl_seed):
    from flask import Flask

    import api
    app = Flask("cawp_test_voice_lab_registry")
    api.register_blueprints(app)
    rules = sorted((r.rule, tuple(sorted(r.methods - {"HEAD", "OPTIONS"})))
                   for r in app.url_map.iter_rules() if r.endpoint.startswith("voice_lab."))
    assert rules == [("/v2/ai/voice-lab/scenarios", ("GET",)),
                     ("/v2/ai/voice-lab/sessions", ("GET",)),
                     ("/v2/ai/voice-lab/sessions", ("POST",)),
                     ("/v2/ai/voice-lab/sessions/<uuid:session_id>/end", ("POST",))]
    all_rules = [(r.rule, m) for r in app.url_map.iter_rules() for m in r.methods - {"HEAD", "OPTIONS"}]
    assert len(all_rules) == len(set(all_rules))
