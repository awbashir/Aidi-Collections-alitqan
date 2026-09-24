"""Phase 6 Q1: AI assistance drafts through the real api/ai.py routes and the Q0 gateway, with a
provider fake (no real provider is ever contacted).

Covers: the input of each kind (only the declared categories, masked; checked on what the fake
received), schema validation, honest refusals (not configured / no policy / category / quota),
case scope, save-as-note (exactly one legacy note, nothing else changes), edit + revision
conflict, discard, idempotent generation, retention purge and a content-free audit.

Run: docker compose --profile test run --rm -e TEST_DB_PREFIX=cawp_test_ai1 api-test tests/test_ai_*.py
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json

import psycopg2
import pytest

from test_ai_drafts_helpers import (D, FOLLOWUP_MARKER, IDENTIFIERS, NOTE_MARKER, TRANSCRIPT_BIT, ai, ai1,  # noqa: F401
                                    ai_app, ai_env, answer, dclient, echo, generate, hdr, http, insert_policy,
                                    key, pgc, q1, qall, ready, run, save_body)
from test_ai_helpers import AI, configure


def calls(pgc):
    return qall(pgc, "SELECT outcome, error_code, purpose, data_categories, entity_type, entity_id, case_id::text, "
                     "actor_user_id::text FROM ai_provider_calls ORDER BY started_at, id")


def draft_count(pgc):
    return q1(pgc, "SELECT count(*) FROM ai_drafts")[0]


# ------------------------------------------------------------------------------ inputs per kind
KIND_CASES = [
    # kind, body, expected categories, payload keys, markers present, markers absent
    ("call_script", {"case_id": D.case}, ["case_facts", "notes"], {"case_facts", "calls", "notes", "debtor_name"},
     [NOTE_MARKER, FOLLOWUP_MARKER], [TRANSCRIPT_BIT, "out-of-scope note"]),
    ("case_guidance", {"case_id": D.case}, ["case_facts", "notes"], {"case_facts", "calls", "notes"},
     [NOTE_MARKER, FOLLOWUP_MARKER], [TRANSCRIPT_BIT]),
    ("narrative", {"case_id": D.case, "options": {"audience": "legal"}}, ["case_facts", "notes"],
     {"case_facts", "calls", "notes", "debtor_name"}, [NOTE_MARKER], [TRANSCRIPT_BIT]),
    ("followup_message", {"case_id": D.case, "options": {"channel": "sms"}}, ["case_facts"],
     {"case_facts", "debtor_name"},
     ["11500.00"], [NOTE_MARKER, FOLLOWUP_MARKER, TRANSCRIPT_BIT, "wrap-up note"]),
    ("call_summary", {"attempt_id": D.call}, ["case_facts", "notes"], {"call", "wrapup_note"},
     ["wrap-up note"], [TRANSCRIPT_BIT, NOTE_MARKER, "11500.00"]),
    ("call_summary", {"attempt_id": D.call, "options": {"include_transcript": True}},
     ["case_facts", "notes", "transcripts"], {"call", "wrapup_note", "transcript"}, [TRANSCRIPT_BIT], [NOTE_MARKER]),
    ("coaching", {"attempt_id": D.call}, ["case_facts", "notes", "transcripts"], {"call", "wrapup_note", "transcript"},
     [TRANSCRIPT_BIT], [NOTE_MARKER]),
]


@pytest.mark.parametrize("kind,body,cats,keys,present,absent", KIND_CASES,
                         ids=[f"{c[0]}{'+transcript' if 'transcripts' in c[2] and c[0] == 'call_summary' else ''}"
                              for c in KIND_CASES])
def test_each_kind_sends_only_its_declared_categories_masked(dclient, hdr, pgc, echo, monkeypatch,
                                                            kind, body, cats, keys, present, absent):
    ready(monkeypatch, pgc)
    echo.answers.append(answer(kind))
    status, res, _ = generate(dclient, hdr, kind=kind, **body)
    assert status == 201, res
    d = res["data"]
    assert len(echo.requests) == 1
    sent = echo.json()
    assert sent["store"] is False and sent["text"]["format"]["strict"] is True
    assert sent["text"]["format"]["name"] == kind
    assert "Write every text field in English" in sent["instructions"]
    payload = echo.payload()
    assert set(payload) == {"task", "language", "options"} | keys
    text_sent = echo.sent_text()
    for value in IDENTIFIERS:
        assert value not in text_sent, (kind, value)
    name_sent = "[NAME_" in sent["input"][0]["content"]         # the instructions show example tokens only
    assert name_sent == (set(payload) != {"task", "language", "options", "call", "wrapup_note"})
    for marker in present:
        assert marker in text_sent, (kind, marker)
    for marker in absent:
        assert marker not in text_sent, (kind, marker)
    # the declared categories are what the policy checked and the audit recorded
    assert d["input"]["categories"] == cats
    rows = calls(pgc)
    assert len(rows) == 1 and rows[0][0] == "succeeded" and rows[0][2] == f"ai_draft_{kind}" and rows[0][3] == cats
    assert rows[0][6] == D.case and rows[0][7] == D.sara
    # the fingerprint is of exactly what was sent; no prompt is stored
    fp = hashlib.sha256(json.dumps({"instructions": sent["instructions"], "input": sent["input"]}, ensure_ascii=False,
                                   sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert d["input"]["masked_fingerprint"] == fp
    assert d["provenance"]["provider_call_id"] == str(q1(pgc, "SELECT id FROM ai_provider_calls")[0])
    assert d["provenance"]["masked"] is True and d["provenance"]["masked_count"] > 0
    # the answer's placeholders are mapped back only for the user
    assert d["status"] == "generated" and d["ai_generated"] is True and d["notice"].startswith("AI-generated draft")
    assert ("محمد" in d["text"]) == name_sent and "[NAME_" not in d["text"]
    assert d["text"] == d["generated_text"] and d["content"] is not None
    stored = q1(pgc, "SELECT row_to_json(d)::text FROM ai_drafts d")[0]
    assert "licensed Saudi collections agency" not in stored and "[NAME_" not in stored


def test_arabic_output_is_requested_and_rendered_with_arabic_headings(dclient, hdr, pgc, echo, monkeypatch):
    ready(monkeypatch, pgc)
    echo.answers.append(answer("case_guidance"))
    status, res, _ = generate(dclient, hdr, kind="case_guidance", case_id=D.case, language="ar")
    assert status == 201
    assert "Write every text field in Arabic" in echo.json()["instructions"]
    assert "الإجراءات المقترحة" in res["data"]["text"] and res["data"]["notice"].startswith("مسودة")
    payload = echo.payload()
    assert any("الرصيد المتبقي: 11500.00 ريال" == f["text"] for f in payload["case_facts"])


def test_case_facts_reflect_money_promises_plan_and_follow_up(dclient, hdr, pgc, echo, monkeypatch):
    ready(monkeypatch, pgc)
    echo.answers.append(answer("call_script"))
    generate(dclient, hdr, kind="call_script", case_id=D.case)
    facts = [f["text"] for f in echo.payload()["case_facts"]]
    joined = "\n".join(facts)
    assert "Outstanding balance: 11500.00 SAR" in facts
    assert "Payments awaiting approval: 500.00 SAR (1); not recovered money until approved" in facts
    assert "Last approved payment: 1000.00 SAR on 2026-08-15" in facts
    assert "Promise to pay 2000.00 SAR due 2026-09-10" in joined
    assert "Repayment plan (monthly, 3 installments, total 3000.00 SAR)" in joined
    assert "Open follow-up due 2035-09-28T09:00" in facts
    calls_sent = [c["text"] for c in echo.payload()["calls"]]
    assert any("wrap-up outcome: connected" in c for c in calls_sent)
    assert all(f["id"].startswith("F") for f in echo.payload()["case_facts"])


# ------------------------------------------------------------------------------ server checks
def test_citations_are_resolved_and_unknown_ones_flagged(dclient, hdr, pgc, echo, monkeypatch):
    ready(monkeypatch, pgc)
    echo.answers.append(answer("case_guidance", actions=[
        {"action": "Call about the promise", "reason": "overdue", "priority": "high", "fact_ids": ["F8", "F99"]},
        {"action": "Threaten court", "reason": "none", "priority": "low", "fact_ids": ["F77"]}]))
    status, res, _ = generate(dclient, hdr, kind="case_guidance", case_id=D.case)
    d = res["data"]
    assert status == 201
    assert d["checks"] == {"unknown_fact_ids": ["F99", "F77"], "unsupported": ["actions[1]"]}
    assert set(d["citations"]) == {"F8"} and d["citations"]["F8"].startswith("Promise to pay 2000.00 SAR")
    assert "2. Threaten court (low) (no supporting case fact was cited)\n   Reason: none" in d["text"]
    assert "Call about the promise (high)\n" in d["text"]


def test_coaching_quotes_are_checked_against_the_transcript(dclient, hdr, pgc, echo, monkeypatch):
    ready(monkeypatch, pgc)

    def coach(body):
        masked = json.loads(body["input"][0]["content"])["transcript"]
        real_line = next(ln for ln in masked.splitlines() if ln.startswith("Debtor: I can pay"))
        verify_line = next(ln for ln in masked.splitlines() if "للتحقق" in ln)
        return {"overall": "ok", "strengths": [{"point": "Verified identity", "quote": verify_line.split(": ", 1)[1]}],
                "improvements": [{"point": "Pressure", "suggestion": "Avoid", "quote": "You must pay today or else"}],
                "compliance_concerns": [{"concern": "none", "quote": real_line.split(": ", 1)[1]}],
                "practice_tips": []}
    echo.answers.append(coach)
    status, res, _ = generate(dclient, hdr, kind="coaching", attempt_id=D.call)
    assert status == 201, res
    d = res["data"]
    assert d["checks"] == {"quotes": 3, "unverified_quotes": ["improvements[0]"]}
    assert "«You must pay today or else» (quote not found in the transcript)" in d["text"]
    assert "cannot hear the call" in echo.json()["instructions"]


# ------------------------------------------------------------------------------ schema
@pytest.mark.parametrize("bad", [
    {"summary": "x", "actions": [], "risks": []},                                     # missing field
    {"summary": "x", "actions": [{"action": "a", "reason": "r", "priority": "urgent", "fact_ids": []}],
     "risks": [], "missing_information": []},                                         # not in the enum
    {"summary": "x", "actions": [], "risks": [], "missing_information": [], "extra": 1},   # extra field
])
def test_an_answer_that_breaks_the_schema_is_not_a_draft(dclient, hdr, pgc, echo, monkeypatch, bad):
    ready(monkeypatch, pgc)
    echo.answers.append(bad)
    status, res, _ = generate(dclient, hdr, kind="case_guidance", case_id=D.case)
    assert (status, res["code"]) == (502, "ai_provider_invalid_response")
    assert draft_count(pgc) == 0
    assert calls(pgc)[0][:2] == ("failed", "provider_invalid_response")


def test_every_schema_is_strict_and_sent_as_is(dclient, hdr, pgc, echo, monkeypatch):
    from services.ai import drafts_kinds as K
    from services.ai import schema
    for s in K.SCHEMAS.values():
        schema.check_strict(s)
    ready(monkeypatch, pgc)
    echo.answers.append(answer("followup_message"))
    generate(dclient, hdr, kind="followup_message", case_id=D.case)
    assert echo.json()["text"]["format"]["schema"] == K.SCHEMAS["followup_message"]


# ------------------------------------------------------------------------------ honest refusals
def test_not_configured_and_no_policy_refuse_honestly(dclient, hdr, pgc, echo, monkeypatch):
    status, res, _ = generate(dclient, hdr, kind="call_script", case_id=D.case)
    assert (status, res["code"]) == (503, "ai_not_configured")
    assert res["error"]["reasons"] == ["ai_not_configured", "model_not_set", "ai_policy_required"]
    assert "data" not in res
    configure(monkeypatch)
    status, res, _ = generate(dclient, hdr, kind="call_script", case_id=D.case)
    assert (status, res["code"]) == (409, "ai_policy_required")
    insert_policy(pgc, state="suspended")
    status, res, _ = generate(dclient, hdr, kind="call_script", case_id=D.case)
    assert (status, res["code"]) == (409, "ai_policy_required")
    assert echo.requests == [] and draft_count(pgc) == 0
    assert [r[:2] for r in calls(pgc)] == [("refused", "ai_not_configured"), ("refused", "ai_policy_required"),
                                           ("refused", "ai_policy_required")]


def test_a_category_the_policy_does_not_allow_is_refused(dclient, hdr, pgc, echo, monkeypatch):
    ready(monkeypatch, pgc, allowed_categories=["case_facts", "notes"])
    status, res, _ = generate(dclient, hdr, kind="coaching", attempt_id=D.call)
    assert (status, res["code"]) == (409, "ai_policy_category_denied")
    assert res["error"]["denied_categories"] == ["transcripts"]
    status, res, _ = generate(dclient, hdr, kind="call_summary", attempt_id=D.call,
                              options={"include_transcript": True})
    assert (status, res["code"], res["error"]["denied_categories"]) == (409, "ai_policy_category_denied", ["transcripts"])
    insert_policy(pgc, allowed_categories=["case_facts"])
    status, res, _ = generate(dclient, hdr, kind="case_guidance", case_id=D.case)
    assert (status, res["error"]["denied_categories"]) == (409, ["notes"])
    assert echo.requests == [] and draft_count(pgc) == 0
    # a kind that needs only case facts still works under that policy
    echo.answers.append(answer("followup_message"))
    status, res, _ = generate(dclient, hdr, kind="followup_message", case_id=D.case)
    assert status == 201 and res["data"]["input"]["categories"] == ["case_facts"]


def test_the_daily_quota_refuses_a_draft_before_the_provider_call(dclient, hdr, pgc, echo, monkeypatch):
    ready(monkeypatch, pgc)
    monkeypatch.setenv("AI_DAILY_CALLS_PER_USER", "1")
    echo.answers.append(answer("followup_message"))
    assert generate(dclient, hdr, kind="followup_message", case_id=D.case)[0] == 201
    status, res, _ = generate(dclient, hdr, kind="followup_message", case_id=D.case)
    assert (status, res["code"]) == (429, "ai_quota_exceeded")
    assert res["error"]["scope"] == "user" and res["error"]["limit"] == 1 and res["error"]["resets_at"]
    assert len(echo.requests) == 1 and draft_count(pgc) == 1
    assert calls(pgc)[-1][:2] == ("refused", "ai_quota_exceeded")


# ------------------------------------------------------------------------------ validation and scope
def test_request_validation(dclient, hdr, pgc, echo, monkeypatch):
    ready(monkeypatch, pgc)
    cases = [({"kind": "poem", "case_id": D.case}, "kind"),
             ({"kind": "call_script", "case_id": D.case, "language": "fr"}, "language"),
             ({"kind": "call_script"}, "case_id"),
             ({"kind": "coaching", "case_id": D.case}, "attempt_id"),
             ({"kind": "call_script", "case_id": D.case, "attempt_id": D.call}, "attempt_id"),
             ({"kind": "narrative", "case_id": D.case, "options": {"audience": "press"}}, "options.audience"),
             ({"kind": "followup_message", "case_id": D.case, "options": {"channel": "fax"}}, "options.channel"),
             ({"kind": "call_script", "case_id": D.case, "options": {"tone": "firm"}}, "options.tone"),
             ({"kind": "followup_message", "case_id": D.case, "send": True}, "send")]
    for body, field in cases:
        status, res, _ = generate(dclient, hdr, **body)
        assert (status, res["code"]) == (422, "validation_failed"), body
        assert field in res["error"], (body, res)
    r = dclient.post("/v2/ai/drafts", json={"kind": "call_script", "case_id": D.case, "language": "en"},
                     headers=hdr(D.sara))
    assert (r.status_code, r.get_json()["code"]) == (400, "idempotency_key_required")
    assert echo.requests == [] and calls(pgc) == []


def test_sources_a_kind_needs(dclient, hdr, pgc, echo, monkeypatch):
    ready(monkeypatch, pgc)
    status, res, _ = generate(dclient, hdr, kind="coaching", attempt_id=D.call_no_transcript)
    assert (status, res["code"]) == (422, "transcript_required")
    status, res, _ = generate(dclient, hdr, kind="call_summary", attempt_id=D.call_no_transcript,
                              options={"include_transcript": True})
    assert (status, res["code"]) == (422, "transcript_not_available")
    status, res, _ = generate(dclient, hdr, kind="call_summary", attempt_id=D.call_omar)   # no wrap-up
    assert (status, res["code"]) == (422, "ai_draft_no_source")
    # coaching on someone else's call needs calls_manage or quality_review
    status, res, _ = generate(dclient, hdr, kind="coaching", attempt_id=D.call_omar)
    assert (status, res["code"]) == (403, "forbidden")
    assert echo.requests == [] and calls(pgc) == []
    # a summary of that call from its transcript is fine
    echo.answers.append(answer("call_summary"))
    status, res, _ = generate(dclient, hdr, kind="call_summary", attempt_id=D.call_omar,
                              options={"include_transcript": True})
    assert status == 201 and res["data"]["input"]["categories"] == ["case_facts", "transcripts"]
    assert "Omar" not in echo.sent_text()


def test_no_draft_outside_the_callers_scope(dclient, hdr, pgc, echo, monkeypatch):
    ready(monkeypatch, pgc)
    status, res, _ = generate(dclient, hdr, kind="call_script", case_id=D.case_other)
    assert (status, res["code"]) == (404, "not_found")
    status, res, _ = generate(dclient, hdr, kind="call_summary", attempt_id=D.call_out)
    assert (status, res["code"]) == (404, "call_not_found")
    r = dclient.get(f"/v2/ai/drafts?case={D.case_other}", headers=hdr(D.sara))
    assert r.status_code == 404
    assert echo.requests == [] and calls(pgc) == []            # nothing reached the gateway
    # a draft of sara's case is invisible to omar, visible to her supervisor
    echo.answers.append(answer("call_script"))
    _, res, _ = generate(dclient, hdr, kind="call_script", case_id=D.case)
    did = res["data"]["id"]
    r = dclient.get(f"/v2/ai/drafts/{did}", headers=hdr(D.omar))
    assert (r.status_code, r.get_json()["code"]) == (404, "draft_not_found")
    assert dclient.get(f"/v2/ai/drafts?case={D.case}", headers=hdr(D.omar)).status_code == 404
    assert dclient.get(f"/v2/ai/drafts/{did}", headers=hdr(D.huda)).status_code == 200
    # ... but only the requester changes it
    r = dclient.patch(f"/v2/ai/drafts/{did}", json={"text": "x", "expected_revision": 1}, headers=hdr(D.huda, key()))
    assert (r.status_code, r.get_json()["code"]) == (403, "forbidden")


def test_draft_routes_need_ai_assist(dclient, hdr, pgc):
    did = "00000000-0000-4000-8000-00000000dead"
    for method, url in (("post", "/v2/ai/drafts"), ("get", f"/v2/ai/drafts?case={D.case}"),
                        ("get", f"/v2/ai/drafts/{did}"), ("patch", f"/v2/ai/drafts/{did}"),
                        ("post", f"/v2/ai/drafts/{did}/save-as-note"), ("post", f"/v2/ai/drafts/{did}/discard")):
        r = getattr(dclient, method)(url, json={}, headers=hdr(AI.quality, key()))
        assert (r.status_code, r.get_json()["code"]) == (403, "forbidden"), url
    # there is no way to send anything
    assert dclient.post(f"/v2/ai/drafts/{did}/send", json={}, headers=hdr(D.sara, key())).status_code in (404, 405)


# ------------------------------------------------------------------------------ nothing changes
SNAPSHOTS = {
    "case": """SELECT to_jsonb(c) - 'last_comment_at' - 'last_comment_id' FROM clints c WHERE id = %s""",
    "payments": """SELECT coalesce(jsonb_agg(to_jsonb(p) ORDER BY p.id), '[]') FROM "Payments" p WHERE clint_id = %s""",
    "promises": """SELECT coalesce(jsonb_agg(to_jsonb(p) ORDER BY p.id), '[]') FROM "paymentDates" p WHERE clint_id = %s""",
    "promise_allocations": """SELECT coalesce(jsonb_agg(to_jsonb(a) ORDER BY a.id), '[]') FROM promise_allocations a
                              JOIN "paymentDates" p ON p.id = a.promise_id WHERE p.clint_id = %s""",
    "follow_ups": """SELECT coalesce(jsonb_agg(to_jsonb(f) ORDER BY f.id), '[]') FROM follow_ups f WHERE case_id = %s""",
    "follow_up_events": """SELECT count(*) FROM follow_up_events e JOIN follow_ups f ON f.id = e.follow_up_id
                           WHERE f.case_id = %s""",
    "plans": """SELECT coalesce(jsonb_agg(to_jsonb(p) ORDER BY p.id), '[]') FROM repayment_plans p WHERE case_id = %s""",
    "plan_allocations": """SELECT count(*) FROM plan_allocations WHERE case_id = %s""",
    "calls": """SELECT coalesce(jsonb_agg(to_jsonb(a) ORDER BY a.id), '[]') FROM call_attempts a WHERE case_id = %s""",
    "wrapups": """SELECT coalesce(jsonb_agg(to_jsonb(w) ORDER BY w.id), '[]') FROM call_wrapups w WHERE case_id = %s""",
    "calling_blocks": """SELECT count(*) FROM calling_blocks WHERE case_id = %s""",
    "reservations": """SELECT count(*) FROM call_reservations WHERE case_id = %s""",
    "contacts": """SELECT coalesce(jsonb_agg(to_jsonb(k) ORDER BY k.id), '[]') FROM "ClintContacts" k
                   WHERE "identityNumber" = (SELECT "identityNumber" FROM clints WHERE id = %s)""",
    "notes": """SELECT coalesce(jsonb_agg(to_jsonb(m) ORDER BY m.id), '[]') FROM "Comments" m WHERE clint_id = %s""",
}


def snapshot(pgc, case_id):
    return {k: q1(pgc, sql, case_id)[0] for k, sql in SNAPSHOTS.items()}


def test_save_as_note_writes_exactly_one_legacy_note_and_nothing_else(dclient, hdr, pgc, echo, monkeypatch):
    ready(monkeypatch, pgc)
    before = snapshot(pgc, D.case)
    echo.answers.append(answer("call_summary"))
    status, res, _ = generate(dclient, hdr, kind="call_summary", attempt_id=D.call)
    assert status == 201
    d = res["data"]
    assert snapshot(pgc, D.case) == before                     # generating changed nothing at all

    edited = d["text"] + "\nChecked by the collector."
    idem = key()
    r = dclient.post(f"/v2/ai/drafts/{d['id']}/save-as-note", json=save_body(d, text=edited, comment_reachability_id=D.reach),
                     headers=hdr(D.sara, idem))
    assert r.status_code == 201, r.get_json()
    out = r.get_json()["data"]
    note_id = out["note"]["id"]
    assert out["draft"]["status"] == "saved_as_note" and out["draft"]["saved_note_id"] == note_id
    assert "text" not in out["draft"] and "content" not in out["draft"]      # nothing to replay from idempotency_keys

    after = snapshot(pgc, D.case)
    new_notes = [n for n in after["notes"] if n["id"] not in {m["id"] for m in before["notes"]}]
    assert {k: v for k, v in after.items() if k != "notes"} == {k: v for k, v in before.items() if k != "notes"}                                    # payments, promises, follow-ups, plans, calls, case
    assert len(new_notes) == 1
    n = new_notes[0]
    assert (n["id"], n["comment"], n["user_id"], n["comment_status_id"], n["contactMethod_id"],
            n["comment_reachability_id"], n["next_contact_date"], n["call_id"], n["is_deleted"]) == (
        note_id, edited, D.sara, D.status, D.method, D.reach, None, None, False)
    # the legacy note triggers fired: the case's last note is the new one (its revision did not move)
    assert q1(pgc, "SELECT last_comment_id::text FROM clints WHERE id = %s", D.case)[0] == note_id
    ev = q1(pgc, "SELECT after, reason, actor_user_id::text FROM audit_events WHERE action = 'note.create' "
                 "AND entity_id = %s", note_id)
    assert ev[0]["ai_draft_id"] == d["id"] and ev[0]["next_contact_date"] is None and ev[1] == "saved from an AI draft"
    row = q1(pgc, "SELECT status, text, edit_count, saved_by::text FROM ai_drafts WHERE id = %s", d["id"])
    assert row == ("saved_as_note", edited, 1, D.sara)

    # replaying the same request returns the same answer and writes nothing
    r2 = dclient.post(f"/v2/ai/drafts/{d['id']}/save-as-note", json=save_body(d, text=edited,
                                                                             comment_reachability_id=D.reach),
                      headers=hdr(D.sara, idem))
    assert r2.status_code == 201 and r2.headers.get("Idempotent-Replayed") == "true" and r2.get_json() == r.get_json()
    assert q1(pgc, 'SELECT count(*) FROM "Comments" WHERE clint_id = %s', D.case)[0] == len(before["notes"]) + 1
    # a second save is refused
    r3 = dclient.post(f"/v2/ai/drafts/{d['id']}/save-as-note",
                      json=save_body({**d, "revision": out["draft"]["revision"]}, text=edited),
                      headers=hdr(D.sara, key()))
    assert (r3.status_code, r3.get_json()["code"]) == (409, "draft_saved")


def test_save_as_note_rules(dclient, hdr, pgc, echo, monkeypatch):
    ready(monkeypatch, pgc)
    echo.answers += [answer("call_script"), answer("call_script"), answer("call_script")]
    d = generate(dclient, hdr, kind="call_script", case_id=D.case)[1]["data"]
    url = f"/v2/ai/drafts/{d['id']}/save-as-note"
    notes = q1(pgc, 'SELECT count(*) FROM "Comments"')[0]
    for body, status, code in (
            (save_body(d, confirmed=None), 422, "validation_failed"),
            (save_body(d, callback_at="2035-01-01T09:00:00+03:00"), 422, "draft_note_only"),
            (save_body(d, promise={"agreed": True}), 422, "draft_note_only"),
            (save_body(d, text="  "), 422, "validation_failed"),
            (save_body(d, text="x" * 10_001), 422, "validation_failed"),
            (save_body(d, comment_status_id=D.status_other_contract), 422, "validation_failed"),
            (save_body(d, contact_method_id="00000000-0000-4000-8000-00000000beef"), 422, "validation_failed"),
            (save_body(d, expected_revision=7), 409, "revision_conflict")):
        r = dclient.post(url, json=body, headers=hdr(D.sara, key()))
        assert (r.status_code, r.get_json()["code"]) == (status, code), body
    r = dclient.post(url, json=save_body(d), headers=hdr(D.huda, key()))
    assert (r.status_code, r.get_json()["code"]) == (403, "forbidden")
    # the legacy rule: no note on a locked case (nor, as in the Phase 5 wrap-up, an archived one)
    for case_id, code in ((D.case_locked, "case_locked"), (D.case_archived, "case_archived")):
        dd = generate(dclient, hdr, kind="call_script", case_id=case_id)[1]["data"]
        r = dclient.post(f"/v2/ai/drafts/{dd['id']}/save-as-note", json=save_body(dd), headers=hdr(D.sara, key()))
        assert (r.status_code, r.get_json()["code"]) == (409, code)
    assert q1(pgc, 'SELECT count(*) FROM "Comments"')[0] == notes
    assert q1(pgc, "SELECT count(*) FROM ai_drafts WHERE status = 'generated'")[0] == 3


# ------------------------------------------------------------------------------ edit, conflict, discard
def test_edit_revision_conflict_and_discard(dclient, hdr, pgc, echo, monkeypatch):
    ready(monkeypatch, pgc)
    echo.answers.append(answer("narrative"))
    d = generate(dclient, hdr, kind="narrative", case_id=D.case)[1]["data"]
    assert d["revision"] == 1
    url = f"/v2/ai/drafts/{d['id']}"
    r = dclient.patch(url, json={"text": "Edited narrative", "expected_revision": 1}, headers=hdr(D.sara, key()))
    assert r.status_code == 200
    m = r.get_json()["data"]
    assert (m["status"], m["revision"], m["edit_count"], m["edited_by"]["id"]) == ("edited", 2, 1, D.sara)
    assert "text" not in m and "content" not in m
    # a stale revision is a conflict for every change
    for method, u, body in (("patch", url, {"text": "Other", "expected_revision": 1}),
                            ("post", url + "/discard", {"expected_revision": 1}),
                            ("post", url + "/save-as-note", save_body(d))):
        r = getattr(dclient, method)(u, json=body, headers=hdr(D.sara, key()))
        assert (r.status_code, r.get_json()["code"]) == (409, "revision_conflict"), u
        assert r.get_json()["error"]["current_revision"] == 2
    g = dclient.get(url, headers=hdr(D.sara)).get_json()["data"]
    assert g["text"] == "Edited narrative" and g["generated_text"] == d["generated_text"] and g["content"] == d["content"]
    # expected_revision can also come from If-Match
    r = dclient.patch(url, json={"text": "Edited twice"}, headers=hdr(D.sara, key(), **{"If-Match": '"2"'}))
    assert r.status_code == 200 and r.get_json()["data"]["revision"] == 3
    # discard clears the content; the draft can no longer change
    r = dclient.post(url + "/discard", json={"expected_revision": 3}, headers=hdr(D.sara, key()))
    assert r.status_code == 200 and r.get_json()["data"]["status"] == "discarded"
    g = dclient.get(url, headers=hdr(D.sara)).get_json()["data"]
    assert (g["text"], g["content"], g["generated_text"], g["content_available"]) == (None, None, None, False)
    assert g["discarded_by"]["id"] == D.sara
    for method, u, body in (("patch", url, {"text": "again", "expected_revision": 4}),
                            ("post", url + "/save-as-note", save_body(g, text="some text"))):
        r = getattr(dclient, method)(u, json=body, headers=hdr(D.sara, key()))
        assert (r.status_code, r.get_json()["code"]) == (409, "draft_discarded")
    events = [e[0] for e in qall(pgc, "SELECT action FROM audit_events WHERE entity_type = 'ai_draft' AND entity_id = %s "
                                      "ORDER BY occurred_at, id", d["id"])]
    assert events == ["ai.draft.generate", "ai.draft.edit", "ai.draft.edit", "ai.draft.discard"]


def test_the_database_guards_the_draft_lifecycle(dclient, hdr, pgc, echo, monkeypatch):
    ready(monkeypatch, pgc)
    echo.answers.append(answer("call_script"))
    d = generate(dclient, hdr, kind="call_script", case_id=D.case)[1]["data"]
    with pytest.raises(psycopg2.Error, match="provenance"):
        run(pgc, "UPDATE ai_drafts SET case_id = %s WHERE id = %s", D.case_other, d["id"])
    with pytest.raises(psycopg2.Error, match="never rewritten"):
        run(pgc, "UPDATE ai_drafts SET content = '{}'::jsonb WHERE id = %s", d["id"])
    with pytest.raises(psycopg2.Error, match="retention task only"):
        run(pgc, "DELETE FROM ai_drafts WHERE id = %s", d["id"])
    with pytest.raises(psycopg2.Error):                       # a saved draft needs its note
        run(pgc, "UPDATE ai_drafts SET status = 'saved_as_note' WHERE id = %s", d["id"])
    run(pgc, "UPDATE ai_drafts SET status = 'edited', text = 'x', edit_count = 1, edited_at = now() WHERE id = %s",
        d["id"])
    with pytest.raises(psycopg2.Error, match="transition"):
        run(pgc, "UPDATE ai_drafts SET status = 'generated' WHERE id = %s", d["id"])
    run(pgc, "UPDATE ai_drafts SET status = 'discarded', discarded_at = now(), discarded_by = %s, content = NULL, "
             "generated_text = NULL, text = NULL WHERE id = %s", D.sara, d["id"])
    with pytest.raises(psycopg2.Error, match="can no longer change"):
        run(pgc, "UPDATE ai_drafts SET status = 'edited', text = 'back' WHERE id = %s", d["id"])


# ------------------------------------------------------------------------------ idempotency, list
def test_generation_is_idempotent_per_key(dclient, hdr, pgc, echo, monkeypatch):
    ready(monkeypatch, pgc)
    echo.answers.append(answer("followup_message"))
    idem = key()
    s1, r1, _ = generate(dclient, hdr, idem=idem, kind="followup_message", case_id=D.case)
    s2, r2, resp2 = generate(dclient, hdr, idem=idem, kind="followup_message", case_id=D.case)
    assert (s1, s2) == (201, 201) and resp2.headers.get("Idempotent-Replayed") == "true"
    assert r2["data"]["id"] == r1["data"]["id"] and len(echo.requests) == 1 and draft_count(pgc) == 1
    s3, r3, _ = generate(dclient, hdr, idem=idem, kind="call_script", case_id=D.case)
    assert (s3, r3["code"]) == (422, "idempotency_key_reused")
    assert q1(pgc, "SELECT count(*) FROM idempotency_keys WHERE key = %s", idem)[0] == 0


def test_list_and_get(dclient, hdr, pgc, echo, monkeypatch):
    ready(monkeypatch, pgc)
    echo.answers += [answer("call_script"), answer("call_summary")]
    a = generate(dclient, hdr, kind="call_script", case_id=D.case)[1]["data"]
    b = generate(dclient, hdr, kind="call_summary", attempt_id=D.call)[1]["data"]
    r = dclient.get(f"/v2/ai/drafts?case={D.case}", headers=hdr(D.sara))
    items = r.get_json()["data"]["items"]
    assert r.headers["Cache-Control"].startswith("no-store")
    assert [i["id"] for i in items] == [b["id"], a["id"]] and r.get_json()["data"]["total"] == 2
    assert "content" not in items[0] and items[0]["text"] == b["text"]
    assert [i["id"] for i in dclient.get(f"/v2/ai/drafts?case={D.case}&kind=call_script",
                                         headers=hdr(D.sara)).get_json()["data"]["items"]] == [a["id"]]
    assert [i["id"] for i in dclient.get(f"/v2/ai/drafts?attempt={D.call}",
                                         headers=hdr(D.sara)).get_json()["data"]["items"]] == [b["id"]]
    assert dclient.get(f"/v2/ai/drafts?case={D.case}&mine=1", headers=hdr(D.huda)).get_json()["data"]["total"] == 0
    assert dclient.get(f"/v2/ai/drafts?case={D.case}", headers=hdr(D.huda)).get_json()["data"]["total"] == 2
    assert dclient.get("/v2/ai/drafts", headers=hdr(D.sara)).status_code == 422
    g = dclient.get(f"/v2/ai/drafts/{a['id']}", headers=hdr(D.sara)).get_json()["data"]
    assert g == a
    assert g["provenance"]["schema"] == "call_script.v1" and g["expires_at"] > g["created_at"]


# ------------------------------------------------------------------------------ retention
@pytest.fixture
def drafts_hook():
    from services.ai import retention
    from services.ai.retention_hooks import drafts as hook_mod
    retention.register(hook_mod.HOOK, "drafts", hook_mod.purge_drafts)
    retention._discovered["done"] = True
    yield hook_mod
    retention.unregister(hook_mod.HOOK)


def age(pgc, draft_id, days):
    with pgc.cursor() as cur:
        cur.execute("SET session_replication_role = replica")
        cur.execute("UPDATE ai_drafts SET created_at = now() - make_interval(days => %s) WHERE id = %s", (days, draft_id))
        cur.execute("SET session_replication_role = DEFAULT")


def test_retention_purges_old_drafts_per_policy(dclient, hdr, pgc, echo, monkeypatch, ai_app, drafts_hook):
    from services.ai import retention
    ready(monkeypatch, pgc, retention_days={"transcripts": 90, "audio": 30, "drafts": 14, "provider_logs": 365})
    echo.answers += [answer("call_script"), answer("call_script"), answer("call_script")]
    old_unsaved = generate(dclient, hdr, kind="call_script", case_id=D.case)[1]["data"]
    old_saved = generate(dclient, hdr, kind="call_script", case_id=D.case)[1]["data"]
    fresh = generate(dclient, hdr, kind="call_script", case_id=D.case)[1]["data"]
    r = dclient.post(f"/v2/ai/drafts/{old_saved['id']}/save-as-note", json=save_body(old_saved),
                     headers=hdr(D.sara, key()))
    note_id = r.get_json()["data"]["note"]["id"]
    age(pgc, old_unsaved["id"], 20)
    age(pgc, old_saved["id"], 20)
    with ai_app.app_context():
        report = retention.run()
    assert report["hooks"][drafts_hook.HOOK]["purged"] == 2 and report["hooks"][drafts_hook.HOOK]["days"] == 14
    assert q1(pgc, "SELECT count(*) FROM ai_drafts WHERE id = %s", old_unsaved["id"])[0] == 0
    row = q1(pgc, "SELECT status, content, text, generated_text, purged_at IS NOT NULL, saved_note_id::text "
                  "FROM ai_drafts WHERE id = %s", old_saved["id"])
    assert row == ("saved_as_note", None, None, None, True, note_id)
    assert q1(pgc, 'SELECT comment FROM "Comments" WHERE id = %s', note_id)[0] == old_saved["text"]   # the note stays
    assert q1(pgc, "SELECT status, text IS NOT NULL FROM ai_drafts WHERE id = %s", fresh["id"]) == ("generated", True)
    ev = q1(pgc, "SELECT after FROM audit_events WHERE action = 'ai_retention.purge' AND entity_id = %s "
                 "ORDER BY occurred_at DESC LIMIT 1", drafts_hook.HOOK)[0]
    assert ev["purged"] == 2 and ev["category"] == "drafts"
    g = dclient.get(f"/v2/ai/drafts/{old_saved['id']}", headers=hdr(D.sara)).get_json()["data"]
    assert g["purged_at"] and g["content_available"] is False and g["saved_note_id"] == note_id
    with ai_app.app_context():
        assert retention.run()["hooks"][drafts_hook.HOOK]["purged"] == 0          # idempotent


def test_no_policy_purges_no_draft(dclient, hdr, pgc, echo, monkeypatch, ai_app, drafts_hook):
    from services.ai import retention
    ready(monkeypatch, pgc)
    echo.answers.append(answer("call_script"))
    d = generate(dclient, hdr, kind="call_script", case_id=D.case)[1]["data"]
    age(pgc, d["id"], 4000)
    run(pgc, "SET session_replication_role = replica")
    run(pgc, "DELETE FROM ai_data_policies")
    run(pgc, "SET session_replication_role = DEFAULT")
    with ai_app.app_context():
        assert retention.run()["policy"] == "missing"
    assert draft_count(pgc) == 1


# ------------------------------------------------------------------------------ audit without content
def test_the_audit_holds_no_content(dclient, hdr, pgc, echo, monkeypatch):
    ready(monkeypatch, pgc)
    t0 = q1(pgc, "SELECT now()")[0]
    echo.answers.append(answer("call_summary"))
    d = generate(dclient, hdr, kind="call_summary", attempt_id=D.call, options={"include_transcript": True})[1]["data"]
    secret_edit = "EDITED-SECRET-TEXT about the debtor"
    dclient.patch(f"/v2/ai/drafts/{d['id']}", json={"text": secret_edit, "expected_revision": 1},
                  headers=hdr(D.sara, key()))
    echo.answers.append(answer("followup_message"))
    d2 = generate(dclient, hdr, kind="followup_message", case_id=D.case)[1]["data"]
    dclient.post(f"/v2/ai/drafts/{d2['id']}/discard", json={"expected_revision": 1}, headers=hdr(D.sara, key()))
    ai_events = qall(pgc, "SELECT to_jsonb(e)::text FROM audit_events e WHERE e.entity_type = 'ai_draft' "
                          "AND e.occurred_at >= %s ORDER BY e.occurred_at, e.id", t0)
    provider_rows = qall(pgc, "SELECT to_jsonb(c)::text FROM ai_provider_calls c")
    assert len(ai_events) == 4 and len(provider_rows) == 2
    blob = "\n".join(r[0] for r in ai_events + provider_rows)
    forbidden = list(IDENTIFIERS) + [TRANSCRIPT_BIT, "wrap-up note", secret_edit, "Spoke with", "Will pay after salary",
                                     "عزيزي", "[NAME_", "licensed Saudi", d["text"][:40], d2["text"][:30]]
    for value in forbidden:
        assert value not in blob, value
    gen = json.loads(ai_events[0][0])["after"]
    assert set(gen) == {"kind", "attempt_id", "language", "options", "categories", "input_hash", "input_summary",
                        "provider_call_id", "model", "policy_revision", "masked", "masked_count", "schema", "checks"}
    assert gen["input_summary"] == {"calls": 1, "wrapup_note": True, "transcript_chars": gen["input_summary"]["transcript_chars"]}


def test_generation_has_no_side_effect_even_when_it_fails(dclient, hdr, pgc, echo, monkeypatch):
    ready(monkeypatch, pgc)
    before = snapshot(pgc, D.case)
    echo.answers += [http(500, {"error": {"type": "server_error"}})] * 3
    status, res, _ = generate(dclient, hdr, kind="case_guidance", case_id=D.case)
    assert (status, res["code"]) == (502, "ai_provider_unavailable")
    assert snapshot(pgc, D.case) == before and draft_count(pgc) == 0


def test_dates_are_riyadh_and_unambiguous_for_the_masker():
    from services.ai import drafts_inputs as I
    assert I.when(dt.datetime(2026, 9, 20, 9, 0, tzinfo=dt.timezone.utc)) == "2026-09-20T12:00"
    from services.ai import masking
    m = masking.Masker(set(masking.CLASSES))
    line = "Call 2026-09-20T12:00 (click-to-call, outbound): ended; duration 180 s"
    assert m.mask(line) == line
