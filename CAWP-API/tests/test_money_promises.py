"""Stream A: promises, v_promise_state, the allocation ledger (DB-enforced capacity),
auto-allocation, void on reversal, manual allocate/deallocate, promise routes."""
from __future__ import annotations

import datetime as dt
import threading
import time
import uuid

import psycopg2
import pytest

from test_money_support import *  # noqa: F401,F403

UTC = dt.timezone.utc


def approve(mc, db, pid, user=M.super):
    r = mc.post(f"/v2/payments/{pid}/approve", user, {"expected_revision": revision(db, "Payments", pid)})
    assert r.status_code == 200, r.get_json()
    return r.get_json()["data"]


def alloc_sql(db, promise, payment, case, amount):
    q(db, "INSERT INTO promise_allocations (promise_id, payment_id, case_id, amount, source) "
          "VALUES (%s, %s, %s, %s, 'manual')", (promise, payment, case, amount))


def pgcode(fn):
    with pytest.raises(psycopg2.Error) as e:
        fn()
    return e.value.pgcode


# --------------------------------------------------------------------------- the view contract
def test_v_promise_state_columns_are_the_contract(db):
    cols = q(db, """SELECT column_name, data_type, numeric_precision, numeric_scale
                      FROM information_schema.columns WHERE table_name = 'v_promise_state'
                     ORDER BY ordinal_position""")
    assert [c[0] for c in cols] == ["promise_id", "case_id", "recorder_user_id", "due_date", "amount",
                                    "allocated", "state", "cancelled_at", "fulfilled_at"]
    types = {c[0]: c[1:] for c in cols}
    assert types["due_date"][0] == "date"
    assert types["amount"] == ("numeric", 14, 2) and types["allocated"] == ("numeric", 14, 2)
    assert types["state"][0] == "text"


def test_each_state_and_the_riyadh_date_boundary(db):
    today = riyadh_today()
    case = make_case(db, 10_000)
    riyadh_midnight = dt.datetime.combine(today, dt.time.min, tzinfo=UTC) - dt.timedelta(hours=3)
    due_today_riyadh = make_promise(db, case, 100, riyadh_midnight)                       # 21:00Z yesterday
    utc_yesterday_riyadh_today = make_promise(db, case, 100, riyadh_midnight + dt.timedelta(minutes=30))
    due_yesterday = make_promise(db, case, 100, riyadh_midnight - dt.timedelta(seconds=1))  # 20:59:59Z
    future = make_promise(db, case, 100, today + dt.timedelta(days=3))
    s = state(db, due_today_riyadh)
    assert s[0] == "open" and s[3] == today
    assert state(db, utc_yesterday_riyadh_today)[0] == "open"      # a UTC-date rule would say overdue
    s = state(db, due_yesterday)
    assert s[0] == "overdue" and s[3] == today - dt.timedelta(days=1)
    assert state(db, future)[0] == "open"

    pay = make_payment(db, case, 1000, status=True, approver=M.super)   # auto-allocates oldest first
    s = state(db, due_yesterday)
    assert s[0] == "fulfilled" and s[1] == D("100.00") and s[4] is not None
    alloc_time = q1(db, "SELECT created_at FROM promise_allocations WHERE promise_id=%s", (due_yesterday,))[0]
    assert s[4] == alloc_time

    part = make_promise(db, case, 500, today - dt.timedelta(days=10))
    q(db, "UPDATE promise_allocations SET voided_at=now(), void_reason='test' WHERE payment_id=%s", (pay,))
    alloc_sql(db, part, pay, case, 499.99)
    s = state(db, part)
    assert s[0] == "overdue" and s[1] == D("499.99")                   # partly allocated stays overdue

    cancelled = make_promise(db, case, 100, today + dt.timedelta(days=1))
    q(db, """UPDATE "paymentDates" SET cancelled_at=now(), cancelled_by=%s, cancel_reason='client withdrew'
             WHERE id=%s""", (M.super, cancelled))
    s = state(db, cancelled)
    assert s[0] == "cancelled" and s[5] is not None and s[4] is None

    deleted = make_promise(db, case, 100, today)
    q(db, 'UPDATE "paymentDates" SET is_deleted = true WHERE id=%s', (deleted,))
    assert state(db, deleted) is None                                   # legacy deleted rows excluded

    zero = make_promise(db, case, 0, today - dt.timedelta(days=1))
    assert state(db, zero)[0] == "overdue"                              # never fulfilled without evidence


# --------------------------------------------------------------------------- capacity in the DB
def test_capacity_is_enforced_on_both_sides_by_the_database(db):
    today = riyadh_today()
    case = make_case(db, 10_000)
    other_case = make_case(db, 10_000)
    p60 = make_promise(db, case, 60, today)
    p80 = make_promise(db, case, 80, today)
    pay100 = make_payment(db, case, 100, status=True, approver=M.super)   # auto: 60 -> p60, 40 -> p80
    assert active_allocs(db, payment_id=pay100) == [(p60, pay100, D("60.00"), "auto"),
                                                    (p80, pay100, D("40.00"), "auto")]
    assert pgcode(lambda: alloc_sql(db, p80, pay100, case, 0.01)) == "CW001"      # payment full
    pay50 = make_payment(db, case, 50)
    q(db, 'UPDATE "Payments" SET approval_status=true, approval_by=%s WHERE id=%s', (M.super, pay50))
    assert state(db, p80)[0] == "fulfilled"                                      # 40 + 40 auto
    assert active_allocs(db, payment_id=pay50) == [(p80, pay50, D("40.00"), "auto")]
    p30 = make_promise(db, case, 30, today)
    assert pgcode(lambda: alloc_sql(db, p80, pay50, case, 0.01)) == "CW002"      # promise full
    assert pgcode(lambda: alloc_sql(db, p30, pay50, case, 10.01)) == "CW001"     # 10.00 left on pay50
    alloc_sql(db, p30, pay50, case, 10)                                           # exactly the rest

    pending = make_payment(db, case, 10)
    assert pgcode(lambda: alloc_sql(db, p30, pending, case, 1)) == "CW003"
    other_pay = make_payment(db, other_case, 10, status=True, approver=M.super)
    assert pgcode(lambda: alloc_sql(db, p30, other_pay, case, 1)) == "CW005"
    q(db, """UPDATE "paymentDates" SET cancelled_at=now(), cancel_reason='x' WHERE id=%s""", (p30,))
    assert active_allocs(db, promise_id=p30) == [(p30, pay50, D("10.00"), "manual")]  # legacy-style cancel keeps rows
    pay_free = make_payment(db, case, 5, status=True, approver=M.super)
    assert pgcode(lambda: alloc_sql(db, p30, pay_free, case, 1)) == "CW004"
    assert pgcode(lambda: alloc_sql(db, p60, pay_free, case, 0)) == "23514"      # amount > 0

    aid = q1(db, "SELECT id FROM promise_allocations WHERE payment_id=%s LIMIT 1", (pay100,))[0]
    assert pgcode(lambda: q(db, "UPDATE promise_allocations SET amount = 1 WHERE id=%s", (aid,))) == "CW007"
    assert pgcode(lambda: q(db, "DELETE FROM promise_allocations WHERE id=%s", (aid,))) == "42501"
    assert pgcode(lambda: q(db, "UPDATE promise_allocations SET voided_at=now() WHERE id=%s", (aid,))) == "23514"


def test_concurrent_allocations_cannot_overfill_a_payment(db):
    from conftest import MAIN_DB, db_url
    today = riyadh_today()
    case = make_case(db, 10_000)
    pay = make_payment(db, case, 100, status=True, approver=M.super)       # no promises yet: unallocated
    a = make_promise(db, case, 100, today)
    b = make_promise(db, case, 100, today)
    c1, c2 = psycopg2.connect(db_url(MAIN_DB)), psycopg2.connect(db_url(MAIN_DB))
    try:
        cur1 = c1.cursor()
        cur1.execute("INSERT INTO promise_allocations (promise_id, payment_id, case_id, amount, source) "
                     "VALUES (%s, %s, %s, 70, 'manual')", (a, pay, case))
        out = {}

        def second():
            try:
                with c2.cursor() as cur2:
                    cur2.execute("INSERT INTO promise_allocations (promise_id, payment_id, case_id, amount, source) "
                                 "VALUES (%s, %s, %s, 70, 'manual')", (b, pay, case))
                c2.commit()
                out["ok"] = True
            except psycopg2.Error as e:
                c2.rollback()
                out["code"] = e.pgcode

        t = threading.Thread(target=second)
        t.start()
        time.sleep(0.8)
        assert t.is_alive(), "second allocation must wait for the case lock"
        c1.commit()
        t.join(30)
    finally:
        c1.close()
        c2.close()
    assert out == {"code": "CW001"}
    assert q1(db, "SELECT sum(amount) FROM promise_allocations WHERE payment_id=%s AND voided_at IS NULL",
              (pay,))[0] == D("70.00")


# --------------------------------------------------------------------------- auto-allocation / void
def test_auto_allocation_order_and_void_on_reversal(mc, db):
    today = riyadh_today()
    case = make_case(db, 10_000)
    later = make_promise(db, case, 100, today + dt.timedelta(days=10))
    soon = make_promise(db, case, 200, today + dt.timedelta(days=2))
    overdue = make_promise(db, case, 150, today - dt.timedelta(days=3))
    cancelled = make_promise(db, case, 50, today - dt.timedelta(days=5))
    q(db, """UPDATE "paymentDates" SET cancelled_at=now(), cancel_reason='x' WHERE id=%s""", (cancelled,))

    first = make_payment(db, case, 400)
    data = approve(mc, db, first)
    assert [(a["promise_id"], a["amount"], a["source"]) for a in data["allocations"]] == [
        (overdue, "150.00", "auto"), (soon, "200.00", "auto"), (later, "50.00", "auto")]
    assert data["payment"]["allocated"] == "400.00" and data["payment"]["unallocated"] == "0.00"
    assert [state(db, p)[0] for p in (overdue, soon, later, cancelled)] == ["fulfilled", "fulfilled", "open", "cancelled"]
    ev = q(db, "SELECT event, source, actor_user_id::text FROM promise_events WHERE promise_id=%s ORDER BY id", (soon,))
    assert ev[-1] == ("allocated", "api", M.super)

    second = make_payment(db, case, 100)
    data = approve(mc, db, second)
    assert [(a["promise_id"], a["amount"]) for a in data["allocations"]] == [(later, "50.00")]
    assert data["payment"]["unallocated"] == "50.00"
    assert state(db, later)[0] == "fulfilled"

    # reversing the first payment voids its allocations; fulfilled promises revert
    r = mc.post(f"/v2/payments/{first}/reverse", M.sup,
                {"expected_revision": revision(db, "Payments", first), "reason": "cheque bounced"})
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["data"]["voided_allocations"] == 3
    voided = q(db, "SELECT voided_by::text, void_reason FROM promise_allocations WHERE payment_id=%s", (first,))
    assert voided == [(M.sup, "payment reversed: cheque bounced")] * 3          # rows kept
    assert [state(db, p)[0] for p in (overdue, soon, later)] == ["overdue", "open", "open"]
    assert state(db, later)[1] == D("50.00")                                    # the second payment's share
    ev = q(db, "SELECT event, reason FROM promise_events WHERE promise_id=%s ORDER BY id DESC LIMIT 1", (soon,))
    assert ev == [("allocation_voided", "payment reversed: cheque bounced")]


def test_backfilled_promises_start_without_allocations(db):
    # existing approved payments are never allocated retroactively to promises created later
    today = riyadh_today()
    case = make_case(db, 10_000)
    pay = make_payment(db, case, 300, status=True, approver=M.super)
    promise = make_promise(db, case, 100, today)
    assert state(db, promise)[:2] == ("open", D("0.00"))
    assert active_allocs(db, payment_id=pay) == []


# --------------------------------------------------------------------------- create / cancel
def test_create_promise_validation_and_storage(mc, db):
    today = riyadh_today()
    case = make_case(db, 1000)
    make_payment(db, case, 900, status=True, approver=M.super)
    url = f"/v2/cases/{case}/promises"
    r = mc.post(url, M.col, {"amount": "50.00", "due_date": (today - dt.timedelta(days=1)).isoformat()})
    assert r.status_code == 422 and r.get_json()["code"] == "due_date_in_past"
    r = mc.post(url, M.col, {"amount": "100.01", "due_date": today.isoformat()})
    assert r.status_code == 409 and r.get_json()["code"] == "amount_exceeds_balance"
    assert r.get_json()["error"]["outstanding"] == "100.00"
    for bad in ("0", "-5", "1.234", "abc", None, True):
        r = mc.post(url, M.col, {"amount": bad, "due_date": today.isoformat()})
        assert r.status_code == 422, bad
    r = mc.post(url, M.col, {"amount": 12.5, "due_date": "2026-13-01"})
    assert r.status_code == 422
    key = str(uuid.uuid4())
    r = mc.post(url, M.col, {"amount": "100.00", "due_date": today.isoformat()}, key=key)   # today is allowed
    assert r.status_code == 201, r.get_json()
    p = r.get_json()["data"]
    assert (p["state"], p["amount"], p["due_date"], p["recorder"]["id"]) == ("open", "100.00", today.isoformat(), M.col)
    row = q1(db, 'SELECT payment_date, amount, user_id::text FROM "paymentDates" WHERE id=%s', (p["id"],))
    assert row[0] == dt.datetime.combine(today, dt.time.min, tzinfo=UTC)        # like the legacy route
    assert q(db, "SELECT event, source, idempotency_key FROM promise_events WHERE promise_id=%s", (p["id"],)) == \
        [("created", "api", key)]
    assert q1(db, "SELECT action, actor_user_id::text FROM audit_events WHERE entity_id=%s", (p["id"],)) == \
        ("promise.create", M.col)

    locked = make_case(db, 1000, locked=True)
    r = mc.post(f"/v2/cases/{locked}/promises", M.col, {"amount": "1", "due_date": today.isoformat()})
    assert r.status_code == 423 and r.get_json()["code"] == "case_locked"
    other = make_case(db, 1000, owner=M.col2, portfolio=M.other_portfolio)
    r = mc.post(f"/v2/cases/{other}/promises", M.col, {"amount": "1", "due_date": today.isoformat()})
    assert r.status_code == 404


def test_cancel_promise(mc, db):
    today = riyadh_today()
    case = make_case(db, 1000)
    p = make_promise(db, case, 100, today + dt.timedelta(days=1))
    pay = make_payment(db, case, 40, status=True, approver=M.super)       # auto 40 -> p (partial)
    url = f"/v2/promises/{p}/cancel"
    r = mc.post(url, M.super, {"expected_revision": revision(db, "paymentDates", p)})
    assert r.status_code == 422 and r.get_json()["error"] == {"reason": "required"}
    r = mc.post(url, M.super, {"expected_revision": revision(db, "paymentDates", p) + 5, "reason": "x"})
    assert r.status_code == 409 and r.get_json()["code"] == "revision_conflict"
    r = mc.post(url, M.col, {"expected_revision": revision(db, "paymentDates", p), "reason": "x"})
    assert r.status_code == 403                                   # collector lacks promises_cancel
    r = mc.post(url, M.super, {"expected_revision": revision(db, "paymentDates", p), "reason": "debtor disputes"})
    assert r.status_code == 200, r.get_json()
    d = r.get_json()["data"]
    assert d["state"] == "cancelled" and d["cancellation"]["reason"] == "debtor disputes"
    assert d["allocated"] == "0.00"                              # its allocation was voided: capacity freed
    assert active_allocs(db, payment_id=pay) == []
    assert [e["event"] for e in d["events"]][-2:] == ["allocation_voided", "cancelled"]
    r = mc.post(url, M.super, {"expected_revision": revision(db, "paymentDates", p), "reason": "again"})
    assert r.status_code == 409 and r.get_json()["code"] == "promise_not_cancellable"

    full = make_promise(db, case, 10, today)
    make_payment(db, case, 10, status=True, approver=M.super)
    r = mc.post(f"/v2/promises/{full}/cancel", M.super,
                {"expected_revision": revision(db, "paymentDates", full), "reason": "x"})
    assert r.status_code == 409 and r.get_json()["error"] == {"state": "fulfilled"}


# --------------------------------------------------------------------------- manual allocate / deallocate
def test_manual_allocate_and_deallocate(mc, db):
    today = riyadh_today()
    case = make_case(db, 1000)
    pay = make_payment(db, case, 100, status=True, approver=M.super)   # no promise yet -> unallocated
    a = make_promise(db, case, 60, today)
    b = make_promise(db, case, 80, today)
    r = mc.post(f"/v2/promises/{a}/allocations", M.super, {"payment_id": pay, "amount": "60.01"})
    assert r.status_code == 409 and r.get_json()["code"] == "promise_capacity_exceeded"
    r = mc.post(f"/v2/promises/{a}/allocations", M.super, {"payment_id": pay, "note": "agreed on call"})
    assert r.status_code == 201, r.get_json()
    d = r.get_json()["data"]["promise"]
    assert d["state"] == "fulfilled" and d["allocations"][0]["source"] == "manual"
    assert d["allocations"][0]["amount"] == "60.00" and d["allocations"][0]["note"] == "agreed on call"
    r = mc.post(f"/v2/promises/{b}/allocations", M.super, {"payment_id": pay, "amount": "40.01"})
    assert r.status_code == 409 and r.get_json()["code"] == "payment_capacity_exceeded"
    assert r.get_json()["error"] == {"available": "40.00"}
    pending = make_payment(db, case, 10)
    r = mc.post(f"/v2/promises/{b}/allocations", M.super, {"payment_id": pending, "amount": "1"})
    assert r.status_code == 409 and r.get_json()["code"] == "payment_not_allocatable"
    other_pay = make_payment(db, make_case(db, 100), 10, status=True, approver=M.super)
    r = mc.post(f"/v2/promises/{b}/allocations", M.super, {"payment_id": other_pay, "amount": "1"})
    assert r.status_code == 422 and r.get_json()["code"] == "case_mismatch"
    r = mc.post(f"/v2/promises/{b}/allocations", M.col, {"payment_id": pay, "amount": "1"})
    assert r.status_code == 403

    aid = d["allocations"][0]["id"]
    url = f"/v2/promise-allocations/{aid}/deallocate"
    assert mc.post(url, M.super, {}).status_code == 422               # reason required
    r = mc.post(url, M.super, {"reason": "allocated to the wrong promise"})
    assert r.status_code == 200, r.get_json()
    d = r.get_json()["data"]["promise"]
    assert d["state"] == "open" and d["allocated"] == "0.00"
    assert d["allocations"][0]["active"] is False and d["allocations"][0]["voided_by"] == M.super
    assert d["events"][-1]["event"] == "deallocated"
    r = mc.post(url, M.super, {"reason": "again"})
    assert r.status_code == 409 and r.get_json()["code"] == "allocation_already_voided"
    r = mc.post(f"/v2/promises/{b}/allocations", M.super, {"payment_id": pay})   # default = max possible
    assert r.status_code == 201 and r.get_json()["data"]["promise"]["allocated"] == "80.00"
    acts = [x[0] for x in q(db, "SELECT action FROM audit_events WHERE case_id=%s ORDER BY id", (case,))]
    assert acts.count("promise.allocate") == 2 and acts.count("promise.deallocate") == 1


# --------------------------------------------------------------------------- lists
def test_promise_list_filters_totals_and_scope(mc, db):
    today = riyadh_today()
    case = make_case(db, 10_000)
    other = make_case(db, 10_000, owner=M.col2, portfolio=M.other_portfolio)
    o1 = make_promise(db, case, 10.10, today + dt.timedelta(days=1))
    o2 = make_promise(db, case, 20.20, today - dt.timedelta(days=1))
    f1 = make_promise(db, case, 30.30, today - dt.timedelta(days=30))
    make_payment(db, case, 30.30, status=True, approver=M.super)          # fulfils f1 (oldest)
    x = make_promise(db, other, 99, today)

    r = mc.get(f"/v2/promises?case_id={case}", M.super)
    assert r.status_code == 200, r.get_json()
    d = r.get_json()["data"]
    assert d["total"] == 3 and d["totals"] == {"amount": "60.60", "allocated": "30.30", "remaining": "30.30"}
    assert [i["id"] for i in d["items"]] == [f1, o2, o1]                  # due date ascending
    assert d["counts_by_state"]["overdue"] == {"count": 1, "amount": "20.20", "allocated": "0.00"}
    assert d["counts_by_state"]["fulfilled"]["count"] == 1

    d = mc.get(f"/v2/promises?case_id={case}&state=open,overdue", M.super).get_json()["data"]
    assert d["total"] == 2 and d["totals"]["amount"] == "30.30"
    assert d["counts_by_state"]["fulfilled"]["count"] == 1               # tabs keep every state's count

    d = mc.get(f"/v2/promises?case_id={case}&due_from={today.isoformat()}&due_to={today + dt.timedelta(days=5)}",
               M.super).get_json()["data"]
    assert [i["id"] for i in d["items"]] == [o1]

    d = mc.get(f"/v2/promises?recorder_id={M.col}&owner_id={M.col}&page_size=200", M.col).get_json()["data"]
    ids = {i["id"] for i in d["items"]}
    assert {o1, o2} <= ids and x not in ids
    d = mc.get(f"/v2/promises?case_id={other}", M.col).get_json()["data"]    # out of scope: empty
    assert d["total"] == 0 and d["items"] == []
    r = mc.get(f"/v2/promises/{x}", M.col)
    assert r.status_code == 404 and r.get_json()["code"] == "promise_not_found"

    d = mc.get(f"/v2/cases/{case}/promises", M.col).get_json()["data"]
    assert d["counts_by_state"] == {"open": 1, "overdue": 1, "fulfilled": 1, "cancelled": 0}
    f = next(i for i in d["items"] if i["id"] == f1)
    assert f["allocations"][0]["amount"] == "30.30" and f["events"][0]["event"] == "created"

    assert mc.get("/v2/promises?state=late", M.super).status_code == 422
    assert mc.get("/v2/promises", M.none).status_code == 403
    assert mc.get(f"/v2/cases/{case}/promises", M.none).status_code == 403
    assert mc.post(f"/v2/cases/{case}/promises", M.none, {"amount": "1", "due_date": today.isoformat()}).status_code == 403
