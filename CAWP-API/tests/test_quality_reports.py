"""Phase 6 Q2: the HTML report (Arabic and English; draft, approved and superseded; provenance and
evidence), the retention hooks (audio files purged with a durable intent and removed after the
commit; transcript text purged, also inside approval snapshots; idempotent; nothing without a
policy), and the audit trail carrying no content."""
from __future__ import annotations

import datetime as dt
import json

from test_quality_support import *  # noqa: F401,F403
from test_quality_support import (Q, TRANSCRIPT, all_results, audio_ts, configure, insert_policy, q1, qall, quote,
                                  ready_recording, responses_ok, run, wav_bytes)


def _results():
    return all_results(
        intro={"result": "met", "reason": "Clear <b>introduction</b>.",
               "evidence": [quote("معك سارة من شركة المثال للتحصيل", "s1")]},
        next_steps={"result": "needs_improvement", "reason": "لم يؤكد الموظف التاريخ.",
                    "evidence": [quote("أستطيع السداد يوم الأحد القادم.", "s5")]},
        delivery={"result": "met", "reason": "Courteous (listened).", "evidence": [audio_ts(1.25, 2.5)]})


def _report(qc, rid, language, user=Q.rev_a, **params):
    qs = "&".join([f"language={language}"] + [f"{k}={v}" for k, v in params.items()])
    r = qc.get(f"/v2/quality/recordings/{rid}/report?{qs}", user)
    assert r.status_code == 200, r.get_json()
    assert r.headers["Content-Type"] == "text/html; charset=utf-8" and r.headers["Cache-Control"] == "no-store"
    assert "attachment" in r.headers["Content-Disposition"] and "default-src 'none'" in \
        r.headers["Content-Security-Policy"]
    return r.get_data(as_text=True)


def test_report_contents_in_both_languages(qc, pgq, fake, monkeypatch):
    rec = ready_recording(qc, case_id=Q.KA1)
    rid = rec["id"]
    assert qc.get(f"/v2/quality/recordings/{rid}/report?language=ar", Q.rev_a).status_code == 422   # no review yet
    qc.review(rid, _results(), summary="ملخص المراجعة", coaching="Confirm the payment date.")
    en = _report(qc, rid, "en")
    assert '<html lang="en" dir="ltr">' in en and "DRAFT: human review saved, awaiting approval. Not approved." in en
    assert "This report is a draft." in en
    assert "Collections · conversation quality" in en and "collections-v1" in en and "version 1" in en
    assert "Manual transcript" in en or "Human correction" in en
    assert "A (anonymous (not verified))" in en                       # speaker labels stay anonymous
    assert "معك سارة من شركة المثال للتحصيل" in en                    # the quotation, verbatim
    assert "0:07.000–0:09.000" in en and "segments s5" in en           # segment timestamps
    assert "Human-reviewed audio timestamp: 0:01.250–0:02.500" in en
    assert "Clear &lt;b&gt;introduction&lt;/b&gt;." in en and "<b>introduction</b>" not in en   # escaped
    assert "<strong>66.7%</strong> (2/3)" in en and "30.0% (3/10)" in en
    assert "not an employee ranking" in en and "human listening only" in en and "conditional" in en
    assert rec["audio"]["sha256"] in en and f"Case: {Q.KA1}" in en
    assert "Reviewer-supplied evidence" in en and "Employee notes" in en
    ar = _report(qc, rid, "ar")
    assert '<html lang="ar" dir="rtl">' in ar and "مسودة: مراجعة بشرية محفوظة بانتظار الاعتماد" in ar
    assert "التحصيل · جودة الحوار" in ar and "التعريف بالنفس والغرض" in ar and "يحتاج تحسيناً" in ar
    assert "مستوفى" in ar and "أدلة غير كافية" in ar and "ليست ترتيباً للموظفين" in ar
    assert "استماع بشري فقط" in ar and "لم يؤكد الموظف التاريخ." in ar
    # approved: the status and who approved it
    qc.approve(rid)
    en = _report(qc, rid, "en")
    assert "Status: APPROVED after human review" in en and "approved by: Q2 Approver A" in en
    assert "This report is a draft." not in en
    ar = _report(qc, rid, "ar")
    assert "معتمد بعد مراجعة بشرية" in ar
    ap_id = qc.detail(rid)["approval"]["id"]
    # the evidence changes: the old approval's report is "superseded" and still shows its snapshot
    qc.evidence(rid, notes="changed later")
    old = _report(qc, rid, "en", approval_id=ap_id)
    assert "APPROVED (superseded: the evidence changed later)" in old and "معك سارة من شركة المثال للتحصيل" in old
    assert qc.get(f"/v2/quality/recordings/{rid}/report?language=en", Q.rev_a).status_code == 422
    assert qc.get(f"/v2/quality/recordings/{rid}/report?language=fr&approval_id={ap_id}", Q.rev_a).status_code == 422
    assert qc.get(f"/v2/quality/recordings/{rid}/report?language=en&approval_id={ap_id}", Q.rev_b).status_code == 404
    # an AI draft report says so, with the model and provider-call provenance
    configure(monkeypatch)
    insert_policy(pgq, allowed_categories=["transcripts", "notes"])
    items = [{"criterion_id": x["criterion_id"], "result": "insufficient_evidence", "reason": "لا دليل.",
              "quotations": []} for x in all_results()]
    fake.script.append(responses_ok({"summary": "s", "coaching": "c", "criteria": items}, model="gpt-test-text-2026"))
    r = qc.post(f"/v2/quality/recordings/{rid}/draft", Q.rev_a, {"expected_revision": qc.detail(rid)["revision"]})
    assert r.status_code == 200, r.get_json()
    call_id = r.get_json()["data"]["review"]["provider_call_id"]
    en = _report(qc, rid, "en")
    assert "DRAFT: AI draft awaiting human review. Not approved." in en and "gpt-test-text-2026" in en
    assert call_id in en and "No percentage" in en
    exports = qall(pgq, "SELECT after FROM audit_events WHERE action = 'qc.report.export' AND entity_id = %s", rid)
    assert len(exports) == 6 and {e[0]["language"] for e in exports} == {"ar", "en"}


# ------------------------------------------------------------------------------ retention
import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _restore_retention_registry():
    """discover() registers every hook module; restore the registry so other test modules (e.g. the Q0
    retention tests, which assert their exact hooks) are not affected by the order of the run."""
    from services.ai import retention
    hooks, done = dict(retention._HOOKS), retention._discovered["done"]
    yield
    retention._HOOKS.clear()
    retention._HOOKS.update(hooks)
    retention._discovered["done"] = done


def _retention(qc_app, now=None):
    """One AI retention run with exactly the Q2 hooks registered (the module is found by discover())."""
    from services.ai import retention
    from services.ai.retention_hooks import quality as qh
    assert "services.ai.retention_hooks.quality" in retention.discover()
    retention._HOOKS.clear()
    retention.register("q2.qc_audio", "audio", qh.purge_audio)
    retention.register("q2.qc_transcripts", "transcripts", qh.purge_transcripts)
    retention._discovered["done"] = True
    with qc_app.app_context():
        return retention.run(now=now)


def test_retention_purges_audio_files_and_transcripts(qc, qc_app, pgq, storage):
    rec = ready_recording(qc)
    rid = rec["id"]
    qc.review(rid, _results())
    approved = qc.approve(rid)
    ap_id = approved["approval"]["id"]
    path = q1(pgq, "SELECT stored_path FROM qc_recordings WHERE id = %s", rid)[0]
    f = storage / path
    assert f.exists()
    # no policy: nothing is purged
    report = _retention(qc_app, now=dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=5000))
    assert report.get("policy") == "missing" and f.exists()
    insert_policy(pgq, retention_days={"transcripts": 90, "audio": 30, "drafts": 14, "provider_logs": 365})
    # inside the periods: nothing
    report = _retention(qc_app)
    assert report["hooks"]["q2.qc_audio"]["purged"] == 0 and f.exists()
    # 40 days later: the audio (30 days) goes, the transcript (90 days) stays
    report = _retention(qc_app, now=dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=40))
    assert report["hooks"]["q2.qc_audio"]["purged"] >= 1 and report["hooks"]["q2.qc_transcripts"]["purged"] == 0
    assert not f.exists()
    row = q1(pgq, "SELECT stored_path, audio_purged_at IS NOT NULL, sha256 FROM qc_recordings WHERE id = %s", rid)
    assert row[0] is None and row[1] is True and row[2] == rec["audio"]["sha256"]
    intent = qall(pgq, "SELECT stored_path, done_at IS NOT NULL FROM qc_file_purges WHERE recording_id = %s", rid)
    assert intent == [(path, True)]
    audio = qc.get(f"/v2/quality/recordings/{rid}/audio", Q.rev_a)
    assert audio.status_code == 410 and audio.get_json()["code"] == "audio_purged"
    d = qc.detail(rid)
    assert d["audio"]["available"] is False and d["audio"]["url"] is None and d["status"] == "approved"
    # the same audio may be imported again once the old copy is purged (duplicates count live audio only)
    ev = qall(pgq, "SELECT after FROM audit_events WHERE action = 'qc.audio.purge' AND entity_id = %s", rid)
    assert len(ev) == 1 and ev[0][0]["purged"] is True
    # a second run is idempotent
    again = _retention(qc_app, now=dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=40))
    assert again["hooks"]["q2.qc_audio"]["purged"] == 0
    # 100 days later: the transcript text goes, from the version and from the approval snapshot
    report = _retention(qc_app, now=dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=100))
    assert report["hooks"]["q2.qc_transcripts"]["purged"] >= 1
    t = qc.detail(rid)["transcript"]
    assert t["purged"] is True and t["text"] is None and t["segments"] is None and t["text_sha256"]
    snap = qc.get(f"/v2/quality/approvals/{ap_id}", Q.rev_a).get_json()["data"]
    assert snap["transcript_purged"] is True and snap["snapshot"]["transcript"]["text"] is None
    assert snap["snapshot"]["transcript"]["segments"] is None and snap["snapshot"]["transcript"]["purged"] is True
    assert snap["snapshot"]["review"]["results"][0]["reason"]           # the findings stay
    purge_events = qall(pgq, "SELECT entity_id, after FROM audit_events WHERE action = 'ai_retention.purge' "
                             "AND entity_id IN ('q2.qc_audio', 'q2.qc_transcripts')")
    assert {e[0] for e in purge_events} == {"q2.qc_audio", "q2.qc_transcripts"}
    # outside retention the snapshot stays immutable
    import psycopg2
    try:
        run(pgq, "UPDATE qc_approvals SET snapshot = snapshot - 'transcript' WHERE id = %s", ap_id)
        raise AssertionError("update allowed")
    except psycopg2.Error as e:
        assert "immutable" in str(e)


def test_retention_completes_an_interrupted_file_purge(qc, qc_app, pgq, storage):
    """A crash after the commit and before the unlink leaves a pending intent; the next run removes the file."""
    rec = qc.new_recording()
    path = q1(pgq, "SELECT stored_path FROM qc_recordings WHERE id = %s", rec["id"])[0]
    f = storage / path
    run(pgq, "INSERT INTO qc_file_purges (recording_id, stored_path, reason) VALUES (%s, %s, 'test: crashed run')",
        rec["id"], path)
    run(pgq, "UPDATE qc_recordings SET stored_path = NULL, audio_purged_at = now() WHERE id = %s", rec["id"])
    insert_policy(pgq)
    assert f.exists()
    _retention(qc_app)
    assert not f.exists()
    assert q1(pgq, "SELECT count(*) FROM qc_file_purges WHERE recording_id = %s AND done_at IS NULL", rec["id"])[0] == 0


def test_rolled_back_purge_never_removes_a_file(qc, qc_app, pgq, storage):
    from services.ai.retention_hooks import quality as hook
    rec = qc.new_recording()
    run(pgq, "UPDATE qc_recordings SET created_at = now() - interval '400 days' WHERE id = %s", rec["id"])
    path = q1(pgq, "SELECT stored_path FROM qc_recordings WHERE id = %s", rec["id"])[0]
    with qc_app.app_context():
        from extensions import db
        n = hook.purge_audio(db.session, cutoff=dt.datetime.now(dt.timezone.utc), limit=1000)
        assert n >= 1
        db.session.rollback()
        db.session.execute(__import__("sqlalchemy").text("SELECT 1"))
        db.session.commit()                                         # the stale listener fires: nothing committed
    assert (storage / path).exists()
    assert q1(pgq, "SELECT stored_path FROM qc_recordings WHERE id = %s", rec["id"])[0] == path
    run(pgq, "UPDATE qc_recordings SET created_at = now() WHERE id = %s", rec["id"])


# ------------------------------------------------------------------------------ audit
def test_the_audit_has_no_content(qc, pgq, fake, monkeypatch):
    configure(monkeypatch)
    insert_policy(pgq, allowed_categories=["transcripts", "notes", "audio"])
    secret_note = "ملاحظة سرية عن العميل 0551234567"
    rec = ready_recording(qc, case_id=Q.KA1, notes=secret_note, action="promise to pay",
                          title="Title is fine")
    rid = rec["id"]
    items = [{"criterion_id": x["criterion_id"], "result": "insufficient_evidence", "reason": "سبب خاص بالنموذج",
              "quotations": []} for x in all_results()]
    fake.script.append(responses_ok({"summary": "ملخص النموذج الخاص", "coaching": "تدريب خاص", "criteria": items}))
    assert qc.post(f"/v2/quality/recordings/{rid}/draft", Q.rev_a,
                   {"expected_revision": qc.detail(rid)["revision"]}).status_code == 200
    qc.review(rid, _results(), summary="ملخص بشري خاص", coaching="تدريب بشري خاص")
    qc.approve(rid)
    qc.get(f"/v2/quality/recordings/{rid}/audio", Q.rev_a, Range="bytes=0-9")
    qc.get(f"/v2/quality/recordings/{rid}/report?language=en", Q.rev_a)
    rows = qall(pgq, "SELECT action, before::text, after::text, coalesce(reason, '') FROM audit_events "
                     "WHERE entity_type = 'qc_recording' AND entity_id = %s", rid)
    actions = {r[0] for r in rows}
    assert {"qc.recording.import", "qc.evidence.update", "qc.transcript.save", "qc.review.draft", "qc.review.save",
            "qc.review.approve", "qc.audio.read", "qc.report.export"} <= actions
    blob = json.dumps([list(r) for r in rows], ensure_ascii=False)
    for content in (secret_note, "0551234567", "promise to pay", "ملخص النموذج الخاص", "سبب خاص بالنموذج",
                    "ملخص بشري خاص", "معك سارة", TRANSCRIPT[4]["text"], "Courteous", Q.KA1_NAME):
        assert content not in blob, content
    calls = qall(pgq, "SELECT * FROM ai_provider_calls WHERE entity_id = %s", rid)
    calls_blob = json.dumps([[str(c) for c in row] for row in calls], ensure_ascii=False)
    assert calls and secret_note not in calls_blob and "0551234567" not in calls_blob
    # the import audit names the hash, not the filename
    imp = q1(pgq, "SELECT after FROM audit_events WHERE action = 'qc.recording.import' AND entity_id = %s", rid)[0]
    assert set(imp) == {"source", "format", "size_bytes", "sha256", "duration_seconds", "case_id"}


def test_wav_builder_is_unique():
    assert wav_bytes() != wav_bytes()
