"""Phase 3 legacy admin gates (screen-key rule), through the REAL app.py.

Every newly gated route: 403 with the legacy body and no change without any of its keys;
unchanged behaviour with each key that opens its screen or button today. Lookup lists that
non-admin screens read (the header users list, filters, case tabs) stay open.
"""
from __future__ import annotations

import uuid

import pytest

from test_admin_support import *  # noqa: F401,F403


def one(db, sql, params=None):
    return q1(db, sql, params)[0]


@pytest.fixture
def objs(db):
    o = {
        "victim": make_user(db, {"dashbord_user": True}, contract=A.contract_a),
        "nationality": one(db, "INSERT INTO nationalities (name) VALUES ('gate-nat') RETURNING id::text"),
        "side": one(db, "INSERT INTO updatesides (name) VALUES ('gate-side') RETURNING id::text"),
        "archive_reason": one(db, 'INSERT INTO "archiveReason" (name) VALUES (\'gate-ar\') RETURNING id::text'),
        "delinquency": one(db, 'INSERT INTO "delinquencyReason" (name) VALUES (\'gate-dr\') RETURNING id::text'),
        "comment_status": one(db, 'INSERT INTO "CommentStatuses" (name) VALUES (\'gate-cs\') RETURNING id::text'),
        "method": one(db, 'INSERT INTO "CollectionMethods" (name) VALUES (\'gate-cm\') RETURNING id::text'),
        "contract": one(db, "INSERT INTO contracts (name) VALUES ('gate-contract') RETURNING id::text"),
    }
    o["portfolio"] = one(db, "INSERT INTO portfolios (name, contract_id) VALUES ('gate-pf', %s) RETURNING id::text",
                         (o["contract"],))
    sup_user = make_user(db, {})
    o["supervisor"] = one(db, "INSERT INTO supervisors (user_id) VALUES (%s) RETURNING id::text", (sup_user,))
    return o


def gates(o):
    """(method, path, body, keys that open it today)."""
    v = o["victim"]
    return [
        ("GET", f"/loginLog/user/{v}", None, ["users_details"]),
        ("GET", f"/users/{v}", None, ["users_details", "users_edit"]),
        ("PUT", f"/uesrs/{v}", {}, ["users_edit", "users_details"]),
        ("PUT", f"/user/active/{v}", None, ["users_delete", "users_details"]),
        ("GET", "/uesrs/export", None, ["users"]),
        ("GET", "/roles", None, ["roles", "users_add", "users_edit"]),
        ("GET", f"/roles/{A.role_col}", None, ["roles_edit", "roles_add"]),
        ("GET", "/nationalities", None, ["settings_general_nationalities", "users_add", "users_edit"]),
        ("GET", f"/nationalities/{o['nationality']}", None, ["settings_general_nationalities_edit"]),
        ("POST", "/nationalities", {"name": "gate-new-nat"}, ["settings_general_nationalities_add"]),
        ("PUT", f"/nationalities/{o['nationality']}", {"name": "gate-nat"}, ["settings_general_nationalities_edit"]),
        ("GET", f"/updateSides/{o['side']}", None, ["settings_general_updateSide_edit"]),
        ("POST", "/updateSides", {"name": "gate-new-side"}, ["settings_general_updateSide_add"]),
        ("PUT", f"/updateSides/{o['side']}", {"name": "gate-side"}, ["settings_general_updateSide_edit"]),
        ("PUT", f"/contract/activate/{o['contract']}", None, ["contracts"]),
        ("PUT", f"/contract/deactivate/{o['contract']}", None, ["contracts"]),
        ("POST", "/clints/archive/reson", {"name": "gate-new-ar"}, ["settings_general_archiveReasons_add"]),
        ("GET", f"/clints/archive/reson/{o['archive_reason']}", None, ["settings_general_archiveReasons_edit"]),
        ("PUT", f"/clints/archive/reson/{o['archive_reason']}", {"name": "gate-ar"},
         ["settings_general_archiveReasons_edit"]),
        ("POST", "/clints/delinquency_reasons", {"name": "gate-new-dr"}, ["settings_general_delinquencyReasons_add"]),
        ("GET", f"/clints/delinquency_reasons/{o['delinquency']}", None,
         ["settings_general_delinquencyReasons_edit"]),
        ("PUT", f"/clints/delinquency_reasons/{o['delinquency']}", {"name": "gate-dr"},
         ["settings_general_delinquencyReasons_edit"]),
        ("POST", "/comment_status", {"name": "gate-new-cs"}, ["settings_comments_status_add"]),
        ("GET", f"/comment_status/{o['comment_status']}", None, ["settings_comments_status_edit"]),
        ("PUT", f"/comment_status/{o['comment_status']}", {"name": "gate-cs"}, ["settings_comments_status_edit"]),
        ("POST", "/collection_methods", {"name": "gate-new-cm"}, ["settings_general_collectionMethods_add"]),
        ("GET", f"/collection_methods/{o['method']}", None, ["settings_general_collectionMethods_edit"]),
        ("PUT", f"/collection_methods/{o['method']}", {"name": "gate-cm"}, ["settings_general_collectionMethods_edit"]),
        ("GET", "/supervisors", None, ["supervisors"]),
        ("POST", "/supervisor_contract", {"supervisor_id": o["supervisor"], "contract_id": [o["contract"]]},
         ["supervisors_add"]),
        ("POST", "/supervisor_user", {"supervisor_id": o["supervisor"], "users": [o["victim"]]}, ["supervisors_add"]),
        ("GET", "/portfolios/export", None, ["portfolios", "report_portfolios"]),
        ("GET", f"/portfolio/{o['portfolio']}", None, ["portfolios_edit"]),
        ("PUT", f"/portfolio/{o['portfolio']}", {"name": "gate-pf"}, ["portfolios_edit"]),
    ]


def run_as(users_calls):
    """[(user, [call...])] in ONE app.py process -> {user: [{status, body}]}."""
    steps, order = [], []
    for i, (user, calls) in enumerate(users_calls):
        steps.append({"op": "mint", "user": user, "as": f"U{i}"})
        for c in calls:
            steps.append({"op": "call", "token": f"U{i}", "method": c[0], "path": c[1], "json": c[2]})
            order.append(i)
    results = [r for r in run_app(steps) if r["op"] == "call"]
    out = {}
    for i, r in zip(order, results):
        out.setdefault(i, []).append(r)
    return out


def test_every_new_gate_refuses_a_user_without_the_key(db, objs):
    nobody = make_user(db, {"clints": True, "dashbord": True, "dashbord_user": True, "collections_currentUser": True})
    before = q1(db, "SELECT (SELECT count(*) FROM nationalities), (SELECT count(*) FROM updatesides), "
                    "(SELECT count(*) FROM \"archiveReason\"), (SELECT count(*) FROM supervisor_users), "
                    "(SELECT is_active FROM contracts WHERE id = %s)", (objs["contract"],))
    calls = [(m, p, b) for m, p, b, _ in gates(objs)]
    res = run_as([(nobody, calls)])[0]
    bad = [(c[0], c[1], r["status"]) for c, r in zip(calls, res) if (r["status"], r["body"]) != (403, FORBIDDEN)]
    assert bad == []
    after = q1(db, "SELECT (SELECT count(*) FROM nationalities), (SELECT count(*) FROM updatesides), "
                   "(SELECT count(*) FROM \"archiveReason\"), (SELECT count(*) FROM supervisor_users), "
                   "(SELECT is_active FROM contracts WHERE id = %s)", (objs["contract"],))
    assert before == after


def test_each_screen_key_still_opens_its_route(db, objs):
    plan = []
    for m, p, b, keys in gates(objs):
        for k in keys:
            plan.append((make_user(db, {k: True}), [(m, p, b)], k))
    res = run_as([(u, c) for u, c, _ in plan])
    bad = []
    for i, (_, calls, key) in enumerate(plan):
        r = res[i][0]
        if r["status"] == 403 or r["status"] >= 500:
            bad.append((calls[0][0], calls[0][1], key, r["status"], r["body"]))
    assert bad == []
    # the writes really happened
    assert one(db, "SELECT count(*) FROM nationalities WHERE name = 'gate-new-nat'") >= 1
    assert one(db, "SELECT count(*) FROM supervisor_users WHERE supervisor_id = %s AND user_id = %s",
               (objs["supervisor"], objs["victim"])) >= 1


def test_shared_lookup_lists_stay_open(db):
    """Read by non-admin screens (header bell, filters, case tabs): not gated."""
    nobody = make_user(db, {})
    paths = ["/uesrs", "/branches", "/contracts", "/portfolios", "/comment_status",
             "/clints/archive/reson", "/clints/delinquency_reasons", "/collection_methods", "/updateSides"]
    res = run_as([(nobody, [("GET", p, None) for p in paths])])[0]
    assert [(p, r["status"]) for p, r in zip(paths, res)] == [(p, 200) for p in paths]


# --------------------------------------------------------------------------- user form field blocks
def _form_body(db, uid, **changes):
    """What the legacy form submits: every field, hidden ones with their loaded values."""
    row = q1(db, """SELECT frist_name, last_name, email, mobile, user_name, role_id::text, nationality_id::text,
                           branch_id::text, contract_id::text, extension, target_amount, target_percent,
                           target_is_percentage, is_active, "identityNumber" FROM users WHERE id = %s""", (uid,))
    keys = ["frist_name", "last_name", "email", "mobile", "user_name", "role_id", "nationality_id", "branch_id",
            "contract_id", "extension", "target_amount", "target_percent", "target_is_percentage", "is_active",
            "identityNumber"]
    body = dict(zip(keys, row))
    body["password"] = ""
    body.update(changes)
    return body


def test_user_form_blocks_follow_the_general_and_target_keys(db):
    victim = make_user(db, {"dashbord_user": True}, contract=A.contract_a, target=(20000, 0, False))
    plain = make_user(db, {"users_edit": True})
    general = make_user(db, {"users_edit": True, "users_edit_general": True})
    target = make_user(db, {"users_edit": True, "users_edit_target": True})
    toggler = make_user(db, {"users_edit": True, "users_delete": True})
    same = _form_body(db, victim)
    same_strings = {**same, "target_amount": "20000", "target_percent": "0"}      # text inputs
    res = run_as([
        (plain, [("PUT", f"/uesrs/{victim}", same),                               # unchanged hidden blocks
                 ("PUT", f"/uesrs/{victim}", same_strings),
                 ("PUT", f"/uesrs/{victim}", {**same, "target_amount": 25000}),
                 ("PUT", f"/uesrs/{victim}", {**same, "role_id": A.role_other}),
                 ("PUT", f"/uesrs/{victim}", {**same, "mobile": "0599999999"}),
                 ("PUT", f"/uesrs/{victim}", {**same, "password": "x"}),
                 ("PUT", f"/uesrs/{victim}", {**same, "is_active": False})]),
        (general, [("PUT", f"/uesrs/{victim}", {**same, "mobile": "0599999999"}),
                   ("PUT", f"/uesrs/{victim}", {**same, "mobile": "0599999999", "target_amount": 1})]),
        (target, [("PUT", f"/uesrs/{victim}", {**same, "mobile": "0599999999", "target_amount": 30000})]),
        (toggler, [("PUT", f"/uesrs/{victim}", {**same, "mobile": "0599999999", "target_amount": 30000,
                                                "is_active": False})]),
    ])
    assert [r["status"] for r in res[0]] == [200, 200, 403, 403, 403, 403, 403]
    assert [r["status"] for r in res[1]] == [200, 403]
    # the later bodies carry the mobile saved by `general`, so only their own block changes
    assert [r["status"] for r in res[2]] == [200]
    assert [r["status"] for r in res[3]] == [200]
    assert q1(db, "SELECT mobile, target_amount, is_active FROM users WHERE id = %s", (victim,)) == \
        ("0599999999", 30000.0, False)
    assert [r[3] for r in target_rows(db, victim)][-1] == 30000
