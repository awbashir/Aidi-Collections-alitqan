"""Stream B: repayment plan rules in the database (migrations 0031-0033): schedule integrity,
immutability, one active plan per case, the plan capacity ledger (CW101-CW109), automatic
allocation / voiding on "Payments" changes (independent of the promise ledger), and the
installment / plan state views."""
from __future__ import annotations

import datetime as dt

import psycopg2
import pytest

from test_plans_support import *  # noqa: F401,F403

DAY = dt.timedelta(days=1)


def pgcode(fn):
    with pytest.raises(psycopg2.Error) as e:
        fn()
    return e.value.pgcode


def alloc_sql(db, plan, inst, payment, case, amount, source="manual", note="test"):
    q(db, "INSERT INTO plan_allocations (plan_id, installment_id, payment_id, case_id, amount, source, note) "
          "VALUES (%s, %s, %s, %s, %s, %s, %s)", (plan, inst, payment, case, amount, source, note))


def in_tx(db, *stmts):
    """Run statements in ONE transaction (so the deferred schedule check runs at commit)."""
    db.autocommit = False
    try:
        with db.cursor() as cur:
            for sql, params in stmts:
                cur.execute(sql, params)
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.autocommit = True


# --------------------------------------------------------------------------- view contracts
def test_state_views_columns_are_the_contract(db):
    cols = q(db, """SELECT column_name, data_type FROM information_schema.columns
                     WHERE table_name = 'v_plan_installment_state' ORDER BY ordinal_position""")
    assert [c[0] for c in cols] == ["installment_id", "plan_id", "case_id", "seq", "due_date", "amount",
                                    "allocated", "remaining", "state", "paid_at", "plan_status"]
    assert dict(cols)["due_date"] == "date" and dict(cols)["allocated"] == "numeric"
    cols = [c[0] for c in q(db, """SELECT column_name FROM information_schema.columns
                                    WHERE table_name = 'v_plan_state' ORDER BY ordinal_position""")]
    assert cols[:4] == ["plan_id", "case_id", "status", "state"]
    assert {"overdue_count", "paid_count", "next_due_date", "completed_at", "allocated", "remaining"} <= set(cols)


# --------------------------------------------------------------------------- schedule integrity
def test_schedule_must_match_the_plan(db):
    case = make_case(db, 10_000)
    ok_plan = insert_plan(db, case, "100.00", count=3)
    assert [(i[1], i[3]) for i in installments(db, ok_plan)] == [(1, D("33.34")), (2, D("33.33")), (3, D("33.33"))]
    q(db, "UPDATE repayment_plans SET status='cancelled', cancelled_at=now(), cancel_reason='t' WHERE id=%s", (ok_plan,))

    plan_sql = ("INSERT INTO repayment_plans (id, case_id, frequency, start_date, total_amount, installment_count, "
                "split_mode, outstanding_at_creation) VALUES (%s, %s, 'monthly', %s, %s, %s, 'count', 10000)")
    inst_sql = "INSERT INTO plan_installments (plan_id, case_id, seq, due_date, amount) VALUES (%s,%s,%s,%s,%s)"
    t = today()
    import uuid
    for n, bad in enumerate([
        [(1, t, "50.00")],                                          # sum 50 != 100, count 1 != 2
        [(1, t, "50.00"), (2, t + DAY, "49.99")],                   # one halala short
        [(1, t + DAY, "50.00"), (2, t + 2 * DAY, "50.00")],         # seq 1 not on start_date
        [(1, t, "50.00"), (2, t, "50.00")],                         # due dates not increasing
        [],                                                         # no schedule at all
    ]):
        pid = str(uuid.uuid4())
        stmts = [(plan_sql, (pid, case, t, "100.00", 2))] + [(inst_sql, (pid, case, s, dd, a)) for s, dd, a in bad]
        assert pgcode(lambda: in_tx(db, *stmts)) == "CW109", n
    pid = str(uuid.uuid4())
    other = make_case(db, 100)
    assert pgcode(lambda: in_tx(db, (plan_sql, (pid, case, t, "100.00", 2)),
                                (inst_sql, (pid, other, 1, t, "50.00")))) == "CW109"      # wrong case
    assert pgcode(lambda: in_tx(db, (plan_sql, (pid, case, t, "100.00", 2)),
                                (inst_sql, (pid, case, 3, t, "50.00")))) == "CW109"       # seq > count
    # an extra installment for an existing plan is refused at once (unique seq / seq bound)
    live = insert_plan(db, case, "100.00", count=2)
    assert pgcode(lambda: q(db, inst_sql, (live, case, 3, t + 90 * DAY, "1.00"))) == "CW109"
    assert pgcode(lambda: q(db, inst_sql, (live, case, 2, t + 90 * DAY, "1.00"))) == "23505"


def test_plans_installments_and_events_are_immutable(db):
    case = make_case(db, 1000)
    plan = insert_plan(db, case, "300.00", count=3)
    inst = installments(db, plan)[0][0]
    assert pgcode(lambda: q(db, "UPDATE repayment_plans SET total_amount = 1 WHERE id=%s", (plan,))) == "CW108"
    assert pgcode(lambda: q(db, "UPDATE repayment_plans SET note = 'x' WHERE id=%s", (plan,))) == "CW108"
    assert pgcode(lambda: q(db, "DELETE FROM repayment_plans WHERE id=%s", (plan,))) == "42501"
    assert pgcode(lambda: q(db, "UPDATE plan_installments SET amount = 1 WHERE id=%s", (inst,))) == "42501"
    assert pgcode(lambda: q(db, "DELETE FROM plan_installments WHERE id=%s", (inst,))) == "42501"
    assert pgcode(lambda: q(db, "UPDATE repayment_plans SET status='cancelled', cancelled_at=now() "
                                "WHERE id=%s", (plan,))) == "23514"                       # reason required
    q(db, "UPDATE repayment_plans SET status='cancelled', cancelled_at=now(), cancel_reason='r' WHERE id=%s", (plan,))
    assert q1(db, "SELECT status, revision FROM repayment_plans WHERE id=%s", (plan,)) == ("cancelled", 1)
    assert pgcode(lambda: q(db, "UPDATE repayment_plans SET cancel_reason='again' WHERE id=%s", (plan,))) == "CW108"
    assert pgcode(lambda: q(db, "UPDATE repayment_plans SET status='active', cancelled_at=NULL, cancelled_by=NULL, "
                                "cancel_reason=NULL WHERE id=%s", (plan,))) == "CW108"      # no un-cancel
    q(db, "INSERT INTO plan_events (plan_id, case_id, event, source) VALUES (%s, %s, 'created', 'api')", (plan, case))
    assert pgcode(lambda: q(db, "UPDATE plan_events SET source='legacy' WHERE plan_id=%s", (plan,))) == "42501"
    assert pgcode(lambda: q(db, "DELETE FROM plan_events WHERE plan_id=%s", (plan,))) == "42501"
    assert pgcode(lambda: q(db, "INSERT INTO plan_events (plan_id, case_id, event, source) "
                                "VALUES (%s, %s, 'cancelled', 'api')", (plan, case))) == "23514"


def test_one_active_plan_per_case(db):
    case = make_case(db, 1000)
    first = insert_plan(db, case, "100.00", count=1)
    assert pgcode(lambda: insert_plan(db, case, "100.00", count=1)) == "23505"
    q(db, "UPDATE repayment_plans SET status='cancelled', cancelled_at=now(), cancel_reason='r' WHERE id=%s", (first,))
    second = insert_plan(db, case, "100.00", count=1)
    assert q1(db, "SELECT count(*) FROM repayment_plans WHERE case_id=%s AND status='active'", (case,))[0] == 1
    assert second != first
    # created cancelled is refused, total above the stored outstanding is refused
    assert pgcode(lambda: q(db, "INSERT INTO repayment_plans (case_id, status, frequency, start_date, total_amount, "
                                "installment_count, split_mode, outstanding_at_creation, cancelled_at, cancel_reason) "
                                "VALUES (%s, 'cancelled', 'weekly', now(), 1, 1, 'count', 1, now(), 'x')",
                                (make_case(db, 1),))) == "CW108"
    assert pgcode(lambda: insert_plan(db, make_case(db, 1000), "100.00", outstanding="99.99")) == "23514"


# --------------------------------------------------------------------------- capacity ledger
def test_plan_capacity_is_enforced_on_both_sides_by_the_database(db):
    case = make_case(db, 10_000)
    other = make_case(db, 10_000)
    plan = insert_plan(db, case, "300.00", count=3, frequency="weekly")
    i1, i2, i3 = [i[0] for i in installments(db, plan)]
    pay = make_payment(db, case, 150, status=True)                      # auto: 100 -> i1, 50 -> i2
    assert plan_allocs(db, payment_id=pay) == [(i1, pay, D("100.00"), "auto"), (i2, pay, D("50.00"), "auto")]
    assert pgcode(lambda: alloc_sql(db, plan, i3, pay, case, "0.01")) == "CW101"          # payment full
    pay2 = make_payment(db, case, 500, status=True)                     # auto: 50 -> i2, 100 -> i3
    assert pgcode(lambda: alloc_sql(db, plan, i1, pay2, case, "0.01")) == "CW102"         # installment full
    assert plan_state(db, plan)[:2] == ("completed", D("300.00"))
    pending = make_payment(db, case, 10)
    q(db, "UPDATE plan_allocations SET voided_at=now(), void_reason='t' WHERE payment_id=%s", (pay2,))
    assert pgcode(lambda: alloc_sql(db, plan, i3, pending, case, 1)) == "CW103"           # not approved
    other_pay = make_payment(db, other, 10, status=True)
    assert pgcode(lambda: alloc_sql(db, plan, i3, other_pay, case, 1)) == "CW105"         # other case
    other_plan = insert_plan(db, other, "10.00", count=1)
    oi = installments(db, other_plan)[0][0]
    assert pgcode(lambda: alloc_sql(db, plan, oi, pay2, case, 1)) == "CW105"              # other plan's installment
    assert pgcode(lambda: alloc_sql(db, plan, i3, "00000000-0000-4000-8000-00000000dead", case, 1)) == "CW106"
    assert pgcode(lambda: alloc_sql(db, plan, i3, pay2, case, 1, note=None)) == "23514"    # manual needs a reason
    assert pgcode(lambda: alloc_sql(db, plan, i3, pay2, case, 0)) == "23514"               # amount > 0
    alloc_sql(db, plan, i3, pay2, case, "100.00")                                           # exactly the rest
    aid = q1(db, "SELECT id FROM plan_allocations WHERE payment_id=%s AND voided_at IS NULL", (pay2,))[0]
    assert pgcode(lambda: q(db, "UPDATE plan_allocations SET amount = 1 WHERE id=%s", (aid,))) == "CW107"
    assert pgcode(lambda: q(db, "DELETE FROM plan_allocations WHERE id=%s", (aid,))) == "42501"
    assert pgcode(lambda: q(db, "UPDATE plan_allocations SET voided_at=now() WHERE id=%s", (aid,))) == "23514"
    q(db, "UPDATE repayment_plans SET status='cancelled', cancelled_at=now(), cancel_reason='r' WHERE id=%s", (plan,))
    q(db, "UPDATE plan_allocations SET voided_at=now(), void_reason='t' WHERE id=%s", (aid,))
    assert pgcode(lambda: alloc_sql(db, plan, i3, pay2, case, 1)) == "CW104"                # plan cancelled
    assert pgcode(lambda: q(db, "UPDATE plan_allocations SET void_reason='x' WHERE id=%s", (aid,))) == "CW107"


# --------------------------------------------------------------------------- auto allocation
def test_both_ledgers_fill_independently_on_one_approval(db):
    t = today()
    case = make_case(db, 100_000)
    promise = make_promise(db, case, 300, t + 3 * DAY)
    plan = insert_plan(db, case, "300.00", count=3)
    insts = [i[0] for i in installments(db, plan)]
    pay = make_payment(db, case, 250)
    q(db, 'UPDATE "Payments" SET approval_status=true, approval_by=%s, approval_at=now() WHERE id=%s', (P.sup, pay))
    # the same 250 is evidence in BOTH ledgers, each with its own capacity
    assert q(db, "SELECT promise_id::text, amount FROM promise_allocations WHERE payment_id=%s", (pay,)) == \
        [(promise, D("250.00"))]
    assert plan_allocs(db, payment_id=pay) == [(insts[0], pay, D("100.00"), "auto"),
                                               (insts[1], pay, D("100.00"), "auto"),
                                               (insts[2], pay, D("50.00"), "auto")]
    ev = q(db, "SELECT event, source, actor_user_id::text, amount FROM plan_events WHERE payment_id=%s ORDER BY id",
           (pay,))
    assert ev == [("allocated", "legacy", P.sup, D("100.00")), ("allocated", "legacy", P.sup, D("100.00")),
                  ("allocated", "legacy", P.sup, D("50.00"))]
    # plans never change the debt: the legacy case amount is untouched and nothing is "paid" twice
    assert q1(db, "SELECT amount FROM clints WHERE id=%s", (case,))[0] == 100_000
    assert [i[5] for i in installments(db, plan)] == ["paid", "paid", "partially_paid"]


def test_auto_allocation_order_insert_and_non_positive(db):
    t = today()
    case = make_case(db, 100_000)
    plan = insert_plan(db, case, "400.00", count=4, frequency="weekly")
    insts = [i[0] for i in installments(db, plan)]
    # a legacy insert of an already approved row allocates too (legacy POST /payment accepts it)
    first = make_payment(db, case, 130, status=True)
    assert plan_allocs(db, payment_id=first) == [(insts[0], first, D("100.00"), "auto"),
                                                 (insts[1], first, D("30.00"), "auto")]
    second = make_payment(db, case, 100, status=True)                  # fills i2 first (oldest due)
    assert plan_allocs(db, payment_id=second) == [(insts[1], second, D("70.00"), "auto"),
                                                  (insts[2], second, D("30.00"), "auto")]
    assert make_payment(db, case, -5, status=True) and plan_allocs(db, plan_id=plan)[-1][1] == second
    zero = make_payment(db, case, 0, status=True)
    assert plan_allocs(db, payment_id=zero) == []
    rejected = make_payment(db, case, 50, status=False)
    assert plan_allocs(db, payment_id=rejected) == []
    # an approved payment with free capacity is NOT auto-filled into a plan created later
    case2 = make_case(db, 1000)
    early = make_payment(db, case2, 100, status=True)
    plan2 = insert_plan(db, case2, "100.00", count=1)
    assert plan_allocs(db, plan_id=plan2) == [] and plan_allocs(db, payment_id=early) == []
    assert t == today()


def test_void_on_reject_delete_move_shrink_and_no_allocation_to_cancelled_plans(db):
    case = make_case(db, 100_000)
    plan = insert_plan(db, case, "1000.00", count=10)
    p_reject = make_payment(db, case, 100, status=True)
    p_delete = make_payment(db, case, 100, status=True)
    p_move = make_payment(db, case, 100, status=True)
    p_shrink = make_payment(db, case, 100, status=True)
    p_grow = make_payment(db, case, 100, status=True)
    assert plan_state(db, plan)[1] == D("500.00")
    # legacy reject of an approved payment: actor = the rejecter
    q(db, 'UPDATE "Payments" SET approval_status=false, approval_by=%s WHERE id=%s', (P.sup, p_reject))
    assert q1(db, "SELECT voided_by::text, void_reason FROM plan_allocations WHERE payment_id=%s", (p_reject,)) == \
        (P.sup, "payment no longer approved")
    q(db, 'DELETE FROM "Payments" WHERE id=%s', (p_delete,))
    assert q1(db, "SELECT voided_at IS NOT NULL, voided_by, void_reason FROM plan_allocations WHERE payment_id=%s",
              (p_delete,)) == (True, None, "payment hard-deleted")
    other = make_case(db, 100_000)
    q(db, 'UPDATE "Payments" SET clint_id=%s WHERE id=%s', (other, p_move))
    assert q1(db, "SELECT void_reason FROM plan_allocations WHERE payment_id=%s", (p_move,))[0] == \
        "payment moved to another case"
    q(db, 'UPDATE "Payments" SET amount=150 WHERE id=%s', (p_grow,))           # grows: kept, not re-filled
    assert [a[2] for a in plan_allocs(db, payment_id=p_grow)] == [D("100.00")]
    q(db, 'UPDATE "Payments" SET amount=99.99 WHERE id=%s', (p_shrink,))       # below its 100 allocated
    assert plan_allocs(db, payment_id=p_shrink) == []
    ev = q(db, "SELECT event, reason FROM plan_events WHERE payment_id=%s ORDER BY id", (p_shrink,))
    assert ev[-1] == ("allocation_voided", "payment amount reduced below its allocations")
    assert plan_state(db, plan)[1] == D("100.00")                               # only p_grow's share left
    # history rows are kept (voided, never deleted)
    assert q1(db, "SELECT count(*) FROM plan_allocations WHERE plan_id=%s", (plan,))[0] == 5
    # a cancelled plan gets no new allocations; its paid installments stay paid
    q(db, "UPDATE repayment_plans SET status='cancelled', cancelled_at=now(), cancel_reason='r' WHERE id=%s", (plan,))
    make_payment(db, case, 100, status=True)
    assert plan_state(db, plan)[:2] == ("cancelled", D("100.00"))
    states = [i[5] for i in installments(db, plan)]
    assert states == ["cancelled"] * 4 + ["paid"] + ["cancelled"] * 5          # p_grow paid the 5th
    # a legacy case delete cascades the payments through the trigger without error
    q(db, "DELETE FROM clints WHERE id=%s", (case,))
    assert q1(db, "SELECT count(*) FROM plan_allocations WHERE plan_id=%s AND voided_at IS NULL", (plan,))[0] == 0


# --------------------------------------------------------------------------- states
def test_installment_states_and_riyadh_today(db):
    t = today()
    assert q1(db, "SELECT cawp_riyadh_today()")[0] == t
    case = make_case(db, 100_000)
    dates = [t - 3 * DAY, t - 2 * DAY, t - DAY, t, t + DAY, t + 2 * DAY, t + 3 * DAY]
    plan = insert_plan(db, case, "700.00", count=7, dates=dates)
    ids = [i[0] for i in installments(db, plan)]
    pay = make_payment(db, case, 1000)
    q(db, 'UPDATE "Payments" SET approval_status=true, approval_by=%s WHERE id=%s', (P.super, pay))
    # auto fills oldest first: 100 each -> everything paid
    assert plan_state(db, plan)[0] == "completed"
    q(db, "UPDATE plan_allocations SET voided_at=now(), void_reason='reset' WHERE plan_id=%s", (plan,))
    pay_id = pay
    alloc_sql(db, plan, ids[0], pay_id, case, "100.00")          # overdue -> paid
    alloc_sql(db, plan, ids[1], pay_id, case, "40.00")           # overdue, partly paid: still overdue
    alloc_sql(db, plan, ids[4], pay_id, case, "0.01")            # tomorrow, partly paid
    states = {i[1]: (i[5], i[4]) for i in installments(db, plan)}
    assert states == {1: ("paid", D("100.00")), 2: ("overdue", D("40.00")), 3: ("overdue", D("0.00")),
                      4: ("due", D("0.00")), 5: ("partially_paid", D("0.01")), 6: ("upcoming", D("0.00")),
                      7: ("upcoming", D("0.00"))}
    row = q1(db, """SELECT state, overdue_count, overdue_amount, oldest_overdue_date, next_due_date, paid_count,
                           allocated, remaining, end_date FROM v_plan_state WHERE plan_id=%s""", (plan,))
    assert row == ("broken", 2, D("160.00"), t - 2 * DAY, t, 1, D("140.01"), D("559.99"), t + 3 * DAY)
    paid_at = q1(db, "SELECT paid_at FROM v_plan_installment_state WHERE installment_id=%s", (ids[0],))[0]
    assert paid_at == q1(db, "SELECT created_at FROM plan_allocations WHERE installment_id=%s AND voided_at IS NULL",
                         (ids[0],))[0]
    assert q1(db, "SELECT paid_at FROM v_plan_installment_state WHERE installment_id=%s", (ids[1],))[0] is None


def test_broken_threshold_is_configurable_and_reported_only(db):
    t = today()
    case = make_case(db, 100_000)
    plan = insert_plan(db, case, "300.00", count=3, dates=[t - 2 * DAY, t - DAY, t + 30 * DAY])
    assert plan_state(db, plan)[:3] == ("broken", D("0.00"), 2)                   # default threshold 2
    db.autocommit = False
    try:
        with db.cursor() as cur:
            cur.execute("SELECT set_config('cawp.plan_broken_threshold', '3', true)")
            cur.execute("SELECT state, cawp_plan_broken_threshold() FROM v_plan_state WHERE plan_id=%s", (plan,))
            assert cur.fetchone() == ("active", 3)
            for bad in ("0", "abc", "-1", ""):
                cur.execute("SELECT set_config('cawp.plan_broken_threshold', %s, true)", (bad,))
                cur.execute("SELECT cawp_plan_broken_threshold()")
                assert cur.fetchone()[0] == 2
            cur.execute("SELECT set_config('cawp.plan_broken_threshold', '1', true)")
            cur.execute("SELECT state FROM v_plan_state WHERE plan_id=%s", (plan,))
            assert cur.fetchone()[0] == "broken"
        db.rollback()
    finally:
        db.autocommit = True
    # "broken" changes nothing: the stored status stays active and payments still allocate
    assert q1(db, "SELECT status FROM repayment_plans WHERE id=%s", (plan,))[0] == "active"
    make_payment(db, case, 200, status=True)
    assert plan_state(db, plan)[:3] == ("active", D("200.00"), 0)


def test_completed_state_and_completed_at(db):
    case = make_case(db, 100_000)
    plan = insert_plan(db, case, "0.03", count=3, frequency="weekly")
    assert [i[3] for i in installments(db, plan)] == [D("0.01")] * 3
    make_payment(db, case, "0.02", status=True)
    assert plan_state(db, plan)[0] == "active"
    last = make_payment(db, case, "5.00", status=True)
    row = q1(db, "SELECT state, completed_at, allocated, remaining FROM v_plan_state WHERE plan_id=%s", (plan,))
    assert row[0] == "completed" and row[2:] == (D("0.03"), D("0.00"))
    assert row[1] == q1(db, "SELECT created_at FROM plan_allocations WHERE payment_id=%s", (last,))[0]
