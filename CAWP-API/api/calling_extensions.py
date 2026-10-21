"""Extensions management ("التحويلات"; customer request 2026-10-20, migration 0077): the agents'
extensions on the PBX (create, rename, delete, retry) next to their app ownership.
services/calling/extension_provisioning.py has the rules; assign / release stay on the existing
/v2/calling/extensions/<ext>/assign|release routes (api/calling.py).

                                                                                permission
  GET  /v2/calling/pbx-extensions                  merged list (?view=all|unassigned|assigned|attention, ?q=)   calls_manage
  GET  /v2/calling/pbx-extensions/users-without    active users without an extension (?q=)                      calls_manage
  GET  /v2/calling/pbx-extensions/settings         auto-provisioning + range                                    calls_manage
  PUT  /v2/calling/pbx-extensions/settings         {auto_provision, range_from, range_to, expected_revision, reason}
  POST /v2/calling/pbx-extensions                  create {extension?, name?, user_id?, reason}                 calls_manage
  GET  /v2/calling/pbx-extensions/<ext>/requests   provisioning history of one number                           calls_manage
  POST /v2/calling/pbx-extensions/<ext>/retry      retry a pending / failed request {reason?}                   calls_manage
  POST /v2/calling/pbx-extensions/<ext>/rename     {name, reason}                                               calls_manage
  POST /v2/calling/pbx-extensions/<ext>/delete     {reason}: unassigned only (or cancel a pending request)      calls_manage
  PUT  /v2/calling/users/<user_id>/softphone       {enabled, reason}: the user's in-app softphone (0082; off by
                                                   default; off revokes the user's SIP credentials)             calls_manage

Writes need an Idempotency-Key. A create / retry commits its request row BEFORE the PBX is called
(the PBX is not transactional): an unreachable PBX answers 201 with `state: "pending"`, never an
error that loses the request. PBX refusals answer 409/422 `pbx_<code>`; an unreachable PBX on
rename / delete answers 503 `pbx_unavailable`.
"""
from __future__ import annotations

from flask import Blueprint, request
from flask_jwt_extended import jwt_required

from core.api_response import created, json_body, ok, register_api_errors
from core.idempotency import idempotent
from services.calling import extension_provisioning as prov
from services.calling import softphone_access
from services.calling.common import CALLS_MANAGE, permission_required

bp = Blueprint("calling_extensions", __name__)
register_api_errors(bp)
URL_PREFIX = None


@bp.get("/v2/calling/pbx-extensions")
@jwt_required()
@permission_required(CALLS_MANAGE)
def overview():
    return ok(prov.overview(request.args))


@bp.get("/v2/calling/pbx-extensions/users-without")
@jwt_required()
@permission_required(CALLS_MANAGE)
def users_without():
    return ok(prov.users_without_extension(request.args))


@bp.get("/v2/calling/pbx-extensions/settings")
@jwt_required()
@permission_required(CALLS_MANAGE)
def get_settings():
    return ok(prov.get_settings())


@bp.put("/v2/calling/pbx-extensions/settings")
@jwt_required()
@permission_required(CALLS_MANAGE)
@idempotent()
def save_settings():
    return ok(prov.save_settings(json_body()), "extension settings saved")


@bp.post("/v2/calling/pbx-extensions")
@jwt_required()
@permission_required(CALLS_MANAGE)
@idempotent()
def create():
    return created(prov.create(json_body()), "extension requested")


@bp.get("/v2/calling/pbx-extensions/<extension>/requests")
@jwt_required()
@permission_required(CALLS_MANAGE)
def requests_of(extension):
    return ok(prov.history(extension))


@bp.post("/v2/calling/pbx-extensions/<extension>/retry")
@jwt_required()
@permission_required(CALLS_MANAGE)
@idempotent()
def retry(extension):
    return ok(prov.retry(extension, json_body(required=False)), "extension provisioning retried")


@bp.post("/v2/calling/pbx-extensions/<extension>/rename")
@jwt_required()
@permission_required(CALLS_MANAGE)
@idempotent()
def rename(extension):
    return ok(prov.rename(extension, json_body()), "extension renamed")


@bp.post("/v2/calling/pbx-extensions/<extension>/delete")
@jwt_required()
@permission_required(CALLS_MANAGE)
@idempotent()
def delete(extension):
    return ok(prov.delete(extension, json_body()), "extension deleted")


@bp.put("/v2/calling/users/<uuid:user_id>/softphone")
@jwt_required()
@permission_required(CALLS_MANAGE)
@idempotent()
def set_softphone(user_id):
    out = softphone_access.set_enabled(user_id, json_body())
    return ok(out, "softphone enabled" if out["enabled"] else "softphone disabled")
