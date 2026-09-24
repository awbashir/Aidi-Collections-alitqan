"""Phase 5 P4: PBX evidence of a call attempt (read only). review/phase5-evidence.md.

    GET /v2/calls/<attempt_id>/pbx-evidence    live CDR tagged with the attempt, imported CDR
                                               evidence, recordings     calls | calls_make

Out of scope or unknown -> 404 `call_not_found` (never 403, so an id reveals nothing).
Register: add "api.calling_evidence" to api/__init__.py MODULES (the lead's file).
"""
from __future__ import annotations

from flask import Blueprint
from flask_jwt_extended import jwt_required

from core.api_response import ok, register_api_errors
from services.case_history_common import permission_required

bp = Blueprint("calling_evidence", __name__)
register_api_errors(bp)
URL_PREFIX = None


@bp.get("/v2/calls/<uuid:attempt_id>/pbx-evidence")
@jwt_required()
@permission_required("calls", "calls_make")
def attempt_pbx_evidence(attempt_id):
    from services.calling_cdr_attempts import attempt_evidence
    return ok(attempt_evidence(attempt_id))
