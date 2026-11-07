"""Collector activity report (customer request 2026-11-05; services/collector_report.py).

  GET /v2/reports/collector-activity/options   the users in the caller's scope   reports_performance |
                                                                                calls_supervise | calls_manage
  GET /v2/reports/collector-activity           ?user_id&from&to (YYYY-MM-DD, Asia/Riyadh; default the last
                                               30 days; at most 92) -> {user, extensions, from, to, totals,
                                               weeks, days}                       same keys
  GET /v2/reports/collector-activity/html      the same as a print-ready Arabic page (the browser saves it
                                               as PDF); audited                   same keys
  GET /v2/reports/collector-activity/docx      the same as a Word file (right to left, charts as images);
                                               audited                            same keys

Scope is the team dashboard's: SuperAdmin everyone, a supervisor their team, anyone else themselves
(403 not_in_scope outside it).
"""
from __future__ import annotations

import io

from flask import Blueprint, Response, request, send_file
from flask_jwt_extended import jwt_required

from core import audit
from core.api_response import ok, register_api_errors
from extensions import db
from services import collector_report as svc
from services.calling.common import CALLS_MANAGE, CALLS_SUPERVISE, permission_required

bp = Blueprint("collector_report", __name__)
register_api_errors(bp)
URL_PREFIX = None
KEYS = ("reports_performance", CALLS_SUPERVISE, CALLS_MANAGE)


@bp.get("/v2/reports/collector-activity/options")
@jwt_required()
@permission_required(*KEYS)
def options():
    return ok(svc.options())


@bp.get("/v2/reports/collector-activity")
@jwt_required()
@permission_required(*KEYS)
def report():
    return ok(svc.report(request.args))


@bp.get("/v2/reports/collector-activity/html")
@jwt_required()
@permission_required(*KEYS)
def report_html():
    data = svc.report(request.args)
    audit.record("collector_report.export", "user", data["user"]["id"],
                 after={"from": data["from"], "to": data["to"], "format": "html"})
    db.session.commit()
    resp = Response(svc.render_html(data), mimetype="text/html")
    resp.headers["Cache-Control"] = "private, no-store"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


@bp.get("/v2/reports/collector-activity/docx")
@jwt_required()
@permission_required(*KEYS)
def report_docx():
    data = svc.report(request.args)
    payload = svc.render_docx(data)
    audit.record("collector_report.export", "user", data["user"]["id"],
                 after={"from": data["from"], "to": data["to"], "format": "docx"})
    db.session.commit()
    resp = send_file(io.BytesIO(payload), as_attachment=True, download_name=svc.file_name(data, "docx"), max_age=0,
                     mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    resp.headers["Cache-Control"] = "private, no-store"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp
