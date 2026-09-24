"""Phase 6 Q1: spend control in services/ai/gateway.py, for EVERY AI capability: a per-user and a
global daily cap on provider calls (Asia/Riyadh day), refused with 429 `ai_quota_exceeded` and the
reset time before any provider request, audited as a `refused` row.

Uses the Q0 test routes (/test/ai/draft, /transcribe, /realtime, /guarded in tests/test_ai_helpers.py).

Run: docker compose --profile test run --rm -e TEST_DB_PREFIX=cawp_test_ai1 api-test tests/test_ai_quota.py
"""
from __future__ import annotations

import concurrent.futures
import datetime as dt

from test_ai_helpers import (AI, ai, ai_app, ai_client, ai_env, configure, fake, hdr, http, insert_policy,  # noqa: F401
                             pgc, q1, qall, realtime_ok, responses_ok, run, transcription_ok)

CASE = "00000000-0000-4000-8000-000000000601"
DRAFT = {"categories": ["case_facts"], "text": "Balance 100.00 SAR", "case_id": CASE}
OK_DRAFT = {"summary": "s", "next_step": None}


def draft(client, hdr, user=AI.assist):
    return client.post("/test/ai/draft", json=DRAFT, headers=hdr(user))


def transcribe(client, hdr, user=AI.assist):
    return client.post("/test/ai/transcribe", data=b"RIFF....WAVEfmt ", headers=hdr(user))


def realtime(client, hdr, user=AI.assist):
    return client.post("/test/ai/realtime", headers=hdr(user))


def add_calls(pgc, n, *, actor=AI.admin, outcome="started", started_at="now()"):
    """Provider-call rows written as another user would have (the guard allows started/refused inserts)."""
    for _ in range(n):
        run(pgc, f"""INSERT INTO ai_provider_calls (purpose, capability, provider, model, policy_revision, outcome,
                        error_code, finished_at, actor_user_id, started_at)
                     VALUES ('other_work', 'generate_structured', 'openai', 'm', 1, %s,
                             CASE WHEN %s = 'refused' THEN 'ai_policy_required' END,
                             CASE WHEN %s = 'refused' THEN now() END, %s, {started_at})""",
            outcome, outcome, outcome, actor)


def next_riyadh_midnight():
    from zoneinfo import ZoneInfo
    riyadh = ZoneInfo("Asia/Riyadh")
    now = dt.datetime.now(riyadh)
    return dt.datetime.combine(now.date() + dt.timedelta(days=1), dt.time.min, tzinfo=riyadh)


def test_per_user_cap_covers_every_capability(ai_client, hdr, pgc, fake, monkeypatch):
    configure(monkeypatch, AI_DAILY_CALLS_PER_USER=2)
    insert_policy(pgc)
    fake.script += [responses_ok(OK_DRAFT), transcription_ok()]
    assert draft(ai_client, hdr).status_code == 200
    assert transcribe(ai_client, hdr).status_code == 200
    for call in (realtime, draft, transcribe):
        r = call(ai_client, hdr)
        body = r.get_json()
        assert (r.status_code, body["code"]) == (429, "ai_quota_exceeded")
        e = body["error"]
        assert (e["scope"], e["limit"], e["used"], e["reasons"]) == ("user", 2, 2, ["ai_quota_exceeded"])
        assert dt.datetime.fromisoformat(e["resets_at"]) == next_riyadh_midnight()
        assert e["call_id"]
    assert len(fake.requests) == 2                        # nothing was sent after the cap
    rows = qall(pgc, "SELECT outcome, error_code, capability FROM ai_provider_calls ORDER BY started_at, id")
    assert [r[:2] for r in rows[2:]] == [("refused", "ai_quota_exceeded")] * 3
    assert [r[2] for r in rows[2:]] == ["realtime_session", "generate_structured", "transcribe"]
    # the cap is per user: another user still has calls today
    fake.script.append(realtime_ok())
    assert realtime(ai_client, hdr, user=AI.admin).status_code == 200


def test_global_cap_counts_everyone(ai_client, hdr, pgc, fake, monkeypatch):
    configure(monkeypatch, AI_DAILY_CALLS_TOTAL=3)
    insert_policy(pgc)
    add_calls(pgc, 3)
    r = draft(ai_client, hdr)
    assert (r.status_code, r.get_json()["error"]["scope"], r.get_json()["error"]["used"]) == (429, "total", 3)
    assert "organisation" in r.get_json()["message"]
    assert fake.requests == []


def test_refused_rows_and_other_days_do_not_count_but_failures_do(ai_client, hdr, pgc, fake, monkeypatch):
    configure(monkeypatch, AI_DAILY_CALLS_PER_USER=2, AI_MAX_RETRIES=0)
    insert_policy(pgc)
    add_calls(pgc, 5, actor=AI.assist, outcome="refused")
    # 26 hours ago is always before the start of the current Riyadh day
    add_calls(pgc, 5, actor=AI.assist, started_at="now() - interval '26 hours'")
    fake.script += [http(400, {"error": {"type": "invalid_request_error"}}), responses_ok(OK_DRAFT)]
    assert draft(ai_client, hdr).status_code == 502      # a failed call still counts: it was attempted
    assert draft(ai_client, hdr).status_code == 200
    assert draft(ai_client, hdr).status_code == 429
    assert len(fake.requests) == 2


def test_the_fail_fast_guard_and_the_status_report_the_quota(ai_client, hdr, pgc, fake, monkeypatch):
    configure(monkeypatch, AI_DAILY_CALLS_PER_USER=1)
    insert_policy(pgc, allowed_categories=["case_facts", "notes", "transcripts", "audio"])
    s = ai_client.get("/v2/ai/status", headers=hdr(AI.assist)).get_json()["data"]["quota"]
    assert s["user"] == {"limit": 1, "used": 0, "remaining": 1} and s["total"]["limit"] == 1000
    assert s["exceeded"] is None and dt.datetime.fromisoformat(s["resets_at"]) == next_riyadh_midnight()
    fake.script.append(responses_ok(OK_DRAFT))
    assert draft(ai_client, hdr).status_code == 200
    rows = q1(pgc, "SELECT count(*) FROM ai_provider_calls")[0]
    r = ai_client.post("/test/ai/guarded", headers=hdr(AI.assist))
    assert (r.status_code, r.get_json()["code"]) == (429, "ai_quota_exceeded")
    assert q1(pgc, "SELECT count(*) FROM ai_provider_calls")[0] == rows      # the guard writes no row
    s = ai_client.get("/v2/ai/status", headers=hdr(AI.assist)).get_json()["data"]["quota"]
    assert s["user"] == {"limit": 1, "used": 1, "remaining": 0} and s["exceeded"] == "user"


def test_limits_default_and_bad_values_fall_back(ai_app, monkeypatch):
    from services.ai import gateway
    assert gateway.quota_limits() == (50, 1000)
    monkeypatch.setenv("AI_DAILY_CALLS_PER_USER", "ten")
    monkeypatch.setenv("AI_DAILY_CALLS_TOTAL", "-4")
    assert gateway.quota_limits() == (50, 1000)
    monkeypatch.setenv("AI_DAILY_CALLS_PER_USER", "0")
    monkeypatch.setenv("AI_DAILY_CALLS_TOTAL", "250000")
    assert gateway.quota_limits() == (0, 250000)


def test_parallel_requests_cannot_overshoot_the_cap(ai_client, hdr, pgc, fake, monkeypatch):
    configure(monkeypatch, AI_DAILY_CALLS_PER_USER=2)
    insert_policy(pgc)
    fake.script += [responses_ok(OK_DRAFT) for _ in range(6)]
    headers = hdr(AI.assist)

    def one(_):
        return ai_client.post("/test/ai/draft", json=DRAFT, headers=headers).status_code

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        codes = sorted(pool.map(one, range(6)))
    assert codes == [200, 200, 429, 429, 429, 429]
    assert len(fake.requests) == 2
    assert q1(pgc, "SELECT count(*) FROM ai_provider_calls WHERE outcome = 'succeeded'")[0] == 2
