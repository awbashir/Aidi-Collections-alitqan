"""Phase 6 Q2: the quality workspace (docs/phase6-spec.md stream Q2, review/phase6-quality.md).

Flow of one recording (`qc_recordings`, migrations 0062-0063):

1. **Library.** A private import of a WAV/MP3 (inspected, sha256 duplicate detection, the original
   filename is never stored or returned), or an explicit "add call recording to QC" from a Phase 5
   call attempt whose recording the caller may hear (scoped and audited by
   services/calling_cdr.scoped_recording; copied into private QC storage with provenance).
2. **Evidence.** The reviewer chooses the template and adds the verified employee label, notes,
   recorded action and context. Any evidence change clears the current review and approval.
3. **Transcript.** Provider transcription through the AI gateway (services/ai/quality_ai.py) or a
   manual transcript; corrections are new versions. Speaker labels stay anonymous until a human
   verifies their roles.
4. **Review.** An AI draft (validated server-side, never approved automatically) or a human save;
   every save is a new immutable revision. Human saves must satisfy the evidence rule
   (services/ai/quality_evidence.py).
5. **Approval.** `quality_approve`, `confirmed: true` and the current revision: an immutable
   snapshot in `qc_approvals`. Only a human-saved review can be approved.
6. **Dashboard / report.** Current approvals only; the HTML report states status and provenance.

Scope: a recording linked to a case (imported with a case, or copied from a call) follows the
caller's case scope; an unlinked imported recording is visible to every quality reviewer. Out of
scope answers 404 (an id reveals nothing). Every mutation takes `expected_revision` (stale edits
answer 409 `revision_conflict`) and is audited without content (ids, revisions, counts, hashes).
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import uuid

from sqlalchemy import text

from core import audit
from core.api_response import ApiError
from core.revisions import RevisionConflict
from services.ai import quality_audio as qa
from services.ai import quality_evidence as qe

STALE_DEFAULT = 1800
ROLES = ("agent", "customer", "other")
MAX_TRANSCRIPT_CHARS = 150_000
MAX_SEGMENTS = 5000
ENTITY = "qc_recording"


def _s():
    from extensions import db
    return db.session


def me() -> str:
    from services.case_history_common import me as _me
    return _me()


def _iso(v):
    from services.case_history_common import iso
    return iso(v)


def _json(v) -> str:
    return json.dumps(v, ensure_ascii=False, sort_keys=True, default=str)


def canonical_sha256(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                                     default=str).encode("utf-8")).hexdigest()


def _v(field, message, code="validation_failed", status=422, detail="invalid"):
    return ApiError(status, code, message, {field: detail})


# ------------------------------------------------------------------------------ scope
def _visible_sql(alias: str = "r") -> tuple[str, dict]:
    from services.case_history_common import scope_sql
    cond, params = scope_sql("c")
    return (f"({alias}.case_id IS NULL OR EXISTS (SELECT 1 FROM clints c WHERE c.id = {alias}.case_id AND {cond}))",
            params)


def _not_found(rec_id):
    return ApiError(404, "recording_not_found", "quality recording not found", {"recording_id": str(rec_id)})


_REC_COLS = "r.*"


def _load(rec_id, *, lock: bool = False) -> dict:
    cond, params = _visible_sql("r")
    sql = f"SELECT {_REC_COLS} FROM qc_recordings r WHERE r.id = CAST(:id AS uuid) AND {cond}"
    if lock:
        sql += " FOR UPDATE OF r"
    row = _s().execute(text(sql), {**params, "id": str(rec_id)}).mappings().first()
    if row is None:
        raise _not_found(rec_id)
    return dict(row)


def _stale_seconds() -> int:
    try:
        from services.ai import config as config_mod
        return int(config_mod.load().call_stale_seconds)
    except Exception:  # noqa: BLE001
        return STALE_DEFAULT


def _processing_stale(rec: dict, now: _dt.datetime) -> bool:
    started = rec.get("processing_started_at")
    return bool(rec.get("processing")) and started is not None and \
        (now - started).total_seconds() > _stale_seconds()


def _now() -> _dt.datetime:
    return _s().execute(text("SELECT now()")).scalar()


def lock_for_write(rec_id, expected) -> dict:
    """Lock the visible recording, clear a stale processing lock (never replaying the work),
    refuse while processing, and compare the revision."""
    from core.revisions import parse_revision
    try:
        expected = parse_revision(expected)
    except ValueError:
        raise _v("expected_revision", "expected_revision is required (the recording revision you saw)",
                 detail="required") from None
    rec = _load(rec_id, lock=True)
    if rec["processing"]:
        if _processing_stale(rec, _now()):
            _s().execute(text("""
                UPDATE qc_recordings SET processing = NULL, processing_token = NULL, processing_started_at = NULL,
                       processing_by = NULL, last_error = 'interrupted', updated_at = now()
                 WHERE id = CAST(:id AS uuid)"""), {"id": str(rec_id)})
            audit.record("qc.processing.interrupted", ENTITY, rec_id, case_id=rec["case_id"],
                         after={"kind": rec["processing"], "started_at": rec["processing_started_at"]},
                         reason="provider work interrupted (restart or crash); never replayed")
            rec.update(processing=None, processing_token=None, processing_started_at=None, last_error="interrupted")
        else:
            raise ApiError(409, "recording_processing", "this recording is being processed; try again when it is done",
                           {"processing": rec["processing"]})
    if rec["revision"] != expected:
        raise RevisionConflict(ENTITY, rec_id, expected, rec["revision"])
    return rec


def _bump(rec_id, **values) -> int:
    """UPDATE the recording (column names from code only), revision + 1. Returns the new revision."""
    sets = ", ".join(f"{k} = :{k}" for k in values)
    sql = f"UPDATE qc_recordings SET {sets + ', ' if sets else ''}revision = revision + 1, updated_at = now() " \
          f"WHERE id = CAST(:_id AS uuid) RETURNING revision"
    return int(_s().execute(text(sql), {**values, "_id": str(rec_id)}).scalar())


# ------------------------------------------------------------------------------ templates
def _template_row_to_dict(r) -> dict:
    return {"key": r["template_key"], "version": r["version"], "name_en": r["name_en"], "name_ar": r["name_ar"],
            "origin_en": r["origin_en"], "origin_ar": r["origin_ar"], "criteria": list(r["criteria"]),
            "active": r["active"], "created_at": _iso(r["created_at"])}


def list_templates(include_inactive: bool = False) -> list:
    rows = _s().execute(text("SELECT * FROM qc_templates" + ("" if include_inactive else " WHERE active")
                             + " ORDER BY template_key, version")).mappings().all()
    return [_template_row_to_dict(r) for r in rows]


def get_template(key, version) -> dict:
    if not key or version is None:
        raise ApiError(422, "template_required", "choose a quality template first", {"template": "required"})
    r = _s().execute(text("SELECT * FROM qc_templates WHERE template_key = :k AND version = :v"),
                     {"k": key, "v": int(version)}).mappings().first()
    if r is None:
        raise ApiError(404, "template_not_found", "quality template not found", {"template": f"{key}@{version}"})
    return _template_row_to_dict(r)


# ------------------------------------------------------------------------------ serialization
def _users(ids) -> dict:
    ids = sorted({str(i) for i in ids if i})
    if not ids:
        return {}
    rows = _s().execute(text("""
        SELECT id::text, nullif(btrim(coalesce(frist_name, '') || ' ' || coalesce(last_name, '')), '') AS name
          FROM users WHERE id = ANY(CAST(:ids AS uuid[]))"""), {"ids": ids}).all()
    return {r[0]: r[1] for r in rows}


def _ref(uid, names):
    return None if uid is None else {"id": str(uid), "name": names.get(str(uid))}


def _status(rec: dict, review_origin: str | None) -> str:
    if rec["current_approval_id"]:
        return "approved"
    if rec["current_review_id"]:
        return "human_draft" if review_origin == "human" else "ai_draft"
    if rec["current_transcript_id"]:
        return "transcribed"
    return "new"


def _audio_public(rec: dict) -> dict:
    return {"format": rec["audio_format"], "content_type": rec["content_type"], "size_bytes": rec["size_bytes"],
            "sha256": rec["sha256"], "duration_seconds": float(rec["duration_seconds"]),
            "channels": rec["channels"], "sample_rate": rec["sample_rate"], "bits_per_sample": rec["bits_per_sample"],
            "bitrate_kbps": rec["bitrate_kbps"], "available": rec["audio_purged_at"] is None,
            "purged_at": _iso(rec["audio_purged_at"]),
            "url": f"/v2/quality/recordings/{rec['id']}/audio" if rec["audio_purged_at"] is None else None}


def _processing_public(rec: dict) -> dict | None:
    if not rec["processing"]:
        return None
    return {"kind": rec["processing"], "started_at": _iso(rec["processing_started_at"]),
            "stale": _processing_stale(rec, _dt.datetime.now(_dt.timezone.utc))}


def transcript_public(t: dict | None, names: dict) -> dict | None:
    if t is None:
        return None
    return {"id": str(t["id"]), "version": t["version"], "source": t["source"],
            "based_on_id": str(t["based_on_id"]) if t["based_on_id"] else None, "language": t["language"],
            "diarized": t["diarized"], "model": t["model"],
            "provider_call_id": str(t["provider_call_id"]) if t["provider_call_id"] else None,
            "text": t["text"], "segments": t["segments"], "segment_count": t["segment_count"],
            "speakers": {k: {"role": v.get("role"), "verified": True, "verified_by": _ref(v.get("verified_by"), names),
                             "verified_at": v.get("verified_at")} for k, v in (t["speakers"] or {}).items()},
            "speaker_labels": _speaker_labels(t["segments"]),
            "text_sha256": t["text_sha256"], "reason": t["reason"],
            "purged": t["purged_at"] is not None, "purged_at": _iso(t["purged_at"]),
            "created_by": _ref(t["created_by"], names), "created_at": _iso(t["created_at"])}


def _speaker_labels(segments) -> list:
    return sorted({s.get("speaker") for s in segments or [] if s.get("speaker")})


def review_public(rv: dict | None, names: dict) -> dict | None:
    if rv is None:
        return None
    c = {k: rv[k] for k in qe.RESULTS}
    c["total"] = rv["total"]
    return {"id": str(rv["id"]), "version": rv["version"], "origin": rv["origin"],
            "status": "ai_draft" if rv["origin"] == "ai" else "human_draft",
            "based_on_id": str(rv["based_on_id"]) if rv["based_on_id"] else None,
            "recording_revision": rv["recording_revision"],
            "template": {"key": rv["template_key"], "version": rv["template_version"]},
            "transcript_id": str(rv["transcript_id"]) if rv["transcript_id"] else None,
            "results": rv["results"], "summary": rv["summary"], "coaching": rv["coaching"], "language": rv["language"],
            "metrics": qe.metrics_from_counts(c), "model": rv["model"],
            "provider_call_id": str(rv["provider_call_id"]) if rv["provider_call_id"] else None,
            "validation": rv["validation"], "created_by": _ref(rv["created_by"], names),
            "created_at": _iso(rv["created_at"])}


def approval_public(a: dict | None, names: dict, *, current_id=None, with_snapshot=False) -> dict | None:
    if a is None:
        return None
    c = {k: a[k] for k in qe.RESULTS}
    c["total"] = a["total"]
    out = {"id": str(a["id"]), "recording_id": str(a["recording_id"]), "review_id": str(a["review_id"]),
           "approved_revision": a["approved_revision"], "approved_by": _ref(a["approved_by"], names),
           "approved_at": _iso(a["approved_at"]), "template": {"key": a["template_key"], "version": a["template_version"]},
           "metrics": qe.metrics_from_counts(c), "snapshot_sha256": a["snapshot_sha256"],
           "transcript_purged": a["transcript_purged_at"] is not None,
           "current": current_id is not None and str(current_id) == str(a["id"])}
    if with_snapshot:
        out["snapshot"] = a["snapshot"]
    return out


def _one(table, row_id):
    if not row_id:
        return None
    r = _s().execute(text(f"SELECT * FROM {table} WHERE id = CAST(:id AS uuid)"), {"id": str(row_id)}).mappings().first()
    return dict(r) if r else None


def _counts_for(rec_id) -> dict:
    r = _s().execute(text("""
        SELECT (SELECT count(*) FROM qc_transcripts WHERE recording_id = CAST(:id AS uuid)),
               (SELECT count(*) FROM qc_reviews WHERE recording_id = CAST(:id AS uuid)),
               (SELECT count(*) FROM qc_approvals WHERE recording_id = CAST(:id AS uuid))"""),
        {"id": str(rec_id)}).one()
    return {"transcripts": int(r[0]), "reviews": int(r[1]), "approvals": int(r[2])}


def recording_public(rec: dict) -> dict:
    t = _one("qc_transcripts", rec["current_transcript_id"])
    rv = _one("qc_reviews", rec["current_review_id"])
    ap = _one("qc_approvals", rec["current_approval_id"])
    names = _users([rec["created_by"], t and t["created_by"], rv and rv["created_by"], ap and ap["approved_by"],
                    *[v.get("verified_by") for v in ((t or {}).get("speakers") or {}).values()]])
    return {
        "id": str(rec["id"]), "revision": rec["revision"], "title": rec["title"], "source": rec["source"],
        "status": _status(rec, rv["origin"] if rv else None),
        "audio": _audio_public(rec),
        "link": {"case_id": str(rec["case_id"]) if rec["case_id"] else None,
                 "attempt_id": str(rec["attempt_id"]) if rec["attempt_id"] else None},
        "provenance": rec["provenance"],
        "evidence": {"template": ({"key": rec["template_key"], "version": rec["template_version"]}
                                  if rec["template_key"] else None),
                     "employee_label": rec["employee_label"], "notes": rec["notes"],
                     "recorded_action": rec["recorded_action"], "context": rec["context"]},
        "transcript": transcript_public(t, names),
        "review": review_public(rv, names),
        "approval": approval_public(ap, names, current_id=rec["current_approval_id"]),
        "processing": _processing_public(rec),
        "last_error": rec["last_error"],
        "history": _counts_for(rec["id"]),
        "created_by": _ref(rec["created_by"], names), "created_at": _iso(rec["created_at"]),
        "updated_at": _iso(rec["updated_at"]),
        "note": "Call-level quality findings, not an employee ranking. AI output is a draft until a human approves it.",
    }


def get_recording(rec_id) -> dict:
    return recording_public(_load(rec_id))


# ------------------------------------------------------------------------------ list
_STATUS_SQL = {
    "approved": "r.current_approval_id IS NOT NULL",
    "human_draft": "r.current_approval_id IS NULL AND rv.origin = 'human'",
    "ai_draft": "r.current_approval_id IS NULL AND rv.origin = 'ai'",
    "transcribed": "r.current_review_id IS NULL AND r.current_transcript_id IS NOT NULL",
    "new": "r.current_review_id IS NULL AND r.current_transcript_id IS NULL",
}


def list_recordings(args) -> dict:
    from services.case_history_common import page_args, paged, parse_uuid
    page, size, offset = page_args()
    cond, params = _visible_sql("r")
    where = [cond]
    status = args.get("status")
    if status:
        if status not in _STATUS_SQL:
            raise _v("status", "unknown status", detail=f"one of {sorted(_STATUS_SQL)}")
        where.append(_STATUS_SQL[status])
    if args.get("source"):
        if args["source"] not in ("import", "pbx_call"):
            raise _v("source", "source must be import or pbx_call")
        where.append("r.source = :source")
        params["source"] = args["source"]
    for field in ("case_id", "attempt_id"):
        if args.get(field):
            where.append(f"r.{field} = CAST(:{field} AS uuid)")
            params[field] = parse_uuid(args[field], field)
    if args.get("template"):
        where.append("r.template_key = :tkey")
        params["tkey"] = args["template"]
    w = " AND ".join(where)
    total = _s().execute(text(f"SELECT count(*) FROM qc_recordings r LEFT JOIN qc_reviews rv ON rv.id = r.current_review_id "
                              f"WHERE {w}"), params).scalar()
    rows = _s().execute(text(f"""
        SELECT r.*, rv.origin AS review_origin, rv.met AS rv_met, rv.needs_improvement AS rv_ni,
               rv.not_applicable AS rv_na, rv.insufficient_evidence AS rv_ie, rv.total AS rv_total,
               a.approved_at, t.purged_at AS transcript_purged_at
          FROM qc_recordings r
          LEFT JOIN qc_reviews rv ON rv.id = r.current_review_id
          LEFT JOIN qc_approvals a ON a.id = r.current_approval_id
          LEFT JOIN qc_transcripts t ON t.id = r.current_transcript_id
         WHERE {w}
         ORDER BY r.created_at DESC, r.id DESC LIMIT :lim OFFSET :off"""),
        {**params, "lim": size, "off": offset}).mappings().all()
    items = []
    for r in rows:
        rec = dict(r)
        m = None
        if rec["review_origin"]:
            m = qe.metrics_from_counts({"met": rec["rv_met"], "needs_improvement": rec["rv_ni"],
                                        "not_applicable": rec["rv_na"], "insufficient_evidence": rec["rv_ie"],
                                        "total": rec["rv_total"]})
        items.append({
            "id": str(rec["id"]), "revision": rec["revision"], "title": rec["title"], "source": rec["source"],
            "status": _status(rec, rec["review_origin"]),
            "duration_seconds": float(rec["duration_seconds"]), "format": rec["audio_format"],
            "audio_available": rec["audio_purged_at"] is None,
            "case_id": str(rec["case_id"]) if rec["case_id"] else None,
            "attempt_id": str(rec["attempt_id"]) if rec["attempt_id"] else None,
            "template": {"key": rec["template_key"], "version": rec["template_version"]} if rec["template_key"] else None,
            "has_transcript": rec["current_transcript_id"] is not None and rec["transcript_purged_at"] is None,
            "review_origin": rec["review_origin"], "metrics": m,
            "approved": rec["current_approval_id"] is not None, "approved_at": _iso(rec["approved_at"]),
            "processing": rec["processing"], "created_at": _iso(rec["created_at"]),
        })
    return paged(items, total, page, size)


# ------------------------------------------------------------------------------ import
def _case_for_link(case_id):
    """The case row when it is in the caller's scope, else 404 (never reveals existence)."""
    from services.case_history_common import case_in_scope
    return case_in_scope(case_id)


def _existing_duplicate(sha: str):
    return _s().execute(text("SELECT id FROM qc_recordings WHERE sha256 = :h AND audio_purged_at IS NULL"),
                        {"h": sha}).scalar()


def _duplicate_answer(dup_id) -> dict:
    """A duplicate the caller may see: its id; otherwise a 409 that reveals nothing more."""
    cond, params = _visible_sql("r")
    visible = _s().execute(text(f"SELECT 1 FROM qc_recordings r WHERE r.id = :id AND {cond}"),
                           {**params, "id": str(dup_id)}).first()
    if visible is None:
        raise ApiError(409, "duplicate_recording", "the same audio is already in the QC library", {"visible": False})
    audit.record("qc.recording.duplicate", ENTITY, dup_id, after={"duplicate_of": str(dup_id)})
    return {"duplicate": True, "recording": get_recording(dup_id)}


def _insert_recording(*, data: bytes, info: qa.AudioInfo, sha: str, title: str, source: str, case_id=None,
                      attempt_id=None, provenance: dict) -> tuple[str, str]:
    rec_id = str(uuid.uuid4())
    now = _now()
    relative = qa.audio_relative(rec_id, info.format, now)
    _s().execute(text("""
        INSERT INTO qc_recordings (id, source, title, audio_format, content_type, size_bytes, sha256, duration_seconds,
                                   channels, sample_rate, bits_per_sample, bitrate_kbps, stored_path, case_id,
                                   attempt_id, provenance, created_by, created_at, updated_at)
        VALUES (CAST(:id AS uuid), :source, :title, :fmt, :ctype, :size, :sha, :dur, :ch, :sr, :bits, :kbps, :path,
                CAST(:case AS uuid), CAST(:att AS uuid), CAST(:prov AS jsonb), CAST(:by AS uuid), :now, :now)"""),
        {"id": rec_id, "source": source, "title": title, "fmt": info.format, "ctype": info.content_type,
         "size": info.size_bytes, "sha": sha, "dur": info.duration_seconds, "ch": info.channels,
         "sr": info.sample_rate, "bits": info.bits_per_sample, "kbps": info.bitrate_kbps, "path": relative,
         "case": case_id, "att": attempt_id, "prov": _json(provenance), "by": me(), "now": now})
    _s().flush()
    qa.write_private(relative, data)
    return rec_id, relative


def _commit_or_remove(relative: str):
    try:
        _s().commit()
    except Exception:
        _s().rollback()
        qa.delete(relative)
        raise


def _title(raw, default: str) -> str:
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return default
    if not isinstance(raw, str) or len(raw.strip()) > 200:
        raise _v("title", "title must be text of at most 200 characters")
    return raw.strip()


def import_recording(file_storage, form) -> tuple[dict, bool]:
    """-> (body, created). The original filename is used for nothing and stored nowhere."""
    from sqlalchemy.exc import IntegrityError

    from services.case_history_common import parse_uuid
    if file_storage is None:
        raise ApiError(422, "validation_failed", "a recording is required (multipart field 'file')", {"file": "required"})
    data = qa.receive(file_storage.stream)
    info = qa.inspect(data)
    sha = qa.sha256(data)
    case_id = None
    if form.get("case_id"):
        case_id = str(_case_for_link(parse_uuid(form.get("case_id"), "case_id"))["id"])
    title = _title(form.get("title"), "Recording " + _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d %H:%M"))
    dup = _existing_duplicate(sha)
    if dup:
        return _duplicate_answer(dup), False
    provenance = {"kind": "import", "imported_by": me(), "sha256": sha, "format": info.format,
                  "filename_stored": False}
    try:
        rec_id, relative = _insert_recording(data=data, info=info, sha=sha, title=title, source="import",
                                             case_id=case_id, provenance=provenance)
    except IntegrityError:
        _s().rollback()
        dup = _existing_duplicate(sha)
        if dup:
            return _duplicate_answer(dup), False
        raise
    audit.record("qc.recording.import", ENTITY, rec_id, case_id=case_id,
                 after={"source": "import", "format": info.format, "size_bytes": info.size_bytes, "sha256": sha,
                        "duration_seconds": info.duration_seconds, "case_id": case_id})
    _commit_or_remove(relative)
    return {"duplicate": False, "recording": get_recording(rec_id)}, True


def _attempt_recording_paths(attempt: dict) -> list[str]:
    """The distinct recording paths the attempt's PBX evidence names (live CDR, imported CDR)."""
    from services.calling_cdr import resolve_path
    from services.calling_cdr_attempts import imported, live_cdr
    _status, rows = live_cdr(attempt)
    seen, out = set(), []
    for p in [r.get("recording") for r in rows] + [e.get("recordingfile") for e in imported(attempt["id"])]:
        if not p:
            continue
        key = resolve_path(p) or p
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out


def add_from_call(body: dict) -> tuple[dict, bool]:
    """Copy a call attempt's PBX recording into the QC library. Only on this explicit request
    (`confirmed: true`); the attempt's case must be in the caller's scope and the recording is read
    through the scoped, audited Phase 5 path (services/calling_cdr.scoped_recording)."""
    from services.calling_cdr import RECORDING_KEYS, resolve_path, scoped_recording
    from services.calling_cdr_attempts import visible_attempt
    from services.case_history_common import parse_uuid, require_any, scope_sql
    require_any(*RECORDING_KEYS)
    attempt_id = parse_uuid(body.get("attempt_id"), "attempt_id")
    if body.get("confirmed") is not True:
        raise ApiError(422, "confirmation_required",
                       "adding a call recording to QC copies it into the QC library; confirm with confirmed: true",
                       {"confirmed": "required"})
    attempt = visible_attempt(attempt_id)                      # 404 call_not_found outside the call scope
    cond, params = scope_sql("c")
    in_scope = attempt["case_id"] and _s().execute(
        text(f"SELECT 1 FROM clints c WHERE c.id = CAST(:id AS uuid) AND {cond}"),
        {**params, "id": attempt["case_id"]}).first()
    if not in_scope:                                           # the QC copy follows case scope
        raise ApiError(404, "call_not_found", "call not found", {"attempt_id": attempt_id})
    paths = _attempt_recording_paths(attempt)
    wanted = body.get("recording_path")
    if wanted is not None:
        if not isinstance(wanted, str) or not wanted.strip():
            raise _v("recording_path", "recording_path must be a recording path of this call")
        target = resolve_path(wanted.strip())
        match = [p for p in paths if target is not None and (resolve_path(p) or p) == target]
        if not match:
            raise ApiError(404, "recording_not_found", "this call has no such recording", {"attempt_id": attempt_id})
        path = match[0]
    elif len(paths) == 1:
        path = paths[0]
    elif not paths:
        raise ApiError(404, "recording_not_found", "no recording is known for this call", {"attempt_id": attempt_id})
    else:
        raise ApiError(422, "recording_path_required", "this call has several recordings; choose one",
                       {"recordings": len(paths)})
    _s().commit()                                              # nothing held while the PBX file is read
    data = scoped_recording(me(), path)                        # scoped + audited (cdr.recording.*)
    if not data:
        raise ApiError(404, "recording_not_found", "the recording is not available to you",
                       {"attempt_id": attempt_id})
    if len(data) > qa.MAX_BYTES:
        raise ApiError(413, "audio_too_large", f"the recording is larger than {qa.MAX_BYTES} bytes",
                       {"max_bytes": qa.MAX_BYTES})
    info = qa.inspect(data)
    sha = qa.sha256(data)
    dup = _existing_duplicate(sha)
    if dup:
        return _duplicate_answer(dup), False
    title = _title(body.get("title"), f"Call {attempt_id[:8]}")
    provenance = {"kind": "pbx_call", "attempt_id": attempt_id, "case_id": attempt["case_id"],
                  "recording_path": path, "resolved_path": resolve_path(path), "copied_by": me(),
                  "attempt_created_at": _iso(attempt["created_at"]), "sha256": sha, "format": info.format,
                  "explicit_user_action": True}
    rec_id, relative = _insert_recording(data=data, info=info, sha=sha, title=title, source="pbx_call",
                                         case_id=attempt["case_id"], attempt_id=attempt_id, provenance=provenance)
    audit.record("qc.recording.from_call", ENTITY, rec_id, case_id=attempt["case_id"],
                 after={"source": "pbx_call", "attempt_id": attempt_id, "recording_path": path, "sha256": sha,
                        "size_bytes": info.size_bytes, "duration_seconds": info.duration_seconds})
    _commit_or_remove(relative)
    return {"duplicate": False, "recording": get_recording(rec_id)}, True


# ------------------------------------------------------------------------------ evidence
EVIDENCE_FIELDS = {"employee_label": 100, "notes": 4000, "recorded_action": 1000, "context": 4000}


def update_evidence(rec_id, body: dict) -> dict:
    rec = lock_for_write(rec_id, body.get("expected_revision"))
    values, changed = {}, []
    if "title" in body:
        t = _title(body["title"], "")
        if not t:
            raise _v("title", "title is required", detail="required")
        if t != rec["title"]:
            values["title"] = t
    if "template" in body:
        tpl = body["template"]
        if tpl is None:
            key, version = None, None
        elif isinstance(tpl, dict) and isinstance(tpl.get("key"), str) and isinstance(tpl.get("version"), int) \
                and not isinstance(tpl.get("version"), bool):
            t = get_template(tpl["key"], tpl["version"])
            if not t["active"] and (tpl["key"], tpl["version"]) != (rec["template_key"], rec["template_version"]):
                raise _v("template", "this template version is not active")
            key, version = t["key"], t["version"]
        else:
            raise _v("template", "template must be {\"key\": ..., \"version\": ...} or null")
        if (key, version) != (rec["template_key"], rec["template_version"]):
            values.update(template_key=key, template_version=version)
            changed.append("template")
    for field, limit in EVIDENCE_FIELDS.items():
        if field in body:
            raw = body[field]
            if raw is not None and not isinstance(raw, str):
                raise _v(field, f"{field} must be text")
            val = (raw or "").strip() or None
            if val is not None and len(val) > limit:
                raise _v(field, f"{field} is too long (max {limit})", detail="too_long")
            if val != rec[field]:
                values[field] = val
                changed.append(field)
    if not values:
        return recording_public(rec)
    cleared = {}
    if changed:                                   # the evidence changed: the current draft and approval go
        values.update(current_review_id=None, current_approval_id=None)
        cleared = {"review_cleared": rec["current_review_id"] is not None,
                   "approval_cleared": rec["current_approval_id"] is not None}
    new_rev = _bump(rec_id, **values)
    audit.record("qc.evidence.update", ENTITY, rec_id, case_id=rec["case_id"],
                 after={"fields": sorted(set(values) - {"current_review_id", "current_approval_id"}),
                        "evidence_changed": bool(changed), "revision": new_rev, **cleared})
    return get_recording(rec_id)


# ------------------------------------------------------------------------------ transcripts
def _segments_from_text(body_text: str) -> list:
    lines = [ln.strip() for ln in body_text.replace("\r\n", "\n").split("\n")]
    return [{"start": None, "end": None, "speaker": None, "text": ln} for ln in lines if ln]


def _num_or_none(v, field, i):
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0:
        raise _v("segments", f"segment {i + 1}: {field} must be a non-negative number or null")
    return round(float(v), 3)


def clean_segments(raw, duration: float) -> list:
    if not isinstance(raw, list) or not raw or len(raw) > MAX_SEGMENTS:
        raise _v("segments", f"segments must be a list of 1-{MAX_SEGMENTS} items")
    out = []
    for i, s in enumerate(raw):
        if not isinstance(s, dict) or not isinstance(s.get("text"), str) or not s["text"].strip():
            raise _v("segments", f"segment {i + 1} needs text")
        start, end = _num_or_none(s.get("start"), "start", i), _num_or_none(s.get("end"), "end", i)
        if start is not None and end is not None and end < start:
            raise _v("segments", f"segment {i + 1}: end is before start")
        for v in (start, end):
            if v is not None and v > duration + 1:
                raise _v("segments", f"segment {i + 1}: timing exceeds the recording duration")
        speaker = s.get("speaker")
        if speaker is not None and (not isinstance(speaker, str) or not speaker.strip() or len(speaker) > 40):
            raise _v("segments", f"segment {i + 1}: speaker must be a short label or null")
        out.append({"start": start, "end": end, "speaker": speaker.strip() if speaker else None,
                    "text": s["text"].strip()})
    return out


def number_segments(segments: list) -> list:
    return [{"id": f"s{i + 1}", **{k: s.get(k) for k in ("start", "end", "speaker", "text")}}
            for i, s in enumerate(segments)]


def _speakers(raw, labels: list, previous: dict | None) -> dict:
    """label -> {"role", "verified_by", "verified_at"}. Only labels present in the segments."""
    prev = {k: v for k, v in (previous or {}).items() if k in labels}
    if raw is None:
        return prev
    if not isinstance(raw, dict):
        raise _v("speakers", "speakers must map a speaker label to agent, customer or other (or null)")
    out = dict(prev)
    now = _dt.datetime.now(_dt.timezone.utc).isoformat()
    for label, role in raw.items():
        if label not in labels:
            raise _v("speakers", f"unknown speaker label {label!r}")
        if role is None:
            out.pop(label, None)
            continue
        if role not in ROLES:
            raise _v("speakers", "a speaker role must be agent, customer or other")
        out[label] = {"role": role, "verified_by": me(), "verified_at": now}
    return out


def insert_transcript(rec: dict, *, source: str, text_value: str, segments: list, speakers: dict, language,
                      diarized: bool, model=None, provider_call_id=None, reason=None) -> dict:
    ver = int(_s().execute(text("SELECT coalesce(max(version), 0) + 1 FROM qc_transcripts "
                                "WHERE recording_id = CAST(:id AS uuid)"), {"id": str(rec["id"])}).scalar())
    numbered = number_segments(segments)
    row = _s().execute(text("""
        INSERT INTO qc_transcripts (recording_id, version, source, based_on_id, language, diarized, model,
                                    provider_call_id, text, segments, speakers, segment_count, text_sha256, reason,
                                    created_by)
        VALUES (CAST(:rid AS uuid), :ver, :source, CAST(:based AS uuid), :lang, :diar, :model, CAST(:call AS uuid),
                :text, CAST(:segs AS jsonb), CAST(:spk AS jsonb), :n, :sha, :reason, CAST(:by AS uuid))
        RETURNING *"""),
        {"rid": str(rec["id"]), "ver": ver, "source": source, "based": rec["current_transcript_id"] and
         str(rec["current_transcript_id"]), "lang": language, "diar": diarized, "model": model,
         "call": provider_call_id, "text": text_value, "segs": _json(numbered), "spk": _json(speakers),
         "n": len(numbered), "sha": hashlib.sha256(text_value.encode("utf-8")).hexdigest(), "reason": reason,
         "by": me()}).mappings().one()
    return dict(row)


def save_transcript(rec_id, body: dict) -> dict:
    """A manual transcript, a correction, or a speaker verification: always a new version."""
    rec = lock_for_write(rec_id, body.get("expected_revision"))
    current = _one("qc_transcripts", rec["current_transcript_id"])
    duration = float(rec["duration_seconds"])
    lang = body.get("language")
    if lang is not None and lang not in ("ar", "en"):
        raise _v("language", "language must be ar or en")
    reason = body.get("reason")
    if reason is not None and (not isinstance(reason, str) or len(reason) > 500):
        raise _v("reason", "reason must be text of at most 500 characters")
    if body.get("segments") is not None:
        segments = clean_segments(body["segments"], duration)
        text_value = "\n".join(s["text"] for s in segments)
        diarized = any(s["speaker"] for s in segments)
    elif body.get("text") is not None:
        if not isinstance(body["text"], str) or not body["text"].strip():
            raise _v("text", "text must not be empty", detail="required")
        text_value = body["text"].strip()
        segments = _segments_from_text(text_value)
        diarized = False
    elif body.get("speakers") is not None:
        if current is None or current["purged_at"] is not None:
            raise ApiError(422, "transcript_required", "there is no transcript to verify speakers on",
                           {"transcript": "required"})
        text_value, diarized = current["text"], current["diarized"]
        segments = [{k: s.get(k) for k in ("start", "end", "speaker", "text")} for s in current["segments"]]
        lang = lang or current["language"]
    else:
        raise ApiError(422, "validation_failed", "send text, segments or speakers", {"text": "required"})
    if len(text_value) > MAX_TRANSCRIPT_CHARS:
        raise _v("text", f"the transcript is too long (max {MAX_TRANSCRIPT_CHARS} characters)", detail="too_long")
    labels = _speaker_labels(segments)
    prev_speakers = current["speakers"] if current is not None else None
    speakers = _speakers(body.get("speakers"), labels, prev_speakers)
    source = "manual" if current is None else "correction"
    t = insert_transcript(rec, source=source, text_value=text_value, segments=segments, speakers=speakers,
                          language=lang, diarized=diarized, reason=(reason or "").strip() or None)
    new_rev = _bump(rec_id, current_transcript_id=str(t["id"]), current_review_id=None, current_approval_id=None,
                    last_error=None)
    audit.record("qc.transcript.save", ENTITY, rec_id, case_id=rec["case_id"],
                 after={"transcript_id": str(t["id"]), "version": t["version"], "source": source,
                        "segment_count": t["segment_count"], "text_sha256": t["text_sha256"],
                        "speakers_verified": sorted(speakers), "revision": new_rev,
                        "review_cleared": rec["current_review_id"] is not None,
                        "approval_cleared": rec["current_approval_id"] is not None})
    return get_recording(rec_id)


def list_transcripts(rec_id) -> list:
    _load(rec_id)
    rows = [dict(r) for r in _s().execute(text("SELECT * FROM qc_transcripts WHERE recording_id = CAST(:id AS uuid) "
                                               "ORDER BY version DESC"), {"id": str(rec_id)}).mappings()]
    names = _users([r["created_by"] for r in rows])
    return [transcript_public(r, names) for r in rows]


# ------------------------------------------------------------------------------ reviews
def evidence_context(rec: dict, transcript: dict | None) -> qe.Context:
    t = qe.Transcript(transcript["text"] if transcript else None, transcript["segments"] if transcript else None)
    return qe.Context(transcript=t, duration=float(rec["duration_seconds"]),
                      audio_available=rec["audio_purged_at"] is None,
                      inputs={"notes": rec["notes"], "recorded_action": rec["recorded_action"]})


def insert_review(rec: dict, *, origin: str, template: dict, transcript: dict | None, results: list, summary,
                  coaching, language, recording_revision: int, based_on_id=None, model=None,
                  provider_call_id=None, validation=None) -> dict:
    ver = int(_s().execute(text("SELECT coalesce(max(version), 0) + 1 FROM qc_reviews "
                                "WHERE recording_id = CAST(:id AS uuid)"), {"id": str(rec["id"])}).scalar())
    c = qe.counts(results)
    row = _s().execute(text("""
        INSERT INTO qc_reviews (recording_id, version, origin, based_on_id, recording_revision, template_key,
                                template_version, transcript_id, results, summary, coaching, language, met,
                                needs_improvement, not_applicable, insufficient_evidence, total, model,
                                provider_call_id, validation, created_by)
        VALUES (CAST(:rid AS uuid), :ver, :origin, CAST(:based AS uuid), :rrev, :tkey, :tver, CAST(:tid AS uuid),
                CAST(:results AS jsonb), :summary, :coaching, :lang, :met, :ni, :na, :ie, :total, :model,
                CAST(:call AS uuid), CAST(:validation AS jsonb), CAST(:by AS uuid))
        RETURNING *"""),
        {"rid": str(rec["id"]), "ver": ver, "origin": origin, "based": based_on_id, "rrev": recording_revision,
         "tkey": template["key"], "tver": template["version"], "tid": transcript and str(transcript["id"]),
         "results": _json(results), "summary": summary, "coaching": coaching, "lang": language,
         "met": c["met"], "ni": c["needs_improvement"], "na": c["not_applicable"], "ie": c["insufficient_evidence"],
         "total": c["total"], "model": model, "call": provider_call_id, "validation": _json(validation or []),
         "by": me()}).mappings().one()
    return dict(row)


def _opt_text(body, field, limit):
    v = body.get(field)
    if v is None:
        return None
    if not isinstance(v, str) or len(v) > limit:
        raise _v(field, f"{field} must be text of at most {limit} characters")
    return v.strip() or None


def save_review(rec_id, body: dict) -> dict:
    """A human review revision: every criterion, each with a reason; the evidence rule applies."""
    rec = lock_for_write(rec_id, body.get("expected_revision"))
    template = get_template(rec["template_key"], rec["template_version"])
    transcript = _one("qc_transcripts", rec["current_transcript_id"])
    ctx = evidence_context(rec, transcript)
    results = qe.validate_human(body.get("results"), template, ctx)
    lang = body.get("language")
    if lang is not None and lang not in ("ar", "en"):
        raise _v("language", "language must be ar or en")
    based = body.get("based_on_review_id")
    if based is not None:
        from services.case_history_common import parse_uuid
        based = parse_uuid(based, "based_on_review_id")
        ok_ = _s().execute(text("SELECT 1 FROM qc_reviews WHERE id = CAST(:b AS uuid) AND recording_id = CAST(:r AS uuid)"),
                           {"b": based, "r": str(rec_id)}).first()
        if ok_ is None:
            raise _v("based_on_review_id", "based_on_review_id must be a review of this recording")
    elif rec["current_review_id"]:
        based = str(rec["current_review_id"])
    rv = insert_review(rec, origin="human", template=template, transcript=transcript, results=results,
                       summary=_opt_text(body, "summary", 4000), coaching=_opt_text(body, "coaching", 4000),
                       language=lang, recording_revision=rec["revision"] + 1, based_on_id=based)
    new_rev = _bump(rec_id, current_review_id=str(rv["id"]), current_approval_id=None, last_error=None)
    m = qe.metrics(results)
    audit.record("qc.review.save", ENTITY, rec_id, case_id=rec["case_id"],
                 after={"review_id": str(rv["id"]), "version": rv["version"], "origin": "human", "revision": new_rev,
                        "counts": {k: m[k] for k in (*qe.RESULTS, "total")},
                        "approval_cleared": rec["current_approval_id"] is not None})
    return get_recording(rec_id)


def list_reviews(rec_id) -> list:
    _load(rec_id)
    rows = [dict(r) for r in _s().execute(text("SELECT * FROM qc_reviews WHERE recording_id = CAST(:id AS uuid) "
                                               "ORDER BY version DESC"), {"id": str(rec_id)}).mappings()]
    names = _users([r["created_by"] for r in rows])
    return [review_public(r, names) for r in rows]


# ------------------------------------------------------------------------------ approval
def _contract_of(case_id):
    if not case_id:
        return None
    return _s().execute(text("""
        SELECT p.contract_id::text FROM clints c JOIN portfolios p ON p.id = c.portfolios_id
         WHERE c.id = CAST(:id AS uuid)"""), {"id": str(case_id)}).scalar()


def approve(rec_id, body: dict) -> dict:
    if body.get("confirmed") is not True:
        raise ApiError(422, "confirmation_required",
                       "confirm that every criterion and its evidence were reviewed (confirmed: true)",
                       {"confirmed": "required"})
    rec = lock_for_write(rec_id, body.get("expected_revision"))
    if rec["current_approval_id"]:
        raise ApiError(409, "already_approved", "this revision is already approved", {"revision": rec["revision"]})
    rv = _one("qc_reviews", rec["current_review_id"])
    if rv is None:
        raise ApiError(422, "review_required", "save a review before approving", {"review": "required"})
    if rv["origin"] != "human":
        raise ApiError(422, "human_review_required",
                       "an AI draft cannot be approved: a reviewer must check every criterion and save the review",
                       {"review_origin": rv["origin"]})
    template = get_template(rv["template_key"], rv["template_version"])
    transcript = _one("qc_transcripts", rv["transcript_id"])
    # the saved review must still satisfy the evidence rule against the saved evidence
    qe.validate_human([{k: r.get(k) for k in ("criterion_id", "result", "reason", "evidence")} for r in rv["results"]],
                      template, evidence_context(rec, transcript))
    now = _now()
    c = {k: rv[k] for k in qe.RESULTS}
    c["total"] = rv["total"]
    names = _users([rv["created_by"], me()])
    snapshot = {
        "schema": 1,
        "recording": {"id": str(rec["id"]), "title": rec["title"], "source": rec["source"],
                      "audio": {k: v for k, v in _audio_public(rec).items() if k not in ("url",)},
                      "case_id": str(rec["case_id"]) if rec["case_id"] else None,
                      "attempt_id": str(rec["attempt_id"]) if rec["attempt_id"] else None,
                      "provenance": rec["provenance"]},
        "template": template,
        "transcript": ({k: v for k, v in transcript_public(transcript, _users([transcript["created_by"]])).items()}
                       if transcript else None),
        "evidence": {"template": {"key": rec["template_key"], "version": rec["template_version"]},
                     "employee_label": rec["employee_label"], "notes": rec["notes"],
                     "recorded_action": rec["recorded_action"], "context": rec["context"]},
        "review": review_public(rv, names),
        "metrics": qe.metrics_from_counts(c),
        "approval": {"approved_revision": rec["revision"], "approved_by": {"id": me(), "name": names.get(me())},
                     "approved_at": _iso(now)},
    }
    sha = canonical_sha256(snapshot)
    ap = _s().execute(text("""
        INSERT INTO qc_approvals (recording_id, review_id, approved_revision, approved_by, approved_at, template_key,
                                  template_version, case_id, contract_id, met, needs_improvement, not_applicable,
                                  insufficient_evidence, total, snapshot, snapshot_sha256)
        VALUES (CAST(:rid AS uuid), CAST(:rv AS uuid), :arev, CAST(:by AS uuid), :now, :tkey, :tver, CAST(:case AS uuid),
                CAST(:contract AS uuid), :met, :ni, :na, :ie, :total, CAST(:snap AS jsonb), :sha)
        RETURNING id"""),
        {"rid": str(rec_id), "rv": str(rv["id"]), "arev": rec["revision"], "by": me(), "now": now,
         "tkey": rv["template_key"], "tver": rv["template_version"], "case": rec["case_id"] and str(rec["case_id"]),
         "contract": _contract_of(rec["case_id"]), "met": c["met"], "ni": c["needs_improvement"],
         "na": c["not_applicable"], "ie": c["insufficient_evidence"], "total": c["total"], "snap": _json(snapshot),
         "sha": sha}).scalar()
    new_rev = _bump(rec_id, current_approval_id=str(ap))
    audit.record("qc.review.approve", ENTITY, rec_id, case_id=rec["case_id"],
                 after={"approval_id": str(ap), "review_id": str(rv["id"]), "approved_revision": rec["revision"],
                        "revision": new_rev, "snapshot_sha256": sha,
                        "counts": {k: c[k] for k in (*qe.RESULTS, "total")}})
    return get_recording(rec_id)


def list_approvals(rec_id) -> list:
    rec = _load(rec_id)
    rows = [dict(r) for r in _s().execute(text("SELECT * FROM qc_approvals WHERE recording_id = CAST(:id AS uuid) "
                                               "ORDER BY approved_at DESC, id"), {"id": str(rec_id)}).mappings()]
    names = _users([r["approved_by"] for r in rows])
    return [approval_public(r, names, current_id=rec["current_approval_id"]) for r in rows]


def get_approval(approval_id) -> dict:
    a = _one("qc_approvals", approval_id)
    if a is None:
        raise ApiError(404, "approval_not_found", "approval not found", {"approval_id": str(approval_id)})
    try:
        rec = _load(a["recording_id"])
    except ApiError:
        raise ApiError(404, "approval_not_found", "approval not found", {"approval_id": str(approval_id)}) from None
    return approval_public(a, _users([a["approved_by"]]), current_id=rec["current_approval_id"], with_snapshot=True)


# ------------------------------------------------------------------------------ audio
def audio_for_playback(rec_id) -> dict:
    rec = _load(rec_id)
    if rec["audio_purged_at"] is not None or not rec["stored_path"]:
        raise ApiError(410, "audio_purged", "the audio of this recording was removed by the retention policy",
                       {"purged_at": _iso(rec["audio_purged_at"])})
    if not qa.exists(rec["stored_path"]):
        raise ApiError(404, "audio_missing", "the audio file is missing from private storage", None)
    return rec


def audit_audio_read(rec: dict, start: int, length: int, status: int) -> None:
    audit.record("qc.audio.read", ENTITY, rec["id"], case_id=rec["case_id"],
                 after={"range_start": start, "bytes": length, "status": status, "size_bytes": rec["size_bytes"]})
    _s().commit()


# ------------------------------------------------------------------------------ dashboard
def _date_arg(args, field):
    raw = args.get(field)
    if not raw:
        return None
    try:
        return _dt.date.fromisoformat(raw)
    except ValueError:
        raise _v(field, f"{field} must be YYYY-MM-DD") from None


GROUPS = {"day": "YYYY-MM-DD", "week": 'IYYY-"W"IW', "month": "YYYY-MM"}


def dashboard(args) -> dict:
    """Current approvals only (qc_recordings.current_approval_id), in the caller's scope.
    Exact counts; compliance is pooled: sum(met) / sum(met + needs improvement)."""
    from services.case_history_common import parse_uuid
    cond, params = _visible_sql("r")
    where = [cond]
    tkey = args.get("template")
    if tkey:
        where.append("a.template_key = :tkey")
        params["tkey"] = tkey
    if args.get("template_version"):
        try:
            params["tver"] = int(args["template_version"])
        except ValueError:
            raise _v("template_version", "template_version must be an integer") from None
        where.append("a.template_version = :tver")
    if args.get("contract_id"):
        params["contract"] = parse_uuid(args["contract_id"], "contract_id")
        where.append("a.contract_id = CAST(:contract AS uuid)")
    frm, to = _date_arg(args, "from"), _date_arg(args, "to")
    if frm and to and frm > to:
        raise _v("from", "from must not be after to")
    if frm:
        where.append("(a.approved_at AT TIME ZONE 'Asia/Riyadh')::date >= :frm")
        params["frm"] = frm
    if to:
        where.append("(a.approved_at AT TIME ZONE 'Asia/Riyadh')::date <= :to")
        params["to"] = to
    group = args.get("group") or "month"
    if group not in GROUPS:
        raise _v("group", "group must be day, week or month")
    where_sql = " AND ".join(where)

    def base(joins: str = "") -> str:
        return (f"FROM qc_approvals a JOIN qc_recordings r ON r.current_approval_id = a.id {joins} "
                f"WHERE {where_sql}")
    sums = """count(*) AS reviews, coalesce(sum(a.met), 0) AS met, coalesce(sum(a.needs_improvement), 0) AS ni,
              coalesce(sum(a.not_applicable), 0) AS na, coalesce(sum(a.insufficient_evidence), 0) AS ie,
              coalesce(sum(a.total), 0) AS total,
              count(*) FILTER (WHERE a.met + a.needs_improvement > 0) AS with_pct"""

    def pack(r) -> dict:
        m = qe.metrics_from_counts({"met": int(r["met"]), "needs_improvement": int(r["ni"]),
                                    "not_applicable": int(r["na"]), "insufficient_evidence": int(r["ie"]),
                                    "total": int(r["total"])})
        m["reviews"] = int(r["reviews"])
        m["reviews_with_percentage"] = int(r["with_pct"])
        m["reviews_without_percentage"] = int(r["reviews"]) - int(r["with_pct"])
        return m

    s = _s()
    totals = pack(s.execute(text(f"SELECT {sums} {base()}"), params).mappings().one())
    by_template = []
    for r in s.execute(text(f"""
            SELECT a.template_key, a.template_version, t.name_en, t.name_ar, {sums}
              {base("JOIN qc_templates t ON t.template_key = a.template_key AND t.version = a.template_version")}
             GROUP BY a.template_key, a.template_version, t.name_en, t.name_ar
             ORDER BY a.template_key, a.template_version"""), params).mappings():
        by_template.append({"template": {"key": r["template_key"], "version": r["template_version"],
                                         "name_en": r["name_en"], "name_ar": r["name_ar"]}, **pack(r)})
    fmt = GROUPS[group]
    by_period = [{"period": r["period"], **pack(r)} for r in s.execute(text(f"""
            SELECT to_char(a.approved_at AT TIME ZONE 'Asia/Riyadh', '{fmt}') AS period, {sums} {base()}
             GROUP BY 1 ORDER BY 1"""), params).mappings()]
    by_contract = [{"contract": {"id": r["contract_id"], "name": r["contract_name"]} if r["contract_id"] else None,
                    **pack(r)} for r in s.execute(text(f"""
            SELECT a.contract_id::text AS contract_id, k.name AS contract_name, {sums}
              {base("LEFT JOIN contracts k ON k.id = a.contract_id")}
             GROUP BY a.contract_id, k.name ORDER BY k.name NULLS LAST, a.contract_id"""), params).mappings()]
    by_criterion = []
    for r in s.execute(text(f"""
            SELECT a.template_key, a.template_version, x.item->>'criterion_id' AS criterion_id,
                   count(*) AS reviews,
                   count(*) FILTER (WHERE x.item->>'result' = 'met') AS met,
                   count(*) FILTER (WHERE x.item->>'result' = 'needs_improvement') AS ni,
                   count(*) FILTER (WHERE x.item->>'result' = 'not_applicable') AS na,
                   count(*) FILTER (WHERE x.item->>'result' = 'insufficient_evidence') AS ie,
                   count(*) AS total,
                   count(*) FILTER (WHERE x.item->>'result' IN ('met', 'needs_improvement')) AS with_pct
              {base("JOIN qc_reviews rv ON rv.id = a.review_id "
                    "CROSS JOIN LATERAL jsonb_array_elements(rv.results) WITH ORDINALITY AS x(item, ord)")}
             GROUP BY a.template_key, a.template_version, x.item->>'criterion_id'
             ORDER BY a.template_key, a.template_version, min(x.ord)"""), params).mappings():
        by_criterion.append({"template": {"key": r["template_key"], "version": r["template_version"]},
                             "criterion_id": r["criterion_id"], **pack(r)})
    # criterion names from the templates
    tpl_cache = {}
    for item in by_criterion:
        k = (item["template"]["key"], item["template"]["version"])
        if k not in tpl_cache:
            tpl_cache[k] = {c["id"]: c for c in get_template(*k)["criteria"]}
        c = tpl_cache[k].get(item["criterion_id"]) or {}
        item.update(name_en=c.get("en"), name_ar=c.get("ar"), human_listening_only=bool(c.get("human_listening_only")))
    return {
        "basis": "approved_reviews_only",
        "note": ("Approved reviews only (the current approval of each recording). Compliance = met / (met + needs "
                 "improvement); coverage = (met + needs improvement) / all criteria; a review with no met or needs "
                 "improvement has no percentage. Call-level findings, not an employee ranking."),
        "filters": {"template": tkey, "template_version": params.get("tver"), "contract_id": params.get("contract"),
                    "from": frm.isoformat() if frm else None, "to": to.isoformat() if to else None, "group": group,
                    "timezone": "Asia/Riyadh", "date_basis": "approved_at"},
        "totals": totals, "by_template": by_template, "by_criterion": by_criterion, "by_period": by_period,
        "by_contract": by_contract,
    }


def overview() -> dict:
    from services.ai import gateway
    st = gateway.status(details=False)
    cond, params = _visible_sql("r")
    counts = _s().execute(text(f"""
        SELECT count(*) AS recordings,
               count(*) FILTER (WHERE r.current_approval_id IS NOT NULL) AS approved,
               count(*) FILTER (WHERE r.current_review_id IS NOT NULL AND r.current_approval_id IS NULL) AS drafts,
               count(*) FILTER (WHERE r.audio_purged_at IS NULL) AS with_audio
          FROM qc_recordings r WHERE {cond}"""), params).mappings().one()
    return {"templates": list_templates(),
            "ai": {"transcribe": st["capabilities"]["transcribe"],
                   "draft": st["capabilities"]["generate_structured"], "policy": st["policy"]},
            "counts": {k: int(v) for k, v in counts.items()},
            "limits": {"max_bytes": qa.MAX_BYTES, "max_seconds": qa.MAX_SECONDS, "formats": ["wav", "mp3"],
                       "sample_rate": [qa.MIN_RATE, qa.MAX_RATE]},
            "results": list(qe.RESULTS)}
