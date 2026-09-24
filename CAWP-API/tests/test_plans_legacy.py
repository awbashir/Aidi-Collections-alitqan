"""Stream B: legacy payment routes in the REAL app.py feed and void plan allocations, and the
real app serves the /v2 plan routes. The app runs in a subprocess bound to the test database
(test_money_support.run_legacy), so its JWT loaders never leak into this process."""
from __future__ import annotations

import uuid

from test_plans_support import *  # noqa: F401,F403


def test_legacy_approve_reject_delete_edit_and_create_feed_the_plan_ledger(db):
    t = today()
    case = make_case(db, 100_000)
    promise = make_promise(db, case, 1000, t)
    plan = insert_plan(db, case, "300.00", count=3, frequency="weekly")
    i1, i2, i3 = [i[0] for i in installments(db, plan)]
    p_reject = make_payment(db, case, 100, status=True)                # auto: i1
    p_delete = make_payment(db, case, 100, status=True)                # auto: i2
    p_edit = make_payment(db, case, 100, status=True)                  # auto: i3
    p_approve = make_payment(db, case, 150)
    assert plan_state(db, plan)[:2] == ("completed", D("300.00"))
    res = run_legacy([
        {"method": "PUT", "path": f"/payment/reject/{p_reject}", "json": {"approval_comment": "slip was fake"}},
        {"method": "DELETE", "path": f"/payments/{p_delete}"},
        {"method": "PUT", "path": f"/payment/{p_edit}", "json": {"amount": 50}},
        {"method": "PUT", "path": f"/payment/approve/{p_approve}"},
        {"method": "POST", "path": "/payment",
         "json": {"clint_id": case, "user_id": P.col, "amount": 80, "payment_date": t.isoformat(),
                  "collection_type_id": "00000000-0000-4000-8000-000000000704",
                  "collection_method_id": "00000000-0000-4000-8000-000000000705",
                  "approval_status": True, "approval_by": P.sup}},
    ], user=P.super)
    assert [r["status"] for r in res] == [200, 200, 200, 200, 200], res
    p_new = res[4]["body"]["id"]

    def voided(p):
        return q(db, "SELECT voided_by::text, void_reason FROM plan_allocations WHERE payment_id=%s", (p,))
    assert voided(p_reject) == [(P.super, "payment no longer approved")]          # the legacy rejecter
    assert voided(p_delete) == [(None, "payment hard-deleted")]
    assert voided(p_edit) == [(None, "payment amount reduced below its allocations")]
    # the freed capacity is used by the later approvals, oldest due installment first
    assert plan_allocs(db, payment_id=p_approve) == [(i1, p_approve, D("100.00"), "auto"),
                                                     (i2, p_approve, D("50.00"), "auto")]
    assert plan_allocs(db, payment_id=p_new) == [(i2, p_new, D("50.00"), "auto"), (i3, p_new, D("30.00"), "auto")]
    ev = q(db, "SELECT event, source, actor_user_id::text FROM plan_events WHERE payment_id=%s ORDER BY id",
           (p_approve,))
    assert ev == [("allocated", "legacy", P.super)] * 2
    assert q(db, "SELECT DISTINCT source, actor_user_id::text FROM plan_events WHERE payment_id=%s", (p_new,)) == \
        [("legacy", P.sup)]
    voids = q(db, "SELECT count(*) FROM plan_events WHERE case_id=%s AND event='allocation_voided' AND source='legacy'",
              (case,))[0][0]
    assert voids == 3
    # the promise ledger was fed by the same legacy writes, independently
    # (reject, delete and the shrink voided those three promise allocations too; 150 + 80 remain)
    assert q1(db, "SELECT allocated FROM v_promise_state WHERE promise_id=%s", (promise,))[0] == D("230.00")
    assert ledger_invariants(db, case) == []
    # legacy response shapes are unchanged: no plan or revision keys leak
    for body in (res[0]["body"], res[2]["body"], res[3]["body"], res[4]["body"]):
        assert "revision" not in body and not any("plan" in k for k in body)
    assert set(res[3]["body"]) == set(res[0]["body"])


def test_legacy_concurrent_approvals_fill_the_plan_exactly(db):
    case = make_case(db, 1000)
    plan = insert_plan(db, case, "900.00", count=3)
    pids = [make_payment(db, case, 300) for _ in range(8)]
    res = run_legacy([{"method": "PUT", "path": f"/payment/approve/{p}"} for p in pids], user=P.super, parallel=True)
    ok = [p for p, r in zip(pids, res) if r["status"] == 200]
    assert len(ok) == 3, res                                          # the debt allows exactly 3
    assert plan_state(db, plan)[:2] == ("completed", D("900.00"))
    for p in ok:
        assert sum(a[2] for a in plan_allocs(db, payment_id=p)) == D("300.00")
    assert ledger_invariants(db, case) == []


def test_real_app_serves_the_plan_routes(db):
    case = make_case(db, 5000)
    key = str(uuid.uuid4())
    body = plan_body("1000.00", "monthly", installment_count=3)
    pay = make_payment(db, case, 400)
    res = run_legacy([
        {"method": "POST", "path": f"/v2/cases/{case}/plans/preview", "json": body},
        {"method": "POST", "path": f"/v2/cases/{case}/plans", "json": body, "headers": {"Idempotency-Key": key}},
        {"method": "POST", "path": f"/v2/cases/{case}/plans", "json": body, "headers": {"Idempotency-Key": key}},
        {"method": "PUT", "path": f"/payment/approve/{pay}"},
        {"method": "GET", "path": f"/v2/cases/{case}/plans"},
        {"method": "GET", "path": f"/v2/plans?case_id={case}"},
    ], user=P.super)
    assert [r["status"] for r in res] == [200, 201, 201, 200, 200, 200], res
    assert res[1]["body"] == res[2]["body"]                                         # idempotent replay
    assert [i["amount"] for i in res[0]["body"]["data"]["installments"]] == \
        [i["amount"] for i in res[1]["body"]["data"]["installments"]] == ["333.34", "333.33", "333.33"]
    plan = res[4]["body"]["data"]["items"][0]
    assert [i["allocated"] for i in plan["installments"]] == ["333.34", "66.66", "0.00"]
    assert res[4]["body"]["data"]["balance"]["approved"] == "400.00"
    assert res[5]["body"]["data"]["total"] == 1 and res[5]["body"]["data"]["totals"]["allocated"] == "400.00"
    assert q1(db, "SELECT count(*) FROM repayment_plans WHERE case_id=%s", (case,))[0] == 1
