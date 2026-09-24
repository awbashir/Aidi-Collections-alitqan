"""Phase 6 Q2: the quality workspace API (docs/phase6-spec.md, review/phase6-quality.md).

Keys: `quality_review` (library, transcription, drafts, reviews, reports, dashboard) and
`quality_approve` (approval; it may also read). Adding a call recording also needs a recording key
(`clints_callsRecords_play` or `_download`). Every response is `Cache-Control: no-store`.

    GET    /v2/quality                                   overview: templates, AI readiness, counts, limits
    GET    /v2/quality/templates                         template versions (?include_inactive=1)
    GET    /v2/quality/templates/<key>/<version>         one template
    GET    /v2/quality/recordings                        library (?status, source, case_id, attempt_id, template, page)
    POST   /v2/quality/recordings                        multipart `file` (WAV/MP3), `title`, `case_id`   idempotent
    POST   /v2/quality/recordings/from-call              {attempt_id, recording_path?, title?, confirmed: true} idempotent
    GET    /v2/quality/recordings/<id>                   detail (evidence, transcript, review, approval)
    PATCH  /v2/quality/recordings/<id>                   evidence {expected_revision, template, notes, ...} idempotent
    GET    /v2/quality/recordings/<id>/audio             audio (HEAD, Range; no-store)
    PUT    /v2/quality/recordings/<id>/transcript        manual / correction / speaker roles    idempotent
    GET    /v2/quality/recordings/<id>/transcripts       transcript versions
    POST   /v2/quality/recordings/<id>/transcribe        provider transcription (gateway)       idempotent
    POST   /v2/quality/recordings/<id>/draft             AI review draft (gateway)              idempotent
    PUT    /v2/quality/recordings/<id>/review            human review revision                  idempotent
    GET    /v2/quality/recordings/<id>/reviews           review revisions
    POST   /v2/quality/recordings/<id>/approve           {expected_revision, confirmed: true}  quality_approve, idempotent
    GET    /v2/quality/recordings/<id>/approvals         approval history
    GET    /v2/quality/approvals/<approval_id>           one approval with its immutable snapshot
    GET    /v2/quality/recordings/<id>/report            HTML report (?language=ar|en, approval_id)
    GET    /v2/quality/dashboard                         approved reviews only (?template, template_version,
                                                         contract_id, from, to, group=day|week|month)
"""
from __future__ import annotations

from flask import Blueprint, Response, request
from flask_jwt_extended import jwt_required

from core.api_response import created, fail, json_body, ok, register_api_errors
from core.idempotency import idempotent
from services.ai.permissions import QUALITY_APPROVE, QUALITY_REVIEW, ai_permission_required, requires_ai

bp = Blueprint("quality", __name__)
register_api_errors(bp)
URL_PREFIX = None

READ = (QUALITY_REVIEW, QUALITY_APPROVE)


@bp.after_request
def _no_store(resp):
    resp.headers["Cache-Control"] = "no-store"
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    return resp


def _svc():
    from services.ai import quality
    return quality


@bp.get("/v2/quality")
@jwt_required()
@ai_permission_required(*READ)
def overview():
    return ok(_svc().overview())


@bp.get("/v2/quality/templates")
@jwt_required()
@ai_permission_required(*READ)
def templates():
    return ok(_svc().list_templates(include_inactive=request.args.get("include_inactive") in ("1", "true")))


@bp.get("/v2/quality/templates/<key>/<int:version>")
@jwt_required()
@ai_permission_required(*READ)
def template(key, version):
    return ok(_svc().get_template(key, version))


@bp.get("/v2/quality/recordings")
@jwt_required()
@ai_permission_required(*READ)
def recordings():
    return ok(_svc().list_recordings(request.args))


@bp.post("/v2/quality/recordings")
@jwt_required()
@ai_permission_required(QUALITY_REVIEW)
@idempotent()
def import_recording():
    body, new = _svc().import_recording(request.files.get("file"), request.form)
    return created(body, "imported") if new else ok(body, "duplicate: the same audio is already in the library")


@bp.post("/v2/quality/recordings/from-call")
@jwt_required()
@ai_permission_required(QUALITY_REVIEW)
@idempotent()
def from_call():
    body, new = _svc().add_from_call(json_body())
    return created(body, "call recording added to QC") if new else \
        ok(body, "duplicate: the same audio is already in the library")


@bp.get("/v2/quality/recordings/<uuid:rec_id>")
@jwt_required()
@ai_permission_required(*READ)
def recording(rec_id):
    return ok(_svc().get_recording(rec_id))


@bp.patch("/v2/quality/recordings/<uuid:rec_id>")
@jwt_required()
@ai_permission_required(QUALITY_REVIEW)
@idempotent()
def update_evidence(rec_id):
    return ok(_svc().update_evidence(rec_id, json_body()), "saved")


@bp.route("/v2/quality/recordings/<uuid:rec_id>/audio", methods=["GET", "HEAD"])
@jwt_required()
@ai_permission_required(*READ)
def audio(rec_id):
    from services.ai import quality_audio as qa
    svc = _svc()
    rec = svc.audio_for_playback(rec_id)
    size = int(rec["size_bytes"])
    rng = qa.parse_range(request.headers.get("Range"), size)
    headers = {"Accept-Ranges": "bytes", "Content-Type": rec["content_type"],
               "Content-Disposition": f'inline; filename="qc-{rec["id"]}.{rec["audio_format"]}"'}
    if rng == "unsatisfiable":
        headers["Content-Range"] = f"bytes */{size}"
        return Response(b"", status=416, headers=headers)
    if rng is None:
        start, end, status = 0, size - 1, 200
    else:
        start, end = rng
        status = 206
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    length = end - start + 1
    headers["Content-Length"] = str(length)
    if request.method != "HEAD":             # HEAD: headers only (the lazy body is never read)
        svc.audit_audio_read(rec, start, length, status)
    return Response(qa.iter_file(rec["stored_path"], start, length), status=status, headers=headers,
                    direct_passthrough=True)


@bp.put("/v2/quality/recordings/<uuid:rec_id>/transcript")
@jwt_required()
@ai_permission_required(QUALITY_REVIEW)
@idempotent()
def save_transcript(rec_id):
    return ok(_svc().save_transcript(rec_id, json_body()), "transcript saved")


@bp.get("/v2/quality/recordings/<uuid:rec_id>/transcripts")
@jwt_required()
@ai_permission_required(*READ)
def transcripts(rec_id):
    return ok(_svc().list_transcripts(rec_id))


@bp.post("/v2/quality/recordings/<uuid:rec_id>/transcribe")
@jwt_required()
@ai_permission_required(QUALITY_REVIEW)
@requires_ai("transcribe")
@idempotent()
def transcribe(rec_id):
    from services.ai import quality_ai
    return ok(quality_ai.transcribe(rec_id, json_body()), "transcribed")


@bp.post("/v2/quality/recordings/<uuid:rec_id>/draft")
@jwt_required()
@ai_permission_required(QUALITY_REVIEW)
@requires_ai("generate_structured", categories=("transcripts",))
@idempotent()
def draft(rec_id):
    from services.ai import quality_ai
    return ok(quality_ai.draft(rec_id, json_body()), "AI draft saved; a human must review every criterion")


@bp.put("/v2/quality/recordings/<uuid:rec_id>/review")
@jwt_required()
@ai_permission_required(QUALITY_REVIEW)
@idempotent()
def save_review(rec_id):
    return ok(_svc().save_review(rec_id, json_body()), "review saved")


@bp.get("/v2/quality/recordings/<uuid:rec_id>/reviews")
@jwt_required()
@ai_permission_required(*READ)
def reviews(rec_id):
    return ok(_svc().list_reviews(rec_id))


@bp.post("/v2/quality/recordings/<uuid:rec_id>/approve")
@jwt_required()
@ai_permission_required(QUALITY_APPROVE)
@idempotent()
def approve(rec_id):
    return ok(_svc().approve(rec_id, json_body()), "approved")


@bp.get("/v2/quality/recordings/<uuid:rec_id>/approvals")
@jwt_required()
@ai_permission_required(*READ)
def approvals(rec_id):
    return ok(_svc().list_approvals(rec_id))


@bp.get("/v2/quality/approvals/<uuid:approval_id>")
@jwt_required()
@ai_permission_required(*READ)
def approval(approval_id):
    return ok(_svc().get_approval(approval_id))


@bp.get("/v2/quality/recordings/<uuid:rec_id>/report")
@jwt_required()
@ai_permission_required(*READ)
def report(rec_id):
    from core import audit
    from extensions import db
    from services.ai import quality_report
    from services.case_history_common import parse_uuid
    language = request.args.get("language") or "en"
    approval_id = request.args.get("approval_id")
    if approval_id:
        approval_id = parse_uuid(approval_id, "approval_id")
    svc = _svc()
    names = svc._users([svc.me()])
    doc, meta = quality_report.render(rec_id, language, approval_id, generated_by=names.get(svc.me()) or svc.me())
    rec = svc._load(rec_id)
    audit.record("qc.report.export", svc.ENTITY, rec_id, case_id=rec["case_id"], after=meta)
    db.session.commit()
    return Response(doc.encode("utf-8"), status=200, headers={
        "Content-Type": "text/html; charset=utf-8",
        "Content-Disposition": f'attachment; filename="qc-report-{rec_id}-{language}.html"',
        "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'",
    })


@bp.get("/v2/quality/dashboard")
@jwt_required()
@ai_permission_required(*READ)
def dashboard():
    return ok(_svc().dashboard(request.args))


@bp.errorhandler(413)
def _too_large(_e):
    return fail(413, "audio_too_large", "the recording is too large", None)
