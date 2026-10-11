"""POST /uesrs with the payload the new-user form really sends (UserForm.tsx initial values).

The form sends "" for target_amount / target_percent when the target block is left empty and
null for contract_id. "" used to reach the float columns, the insert failed and the handler
answered 201 {"status": false}, which the page showed as success.
"""
from __future__ import annotations

from test_admin_support import *  # noqa: F401,F403


def form_payload(user_name, **over):
    body = {
        "frist_name": "New", "last_name": "User", "email": f"{user_name}@example.test",
        "identityNumber": "1000000000", "mobile": "0500000001", "nationality_id": A.nationality,
        "password": "Created@2026", "role_id": A.role_col, "user_name": user_name, "branch_id": A.branch,
        "target_is_percentage": False, "target_amount": "", "target_percent": "", "is_active": True,
        "contract_id": None,
    }
    body.update(over)
    return body


def test_create_user_with_blank_targets_then_sign_in(db):
    res = run_app([
        {"op": "mint", "user": A.admin, "as": "ADM"},
        {"op": "call", "token": "ADM", "method": "POST", "path": "/uesrs", "json": form_payload("form_new_1")},
        {"op": "login", "username": "form_new_1", "password": "Created@2026", "as": "NEW"},
    ])
    created, login = res[1], res[2]
    assert created["status"] == 200, created
    assert login["status"] == 200, login
    row = q1(db, "SELECT target_amount, target_percent, contract_id, is_active FROM users WHERE user_name = %s",
             ("form_new_1",))
    assert row == (0.0, 0.0, None, True)


def test_create_user_without_password_is_refused(db):
    res = run_app([
        {"op": "mint", "user": A.admin, "as": "ADM"},
        {"op": "call", "token": "ADM", "method": "POST", "path": "/uesrs",
         "json": form_payload("form_new_2", password="")},
    ])
    assert res[1]["status"] == 201 and res[1]["body"]["status"] is False
    assert q1(db, "SELECT count(*) FROM users WHERE user_name = %s", ("form_new_2",)) == (0,)
