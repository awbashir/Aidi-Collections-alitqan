"""Call monitor (customer request 2026-10-13; services/calling/monitor.py, migration 0067).

  GET /v2/calling/monitor/live    live calls (calling service snapshot) + agent status     reports_calls | calls_manage
      ?extension=<ext>             optional user filter, only within the caller's scope (403 outside)
  GET /v2/calling/monitor/stats   calls by user over time from the PBX call records          reports_calls | calls_manage
      ?from=YYYY-MM-DD&to=YYYY-MM-DD&granularity=hour|day&extension=<ext>
                                     (Asia/Riyadh; default today by hour; 31 days max)
  GET /v2/calling/monitor/stats/export   XLSX: the per-user table + totals, calls over time   same keys
  GET /v2/calling/monitor/calls/export   XLSX: one row per call (time, number, client, line,  same keys
                                         result, ring/talk seconds, recorded)
      both take the stats query (from, to, extension); every download writes an audit event

Scope is applied on the server: SuperAdmin sees every extension, a supervisor their team's
extensions and their own, anyone else their own extension.
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


def _download(build, kind: str):
    args = request.args.to_dict()
    tmp = tempfile.mkdtemp(prefix="call-monitor-")
    try:
        path, name, count = build(args, tmp)
        with open(path, "rb") as f:
            payload = f.read()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    audit.record("call_monitor.export", "call_monitor", None,
                 after={"kind": kind, "from": args.get("from"), "to": args.get("to"),
                        "extension": args.get("extension") or None, "rows": count})
    db.session.commit()
    resp = send_file(io.BytesIO(payload), as_attachment=True, download_name=name, max_age=0,
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    resp.headers["Cache-Control"] = "private, no-store"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


@bp.get("/v2/calling/monitor/stats/export")
@jwt_required()
@permission_required(REPORTS_CALLS, CALLS_MANAGE)
def monitor_stats_export():
    return _download(monitor_svc.export_summary, "summary")


@bp.get("/v2/calling/monitor/calls/export")
@jwt_required()
@permission_required(REPORTS_CALLS, CALLS_MANAGE)
def monitor_calls_export():
    return _download(monitor_svc.export_calls, "calls")
