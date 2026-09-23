"""Phase 3 stream B: /v2 repayment plans (review/phase3-plans.md).

Conventions (core/api_response.py): real HTTP codes, `{message, status, data}` on success,
`{message, status:false, error, code}` on failure, never 401 for a business error. Money is a
string with exactly 2 decimals; civil dates are Asia/Riyadh `YYYY-MM-DD`; instants ISO 8601.

Mutating routes: `@idempotent()` (Idempotency-Key header required), a revision check on
cancel (`expected_revision` in the body or `If-Match`), and `audit_events` + `plan_events`
written in the same transaction. The schedule preview writes nothing and needs no key.

Permissions (403 `forbidden` without the key):
  plans         list, detail, case plans
  plans_manage  preview, create, cancel, allocate, deallocate (also enough to read)
Every read and write is limited to the caller's case scope; outside it the answer is 404.
"""
from __future__ import annotations

from flask import Blueprint, request
from flask_jwt_extended import get_jwt_identity, jwt_required

from core.api_response import created, json_body, ok, register_api_errors
from core.idempotency import idempotent
from core.revisions import expected_revision_from_request
from api.money import require_any
from services import money as msvc
from services import plans as svc
from services import plans_query as qsvc

bp = Blueprint("plans_v2", __name__)
register_api_errors(bp)
URL_PREFIX = None

READ = ("plans", "plans_manage")
MANAGE = ("plans_manage",)


def _me():
    return str(get_jwt_identity())


def _scope():
    return msvc.caller_scope(_me())


# --------------------------------------------------------------------------- reads
@bp.get("/v2/plans")
@jwt_required()
@require_any(*READ)
def plan_list():
    return ok(qsvc.list_plans(_scope(), request.args))


@bp.get("/v2/plans/<uuid:plan_id>")
@jwt_required()
@require_any(*READ)
def plan_detail(plan_id):
    plan = qsvc.get_plan(plan_id)
    msvc.require_case(plan["case_id"], _scope(), qsvc.plan_not_found(plan_id))
    return ok(plan)


@bp.get("/v2/cases/<uuid:case_id>/plans")
@jwt_required()
@require_any(*READ)
def case_plans(case_id):
    msvc.require_case(case_id, _scope())
    return ok(qsvc.case_plans(case_id))


# --------------------------------------------------------------------------- writes
@bp.post("/v2/cases/<uuid:case_id>/plans/preview")
@jwt_required()
@require_any(*MANAGE)
def preview(case_id):
    return ok(svc.preview_plan(case_id, _scope(), json_body()), "preview")


@bp.post("/v2/cases/<uuid:case_id>/plans")
@jwt_required()
@require_any(*MANAGE)
@idempotent()
def create_plan(case_id):
    return created(svc.create_plan(case_id, _me(), _scope(), json_body()), "plan created")


@bp.post("/v2/plans/<uuid:plan_id>/cancel")
@jwt_required()
@require_any(*MANAGE)
@idempotent()
def cancel_plan(plan_id):
    body = json_body()
    expected = expected_revision_from_request()
    reason = msvc.required_text(body, "reason")
    return ok(svc.cancel_plan(plan_id, _me(), _scope(), expected, reason), "cancelled")


@bp.post("/v2/plans/<uuid:plan_id>/allocations")
@jwt_required()
@require_any(*MANAGE)
@idempotent()
def allocate(plan_id):
    body = json_body()
    reason = msvc.required_text(body, "reason")
    return created(svc.allocate(plan_id, _me(), _scope(), body, reason), "allocated")


@bp.post("/v2/plan-allocations/<uuid:allocation_id>/deallocate")
@jwt_required()
@require_any(*MANAGE)
@idempotent()
def deallocate(allocation_id):
    body = json_body()
    reason = msvc.required_text(body, "reason")
    return ok(svc.deallocate(allocation_id, _me(), _scope(), reason), "deallocated")
