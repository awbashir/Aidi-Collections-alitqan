"""Stream B: coexistence of the plan and promise allocation triggers under concurrency.

Lock order everywhere: the case row (FOR NO KEY UPDATE) first, then the payment row, then the
ledger rows. The void paths take no case lock. These tests prove:
  * an approval waits for the case lock and then fills BOTH ledgers;
  * the plan guard's payment FOR SHARE lock closes the allocate-vs-legacy-reject race, in both
    orders, with no deadlock although the allocating transaction holds the case lock;
  * parallel v2 approvals / reversals, legacy approvals / rejections / deletions and manual plan
    allocations on ONE case, through the real app.py, never deadlock and leave both ledgers exact.
"""
from __future__ import annotations

import datetime as dt
import threading
import time
import uuid

import psycopg2

from conftest import MAIN_DB, db_url
from test_plans_support import *  # noqa: F401,F403

DAY_ = dt.timedelta(days=1)


def _conn():
    c = psycopg2.connect(db_url(MAIN_DB))
    c.autocommit = False
    return c


def test_approval_waits_for_the_case_lock_then_fills_both_ledgers(pc, db):
    case = make_case(db, 10_000)
    promise = make_promise(db, case, 500, today())
    plan = insert_plan(db, case, "300.00", count=3, frequency="weekly")
    pay = make_payment(db, case, 200)
    rev = revision(db, "Payments", pay)
    holder = _conn()
    out = {}
    try:
        with holder.cursor() as cur:
            cur.execute("SELECT 1 FROM clints WHERE id = %s FOR NO KEY UPDATE", (case,))

        def approve():
            r = pc.fresh().post(f"/v2/payments/{pay}/approve", P.super, {"expected_revision": rev})
            out["status"] = r.status_code

        t = threading.Thread(target=approve)
        t.start()
        time.sleep(0.8)
        assert t.is_alive(), "the approval must wait for the case row"
        holder.commit()
        t.join(30)
    finally:
        holder.close()
    assert out == {"status": 200}
    assert q(db, "SELECT promise_id::text, amount FROM promise_allocations WHERE payment_id=%s", (pay,)) == \
        [(promise, D("200.00"))]
    assert [a[2] for a in plan_allocs(db, payment_id=pay)] == [D("100.00"), D("100.00")]
    assert plan_state(db, plan)[1] == D("200.00")


def test_manual_allocation_and_legacy_reject_serialize_on_the_payment_row(db):
    case = make_case(db, 10_000)
    pay = make_payment(db, case, 100, status=True)                     # approved before the plan
    plan = insert_plan(db, case, "100.00", count=1)
    inst = installments(db, plan)[0][0]
    ins = ("INSERT INTO plan_allocations (plan_id, installment_id, payment_id, case_id, amount, source, note) "
           "VALUES (%s, %s, %s, %s, 100, 'manual', 'race test')")
    rej = 'UPDATE "Payments" SET approval_status=false, approval_by=%s WHERE id=%s'

    # 1) the allocation (holding the case lock and the payment FOR SHARE) goes first; the legacy
    #    reject waits for it, then voids it. No deadlock: the reject never asks for the case row.
    alloc, reject = _conn(), _conn()
    out = {}
    try:
        with alloc.cursor() as cur:
            cur.execute(ins, (plan, inst, pay, case))

        def second():
            try:
                with reject.cursor() as cur:
                    cur.execute(rej, (P.sup, pay))
                reject.commit()
                out["ok"] = True
            except psycopg2.Error as e:
                reject.rollback()
                out["code"] = e.pgcode

        t = threading.Thread(target=second)
        t.start()
        time.sleep(0.8)
        assert t.is_alive(), "the reject must wait for the allocating transaction"
        alloc.commit()
        t.join(30)
    finally:
        alloc.close()
        reject.close()
    assert out == {"ok": True}
    assert q(db, "SELECT voided_by::text, void_reason FROM plan_allocations WHERE payment_id=%s", (pay,)) == \
        [(P.sup, "payment no longer approved")]

    # 2) the reject goes first; the allocation waits on the payment row, then sees it rejected
    pay2 = make_payment(db, case, 100, status=True)
    reject, alloc = _conn(), _conn()
    out = {}
    try:
        with reject.cursor() as cur:
            cur.execute(rej, (P.sup, pay2))

        def second():
            try:
                with alloc.cursor() as cur:
                    cur.execute(ins, (plan, inst, pay2, case))
                alloc.commit()
                out["ok"] = True
            except psycopg2.Error as e:
                alloc.rollback()
                out["code"] = e.pgcode

        t = threading.Thread(target=second)
        t.start()
        time.sleep(0.8)
        assert t.is_alive(), "the allocation must wait for the rejecting transaction"
        reject.commit()
        t.join(30)
    finally:
        alloc.close()
        reject.close()
    assert out == {"code": "CW103"}
    assert ledger_invariants(db, case) == []


def _deadlocks(db):
    q(db, "SELECT pg_stat_clear_snapshot()")
    return q1(db, "SELECT deadlocks FROM pg_stat_database WHERE datname = current_database()")[0]


def _storm_round(db):
    """One case; 19 conflicting money calls released together through the real app.py."""
    t = today()
    case = make_case(db, 1_000_000)
    early = [make_payment(db, case, 100, status=True) for _ in range(2)]        # before plan/promises
    promises = [make_promise(db, case, 2000, t + k * DAY_) for k in range(3)]
    plan = insert_plan(db, case, "12000.00", count=12, frequency="weekly")
    approved = [make_payment(db, case, 100, status=True) for _ in range(10)]    # auto-allocated to both
    v2_pending = [make_payment(db, case, 100) for _ in range(5)]
    legacy_pending = [make_payment(db, case, 100) for _ in range(5)]
    to_reverse, to_reject, to_delete, untouched = approved[:3], approved[3:5], approved[5:7], approved[7:]
    calls = []
    for p in v2_pending:
        calls.append({"method": "POST", "path": f"/v2/payments/{p}/approve",
                      "json": {"expected_revision": revision(db, "Payments", p)},
                      "headers": {"Idempotency-Key": str(uuid.uuid4())}})
    for p in legacy_pending:
        calls.append({"method": "PUT", "path": f"/payment/approve/{p}"})
    for p in to_reverse:
        calls.append({"method": "POST", "path": f"/v2/payments/{p}/reverse",
                      "json": {"expected_revision": revision(db, "Payments", p), "reason": "storm reversal"},
                      "headers": {"Idempotency-Key": str(uuid.uuid4())}})
    for p in to_reject:
        calls.append({"method": "PUT", "path": f"/payment/reject/{p}", "json": {"approval_comment": "storm"}})
    for p in to_delete:
        calls.append({"method": "DELETE", "path": f"/payments/{p}"})
    for p in early:
        calls.append({"method": "POST", "path": f"/v2/plans/{plan}/allocations",
                      "json": {"payment_id": p, "reason": "storm manual allocation"},
                      "headers": {"Idempotency-Key": str(uuid.uuid4())}})
    res = run_legacy(calls, user=P.super, parallel=True)
    expected = [200] * (len(v2_pending) + len(legacy_pending) + len(to_reverse) + len(to_reject) + len(to_delete)) \
        + [201] * len(early)
    assert [r["status"] for r in res] == expected, res
    assert all(r["body"] is None or r["body"].get("status") is not False for r in res), res

    # exact end state, payment by payment, in both ledgers (capacity is ample on both sides, so
    # every surviving approved payment is fully allocated in each ledger, whatever the order)
    def alloc(table, p):
        return q1(db, f"SELECT coalesce(sum(amount), 0) FROM {table} WHERE payment_id=%s AND voided_at IS NULL",
                  (p,))[0]
    for p in v2_pending + legacy_pending + untouched:
        assert (alloc("plan_allocations", p), alloc("promise_allocations", p)) == (D("100.00"), D("100.00")), p
    for p in to_reverse + to_reject + to_delete:
        assert (alloc("plan_allocations", p), alloc("promise_allocations", p)) == (D("0.00"), D("0.00")), p
    for p in early:
        assert (alloc("plan_allocations", p), alloc("promise_allocations", p)) == (D("100.00"), D("0.00")), p
    assert plan_state(db, plan)[1] == D("1500.00")                 # 5 + 5 + 3 untouched + 2 early
    assert q1(db, "SELECT sum(allocated) FROM v_promise_state WHERE case_id=%s", (case,))[0] == D("1300.00")
    assert ledger_invariants(db, case) == []
    # every void left a trail, in each ledger
    assert q1(db, "SELECT count(*) FROM plan_events WHERE case_id=%s AND event='allocation_voided'", (case,))[0] == \
        q1(db, "SELECT count(*) FROM plan_allocations WHERE case_id=%s AND voided_at IS NOT NULL", (case,))[0]
    assert len(promises) == 3


def test_parallel_approvals_reversals_and_legacy_writes_on_one_case_never_deadlock(db):
    before = _deadlocks(db)
    for _ in range(3):
        _storm_round(db)
    time.sleep(0.5)
    assert _deadlocks(db) == before


def test_negative_control_a_void_path_that_locked_the_case_would_deadlock(db):
    """Why the void paths take no case lock: an approval holds case -> payment; a legacy reject
    holds payment first. If the reject's trigger then asked for the case row, the two would
    deadlock. Reproduced here with plain SQL (no trigger involved) to show the detector fires."""
    case = make_case(db, 10_000)
    pay = make_payment(db, case, 100, status=True)
    approver, rejecter = _conn(), _conn()
    out = {}
    try:
        with approver.cursor() as cur:
            cur.execute("SELECT 1 FROM clints WHERE id=%s FOR NO KEY UPDATE", (case,))     # case first
        with rejecter.cursor() as cur:
            cur.execute('UPDATE "Payments" SET approval_status=false WHERE id=%s', (pay,))  # payment first

        def rejecter_wants_case():
            try:
                with rejecter.cursor() as cur:
                    cur.execute("SELECT 1 FROM clints WHERE id=%s FOR NO KEY UPDATE", (case,))
                out["rejecter"] = "ok"
            except psycopg2.Error as e:
                out["rejecter"] = e.pgcode

        t = threading.Thread(target=rejecter_wants_case)
        t.start()
        time.sleep(0.3)
        try:
            with approver.cursor() as cur:
                cur.execute('SELECT 1 FROM "Payments" WHERE id=%s FOR UPDATE', (pay,))
            out["approver"] = "ok"
        except psycopg2.Error as e:
            out["approver"] = e.pgcode
        t.join(30)
    finally:
        approver.rollback()
        rejecter.rollback()
        approver.close()
        rejecter.close()
    assert sorted(out.values()) == ["40P01", "ok"], out          # one side is killed as a deadlock victim
