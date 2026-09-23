"""Server-side permission checks on legacy routes (decision 2026-09-23).

Each check matches what the legacy UI already shows: a user who sees the button keeps
working, and anyone else calling the route directly gets 403. Runs the REAL app.py
through test_money_support.run_legacy.
"""
from __future__ import annotations

import datetime as dt
import json
import uuid

import pytest

from test_money_support import *  # noqa: F401,F403

FORBIDDEN = {"message": "You are not authorized to access this resource"}


def make_user(db, perms: dict) -> str:
    """A throwaway role with exactly `perms`, and one user in it (cloned from M.col)."""
    role_id, user_id = str(uuid.uuid4()), str(uuid.uuid4())
    q(db, "INSERT INTO roles (id, name, permission) VALUES (%s, %s, %s)",
      (role_id, "gate-" + role_id[:8], json.dumps(perms)))
    q(db, "INSERT INTO users (id, email, frist_name, last_name, mobile, password, user_name, role_id, "
          "nationality_id, branch_id) SELECT %s, email, frist_name, last_name, mobile, password, %s, %s, "
          "nationality_id, branch_id FROM users WHERE id = %s",
      (user_id, "gate-" + user_id[:8], role_id, M.col))
    return user_id


def make_note(db, case_id, author, age_seconds=0) -> str:
    method = q1(db, 'INSERT INTO "ContactMethods" (name) VALUES (%s) RETURNING id::text', ("gate",))[0]
    status = q1(db, 'INSERT INTO "CommentStatuses" (name) VALUES (%s) RETURNING id::text', ("gate",))[0]
    note_id = str(uuid.uuid4())
    created = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=age_seconds)
    q(db, 'INSERT INTO "Comments" (id, clint_id, user_id, "contactMethod_id", comment, comment_status_id, '
          'create_at) VALUES (%s, %s, %s, %s, %s, %s, %s)',
      (note_id, case_id, author, method, "gate note", status, created))
    return note_id


def call(method, path, user, json_body=None):
    return run_legacy([{"method": method, "path": path, "json": json_body}], user=user)[0]


def test_user_without_keys_is_refused_everywhere(db):
    nobody = make_user(db, {})
    case = make_case(db, 1000)
    pay = make_payment(db, case, 100)
    note = make_note(db, case, M.col)
    calls = [
        {"method": "PUT", "path": f"/payment/approve/{pay}"},
        {"method": "PUT", "path": f"/payment/reject/{pay}", "json": {"approval_comment": "x"}},
        {"method": "PUT", "path": f"/payment/{pay}", "json": {"amount": 90}},
        {"method": "DELETE", "path": f"/payments/{pay}"},
        {"method": "PUT", "path": f"/clints/{case}", "json": {"name": "changed"}},
        {"method": "PUT", "path": f"/clints/delinquency/{case}", "json": {"delinquencyReason_id": None}},
        {"method": "DELETE", "path": f"/comments/{note}"},
    ]
    results = run_legacy(calls, user=nobody)
    assert [(r["status"], r["body"]) for r in results] == [(403, FORBIDDEN)] * len(calls)
    # nothing changed
    assert q1(db, 'SELECT approval_status, amount FROM "Payments" WHERE id=%s', (pay,)) == (None, 100.0)
    assert q1(db, 'SELECT count(*) FROM "Comments" WHERE id=%s', (note,)) == (1,)


@pytest.mark.parametrize("key", ["collections_users", "collections_currentUser"])
def test_either_collections_screen_key_may_approve_and_reject(db, key):
    user = make_user(db, {key: True})
    case = make_case(db, 1000)
    ok, rej = make_payment(db, case, 100), make_payment(db, case, 50)
    r1 = call("PUT", f"/payment/approve/{ok}", user)
    r2 = call("PUT", f"/payment/reject/{rej}", user, {"approval_comment": "no slip"})
    assert (r1["status"], r2["status"]) == (200, 200)
    assert q1(db, 'SELECT approval_status FROM "Payments" WHERE id=%s', (ok,)) == (True,)
    # the collections keys do not open edit (for collections_currentUser) or delete
    if key == "collections_currentUser":
        assert call("PUT", f"/payment/{ok}", user, {"amount": 90})["status"] == 403
    assert call("DELETE", f"/payments/{rej}", user)["status"] == 403


def test_accounts_payments_may_edit_and_delete(db):
    user = make_user(db, {"accounts_payments": True})
    case = make_case(db, 1000)
    pay = make_payment(db, case, 100)
    assert call("PUT", f"/payment/{pay}", user, {"amount": 90})["status"] == 200
    assert call("DELETE", f"/payments/{pay}", user)["status"] == 200
    assert call("PUT", f"/payment/approve/{make_payment(db, case, 10)}", user)["status"] == 403


def test_collections_users_may_edit_but_not_delete(db):
    user = make_user(db, {"collections_users": True})
    case = make_case(db, 1000)
    pay = make_payment(db, case, 100)
    assert call("PUT", f"/payment/{pay}", user, {"amount": 90})["status"] == 200
    assert call("DELETE", f"/payments/{pay}", user)["status"] == 403


def test_case_page_key_opens_case_edit_and_delinquency(db):
    user = make_user(db, {"clints_details": True})
    case = make_case(db, 1000)
    assert call("PUT", f"/clints/{case}", user, {"name": "renamed"})["status"] != 403
    assert call("PUT", f"/clints/delinquency/{case}", user, {"delinquencyReason_id": None})["status"] != 403


def test_note_delete_follows_the_timeline_rule(db):
    author = make_user(db, {"clints_details": True})
    deleter = make_user(db, {"clints_comments_delete": True})
    case = make_case(db, 1000)
    fresh = make_note(db, case, author, age_seconds=5)
    old = make_note(db, case, author, age_seconds=600)
    others = make_note(db, case, M.col, age_seconds=5)
    assert call("DELETE", f"/comments/{fresh}", author)["status"] == 200      # own, within 60 s
    assert call("DELETE", f"/comments/{old}", author)["status"] == 403        # own, too old
    assert call("DELETE", f"/comments/{others}", author)["status"] == 403     # someone else's
    assert call("DELETE", f"/comments/{old}", deleter)["status"] == 200       # delete-any key
    assert q1(db, 'SELECT count(*) FROM "Comments" WHERE id IN (%s, %s)', (fresh, old)) == (0,)
