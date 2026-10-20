"""Home dashboard v2: interactive KPI cards and charts on /home (customer request 2026-10-20;
services/home_dashboard.py). The legacy /dashbord/* cards of api/dashboard.py stay unchanged.

Every route: dashbord_user | dashbord_admin. Scope is applied on the server (SuperAdmin every case,
a supervisor their team's and supervised contracts' cases, anyone else their own cases and notes).
Common query: contract_id=<uuid> (narrows to that contract), user_id=<uuid> (cases owned by /
notes written by that user, inside the scope), period=today|week|month (Asia/Riyadh).

  GET /v2/dashboards/home/summary     KPI cards with the previous period, trend % and a sparkline
      -> {scope, today, contract_id, user_id, period, from, to, previous: {from, to},
          options: {contracts: [{id, name, is_active}]}, spark_days: [YYYY-MM-DD],
          kpis: {collected: {amount, count, previous_amount, trend, spark},
                 pending: {amount, count},
                 promises_due: {count, amount, by_state: {open|overdue|fulfilled: {count, amount}},
                                previous_count, trend, spark},
                 notes: {count, clients, previous_count, trend, spark},
                 cases: {open, debt, not_contacted_7d},
                 target: {collectors, targets_unknown, target, achieved, percent, expected_to_date,
                          remaining, work_days, work_days_passed, month}},
          by_contract: [{contract_id, name, count, amount}]}          (contract filter not applied)
  GET /v2/dashboards/home/charts      collections this month by day (+ cumulative, target), promises
                                      by due day (7 days back, 14 ahead) by state, today's notes by
                                      hour and main status, notes weekday x hour (28 days)
  GET /v2/dashboards/home/collectors  leaderboard: target / achieved / % this month, the period's
                                      collected, notes and promises due, kept rate (30 days), open
                                      cases, neglect (7/14/30 days, never), first-contact rate
  GET /v2/dashboards/home/portfolio   open cases: recovery per contract and portfolio, outstanding
                                      by case age and amount, latest-note reachability, neglect,
                                      first-contact rate
  GET /v2/dashboards/home/calls       today's calls by hour from the call monitor statistics (same
                                      scope; a supervisor without a calls-reporting key: own only)
                                      -> {available, error, scope, date, truncated, totals|null, hours|null}

Money is exact decimal strings; pending, rejected and reversed payments never count as collected.
Responses are cached per caller and query for CACHE_TTL seconds (`?nocache=1` bypasses).
"""
from __future__ import annotations

from flask import Blueprint, request
from flask_jwt_extended import jwt_required

from core import cache
from core.api_response import ok, register_api_errors
from services import home_dashboard as svc
from services.calling.common import me, permission_required

bp = Blueprint("home_dashboard", __name__)
register_api_errors(bp)
URL_PREFIX = None
KEYS = ("dashbord_user", "dashbord_admin")
CACHE_TTL = {"summary": 60, "charts": 120, "collectors": 120, "portfolio": 300, "calls": 60}
_ARGS = ("period", "contract_id", "user_id")


def _cached(name, fn):
    from core import riyadh
    parts = [f"{k}={request.args.get(k) or ''}" for k in _ARGS]
    key = f"home:v2:{name}:{me()}:{riyadh.today().isoformat()}:{'&'.join(parts)}"
    if request.args.get("nocache") != "1":
        hit = cache.get(key)
        if hit is not None:
            return ok(hit)
    data = fn(request.args)
    cache.set(key, data, CACHE_TTL[name])
    return ok(data)


@bp.get("/v2/dashboards/home/summary")
@jwt_required()
@permission_required(*KEYS)
def home_summary():
    return _cached("summary", svc.summary)


@bp.get("/v2/dashboards/home/charts")
@jwt_required()
@permission_required(*KEYS)
def home_charts():
    return _cached("charts", svc.charts)


@bp.get("/v2/dashboards/home/collectors")
@jwt_required()
@permission_required(*KEYS)
def home_collectors():
    return _cached("collectors", svc.collectors)


@bp.get("/v2/dashboards/home/portfolio")
@jwt_required()
@permission_required(*KEYS)
def home_portfolio():
    return _cached("portfolio", svc.portfolio)


@bp.get("/v2/dashboards/home/calls")
@jwt_required()
@permission_required(*KEYS)
def home_calls():
    return _cached("calls", svc.calls)
