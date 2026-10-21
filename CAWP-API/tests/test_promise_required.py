"""The promise of a promise-to-pay note (migration 0079, services/promise_required.py): offered
by the wrap-up / manual log (outcome promise_discussed, or a note status with requires_promise),
the legacy POST /comments and the follow-up completion; the sub-status inheritance, the review
(deferred) path and the settings flag. Customer feedback 2026-10-21: the promise is OPTIONAL
(2026-10-20 had made it required): empty = the note is saved without one; given values are
still validated and a refusal writes nothing."""
from __future__ import annotations

import datetime as dt
import uuid
from pathlib import Path

from test_admin_support import A, db, make_user, run_app  # noqa: F401
from test_admin_support import admin_db  # noqa: F401
from test_admin_support import make_case as admin_case
from test_admin_support import q1 as aq1
from test_calling_helpers import (CL, cl, cl_app, cl_client, hdr, insert_attempt, key, make_case,  # noqa: F401
                                  note_body, pgc, q1, reserve, run, team)

RIYADH = dt.timezone(dt.timedelta(hours=3))
CONTACT_METHOD = "00000000-0000-4000-8000-000000000701"     # tests/fixtures/seed.sql


def today():
    return dt.datetime.now(RIYADH).date()


def day(n=1):
    return (today() + dt.timedelta(days=n)).isoformat()


def now_iso():
    return (dt.datetime.now(RIYADH) - dt.timedelta(minutes=5)).isoformat(timespec="seconds")


def new_status(conn, name="T promise", *, requires=True, parent=None, contract=None):
    sid = str(uuid.uuid4())
    run(conn, 'INSERT INTO "CommentStatuses" (id, name, requires_promise, parent_id, contract_id) '
              "VALUES (%s, %s, %s, %s, %s)", sid, name, requires, parent, contract)
    return sid


def post(client, h, url, body):
    r = client.post(url, headers=h, json=body)
    return r.status_code, r.get_json()


def manual(client, hdr, user, case, **over):
    body = {"case_id": case, "started_at": now_iso(), "outcome": "connected", "note": note_body()}
    body.update(over)
    return post(client, hdr(user, key=key()), "/v2/calls/manual", body)


def wrap(client, hdr, pgc, user, attempt, **over):
    rev = q1(pgc, "SELECT revision FROM call_attempts WHERE id = %s", attempt)[0]
    body = {"expected_revision": rev, "outcome": "connected", "note": note_body("spoke")}
    body.update(over)
    return post(client, hdr(user, key=key()), f"/v2/calls/{attempt}/wrapup", body)


def promises_of(pgc, case):
    return q1(pgc, 'SELECT count(*) FROM "paymentDates" WHERE clint_id = %s', case)[0]


def promise_row(pgc, pid):
    return q1(pgc, 'SELECT clint_id::text, user_id::text, amount, source, source_comment_id::text, '
                   "(payment_date AT TIME ZONE 'UTC')::date FROM \"paymentDates\" WHERE id = %s", pid)


# --------------------------------------------------------------------------- wrap-up (PBX)
def test_wrapup_promise_discussed_offers_and_creates_a_promise(cl_client, hdr, team, pgc):
    a = insert_attempt(pgc, team["case1"], team["c1"], state="ended", bridged=True)
    reserve(pgc, a)
    # format checks before any write
    for promise, field in (({"amount": "0", "due_date": day()}, "promise.amount"),
                           ({"amount": "10.123", "due_date": day()}, "promise.amount"),
                           ({"amount": "10", "due_date": "tomorrow"}, "promise.due_date"),
                           ({"amount": "10"}, "promise.due_date")):
        st, body = wrap(cl_client, hdr, pgc, team["c1"], a, outcome="promise_discussed", promise=promise)
        assert st == 422 and field in body["error"], (promise, body)
    st, body = wrap(cl_client, hdr, pgc, team["c1"], a, outcome="promise_discussed",
                    promise={"amount": "10", "due_date": day(-1)})
    assert (st, body["code"]) == (422, "due_date_in_past")
    assert promises_of(pgc, team["case1"]) == 0
    # with the amount and the date: the promise is created and linked, in the wrap-up's transaction
    st, body = wrap(cl_client, hdr, pgc, team["c1"], a, outcome="promise_discussed",
                    promise={"amount": "300.50", "due_date": day(3)})
    assert st == 200, body
    w = body["data"]["wrapup"]
    pid = w["promise_id"]
    assert pid and q1(pgc, "SELECT promise_id::text FROM call_wrapups WHERE attempt_id = %s", a)[0] == pid
    assert promise_row(pgc, pid) == (team["case1"], team["c1"], 300.5, "call_wrapup", w["note_id"],
                                     dt.date.fromisoformat(day(3)))
    assert q1(pgc, "SELECT event, source FROM promise_events WHERE promise_id = %s", pid) == ("created", "api")
    # the promise service's balance rule rolls everything back
    b = insert_attempt(pgc, team["case1"], team["c1"], state="ended", bridged=True)
    st, body = wrap(cl_client, hdr, pgc, team["c1"], b, outcome="promise_discussed",
                    promise={"amount": "999999", "due_date": day()})
    assert (st, body["code"]) == (409, "amount_exceeds_balance")
    assert q1(pgc, "SELECT wrapped_up_at FROM call_attempts WHERE id = %s", b)[0] is None
    assert promises_of(pgc, team["case1"]) == 1
    # 2026-10-21: no promise given (or only empty fields) -> the wrap-up is saved without one
    for promise in (None, {"amount": "", "due_date": ""}):
        c = insert_attempt(pgc, team["case1"], team["c1"], state="ended", bridged=True)
        reserve(pgc, c)
        st, body = wrap(cl_client, hdr, pgc, team["c1"], c, outcome="promise_discussed",
                        **({"promise": promise} if promise is not None else {}))
        assert st == 200 and body["data"]["wrapup"]["promise_id"] is None, (promise, body)
        assert body["data"]["wrapup"]["note_id"]
    assert promises_of(pgc, team["case1"]) == 1


def test_unbridged_promise_discussed_is_refused_for_the_evidence_first(cl_client, hdr, team, pgc):
    a = insert_attempt(pgc, team["case1"], team["c1"], state="ended")
    st, body = wrap(cl_client, hdr, pgc, team["c1"], a, outcome="promise_discussed")
    assert (st, body["code"]) == (409, "conversation_not_evidenced")


def test_link_an_open_promise_instead_of_a_new_one(cl_client, hdr, team, pgc):
    st, body = manual(cl_client, hdr, team["c1"], team["case1"], outcome="promise_discussed",
                      promise={"amount": "100", "due_date": day(2)})
    assert st == 201, body
    first = body["data"]["wrapup"]["promise_id"]
    st, body = manual(cl_client, hdr, team["c1"], team["case1"], outcome="promise_discussed",
                      promise={"promise_id": first})
    assert st == 201, body
    assert body["data"]["wrapup"]["promise_id"] == first and promises_of(pgc, team["case1"]) == 1
    # amount + promise_id together, another case's promise, a cancelled promise: refused
    st, body = manual(cl_client, hdr, team["c1"], team["case1"], outcome="promise_discussed",
                      promise={"promise_id": first, "amount": "5", "due_date": day()})
    assert (st, body["code"]) == (422, "validation_failed")
    st, body = manual(cl_client, hdr, team["c2"], team["case2"], outcome="promise_discussed",
                      promise={"promise_id": first})
    assert (st, body["code"]) == (422, "promise_not_linkable")
    run(pgc, 'UPDATE "paymentDates" SET cancelled_at = now(), cancelled_by = %s, cancel_reason = %s WHERE id = %s',
        team["c1"], "test", first)
    st, body = manual(cl_client, hdr, team["c1"], team["case1"], outcome="promise_discussed",
                      promise={"promise_id": first})
    assert (st, body["code"], body["error"]["promise_id"]) == (422, "promise_not_linkable", "cancelled")


# --------------------------------------------------------------------------- note status (manual log)
def test_manual_log_with_a_promise_status(cl_client, hdr, team, pgc):
    status = new_status(pgc, "T promise manual")
    # optional (2026-10-21): the note is saved without a promise
    st, body = manual(cl_client, hdr, team["c1"], team["case1"], note=note_body(comment_status_id=status))
    assert st == 201 and body["data"]["wrapup"]["promise_id"] is None, body
    assert body["data"]["wrapup"]["note_id"]
    # such a status needs a conversation outcome
    st, body = manual(cl_client, hdr, team["c1"], team["case1"], outcome="no_answer",
                      note=note_body(comment_status_id=status), promise={"amount": "10", "due_date": day()})
    assert (st, body["code"]) == (422, "promise_needs_conversation")
    assert promises_of(pgc, team["case1"]) == 0
    # outcome connected: `agreed` may be omitted (the status is the agreement), never false
    st, body = manual(cl_client, hdr, team["c1"], team["case1"], note=note_body(comment_status_id=status),
                      promise={"agreed": False, "amount": "10", "due_date": day()})
    assert (st, body["code"]) == (422, "promise_not_agreed")
    st, body = manual(cl_client, hdr, team["c1"], team["case1"], note=note_body(comment_status_id=status),
                      promise={"amount": "120", "due_date": today().isoformat()})
    assert st == 201, body
    w = body["data"]["wrapup"]
    row = promise_row(pgc, w["promise_id"])
    assert row[2:5] == (120.0, "call_wrapup", w["note_id"]) and row[5] == today()


def test_offered_promise_needs_no_promises_key(cl_client, hdr, team, pgc):
    """A collector without the promises key: the note right covers an OFFERED promise (a
    promise-to-pay status / promise discussed); an optional one (connected, plain status)
    still needs the key."""
    from test_calling_helpers import new_user
    limited = new_user(pgc, CL.limited_role)
    case = make_case(pgc, limited, team["portfolio"])
    status = new_status(pgc, "T promise limited")
    st, body = manual(cl_client, hdr, limited, case, note=note_body(comment_status_id=status),
                      promise={"amount": "50", "due_date": day()})
    assert st == 201 and body["data"]["wrapup"]["promise_id"], body
    st, body = manual(cl_client, hdr, limited, case, promise={"agreed": True, "amount": "50", "due_date": day()})
    assert (st, body["code"]) == (403, "forbidden")


def test_sub_status_inherits_the_flag(cl_client, hdr, team, pgc):
    """The flag of the main status or of the sub-status counts (seen through the rule that
    such a status needs a conversation outcome)."""
    main = new_status(pgc, "T promise main")
    child = new_status(pgc, "T promise child", requires=False, parent=main)   # a stale row: the main decides
    plain = new_status(pgc, "T plain main", requires=False)
    flagged_child = new_status(pgc, "T flagged child", parent=plain)
    st, body = manual(cl_client, hdr, team["c1"], team["case1"], outcome="no_answer",
                      note=note_body(comment_status_id=main, comment_sub_status_id=child))
    assert (st, body["code"]) == (422, "promise_needs_conversation")
    st, body = manual(cl_client, hdr, team["c1"], team["case1"], outcome="no_answer",
                      note=note_body(comment_status_id=plain, comment_sub_status_id=flagged_child))
    assert (st, body["code"]) == (422, "promise_needs_conversation")
    st, body = manual(cl_client, hdr, team["c1"], team["case1"], outcome="no_answer",
                      note=note_body(comment_status_id=plain))
    assert st == 201
    st, body = manual(cl_client, hdr, team["c1"], team["case1"], note=note_body(comment_status_id=plain))
    assert st == 201 and body["data"]["wrapup"]["promise_id"] is None


def test_other_statuses_and_outcomes_are_unaffected(cl_client, hdr, team, pgc):
    for outcome in ("connected", "no_answer", "busy"):
        st, body = manual(cl_client, hdr, team["c1"], team["case1"], outcome=outcome)
        assert st == 201 and body["data"]["wrapup"]["promise_id"] is None, (outcome, body)
    st, body = manual(cl_client, hdr, team["c1"], team["case1"], note=None)
    assert st == 201
    assert promises_of(pgc, team["case1"]) == 0


# --------------------------------------------------------------------------- review (deferred)
def test_locked_case_keeps_the_promise_in_deferred_actions(cl_client, hdr, team, pgc):
    case = make_case(pgc, team["c1"], team["portfolio"])
    a = insert_attempt(pgc, case, team["c1"], state="ended", bridged=True)
    reserve(pgc, a)
    run(pgc, "UPDATE clints SET is_locked = true WHERE id = %s", case)
    st, body = wrap(cl_client, hdr, pgc, team["c1"], a, outcome="promise_discussed",
                    promise={"amount": "75.5", "due_date": day(4)})
    assert st == 200, body
    assert body["data"]["wrapup"]["needs_review"] is True
    row = q1(pgc, "SELECT review_reason, promise_id, deferred_actions FROM call_wrapups WHERE attempt_id = %s", a)
    assert row[0] == "case_locked" and row[1] is None
    assert row[2]["promise"] == {"agreed": True, "amount": "75.50", "due_date": day(4)}
    assert promises_of(pgc, case) == 0
    # a deleted case: only the facts are kept, no promise is asked for
    gone = make_case(pgc, team["c1"], team["portfolio"])
    b = insert_attempt(pgc, gone, team["c1"], state="ended", bridged=True)
    reserve(pgc, b)
    run(pgc, "DELETE FROM clints WHERE id = %s", gone)
    st, body = wrap(cl_client, hdr, pgc, team["c1"], b, outcome="promise_discussed", note={"text": "وعد"})
    assert st == 200 and body["data"]["wrapup"]["review_reason"] == "case_deleted", body


# --------------------------------------------------------------------------- legacy POST /comments
def legacy_note(case, status, user, **extra):
    body = {"clint_id": case, "user_id": user, "contactMethod_id": CONTACT_METHOD, "comment": "T promise note",
            "comment_status_id": status}
    body.update(extra)
    return body


def test_legacy_note_form(db):
    collector = make_user(db, {"clints": True})
    status = new_status(db, "T legacy promise")
    child = new_status(db, "T legacy promise child", parent=status)
    plain = new_status(db, "T legacy plain", requires=False)
    case = admin_case(db, 1000, owner=collector)
    tomorrow = day()
    steps = [{"op": "mint", "user": collector, "as": "T"}]
    calls = [
        legacy_note(case, status, collector),                                                     # 0 no promise
        legacy_note(case, status, collector, promise_amount="abc", promise_date=tomorrow),        # 1 bad amount
        legacy_note(case, status, collector, promise_amount="10", promise_date="2020-01-01"),     # 2 past
        legacy_note(case, status, collector, promise_amount="5000", promise_date=tomorrow),       # 3 > balance
        legacy_note(case, status, collector, promise_amount="250", promise_date=tomorrow),        # 4 ok
        legacy_note(case, status, collector, comment_sub_status_id=child, promise_amount="100",
                    promise_date=tomorrow),                                                       # 5 ok + sub
        legacy_note(case, plain, collector, promise_amount="1", promise_date=tomorrow),           # 6 plain: ignored
    ]
    steps += [{"op": "call", "token": "T", "method": "POST", "path": "/comments", "json": c} for c in calls]
    res = [r for r in run_app(steps) if r["op"] == "call"]
    assert [r["status"] for r in res] == [200, 201, 201, 201, 200, 200, 200], res
    assert [r["body"]["error"] for r in res[1:4]] == ["validation_failed", "due_date_in_past",
                                                      "amount_exceeds_balance"]
    assert all(r["body"]["status"] is False and r["body"]["message"] for r in res[1:4])
    # optional (2026-10-21): a promise-to-pay note without the fields is saved as before
    assert res[0]["body"]["comment_status_id"] == status and "promise" not in res[0]["body"]
    ok, with_sub, plain_note = res[4]["body"], res[5]["body"], res[6]["body"]
    assert ok["promise"]["amount"] == "250.00" and ok["promise"]["due_date"] == tomorrow
    assert ok["promise"]["source"] == "note" and ok["promise"]["source_comment_id"] == ok["id"]
    assert with_sub["comment_sub_status_id"] == child and with_sub["promise"]["source_comment_id"] == with_sub["id"]
    assert "promise" not in plain_note
    # refused notes wrote nothing; four notes, two promises
    assert aq1(db, 'SELECT count(*) FROM "Comments" WHERE clint_id = %s', (case,))[0] == 4
    assert aq1(db, 'SELECT count(*), sum(amount) FROM "paymentDates" WHERE clint_id = %s', (case,)) == (2, 350.0)


# --------------------------------------------------------------------------- follow-up completion
def test_follow_up_completion(db):
    collector = make_user(db, {"clints": True, "follow_ups": True})
    status = new_status(db, "T follow-up promise")
    plain = new_status(db, "T follow-up plain", requires=False)
    note = {"text": "will pay", "comment_status_id": status, "contact_method_id": CONTACT_METHOD}

    def open_follow_up():
        case = admin_case(db, 1000, owner=collector)
        run(db, 'INSERT INTO "Comments" (clint_id, user_id, "contactMethod_id", comment, comment_status_id, '
                "next_contact_date) VALUES (%s, %s, %s, 'call back', %s, now() + interval '1 day')",
            case, collector, CONTACT_METHOD, plain)
        fu, rev = aq1(db, "SELECT id::text, revision FROM follow_ups WHERE case_id = %s AND status = 'open'", (case,))
        return case, fu, rev

    def call(fu, body):
        return {"op": "call", "token": "T", "method": "POST", "path": f"/v2/follow-ups/{fu}/complete", "json": body,
                "headers": {"Idempotency-Key": key()}}
    case, fu, rev = open_follow_up()
    case2, fu2, rev2 = open_follow_up()
    base = {"expected_revision": rev, "outcome_note": note, "no_further_action_reason": "promised"}
    res = [r for r in run_app([{"op": "mint", "user": collector, "as": "T"},
                               call(fu, {**base, "outcome_note": {**note, "comment_status_id": plain},
                                         "promise": {"amount": "1", "due_date": day()}}),          # 0 not allowed
                               call(fu, {**base, "promise": {"amount": "400", "due_date": day(5)}}),  # 1 ok
                               call(fu2, {**base, "expected_revision": rev2}),                    # 2 no promise: ok
                               ]) if r["op"] == "call"]
    assert (res[0]["status"], res[0]["body"]["code"]) == (422, "promise_not_allowed"), res[0]
    assert res[1]["status"] == 200, res[1]
    data = res[1]["body"]["data"]
    assert data["promise"]["amount"] == "400.00" and data["promise"]["source"] == "follow_up"
    assert data["promise"]["source_comment_id"] == data["outcome_comment_id"]
    assert aq1(db, 'SELECT count(*) FROM "paymentDates" WHERE clint_id = %s', (case,))[0] == 1
    # optional (2026-10-21): completed with the promise-to-pay status and no promise
    assert res[2]["status"] == 200 and "promise" not in res[2]["body"]["data"], res[2]
    assert aq1(db, 'SELECT count(*) FROM "paymentDates" WHERE clint_id = %s', (case2,))[0] == 0


# --------------------------------------------------------------------------- settings flag
SETTINGS = {"settings_comments_status": True, "settings_comments_status_add": True,
            "settings_comments_status_edit": True}


def test_settings_flag_round_trip_and_inheritance(db):
    admin = make_user(db, SETTINGS)
    tag = uuid.uuid4().hex[:6]
    first = run_app([{"op": "mint", "user": admin, "as": "T"},
                     {"op": "call", "token": "T", "method": "POST", "path": "/comment_status",
                      "json": {"name": "T flag " + tag, "requires_promise": True}}])
    main = first[-1]["body"]
    assert first[-1]["status"] == 200 and main["requires_promise"] is True, first
    mid = main["id"]
    res = [r for r in run_app([
        {"op": "mint", "user": admin, "as": "T"},
        {"op": "call", "token": "T", "method": "POST", "path": "/comment_status",                  # 0 sub: inherits
         "json": {"name": "T flag sub " + tag, "parent_id": mid, "requires_promise": False}},
        {"op": "call", "token": "T", "method": "GET", "path": f"/comment_status/{mid}"},           # 1
        {"op": "call", "token": "T", "method": "PUT", "path": f"/comment_status/{mid}",           # 2 off
         "json": {"requires_promise": False}},
        {"op": "call", "token": "T", "method": "GET", "path": "/comment_status"},                  # 3
        {"op": "call", "token": "T", "method": "PUT", "path": f"/comment_status/{mid}",           # 4 name only
         "json": {"name": "T flag renamed " + tag}},
    ]) if r["op"] == "call"]
    assert [r["status"] for r in res] == [200, 200, 200, 200, 200], res
    sub = res[0]["body"]
    assert sub["requires_promise"] is True and sub["parent_id"] == mid
    assert res[1]["body"]["requires_promise"] is True
    assert res[2]["body"]["requires_promise"] is False
    rows = {r["id"]: r for r in res[3]["body"]["comment_statuses"]}
    assert rows[mid]["requires_promise"] is False and rows[sub["id"]]["requires_promise"] is False
    assert all("requires_promise" in r for r in rows.values())
    assert res[4]["body"]["requires_promise"] is False                 # unchanged when not sent
    assert aq1(db, 'SELECT requires_promise FROM "CommentStatuses" WHERE id = %s', (sub["id"],))[0] is False


def test_migration_flags_the_promise_status(db):
    """0079 set the flag on the live main statuses named «وعد بالسداد»; re-running its UPDATE
    on a new row behaves the same (trimmed name, live main status only)."""
    live = new_status(db, " وعد بالسداد ", requires=False)
    child = new_status(db, "T promise sub", requires=False, parent=live)
    other = new_status(db, "وعد بالسداد تجربة", requires=False)
    sql = (Path(__file__).resolve().parents[2] / "db" / "migrations" / "0079_promise_required_statuses.sql")
    body = sql.read_text(encoding="utf-8")
    start = body.index('UPDATE "CommentStatuses"')
    end = body.index('ALTER TABLE "paymentDates"')
    with db.cursor() as cur:
        cur.execute(body[start:end])
    assert aq1(db, 'SELECT requires_promise FROM "CommentStatuses" WHERE id = %s', (live,))[0] is True
    assert aq1(db, 'SELECT requires_promise FROM "CommentStatuses" WHERE id = %s', (child,))[0] is True
    assert aq1(db, 'SELECT requires_promise FROM "CommentStatuses" WHERE id = %s', (other,))[0] is False

