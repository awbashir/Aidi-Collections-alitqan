"""Protected employee fields (core/user_privacy.py), through the REAL app.py.

identityNumber / mobile / email need users_details | users_edit; target_* also open with
users_edit_target; the viewer's own record and requests without a JWT are never redacted.
Keys always stay (null values), so response shapes do not change.
"""
from __future__ import annotations

import json
import uuid

from test_admin_support import *  # noqa: F401,F403

IDENT = ("identityNumber", "mobile", "email")
TGT = ("target_amount", "target_percent", "target_is_percentage")


def _victim(db):
    uid = make_user(db, {"dashbord_user": True}, contract=A.contract_a, target=(12345, 0, False))
    q(db, 'UPDATE users SET "identityNumber" = %s WHERE id = %s', ("1098765432", uid))
    return uid


def _viewer(db, perms, super_scope=True):
    role = make_role(db, perms, name="SuperAdmin" if super_scope else None)   # SuperAdmin = unrestricted case scope
    return make_user(db, role_id=role)


def _case_with_history(db, owner, recorder):
    case = make_case(db, 5000, owner=owner)
    make_payment(db, case, 10, "2026-09-10T10:00:00Z", recorder=recorder)
    method = q1(db, 'INSERT INTO "ContactMethods" (name) VALUES (%s) RETURNING id::text', ("priv",))[0]
    status = q1(db, 'INSERT INTO "CommentStatuses" (name) VALUES (%s) RETURNING id::text', ("priv",))[0]
    q(db, 'INSERT INTO "Comments" (clint_id, user_id, "contactMethod_id", comment, comment_status_id) '
          'VALUES (%s, %s, %s, %s, %s)', (case, recorder, method, "privacy note", status))
    q(db, 'INSERT INTO "SepcifyUsers" (user_id, clint_id, create_by) VALUES (%s, %s, %s)', (owner, case, recorder))
    return case


def _user_blocks(results, victim, recorder):
    """Pull the embedded user dicts out of the legacy responses, by route."""
    by = {}
    uesrs, clint, comments, payments, spec = results
    by["uesrs"] = [u for u in uesrs["body"]["users"] if u["id"] == victim]
    by["case_owner"] = [clint["body"]["user"]] if isinstance(clint["body"], dict) and clint["body"].get("user") else []
    rows = comments["body"].get("comments") or comments["body"].get("data") or []
    by["note_author"] = [r["user"] for r in rows if (r.get("user") or {}).get("id") == recorder]
    prow = payments["body"].get("payments") or payments["body"].get("data") or []
    by["payment_recorder"] = [r["user"] for r in prow if (r.get("user") or {}).get("id") == recorder]
    srows = spec["body"].get("sepcifyusers") or spec["body"].get("data") or []
    by["assignee"] = [r["user"] for r in srows if r.get("user")]
    by["assigner"] = [r["create_by"] for r in srows if isinstance(r.get("create_by"), dict)]
    return by


def _calls(case):
    return [{"method": "GET", "path": "/uesrs"},
            {"method": "GET", "path": f"/clints/{case}"},
            {"method": "GET", "path": f"/comments/clint/{case}"},
            {"method": "GET", "path": f"/payments/clint/{case}"},
            {"method": "GET", "path": f"/sepcifyusers/clint/{case}"}]


def test_viewer_without_keys_gets_nulls_everywhere_but_own_record(db):
    victim = _victim(db)
    recorder = _victim(db)                   # a second protected employee (note/payment author)
    case = _case_with_history(db, victim, recorder)
    viewer = _viewer(db, {"clints": True, "clints_details": True, "dashbord_admin": True})
    res = legacy_calls(viewer, _calls(case))
    assert [r["status"] for r in res] == [200] * 5, [r["body"] for r in res]
    blocks = _user_blocks(res, victim, recorder)
    for where in ("uesrs", "case_owner", "note_author", "payment_recorder", "assignee", "assigner"):
        assert blocks[where], (where, res)
        for u in blocks[where]:
            assert all(k in u and u[k] is None for k in IDENT + TGT), (where, u)
            assert u["frist_name"] and u["user_name"]            # everything else intact
    # the viewer's own row in the same list is intact
    me_row = [u for u in res[0]["body"]["users"] if u["id"] == viewer][0]
    assert me_row["mobile"] == "0500009999" and me_row["email"]
    # and nothing protected leaks anywhere in those five bodies
    text = json.dumps([r["body"] for r in res])
    assert "1098765432" not in text


def test_key_holders_see_the_values(db):
    victim = _victim(db)
    case = _case_with_history(db, victim, victim)
    for perms, see_ident in (({"users_details": True}, True), ({"users_edit": True}, True),
                             ({"users_edit_target": True}, False)):
        viewer = _viewer(db, {"clints": True, **perms})
        res = legacy_calls(viewer, _calls(case)[:2])
        row = [u for u in res[0]["body"]["users"] if u["id"] == victim][0]
        owner = res[1]["body"]["user"]
        for u in (row, owner):
            assert u["target_amount"] == 12345.0 and u["target_is_percentage"] is False, perms
            if see_ident:
                assert (u["identityNumber"], u["mobile"]) == ("1098765432", "0500009999"), perms
            else:
                assert all(u[k] is None for k in IDENT), perms


def test_users_details_and_edit_pages_show_full_records(db):
    victim = _victim(db)
    for perms in ({"users_details": True}, {"users_edit": True}):
        r = legacy_calls(_viewer(db, perms, super_scope=False), [{"method": "GET", "path": f"/users/{victim}"}])[0]
        assert r["status"] == 200 and r["body"]["identityNumber"] == "1098765432" and r["body"]["target_amount"] == 12345.0
    # the edit form round trip: GET then PUT the same body keeps the values
    editor = _viewer(db, {"users_edit": True}, super_scope=False)
    got = legacy_calls(editor, [{"method": "GET", "path": f"/users/{victim}"}])[0]["body"]
    body = {k: got[k] for k in ("frist_name", "last_name", "email", "mobile", "identityNumber", "user_name",
                                "role_id", "nationality_id", "branch_id", "contract_id", "target_amount",
                                "target_percent", "target_is_percentage", "is_active")}
    assert legacy_calls(editor, [{"method": "PUT", "path": f"/uesrs/{victim}", "json": body}])[0]["status"] == 200
    assert q1(db, 'SELECT "identityNumber", mobile, target_amount FROM users WHERE id = %s', (victim,)) == \
        ("1098765432", "0500009999", 12345.0)


def test_own_record_login_and_me_are_intact(db):
    victim = _victim(db)
    name = user_name(db, victim)
    res = run_app([{"op": "login", "username": name, "password": PASSWORD, "as": "T"},
                   {"op": "call", "token": "T", "method": "GET", "path": "/v2/me"},
                   {"op": "call", "token": "T", "method": "POST", "path": "/refresh"},
                   {"op": "call", "token": "T", "method": "GET", "path": "/dashbord/collector/v2"}])
    login, me, refresh, dash = res
    assert login["body"]["user"]["identityNumber"] == "1098765432" and login["body"]["user"]["target_amount"] == 12345.0
    assert me["body"]["data"]["user"]["mobile"] == "0500009999"
    assert refresh["body"]["user"]["identityNumber"] == "1098765432"
    assert dash["status"] == 200 and dash["body"]["user"]["target_amount"] == 12345.0   # own KPI card


def test_legacy_dashboards_keep_their_target_columns(db):
    """/dashbord/Allcollector/v2 selects its columns explicitly: the all-collectors KPI card
    (dashbord_admin) still gets every collector's target."""
    victim = _victim(db)
    viewer = _viewer(db, {"dashbord_admin": True}, super_scope=False)
    r = legacy_calls(viewer, [{"method": "GET", "path": "/dashbord/Allcollector/v2?nocache=1"}])[0]
    assert r["status"] == 200
    row = [u for u in r["body"]["users"] if u["id"] == victim][0]
    assert (row["target_amount"], row["target_percent"], row["target_is_percentage"]) == (12345.0, 0.0, False)


def test_redact_rules_and_cache_key(admin_app, db):
    from flask_jwt_extended import create_access_token, verify_jwt_in_request

    from core import lookups, user_privacy
    plain = _viewer(db, {"clints": True}, super_scope=False)
    full = _viewer(db, {"users_details": True}, super_scope=False)
    rec = {"id": "x", "identityNumber": "1", "mobile": "2", "email": "3", "target_amount": 4.0,
           "target_percent": 0.0, "target_is_percentage": False, "frist_name": "n"}
    lookups.invalidate()
    with admin_app.test_request_context("/"):
        assert user_privacy.redact(dict(rec)) == rec                 # no JWT: untouched
        assert user_privacy.cache_key() == "pf"
    for viewer, expect_null, key in ((plain, True, f"p00:{plain}"), (full, False, "pf")):
        with admin_app.app_context():
            tok = create_access_token(identity=viewer)
        with admin_app.test_request_context("/", headers={"Authorization": f"Bearer {tok}"}):
            verify_jwt_in_request()
            out = user_privacy.redact(dict(rec))
            assert (out["mobile"] is None) is expect_null and out["frist_name"] == "n"
            assert set(out) == set(rec)
            assert user_privacy.redact({**rec, "id": viewer})["mobile"] == "2"        # own record
            assert user_privacy.cache_key() == key
