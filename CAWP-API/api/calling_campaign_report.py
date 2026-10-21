"""Campaign report ("تقرير الحملة", customer request 2026-10-21), beside api/calling_campaigns.py.
Definitions: services/calling/campaign_report.py.

                                                                      permission
  GET /v2/campaigns/<id>/report          ?payment_days=0..90 (7)       campaigns_manage
  GET /v2/campaigns/<id>/report/export   the same as XLSX (audited)    campaigns_manage

Same visibility as the campaign itself (404 when the caller may not see it). Final once the
campaign is stopped (`final` true), "so far" while it runs.
"""
from __future__ import annotations

import io
import shutil
import tempfile

from flask import Blueprint, request, send_file
from flask_jwt_extended import jwt_required

from core import audit
from core.api_response import ok, register_api_errors
from extensions import db
from services.calling import campaign_report as svc
from services.calling.campaign import CAMPAIGNS_MANAGE
from services.calling.common import permission_required

bp = Blueprint("calling_campaign_report", __name__)
register_api_errors(bp)
URL_PREFIX = None


@bp.get("/v2/campaigns/<uuid:campaign_id>/report")
@jwt_required()
@permission_required(CAMPAIGNS_MANAGE)
def campaign_report(campaign_id):
    return ok(svc.report(campaign_id, request.args))


@bp.get("/v2/campaigns/<uuid:campaign_id>/report/export")
@jwt_required()
@permission_required(CAMPAIGNS_MANAGE)
def campaign_report_export(campaign_id):
    tmp = tempfile.mkdtemp(prefix="campaign-report-")
    try:
        path, name, count = svc.export(campaign_id, request.args, tmp)
        with open(path, "rb") as f:
            payload = f.read()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    audit.record("campaign.report.export", "campaign", str(campaign_id),
                 after={"payment_days": svc._days(request.args), "clients": count})
    db.session.commit()
    resp = send_file(io.BytesIO(payload), as_attachment=True, download_name=name, max_age=0,
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    resp.headers["Cache-Control"] = "private, no-store"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp
