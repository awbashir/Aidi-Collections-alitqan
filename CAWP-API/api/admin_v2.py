"""Phase 3 stream C: targets with history, server-side sessions, /v2/me.

New routes only; `core/api_response.py` conventions (real HTTP codes, `code`), every
mutating route is `@idempotent()` (Idempotency-Key header) and writes `audit_events` in
the same transaction. Keys follow the screen-key rule (legacy keys of the legacy screens),
evidence in review/phase3-admin.md.

  GET  /v2/me                                   any signed-in user
  POST /v2/users/<id>/sign-out                  users_sessions
  POST /v2/users/<id>/deactivate                users_delete | users_details (the legacy switch)
  POST /v2/users/<id>/activate                  users_delete | users_details
  GET  /v2/users/<id>/targets                   users_edit_target | users_details | self
  PUT  /v2/users/<id>/targets/<YYYY-MM>         users_edit_target
  GET  /v2/contracts/<id>/targets               contracts | contracts_edit
  PUT  /v2/contracts/<id>/targets/<YYYY-MM>     contracts_edit
  GET  /v2/targets/achievement?month=YYYY-MM    dashbord_admin (every collector + contracts,
                                                like the legacy all-collectors KPI) or
                                                dashbord_user (own row only)
"""
from __future__ import annotations

import datetime as dt
from functools import wraps

from flask import Blueprint, after_this_request, request
from flask_jwt_extended import get_jwt, get_jwt_identity, jwt_required
from sqlalchemy import text

from core import audit, lookups
from core.api_response import VALIDATION_FAILED, ApiError, json_body, ok, register_api_errors
from core.auth import has_permission, roles_map
from core.idempotency import idempotent
from core.revisions import expected_revision_from_request
from extensions import db
from services import sessions
from services import targets as tsvc

bp = Blueprint("admin_v2", __name__)
URL_PREFIX = None
register_api_errors(bp)

# the legacy active/inactive switch: users list (users_delete, UsersTable.tsx:119) and the
# ungated switch on the details page (route key users_details, CustomerProfile.tsx:86)
USERS_STATUS_KEYS = ("users_delete", "users_details")


def require_any(*keys):
    if not any(has_permission(k) for k in keys):
        raise ApiError(403, "forbidden", "You are not authorized to access this resource",
                       {"required_any": list(keys)})


def permission_required(*keys):
    """Place below @jwt_required() and above @idempotent()."""
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            require_any(*keys)
            return fn(*args, **kwargs)
        return wrapper
    return decorator


def me() -> str:
    return str(get_jwt_identity())


def _iso(ts):
    if ts is None:
        return None
    if isinstance(ts, (int, float)):
        ts = dt.datetime.fromtimestamp(ts, tz=dt.timezone.utc)
    return ts.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _reason(body, required=True):
    reason = body.get("reason")
    if reason is not None and not isinstance(reason, str):
        raise ApiError(422, VALIDATION_FAILED, "reason must be text", {"reason": "invalid"})
    reason = (reason or "").strip()
    if required and not reason:
        raise ApiError(422, VALIDATION_FAILED, "reason is required", {"reason": "required"})
    if len(reason) > 1000:
        raise ApiError(422, VALIDATION_FAILED, "reason is too long", {"reason": "too_long"})
    return reason or None


def _user_row(user_id, lock=False):
    return db.session.execute(text(
        "SELECT id::text AS id, user_name, frist_name, last_name, is_active, role_id::text AS role_id, "
        "sessions_valid_after FROM users WHERE id = CAST(:id AS uuid)" + (" FOR UPDATE" if lock else "")),
        {"id": str(user_id)}).mappings().first()


# =========================================================================== /v2/me
def effective_permissions(role_permission: dict | None) -> dict:
    """Every grantable key (legacy + registered new keys) -> bool, plus any other key the
    role JSON holds. Only a literal `true` grants (same as core.auth.has_permission)."""
    from core.permissions import role_permission_template
    perm = role_permission or {}
    out = {k: perm.get(k) is True for k in role_permission_template()}
    for k, v in perm.items():
        out.setdefault(k, v is True)
    return out


@bp.get("/v2/me")
@jwt_required()
def get_me():
    from models import Branch, Role, User
    from services.clients_scope import invalidate_scope, resolve_scope

    uid = me()
    user = db.session.get(User, uid)
    if user is None:           # the loader already rejects deleted users; defensive
        raise ApiError(404, "not_found", "user not found")
    role = db.session.get(Role, user.role_id)
    perm = (role.permission if role is not None else None) or {}
    # the permission gate caches roles per process for 60 s; refresh it when this read
    # shows that a grant changed in another process
    cached = roles_map().get(str(user.role_id)) or {}
    if (cached.get("permission") or {}) != perm:
        lookups.invalidate("roles", "users_role")
    invalidate_scope(uid)
    scope = resolve_scope(uid)
    state = sessions.current_state(uid)
    claims = get_jwt()
    tuser = user.as_dict()                       # password is never serialized
    tuser["name"] = f"{tuser.get('frist_name') or ''} {tuser.get('last_name') or ''}".strip()
    branch = db.session.get(Branch, user.branch_id) if user.branch_id else None
    return ok({
        "user": tuser,
        "role": {"id": str(role.id), "name": role.name} if role is not None else None,
        "branch": {"id": str(branch.id), "name": branch.name} if branch is not None else None,
        "permissions": effective_permissions(perm),
        "scope": {"kind": scope.kind, "supervisor_id": scope.supervisor_id},
        "session": {
            "sessions_valid_after": sessions.as_iso(state.get("v")),
            "token_issued_at": sessions.as_iso(sessions.issued_at_ms(claims)),
            "token_expires_at": _iso(claims.get("exp")),
        },
    })


# =========================================================================== sessions
def _invalidate_after_commit(user_id, permission_maps=False):
    """@idempotent commits after the view returns; drop the caches only after that, so a
    concurrent request cannot re-cache the old state in between."""
    @after_this_request
    def _drop(response):
        sessions.invalidate(str(user_id))
        if permission_maps:
            from core.auth import invalidate_permission_maps
            invalidate_permission_maps()
        return response


@bp.post("/v2/users/<uuid:user_id>/sign-out")
@jwt_required()
@permission_required("users_sessions")
@idempotent()
def sign_out_everywhere(user_id):
    body = json_body(required=False)
    reason = _reason(body, required=False)
    if _user_row(user_id, lock=True) is None:
        raise ApiError(404, "not_found", "user not found")
    valid_after = sessions.sign_out_user(user_id, reason=reason or "sign out everywhere")
    _invalidate_after_commit(user_id)
    return ok({"user_id": str(user_id), "sessions_valid_after": _iso(valid_after)}, "signed_out")


def _set_active(user_id, active: bool):
    body = json_body()
    reason = _reason(body)
    row = _user_row(user_id, lock=True)
    if row is None:
        raise ApiError(404, "not_found", "user not found")
    if not active and str(user_id) == me():
        raise ApiError(409, "cannot_deactivate_self", "you cannot deactivate your own account")
    if row["is_active"] == active:
        raise ApiError(409, "already_" + ("active" if active else "inactive"),
                       "the account is already " + ("active" if active else "inactive"))
    sessions.set_context(**sessions.request_context(), reason=reason, source="api")
    new = db.session.execute(text(
        "UPDATE users SET is_active = :a WHERE id = CAST(:id AS uuid) RETURNING sessions_valid_after"),
        {"a": active, "id": str(user_id)}).first()
    audit.record("user.activate" if active else "user.deactivate", "user", user_id,
                 before={"is_active": row["is_active"]}, after={"is_active": active}, reason=reason)
    _invalidate_after_commit(user_id, permission_maps=True)
    return ok({"user_id": str(user_id), "is_active": active,
               "sessions_valid_after": _iso(new[0]) if new else None},
              "activated" if active else "deactivated")


@bp.post("/v2/users/<uuid:user_id>/deactivate")
@jwt_required()
@permission_required(*USERS_STATUS_KEYS)
@idempotent()
def deactivate_user(user_id):
    return _set_active(user_id, False)


@bp.post("/v2/users/<uuid:user_id>/activate")
@jwt_required()
@permission_required(*USERS_STATUS_KEYS)
@idempotent()
def activate_user(user_id):
    return _set_active(user_id, True)


# =========================================================================== targets
def _history_payload(subject, subject_id):
    if not tsvc.subject_exists(db.session, subject, subject_id):
        raise ApiError(404, "not_found", f"{subject} not found")
    now_month = tsvc.current_month()
    rows = tsvc.history(db.session, subject, subject_id)
    months = sorted({r["month"] for r in rows} | {tsvc.month_str(now_month)}, reverse=True)
    parsed = [tsvc.parse_month(m) for m in months]
    eff = {m: tsvc.effective_targets(db.session, subject, [subject_id], pm).get(str(subject_id))
           for m, pm in zip(months, parsed)}
    return {
        "subject_type": subject,
        "subject_id": str(subject_id),
        "current_month": tsvc.month_str(now_month),
        "legacy_current": tsvc.legacy_current(db.session, subject, subject_id),
        "months": [{"month": m, "locked": pm < now_month,
                    "revision": tsvc.month_revision(db.session, subject, subject_id, pm),
                    "in_force": tsvc.target_view(eff[m])}
                   for m, pm in zip(months, parsed)],
        "history": rows,
    }


def _set_payload(subject, subject_id, month_text):
    month = tsvc.parse_month(month_text)
    body = json_body()
    expected = expected_revision_from_request()
    row = tsvc.set_target(db.session, subject, subject_id, month, body.get("kind"), body.get("value"),
                          reason=body.get("reason") if isinstance(body.get("reason"), str) else "",
                          expected_revision=expected, actor=me(),
                          request_id=request.headers.get("X-Request-ID"),
                          idempotency_key=request.headers.get("Idempotency-Key"))
    return ok({"target": row, "month": tsvc.month_str(month), "revision": row["revision"],
               "mirrored_to_legacy": month == tsvc.current_month()}, "saved")


@bp.get("/v2/users/<uuid:user_id>/targets")
@jwt_required()
def user_targets(user_id):
    if str(user_id) != me():
        require_any("users_edit_target", "users_details")
    return ok(_history_payload("user", user_id))


@bp.put("/v2/users/<uuid:user_id>/targets/<month>")
@jwt_required()
@permission_required("users_edit_target")
@idempotent()
def set_user_target(user_id, month):
    return _set_payload("user", user_id, month)


@bp.get("/v2/contracts/<uuid:contract_id>/targets")
@jwt_required()
@permission_required("contracts", "contracts_edit")
def contract_targets(contract_id):
    return ok(_history_payload("contract", contract_id))


@bp.put("/v2/contracts/<uuid:contract_id>/targets/<month>")
@jwt_required()
@permission_required("contracts_edit")
@idempotent()
def set_contract_target(contract_id, month):
    return _set_payload("contract", contract_id, month)


@bp.get("/v2/targets/achievement")
@jwt_required()
@permission_required("dashbord_admin", "dashbord_user")
def targets_achievement():
    month = tsvc.parse_month(request.args.get("month")) if request.args.get("month") else tsvc.current_month()
    contract_ids = request.args.getlist("contract_id") or None
    user_ids = request.args.getlist("user_id") or None
    for v in (contract_ids or []) + (user_ids or []):
        if not sessions._valid_uuid(v):
            raise ApiError(422, VALIDATION_FAILED, "ids must be UUIDs", {"id": v})
    if has_permission("dashbord_admin"):
        data = tsvc.achievement(db.session, month, user_ids=user_ids, contract_ids=contract_ids)
        data["view"] = "all"
    else:
        # own KPI only (the legacy dashbord_user card)
        data = tsvc.achievement(db.session, month, user_ids=[me()], contract_ids=contract_ids,
                                include_contracts=False)
        data["view"] = "own"
    return ok(data)
