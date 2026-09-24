"""Phase 4 stream R0: v2 reports, scoped selector options and private report exports
(docs/phase4-spec.md; the contract and examples are in review/phase4-framework.md).

Conventions (core/api_response.py): real HTTP codes, `{message, status, data}` on success,
`{message, status:false, error, code}` on failure, never 401 for a business error. Money is
an exact decimal string; instants are ISO 8601 UTC (`...Z`); date filters are inclusive
Asia/Riyadh civil dates. Each report is gated by the key of its legacy screen (screen-key
rule); exporting follows the same key.

    GET  /v2/reports                          reports the caller may open (grouped by family)
    GET  /v2/reports/<id>                     definition: filters, columns, sorts, totals, formats
    GET  /v2/reports/<id>/rows                filters + sort + page -> items, total, exact totals
    GET  /v2/report-options/<field>?report=   scoped selector (50 per page, q, selected)
    POST /v2/reports/<id>/exports             {format, params, sort} -> 202 queued (Idempotency-Key)
    GET  /v2/report-exports                   the caller's exports (report, status, paging)
    GET  /v2/report-exports/<id>              one export (creator only) + attempts
    GET  /v2/report-exports/<id>/download     the file (creator only, permission rechecked)
"""
from __future__ import annotations

from flask import Blueprint, request, send_file
from flask_jwt_extended import get_jwt_identity, jwt_required

from core.api_response import ApiError, json_body, ok, register_api_errors
from core.idempotency import idempotent

bp = Blueprint("reports_v2", __name__)
register_api_errors(bp)
URL_PREFIX = None


def _me() -> str:
    return str(get_jwt_identity())


def _report_or_404(report_id):
    from core.reports import registry
    report = registry.get(report_id)
    if report is None:
        raise ApiError(404, "report_not_found", f"there is no report {report_id!r}", {"id": report_id})
    return report


def _allowed(report) -> bool:
    from core.auth import has_permission
    return any(has_permission(k) for k in report.permissions)


def _require(report):
    if not _allowed(report):
        raise ApiError(403, "forbidden", "You are not authorized to access this resource",
                       {"required_any": list(report.permissions)})


def _scope():
    from services.clients_scope import resolve_scope
    return resolve_scope(_me())


# --------------------------------------------------------------------------- catalogue
@bp.get("/v2/reports")
@jwt_required()
def reports_list():
    from core.reports import registry
    items = [r.describe(full=False) for r in registry.all_reports() if _allowed(r)]
    families = {}
    for it in items:
        families.setdefault(it["family"], []).append(it["id"])
    return ok({"items": items, "total": len(items),
               "families": [{"family": k, "reports": v} for k, v in families.items()]})


@bp.get("/v2/reports/<report_id>")
@jwt_required()
def report_definition(report_id):
    report = _report_or_404(report_id)
    _require(report)
    return ok(report.describe(full=True))


@bp.get("/v2/reports/<report_id>/rows")
@jwt_required()
def report_rows(report_id):
    from core.reports import engine, privacy
    report = _report_or_404(report_id)
    _require(report)
    return ok(engine.preview(report, request.args, _me(), _scope(), privacy.request_rights()))


@bp.get("/v2/report-options/<field>")
@jwt_required()
def report_options(field):
    from core.reports import options
    report_id = request.args.get("report")
    if not report_id:
        raise ApiError(422, "validation_failed", "report is required", {"report": "required"})
    report = _report_or_404(report_id)
    _require(report)
    return ok(options.options(report, field, request.args, _me(), _scope()))


# --------------------------------------------------------------------------- exports
@bp.post("/v2/reports/<report_id>/exports")
@jwt_required()
@idempotent()
def export_create(report_id):
    from core.reports import exports
    report = _report_or_404(report_id)
    _require(report)
    return ok(exports.create(report, json_body(required=False), _me()), "export queued", 202)


@bp.get("/v2/report-exports")
@jwt_required()
def export_list():
    from core.reports import exports
    return ok(exports.list_own(_me(), request.args))


@bp.get("/v2/report-exports/<uuid:export_id>")
@jwt_required()
def export_get(export_id):
    from core.reports import exports
    return ok(exports.get(export_id, _me()))


@bp.get("/v2/report-exports/<uuid:export_id>/download")
@jwt_required()
def export_download(export_id):
    from core.reports import exports
    path, name, ctype = exports.for_download(export_id, _me())
    resp = send_file(path, mimetype=ctype.split(";")[0], as_attachment=True, download_name=name, max_age=0,
                     conditional=False, etag=False)
    if ctype.startswith("text/csv"):
        resp.headers["Content-Type"] = ctype
    resp.headers["Cache-Control"] = "private, no-store"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp
