"""Phase 1 stream C: /v2 note history and corrections, debtor contacts, reviewed case
corrections. New routes only (legacy routes are untouched); the response convention is
`core/api_response.py`, every mutating route is `@idempotent()` (Idempotency-Key header
required) and runs in one transaction with its audit rows.

Notes                                                   permission
  GET  /v2/cases/<case_id>/notes                        clints_details
  GET  /v2/notes/<note_id>/history                      clints_history
  POST /v2/notes/<note_id>/correct                      clints_comments_correct
  POST /v2/notes/<note_id>/retire                       clints_comments_retire
Contacts (debtor = identityNumber, shared by the debtor's cases)
  GET  /v2/debtors/<identity>/contacts                  clints_details
  GET  /v2/debtors/<identity>/contacts/history          clints_history
  POST /v2/debtors/<identity>/contacts                  clints_contacts_edit
  PUT  /v2/contacts/<contact_id>                        clints_contacts_edit
  POST /v2/contacts/<contact_id>/default                clints_contacts_edit
  POST /v2/contacts/<contact_id>/block                  clints_contacts_edit
  POST /v2/contacts/<contact_id>/unblock                clints_contacts_edit
Case corrections
  POST /v2/cases/<case_id>/corrections                  case_corrections_request
  GET  /v2/corrections                                  case_corrections_request | _review
  GET  /v2/corrections/<id>                             case_corrections_request | _review
  POST /v2/corrections/<id>/approve                     case_corrections_review (not own)
  POST /v2/corrections/<id>/reject                      case_corrections_review (not own)
  POST /v2/corrections/<id>/cancel                      case_corrections_request (requester)

Every route also applies the caller's case scope (services/clients_scope.py); anything
outside it answers 404.
"""
from flask import Blueprint, request
from flask_jwt_extended import jwt_required

from core.api_response import created, json_body, ok, register_api_errors
from core.idempotency import idempotent
from core.revisions import expected_revision_from_request
from services import case_history_contacts as contacts
from services import case_history_corrections as corrections
from services import case_history_notes as notes
from services.case_history_common import page_args, permission_required

bp = Blueprint("case_history", __name__)
URL_PREFIX = None
register_api_errors(bp)

_TRUTHY = ("1", "true", "yes", "on")


# ---------------------------------------------------------------------------- notes --

@bp.get("/v2/cases/<uuid:case_id>/notes")
@jwt_required()
@permission_required("clints_details")
def case_notes(case_id):
    page, size, offset = page_args()
    include_retired = (request.args.get("include_retired") or "").lower() in _TRUTHY
    return ok(notes.case_notes(case_id, include_retired=include_retired, page=page, size=size, offset=offset))


@bp.get("/v2/notes/<uuid:note_id>/history")
@jwt_required()
@permission_required("clints_history")
def note_history(note_id):
    return ok(notes.note_history(note_id))


@bp.post("/v2/notes/<uuid:note_id>/correct")
@jwt_required()
@permission_required("clints_comments_correct")
@idempotent()
def correct_note(note_id):
    body = json_body()
    return ok(notes.correct_note(note_id, body, expected_revision_from_request()), "corrected")


@bp.post("/v2/notes/<uuid:note_id>/retire")
@jwt_required()
@permission_required("clints_comments_retire")
@idempotent()
def retire_note(note_id):
    body = json_body()
    return ok(notes.retire_note(note_id, body, expected_revision_from_request()), "retired")


# ------------------------------------------------------------------------- contacts --

@bp.get("/v2/debtors/<identity_number>/contacts")
@jwt_required()
@permission_required("clints_details")
def debtor_contacts(identity_number):
    return ok(contacts.debtor_contacts(identity_number))


@bp.get("/v2/debtors/<identity_number>/contacts/history")
@jwt_required()
@permission_required("clints_history")
def debtor_contact_history(identity_number):
    page, size, offset = page_args()
    return ok(contacts.contact_history(identity_number, page, size, offset))


@bp.post("/v2/debtors/<identity_number>/contacts")
@jwt_required()
@permission_required("clints_contacts_edit")
@idempotent()
def add_contact(identity_number):
    return created(contacts.add_contact(identity_number, json_body()), "contact added")


@bp.put("/v2/contacts/<uuid:contact_id>")
@jwt_required()
@permission_required("clints_contacts_edit")
@idempotent()
def edit_contact(contact_id):
    body = json_body()
    return ok(contacts.edit_contact(contact_id, body, expected_revision_from_request()), "contact updated")


@bp.post("/v2/contacts/<uuid:contact_id>/default")
@jwt_required()
@permission_required("clints_contacts_edit")
@idempotent()
def set_default_contact(contact_id):
    body = json_body(required=False)
    return ok(contacts.set_default(contact_id, body, expected_revision_from_request()), "default contact set")


@bp.post("/v2/contacts/<uuid:contact_id>/block")
@jwt_required()
@permission_required("clints_contacts_edit")
@idempotent()
def block_contact(contact_id):
    body = json_body()
    return ok(contacts.block_contact(contact_id, body, expected_revision_from_request()), "contact blocked")


@bp.post("/v2/contacts/<uuid:contact_id>/unblock")
@jwt_required()
@permission_required("clints_contacts_edit")
@idempotent()
def unblock_contact(contact_id):
    body = json_body()
    return ok(contacts.unblock_contact(contact_id, body, expected_revision_from_request()), "contact unblocked")


# ---------------------------------------------------------------------- corrections --

@bp.post("/v2/cases/<uuid:case_id>/corrections")
@jwt_required()
@permission_required("case_corrections_request")
@idempotent()
def request_correction(case_id):
    return created(corrections.request_correction(case_id, json_body()), "correction requested")


@bp.get("/v2/corrections")
@jwt_required()
@permission_required("case_corrections_request", "case_corrections_review")
def list_corrections():
    page, size, offset = page_args()
    return ok(corrections.list_corrections(request.args, page, size, offset))


@bp.get("/v2/corrections/<uuid:correction_id>")
@jwt_required()
@permission_required("case_corrections_request", "case_corrections_review")
def correction_detail(correction_id):
    return ok(corrections.detail(correction_id))


@bp.post("/v2/corrections/<uuid:correction_id>/approve")
@jwt_required()
@permission_required("case_corrections_review")
@idempotent()
def approve_correction(correction_id):
    return ok(corrections.approve(correction_id, json_body(required=False)), "correction approved")


@bp.post("/v2/corrections/<uuid:correction_id>/reject")
@jwt_required()
@permission_required("case_corrections_review")
@idempotent()
def reject_correction(correction_id):
    return ok(corrections.reject(correction_id, json_body()), "correction rejected")


@bp.post("/v2/corrections/<uuid:correction_id>/cancel")
@jwt_required()
@permission_required("case_corrections_request")
@idempotent()
def cancel_correction(correction_id):
    return ok(corrections.cancel(correction_id, json_body(required=False)), "correction cancelled")
