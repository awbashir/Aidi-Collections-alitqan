"""Phase 6 Q0: readiness, honest refusals, the AI data policy lifecycle, policy refusals by data
category, the gateway through an HTTP-level fake, and content-free provider-call audit rows.

Routes under test: api/ai.py, plus test-only routes that call services/ai/gateway.py the way Q1-Q3
will (tests/test_ai_helpers.py `_test_blueprint`). No real provider is ever contacted.

Run: docker compose --profile test run --rm -e TEST_DB_PREFIX=cawp_test_ai0 api-test tests/test_ai_*.py
"""
from __future__ import annotations

import json

import psycopg2
import pytest

from test_ai_helpers import (AI, POLICY, TEST_KEY, ai, ai_app, ai_client, ai_env, configure, fake, hdr,  # noqa: F401
                             http, insert_policy, key, pgc, q1, qall,
                             realtime_ok, responses_ok, run, transcription_ok)

CASE = "00000000-0000-4000-8000-000000000601"      # seed.sql client1
SECRETS = ("Fatima Al-Harbi", "Fatima", "0509998877", "1098765432", "SA4420000001234567891234")
DRAFT_TEXT = ("Customer Fatima Al-Harbi (id 1098765432) called from 0509998877 and asked to pay by "
              "IBAN SA4420000001234567891234.")


def draft(client, hdr, user=AI.assist, categories=("case_facts", "notes"), text=DRAFT_TEXT, **extra):
    body = {"categories": list(categories), "text": text, "case_id": CASE,
            "identifiers": {"names": ["Fatima Al-Harbi"], "identity_numbers": ["1098765432"],
                            "phones": ["0509998877"]}, **extra}
    r = client.post("/test/ai/draft", json=body, headers=hdr(user))
    return r.status_code, r.get_json()


def calls(pgc):
    return qall(pgc, "SELECT outcome, error_code, capability, purpose, model, policy_revision, data_categories, "
                     "attempts, http_status, input_tokens, output_tokens, total_tokens, latency_ms, masked, "
                     "masked_count, actor_user_id::text, entity_type, entity_id, case_id::text "
                     "FROM ai_provider_calls ORDER BY started_at, id")


def put_policy(client, hdr, expected, idem=None, user=AI.admin, **overrides):
    body = {**POLICY, "expected_revision": expected, **overrides}
    r = client.put("/v2/ai/policy", json=body, headers=hdr(user, idem or key()))
    return r.status_code, r.get_json(), r


# ------------------------------------------------------------------------------ status
def test_status_needs_an_ai_key(ai_client, hdr):
    r = ai_client.get("/v2/ai/status", headers=hdr(AI.none))
    assert (r.status_code, r.get_json()["code"]) == (403, "forbidden")
    for user in (AI.assist, AI.quality, AI.admin):
        assert ai_client.get("/v2/ai/status", headers=hdr(user)).status_code == 200


def test_status_not_configured_is_honest(ai_client, hdr):
    r = ai_client.get("/v2/ai/status", headers=hdr(AI.assist))
    d = r.get_json()["data"]
    assert r.headers["Cache-Control"].startswith("no-store")
    assert d["reasons"] == ["ai_not_configured", "model_not_set", "ai_policy_required"]
    for cap in ("generate_structured", "transcribe", "realtime_session"):
        assert d["capabilities"][cap]["ready"] is False
        assert d["capabilities"][cap]["reasons"] == ["ai_not_configured", "model_not_set", "ai_policy_required"]
    assert d["capabilities"]["transcribe"]["required_categories"] == ["audio"]
    assert d["policy"] == {"state": "missing", "revision": 0, "provider": None, "allowed_categories": [],
                           "masking": None}
    assert "configuration" not in d                       # details are for ai_admin only


def test_status_details_for_admin_never_show_the_key(ai_client, hdr, pgc, monkeypatch):
    configure(monkeypatch, transcribe=None)
    insert_policy(pgc)
    r = ai_client.get("/v2/ai/status", headers=hdr(AI.admin))
    d = r.get_json()["data"]
    assert TEST_KEY not in r.get_data(as_text=True)
    assert d["capabilities"]["generate_structured"] == {"ready": True, "reasons": [], "required_categories": [],
                                                        "model": "gpt-test-text"}
    assert d["capabilities"]["transcribe"]["reasons"] == ["model_not_set"]
    assert d["capabilities"]["realtime_session"]["ready"] is True
    assert d["reasons"] == ["model_not_set"]
    c = d["configuration"]
    assert c["key_configured"] is True and c["models"]["transcribe"] is None and c["base_url_is_default"] is False
    assert d["policy"]["approved_by"]["id"] == AI.admin and d["policy"]["reason"] == POLICY["reason"]
    # an assist user sees readiness, but not models or configuration
    d2 = ai_client.get("/v2/ai/status", headers=hdr(AI.assist)).get_json()["data"]
    assert "configuration" not in d2 and "model" not in d2["capabilities"]["generate_structured"]


# ------------------------------------------------------------------------------ honest refusals
def test_not_configured_refuses_and_nothing_is_fabricated(ai_client, hdr, pgc, fake):
    status, body = draft(ai_client, hdr)
    assert (status, body["code"]) == (503, "ai_not_configured")
    assert body["error"]["reasons"] == ["ai_not_configured", "model_not_set", "ai_policy_required"]
    assert body["status"] is False and "data" not in body
    assert fake.requests == []
    rows = calls(pgc)
    assert len(rows) == 1 and rows[0][:4] == ("refused", "ai_not_configured", "generate_structured", "test_draft")
    assert body["error"]["call_id"]
    # every capability refuses the same way
    for url, data in (("/test/ai/transcribe", b"RIFF"), ("/test/ai/realtime", None)):
        r = ai_client.post(url, data=data, headers=hdr(AI.assist))
        assert (r.status_code, r.get_json()["code"]) == (503, "ai_not_configured")
    assert fake.requests == []


def test_model_not_set_refuses(ai_client, hdr, pgc, fake, monkeypatch):
    configure(monkeypatch, text=None)
    insert_policy(pgc)
    status, body = draft(ai_client, hdr)
    assert (status, body["code"], body["error"]["reasons"]) == (503, "model_not_set", ["model_not_set"])
    assert fake.requests == []


def test_no_policy_or_a_suspended_policy_refuses(ai_client, hdr, pgc, fake, monkeypatch):
    configure(monkeypatch)
    status, body = draft(ai_client, hdr)
    assert (status, body["code"]) == (409, "ai_policy_required")
    insert_policy(pgc, state="suspended")
    status, body = draft(ai_client, hdr)
    assert (status, body["code"]) == (409, "ai_policy_required")
    assert fake.requests == []
    assert [r[:2] for r in calls(pgc)] == [("refused", "ai_policy_required")] * 2
    assert calls(pgc)[1][5] == 1                          # the suspended revision is recorded


def test_requires_ai_decorator_fails_fast_without_an_audit_row(ai_client, hdr, pgc, fake, monkeypatch):
    r = ai_client.post("/test/ai/guarded", headers=hdr(AI.assist))
    assert (r.status_code, r.get_json()["code"]) == (503, "ai_not_configured")
    configure(monkeypatch)
    insert_policy(pgc, allowed_categories=["case_facts"])
    r = ai_client.post("/test/ai/guarded", headers=hdr(AI.assist))
    assert (r.status_code, r.get_json()["code"]) == (409, "ai_policy_category_denied")
    assert r.get_json()["error"]["denied_categories"] == ["transcripts"]
    insert_policy(pgc, allowed_categories=["transcripts"])
    r = ai_client.post("/test/ai/guarded", headers=hdr(AI.assist))
    assert (r.status_code, r.get_json()["data"]) == (200, {"reached": True})
    assert calls(pgc) == [] and fake.requests == []


# ------------------------------------------------------------------------------ policy lifecycle
def test_policy_lifecycle_with_revisions_idempotency_and_audit(ai_client, hdr, pgc):
    r = ai_client.get("/v2/ai/policy", headers=hdr(AI.admin))
    d = r.get_json()["data"]
    assert d["policy"] is None and d["revision"] == 0
    assert d["options"]["categories"] == ["case_facts", "notes", "transcripts", "audio", "employee_names"]

    idem = key()
    status, body, resp = put_policy(ai_client, hdr, 0, idem)
    assert status == 200, body
    p1 = body["data"]["policy"]
    assert body["data"]["revision"] == 1 and p1["state"] == "approved" and p1["approved_by"]["id"] == AI.admin
    assert p1["allowed_categories"] == ["case_facts", "notes", "transcripts", "audio"]
    assert p1["retention_days"] == POLICY["retention_days"] and p1["reason"] == POLICY["reason"]
    # the same key and body replays the stored response, without a second revision
    status2, body2, resp2 = put_policy(ai_client, hdr, 0, idem)
    assert status2 == 200 and body2 == body and resp2.headers.get("Idempotent-Replayed") == "true"
    assert q1(pgc, "SELECT count(*) FROM ai_data_policies")[0] == 1

    ev = qall(pgc, "SELECT actor_user_id::text, before, after, reason, entity_id, idempotency_key FROM audit_events "
                   "WHERE action = 'ai.policy.update' AND entity_id = '1' ORDER BY occurred_at DESC LIMIT 1")[0]
    assert ev[0] == AI.admin and ev[1] is None and ev[3] == POLICY["reason"] and ev[5] == idem
    assert ev[2]["revision"] == 1 and ev[2]["masking"] == POLICY["masking"]

    # a stale writer gets a revision conflict with the current revision
    status, body, _ = put_policy(ai_client, hdr, 0, allowed_categories=["case_facts"])
    assert (status, body["code"]) == (409, "revision_conflict")
    assert body["error"]["expected_revision"] == 0 and body["error"]["current_revision"] == 1

    # a new revision (suspension) records before/after
    status, body, _ = put_policy(ai_client, hdr, 1, state="suspended", reason="provider contract under review")
    assert status == 200 and body["data"]["revision"] == 2 and body["data"]["policy"]["state"] == "suspended"
    ev = q1(pgc, "SELECT before, after, reason FROM audit_events WHERE action = 'ai.policy.update' AND entity_id = '2'")
    assert ev[0]["state"] == "approved" and ev[1]["state"] == "suspended" and ev[2] == "provider contract under review"

    h = ai_client.get("/v2/ai/policy/history", headers=hdr(AI.admin)).get_json()["data"]
    assert h["total"] == 2 and [p["revision"] for p in h["items"]] == [2, 1]
    cur = ai_client.get("/v2/ai/policy", headers=hdr(AI.admin)).get_json()["data"]
    assert cur["revision"] == 2 and cur["policy"]["state"] == "suspended"


def test_policy_writes_need_admin_key_revision_reason_and_confirmation(ai_client, hdr, pgc):
    for user in (AI.assist, AI.quality, AI.none):
        for method, url in (("get", "/v2/ai/policy"), ("put", "/v2/ai/policy"), ("get", "/v2/ai/policy/history"),
                            ("get", "/v2/ai/provider-calls"), ("post", "/v2/ai/check")):
            r = getattr(ai_client, method)(url, json={}, headers=hdr(user, key()))
            assert r.status_code == 403, (user, url)
    r = ai_client.put("/v2/ai/policy", json={**POLICY, "expected_revision": 0}, headers=hdr(AI.admin))
    assert (r.status_code, r.get_json()["code"]) == (400, "idempotency_key_required")
    cases = [({"expected_revision": None}, "expected_revision"),
             ({"reason": "  "}, "reason"),
             ({"confirmed": False}, "confirmed"),
             ({"state": "on"}, "state"),
             ({"provider": "other"}, "provider"),
             ({"allowed_categories": ["case_facts", "emails"]}, "allowed_categories"),
             ({"masking": {"name": True}}, "masking"),
             ({"retention_days": {**POLICY["retention_days"], "provider_logs": 7}}, "retention_days.provider_logs"),
             ({"retention_days": {**POLICY["retention_days"], "audio": 0}}, "retention_days.audio"),
             ({"retention_days": {**POLICY["retention_days"], "drafts": True}}, "retention_days.drafts")]
    for override, field in cases:
        body = {**POLICY, "expected_revision": 0, **override}
        if override.get("expected_revision", 0) is None:
            body.pop("expected_revision")
        r = ai_client.put("/v2/ai/policy", json=body, headers=hdr(AI.admin, key()))
        assert r.status_code == 422, (override, r.get_json())
        assert field in (r.get_json()["error"] or {}), (override, r.get_json())
    assert q1(pgc, "SELECT count(*) FROM ai_data_policies")[0] == 0


def test_policy_rows_are_append_only_in_the_database(pgc):
    from test_ai_helpers import reset_ai_state
    reset_ai_state(pgc)
    insert_policy(pgc)
    for sql in ("UPDATE ai_data_policies SET state = 'suspended'", "DELETE FROM ai_data_policies",
                "TRUNCATE ai_data_policies"):
        with pytest.raises(psycopg2.Error) as e:
            run(pgc, sql)
        assert "append-only" in str(e.value)
    insert_policy(pgc)
    with pytest.raises(psycopg2.Error) as e:          # revisions are consecutive
        run(pgc, "INSERT INTO ai_data_policies (revision, state, provider, mask_names, mask_identity_numbers, "
                 "mask_phones, mask_accounts, transcript_days, audio_days, draft_days, provider_log_days, "
                 "approved_by, reason) VALUES (9, 'approved', 'openai', true, true, true, true, 1, 1, 1, 30, %s, 'x')",
            AI.admin)
    assert "revision must be 3" in str(e.value)


# ------------------------------------------------------------------------------ policy refusals by category
def test_policy_refuses_data_categories_it_does_not_allow(ai_client, hdr, pgc, fake, monkeypatch):
    configure(monkeypatch)
    rev = insert_policy(pgc, allowed_categories=["case_facts"])
    status, body = draft(ai_client, hdr, categories=("case_facts", "transcripts"))
    assert (status, body["code"]) == (409, "ai_policy_category_denied")
    assert body["error"]["denied_categories"] == ["transcripts"]
    # employee names must be allowed explicitly when a call declares them
    status, body = draft(ai_client, hdr, categories=("case_facts", "employee_names"))
    assert (status, body["error"]["denied_categories"]) == (409, ["employee_names"])
    # transcription always sends audio
    r = ai_client.post("/test/ai/transcribe", data=b"RIFF", headers=hdr(AI.assist))
    assert (r.status_code, r.get_json()["error"]["denied_categories"]) == (409, ["audio"])
    assert fake.requests == []
    rows = calls(pgc)
    assert [(x[0], x[1], x[5]) for x in rows] == [("refused", "ai_policy_category_denied", rev)] * 3
    assert rows[0][6] == ["case_facts", "transcripts"] and rows[2][6] == ["audio"]
    # the same call passes once the category is allowed
    insert_policy(pgc, allowed_categories=["case_facts", "transcripts"])
    fake.script.append(responses_ok({"summary": "s", "next_step": None}))
    status, body = draft(ai_client, hdr, categories=("case_facts", "transcripts"))
    assert status == 200, body


# ------------------------------------------------------------------------------ gateway success path
def test_a_draft_is_masked_on_the_way_out_and_rendered_back(ai_client, hdr, pgc, fake, monkeypatch):
    configure(monkeypatch)
    rev = insert_policy(pgc)
    fake.script.append(responses_ok({"summary": "[NAME_1] will pay; call [PHONE_1]. Unknown [PHONE_9].",
                                     "next_step": "verify [ID_NUMBER_1]"}))
    status, body = draft(ai_client, hdr)
    assert status == 200, body
    sent = fake.sent_text()
    for secret in SECRETS:
        assert secret not in sent
    user_msg = fake.json()["input"][0]["content"]
    assert user_msg == ("Customer [NAME_1] (id [ID_NUMBER_1]) called from [PHONE_1] and asked to pay by "
                        "IBAN [ACCOUNT_1].")
    d = body["data"]
    assert d["data"] == {"summary": "Fatima Al-Harbi will pay; call 0509998877. Unknown [PHONE_9].",
                         "next_step": "verify 1098765432"}
    assert d["raw_data"]["summary"].startswith("[NAME_1]")
    assert (d["model"], d["policy_revision"], d["masked"], d["masked_count"]) == ("gpt-test-text-2026", rev, True, 4)
    assert d["usage"] == {"input_tokens": 120, "output_tokens": 40, "total_tokens": 160, "audio_seconds": None}
    row = calls(pgc)[-1]
    assert row[:9] == ("succeeded", None, "generate_structured", "test_draft", "gpt-test-text-2026", rev,
                       ["case_facts", "notes"], 1, 200)
    assert row[9:12] == (120, 40, 160) and row[12] is not None and row[13:15] == (True, 4)
    assert row[15:] == (AI.assist, "case", CASE, CASE)
    assert d["call_id"] == q1(pgc, "SELECT id::text FROM ai_provider_calls ORDER BY started_at DESC LIMIT 1")[0]


def test_policy_can_allow_sending_phone_numbers(ai_client, hdr, pgc, fake, monkeypatch):
    configure(monkeypatch)
    insert_policy(pgc, masking={"name": True, "identity_number": True, "phone": False, "account": True})
    fake.script.append(responses_ok({"summary": "s", "next_step": None}))
    status, _ = draft(ai_client, hdr)
    assert status == 200
    content = fake.json()["input"][0]["content"]
    assert "0509998877" in content and "Fatima" not in content and "1098765432" not in content


def test_audit_rows_and_events_never_hold_content(ai_client, hdr, pgc, fake, monkeypatch):
    configure(monkeypatch)
    insert_policy(pgc)
    reply = "UNIQUE-MODEL-REPLY-7731"
    fake.script += [responses_ok({"summary": reply, "next_step": None}), http(503), http(503), http(503),
                    transcription_ok(text="UNIQUE-TRANSCRIPT-5521"), realtime_ok(value="ek_UNIQUE_SECRET_9912")]
    assert draft(ai_client, hdr)[0] == 200
    assert draft(ai_client, hdr)[0] == 502
    assert ai_client.post("/test/ai/transcribe", data=b"RIFF-UNIQUE-AUDIO", headers=hdr(AI.assist)).status_code == 200
    assert ai_client.post("/test/ai/realtime", headers=hdr(AI.assist)).status_code == 200
    cols = {r[0] for r in qall(pgc, "SELECT column_name FROM information_schema.columns "
                                    "WHERE table_name = 'ai_provider_calls'")}
    assert cols == {"id", "started_at", "finished_at", "purpose", "capability", "provider", "model", "policy_revision",
                    "data_categories", "masked", "masked_count", "outcome", "error_code", "provider_error_type",
                    "http_status", "attempts", "input_tokens", "output_tokens", "total_tokens", "audio_seconds",
                    "latency_ms", "input_bytes", "actor_user_id", "entity_type", "entity_id", "case_id",
                    "request_id", "provider_request_id"}          # metadata only: no column for content
    dump = json.dumps([r[0] for r in qall(pgc, "SELECT row_to_json(c)::text FROM ai_provider_calls c")])
    dump += json.dumps([r[0] for r in qall(pgc, "SELECT row_to_json(a)::text FROM audit_events a "
                                                "WHERE occurred_at > now() - interval '5 minutes'")])
    for secret in SECRETS + (reply, "UNIQUE-TRANSCRIPT-5521", "ek_UNIQUE_SECRET_9912", "RIFF-UNIQUE-AUDIO",
                             TEST_KEY, "Draft for a human"):
        assert secret not in dump, secret


# ------------------------------------------------------------------------------ gateway failures
def test_retry_then_success_and_exhausted_retries(ai_client, hdr, pgc, fake, monkeypatch):
    configure(monkeypatch)
    insert_policy(pgc)
    fake.script += [http(503), http(429, headers={"retry-after-ms": "10"}), responses_ok({"summary": "s", "next_step": None})]
    status, body = draft(ai_client, hdr)
    assert status == 200 and body["data"]["attempts"] == 3 and fake.sleeps[1] == pytest.approx(0.01)
    fake.script += [http(500), http(502), http(503, {"error": {"code": "overloaded", "message": "private"}})]
    status, body = draft(ai_client, hdr)
    assert (status, body["code"]) == (502, "ai_provider_unavailable")
    assert body["error"]["retryable"] is True and body["error"]["provider_error_type"] == "overloaded"
    assert "private" not in json.dumps(body)
    row = q1(pgc, "SELECT outcome, error_code, provider_error_type, http_status, attempts, finished_at IS NOT NULL, "
                  "id::text FROM ai_provider_calls ORDER BY started_at DESC LIMIT 1")
    assert row[:6] == ("failed", "provider_unavailable", "overloaded", 503, 3, True)
    assert body["error"]["call_id"] == row[6]
    assert len(fake.requests) == 6


def test_timeout_is_reported_and_not_retried(ai_client, hdr, pgc, fake, monkeypatch):
    from services.ai.transport import TransportError
    configure(monkeypatch)
    insert_policy(pgc)
    fake.script.append(TransportError("timeout"))
    status, body = draft(ai_client, hdr)
    assert (status, body["code"]) == (504, "ai_provider_timeout") and len(fake.requests) == 1
    assert q1(pgc, "SELECT outcome, error_code, attempts FROM ai_provider_calls ORDER BY started_at DESC LIMIT 1") == \
        ("failed", "provider_timeout", 1)


def test_an_unusable_answer_is_an_error_not_a_draft(ai_client, hdr, pgc, fake, monkeypatch):
    configure(monkeypatch)
    insert_policy(pgc)
    fake.script.append(responses_ok({"summary": 42, "next_step": None}))       # wrong type
    status, body = draft(ai_client, hdr)
    assert (status, body["code"]) == (502, "ai_provider_invalid_response") and "data" not in body


# ------------------------------------------------------------------------------ transcription and realtime
def test_transcription_through_the_gateway(ai_client, hdr, pgc, fake, monkeypatch):
    configure(monkeypatch)
    rev = insert_policy(pgc)
    fake.script.append(transcription_ok())
    r = ai_client.post("/test/ai/transcribe?language=ar", data=b"RIFF....WAVE", headers=hdr(AI.assist))
    d = r.get_json()["data"]
    assert r.status_code == 200 and d["diarized"] is True and d["segments"][1]["speaker"] == "B"
    assert d["usage"]["audio_seconds"] == 12.5
    row = q1(pgc, "SELECT outcome, capability, data_categories, policy_revision, audio_seconds, input_bytes, "
                  "entity_type, entity_id FROM ai_provider_calls ORDER BY started_at DESC LIMIT 1")
    assert row[:4] == ("succeeded", "transcribe", ["audio"], rev) and float(row[4]) == 12.5
    assert row[5] > len(b"RIFF....WAVE") and row[6:] == ("qc_recording", "rec-1")


def test_realtime_session_through_the_gateway(ai_client, hdr, pgc, fake, monkeypatch):
    configure(monkeypatch)
    insert_policy(pgc, allowed_categories=[])            # the voice lab sends no data category
    fake.script.append(realtime_ok())
    r = ai_client.post("/test/ai/realtime", headers=hdr(AI.assist))
    assert r.status_code == 200 and r.get_json()["data"]["client_secret"] == "ek_fake_ephemeral_secret_123"
    assert q1(pgc, "SELECT outcome, capability, data_categories FROM ai_provider_calls "
                   "ORDER BY started_at DESC LIMIT 1") == ("succeeded", "realtime_session", [])


# ------------------------------------------------------------------------------ admin routes
def test_connectivity_check_and_provider_call_list(ai_client, hdr, pgc, fake, monkeypatch):
    r = ai_client.post("/v2/ai/check", headers=hdr(AI.admin))
    assert (r.status_code, r.get_json()["code"]) == (503, "ai_not_configured")
    configure(monkeypatch)
    insert_policy(pgc, allowed_categories=[])
    fake.script.append(responses_ok({"ok": True}))
    r = ai_client.post("/v2/ai/check", headers=hdr(AI.admin))
    d = r.get_json()["data"]
    assert r.status_code == 200 and d["ok"] is True and d["model"] == "gpt-test-text-2026" and d["attempts"] == 1
    sent = fake.json()
    assert sent["store"] is False and sent["max_output_tokens"] == 200
    assert sent["text"]["format"]["schema"]["required"] == ["ok"]

    lst = ai_client.get("/v2/ai/provider-calls", headers=hdr(AI.admin)).get_json()["data"]
    assert lst["total"] == 2 and [i["outcome"] for i in lst["items"]] == ["succeeded", "refused"]
    item = lst["items"][0]
    assert item["purpose"] == "connectivity_check" and item["actor"]["id"] == AI.admin
    assert item["usage"]["total_tokens"] == 160 and item["entity_type"] == "ai_configuration"
    only = ai_client.get("/v2/ai/provider-calls?outcome=refused", headers=hdr(AI.admin)).get_json()["data"]
    assert only["total"] == 1 and only["items"][0]["error_code"] == "ai_not_configured"
    bad = ai_client.get("/v2/ai/provider-calls?outcome=done", headers=hdr(AI.admin))
    assert bad.status_code == 422


def test_provider_call_rows_are_guarded(pgc):
    from test_ai_helpers import reset_ai_state
    reset_ai_state(pgc)
    started = q1(pgc, "INSERT INTO ai_provider_calls (purpose, capability, provider, outcome) "
                      "VALUES ('t_guard', 'transcribe', 'openai', 'started') RETURNING id")[0]
    with pytest.raises(psycopg2.Error):                  # a new row cannot claim success
        run(pgc, "INSERT INTO ai_provider_calls (purpose, capability, provider, outcome, finished_at) "
                 "VALUES ('t_guard', 'transcribe', 'openai', 'succeeded', now())")
    with pytest.raises(psycopg2.Error):                  # identifying fields are immutable
        run(pgc, "UPDATE ai_provider_calls SET purpose = 'other', outcome = 'succeeded', finished_at = now() "
                 "WHERE id = %s", started)
    run(pgc, "UPDATE ai_provider_calls SET outcome = 'succeeded', finished_at = now() WHERE id = %s", started)
    with pytest.raises(psycopg2.Error):                  # a finished row is immutable
        run(pgc, "UPDATE ai_provider_calls SET latency_ms = 1 WHERE id = %s", started)
    with pytest.raises(psycopg2.Error) as e:             # only the retention task deletes
        run(pgc, "DELETE FROM ai_provider_calls WHERE id = %s", started)
    assert "retention task" in str(e.value)
    with pytest.raises(psycopg2.Error):
        run(pgc, "TRUNCATE ai_provider_calls")
