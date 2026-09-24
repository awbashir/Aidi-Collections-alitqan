"""Phase 6 Q2: reviews. The evidence rule on human saves, quotations validated verbatim against the
saved transcript (paraphrases refused), AI drafts through the gateway (request shape, masking, only
the declared categories, server-side repair of bad items, never approved automatically), explicit
approval with an immutable snapshot, evidence changes clearing the current draft and approval
(earlier approvals kept), the metrics (including all-unknown) and the dashboard."""
from __future__ import annotations

import json

import psycopg2
import pytest

from test_quality_support import *  # noqa: F401,F403
from test_quality_support import (CRITERIA, Q, TRANSCRIPT, all_results, audio_ts, configure, insert_policy, q1, qall,
                                  quote, ready_recording, responses_ok, run)


# ------------------------------------------------------------------------------ units
def _ctx(text_segments=TRANSCRIPT, duration=10.0, audio=True, notes="", action=""):
    from services.ai import quality as svc
    from services.ai import quality_evidence as qe
    segs = svc.number_segments(text_segments)
    t = qe.Transcript("\n".join(s["text"] for s in segs), segs)
    return qe.Context(transcript=t, duration=duration, audio_available=audio,
                      inputs={"notes": notes, "recorded_action": action})


def test_quotation_validation_rejects_paraphrases():
    ctx = _ctx()
    t = ctx.transcript
    assert t.find("معك سارة من شركة المثال", [])[0] is None                       # verbatim, no segment cited
    assert t.find("معك سارة من شركة المثال", ["s1"]) == (None, ["s1"])
    assert t.find("  معك   سارة من شركة المثال ", ["s1"])[0] is None                # whitespace only
    assert t.find("معكِ سارة من شركة المثال", [])[0] == "quote_not_found"           # one diacritic added
    assert t.find("معك سارة من الشركة", [])[0] == "quote_not_found"                 # paraphrase
    assert t.find("Sara from the example company", [])[0] == "quote_not_found"      # translation
    assert t.find("معك سارة من شركة المثال", ["s2"])[0] == "quote_not_in_segments"  # wrong segment cited
    assert t.find("معك سارة", ["s99"])[0] == "unknown_segment"
    assert t.find("ن", [])[0] == "quote_too_short"
    # across two consecutive segments (cited or located)
    assert t.find("تفضلي. أستطيع السداد", ["s4", "s5"]) == (None, ["s4", "s5"])
    assert t.find("تفضلي. أستطيع السداد", []) == (None, ["s4", "s5"])
    from services.ai import quality_evidence as qe
    none = qe.Transcript(None, None)
    assert none.find("anything", [])[0] == "no_transcript"


def test_metrics_formulas_including_all_unknown():
    from services.ai.quality_evidence import metrics

    def res(*rs):
        return [{"result": r} for r in rs]
    m = metrics(res("met", "met", "needs_improvement", "not_applicable", "insufficient_evidence"))
    assert (m["met"], m["needs_improvement"], m["assessed"], m["total"]) == (2, 1, 3, 5)
    assert m["compliance"] == {"numerator": 2, "denominator": 3, "ratio": 0.666667, "percent": 66.7}
    assert m["coverage"] == {"numerator": 3, "denominator": 5, "ratio": 0.6, "percent": 60.0}
    unknown = metrics(res("not_applicable", "insufficient_evidence", "insufficient_evidence"))
    assert unknown["compliance"] is None and unknown["has_percentage"] is False
    assert unknown["coverage"]["percent"] == 0.0
    assert metrics(res("needs_improvement"))["compliance"]["percent"] == 0.0
    assert metrics(res("met"))["compliance"]["percent"] == 100.0


# ------------------------------------------------------------------------------ human saves
def test_evidence_rule_on_human_saves(qc):
    rec = ready_recording(qc)
    rid = rec["id"]

    def errors(results):
        return qc.review(rid, results, expect=422)["error"]["criteria"]

    # a reason for every criterion
    assert errors(all_results(reason=""))["intro"] == ["reason_required"]
    # met without evidence; a paraphrase; a quote in the wrong segment
    e = errors(all_results(intro={"result": "met"},
                           debt={"result": "needs_improvement", "evidence": [quote("قال إنه سيدفع الأحد")]},
                           listening={"result": "met", "evidence": [quote("وعليكم السلام", "s1")]}))
    assert e == {"intro": ["evidence_required"], "debt": ["evidence_required", "quote_not_found"],
                 "listening": ["evidence_required", "quote_not_in_segments"]}
    # audible delivery: a quotation is not enough; an unreviewed or out-of-range timestamp is refused
    e = errors(all_results(delivery={"result": "met", "evidence": [quote("وعليكم السلام", "s2")]}))
    assert e == {"delivery": ["audio_evidence_required"]}
    e = errors(all_results(delivery={"result": "met", "evidence": [audio_ts(3.0, 5.0, reviewed=False)]},
                           intro={"result": "met", "evidence": [audio_ts(3.0, 99.0)]}))
    assert e == {"delivery": ["audio_evidence_required", "audio_not_human_reviewed"],
                 "intro": ["evidence_required", "invalid_timestamp"]}
    # notes / recorded action criteria need the input
    e = errors(all_results(notes={"result": "met", "evidence": [quote("أستطيع السداد يوم الأحد القادم.")]}))
    assert e == {"notes": ["input_missing"]}
    # structure: every criterion exactly once, known ids only, known results
    short = all_results()[:-1]
    assert errors(short) == {"delivery": ["criterion_missing"]}
    dup = all_results() + [all_results()[0]]
    assert errors(dup) == {"intro": ["duplicate_criterion"]}
    assert "_" in errors(all_results() + [{"criterion_id": "price", "result": "met", "reason": "x"}])
    assert errors(all_results(debt={"result": "pass"})) == {"debt": ["invalid_result"]}
    # a valid save: exact quotes (timestamps from the cited segments) and a human-reviewed audio timestamp
    ok = qc.review(rid, all_results(
        intro={"result": "met", "reason": "Name, organization and purpose stated.",
               "evidence": [quote("معك سارة من شركة المثال للتحصيل", "s1")]},
        next_steps={"result": "needs_improvement", "reason": "The date was not confirmed back.",
                    "evidence": [quote("أستطيع السداد يوم الأحد القادم.")]},
        affordability={"result": "not_applicable", "reason": "No payment difficulty raised."},
        delivery={"result": "met", "reason": "Clear and courteous.", "evidence": [audio_ts(0.5, 2.0)]}),
        summary="ملخص", coaching="Confirm the date.")
    rv = ok["review"]
    assert ok["status"] == "human_draft" and rv["origin"] == "human" and rv["version"] == 1
    intro = next(x for x in rv["results"] if x["criterion_id"] == "intro")
    assert intro["evidence"] == [{"type": "quote", "text": "معك سارة من شركة المثال للتحصيل", "segment_ids": ["s1"],
                                  "start": 0.0, "end": 2.0}]
    nxt = next(x for x in rv["results"] if x["criterion_id"] == "next_steps")
    assert nxt["evidence"][0]["segment_ids"] == ["s5"] and nxt["evidence"][0]["start"] == 7.0
    assert rv["metrics"]["compliance"] == {"numerator": 2, "denominator": 3, "ratio": 0.666667, "percent": 66.7}
    assert rv["metrics"]["coverage"]["numerator"] == 3 and rv["metrics"]["total"] == 10


# ------------------------------------------------------------------------------ AI drafts
def _ai_answer(**over):
    items = []
    for cid in CRITERIA:
        items.append({"criterion_id": cid, "result": "insufficient_evidence", "reason": "لا يوجد دليل كافٍ.",
                      "quotations": []})
    by = {i["criterion_id"]: i for i in items}
    for cid, patch in over.items():
        by[cid].update(patch)
    return {"summary": "مسودة", "coaching": "خطة", "criteria": items}


def test_ai_draft_request_masking_categories_and_validation(qc, pgq, fake, monkeypatch):
    configure(monkeypatch)
    insert_policy(pgq, allowed_categories=["transcripts"])
    rec = ready_recording(qc, case_id=Q.KA1)
    rid = rec["id"]
    answer = _ai_answer(
        # valid: exact quote with its segment (the masked placeholders are mapped back by the gateway)
        intro={"result": "met", "reason": "تعريف واضح.", "quotations": [
            {"segment_ids": ["s1"], "text": "معك [EMPLOYEE_1] من شركة المثال للتحصيل"}]},
        identity={"result": "met", "reason": "تحقق.", "quotations": [
            {"segment_ids": ["s3"], "text": "هل أتحدث مع [NAME_1]؟"}]},
        # a paraphrase: rejected, downgraded to insufficient_evidence with a note
        debt={"result": "needs_improvement", "reason": "لم يشرح المبلغ.", "quotations": [
            {"segment_ids": ["s3"], "text": "سأل الموظف عن المبلغ"}]},
        # audible delivery judged from text: forced to insufficient evidence (human listening only)
        delivery={"result": "met", "reason": "النبرة لطيفة.", "quotations": [
            {"segment_ids": ["s1"], "text": "السلام عليكم"}]},
        # no reason: insufficient evidence with a note
        listening={"result": "met", "reason": "", "quotations": [{"segment_ids": ["s2"], "text": "وعليكم السلام."}]},
        # needs the employee notes, which are missing
        notes={"result": "met", "reason": "مطابقة.", "quotations": [{"segment_ids": ["s5"],
                                                                      "text": "أستطيع السداد يوم الأحد القادم."}]},
    )
    fake.script.append(responses_ok(answer, model="gpt-test-text-2026"))
    qc.evidence(rid, employee_label="سارة")
    rev = qc.detail(rid)["revision"]
    r = qc.post(f"/v2/quality/recordings/{rid}/draft", Q.rev_a, {"expected_revision": rev, "language": "ar"})
    assert r.status_code == 200, r.get_json()
    d = r.get_json()["data"]
    rv = d["review"]
    assert d["status"] == "ai_draft" and rv["origin"] == "ai" and rv["model"] == "gpt-test-text-2026"
    by = {x["criterion_id"]: x for x in rv["results"]}
    assert by["intro"]["result"] == "met" and by["intro"]["evidence"][0]["text"] == "معك سارة من شركة المثال للتحصيل"
    assert by["identity"]["result"] == "met", by["identity"]
    assert by["identity"]["evidence"][0]["text"] == f"هل أتحدث مع {Q.KA1_NAME}؟"
    assert by["debt"]["result"] == "insufficient_evidence" and by["debt"]["ai_result"] == "needs_improvement"
    assert set(by["debt"]["validation"]) == {"quote_not_in_segments", "evidence_required"} and by["debt"]["evidence"] == []
    assert by["delivery"]["result"] == "insufficient_evidence" and "human_listening_only" in by["delivery"]["validation"]
    assert by["delivery"]["evidence"] == [] and by["delivery"]["ai_result"] == "met"
    assert by["listening"]["result"] == "insufficient_evidence" and by["listening"]["reason"]
    assert by["notes"]["result"] == "insufficient_evidence" and "input_missing" in by["notes"]["validation"]
    assert {v["criterion_id"] for v in rv["validation"]} == {"debt", "delivery", "listening", "notes"}
    # the request: Responses API, store false, strict schema with the template's criterion ids
    body = fake.json(0)
    assert fake.requests[0].url.endswith("/responses") and body["store"] is False
    fmt = body["text"]["format"]
    assert fmt["type"] == "json_schema" and fmt["strict"] is True and fmt["name"] == "qc_review_draft"
    item = fmt["schema"]["properties"]["criteria"]["items"]
    assert item["properties"]["criterion_id"]["enum"] == CRITERIA
    assert item["properties"]["result"]["enum"] == ["met", "needs_improvement", "not_applicable", "insufficient_evidence"]
    assert "Arabic" in body["instructions"] and "human_listening_only" in body["instructions"]
    assert "[NAME_" not in body["instructions"]
    sent = json.dumps(fake.json(0), ensure_ascii=False)
    # masked: the debtor's name and number, and the employee label never leave the server
    for secret in (Q.KA1_NAME, Q.KA1_PHONE, "سارة"):
        assert secret not in sent, secret
    assert "[NAME_1]" in sent and "[PHONE_1]" in sent and "[EMPLOYEE_1]" in sent
    payload = json.loads(body["input"][0]["content"])
    assert [s["id"] for s in payload["transcript"]["segments"]] == ["s1", "s2", "s3", "s4", "s5"]
    assert payload["employee_notes"] is None and payload["transcript"]["speaker_labels_are_anonymous"] is True
    # only the declared categories: the transcript, no notes (none were entered)
    row = q1(pgq, "SELECT purpose, data_categories, masked, entity_type, case_id::text FROM ai_provider_calls "
                  "WHERE id = %s", rv["provider_call_id"])
    assert row == ("qc_review_draft", ["transcripts"], True, "qc_recording", Q.KA1)
    # an AI draft is never approved
    a = qc.approve(rid, expect=422)
    assert a["code"] == "human_review_required"


def test_ai_draft_with_notes_needs_the_notes_category(qc, pgq, fake, monkeypatch):
    configure(monkeypatch)
    insert_policy(pgq, allowed_categories=["transcripts"])
    rec = ready_recording(qc, notes="العميل وعد بالسداد يوم الأحد", action="promise")
    rev = rec["revision"]
    r = qc.post(f"/v2/quality/recordings/{rec['id']}/draft", Q.rev_a, {"expected_revision": rev})
    assert r.status_code == 409 and r.get_json()["code"] == "ai_policy_category_denied"
    assert r.get_json()["error"]["denied_categories"] == ["notes"] and fake.requests == []
    assert qc.detail(rec["id"])["processing"] is None
    insert_policy(pgq, allowed_categories=["transcripts", "notes"])
    fake.script.append(responses_ok(_ai_answer(notes={"result": "met", "reason": "الملاحظة تطابق الحوار.",
                                                      "quotations": [{"segment_ids": ["s5"],
                                                                      "text": "أستطيع السداد يوم الأحد القادم."}]})))
    r = qc.post(f"/v2/quality/recordings/{rec['id']}/draft", Q.rev_a, {"expected_revision": rev, "language": "en"})
    assert r.status_code == 200, r.get_json()
    rv = r.get_json()["data"]["review"]
    assert next(x for x in rv["results"] if x["criterion_id"] == "notes")["result"] == "met"
    assert q1(pgq, "SELECT data_categories FROM ai_provider_calls WHERE id = %s", rv["provider_call_id"])[0] == \
        ["notes", "transcripts"]
    assert "English" in fake.json(0)["instructions"]
    payload = json.loads(fake.json(0)["input"][0]["content"])
    assert payload["employee_notes"] == "العميل وعد بالسداد يوم الأحد" and payload["recorded_action"] == "promise"


def test_ai_draft_needs_template_and_transcript(qc, pgq, fake, monkeypatch):
    configure(monkeypatch)
    insert_policy(pgq, allowed_categories=["transcripts"])
    rec = qc.new_recording()
    r = qc.post(f"/v2/quality/recordings/{rec['id']}/draft", Q.rev_a, {"expected_revision": 1})
    assert r.status_code == 422 and r.get_json()["code"] == "template_required"
    qc.evidence(rec["id"], template={"key": "collections-v1", "version": 1})
    r = qc.post(f"/v2/quality/recordings/{rec['id']}/draft", Q.rev_a, {"expected_revision": 2})
    assert r.status_code == 422 and r.get_json()["code"] == "transcript_required" and fake.requests == []


# ------------------------------------------------------------------------------ approval
def _good_results():
    return all_results(
        intro={"result": "met", "reason": "Clear introduction.", "evidence": [quote("معك سارة من شركة المثال للتحصيل")]},
        next_steps={"result": "needs_improvement", "reason": "Date not confirmed.",
                    "evidence": [quote("أستطيع السداد يوم الأحد القادم.", "s5")]},
        delivery={"result": "met", "reason": "Courteous (listened).", "evidence": [audio_ts(0.0, 2.0)]})


def test_approval_snapshot_immutability_and_evidence_change(qc, pgq):
    rec = ready_recording(qc, case_id=Q.KA1)
    rid = rec["id"]
    # permissions and preconditions
    assert qc.approve(rid, expect=422)["code"] == "review_required"
    qc.review(rid, _good_results(), summary="s", coaching="c")
    no_key = qc.post(f"/v2/quality/recordings/{rid}/approve", Q.rev_a,
                     {"expected_revision": qc.detail(rid)["revision"], "confirmed": True})
    assert no_key.status_code == 403
    assert qc.approve(rid, expect=422, confirmed=False)["code"] == "confirmation_required"
    stale = qc.post(f"/v2/quality/recordings/{rid}/approve", Q.app_a, {"expected_revision": 1, "confirmed": True})
    assert stale.status_code == 409 and stale.get_json()["code"] == "revision_conflict"
    rev_before = qc.detail(rid)["revision"]
    d = qc.approve(rid)
    assert d["status"] == "approved" and d["revision"] == rev_before + 1
    ap = d["approval"]
    assert ap["approved_revision"] == rev_before and ap["approved_by"]["id"] == Q.app_a and ap["current"] is True
    assert qc.approve(rid, expect=409)["code"] == "already_approved"
    # the snapshot: template, transcript, segments, evidence, review, metrics
    full = qc.get(f"/v2/quality/approvals/{ap['id']}", Q.rev_a).get_json()["data"]
    snap = full["snapshot"]
    assert snap["template"]["key"] == "collections-v1" and len(snap["template"]["criteria"]) == 10
    assert snap["transcript"]["segments"][0]["text"] == TRANSCRIPT[0]["text"]
    assert snap["review"]["origin"] == "human" and snap["metrics"]["compliance"]["percent"] == 66.7
    assert snap["recording"]["case_id"] == Q.KA1 and snap["approval"]["approved_revision"] == rev_before
    from services.ai.quality import canonical_sha256
    assert canonical_sha256(snap) == full["snapshot_sha256"]
    # immutable in the database
    for sql in ("UPDATE qc_approvals SET met = 9 WHERE id = %s",
                "UPDATE qc_approvals SET snapshot = '{}'::jsonb WHERE id = %s",
                "DELETE FROM qc_approvals WHERE id = %s"):
        with pytest.raises(psycopg2.Error) as ei:
            run(pgq, sql, ap["id"])
        assert "immutable" in str(ei.value)
    with pytest.raises(psycopg2.Error):
        run(pgq, "UPDATE qc_reviews SET met = 0 WHERE recording_id = %s", rid)
    # changing the evidence clears the current draft and approval; the earlier approval stays
    d2 = qc.evidence(rid, notes="Customer promised Sunday.")
    assert d2["status"] == "transcribed" and d2["review"] is None and d2["approval"] is None
    hist = qc.get(f"/v2/quality/recordings/{rid}/approvals", Q.rev_a).get_json()["data"]
    assert [h["id"] for h in hist] == [ap["id"]] and hist[0]["current"] is False
    again = qc.get(f"/v2/quality/approvals/{ap['id']}", Q.rev_a).get_json()["data"]
    assert again["snapshot"] == snap and again["snapshot_sha256"] == full["snapshot_sha256"]
    assert again["snapshot"]["evidence"]["notes"] is None
    # re-review and approve again: two approvals in the history, the newest current
    qc.review(rid, _good_results())
    d3 = qc.approve(rid)
    hist = qc.get(f"/v2/quality/recordings/{rid}/approvals", Q.rev_a).get_json()["data"]
    assert len(hist) == 2 and hist[0]["id"] == d3["approval"]["id"] and hist[0]["current"] is True
    # a transcript correction also clears it
    d4 = qc.transcript(rid, speakers={"A": "agent"})
    assert d4["approval"] is None and d4["review"] is None
    revs = qc.get(f"/v2/quality/recordings/{rid}/reviews", Q.rev_a).get_json()["data"]
    assert [r["version"] for r in revs] == [2, 1]
    # other scope: the approval is not visible
    assert qc.get(f"/v2/quality/approvals/{ap['id']}", Q.rev_b).status_code == 404
    ev = qall(pgq, "SELECT action FROM audit_events WHERE entity_type = 'qc_recording' AND entity_id = %s "
                   "ORDER BY occurred_at, id", rid)
    assert [e[0] for e in ev].count("qc.review.approve") == 2


def test_approval_rechecks_evidence(qc, pgq):
    rec = ready_recording(qc)
    rid = rec["id"]
    qc.review(rid, _good_results())
    # the audio disappears (retention) after the review was saved: its audio timestamp is no longer valid
    run(pgq, "UPDATE qc_recordings SET stored_path = NULL, audio_purged_at = now() WHERE id = %s", rid)
    r = qc.approve(rid, expect=422)
    assert r["code"] == "evidence_rule_failed" and r["error"]["criteria"] == {
        "delivery": ["audio_evidence_required", "audio_unavailable"]}


# ------------------------------------------------------------------------------ dashboard
def _dash(qc, user=Q.rev_a, **params):
    qs = "&".join(f"{k}={v}" for k, v in {"contract_id": Q.contract_a, "template": "collections-v1", **params}.items())
    r = qc.get(f"/v2/quality/dashboard?{qs}", user)
    assert r.status_code == 200, r.get_json()
    return r.get_json()["data"]


KEYS = ("reviews", "met", "needs_improvement", "not_applicable", "insufficient_evidence", "total",
        "reviews_with_percentage", "reviews_without_percentage")


def _delta(after, before):
    return tuple(after[k] - before[k] for k in KEYS)


def test_dashboard_counts_approved_reviews_only(qc, pgq):
    before = _dash(qc)
    crit_before = {x["criterion_id"]: x for x in before["by_criterion"]}
    a1 = ready_recording(qc, case_id=Q.KA1)
    qc.review(a1["id"], _good_results())
    qc.approve(a1["id"])                                          # met 2, NI 1, NA 0, IE 7
    a2 = ready_recording(qc, case_id=Q.KA1)
    qc.review(a2["id"], all_results())                            # all unknown: no percentage
    qc.approve(a2["id"])
    a3 = ready_recording(qc, case_id=Q.KA1)
    qc.review(a3["id"], _good_results())                          # a draft only: not counted
    a4 = ready_recording(qc, case_id=Q.KA1)
    qc.review(a4["id"], _good_results())
    qc.approve(a4["id"])
    qc.evidence(a4["id"], context="changed")                      # approval cleared: not counted
    d = _dash(qc)
    t = d["totals"]
    assert _delta(t, before["totals"]) == (2, 2, 1, 0, 17, 20, 1, 1)
    # the formulas over the exact pooled counts
    assessed = t["met"] + t["needs_improvement"]
    assert t["compliance"] == {"numerator": t["met"], "denominator": assessed,
                               "ratio": round(t["met"] / assessed, 6), "percent": round(100 * t["met"] / assessed, 1)}
    assert t["coverage"]["numerator"] == assessed and t["coverage"]["denominator"] == t["total"]
    assert d["basis"] == "approved_reviews_only" and "not an employee ranking" in d["note"]
    assert [x["template"]["key"] for x in d["by_template"]] == ["collections-v1"]
    assert _delta(d["by_template"][0], before["by_template"][0] if before["by_template"] else
                  {k: 0 for k in KEYS}) == (2, 2, 1, 0, 17, 20, 1, 1)
    crit = {x["criterion_id"]: x for x in d["by_criterion"]}
    assert list(crit) == CRITERIA
    zero = {k: 0 for k in KEYS}
    assert _delta(crit["intro"], crit_before.get("intro", zero))[:5] == (2, 1, 0, 0, 1)
    assert _delta(crit["delivery"], crit_before.get("delivery", zero))[:5] == (2, 1, 0, 0, 1)
    assert crit["delivery"]["human_listening_only"] is True and crit["intro"]["name_ar"] == "التعريف بالنفس والغرض"
    assert [c["contract"]["id"] for c in d["by_contract"]] == [Q.contract_a]
    assert sum(p["reviews"] for p in d["by_period"]) == t["reviews"]
    # only approved: a single-recording view of the same numbers
    rows = q1(pgq, "SELECT count(*) FROM qc_approvals a JOIN qc_recordings r ON r.current_approval_id = a.id "
                   "WHERE a.contract_id = %s", Q.contract_a)[0]
    assert rows == t["reviews"]
    # scope: a collector of contract B sees none of these
    other = _dash(qc, user=Q.rev_b)
    assert other["totals"]["reviews"] == 0 and other["totals"]["compliance"] is None
    assert qc.get("/v2/quality/dashboard?group=year", Q.rev_a).status_code == 422
    assert qc.get("/v2/quality/dashboard?from=2026-09-30&to=2026-09-01", Q.rev_a).status_code == 422
    assert qc.get("/v2/quality/dashboard", Q.none).status_code == 403
