"""Shared helpers for the calling services (request-bound: they use the JWT identity).

Scope, permission and JSON helpers are reused from the Phase 1 stream C helpers
(services/case_history_common.py) so calling follows exactly the same case-scope rules as
notes and contacts: out-of-scope rows answer 404, never 403, so an id never reveals a case.
"""
from __future__ import annotations

import re
import uuid

from sqlalchemy import text

from core.api_response import VALIDATION_FAILED, ApiError
from extensions import db
from services.case_history_common import (USER_NAME_SQL, advisory_lock, case_in_scope, iso,  # noqa: F401
                                          me, optional_text, page_args, paged, parse_uuid,
                                          permission_required, require_any, required_text, s,
                                          scope_sql, user_ref)

OUTCOMES = ("connected", "no_answer", "busy", "promise_discussed", "dispute", "wrong_number", "do_not_call")
STATES = ("prepared", "authorized", "dispatched", "agent_ringing", "agent_answered", "customer_dialing",
          "customer_answered", "bridged", "ended", "failed", "cancelled", "uncertain")
IN_FLIGHT = ("authorized", "dispatched", "agent_ringing", "agent_answered", "customer_dialing",
             "customer_answered", "bridged")
TERMINAL = ("ended", "failed", "cancelled")
MODES = ("manual", "click_to_call", "campaign")
EXTENSION_RE = re.compile(r"^[0-9]{2,6}$")
E164_RE = re.compile(r"^\+[1-9][0-9]{7,14}$")

# Permission keys (registered in core/permissions.py by the lead)
CALLS = "calls"                  # call log, my calls, manual log + wrap-up
CALLS_MAKE = "calls_make"        # click-to-call and the softphone (P1/P3)
CALLS_MANAGE = "calls_manage"    # calling admin: extensions, windows, blocks, others' wrap-ups


def sess():
    return db.session


def valid_uuid(value) -> str | None:
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        return None


def extension_arg(value, field: str = "extension") -> str:
    if not isinstance(value, str) or not EXTENSION_RE.match(value):
        raise ApiError(422, VALIDATION_FAILED, f"{field} must be 2-6 digits", {field: "invalid"})
    return value


def bool_arg(body: dict, field: str, default=None):
    if field not in body or body[field] is None:
        return default
    if not isinstance(body[field], bool):
        raise ApiError(422, VALIDATION_FAILED, f"{field} must be true or false", {field: "invalid"})
    return body[field]


def e164_from_contact(value: str | None) -> str | None:
    """Stored Phase 1 contact values are digits like 9665XXXXXXXX (country code, no plus).
    Returns +<digits> when that is a plausible E.164 number, else None (never guessed)."""
    if not isinstance(value, str):
        return None
    digits = re.sub(r"[\s\-().]", "", value.strip())
    if digits.startswith("00"):
        digits = digits[2:]
    digits = digits.lstrip("+")
    if re.fullmatch(r"0?5\d{8}", digits):                      # national Saudi mobile
        digits = "966" + digits[-9:]
    candidate = "+" + digits
    return candidate if E164_RE.match(candidate) else None


def user_row(user_id):
    return sess().execute(text(f"""
        SELECT u.id, {USER_NAME_SQL.format(a='u')} AS name, u.is_active, u.extension
          FROM users u WHERE u.id = CAST(:id AS uuid)"""), {"id": str(user_id)}).mappings().first()


def case_ref(case_id) -> dict | None:
    r = sess().execute(text("""
        SELECT c.id, c.name, c."accountNumber" AS account_number, c."identityNumber" AS identity_number,
               c.user_id, c.is_archive, c.is_locked
          FROM clints c WHERE c.id = CAST(:id AS uuid)"""), {"id": str(case_id)}).mappings().first()
    if r is None:
        return None
    return {"id": str(r["id"]), "name": r["name"], "account_number": r["account_number"],
            "identity_number": r["identity_number"], "owner_id": s(r["user_id"]),
            "is_archived": bool(r["is_archive"]), "is_locked": bool(r["is_locked"])}
