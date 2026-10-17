"""Other accounts of the same debtor on the case page (customer request 2026-10-17).

    GET /v2/cases/<case_id>/same-identity?include_archived=0|1

Authorized by the OPENED case: the caller needs a case-view key (`clints_details` or
`clints`, as /v2/cases/<id>/previous-records) and the case must be inside the caller's scope
(404 `case_not_found` otherwise, no existence leak). The other accounts of the same identity
are then listed read-only whatever their scope; each carries `can_open`. Service and money
rules: services/same_identity.py. Read-only, not audited.

    data = {case_id, identity, include_archived, truncated,
            summary: {count, amount, paid, remaining},
            accounts: [{id, account_number, name, contract{id,name}, portfolio{id,name},
                        amount, paid, remaining, is_archive,
                        last_note{status, sub_status, color, at} | null,
                        owner{id,name} | null, can_open}]}
Money is a string with 2 decimals; `paid` counts approved, non-deleted, non-reversed payments.
"""
from __future__ import annotations

from flask import Blueprint, request
from flask_jwt_extended import get_jwt_identity, jwt_required

from core.api_response import ApiError, ok, register_api_errors
from core.auth import has_permission
from extensions import db
from services import money
from services import same_identity as svc

bp = Blueprint("same_identity_v2", __name__)
register_api_errors(bp)
URL_PREFIX = None

CASE_VIEW_KEYS = ("clints_details", "clints")


@bp.get("/v2/cases/<uuid:case_id>/same-identity")
@jwt_required()
def same_identity(case_id):
    if not any(has_permission(k) for k in CASE_VIEW_KEYS):
        raise ApiError(403, "forbidden", "You are not authorized to access this resource",
                       {"required_any": list(CASE_VIEW_KEYS)})
    scope = money.caller_scope(str(get_jwt_identity()))
    money.require_case(case_id, scope)
    data = svc.same_identity(case_id, scope, svc.parse_flag(request.args.get("include_archived")))
    db.session.rollback()
    return ok(data)
