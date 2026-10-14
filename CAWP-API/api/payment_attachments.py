"""Payment attachments (receipts) on /v2 (db/migrations/0069, services/payment_attachments.py).

    POST   /v2/payments/<id>/attachments                      multipart `file`, optional `name`   idempotent
    GET    /v2/payments/<id>/attachments                      list
    GET    /v2/payments/<id>/attachments/<document_id>/download   the file, inline, private
    DELETE /v2/payments/<id>/attachments/<document_id>        pending payments; uploader or accounts   idempotent
    GET    /v2/cases/<case_id>/payment-attachments            {payment_id: [attachments]} for the case's payments

Permissions: attaching and removing need one of clints_Payments, accounts_payments,
payments_approve; reading needs a case-money read key (api.money.CASE_MONEY_READ), so collectors
and approvers see receipts without the documents key. Every route is limited to the caller's case
scope; outside it the payment answers 404. Same envelope and error conventions as api/money.py.
"""
from __future__ import annotations

from functools import wraps

from flask import Blueprint, request, send_file
from flask_jwt_extended import get_jwt_identity, jwt_required

from api.money import CASE_MONEY_READ, require_any
from core.api_response import created, fail, ok, register_api_errors
from core.auth import has_permission
from core.idempotency import idempotent
from services import money as msvc
from services import payment_attachments as svc

bp = Blueprint("payment_attachments_v2", __name__)
register_api_errors(bp)
URL_PREFIX = None

ATTACH_KEYS = ("clints_Payments", "accounts_payments", "payments_approve")
# multipart overhead allowed on top of the file itself before the body is read
_BODY_SLACK = 256 * 1024


def _me():
    return str(get_jwt_identity())


def _scope():
    return msvc.caller_scope(_me())


def _body_size_guard(fn):
    """413 before the idempotency fingerprint reads a body that cannot be accepted anyway."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        length = request.content_length
        if length is not None and length > svc.MAX_BYTES + _BODY_SLACK:
            return fail(413, "file_too_large", f"the file is larger than {svc.MAX_BYTES} bytes",
                        {"max_bytes": svc.MAX_BYTES})
        return fn(*args, **kwargs)
    return wrapper


@bp.post("/v2/payments/<uuid:payment_id>/attachments")
@jwt_required()
@require_any(*ATTACH_KEYS)
@_body_size_guard
@idempotent()
def add(payment_id):
    body = svc.add_attachment(payment_id, request.files.get("file"), request.form, _me(), _scope())
    return created(body, "attached")


@bp.get("/v2/payments/<uuid:payment_id>/attachments")
@jwt_required()
@require_any(*CASE_MONEY_READ)
def list_(payment_id):
    return ok(svc.list_attachments(payment_id, _scope()))


@bp.get("/v2/payments/<uuid:payment_id>/attachments/<uuid:document_id>/download")
@jwt_required()
@require_any(*CASE_MONEY_READ)
def download(payment_id, document_id):
    path, name, mime = svc.download(payment_id, document_id, _scope())
    resp = send_file(path, mimetype=mime, as_attachment=False, download_name=name,
                     conditional=False, etag=False, max_age=0)
    resp.headers["Cache-Control"] = "private, no-store"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


@bp.delete("/v2/payments/<uuid:payment_id>/attachments/<uuid:document_id>")
@jwt_required()
@require_any(*ATTACH_KEYS)
@idempotent()
def remove(payment_id, document_id):
    body = svc.remove_attachment(payment_id, document_id, _me(), _scope(),
                                 manage_any=has_permission("accounts_payments"))
    return ok(body, "removed")


@bp.get("/v2/cases/<uuid:case_id>/payment-attachments")
@jwt_required()
@require_any(*CASE_MONEY_READ)
def case_attachments(case_id):
    return ok(svc.case_attachments(case_id, _scope()))
