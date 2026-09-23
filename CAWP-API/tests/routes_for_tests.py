"""Throwaway routes that exercise the Phase 0 helpers the way a new blueprint would.

Only registered on the pytest Flask app (tests/conftest.py), never on the real app.
"""
from flask import Blueprint, jsonify, request
from flask_jwt_extended import jwt_required

from core import audit
from core.api_response import ApiError, created, json_body, ok, register_api_errors
from core.idempotency import idempotent
from core.revisions import check_revision, expected_revision_from_request

bp = Blueprint("phase0_test_routes", __name__)
register_api_errors(bp)

CALLS = {"notes": 0}


@bp.post("/t/cases/<uuid:case_id>/notes")
@jwt_required()
@idempotent()
def add_note(case_id):
    body = json_body()
    if not body.get("text"):
        raise ApiError(422, "validation_failed", "text is required", {"text": "required"})
    CALLS["notes"] += 1
    ev = audit.record("test.note", "case", case_id, case_id=case_id, after={"text": body["text"]},
                      source="pytest")
    return created({"audit_id": ev["id"], "call": CALLS["notes"]}, "saved")


@bp.post("/t/boom")
@jwt_required()
@idempotent()
def boom():
    audit.record("test.boom", "case", "x", source="pytest")
    raise RuntimeError("handler failed")


@bp.post("/t/optional")
@jwt_required()
@idempotent(required=False)
def optional():
    return ok({"echo": request.get_json(silent=True)})


@bp.put("/t/cases/<uuid:case_id>")
@jwt_required()
def update_case(case_id):
    expected = expected_revision_from_request()
    current = check_revision("case", case_id, expected)
    return ok({"revision": current})


@bp.get("/t/legacy-style")
def legacy_style():
    # a route that does not raise ApiError keeps whatever body it returns
    return jsonify({"status": False, "message": "legacy"}), 201
