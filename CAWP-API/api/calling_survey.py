"""Post-call IVR survey (customer request 2026-10-13; services/calling/survey.py, migration 0066).

  GET  /v2/calling/surveys/settings                 every contract with its survey switch   calls_manage
  PUT  /v2/calling/surveys/settings/<contract_id>    {post_call_survey, reason?, expected_revision}  calls_manage
  GET  /v2/calling/surveys                           results: summary, by agent, by contract, latest  calls_manage
       ?from=YYYY-MM-DD&to=YYYY-MM-DD&contract_id=   (Asia/Riyadh dates; default the last 30 days)

The PBX side (the offer and the answer) goes through the calling service's FastAGI server,
never through these routes.
"""
from __future__ import annotations

from flask import Blueprint, request
from flask_jwt_extended import jwt_required

from core.api_response import json_body, ok, register_api_errors
from core.idempotency import idempotent
from core.revisions import expected_revision_from_request
from services.calling import survey as survey_svc
from services.calling.common import CALLS_MANAGE, permission_required

bp = Blueprint("calling_survey", __name__)
register_api_errors(bp)
URL_PREFIX = None


@bp.get("/v2/calling/surveys/settings")
@jwt_required()
@permission_required(CALLS_MANAGE)
def survey_settings():
    return ok(survey_svc.settings_view())


@bp.put("/v2/calling/surveys/settings/<contract_id>")
@jwt_required()
@permission_required(CALLS_MANAGE)
@idempotent()
def save_survey_setting(contract_id):
    return ok(survey_svc.save_setting(contract_id, json_body(), expected_revision_from_request()), "survey setting saved")


@bp.get("/v2/calling/surveys")
@jwt_required()
@permission_required(CALLS_MANAGE)
def survey_results():
    return ok(survey_svc.results(request.args))
