"""Phase 6 Q2: transcripts. Manual transcripts, revisioned corrections, anonymous speaker labels until
a human verifies them, stale-edit rejection, and provider transcription through the gateway with
the fake transport (request shape, only the `audio` category, non-diarizing model, honest refusals,
the processing lock, interrupted work never replayed)."""
from __future__ import annotations

from test_quality_support import *  # noqa: F401,F403
from test_quality_support import (Q, TRANSCRIPT, configure, insert_policy, q1, qall, ready_recording, run,
                                  transcription_ok, wav_bytes)


def _fields(req):
    ctype = req.headers["Content-Type"]
    boundary = ctype.split("boundary=")[1].encode()
    out = {}
    for part in req.body.split(b"--" + boundary)[1:-1]:
        head, _, value = part[2:-2].partition(b"\r\n\r\n")
        name = head.split(b'name="')[1].split(b'"')[0].decode()
        filename = head.split(b'filename="')[1].split(b'"')[0].decode() if b"filename=" in head else None
        out[name] = (filename, value)
    return out


def test_manual_transcript_corrections_and_speakers(qc, pgq):
    rec = qc.new_recording(data=wav_bytes(seconds=10.0))
    d = qc.transcript(rec["id"], text="السلام عليكم\n\nمعك سارة من شركة المثال", language="ar")
    t1 = d["transcript"]
    assert t1["version"] == 1 and t1["source"] == "manual" and t1["diarized"] is False
    assert [s["id"] for s in t1["segments"]] == ["s1", "s2"] and t1["segments"][0]["start"] is None
    assert d["status"] == "transcribed" and d["revision"] == 2
    # a correction with diarized segments: a new version; speakers anonymous until verified
    d = qc.transcript(rec["id"], segments=TRANSCRIPT, reason="typed from the audio")
    t2 = d["transcript"]
    assert t2["version"] == 2 and t2["source"] == "correction" and t2["based_on_id"] == t1["id"]
    assert t2["speaker_labels"] == ["A", "B"] and t2["speakers"] == {} and t2["diarized"] is True
    assert t2["segments"][2] == {"id": "s3", "start": 3.5, "end": 6.0, "speaker": "A", "text": TRANSCRIPT[2]["text"]}
    # verify one speaker: a new version, same text, the role recorded with who verified it
    d = qc.transcript(rec["id"], speakers={"A": "agent"})
    t3 = d["transcript"]
    assert t3["version"] == 3 and t3["text_sha256"] == t2["text_sha256"]
    assert t3["speakers"]["A"]["role"] == "agent" and t3["speakers"]["A"]["verified_by"]["id"] == Q.rev_a
    assert "B" not in t3["speakers"]
    # a later text correction keeps the verified roles of labels that still exist
    segs = [dict(s) for s in TRANSCRIPT]
    segs[1]["text"] = "وعليكم السلام ورحمة الله."
    t4 = qc.transcript(rec["id"], segments=segs)["transcript"]
    assert t4["version"] == 4 and t4["speakers"]["A"]["role"] == "agent"
    versions = qc.get(f"/v2/quality/recordings/{rec['id']}/transcripts", Q.rev_a).get_json()["data"]
    assert [v["version"] for v in versions] == [4, 3, 2, 1]
    # validation
    assert qc.transcript(rec["id"], expect=422, speakers={"C": "agent"})["error"] == {"speakers": "invalid"}
    assert qc.transcript(rec["id"], expect=422, speakers={"A": "boss"})["code"] == "validation_failed"
    late = [dict(TRANSCRIPT[0], start=0.0, end=99.0)]
    assert qc.transcript(rec["id"], expect=422, segments=late)["error"] == {"segments": "invalid"}
    assert qc.transcript(rec["id"], expect=422, text="   ")["error"] == {"text": "required"}
    # versions are append-only in the database
    import psycopg2
    try:
        run(pgq, "UPDATE qc_transcripts SET text = 'x' WHERE id = %s", t1["id"])
        raise AssertionError("update allowed")
    except psycopg2.Error as e:
        assert "append-only" in str(e)
    ev = qall(pgq, "SELECT after FROM audit_events WHERE action = 'qc.transcript.save' AND entity_id = %s", rec["id"])
    assert len(ev) == 4 and all("text" not in e[0] for e in ev)


def test_stale_edits_are_rejected(qc):
    rec = qc.new_recording()
    first = qc.patch(f"/v2/quality/recordings/{rec['id']}", Q.rev_a, {"expected_revision": 1, "notes": "a"})
    assert first.status_code == 200 and first.get_json()["data"]["revision"] == 2
    stale = qc.patch(f"/v2/quality/recordings/{rec['id']}", Q.rev_a, {"expected_revision": 1, "notes": "b"})
    assert stale.status_code == 409 and stale.get_json()["code"] == "revision_conflict"
    assert stale.get_json()["error"]["current_revision"] == 2
    stale_t = qc.put(f"/v2/quality/recordings/{rec['id']}/transcript", Q.rev_a, {"expected_revision": 1, "text": "x"})
    assert stale_t.status_code == 409
    missing = qc.patch(f"/v2/quality/recordings/{rec['id']}", Q.rev_a, {"notes": "c"})
    assert missing.status_code == 422 and missing.get_json()["error"] == {"expected_revision": "required"}
    bad_tpl = qc.patch(f"/v2/quality/recordings/{rec['id']}", Q.rev_a,
                       {"expected_revision": 2, "template": {"key": "zain-v1", "version": 1}})
    assert bad_tpl.status_code == 404 and bad_tpl.get_json()["code"] == "template_not_found"


def test_transcription_not_configured_is_refused_honestly(qc, pgq, fake):
    rec = qc.new_recording()
    r = qc.post(f"/v2/quality/recordings/{rec['id']}/transcribe", Q.rev_a, {"expected_revision": 1})
    assert r.status_code == 503 and r.get_json()["code"] == "ai_not_configured"
    assert fake.requests == []
    d = qc.detail(rec["id"])
    assert d["transcript"] is None and d["processing"] is None and d["revision"] == 1


def test_transcription_through_gateway(qc, pgq, fake, monkeypatch):
    configure(monkeypatch)
    insert_policy(pgq, allowed_categories=["audio"])
    data = wav_bytes(seconds=5.0)
    rec = qc.new_recording(data=data, case_id=Q.KA1)
    segs = [{"id": "seg_0", "type": "transcript.text.segment", "start": 0.0, "end": 2.0, "speaker": "A",
             "text": "السلام عليكم"},
            {"id": "seg_1", "type": "transcript.text.segment", "start": 2.0, "end": 4.5, "speaker": "B",
             "text": "وعليكم السلام"}]
    fake.script.append(transcription_ok(text="السلام عليكم وعليكم السلام", segments=segs, seconds=5.0))
    r = qc.post(f"/v2/quality/recordings/{rec['id']}/transcribe", Q.rev_a, {"expected_revision": 1, "language": "ar"})
    assert r.status_code == 200, r.get_json()
    d = r.get_json()["data"]
    t = d["transcript"]
    assert t["source"] == "provider" and t["model"] == "gpt-test-transcribe-diarize" and t["diarized"] is True
    assert [(s["id"], s["speaker"], s["start"], s["end"]) for s in t["segments"]] == [
        ("s1", "A", 0.0, 2.0), ("s2", "B", 2.0, 4.5)]
    assert t["speakers"] == {} and t["speaker_labels"] == ["A", "B"]            # anonymous until verified
    assert d["processing"] is None and d["revision"] == 2 and d["status"] == "transcribed"
    # the request: the stored audio, a generic filename, diarized_json, the language
    req = fake.requests[0]
    assert req.url.endswith("/audio/transcriptions")
    f = _fields(req)
    assert f["file"] == ("audio.wav", data)
    assert f["response_format"][1] == b"diarized_json" and f["chunking_strategy"][1] == b"auto"
    assert f["language"][1] == b"ar" and f["model"][1] == b"gpt-test-transcribe-diarize"
    # the audit row: only the audio category, the QC entity and case, no content
    row = q1(pgq, "SELECT purpose, capability, data_categories, outcome, entity_type, entity_id, case_id::text "
                  "FROM ai_provider_calls WHERE id = %s", t["provider_call_id"])
    assert row == ("qc_transcription", "transcribe", ["audio"], "succeeded", "qc_recording", rec["id"], Q.KA1)
    ev = q1(pgq, "SELECT after FROM audit_events WHERE action = 'qc.transcribe' AND entity_id = %s", rec["id"])[0]
    assert ev["call_id"] == t["provider_call_id"] and "text" not in ev


def test_transcription_policy_without_audio_and_non_diarizing_model(qc, pgq, fake, monkeypatch):
    configure(monkeypatch, transcribe="gpt-test-transcribe")
    insert_policy(pgq, allowed_categories=["transcripts"])
    rec = qc.new_recording()
    r = qc.post(f"/v2/quality/recordings/{rec['id']}/transcribe", Q.rev_a, {"expected_revision": 1})
    assert r.status_code == 409 and r.get_json()["code"] == "ai_policy_category_denied"
    assert r.get_json()["error"]["denied_categories"] == ["audio"] and fake.requests == []
    insert_policy(pgq, allowed_categories=["audio"])
    fake.script.append(transcription_ok(text="line one", segments=[]))
    r = qc.post(f"/v2/quality/recordings/{rec['id']}/transcribe", Q.rev_a, {"expected_revision": 1})
    assert r.status_code == 200, r.get_json()
    t = r.get_json()["data"]["transcript"]
    assert t["diarized"] is False and t["segments"] == [
        {"id": "s1", "start": None, "end": None, "speaker": None, "text": "line one"}]
    assert _fields(fake.requests[0])["response_format"][1] == b"json"


def test_provider_failure_clears_processing_and_bad_timing_is_refused(qc, pgq, fake, monkeypatch):
    from test_ai_helpers import http
    configure(monkeypatch, AI_MAX_RETRIES=0)
    insert_policy(pgq, allowed_categories=["audio"])
    rec = qc.new_recording(data=wav_bytes(seconds=2.0))
    fake.script.append(http(503, {"error": {"type": "server_error"}}))
    r = qc.post(f"/v2/quality/recordings/{rec['id']}/transcribe", Q.rev_a, {"expected_revision": 1})
    assert r.status_code == 502 and r.get_json()["code"] == "ai_provider_unavailable"
    d = qc.detail(rec["id"])
    assert d["processing"] is None and d["last_error"] == "ai_provider_unavailable" and d["transcript"] is None
    segs = [{"start": 0.0, "end": 60.0, "speaker": "A", "text": "too long"}]
    fake.script.append(transcription_ok(text="too long", segments=segs))
    r = qc.post(f"/v2/quality/recordings/{rec['id']}/transcribe", Q.rev_a, {"expected_revision": d["revision"]})
    assert r.status_code == 502 and r.get_json()["code"] == "transcript_timing_invalid"
    assert qc.detail(rec["id"])["transcript"] is None


def test_processing_lock_and_interrupted_work_is_not_replayed(qc, pgq, fake, monkeypatch):
    configure(monkeypatch)
    rec = ready_recording(qc)
    rid, rev = rec["id"], rec["revision"]
    # a provider call in flight (the mark a restart would leave behind)
    run(pgq, "UPDATE qc_recordings SET processing = 'transcribe', processing_token = gen_random_uuid(), "
             "processing_started_at = now(), processing_by = %s WHERE id = %s", Q.rev_a, rid)
    r = qc.patch(f"/v2/quality/recordings/{rid}", Q.rev_a, {"expected_revision": rev, "notes": "x"})
    assert r.status_code == 409 and r.get_json()["code"] == "recording_processing"
    assert qc.detail(rid)["processing"]["kind"] == "transcribe" and qc.detail(rid)["processing"]["stale"] is False
    # after AI_CALL_STALE_SECONDS the mark is cleared as interrupted by the next write; nothing is replayed
    run(pgq, "UPDATE qc_recordings SET processing_started_at = now() - interval '2 hours' WHERE id = %s", rid)
    assert qc.detail(rid)["processing"]["stale"] is True
    r = qc.patch(f"/v2/quality/recordings/{rid}", Q.rev_a, {"expected_revision": rev, "notes": "x"})
    assert r.status_code == 200, r.get_json()
    d = r.get_json()["data"]
    assert d["processing"] is None and d["last_error"] == "interrupted" and fake.requests == []
    assert q1(pgq, "SELECT count(*) FROM audit_events WHERE action = 'qc.processing.interrupted' AND entity_id = %s",
              rid)[0] == 1
