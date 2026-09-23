"""Stream A: server-side permission checks on the legacy assignment routes.

`POST /sepcifyusers/ids` and `POST /clint/SepcifyUsersByExcel` now require
`clints_sepcifyUser`, the key the legacy clients screen checks before it shows the
"assign selected", per-row assign and "assign by Excel" actions
(CAWP-frontend/src/views/clients/list/ClientsTable.tsx, components/ActionColumn.tsx).
Runs the REAL app.py (test_money_support.run_legacy). With the key, the behaviour and the
wire contract are unchanged.
"""
from test_bulk_assign_support import *  # noqa: F401,F403
from test_money_support import run_legacy

FORBIDDEN = {"message": "You are not authorized to access this resource"}
OK = {"message": "تم تحديد المستخدمين بنجاح", "status": True}


def _calls(case_id, account, target, login):
    return [
        {"method": "POST", "path": "/sepcifyusers/ids", "json": {"clint_ids": [case_id], "user_id": target}},
        {"method": "POST", "path": "/clint/SepcifyUsersByExcel",
         "json": {"user_name": "Login", "account_number": "Acc", "data": [{"Login": login, "Acc": account}]}},
        {"method": "POST", "path": "/sepcifyusers/ids?async=1", "json": {"clint_ids": [case_id], "user_id": target}},
        {"method": "POST", "path": "/clint/SepcifyUsersByExcel",
         "json": {"user_name": "Login", "account_number": "Acc", "data": [{"Login": login, "Acc": account}],
                  "async": True}},
    ]


def test_legacy_assign_routes_refuse_callers_without_the_key(ac, db):
    c, acc = mk(db, owner=A.col3)
    for user in (A.nokey, A.col1, A.sup_nk):
        results = run_legacy(_calls(c, acc, A.col1, "a.col1"), user=user)
        assert [(r["status"], r["body"]) for r in results] == [(403, FORBIDDEN)] * 4, user
    assert owner(db, c) == A.col3 and specify_rows(db, c) == []


def test_legacy_assign_routes_unchanged_with_the_key(ac, db):
    by_ids, _ = mk(db, owner=A.col3)
    by_excel, acc = mk(db, owner=None)
    results = run_legacy([
        {"method": "POST", "path": "/sepcifyusers/ids", "json": {"clint_ids": [by_ids], "user_id": A.col1}},
        {"method": "POST", "path": "/clint/SepcifyUsersByExcel",
         "json": {"user_name": "Login", "account_number": "Acc", "data": [{"Login": "a.col4", "Acc": acc}]}},
        # legacy business errors keep their 201 bodies
        {"method": "POST", "path": "/sepcifyusers/ids",
         "json": {"clint_ids": [by_ids], "user_id": "00000000-0000-4000-8000-00000000dead"}},
        {"method": "POST", "path": "/clint/SepcifyUsersByExcel",
         "json": {"user_name": "Login", "account_number": "Acc", "data": [{"Login": "no.such", "Acc": acc}]}},
        {"method": "POST", "path": "/clint/SepcifyUsersByExcel",
         "json": {"user_name": "Login", "account_number": "Acc", "data": [{"Login": "a.col4", "Acc": "NOPE-1"}]}},
    ], user=A.legacy)
    assert [(r["status"], r["body"]) for r in results] == [
        (200, OK), (200, OK),
        (201, {"message": "00000000-0000-4000-8000-00000000dead المستخدم غير موجود", "status": False}),
        (201, {"message": "no.such المستخدم غير موجود", "status": False}),
        (201, {"message": "NOPE-1 الحساب غير موجود", "status": False})]
    assert owner(db, by_ids) == A.col1 and specify_rows(db, by_ids) == [(A.col1, A.legacy)]
    assert owner(db, by_excel) == A.col4 and specify_rows(db, by_excel) == [(A.col4, A.legacy)]
    # legacy semantics are untouched: no eligibility rules, no audit rows (the reviewed flow adds those)
    assert assign_audit(db, by_ids) == [] and assign_audit(db, by_excel) == []


def test_legacy_gate_is_the_screen_key():
    """The decorators name exactly the key the legacy screens check (read as text: importing
    api.imports here would load the app config into this process)."""
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "api" / "imports.py").read_text(encoding="utf-8")
    for route, fn in (("'/clint/SepcifyUsersByExcel'", "def sepcifyUsers("),
                      ("'/sepcifyusers/ids'", "def sepcifyusers_ids(")):
        block = src[src.index("@bp.route(" + route):src.index(fn)]
        assert block.splitlines()[1:] == ["@jwt_required()", "@required_roles('clints_sepcifyUser')"], route
