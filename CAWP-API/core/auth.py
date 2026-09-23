"""Permission gate, resolved from the JWT identity.

Moved out of `app.py` so blueprints can import it without an import cycle
(`api/imports.py` used to do a lazy `from app import has_permission` inside the
decorator body). Behaviour is unchanged:

* `has_permission(key)` looks the caller's role up through two process-local maps
  (60 s TTL, `core.lookups`) and reads `key` out of the role's `permission` JSON;
* `required_roles(key)` returns `403 {"message": "You are not authorized to access
  this resource"}` when it is false, and must sit **below** `@jwt_required()` so the
  identity is available;
* `invalidate_permission_maps()` is called after any write to roles or users.

The original implementation read `Role.query.get(session['user']['role_id'])`. The
frontend authenticates with an `Authorization: Bearer` header and never sends cookies,
so `session['user']` raised KeyError -> 500, which is why every `@required_roles(...)`
in the legacy file was commented out (review 3.12 / S4).
"""
import logging
from functools import wraps

from flask import jsonify
from flask_jwt_extended import get_jwt_identity

from core import lookups
from extensions import db
from models import Role, User

log = logging.getLogger("cawp.auth")

ADMIN_ROLES = ("ADMIN", "SuperAdmin")


def roles_map():
    """{role_id: role_dict} - process-local, 60 s TTL, invalidated on role writes."""
    return lookups.get_map('roles', lambda: [r.as_dict() for r in Role.query.all()])


def users_role_map():
    """{user_id: {"id", "role_id"}} - avoids a User.query.get per permission check."""
    def loader():
        return [{"id": str(uid), "role_id": str(rid)}
                for uid, rid in db.session.query(User.id, User.role_id).all()]
    return lookups.get_map('users_role', loader)


def invalidate_permission_maps():
    """Call after any write to roles or users so the gate picks the change up at once."""
    lookups.invalidate('roles', 'users_role')


def current_role():
    """The caller's role dict, or None."""
    entry = users_role_map().get(str(get_jwt_identity()))
    if not entry:
        return None
    return roles_map().get(str(entry.get('role_id')))


def has_permission(key):
    """True when the JWT identity's role grants `key` in its `permission` JSON."""
    if not key:
        return True
    try:
        if get_jwt_identity() is None:
            return False
        role = current_role()
        if not role:
            return False
        return bool((role.get('permission') or {}).get(key))
    except Exception as e:  # noqa: BLE001
        log.exception("permission check for %r failed: %s", key, e)
        return False


def is_admin_identity():
    """True for the ADMIN / SuperAdmin roles (used by the job-ownership check)."""
    try:
        return (current_role() or {}).get('name') in ADMIN_ROLES
    except Exception:  # noqa: BLE001
        return False


def required_roles(*roles):
    """Server-side permission gate. Place **below** `@jwt_required()`."""
    key = roles[0] if roles else None

    def wrapper(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            if not has_permission(key):
                return jsonify({'message': 'You are not authorized to access this resource'}), 403
            return f(*args, **kwargs)
        return wrapped
    return wrapper


def required_any(*keys):
    """Like `required_roles`, but passes when the caller has *any* of `keys`.

    Used for the legacy routes whose buttons appear on more than one screen, so the
    server check matches what the UI already allows. Place **below** `@jwt_required()`.
    Same 403 body as `required_roles`.
    """
    def wrapper(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            if not any(has_permission(k) for k in keys):
                return jsonify({'message': 'You are not authorized to access this resource'}), 403
            return f(*args, **kwargs)
        return wrapped
    return wrapper
