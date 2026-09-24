"""GET /v2/me: current user (no password), effective permissions, scope, session state."""
from __future__ import annotations

import json

from test_admin_support import *  # noqa: F401,F403


def test_me_returns_user_permissions_scope_and_session(ac, db):
    uid = make_user(db, role_id=A.role_col, contract=A.contract_a)
    r = ac.get("/v2/me", uid)
    assert r.status_code == 200, r.get_json()
    data = r.get_json()["data"]
    user = data["user"]
    assert user["id"] == uid and user["user_name"] == user_name(db, uid) and user["name"] == "Adm " + uid[:8]
    assert "password" not in user and "sessions_valid_after" not in user
    assert "password" not in json.dumps(data)
    assert data["role"] == {"id": A.role_col, "name": "AdminCollector"}
    perms = data["permissions"]
    # every legacy key and every registered new key is present; only literal true grants
    from core.permissions import NEW_PERMISSIONS
    from models import defult_permission
    assert set(defult_permission) <= set(perms) and {p.key for p in NEW_PERMISSIONS} <= set(perms)
    assert perms["dashbord_user"] is True and perms["users_add"] is False and perms["users_sessions"] is False
    assert data["scope"]["kind"] == "collector"
    assert data["session"]["sessions_valid_after"] is None
    assert data["session"]["token_issued_at"] and data["session"]["token_expires_at"]


def test_me_reflects_a_grant_change_and_a_sign_out_at_once(ac, db):
    role = make_role(db, {"clints": True})
    uid = make_user(db, role_id=role)
    first = ac.get("/v2/me", uid).get_json()["data"]
    assert first["permissions"]["clints"] is True and first["permissions"]["users"] is False
    q(db, "UPDATE roles SET permission = %s WHERE id = %s", (json.dumps({"clints": False, "users": True}), role))
    q(db, "UPDATE users SET sessions_valid_after = now() WHERE id = %s", (uid,))
    again = ac.get("/v2/me", uid).get_json()["data"]
    assert again["permissions"]["clints"] is False and again["permissions"]["users"] is True
    assert again["session"]["sessions_valid_after"] is not None


def test_me_scope_kinds(ac, db):
    sup_user = make_user(db, {"clints": True})
    q(db, "INSERT INTO supervisors (user_id) VALUES (%s)", (sup_user,))
    assert ac.get("/v2/me", sup_user).get_json()["data"]["scope"]["kind"] == "supervisor"
    sa_role = q1(db, "SELECT id::text FROM roles WHERE name = 'SuperAdmin'")
    if sa_role is None:
        sa_role = (make_role(db, {"clints": True}, name="SuperAdmin"),)
    sa = make_user(db, role_id=sa_role[0])
    assert ac.get("/v2/me", sa).get_json()["data"]["scope"]["kind"] == "super"


def test_me_requires_a_token(ac):
    assert ac.client.get("/v2/me").status_code == 401
