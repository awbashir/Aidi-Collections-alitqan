"""Stream A: legacy writes feed the new model (triggers), and the legacy approval fix.

The legacy handlers run in the REAL app.py, in a subprocess bound to the test database
(test_money_support.run_legacy), so its JWT loaders never leak into this process.
"""
from __future__ import annotations

import datetime as dt
import uuid

from test_money_support import *  # noqa: F401,F403


def audit_rows(db, entity_id):
    return q(db, "SELECT action, actor_user_id::text, before, after, source FROM audit_events "
                 "WHERE entity_id=%s ORDER BY id", (entity_id,))


def events(db, pid):
    return q(db, "SELECT event, source, actor_user_id::text, amount, comment FROM payment_events "
                 "WHERE payment_id=%s ORDER BY id", (pid,))


# --------------------------------------------------------------------------- triggers (SQL-level)
def test_trigger_captures_legacy_create_approve_reject_edit_and_hard_delete(db):
    case = make_case(db, 1000)
    pid = make_payment(db, case, 100)
    assert events(db, pid) == [("submitted", "legacy", M.col, D("100.00"), None)]
    assert audit_rows(db, pid)[0][0] == "payment.legacy.create"

    q(db, 'UPDATE "Payments" SET amount = 120 WHERE id=%s', (pid,))
    a = audit_rows(db, pid)[-1]
    assert a == ("payment.legacy.update", None, {"amount": 100.0}, {"amount": 120.0}, "legacy_trigger")

    q(db, 'UPDATE "Payments" SET approval_status=false, approval_by=%s, approval_at=now(), '
          "approval_comment='no slip' WHERE id=%s", (M.sup, pid))
    a = audit_rows(db, pid)[-1]
    assert a[0] == "payment.legacy.reject" and a[1] == M.sup
    assert a[2]["approval_status"] is None and a[3]["approval_status"] is False
    assert events(db, pid)[-1] == ("rejected", "legacy", M.sup, D("120.00"), "no slip")

    q(db, 'UPDATE "Payments" SET revision = revision WHERE id=%s', (pid,))       # no-op: nothing recorded
    assert len(audit_rows(db, pid)) == 3

    other = make_payment(db, case, 50)
    q(db, 'UPDATE "Payments" SET approval_status=true, approval_by=%s, approval_at=now() WHERE id=%s',
      (M.super, other))
    assert audit_rows(db, other)[-1][:2] == ("payment.legacy.approve", M.super)

    q(db, 'DELETE FROM "Payments" WHERE id=%s', (pid,))
    a = audit_rows(db, pid)[-1]
    assert a[0] == "payment.legacy.delete" and a[3] is None
    assert a[2]["id"] == pid and a[2]["amount"] == 120.0 and a[2]["approval_comment"] == "no slip"
    assert events(db, pid)[-1][:2] == ("deleted", "legacy")
    # the history survives the hard delete (no FK)
    assert len(events(db, pid)) == 4


def test_hard_delete_and_legacy_reject_void_allocations(db):
    today = riyadh_today()
    case = make_case(db, 1000)
    prom = make_promise(db, case, 100, today)
    pay = make_payment(db, case, 60, status=True, approver=M.super)
    pay2 = make_payment(db, case, 40, status=True, approver=M.super)
    assert state(db, prom)[0] == "fulfilled"
    q(db, 'UPDATE "Payments" SET approval_status=false, approval_by=%s WHERE id=%s', (M.sup, pay2))
    assert q1(db, "SELECT voided_by::text, void_reason FROM promise_allocations WHERE payment_id=%s", (pay2,)) \
        == (M.sup, "payment no longer approved")
    assert state(db, prom)[:2] == ("open", D("60.00"))
    q(db, 'DELETE FROM "Payments" WHERE id=%s', (pay,))
    assert q1(db, "SELECT voided_at IS NOT NULL, voided_by, void_reason FROM promise_allocations "
                  "WHERE payment_id=%s", (pay,)) == (True, None, "payment hard-deleted")
    assert state(db, prom)[:2] == ("open", D("0.00"))
    # a legacy case delete cascades through both triggers without error
    q(db, 'DELETE FROM clints WHERE id=%s', (case,))
    assert q1(db, "SELECT count(*) FROM audit_events WHERE case_id=%s AND action='promise.legacy.delete'",
              (case,))[0] == 1


def test_amount_reduced_below_allocations_voids_them(db):
    case = make_case(db, 1000)
    prom = make_promise(db, case, 100, riyadh_today())
    pay = make_payment(db, case, 80, status=True, approver=M.super)
    q(db, 'UPDATE "Payments" SET amount = 90 WHERE id=%s', (pay,))                # grows: allocation kept
    assert active_allocs(db, payment_id=pay) == [(prom, pay, D("80.00"), "auto")]
    q(db, 'UPDATE "Payments" SET amount = 70 WHERE id=%s', (pay,))                # shrinks below 80
    assert active_allocs(db, payment_id=pay) == []


def test_promise_trigger_records_legacy_writes(db):
    case = make_case(db, 1000)
    prom = make_promise(db, case, 100, riyadh_today())
    assert q(db, "SELECT event, source, actor_user_id::text FROM promise_events WHERE promise_id=%s",
             (prom,)) == [("created", "legacy", M.col)]
    q(db, 'UPDATE "paymentDates" SET amount = 150 WHERE id=%s', (prom,))
    assert audit_rows(db, prom)[-1][:4] == ("promise.legacy.update", None, {"amount": 100.0}, {"amount": 150.0})
    q(db, 'DELETE FROM "paymentDates" WHERE id=%s', (prom,))
    assert audit_rows(db, prom)[-1][0] == "promise.legacy.delete"


def test_imported_baseline_backfill(scratch_db):
    """Rows that exist before 0007 get 'imported' events and NO allocations; re-running the
    file adds nothing."""
    import manage_db
    import psycopg2
    from conftest import DB_DIR, SEED_SQL, quiet
    name, url = scratch_db
    r = manage_db.Runner(url, DB_DIR, log=quiet)
    r.baseline()
    r.up(target="0006")
    conn = psycopg2.connect(url)
    conn.autocommit = True
    try:
        q(conn, SEED_SQL.read_text())               # 1 pending payment + 1 promise, pre-0007
        q(conn, "UPDATE \"Payments\" SET approval_status=true, approval_by=%s, approval_at='2026-09-22T10:00:00Z'",
          (Ids.admin,))
        assert "0007" in r.up()
        assert q(conn, "SELECT event, source, actor_user_id::text, amount FROM payment_events ORDER BY id") == [
            ("submitted", "imported", Ids.collector, D("100.00")),
            ("approved", "imported", Ids.admin, D("100.00"))]
        assert q(conn, "SELECT event, source FROM promise_events") == [("created", "imported")]
        assert q1(conn, "SELECT count(*) FROM promise_allocations")[0] == 0
        st, allocated = q1(conn, "SELECT state, allocated FROM v_promise_state")
        assert st in ("open", "overdue") and allocated == D("0.00")          # imported baseline: unallocated
        mig = (DB_DIR / "migrations" / "0007_money_ledger_tables.sql").read_text()
        q(conn, mig)                                                             # manual re-run
        assert q1(conn, "SELECT count(*) FROM payment_events")[0] == 2
        assert q1(conn, "SELECT count(*) FROM promise_events")[0] == 1
    finally:
        conn.close()


# --------------------------------------------------------------------------- real legacy handlers
def test_legacy_approve_fix_exact_numeric_and_decided(db):
    case = make_case(db, 0.3)
    pids = [make_payment(db, case, 0.1) for _ in range(3)]
    over = make_payment(db, case, 0.01)
    calls = [{"method": "PUT", "path": f"/payment/approve/{p}"} for p in pids + [over, pids[0]]]
    calls.append({"method": "PUT", "path": "/payment/approve/00000000-0000-4000-8000-00000000dead"})
    res = run_legacy(calls)
    for r in res[:3]:                    # float loop refused the third 0.1 (0.30000000000000004 > 0.3)
        assert r["status"] == 200 and r["body"]["approval_status"] is True
        assert r["body"]["approval_by"] == M.super
    assert res[3] == {"status": 201, "body": {"message": "المبلغ المدفوع أكبر من المبلغ المطلوب",
                                              "status": False, "error": "amount not valid"}}
    assert res[4] == {"status": 201, "body": {"message": "تم البت في هذه الدفعة مسبقاً",
                                              "status": False, "error": "payment already decided"}}
    assert res[5] == {"status": 201, "body": {"message": "الدفعة غير موجودة", "status": False,
                                              "error": "payment not found"}}
    # captured by the trigger as legacy approvals, with the approver
    assert events(db, pids[0])[-1][:3] == ("approved", "legacy", M.super)
    assert audit_rows(db, pids[0])[-1][:2] == ("payment.legacy.approve", M.super)


def test_legacy_concurrent_approvals_never_exceed_the_debt(db):
    case = make_case(db, 1000)
    pids = [make_payment(db, case, 300) for _ in range(8)]
    res = run_legacy([{"method": "PUT", "path": f"/payment/approve/{p}"} for p in pids], parallel=True)
    ok = [r for r in res if r["status"] == 200]
    refused = [r["body"]["error"] for r in res if r["status"] == 201]
    assert len(ok) == 3 and refused == ["amount not valid"] * 5, res
    total = q1(db, 'SELECT sum(round(amount::numeric,2)) FROM "Payments" WHERE clint_id=%s AND approval_status',
               (case,))[0]
    assert total == D("900.00")


def test_legacy_routes_feed_the_new_model(db, mc):
    today = riyadh_today()
    case = make_case(db, 1000)
    prom = make_promise(db, case, 100, today + dt.timedelta(days=5))
    p_reject = make_payment(db, case, 10)
    p_edit = make_payment(db, case, 20)
    p_delete = make_payment(db, case, 30)
    p_approve = make_payment(db, case, 100)
    res = run_legacy([
        {"method": "PUT", "path": f"/payment/reject/{p_reject}", "json": {"approval_comment": "blurred"}},
        {"method": "PUT", "path": f"/payment/{p_edit}", "json": {"amount": 25}},
        {"method": "DELETE", "path": f"/payments/{p_delete}"},
        {"method": "PUT", "path": f"/payment/approve/{p_approve}"},
        {"method": "POST", "path": "/payment_date",
         "json": {"clint_id": case, "user_id": M.col, "amount": 50,
                  "payment_date": (today + dt.timedelta(days=9)).isoformat()}},
    ], user=M.sup)
    assert [r["status"] for r in res] == [200, 200, 200, 200, 200], res
    assert audit_rows(db, p_reject)[-1][:2] == ("payment.legacy.reject", M.sup)
    assert audit_rows(db, p_edit)[-1][2:4] == ({"amount": 20.0}, {"amount": 25.0})
    assert audit_rows(db, p_delete)[-1][0] == "payment.legacy.delete"
    # legacy approval auto-allocates to the open promise
    assert active_allocs(db, payment_id=p_approve) == [(prom, p_approve, D("100.00"), "auto")]
    assert state(db, prom)[0] == "fulfilled"
    new_promise = res[4]["body"]["id"]
    assert q1(db, "SELECT event, source FROM promise_events WHERE promise_id=%s", (new_promise,)) == ("created", "legacy")
    assert state(db, new_promise)[0] == "open"
    # the legacy response shape is unchanged (no new columns leak)
    assert set(res[4]["body"]) == {"id", "clint_id", "user_id", "payment_date", "amount", "is_deleted",
                                   "create_at", "update_at", "is_archive"}
    assert "revision" not in res[3]["body"]

    # a payment reversed through v2 is "decided" for the legacy approve route
    r = mc.post(f"/v2/payments/{p_approve}/reverse", M.super,
                {"expected_revision": revision(db, "Payments", p_approve), "reason": "duplicate receipt"})
    assert r.status_code == 200, r.get_json()
    res = run_legacy([{"method": "PUT", "path": f"/payment/approve/{p_approve}"},
                      {"method": "GET", "path": f"/payments/clint/{case}/total"}])
    assert res[0]["body"]["error"] == "payment already decided"
    assert res[1]["body"] == {"total": 0}             # legacy case total excludes the reversed payment


def test_real_app_registers_and_serves_the_v2_routes(db):
    case = make_case(db, 1000)
    pid = make_payment(db, case, 250.25)
    key = str(uuid.uuid4())
    body = {"expected_revision": revision(db, "Payments", pid), "comment": "via the real app"}
    res = run_legacy([
        {"method": "POST", "path": f"/v2/payments/{pid}/approve", "json": body, "headers": {"Idempotency-Key": key}},
        {"method": "POST", "path": f"/v2/payments/{pid}/approve", "json": body, "headers": {"Idempotency-Key": key}},
        {"method": "GET", "path": f"/v2/cases/{case}/balance"},
        {"method": "GET", "path": f"/v2/payments?status=approved&case_id={case}"},
    ])
    assert [r["status"] for r in res] == [200, 200, 200, 200], res
    assert res[0]["body"] == res[1]["body"]                                   # idempotent replay
    assert res[2]["body"]["data"]["outstanding"] == "749.75"
    assert res[3]["body"]["data"]["total_amount"] == "250.25"
    assert q1(db, "SELECT count(*) FROM payment_events WHERE payment_id=%s AND event='approved'", (pid,))[0] == 1
