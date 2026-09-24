"""Phase 3, stream A: internal requests (/v2, new response convention).

    GET  /v2/requests                         requests | requests_legal | requests_finance | requests_operations
    GET  /v2/requests/<id>                    any of the four
    GET  /v2/cases/<case_id>/requests         any of the four
    POST /v2/cases/<case_id>/requests         requests
    POST /v2/requests/<id>/comments           any of the four (anyone who can see it)
    POST /v2/requests/<id>/take               requests_<department>, in scope, not own
    POST /v2/requests/<id>/resolve            requests_<department>, in scope, not own   (reason)
    POST /v2/requests/<id>/reopen             requester (requests) or department reviewer (reason)
    POST /v2/requests/<id>/cancel             requester (requests), while open           (reason)

Visibility (services/requests_common.visibility): the requester sees their own requests; a
department reviewer sees that department's requests on cases in their scope; supervisors
(with `requests`) see their team's requests through scope; SuperAdmin sees all. Anything
the caller cannot see is a 404. POSTs need `Idempotency-Key`; take / resolve / reopen /
cancel need `expected_revision` (body) or `If-Match`.
Request/response examples: review/phase3-requests.md.
"""
from __future__ import annotations

from functools import wraps

from flask import Blueprint, g, request
from flask_jwt_extended import get_jwt_identity, jwt_required

from core.api_response import created, json_body, ok, register_api_errors
from core.auth import has_permission
from core.idempotency import idempotent
from core.revisions import expected_revision_from_request
from extensions import db
from services import requests_common as rc, requests_service as svc
from services import worklist_common as wc
from services.clients_scope import resolve_scope

bp = Blueprint("requests_v2", __name__)
register_api_errors(bp)


def _request_ref():
    return request.headers.get("X-Request-ID")


def gate(*keys):
    """Resolve the caller (`g.req_viewer`) and require any of `keys`. v2 403 body:
    {"code": "forbidden", "error": {"required_any": [...]}}. Place below @jwt_required()."""
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            identity = get_jwt_identity()
            viewer = rc.Viewer.from_request(identity, resolve_scope(identity), has_permission)
            rc.require_any(viewer, keys)
            g.req_viewer = viewer
            return fn(*args, **kwargs)
        return wrapper
    return decorator


@bp.get("/v2/requests")
@jwt_required()
@gate(*rc.ALL_KEYS)
def list_requests():
    page, size = wc.paging(request.args)
    flt = svc.ListFilter.from_args(g.req_viewer, request.args)
    data = svc.list_requests(db.session, g.req_viewer, flt, page=page, page_size=size)
    db.session.rollback()      # read-only: end the transaction
    return ok(data)


@bp.get("/v2/requests/<uuid:request_id>")
@jwt_required()
@gate(*rc.ALL_KEYS)
def get_request(request_id):
    data = svc.detail(db.session, g.req_viewer, request_id)
    db.session.rollback()
    return ok(data)


@bp.get("/v2/cases/<uuid:case_id>/requests")
@jwt_required()
@gate(*rc.ALL_KEYS)
def case_requests(case_id):
    page, size = wc.paging(request.args)
    data = svc.case_requests(db.session, g.req_viewer, case_id, request.args, page=page, page_size=size)
    db.session.rollback()
    return ok(data)


@bp.post("/v2/cases/<uuid:case_id>/requests")
@jwt_required()
@gate(rc.PERM_BASE)
@idempotent()
def create_request(case_id):
    data = svc.create(db.session, g.req_viewer, case_id, json_body(), request_ref=_request_ref())
    return created(data, "request created")


@bp.post("/v2/requests/<uuid:request_id>/comments")
@jwt_required()
@gate(*rc.ALL_KEYS)
@idempotent()
def comment_request(request_id):
    data = svc.comment(db.session, g.req_viewer, request_id, json_body(), request_ref=_request_ref())
    return created(data, "comment added")


def _transition(request_id, action, message):
    body = json_body()
    expected = expected_revision_from_request()
    data = svc.transition(db.session, g.req_viewer, request_id, action, body, expected,
                          request_ref=_request_ref())
    return ok(data, message)


@bp.post("/v2/requests/<uuid:request_id>/take")
@jwt_required()
@gate(*rc.REVIEW_KEYS)
@idempotent()
def take_request(request_id):
    return _transition(request_id, "take", "request taken into review")


@bp.post("/v2/requests/<uuid:request_id>/resolve")
@jwt_required()
@gate(*rc.REVIEW_KEYS)
@idempotent()
def resolve_request(request_id):
    return _transition(request_id, "resolve", "request resolved")


@bp.post("/v2/requests/<uuid:request_id>/reopen")
@jwt_required()
@gate(*rc.ALL_KEYS)
@idempotent()
def reopen_request(request_id):
    return _transition(request_id, "reopen", "request reopened")


@bp.post("/v2/requests/<uuid:request_id>/cancel")
@jwt_required()
@gate(rc.PERM_BASE)
@idempotent()
def cancel_request(request_id):
    return _transition(request_id, "cancel", "request cancelled")
