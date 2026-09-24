"""Phase 1, stream B: follow-ups and the daily attention queue (/v2, new response convention).

    GET  /v2/follow-ups                               follow_ups
    GET  /v2/cases/<case_id>/follow-ups               follow_ups
    POST /v2/cases/<case_id>/follow-ups               follow_ups (+ follow_ups_manage for others' cases)
    POST /v2/follow-ups/<id>/complete                 follow_ups (+ follow_ups_manage for others')
    POST /v2/follow-ups/<id>/reschedule               follow_ups (+ follow_ups_manage for others')
    GET  /v2/attention                                attention_queue

Every list and count is limited to the caller's scope (services/clients_scope.py:
SuperAdmin / supervisor / collector) and to live (not archived) cases. POSTs need an
`Idempotency-Key` header; complete/reschedule need `expected_revision` (body) or `If-Match`.
Request/response examples: review/phase1-worklist.md.
"""
from __future__ import annotations

from flask import Blueprint, request
from flask_jwt_extended import get_jwt_identity, jwt_required

from core.api_response import created, json_body, ok, register_api_errors
from core.auth import has_permission, required_roles
from core.idempotency import idempotent
from core.revisions import expected_revision_from_request
from extensions import db
from services import worklist_attention, worklist_common as wc, worklist_follow_ups as fus
from services.clients_scope import resolve_scope

bp = Blueprint("worklist_v2", __name__)
register_api_errors(bp)

PERM_VIEW = "follow_ups"
PERM_MANAGE = "follow_ups_manage"
PERM_QUEUE = "attention_queue"


def _scope():
    return resolve_scope(get_jwt_identity())


def _request_id():
    return request.headers.get("X-Request-ID")


@bp.get("/v2/follow-ups")
@jwt_required()
@required_roles(PERM_VIEW)
def list_follow_ups():
    page, size = wc.paging(request.args)
    flt = wc.CaseFilter.from_args(_scope(), request.args)
    bucket = request.args.get("bucket") or None
    data = fus.list_follow_ups(db.session, flt, bucket=bucket, page=page, page_size=size)
    db.session.rollback()      # read-only: end the transaction, release snapshot
    return ok(data)


@bp.get("/v2/cases/<uuid:case_id>/follow-ups")
@jwt_required()
@required_roles(PERM_VIEW)
def case_follow_ups(case_id):
    data = fus.case_follow_ups(db.session, _scope(), case_id)
    db.session.rollback()
    return ok(data)


@bp.post("/v2/cases/<uuid:case_id>/follow-ups")
@jwt_required()
@required_roles(PERM_VIEW)
@idempotent()
def create_follow_up(case_id):
    body = json_body()
    data = fus.create(db.session, _scope(), get_jwt_identity(), case_id, body,
                      can_manage=has_permission(PERM_MANAGE), request_id=_request_id())
    return created(data, "follow-up created")


@bp.post("/v2/follow-ups/<uuid:follow_up_id>/complete")
@jwt_required()
@required_roles(PERM_VIEW)
@idempotent()
def complete_follow_up(follow_up_id):
    body = json_body()
    expected = expected_revision_from_request()
    data = fus.complete(db.session, _scope(), get_jwt_identity(), follow_up_id, body, expected,
                        can_manage=has_permission(PERM_MANAGE), request_id=_request_id())
    return ok(data, "follow-up completed")


@bp.post("/v2/follow-ups/<uuid:follow_up_id>/reschedule")
@jwt_required()
@required_roles(PERM_VIEW)
@idempotent()
def reschedule_follow_up(follow_up_id):
    body = json_body()
    expected = expected_revision_from_request()
    data = fus.reschedule(db.session, _scope(), get_jwt_identity(), follow_up_id, body, expected,
                          can_manage=has_permission(PERM_MANAGE), request_id=_request_id())
    return ok(data, "follow-up rescheduled")


@bp.get("/v2/attention")
@jwt_required()
@required_roles(PERM_QUEUE)
def attention():
    page, size = wc.paging(request.args)
    flt = wc.CaseFilter.from_args(_scope(), request.args)
    from core.auth import has_permission
    from services import requests_attention
    data = worklist_attention.attention(db.session, flt, reason=request.args.get("reason") or None,
                                        page=page, page_size=size,
                                        departments=requests_attention.reviewer_departments(has_permission))
    db.session.rollback()
    return ok(data)
