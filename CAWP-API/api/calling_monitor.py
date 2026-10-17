"""Call monitor (customer request 2026-10-13; services/calling/monitor.py, migration 0067).

  GET /v2/calling/monitor/live    live calls (calling service snapshot) + agent status     reports_calls | calls_manage
      ?extension=<ext>             optional user filter, only within the caller's scope (403 outside)
  GET /v2/calling/monitor/stats   calls by user over time from the PBX call records          reports_calls | calls_manage
      ?from=YYYY-MM-DD&to=YYYY-MM-DD&granularity=hour|day&extension=<ext>
                                     (Asia/Riyadh; default today by hour; 31 days max)
      users[] and totals also carry talk_over_60 / talk_over_120 / talk_over_180: answered calls
      with talk time strictly longer than 1 / 2 / 3 minutes
  GET /v2/calling/monitor/calls   one user's (or the scope's) calls of the period, newest first  same keys
      ?from&to&extension&page=1&page_size=50 (max 200)
      -> {scope, extension, from, to, truncated, items: [{at (ISO, +03:00), extension, user_id, name,
          direction, answered, result, number, line, client: {name, account_number}|null,
          ring_seconds, talk_seconds, recorded, recording (CDR recordingfile; null without a
          recording key), call_id}], total, page, page_size}
      The recording is played/downloaded through the legacy GET /cdr/recording?recordingPath=, which
      authorizes it again (services/calling_cdr.scoped_recording) under clints_callsRecords_*.
  GET /v2/calling/monitor/stats/export   XLSX: the per-user table + totals, calls over time   same keys
  GET /v2/calling/monitor/calls/export   XLSX: one row per call (time, number, client, line,  same keys
                                         result, ring/talk seconds, talk > 1/2/3 min, recorded)
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
from core.auth import has_permission
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


@bp.get("/v2/calling/monitor/calls")
@jwt_required()
@permission_required(REPORTS_CALLS, CALLS_MANAGE)
def monitor_calls():
    data = monitor_svc.calls_page(request.args)
    from services.calling_cdr import RECORDING_KEYS
    if not any(has_permission(k) for k in RECORDING_KEYS):
        for c in data["items"]:
            c["recording"] = None
    return ok(data)


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
