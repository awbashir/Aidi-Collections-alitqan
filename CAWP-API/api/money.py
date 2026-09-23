"""Phase 1 stream A: /v2 payments, reversals and promises (review/phase1-money.md).

Conventions (core/api_response.py): real HTTP codes, `{message, status, data}` on success,
`{message, status:false, error, code}` on failure, never 401 for a business error.
Money is always a string with exactly 2 decimals ("1234.50"); dates are ISO 8601; civil
dates are Asia/Riyadh `YYYY-MM-DD`.

Mutating routes: `@idempotent()` (Idempotency-Key header required), a revision check
(`expected_revision` in the body or `If-Match`) where an existing row is edited, and
`audit_events` + payment/promise events written in the same transaction.

Permissions (403 `forbidden` without the key):
  payments_approve   approve / reject, approval desk
  payments_reverse   reverse
  promises           promise list / detail / create
  promises_cancel    cancel a promise
  promises_allocate  allocate / deallocate
Case money reads (balance, payment history) accept the legacy case/payment view keys too.
Every read and write is limited to the caller's case scope (services/clients_scope.py);
a case outside it answers 404.
"""
from __future__ import annotations

from functools import wraps

from flask import Blueprint, request
from flask_jwt_extended import get_jwt_identity, jwt_required

from core.api_response import created, forbidden, json_body, ok, register_api_errors
from core.auth import has_permission
from core.idempotency import idempotent
from core.revisions import expected_revision_from_request
from services import money as svc
from services import money_promises as psvc

bp = Blueprint("money_v2", __name__)
register_api_errors(bp)
URL_PREFIX = None

CASE_MONEY_READ = ("clints_Payments", "accounts_payments", "payments_approve", "payments_reverse")
DESK_READ = ("payments_approve", "payments_reverse", "accounts_payments")
PROMISE_READ = ("promises", "clints_PayemntDates")


def require_any(*keys):
    """403 unless the caller's role grants at least one of `keys`. Below @jwt_required()."""
    def deco(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if not any(has_permission(k) for k in keys):
                return forbidden("You are not authorized to access this resource",
                                 {"required_any": list(keys)})
            return fn(*args, **kwargs)
        return wrapper
    return deco


def _me():
    return str(get_jwt_identity())


def _scope():
    return svc.caller_scope(_me())


# --------------------------------------------------------------------------- payments
@bp.get("/v2/cases/<uuid:case_id>/balance")
@jwt_required()
@require_any(*CASE_MONEY_READ)
def case_balance(case_id):
    svc.require_case(case_id, _scope())
    return ok(svc.case_balance(case_id))


@bp.get("/v2/cases/<uuid:case_id>/payments")
@jwt_required()
@require_any(*CASE_MONEY_READ)
def case_payments(case_id):
    svc.require_case(case_id, _scope())
    return ok(svc.case_payments(case_id))


@bp.get("/v2/payments")
@jwt_required()
@require_any(*DESK_READ)
def approval_desk():
    return ok(svc.approval_desk(_scope(), request.args))


@bp.get("/v2/payments/<uuid:payment_id>")
@jwt_required()
@require_any(*CASE_MONEY_READ)
def payment_detail(payment_id):
    payment = svc.get_payment(payment_id)
    svc.require_case(payment["case_id"], _scope(), svc.payment_not_found(payment_id))
    return ok(payment)


@bp.post("/v2/payments/<uuid:payment_id>/approve")
@jwt_required()
@require_any("payments_approve")
@idempotent()
def approve(payment_id):
    body = json_body(required=False)
    expected = expected_revision_from_request()
    comment = svc.optional_text(body, "comment")
    return ok(svc.approve_payment(payment_id, _me(), _scope(), expected, comment), "approved")


@bp.post("/v2/payments/<uuid:payment_id>/reject")
@jwt_required()
@require_any("payments_approve")
@idempotent()
def reject(payment_id):
    body = json_body()
    expected = expected_revision_from_request()
    reason = svc.required_text(body, "reason")
    return ok(svc.reject_payment(payment_id, _me(), _scope(), expected, reason), "rejected")


@bp.post("/v2/payments/<uuid:payment_id>/reverse")
@jwt_required()
@require_any("payments_reverse")
@idempotent()
def reverse(payment_id):
    body = json_body()
    expected = expected_revision_from_request()
    reason = svc.required_text(body, "reason")
    return ok(svc.reverse_payment(payment_id, _me(), _scope(), expected, reason), "reversed")


# --------------------------------------------------------------------------- promises
@bp.get("/v2/promises")
@jwt_required()
@require_any("promises")
def promise_list():
    return ok(psvc.list_promises(_scope(), request.args))


@bp.get("/v2/promises/<uuid:promise_id>")
@jwt_required()
@require_any(*PROMISE_READ)
def promise_detail(promise_id):
    promise = psvc.get_promise(promise_id)
    svc.require_case(promise["case_id"], _scope(), psvc.promise_not_found(promise_id))
    return ok(promise)


@bp.get("/v2/cases/<uuid:case_id>/promises")
@jwt_required()
@require_any(*PROMISE_READ)
def case_promises(case_id):
    svc.require_case(case_id, _scope())
    return ok(psvc.case_promises(case_id))


@bp.post("/v2/cases/<uuid:case_id>/promises")
@jwt_required()
@require_any("promises")
@idempotent()
def create_promise(case_id):
    body = json_body()
    return created(psvc.create_promise(case_id, _me(), _scope(), body), "promise created")


@bp.post("/v2/promises/<uuid:promise_id>/cancel")
@jwt_required()
@require_any("promises_cancel")
@idempotent()
def cancel_promise(promise_id):
    body = json_body()
    expected = expected_revision_from_request()
    reason = svc.required_text(body, "reason")
    return ok(psvc.cancel_promise(promise_id, _me(), _scope(), expected, reason), "cancelled")


@bp.post("/v2/promises/<uuid:promise_id>/allocations")
@jwt_required()
@require_any("promises_allocate")
@idempotent()
def allocate(promise_id):
    body = json_body()
    return created(psvc.allocate(promise_id, _me(), _scope(), body), "allocated")


@bp.post("/v2/promise-allocations/<uuid:allocation_id>/deallocate")
@jwt_required()
@require_any("promises_allocate")
@idempotent()
def deallocate(allocation_id):
    body = json_body()
    reason = svc.required_text(body, "reason")
    return ok(psvc.deallocate(allocation_id, _me(), _scope(), reason), "deallocated")
