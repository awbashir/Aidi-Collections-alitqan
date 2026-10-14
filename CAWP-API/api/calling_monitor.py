"""Call monitor (customer request 2026-10-13; services/calling/monitor.py, migration 0067).

  GET /v2/calling/monitor/live    live calls (calling service snapshot) + agent status     reports_calls | calls_manage
      ?extension=<ext>             optional user filter, only within the caller's scope (403 outside)
  GET /v2/calling/monitor/stats   calls by user over time from the PBX call records          reports_calls | calls_manage
      ?from=YYYY-MM-DD&to=YYYY-MM-DD&granularity=hour|day&extension=<ext>
                                     (Asia/Riyadh; default today by hour; 31 days max)

Scope is applied on the server: SuperAdmin sees every extension, a supervisor their team's
extensions and their own, anyone else their own extension.
"""
from __future__ import annotations

from flask import Blueprint, request
from flask_jwt_extended import jwt_required

from core.api_response import ok, register_api_errors
from services.calling import monitor as monitor_svc
from services.calling.common import CALLS_MANAGE, permission_required

bp = Blueprint("calling_monitor", __name__)
register_api_errors(bp)
URL_PREFIX = None
REPORTS_CALLS = "reports_calls"


@bp.get("/v2/calling/monitor/live")
@jwt_required()
@permission_required(REPORTS_CALLS, CALLS_MANAGE)
def monitor_live():
    return ok(monitor_svc.live(extension=request.args.get("extension")))


@bp.get("/v2/calling/monitor/stats")
@jwt_required()
@permission_required(REPORTS_CALLS, CALLS_MANAGE)
def monitor_stats():
    return ok(monitor_svc.stats(request.args))
