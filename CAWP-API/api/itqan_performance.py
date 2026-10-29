"""Alitqan performance board ("لوحة أداء فنية الإتقان", customer request 2026-10-29;
services/itqan_performance.py), beside the team dashboard.

  GET /v2/dashboards/itqan        the collector and supervisor scorecards       calls_supervise | calls_manage
      ?month=YYYY-MM                  default the current month (Asia/Riyadh); no future month
      &contract_id=<uuid>             the collectors of that contract
      -> {scope, today, contract_id, month, closed, work_days, work_days_passed,
          weights: {collector|supervisor: {original, effective, dropped}}, params,
          totals, collectors: [...], supervisors: [...], portfolios: [...],
          trend: {months, total: [{month, target, collected, achievement}], collectors: {id: [rate|null]}},
          options: {contracts}}

Scope is the home dashboard's (SuperAdmin everyone, a supervisor their team, anyone else
themselves). Money is exact decimal strings; pending, rejected and reversed payments never count.
Cached per caller and query for CACHE_TTL seconds (`?nocache=1` bypasses).
"""
from __future__ import annotations

from flask import Blueprint, request
from flask_jwt_extended import jwt_required

from core import cache
from core.api_response import ok, register_api_errors
from services import itqan_performance as svc
from services.calling.common import CALLS_MANAGE, CALLS_SUPERVISE, me, permission_required

bp = Blueprint("itqan_performance", __name__)
register_api_errors(bp)
URL_PREFIX = None
CACHE_TTL = 300


@bp.get("/v2/dashboards/itqan")
@jwt_required()
@permission_required(CALLS_SUPERVISE, CALLS_MANAGE)
def itqan_board():
    from core import riyadh
    key = (f"itqan:v1:{me()}:{riyadh.today().isoformat()}:{request.args.get('month') or ''}:"
           f"{request.args.get('contract_id') or ''}")
    if request.args.get("nocache") != "1":
        hit = cache.get(key)
        if hit is not None:
            return ok(hit)
    data = svc.board(request.args)
    cache.set(key, data, CACHE_TTL)
    return ok(data)
