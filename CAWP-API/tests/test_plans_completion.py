"""Stream B, migration 0038: a fully paid plan closes automatically (stored status `completed`),
no longer blocks a new plan, reopens when it loses evidence, or is flagged when a newer active
plan exists. Covers v2 and legacy paths, the guard, the backfill and concurrency."""
from __future__ import annotations

import threading
import time
import uuid

import psycopg2
import pytest

from conftest import MAIN_DB, db_url
from test_plans_support import *  # noqa: F401,F403


def events(db, plan):
    return q(db, "SELECT event, source, actor_user_id::text, reason FROM plan_events WHERE plan_id=%s "
                 "ORDER BY occurred_at, id", (plan,))


def status(db, plan):
    return q1(db, "SELECT status, completed_at IS NOT NULL, evidence_voided_at IS NOT NULL, revision "
                  "FROM repayment_plans WHERE id=%s", (plan,))


def create_v2(pc, case, total="300", n=3, frequency="weekly"):
    r = pc.post(f"/v2/cases/{case}/plans", P.super, plan_body(total, frequency, installment_count=n))
    assert r.status_code == 201, r.get_json()
    return r.get_json()["data"]["id"]


def reverse(pc, db, pay, user=P.sup, reason="cheque bounced"):
    r = pc.post(f"/v2/payments/{pay}/reverse", user,
                {"expected_revision": revision(db, "Payments", pay), "reason": reason})
    assert r.status_code == 200, r.get_json()


def test_auto_complete_on_the_last_allocation_and_a_new_plan_is_allowed(pc, db):
    case = make_case(db, 10_000)
    plan = create_v2(pc, case)
    approve_v2(pc, db, make_payment(db, case, 200))
    assert status(db, plan) == ("active", False, False, 0)
    last = make_payment(db, case, 150)
    approve_v2(pc, db, last)                                         # 100 of it completes the plan
    assert status(db, plan) == ("completed", True, False, 1)
    completing = q1(db, "SELECT created_at FROM plan_allocations WHERE payment_id=%s", (last,))[0]
    assert q1(db, "SELECT completed_at FROM repayment_plans WHERE id=%s", (plan,))[0] == completing
    ev = events(db, plan)
    assert ev[-1] == ("completed", "api", P.super, None)              # after the allocation that caused it
    assert [e[0] for e in ev] == ["created", "allocated", "allocated", "allocated", "completed"]
    d = pc.get(f"/v2/plans/{plan}", P.super).get_json()["data"]
    assert (d["state"], d["status"], d["completed_with_voided_evidence"]) == ("completed", "completed", False)
    # the completed plan no longer blocks a new one; later approvals go to the new plan only
    newer = create_v2(pc, case, total="500", n=5)
    pay = make_payment(db, case, 60)
    approve_v2(pc, db, pay)
    assert [a[2] for a in plan_allocs(db, payment_id=pay)] == [D("60.00")]
    assert q1(db, "SELECT plan_id::text FROM plan_allocations WHERE payment_id=%s", (pay,))[0] == newer
    lst = pc.get("/v2/plans", P.super, query_string={"case_id": case}).get_json()["data"]
    assert lst["counts_by_state"]["completed"]["count"] == 1 and lst["counts_by_state"]["active"]["count"] == 1
    assert {i["id"] for i in pc.get("/v2/plans", P.super, query_string={"case_id": case, "state": "completed"})
            .get_json()["data"]["items"]} == {plan}


def test_manual_allocation_completes_and_deallocation_reopens(pc, db):
    case = make_case(db, 10_000)
    early = make_payment(db, case, 300, status=True)                  # approved before the plan
    plan = create_v2(pc, case)
    r = pc.post(f"/v2/plans/{plan}/allocations", P.sup, {"payment_id": early, "reason": "receipt 9"})
    assert r.status_code == 201, r.get_json()
    d = r.get_json()["data"]["plan"]
    assert (d["state"], d["status"]) == ("completed", "completed") and d["events"][-1]["event"] == "completed"
    assert d["events"][-1]["actor"]["id"] == P.sup
    aid = r.get_json()["data"]["allocations"][0]["id"]
    r = pc.post(f"/v2/plan-allocations/{aid}/deallocate", P.super, {"reason": "wrong payment"})
    assert r.status_code == 200, r.get_json()
    d = r.get_json()["data"]["plan"]
    assert (d["state"], d["status"], d["completed_at"]) == ("active", "active", None)
    last = d["events"][-1]
    assert (last["event"], last["reason"], last["actor"]["id"]) == ("reopened_unpaid", "wrong payment", P.super)
    assert [e["event"] for e in d["events"]][-2:] == ["deallocated", "reopened_unpaid"]


def test_reversal_reopens_a_completed_plan(pc, db):
    case = make_case(db, 10_000)
    plan = create_v2(pc, case)
    pay = make_payment(db, case, 300)
    approve_v2(pc, db, pay)
    assert status(db, plan)[0] == "completed"
    reverse(pc, db, pay)
    assert status(db, plan) == ("active", False, False, 2)
    ev = events(db, plan)
    assert ev[-1] == ("reopened_unpaid", "api", P.sup, "payment reversed: cheque bounced")
    assert [e[0] for e in ev[-4:]] == ["allocation_voided"] * 3 + ["reopened_unpaid"]   # reopened once
    assert plan_state(db, plan)[:2] == ("active", D("0.00"))
    # it completes again with new evidence
    approve_v2(pc, db, make_payment(db, case, 300))
    assert status(db, plan)[:2] == ("completed", True)
    assert ledger_invariants(db, case) == []


def test_reversal_with_a_newer_active_plan_flags_the_completed_plan(pc, db):
    case = make_case(db, 10_000)
    old = create_v2(pc, case)
    p1 = make_payment(db, case, 200)
    p2 = make_payment(db, case, 100)
    approve_v2(pc, db, p1)
    approve_v2(pc, db, p2)
    newer = create_v2(pc, case, total="500", n=5)
    reverse(pc, db, p1)
    assert status(db, old) == ("completed", True, True, 2)
    assert status(db, newer)[0] == "active"
    ev = q1(db, "SELECT event, actor_user_id::text, reason, details->>'active_plan_id' FROM plan_events "
                "WHERE plan_id=%s ORDER BY occurred_at DESC, id DESC LIMIT 1", (old,))
    assert ev == ("completed_evidence_voided", P.sup, "payment reversed: cheque bounced", newer)
    # flagged once: losing more evidence adds only the void events
    reverse(pc, db, p2)
    assert q1(db, "SELECT count(*) FROM plan_events WHERE plan_id=%s AND event='completed_evidence_voided'",
              (old,))[0] == 1
    d = pc.get(f"/v2/plans/{old}", P.super).get_json()["data"]
    assert (d["state"], d["completed_with_voided_evidence"]) == ("completed", True)
    assert d["evidence_voided_at"] is not None and d["allocated"] == "0.00"
    assert {i["state"] for i in d["installments"]} <= {"overdue", "due", "upcoming"}     # truthful installments
    flagged = pc.get("/v2/plans", P.super, query_string={"case_id": case, "evidence_voided": "true"}).get_json()["data"]
    assert [i["id"] for i in flagged["items"]] == [old]
    assert pc.get("/v2/plans", P.super, query_string={"evidence_voided": "maybe"}).status_code == 422
    assert ledger_invariants(db, case) == []


def test_guard_allows_only_the_state_machine(db):
    case = make_case(db, 10_000)
    plan = insert_plan(db, case, "100.00", count=1)
    pgc = lambda sql: _pgcode(db, sql, (plan,))  # noqa: E731
    # completed needs full allocation; reopen / flag need missing evidence
    assert pgc("UPDATE repayment_plans SET status='completed', completed_at=now() WHERE id=%s") == "CW108"
    make_payment(db, case, 100, status=True)
    assert status(db, plan)[0] == "completed"
    assert pgc("UPDATE repayment_plans SET status='active', completed_at=NULL WHERE id=%s") == "CW108"
    assert pgc("UPDATE repayment_plans SET evidence_voided_at=now() WHERE id=%s") == "CW108"
    assert pgc("UPDATE repayment_plans SET status='cancelled', completed_at=NULL, cancelled_at=now(), "
               "cancel_reason='x' WHERE id=%s") == "CW108"                         # a completed plan is closed
    assert pgc("UPDATE repayment_plans SET completed_at=now() WHERE id=%s") == "CW108"
    assert pgc("UPDATE repayment_plans SET status='active', completed_at=now() WHERE id=%s") == "CW108"


def _pgcode(db, sql, params):
    with pytest.raises(psycopg2.Error) as e:
        q(db, sql, params)
    return e.value.pgcode


# --------------------------------------------------------------------------- real app.py
def test_legacy_approve_reject_and_delete_complete_reopen_and_flag(db):
    case = make_case(db, 10_000)
    plan = insert_plan(db, case, "300.00", count=3, frequency="weekly")
    p1, p2 = make_payment(db, case, 300), make_payment(db, case, 300)
    key = str(uuid.uuid4())
    res = run_legacy([
        {"method": "PUT", "path": f"/payment/approve/{p1}"},                                   # completes
        {"method": "PUT", "path": f"/payment/reject/{p1}", "json": {"approval_comment": "fake slip"}},  # reopens
        {"method": "PUT", "path": f"/payment/approve/{p2}"},                                   # completes again
        {"method": "POST", "path": f"/v2/cases/{case}/plans", "json": plan_body("500", "weekly", installment_count=5),
         "headers": {"Idempotency-Key": key}},                                                 # allowed now
        {"method": "DELETE", "path": f"/payments/{p2}"},                                       # flags the old plan
    ], user=P.super)
    assert [r["status"] for r in res] == [200, 200, 200, 201, 200], res
    newer = res[3]["body"]["data"]["id"]
    ev = [e[:3] for e in events(db, plan) if e[0] in ("completed", "reopened_unpaid", "completed_evidence_voided")]
    assert ev == [("completed", "legacy", P.super), ("reopened_unpaid", "legacy", P.super),
                  ("completed", "legacy", P.super), ("completed_evidence_voided", "legacy", None)]
    assert status(db, plan)[:3] == ("completed", True, True)
    assert status(db, newer)[0] == "active"
    assert q1(db, "SELECT reason FROM plan_events WHERE plan_id=%s AND event='reopened_unpaid'", (plan,))[0] == \
        "payment no longer approved"
    assert ledger_invariants(db, case) == []


# --------------------------------------------------------------------------- concurrency
def _conn():
    c = psycopg2.connect(db_url(MAIN_DB))
    c.autocommit = False
    return c


def _insert_plan_tx(conn, case, total="50.00"):
    """Insert an active plan (1 installment, due today) in `conn`'s open transaction."""
    pid = str(uuid.uuid4())
    with conn.cursor() as cur:
        cur.execute("INSERT INTO repayment_plans (id, case_id, frequency, start_date, total_amount, installment_count, "
                    "split_mode, outstanding_at_creation) VALUES (%s, %s, 'weekly', %s, %s, 1, 'count', 100000)",
                    (pid, case, today(), total))
        cur.execute("INSERT INTO plan_installments (plan_id, case_id, seq, due_date, amount) VALUES (%s,%s,1,%s,%s)",
                    (pid, case, today(), total))
    return pid


def test_reopen_racing_a_plan_creation_flags_instead_of_failing(pc, db):
    # 1) the new plan is being created (uncommitted) when a legacy reject voids the old plan's
    #    evidence: the reopen waits on the unique index, then flags. No error, no deadlock.
    case = make_case(db, 10_000)
    old = insert_plan(db, case, "100.00", count=1)
    pay = make_payment(db, case, 100, status=True)
    assert status(db, old)[0] == "completed"
    creator, rejecter = _conn(), _conn()
    out = {}
    try:
        newer = _insert_plan_tx(creator, case)

        def reject():
            try:
                with rejecter.cursor() as cur:
                    cur.execute('UPDATE "Payments" SET approval_status=false, approval_by=%s WHERE id=%s', (P.sup, pay))
                rejecter.commit()
                out["ok"] = True
            except psycopg2.Error as e:
                rejecter.rollback()
                out["code"] = e.pgcode

        t = threading.Thread(target=reject)
        t.start()
        time.sleep(0.8)
        assert t.is_alive(), "the reopen must wait for the creating transaction"
        creator.commit()
        t.join(30)
    finally:
        creator.close()
        rejecter.close()
    assert out == {"ok": True}
    assert status(db, old)[:3] == ("completed", True, True) and status(db, newer)[0] == "active"

    # 2) the reopen is first (uncommitted); a v2 create then waits and answers 409 with the
    #    reopened plan, instead of a 500
    case2 = make_case(db, 10_000)
    old2 = insert_plan(db, case2, "100.00", count=1)
    pay2 = make_payment(db, case2, 100, status=True)
    rejecter = _conn()
    res = {}
    try:
        with rejecter.cursor() as cur:
            cur.execute('UPDATE "Payments" SET approval_status=false, approval_by=%s WHERE id=%s', (P.sup, pay2))

        def create():
            r = pc.fresh().post(f"/v2/cases/{case2}/plans", P.super, plan_body("100", installment_count=1))
            res["r"] = (r.status_code, r.get_json().get("code"), (r.get_json().get("error") or {}).get("plan_id"))

        t = threading.Thread(target=create)
        t.start()
        time.sleep(0.8)
        assert t.is_alive(), "the create must wait for the reopening transaction"
        rejecter.commit()
        t.join(30)
    finally:
        rejecter.close()
    assert res["r"] == (409, "plan_already_active", old2)
    assert status(db, old2)[0] == "active"


def test_completion_and_reopen_under_parallel_approvals_and_reversals(db):
    """A small plan that completes and reopens while v2 approvals / reversals and legacy
    approvals / rejections / deletions run in parallel through the real app.py: no deadlock,
    every call answered, both ledgers exact, and the stored status always matches the ledger."""
    def deadlocks():
        q(db, "SELECT pg_stat_clear_snapshot()")
        return q1(db, "SELECT deadlocks FROM pg_stat_database WHERE datname = current_database()")[0]
    before = deadlocks()
    for _ in range(3):
        case = make_case(db, 1_000_000)
        plan = insert_plan(db, case, "500.00", count=5, frequency="weekly")
        approved = [make_payment(db, case, 100, status=True) for _ in range(6)]   # 5 fill it, it completes
        assert status(db, plan)[0] == "completed"
        v2_pending = [make_payment(db, case, 100) for _ in range(4)]
        legacy_pending = [make_payment(db, case, 100) for _ in range(4)]
        calls = [{"method": "POST", "path": f"/v2/payments/{p}/approve",
                  "json": {"expected_revision": revision(db, "Payments", p)},
                  "headers": {"Idempotency-Key": str(uuid.uuid4())}} for p in v2_pending]
        calls += [{"method": "PUT", "path": f"/payment/approve/{p}"} for p in legacy_pending]
        calls += [{"method": "POST", "path": f"/v2/payments/{p}/reverse",
                   "json": {"expected_revision": revision(db, "Payments", p), "reason": "storm"},
                   "headers": {"Idempotency-Key": str(uuid.uuid4())}} for p in approved[:2]]
        calls += [{"method": "PUT", "path": f"/payment/reject/{approved[2]}", "json": {"approval_comment": "x"}},
                  {"method": "DELETE", "path": f"/payments/{approved[3]}"}]
        res = run_legacy(calls, user=P.super, parallel=True)
        assert all(r["status"] == 200 for r in res), res
        assert ledger_invariants(db, case) == []
        full = q1(db, "SELECT plans_fully_allocated(%s)", (plan,))[0]
        st, has_completed_at, flagged, _rev = status(db, plan)
        # the outcome depends on the order (an approval that lands while the plan is completed
        # allocates nothing to it), but the stored status always matches the ledger
        assert not flagged and st == ("completed" if full else "active") and has_completed_at == full
    time.sleep(0.5)
    assert deadlocks() == before


# --------------------------------------------------------------------------- backfill
def test_backfill_completes_fully_paid_active_plans(scratch_db):
    import manage_db
    from conftest import DB_DIR, SEED_SQL, quiet
    name, url = scratch_db
    r = manage_db.Runner(url, DB_DIR, log=quiet)
    r.baseline()
    r.up(target="0037")
    conn = psycopg2.connect(url)
    conn.autocommit = True
    try:
        q(conn, SEED_SQL.read_text())
        full = insert_plan(conn, "00000000-0000-4000-8000-000000000602", "100.00", count=1, created_by=None)
        part = insert_plan(conn, "00000000-0000-4000-8000-000000000601", "500.00", count=1, created_by=None)
        q(conn, 'UPDATE "Payments" SET approval_status=true WHERE id=%s', ("00000000-0000-4000-8000-000000000a01",))
        q(conn, """INSERT INTO "Payments" (clint_id, user_id, collection_type_id, collection_method_id, amount,
                                          payment_date, approval_status)
                   VALUES ('00000000-0000-4000-8000-000000000602', '00000000-0000-4000-8000-000000000502',
                           '00000000-0000-4000-8000-000000000704', '00000000-0000-4000-8000-000000000705',
                           100, now(), true)""")
        assert q1(conn, "SELECT state FROM v_plan_state WHERE plan_id=%s", (full,))[0] == "completed"   # derived
        assert q1(conn, "SELECT status FROM repayment_plans WHERE id=%s", (full,))[0] == "active"
        assert "0038" in r.up()
        assert q1(conn, "SELECT status, completed_at IS NOT NULL FROM repayment_plans WHERE id=%s", (full,)) == \
            ("completed", True)
        assert q1(conn, "SELECT status FROM repayment_plans WHERE id=%s", (part,))[0] == "active"
        assert q(conn, "SELECT event, source FROM plan_events WHERE plan_id=%s ORDER BY id", (full,)) == \
            [("allocated", "legacy"), ("completed", "migration")]
        mig = (DB_DIR / "migrations" / "0038_repayment_plans_auto_complete.sql").read_text()
        q(conn, mig)                                                    # re-run by hand: nothing new
        assert q1(conn, "SELECT count(*) FROM plan_events WHERE plan_id=%s", (full,))[0] == 2
    finally:
        conn.close()
