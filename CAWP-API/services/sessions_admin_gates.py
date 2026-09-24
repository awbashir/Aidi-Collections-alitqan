"""Field-level check for the legacy user edit route (PUT /uesrs/<id>), Phase 3 stream C.

The legacy user form shows its fields in two blocks (CAWP-frontend
src/views/users/form/UserForm.tsx):

  * general block, only with `users_edit_general` (UserForm.tsx:191): frist_name, last_name,
    email, identityNumber, mobile, nationality_id (UserFields2), user_name, password,
    branch_id, role_id (UserFields);
  * target block, only with `users_edit_target` (UserForm.tsx:215): extension, contract_id,
    target_is_percentage, target_amount, target_percent (UserFields3).

Formik submits the whole object (UserForm.tsx:176-184), hidden fields included, with the
values it loaded from GET /users/<id>. So the check compares with the stored row and only
refuses a request that CHANGES a field of a block the caller cannot see (403, the legacy
body). Unchanged hidden fields pass, so every save the UI allows today still works.

`is_active` is not on the form; changing it through PUT needs one of the keys that show the
active/inactive switch (`users_delete` on the users list, UsersTable.tsx:119; the ungated
switch on the details page, route key `users_details`).
"""
from __future__ import annotations

import uuid
from decimal import Decimal, InvalidOperation
from functools import wraps

from flask import jsonify, request

GENERAL_KEY = "users_edit_general"
TARGET_KEY = "users_edit_target"
STATUS_KEYS = ("users_delete", "users_details")

_TEXT = {"frist_name": GENERAL_KEY, "last_name": GENERAL_KEY, "email": GENERAL_KEY,
         "identityNumber": GENERAL_KEY, "mobile": GENERAL_KEY, "user_name": GENERAL_KEY,
         "extension": TARGET_KEY}
_UUID = {"nationality_id": GENERAL_KEY, "branch_id": GENERAL_KEY, "role_id": GENERAL_KEY,
         "contract_id": TARGET_KEY}
_NUM = {"target_amount": TARGET_KEY, "target_percent": TARGET_KEY}
_BOOL = {"target_is_percentage": TARGET_KEY}


def _norm_text(v):
    return None if v is None or v == "" else str(v)


def _norm_uuid(v):
    if v is None or v == "":
        return None
    try:
        return str(uuid.UUID(str(v)))
    except (ValueError, AttributeError, TypeError):
        return ("invalid", str(v))


def _norm_num(v):
    if v is None or v == "":
        return Decimal(0)
    if isinstance(v, bool):
        return ("invalid", v)
    try:
        d = Decimal(str(v).strip())
    except (InvalidOperation, ValueError):
        return ("invalid", str(v))
    return d if d.is_finite() else ("invalid", str(v))


def _norm_bool(v):
    if v is None or v == "":
        return False
    if isinstance(v, str):
        return v.strip().lower() in ("true", "1", "yes", "on")
    return bool(v)


def changed_fields(stored: dict, body: dict) -> dict:
    """{field: key needed} for every protected field the body changes."""
    out = {}
    for table, norm in ((_TEXT, _norm_text), (_UUID, _norm_uuid), (_NUM, _norm_num), (_BOOL, _norm_bool)):
        for field, key in table.items():
            if field in body and norm(body[field]) != norm(stored.get(field)):
                out[field] = key
    if body.get("password"):                     # the route changes the hash only when non-empty
        out["password"] = GENERAL_KEY
    if "is_active" in body and _norm_bool(body["is_active"]) != bool(stored.get("is_active")):
        out["is_active"] = STATUS_KEYS
    return out


def legacy_user_fields_gate(arg: str = "id"):
    """Decorator for PUT /uesrs/<id> (below the route-level gate)."""
    def deco(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            from core.auth import has_permission
            from extensions import db
            from models import User

            body = request.get_json(silent=True)
            user_id = kwargs.get(arg)
            if not isinstance(body, dict) or not user_id:
                return fn(*args, **kwargs)
            try:
                user = db.session.get(User, str(uuid.UUID(str(user_id))))
            except (ValueError, TypeError):
                user = None
            if user is None:
                return fn(*args, **kwargs)          # the route answers "user not found"
            stored = {c: getattr(user, c) for c in
                      list(_TEXT) + list(_UUID) + list(_NUM) + list(_BOOL) + ["is_active"]}
            for field, keys in changed_fields(stored, body).items():
                keys = keys if isinstance(keys, tuple) else (keys,)
                if not any(has_permission(k) for k in keys):
                    return jsonify({"message": "You are not authorized to access this resource"}), 403
            return fn(*args, **kwargs)
        return wrapper
    return deco
