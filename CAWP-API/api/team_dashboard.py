"""Team performance dashboard (customer request 2026-10-20; services/team_dashboard.py).

  GET /v2/dashboards/team          calls and client status updates per user        calls_supervise | calls_manage
      ?from=YYYY-MM-DD&to=YYYY-MM-DD   Asia/Riyadh, default today, 31 days max
      &granularity=hour|day            default hour for one day, else day
      &contract_id=<uuid>              narrows the notes and promises (not the calls: a PBX record has no contract)
      &user_id=<uuid>[&user_id=...]    or comma-separated; only users in the caller's scope (403 not_in_scope)
      &group=status|sub_status         status updates by main status (default) or by sub-status
      -> {scope, from, to, granularity, group, contract_id, user_ids, max_days,
          options: {users: [{id, name, is_active, extensions}], contracts: [{id, name, is_active}]},
          calls: {available, error, truncated, contract_filtered},
          totals: {calls_made, answered, not_answered, inbound_answered, inbound_missed, internal,
                   talk_seconds, handled, answer_rate, notes, clients, promises, promises_amount},
          users: [{user_id, name, is_active, extensions, <the call fields>, answer_rate, notes, clients,
                   by_status: {key: n}, top_status: {id, name, color, count}|null, promises, promises_amount}],
          statuses: [{key, id, name, color, parent_id, parent_name, no_sub, count}],
          series: [{bucket, total, by_status: {key: n}}]}
      The call fields are null when the PBX call records are unavailable (calls.available false).
  GET /v2/dashboards/team/export   XLSX of the same query; writes an audit event       same keys

Scope is applied on the server: SuperAdmin every user, a supervisor their team (and only notes
on cases in their scope), anyone else themselves.
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
from services import team_dashboard as svc
from services.calling.common import CALLS_MANAGE, CALLS_SUPERVISE, permission_required

bp = Blueprint("team_dashboard", __name__)
register_api_errors(bp)
URL_PREFIX = None


@bp.get("/v2/dashboards/team")
@jwt_required()
@permission_required(CALLS_SUPERVISE, CALLS_MANAGE)
def team_dashboard():
    return ok(svc.dashboard(request.args))


@bp.get("/v2/dashboards/team/export")
@jwt_required()
@permission_required(CALLS_SUPERVISE, CALLS_MANAGE)
def team_dashboard_export():
    tmp = tempfile.mkdtemp(prefix="team-dashboard-")
    try:
        path, name, count = svc.export(request.args, tmp)
        with open(path, "rb") as f:
            payload = f.read()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    audit.record("team_dashboard.export", "team_dashboard", None,
                 after={"from": request.args.get("from"), "to": request.args.get("to"),
                        "contract_id": request.args.get("contract_id") or None,
                        "user_ids": svc._list_arg(request.args, "user_id") or None,
                        "group": request.args.get("group") or "status", "rows": count})
    db.session.commit()
    resp = send_file(io.BytesIO(payload), as_attachment=True, download_name=name, max_age=0,
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    resp.headers["Cache-Control"] = "private, no-store"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp
