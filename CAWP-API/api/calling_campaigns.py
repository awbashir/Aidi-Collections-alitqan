"""Phase 5 P2: progressive calling campaigns. review/phase5-campaigns.md has request/response
examples. To register: add "api.calling_campaigns" to api/__init__.py MODULES (the lead).

Conventions (core/api_response.py): real HTTP codes, `{message, status, data}`; mutations need
an `Idempotency-Key` (@idempotent, which also commits) except the heartbeat, where it is
optional (a lease renewal is naturally idempotent and runs every few seconds); row edits need
`expected_revision` (body) or `If-Match` (the campaign's revision). Every write is audited.
Invisible campaigns answer 404.

Campaigns                                                            permission
  GET  /v2/campaigns                       list (status=, mine=1)      campaigns | campaigns_manage
  POST /v2/campaigns                       create (draft)              campaigns_manage
  GET  /v2/campaigns/pool-scope            contracts my scope reaches  campaigns_manage
                                           (the note-status pickers of the campaign form)
  GET  /v2/campaigns/<id>                  detail                      campaigns | campaigns_manage
  PATCH /v2/campaigns/<id>                 name / pool / settings      campaigns_manage
  POST /v2/campaigns/<id>/start            draft|paused -> running     campaigns_manage
  POST /v2/campaigns/<id>/pause            running -> paused           campaigns_manage
  POST /v2/campaigns/<id>/drain            -> draining -> stopped      campaigns_manage
  POST /v2/campaigns/<id>/stop             -> stopped                  campaigns_manage
Roster
  GET  /v2/campaigns/<id>/members                                      campaigns_manage
  POST /v2/campaigns/<id>/members          add / reactivate            campaigns_manage
  POST /v2/campaigns/<id>/members/<user_id>/deactivate                 campaigns_manage
Members (the collector's side)
  GET  /v2/campaigns/<id>/me               my status                   campaigns | campaigns_manage
  POST /v2/campaigns/<id>/heartbeat        lease (+ state)             campaigns | campaigns_manage
  POST /v2/campaigns/<id>/next             "next call"                 campaigns | campaigns_manage
Supervisor
  GET  /v2/campaigns/<id>/live             live view                   campaigns_manage
  GET  /v2/campaigns/<id>/pool             pool in dispatch order      campaigns_manage

No route talks to the PBX: a campaign call is authorized by the same code as POST /v2/calls and
sent by the `calling` service.
"""
from __future__ import annotations

from flask import Blueprint, request
from flask_jwt_extended import jwt_required

from core.api_response import created, json_body, ok, register_api_errors
from core.idempotency import idempotent
from core.revisions import expected_revision_from_request
from services.calling import campaign as svc
from services.calling.campaign import CAMPAIGNS, CAMPAIGNS_MANAGE
from services.calling.common import page_args, permission_required

bp = Blueprint("calling_campaigns", __name__)
register_api_errors(bp)
URL_PREFIX = None


@bp.get("/v2/campaigns")
@jwt_required()
@permission_required(CAMPAIGNS, CAMPAIGNS_MANAGE)
def list_campaigns():
    page, size, offset = page_args()
    return ok(svc.list_campaigns(request.args, page, size, offset))


@bp.post("/v2/campaigns")
@jwt_required()
@permission_required(CAMPAIGNS_MANAGE)
@idempotent()
def create_campaign():
    cid = svc.create(json_body())
    return created(svc.detail(cid), "campaign created")


@bp.get("/v2/campaigns/pool-scope")
@jwt_required()
@permission_required(CAMPAIGNS_MANAGE)
def pool_scope():
    return ok(svc.pool_scope())


@bp.get("/v2/campaigns/<uuid:campaign_id>")
@jwt_required()
@permission_required(CAMPAIGNS, CAMPAIGNS_MANAGE)
def campaign_detail(campaign_id):
    return ok(svc.detail(campaign_id))


@bp.patch("/v2/campaigns/<uuid:campaign_id>")
@jwt_required()
@permission_required(CAMPAIGNS_MANAGE)
@idempotent()
def update_campaign(campaign_id):
    body = json_body()
    svc.update(campaign_id, body, expected_revision_from_request())
    return ok(svc.detail(campaign_id), "campaign updated")


def _transition(campaign_id, action, message):
    body = json_body(required=False) or {}
    out = svc.transition(campaign_id, action, body, expected_revision_from_request())
    return ok({**svc.detail(campaign_id), **out}, message)


@bp.post("/v2/campaigns/<uuid:campaign_id>/start")
@jwt_required()
@permission_required(CAMPAIGNS_MANAGE)
@idempotent()
def start_campaign(campaign_id):
    return _transition(campaign_id, "start", "campaign running")


@bp.post("/v2/campaigns/<uuid:campaign_id>/pause")
@jwt_required()
@permission_required(CAMPAIGNS_MANAGE)
@idempotent()
def pause_campaign(campaign_id):
    return _transition(campaign_id, "pause", "campaign paused; calls in progress continue")


@bp.post("/v2/campaigns/<uuid:campaign_id>/drain")
@jwt_required()
@permission_required(CAMPAIGNS_MANAGE)
@idempotent()
def drain_campaign(campaign_id):
    return _transition(campaign_id, "drain", "campaign draining; it stops after the current calls and wrap-ups")


@bp.post("/v2/campaigns/<uuid:campaign_id>/stop")
@jwt_required()
@permission_required(CAMPAIGNS_MANAGE)
@idempotent()
def stop_campaign(campaign_id):
    return _transition(campaign_id, "stop", "campaign stopped; calls in progress continue")


@bp.get("/v2/campaigns/<uuid:campaign_id>/members")
@jwt_required()
@permission_required(CAMPAIGNS_MANAGE)
def campaign_members(campaign_id):
    return ok(svc.roster(campaign_id))


@bp.post("/v2/campaigns/<uuid:campaign_id>/members")
@jwt_required()
@permission_required(CAMPAIGNS_MANAGE)
@idempotent()
def add_members(campaign_id):
    out = svc.add_members(campaign_id, json_body(), expected_revision_from_request())
    return ok({**svc.roster(campaign_id), **out, "revision": svc.detail(campaign_id)["revision"]}, "members added")


@bp.post("/v2/campaigns/<uuid:campaign_id>/members/<uuid:user_id>/deactivate")
@jwt_required()
@permission_required(CAMPAIGNS_MANAGE)
@idempotent()
def deactivate_member(campaign_id, user_id):
    out = svc.deactivate_member(campaign_id, user_id, json_body(), expected_revision_from_request())
    return ok({**svc.roster(campaign_id), **out, "revision": svc.detail(campaign_id)["revision"]},
              "member deactivated")


@bp.get("/v2/campaigns/<uuid:campaign_id>/me")
@jwt_required()
@permission_required(CAMPAIGNS, CAMPAIGNS_MANAGE)
def my_status(campaign_id):
    return ok(svc.my_status(campaign_id))


@bp.post("/v2/campaigns/<uuid:campaign_id>/heartbeat")
@jwt_required()
@permission_required(CAMPAIGNS, CAMPAIGNS_MANAGE)
@idempotent(required=False)
def heartbeat(campaign_id):
    out = svc.heartbeat(campaign_id, json_body(required=False) or {})
    if not request.headers.get("Idempotency-Key"):
        from extensions import db
        db.session.commit()                  # @idempotent commits only when a key was sent
    return ok(out, "heartbeat")


@bp.post("/v2/campaigns/<uuid:campaign_id>/next")
@jwt_required()
@permission_required(CAMPAIGNS, CAMPAIGNS_MANAGE)
@idempotent()
def next_call(campaign_id):
    return ok(svc.request_next(campaign_id, json_body(required=False) or {}), "next call requested")


@bp.get("/v2/campaigns/<uuid:campaign_id>/live")
@jwt_required()
@permission_required(CAMPAIGNS_MANAGE)
def live_view(campaign_id):
    return ok(svc.live_view(campaign_id))


@bp.get("/v2/campaigns/<uuid:campaign_id>/pool")
@jwt_required()
@permission_required(CAMPAIGNS_MANAGE)
def pool_view(campaign_id):
    page, size, offset = page_args()
    return ok(svc.pool_view(campaign_id, request.args, page, size, offset))
