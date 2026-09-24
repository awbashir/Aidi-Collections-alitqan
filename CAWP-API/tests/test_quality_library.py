"""Phase 6 Q2: the QC recording library. Import inspection (WAV/MP3), size and duration limits,
sha256 duplicates, original filenames never stored or returned, private files, byte-range
playback (HEAD, Range, no-store), scope (case-linked recordings follow case scope), and
"add call recording to QC" from a Phase 5 attempt (scoped; refused out of scope)."""
from __future__ import annotations

import io
import os
import stat
import struct
import wave

import pytest

from test_quality_support import *  # noqa: F401,F403
from test_quality_support import Q, mp3_bytes, q1, qall, wav_bytes
from test_calling_cdr_support import c4_db, cdr, cdr_reachable, rel_of  # noqa: F401  (Phase 5 P4 fixtures)


# ------------------------------------------------------------------------------ inspection (units)
def test_wav_inspection_accepts_pcm_and_measures_duration():
    from services.ai import quality_audio as qa
    info = qa.inspect(wav_bytes(seconds=2.5, rate=8000, channels=1, sampwidth=2))
    assert (info.format, info.content_type, info.duration_seconds, info.channels, info.sample_rate,
            info.bits_per_sample) == ("wav", "audio/wav", 2.5, 1, 8000, 16)
    st = qa.inspect(wav_bytes(seconds=0.5, rate=44100, channels=2, sampwidth=1))
    assert (st.channels, st.sample_rate, st.bits_per_sample, st.duration_seconds) == (2, 44100, 8, 0.5)
    hi = qa.inspect(wav_bytes(seconds=0.1, rate=96000, channels=1, sampwidth=3))
    assert hi.sample_rate == 96000 and hi.bits_per_sample == 24


def _wav_with(fmt_tag=1, rate=8000, channels=1, bits=16, data=b"\x00\x00" * 400, extra=b""):
    align = channels * bits // 8
    fmt = struct.pack("<HHIIHH", fmt_tag, channels, rate, rate * align, align, bits)
    body = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", len(data)) + data + extra
    return b"RIFF" + struct.pack("<I", len(body)) + body


def test_wav_inspection_refusals():
    from core.api_response import ApiError
    from services.ai import quality_audio as qa

    def code(data):
        with pytest.raises(ApiError) as ei:
            qa.inspect(data)
        return ei.value.status, ei.value.code, (ei.value.error or {}).get("reason")

    good = _wav_with()
    assert qa.inspect(good).duration_seconds == 0.05
    assert code(good[:-10]) == (422, "invalid_audio", "riff_size_mismatch")             # truncated file
    assert code(good + b"junk")[2] == "riff_size_mismatch"                              # trailing data
    assert code(_wav_with(fmt_tag=3, bits=32, data=b"\x00" * 400))[2] == "not_pcm"      # IEEE float
    assert code(_wav_with(rate=7000))[2] == "unsupported_parameters"                    # below 8 kHz
    assert code(_wav_with(rate=192000))[2] == "unsupported_parameters"
    assert code(_wav_with(channels=3, data=b"\x00" * 600))[2] == "unsupported_parameters"
    assert code(_wav_with(data=b"\x00" * 3))[2] == "inconsistent_format"                # partial sample frame
    assert code(b"OggS" + b"\x00" * 100)[:2] == (415, "unsupported_audio")
    assert code(b"")[:2] == (422, "empty_file")
    assert code(b"\x00" * (qa.MAX_BYTES + 1))[:2] == (413, "audio_too_large")
    # longer than 30 minutes (8 kHz, 8-bit mono: 1801 s = 14.4 MB, under the size limit)
    long_ = _wav_with(rate=8000, bits=8, data=b"\x80" * (8000 * 1801))
    assert code(long_)[:2] == (422, "audio_too_long")


def test_mp3_inspection():
    from core.api_response import ApiError
    from services.ai import quality_audio as qa
    info = qa.inspect(mp3_bytes(frames=40))
    assert (info.format, info.content_type, info.channels, info.sample_rate) == ("mp3", "audio/mpeg", 1, 44100)
    assert info.duration_seconds == round(40 * 1152 / 44100, 3) and info.bitrate_kbps == 128
    assert qa.inspect(mp3_bytes(frames=10, id3=False, mono=False)).channels == 2
    tagged = mp3_bytes(frames=5) + b"TAG" + b"\x00" * 125                         # ID3v1 trailer allowed
    assert qa.inspect(tagged).duration_seconds == round(5 * 1152 / 44100, 3)
    with pytest.raises(ApiError) as ei:
        qa.inspect(mp3_bytes(frames=5) + b"\x00" * 600)                          # garbage after the frames
    assert ei.value.code == "invalid_audio"
    with pytest.raises(ApiError):
        qa.inspect(b"ID3\x04\x00\x00\x00\x00\x00\x00" + b"not an mpeg frame" * 10)


def test_byte_range_parsing():
    from services.ai.quality_audio import parse_range
    assert parse_range(None, 1000) is None
    assert parse_range("bytes=0-99", 1000) == (0, 99)
    assert parse_range("bytes=900-", 1000) == (900, 999)
    assert parse_range("bytes=-100", 1000) == (900, 999)
    assert parse_range("bytes=950-5000", 1000) == (950, 999)
    assert parse_range("bytes=1000-", 1000) == "unsatisfiable"
    assert parse_range("bytes=-0", 1000) == "unsatisfiable"
    assert parse_range("bytes=0-1,5-6", 1000) is None                                # multi-range: whole file
    assert parse_range("items=0-1", 1000) is None


# ------------------------------------------------------------------------------ import API
def test_import_private_file_no_filename_and_audit(qc, pgq, storage):
    data = wav_bytes(seconds=1.5)
    secret_name = f"out-{Q.KA1_PHONE}-7301-20260922-100000-1790000000.1.wav"
    r = qc.upload(Q.rev_a, data, filename=secret_name, title="Sample call")
    assert r.status_code == 201, r.get_json()
    body = r.get_json()["data"]
    rec = body["recording"]
    assert body["duplicate"] is False and rec["status"] == "new" and rec["revision"] == 1
    assert rec["audio"]["duration_seconds"] == 1.5 and rec["audio"]["sha256"] == __import__("hashlib").sha256(
        data).hexdigest()
    assert rec["source"] == "import" and rec["provenance"]["kind"] == "import"
    raw = r.get_data(as_text=True)
    assert Q.KA1_PHONE not in raw and "out-0551" not in raw and secret_name not in raw
    assert r.headers["Cache-Control"] == "no-store"
    # the file: private, 0600, under the QC root; nothing else stored about the original name
    path = q1(pgq, "SELECT stored_path FROM qc_recordings WHERE id = %s", rec["id"])[0]
    assert not path.startswith("/") and path.startswith("audio/") and path.endswith(f"{rec['id']}.wav")
    f = storage / path
    assert f.read_bytes() == data
    assert stat.S_IMODE(os.stat(f).st_mode) == 0o600 and stat.S_IMODE(os.stat(storage).st_mode) == 0o700
    cols = {c[0] for c in qall(pgq, "SELECT column_name FROM information_schema.columns WHERE table_name = 'qc_recordings'")}
    assert not any("name" in c and c != "employee_label" for c in cols) and "original_name" not in cols
    ev = qall(pgq, "SELECT action, after::text FROM audit_events WHERE entity_type = 'qc_recording' AND entity_id = %s",
              rec["id"])
    assert [e[0] for e in ev] == ["qc.recording.import"] and Q.KA1_PHONE not in ev[0][1]
    assert not list((storage / "tmp").glob("*")) if (storage / "tmp").exists() else True


def test_import_mp3_and_refusals(qc, pgq):
    rec = qc.new_recording(data=mp3_bytes(frames=30), title="An MP3")
    assert rec["audio"]["format"] == "mp3" and rec["audio"]["content_type"] == "audio/mpeg"
    before = q1(pgq, "SELECT count(*) FROM qc_recordings")[0]
    bad = qc.upload(Q.rev_a, b"RIFF\x00\x00\x00\x00WAVEnot-really")
    assert bad.status_code == 422 and bad.get_json()["code"] == "invalid_audio"
    assert qc.upload(Q.rev_a, b"%PDF-1.7 not audio").get_json()["code"] == "unsupported_audio"
    big = qc.upload(Q.rev_a, b"RIFF" + b"\x00" * 24_000_000)
    assert big.status_code == 413 and big.get_json()["code"] == "audio_too_large"
    missing = qc.c.post("/v2/quality/recordings", data={}, content_type="multipart/form-data",
                        headers=qc.headers(Q.rev_a))
    assert missing.status_code == 422
    assert q1(pgq, "SELECT count(*) FROM qc_recordings")[0] == before


def test_duplicates_and_idempotent_replay(qc, pgq):
    data = wav_bytes()
    key = "qc-import-" + os.urandom(6).hex()
    first = qc.upload(Q.rev_a, data, idem=key)
    again = qc.upload(Q.rev_a, data, idem=key)                              # same key: replayed
    assert first.status_code == again.status_code == 201 and again.headers.get("Idempotent-Replayed") == "true"
    rec_id = first.get_json()["data"]["recording"]["id"]
    dup = qc.upload(Q.rev_a, data, title="another title")                   # new key, same audio
    assert dup.status_code == 200 and dup.get_json()["data"]["duplicate"] is True
    assert dup.get_json()["data"]["recording"]["id"] == rec_id
    assert q1(pgq, "SELECT count(*) FROM qc_recordings WHERE sha256 = %s",
              first.get_json()["data"]["recording"]["audio"]["sha256"])[0] == 1
    # a duplicate the caller cannot see: 409 without the id
    linked = wav_bytes()
    qc.new_recording(user=Q.rev_a, data=linked, case_id=Q.KA1)
    hidden = qc.upload(Q.rev_b, linked)
    assert hidden.status_code == 409 and hidden.get_json()["code"] == "duplicate_recording"
    assert hidden.get_json()["error"] == {"visible": False}


def test_scope_and_permissions(qc, pgq):
    linked = qc.new_recording(user=Q.rev_a, case_id=Q.KA1, title="KA1 call")
    free = qc.new_recording(user=Q.rev_a, title="unlinked")
    assert linked["link"]["case_id"] == Q.KA1
    # rev_b (collector of contract B) cannot see a contract-A case recording, but sees unlinked ones
    assert qc.get(f"/v2/quality/recordings/{linked['id']}", Q.rev_b).status_code == 404
    assert qc.get(f"/v2/quality/recordings/{linked['id']}/audio", Q.rev_b).status_code == 404
    assert qc.detail(free["id"], Q.rev_b)["id"] == free["id"]
    ids_b = {x["id"] for x in qc.get("/v2/quality/recordings?page_size=200", Q.rev_b).get_json()["data"]["items"]}
    assert free["id"] in ids_b and linked["id"] not in ids_b
    ids_a = {x["id"] for x in qc.get(f"/v2/quality/recordings?case_id={Q.KA1}", Q.rev_a).get_json()["data"]["items"]}
    assert linked["id"] in ids_a and free["id"] not in ids_a
    # linking to a case outside the caller's scope is refused (404, reveals nothing)
    r = qc.upload(Q.rev_b, wav_bytes(), case_id=Q.KA1)
    assert r.status_code == 404
    # keys
    assert qc.get("/v2/quality/recordings", Q.none).status_code == 403
    assert qc.upload(Q.none, wav_bytes()).status_code == 403
    ov = qc.get("/v2/quality", Q.rev_a).get_json()["data"]
    assert [t["key"] for t in ov["templates"]] == ["collections-v1"]            # no sales ("Zain") template
    assert ov["ai"]["transcribe"]["ready"] is False and "ai_not_configured" in ov["ai"]["transcribe"]["reasons"]
    assert ov["limits"]["max_bytes"] == 24_000_000 and ov["limits"]["max_seconds"] == 1800
    tpl = qc.get("/v2/quality/templates/collections-v1/1", Q.rev_a).get_json()["data"]
    crit = {c["id"]: c for c in tpl["criteria"]}
    assert tpl["name_ar"] == "التحصيل · جودة الحوار" and tpl["name_en"].startswith("Collections")
    assert crit["delivery"]["human_listening_only"] is True and crit["intro"]["human_listening_only"] is False
    assert crit["affordability"]["conditional"] is True and crit["alternatives"]["applies_when_ar"]
    assert crit["notes"]["requires_input"] == "notes" and crit["action"]["requires_input"] == "recorded_action"
    assert all(c["guidance_ar"] and c["guidance_en"] and c["ar"] and c["en"] for c in tpl["criteria"])
    assert qc.get("/v2/quality/templates/zain-v1/1", Q.rev_a).status_code == 404
    assert len(qc.get("/v2/quality/templates", Q.rev_a).get_json()["data"]) == 1
    new_ids = {x["id"] for x in qc.get("/v2/quality/recordings?status=new&source=import&page_size=200",
                                       Q.rev_a).get_json()["data"]["items"]}
    assert free["id"] in new_ids
    assert qc.get("/v2/quality/recordings?status=approved", Q.rev_a).status_code == 200
    assert qc.get("/v2/quality/recordings?status=weird", Q.rev_a).status_code == 422
    # template content is immutable in the database
    import psycopg2
    with pytest.raises(psycopg2.Error):
        from test_quality_support import run
        run(pgq, "UPDATE qc_templates SET criteria = '[]'::jsonb WHERE template_key = 'collections-v1'")


def test_byte_range_playback(qc, pgq):
    data = wav_bytes(seconds=0.5)
    rec = qc.new_recording(data=data)
    url = f"/v2/quality/recordings/{rec['id']}/audio"
    full = qc.get(url, Q.rev_a)
    assert full.status_code == 200 and full.data == data
    assert full.headers["Content-Type"] == "audio/wav" and full.headers["Accept-Ranges"] == "bytes"
    assert full.headers["Cache-Control"] == "no-store" and full.headers["Content-Length"] == str(len(data))
    assert "inline" in full.headers["Content-Disposition"] and rec["id"] in full.headers["Content-Disposition"]
    part = qc.get(url, Q.rev_a, Range="bytes=10-109")
    assert part.status_code == 206 and part.data == data[10:110]
    assert part.headers["Content-Range"] == f"bytes 10-109/{len(data)}" and part.headers["Content-Length"] == "100"
    tail = qc.get(url, Q.rev_a, Range="bytes=-44")
    assert tail.status_code == 206 and tail.data == data[-44:]
    rest = qc.get(url, Q.rev_a, Range=f"bytes={len(data) - 5}-")
    assert rest.data == data[-5:]
    bad = qc.get(url, Q.rev_a, Range=f"bytes={len(data)}-")
    assert bad.status_code == 416 and bad.headers["Content-Range"] == f"bytes */{len(data)}"
    head = qc.head(url, Q.rev_a, Range="bytes=0-9")
    assert head.status_code == 206 and head.data == b"" and head.headers["Content-Length"] == "10"
    reads = qall(pgq, "SELECT after FROM audit_events WHERE action = 'qc.audio.read' AND entity_id = %s "
                      "ORDER BY occurred_at, id", rec["id"])
    assert [(a[0]["range_start"], a[0]["bytes"], a[0]["status"]) for a in reads] == [
        (0, len(data), 200), (10, 100, 206), (len(data) - 44, 44, 206), (len(data) - 5, 5, 206)]


# ------------------------------------------------------------------------------ add call recording to QC
@pytest.fixture
def p4(c4_db, qc, pgq):
    """The Phase 5 P4 fixtures (cases, attempts, CDR), with quality_review granted to its collector role
    for the test only."""
    from test_calling_cdr_support import P4, set_perm
    set_perm(pgq, P4.role_col, "quality_review", True)
    try:
        yield P4
    finally:
        set_perm(pgq, P4.role_col, "quality_review", False)


def test_add_from_call_refusals_without_cdr(qc, p4, pgq):
    P4 = p4
    url = "/v2/quality/recordings/from-call"
    # an explicit user action is required
    r = qc.post(url, P4.cola, {"attempt_id": P4.AT1})
    assert r.status_code == 422 and r.get_json()["code"] == "confirmation_required"
    # another team's call: 404 call_not_found (nothing revealed)
    r = qc.post(url, P4.colb, {"attempt_id": P4.AT1, "confirmed": True})
    assert r.status_code in (403, 404)
    # cola made the call on colb's case K6: the QC copy follows case scope, so it is refused
    r = qc.post(url, P4.cola, {"attempt_id": P4.AT4, "confirmed": True})
    assert r.status_code == 404 and r.get_json()["code"] == "call_not_found"
    # CDR disabled (tests default): no recording is known, nothing copied
    r = qc.post(url, P4.cola, {"attempt_id": P4.AT1, "confirmed": True})
    assert r.status_code == 404 and r.get_json()["code"] == "recording_not_found"
    # quality reviewers without a recording key cannot copy recordings
    assert qc.post(url, Q.rev_a, {"attempt_id": P4.AT1, "confirmed": True}).status_code == 403
    assert q1(pgq, "SELECT count(*) FROM qc_recordings WHERE attempt_id = %s", P4.AT1)[0] == 0


def test_add_from_call_copies_scoped_recording(qc, p4, pgq, cdr, tmp_path, monkeypatch, storage):
    P4 = p4
    monitor = tmp_path / "monitor"
    wav = wav_bytes(seconds=2.0)
    target = monitor / rel_of(P4.REC_AT1)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(wav)
    monkeypatch.setenv("RECORDINGS_READER", "local")
    monkeypatch.setenv("RECORDINGS_LOCAL_DIR", str(monitor))
    url = "/v2/quality/recordings/from-call"
    # a path that is not this call's recording
    r = qc.post(url, P4.cola, {"attempt_id": P4.AT1, "recording_path": P4.REC_AT2, "confirmed": True})
    assert r.status_code == 404 and r.get_json()["code"] == "recording_not_found"
    r = qc.post(url, P4.cola, {"attempt_id": P4.AT1, "confirmed": True, "title": "Call review"})
    assert r.status_code == 201, r.get_json()
    rec = r.get_json()["data"]["recording"]
    assert rec["source"] == "pbx_call" and rec["link"] == {"case_id": P4.K1, "attempt_id": P4.AT1}
    prov = rec["provenance"]
    assert prov["kind"] == "pbx_call" and prov["attempt_id"] == P4.AT1 and prov["recording_path"] == P4.REC_AT1
    assert prov["explicit_user_action"] is True and prov["sha256"] == rec["audio"]["sha256"]
    assert rec["audio"]["duration_seconds"] == 2.0
    stored = q1(pgq, "SELECT stored_path FROM qc_recordings WHERE id = %s", rec["id"])[0]
    assert (storage / stored).read_bytes() == wav
    # the PBX read went through the scoped, audited Phase 5 path
    served = qall(pgq, "SELECT after FROM audit_events WHERE action = 'cdr.recording.served' AND actor_user_id = %s "
                       "AND entity_id = %s ORDER BY occurred_at DESC LIMIT 1", P4.cola, P4.REC_AT1)
    assert served and served[0][0]["sha256"] == rec["audio"]["sha256"]
    assert q1(pgq, "SELECT count(*) FROM audit_events WHERE action = 'qc.recording.from_call' AND entity_id = %s",
              rec["id"])[0] == 1
    # the copy follows case scope: colb (contract B) cannot see it; the supervisor of contract A can
    from test_calling_cdr_support import set_perm
    set_perm(pgq, P4.role_sup, "quality_review", True)
    try:
        assert qc.get(f"/v2/quality/recordings/{rec['id']}", P4.sup).status_code == 200
    finally:
        set_perm(pgq, P4.role_sup, "quality_review", False)
    assert qc.get(f"/v2/quality/recordings/{rec['id']}", P4.colb).status_code == 404
    # the same recording again: a duplicate, nothing copied twice
    again = qc.post(url, P4.cola, {"attempt_id": P4.AT1, "confirmed": True})
    assert again.status_code == 200 and again.get_json()["data"]["duplicate"] is True
    assert q1(pgq, "SELECT count(*) FROM qc_recordings WHERE attempt_id = %s", P4.AT1)[0] == 1


def test_wave_module_fixture_is_small():
    data = wav_bytes(seconds=1.0)
    assert len(data) < 20_000
    with wave.open(io.BytesIO(data)) as w:
        assert w.getframerate() == 8000
