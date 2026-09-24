"""Stream B: /v2 repayment plan routes - preview, create, list, detail, case plans, cancel,
manual allocate / deallocate - with permissions, scope, idempotency, revisions and audit."""
from __future__ import annotations

import datetime as dt
import uuid

import pytest

from test_plans_support import *  # noqa: F401,F403

DAY = dt.timedelta(days=1)


def create(pc, case, user=P.super, key=None, **kw):
    return pc.post(f"/v2/cases/{case}/plans", user, plan_body(**kw), key=key)


def created_plan(pc, case, **kw):
    r = create(pc, case, **kw)
    assert r.status_code == 201, r.get_json()
    return r.get_json()["data"]


def cancel(pc, db, plan_id, user=P.super, reason="debtor asked for a new schedule", rev=None):
    rev = rev if rev is not None else q1(db, "SELECT revision FROM repayment_plans WHERE id=%s", (plan_id,))[0]
    return pc.post(f"/v2/plans/{plan_id}/cancel", user, {"expected_revision": rev, "reason": reason})


# --------------------------------------------------------------------------- preview
def test_preview_returns_the_schedule_and_writes_nothing(pc, db):
    case = make_case(db, 5000)
    before = q1(db, "SELECT (SELECT count(*) FROM repayment_plans), (SELECT count(*) FROM plan_events), "
                    "(SELECT count(*) FROM audit_events)")
    start = dt.date(2026, 10, 31) if today() <= dt.date(2026, 10, 31) else today()
    r = pc.post(f"/v2/cases/{case}/plans/preview", P.super,
                plan_body("1000.00", "monthly", start, installment_count=3), key=False)
    assert r.status_code == 200, r.get_json()
    data = r.get_json()["data"]
    assert data["installments"][0]["amount"] == "333.34" and data["installment_count"] == 3
    assert [i["amount"] for i in data["installments"]] == ["333.34", "333.33", "333.33"]
    if start == dt.date(2026, 10, 31):
        assert [i["due_date"] for i in data["installments"]] == ["2026-10-31", "2026-11-30", "2026-12-31"]
    assert data["balance"] == {"original": "5000.00", "approved": "0.00", "outstanding": "5000.00"}
    assert data["rules"] == {"split": "remainder_spread_from_first",
                             "due_dates": "same_day_each_month_clamped_to_month_end"}
    assert data["end_date"] == data["installments"][-1]["due_date"] and data["installment_amount"] is None
    r = pc.post(f"/v2/cases/{case}/plans/preview", P.super,
                plan_body("1000.00", "weekly", installment_amount="300"), key=False)
    data = r.get_json()["data"]
    assert [i["amount"] for i in data["installments"]] == ["300.00", "300.00", "300.00", "100.00"]
    assert data["split_mode"] == "amount" and data["installment_amount"] == "300.00"
    assert data["rules"]["split"] == "equal_with_smaller_last" and data["rules"]["due_dates"] == "every_7_days"
    after = q1(db, "SELECT (SELECT count(*) FROM repayment_plans), (SELECT count(*) FROM plan_events), "
                   "(SELECT count(*) FROM audit_events)")
    assert before == after


@pytest.mark.parametrize("body,status,code,error", [
    ({"frequency": "monthly", "installment_count": 2}, 422, "validation_failed", {"total_amount": "required"}),
    ({"total_amount": "abc", "frequency": "monthly", "installment_count": 2}, 422, "validation_failed",
     {"total_amount": "invalid"}),
    ({"total_amount": "10.001", "frequency": "monthly", "installment_count": 2}, 422, "validation_failed",
     {"total_amount": "too_precise"}),
    ({"total_amount": "-1", "frequency": "monthly", "installment_count": 2}, 422, "validation_failed",
     {"total_amount": "not_positive"}),
    ({"total_amount": "10", "installment_count": 2}, 422, "validation_failed", {"frequency": "required"}),
    ({"total_amount": "10", "frequency": "daily", "installment_count": 2}, 422, "validation_failed",
     {"frequency": "invalid"}),
    ({"total_amount": "10", "frequency": "weekly"}, 422, "validation_failed",
     {"installment_count": "exactly_one_of", "installment_amount": "exactly_one_of"}),
    ({"total_amount": "10", "frequency": "weekly", "installment_count": 2, "installment_amount": "5"}, 422,
     "validation_failed", {"installment_count": "exactly_one_of", "installment_amount": "exactly_one_of"}),
    ({"total_amount": "10", "frequency": "weekly", "installment_count": True}, 422, "validation_failed",
     {"installment_count": "invalid"}),
    ({"total_amount": "10", "frequency": "weekly", "installment_count": 0}, 422, "validation_failed",
     {"installment_count": "not_positive"}),
    ({"total_amount": "0.02", "frequency": "weekly", "installment_count": 3}, 422, "installment_too_small",
     {"field": "installment_count", "installment_count": 3, "max_installment_count": 2}),
    ({"total_amount": "1000", "frequency": "weekly", "installment_count": 241}, 422, "too_many_installments",
     {"field": "installment_count", "max": 240, "installment_count": 241}),
    ({"total_amount": "10", "frequency": "weekly", "installment_amount": "10.01"}, 422,
     "installment_amount_exceeds_total", {"field": "installment_amount"}),
])
def test_preview_and_create_validation(pc, db, body, status, code, error):
    case = make_case(db, 5000)
    body = {"start_date": today().isoformat(), **body}
    for url, key in ((f"/v2/cases/{case}/plans/preview", False), (f"/v2/cases/{case}/plans", None)):
        r = pc.post(url, P.super, body, key=key)
        assert (r.status_code, r.get_json()["code"], r.get_json()["error"]) == (status, code, error), url


def test_start_date_rules_use_the_riyadh_date(pc, db):
    case = make_case(db, 5000)
    t = today()
    r = pc.post(f"/v2/cases/{case}/plans/preview", P.super, plan_body(start=t - DAY), key=False)
    assert r.status_code == 422 and r.get_json()["code"] == "start_date_in_past"
    assert r.get_json()["error"] == {"start_date": (t - DAY).isoformat(), "today": t.isoformat()}
    r = pc.post(f"/v2/cases/{case}/plans/preview", P.super, plan_body(start=t + 367 * DAY), key=False)
    assert r.status_code == 422 and r.get_json()["code"] == "start_date_too_far"
    r = pc.post(f"/v2/cases/{case}/plans/preview", P.super, {**plan_body(), "start_date": "2026-02-30"}, key=False)
    assert r.get_json()["error"] == {"start_date": "invalid"}
    assert pc.post(f"/v2/cases/{case}/plans/preview", P.super, plan_body(start=t), key=False).status_code == 200


def test_total_must_not_exceed_the_exact_outstanding_balance(pc, db):
    case = make_case(db, 1000.005)                       # float8 debt, read as 1000.01
    make_payment(db, case, 0.1, status=True)
    make_payment(db, case, 0.2, status=True)              # approved 0.30 exactly, not 0.30000000000000004
    make_payment(db, case, 500)                           # pending never counts
    make_payment(db, case, 50, status=False)              # rejected never counts
    r = pc.post(f"/v2/cases/{case}/plans/preview", P.super, plan_body("999.72", installment_count=2), key=False)
    assert r.status_code == 409 and r.get_json()["code"] == "amount_exceeds_balance"
    assert r.get_json()["error"] == {"original": "1000.01", "approved": "0.30", "outstanding": "999.71",
                                     "total_amount": "999.72"}
    r = create(pc, case, total="999.71", installment_count=2)
    assert r.status_code == 201, r.get_json()
    plan = r.get_json()["data"]
    assert plan["outstanding_at_creation"] == "999.71" and plan["total_amount"] == "999.71"
    # the plan does not change the debt or the recovered money
    bal = pc.get(f"/v2/cases/{case}/balance", P.super).get_json()["data"]
    assert (bal["original"], bal["approved"], bal["outstanding"]) == ("1000.01", "0.30", "999.71")
    # an overpaid case has nothing to plan
    over = make_case(db, 100)
    make_payment(db, over, 150, status=True)
    r = create(pc, over, total="0.01", installment_count=1)
    assert r.status_code == 409 and r.get_json()["error"]["outstanding"] == "-50.00"


def test_locked_case_and_one_active_plan(pc, db):
    locked = make_case(db, 1000, locked=True)
    r = create(pc, locked, total="100")
    assert r.status_code == 423 and r.get_json()["code"] == "case_locked"
    case = make_case(db, 1000)
    first = created_plan(pc, case, total="100")
    for url, key in ((f"/v2/cases/{case}/plans/preview", False), (f"/v2/cases/{case}/plans", None)):
        r = pc.post(url, P.super, plan_body("100"), key=key)
        assert r.status_code == 409 and r.get_json()["code"] == "plan_already_active"
        assert r.get_json()["error"] == {"plan_id": first["id"], "state": "active"}


# --------------------------------------------------------------------------- create
def test_create_stores_the_previewed_schedule_with_events_and_audit(pc, db):
    case = make_case(db, 5000)
    body = plan_body("1000.00", "weekly", installment_count=3, note="  agreed on the call  ")
    preview = pc.post(f"/v2/cases/{case}/plans/preview", P.super, body, key=False).get_json()["data"]
    key = str(uuid.uuid4())
    r = pc.post(f"/v2/cases/{case}/plans", P.super, body, key=key)
    assert r.status_code == 201, r.get_json()
    plan = r.get_json()["data"]
    assert [(i["seq"], i["due_date"], i["amount"]) for i in plan["installments"]] == \
        [(i["seq"], i["due_date"], i["amount"]) for i in preview["installments"]]
    assert plan["state"] == "active" and plan["status"] == "active" and plan["revision"] == 0
    assert plan["note"] == "agreed on the call" and plan["created_by"]["id"] == P.super
    assert plan["counts"] == {"upcoming": 2, "due": 1, "overdue": 0, "partially_paid": 0, "paid": 0, "cancelled": 0}
    assert plan["next_due_date"] == today().isoformat() and plan["broken_threshold"] == 2
    assert plan["case"]["id"] == case and plan["case"]["owner"]["id"] == P.col
    assert [i["state"] for i in plan["installments"]] == ["due", "upcoming", "upcoming"]
    ev = plan["events"]
    assert len(ev) == 1 and ev[0]["event"] == "created" and ev[0]["source"] == "api"
    assert ev[0]["details"]["installment_count"] == 3 and ev[0]["amount"] == "1000.00"
    au = q(db, "SELECT action, actor_user_id::text, case_id::text, idempotency_key, after->>'total_amount' "
               "FROM audit_events WHERE entity_id=%s", (plan["id"],))
    assert au == [("plan.create", P.super, case, key, "1000.00")]
    assert q1(db, "SELECT idempotency_key FROM repayment_plans WHERE id=%s", (plan["id"],))[0] == key
    # idempotent replay: same bytes, still one plan; a different body with the same key is refused
    again = pc.post(f"/v2/cases/{case}/plans", P.super, body, key=key)
    assert again.status_code == 201 and again.headers.get("Idempotent-Replayed") == "true"
    assert again.get_data() == r.get_data()
    assert q1(db, "SELECT count(*) FROM repayment_plans WHERE case_id=%s", (case,))[0] == 1
    r = pc.post(f"/v2/cases/{case}/plans", P.super, {**body, "total_amount": "999"}, key=key)
    assert r.status_code == 422 and r.get_json()["code"] == "idempotency_key_reused"
    r = pc.client.post(f"/v2/cases/{case}/plans", json=body, headers=pc.headers(P.super, key=False))
    assert r.status_code == 400 and r.get_json()["code"] == "idempotency_key_required"


def test_money_strings_and_numbers_are_both_accepted(pc, db):
    case = make_case(db, 5000)
    plan = created_plan(pc, case, total=1234.5, installment_amount=500)
    assert [i["amount"] for i in plan["installments"]] == ["500.00", "500.00", "234.50"]
    assert plan["total_amount"] == "1234.50" and plan["installment_amount"] == "500.00"
    assert plan["split_mode"] == "amount" and plan["installment_count"] == 3


# --------------------------------------------------------------------------- permissions and scope
def test_permissions(pc, db):
    case = make_case(db, 5000)                                       # owned by pl.col
    plan = created_plan(pc, case, total="100")
    # pl.col has `plans` (read) only
    assert pc.get("/v2/plans", P.col).status_code == 200
    assert pc.get(f"/v2/plans/{plan['id']}", P.col).status_code == 200
    assert pc.get(f"/v2/cases/{case}/plans", P.col).status_code == 200
    for url, body in ((f"/v2/cases/{case}/plans/preview", plan_body()), (f"/v2/cases/{case}/plans", plan_body()),
                      (f"/v2/plans/{plan['id']}/cancel", {"expected_revision": 0, "reason": "x"}),
                      (f"/v2/plans/{plan['id']}/allocations", {"payment_id": str(uuid.uuid4()), "reason": "x"}),
                      (f"/v2/plan-allocations/{uuid.uuid4()}/deallocate", {"reason": "x"})):
        r = pc.post(url, P.col, body)
        assert r.status_code == 403 and r.get_json()["code"] == "forbidden", url
        assert r.get_json()["error"] == {"required_any": ["plans_manage"]}
    # pl.none has nothing
    for url in ("/v2/plans", f"/v2/plans/{plan['id']}", f"/v2/cases/{case}/plans"):
        r = pc.get(url, P.none)
        assert r.status_code == 403 and r.get_json()["error"] == {"required_any": ["plans", "plans_manage"]}
    # pl.sup has plans_manage (no `plans`): manage alone is enough to read
    assert pc.get(f"/v2/plans/{plan['id']}", P.sup).status_code == 200


def test_scope_answers_404_outside(pc, db):
    mine = make_case(db, 5000, owner=P.col)
    theirs = make_case(db, 5000, owner=P.col2, portfolio=P.other_portfolio)
    p_theirs = created_plan(pc, theirs, total="100")
    # collector pl.col cannot see pl.col2's plan or case
    r = pc.get(f"/v2/plans/{p_theirs['id']}", P.col)
    assert r.status_code == 404 and r.get_json()["code"] == "plan_not_found"
    assert pc.get(f"/v2/cases/{theirs}/plans", P.col).get_json()["code"] == "case_not_found"
    # supervisor pl.sup (Plans contract only) cannot act on the other contract's case
    for url, body, code in ((f"/v2/cases/{theirs}/plans/preview", plan_body(), "case_not_found"),
                            (f"/v2/cases/{theirs}/plans", plan_body(), "case_not_found"),
                            (f"/v2/plans/{p_theirs['id']}/cancel", {"expected_revision": 0, "reason": "x"},
                             "plan_not_found"),
                            (f"/v2/plans/{p_theirs['id']}/allocations",
                             {"payment_id": str(uuid.uuid4()), "reason": "x"}, "plan_not_found")):
        r = pc.post(url, P.sup, body, key=False if url.endswith("preview") else None)
        assert (r.status_code, r.get_json()["code"]) == (404, code), url
    p_mine = created_plan(pc, mine, user=P.sup, total="100")
    assert p_mine["created_by"]["id"] == P.sup
    assert pc.get(f"/v2/plans/{uuid.uuid4()}", P.super).get_json()["code"] == "plan_not_found"


# --------------------------------------------------------------------------- list
def test_list_filters_totals_states_and_scope(pc, db):
    t = today()
    c1 = make_case(db, 10_000, owner=P.col)
    c2 = make_case(db, 10_000, owner=P.col)
    c3 = make_case(db, 10_000, owner=P.col2, portfolio=P.other_portfolio)
    c4 = make_case(db, 10_000, owner=P.col)
    active = created_plan(pc, c1, total="600.00", frequency="weekly", installment_count=3)
    broken = insert_plan(db, c2, "300.10", count=3, dates=[t - 20 * DAY, t - 10 * DAY, t + 5 * DAY])
    other = created_plan(pc, c3, total="50.00", installment_count=1)
    done = created_plan(pc, c4, total="0.03", installment_count=1)
    make_payment(db, c4, "0.03", status=True)                          # completes it
    cancelled = insert_plan(db, make_case(db, 10_000, owner=P.col), "10.00", count=1)
    assert cancel(pc, db, cancelled).status_code == 200
    ids = {active["id"], broken, other["id"], done["id"], cancelled}

    def listing(user, **args):
        r = pc.get("/v2/plans", user, query_string=args)
        assert r.status_code == 200, r.get_json()
        return r.get_json()["data"]

    data = listing(P.super, page_size=200, created_by=P.super)
    mine = [i for i in data["items"] if i["id"] in ids]
    assert {i["id"]: i["state"] for i in mine} == {active["id"]: "active", broken: "broken", other["id"]: "active",
                                                   done["id"]: "completed", cancelled: "cancelled"}
    # exact totals, counts per state independent of the state filter
    data = listing(P.super, case_id=c2)
    assert data["total"] == 1 and data["totals"] == {"total_amount": "300.10", "allocated": "0.00",
                                                     "remaining": "300.10", "overdue_amount": "200.07"}
    assert data["counts_by_state"]["broken"] == {"count": 1, "total_amount": "300.10", "allocated": "0.00",
                                                 "overdue_amount": "200.07"}
    assert data["items"][0]["counts"]["overdue"] == 2 and data["items"][0]["oldest_overdue_date"] == \
        (t - 20 * DAY).isoformat()
    data = listing(P.super, case_id=c2, state="active,completed")
    assert data["total"] == 0 and data["items"] == [] and data["counts_by_state"]["broken"]["count"] == 1
    assert listing(P.super, case_id=c4, state="completed")["items"][0]["completed_at"] is not None
    # filters
    assert {i["id"] for i in listing(P.super, owner_id=P.col2)["items"]} >= {other["id"]}
    assert other["id"] not in {i["id"] for i in listing(P.super, owner_id=P.col, page_size=200)["items"]}
    by_contract = {i["id"] for i in listing(P.super, contract_id=P.other_contract, page_size=200)["items"]}
    assert other["id"] in by_contract and active["id"] not in by_contract
    wk = listing(P.super, frequency="weekly", case_id=c1)
    assert [i["id"] for i in wk["items"]] == [active["id"]] and wk["items"][0]["frequency"] == "weekly"
    assert listing(P.super, frequency="monthly", case_id=c1)["total"] == 0
    assert listing(P.super, created_from=t.isoformat(), created_to=t.isoformat(), case_id=c1)["total"] == 1
    assert listing(P.super, created_to=(t - DAY).isoformat(), case_id=c1)["total"] == 0
    assert listing(P.super, next_due_from=(t + 5 * DAY).isoformat(), case_id=c2)["total"] == 1
    assert listing(P.super, next_due_to=(t + 4 * DAY).isoformat(), case_id=c2)["total"] == 0
    # scope: the collector sees own cases only, the supervisor only the Plans contract
    col = {i["id"] for i in listing(P.col, page_size=200)["items"]}
    assert active["id"] in col and other["id"] not in col
    sup = {i["id"] for i in listing(P.sup, page_size=200)["items"]}
    assert active["id"] in sup and other["id"] not in sup
    # sort and paging
    p1 = listing(P.super, created_by=P.super, sort="created_at", page_size=2, page=1)
    p2 = listing(P.super, created_by=P.super, sort="created_at", page_size=2, page=2)
    assert len(p1["items"]) == 2 and not ({i["id"] for i in p1["items"]} & {i["id"] for i in p2["items"]})
    assert p1["total"] == p2["total"]
    for bad in ({"state": "open"}, {"sort": "nope"}, {"frequency": "daily"}, {"case_id": "x"},
                {"next_due_from": "2026-10-10", "next_due_to": "2026-10-01"}):
        r = pc.get("/v2/plans", P.super, query_string=bad)
        assert r.status_code == 422, bad


def test_detail_and_case_plans(pc, db):
    case = make_case(db, 10_000)
    old = created_plan(pc, case, total="100", installment_count=1)
    assert cancel(pc, db, old["id"]).status_code == 200
    plan = created_plan(pc, case, total="300", frequency="weekly", installment_count=3)
    pay = make_payment(db, case, 150)
    approve_v2(pc, db, pay)
    d = pc.get(f"/v2/plans/{plan['id']}", P.super).get_json()["data"]
    assert [(i["state"], i["allocated"], i["remaining"]) for i in d["installments"]] == [
        ("paid", "100.00", "0.00"), ("partially_paid", "50.00", "50.00"), ("upcoming", "0.00", "100.00")]
    a = d["installments"][0]["allocations"][0]
    assert (a["payment_id"], a["amount"], a["source"], a["active"], a["created_by"]) == \
        (pay, "100.00", "auto", True, P.super)
    assert d["installments"][0]["paid_at"] == a["created_at"]
    assert [e["event"] for e in d["events"]] == ["created", "allocated", "allocated"]
    assert d["events"][1]["source"] == "api" and d["events"][1]["actor"]["id"] == P.super
    cp = pc.get(f"/v2/cases/{case}/plans", P.super).get_json()["data"]
    assert [i["id"] for i in cp["items"]] == [plan["id"], old["id"]] and cp["active_plan_id"] == plan["id"]
    assert cp["balance"]["approved"] == "150.00" and "case" not in cp["items"][0]
    assert cp["items"][1]["state"] == "cancelled" and cp["items"][1]["cancellation"]["reason"]


# --------------------------------------------------------------------------- cancel
def test_cancel_needs_reason_and_revision_and_keeps_evidence(pc, db):
    case = make_case(db, 10_000)
    plan = created_plan(pc, case, total="300", installment_count=3)
    pay = make_payment(db, case, 100)
    approve_v2(pc, db, pay)
    r = pc.post(f"/v2/plans/{plan['id']}/cancel", P.super, {"expected_revision": 0})
    assert r.status_code == 422 and r.get_json()["error"] == {"reason": "required"}
    r = pc.post(f"/v2/plans/{plan['id']}/cancel", P.super, {"reason": "x"})
    assert r.status_code == 422 and r.get_json()["error"] == {"expected_revision": "required"}
    r = cancel(pc, db, plan["id"], rev=5)
    assert r.status_code == 409 and r.get_json()["code"] == "revision_conflict"
    assert r.get_json()["error"] == {"entity": "plan", "id": plan["id"], "expected_revision": 5,
                                     "current_revision": 0}
    key = str(uuid.uuid4())
    r = pc.post(f"/v2/plans/{plan['id']}/cancel", P.sup, {"reason": " debtor lost job "},
                key=key, headers={"If-Match": 'W/"0"'})
    assert r.status_code == 200, r.get_json()
    d = r.get_json()["data"]
    assert d["state"] == "cancelled" and d["revision"] == 1
    assert d["cancellation"]["reason"] == "debtor lost job" and d["cancellation"]["by"]["id"] == P.sup
    assert [i["state"] for i in d["installments"]] == ["paid", "cancelled", "cancelled"]      # evidence kept
    assert d["allocated"] == "100.00"
    assert d["events"][-1]["event"] == "cancelled" and d["events"][-1]["details"]["state_before"] == "active"
    assert q(db, "SELECT action, reason, idempotency_key FROM audit_events WHERE entity_id=%s AND action='plan.cancel'",
             (plan["id"],)) == [("plan.cancel", "debtor lost job", key)]
    r = cancel(pc, db, plan["id"])
    assert r.status_code == 409 and r.get_json()["code"] == "plan_not_cancellable"
    # later approvals go nowhere; a new plan can be created
    pay2 = make_payment(db, case, 100)
    approve_v2(pc, db, pay2)
    assert plan_allocs(db, payment_id=pay2) == []
    assert create(pc, case, total="100").status_code == 201


def test_a_completed_plan_closes_and_a_new_plan_is_allowed(pc, db):
    case = make_case(db, 10_000)
    plan = created_plan(pc, case, total="100", installment_count=2)
    approve_v2(pc, db, make_payment(db, case, 100))
    d = pc.get(f"/v2/plans/{plan['id']}", P.super).get_json()["data"]
    assert (d["state"], d["status"], d["revision"]) == ("completed", "completed", 1)
    assert d["completed_at"] == d["installments"][-1]["paid_at"] and d["completed_with_voided_evidence"] is False
    assert d["events"][-1]["event"] == "completed"
    # a completed plan is closed: it cannot be cancelled and takes no manual allocation
    r = cancel(pc, db, plan["id"], reason="x")
    assert r.status_code == 409 and r.get_json()["error"] == {"state": "completed"}
    r = pc.post(f"/v2/plans/{plan['id']}/allocations", P.super,
                {"payment_id": make_payment(db, case, 5, status=True), "reason": "x"})
    assert r.status_code == 409 and r.get_json()["error"] == {"state": "completed"}
    assert create(pc, case, total="100").status_code == 201


# --------------------------------------------------------------------------- v2 approve / reverse
def test_v2_approve_allocates_both_ledgers_and_reverse_voids_both(pc, db):
    case = make_case(db, 10_000)
    promise = make_promise(db, case, 500, today() + 3 * DAY)
    plan = created_plan(pc, case, total="400", frequency="weekly", installment_count=4)
    pay = make_payment(db, case, 250)
    data = approve_v2(pc, db, pay)
    assert [(a["promise_id"], a["amount"]) for a in data["allocations"]] == [(promise, "250.00")]
    d = pc.get(f"/v2/plans/{plan['id']}", P.super).get_json()["data"]
    assert [i["allocated"] for i in d["installments"]] == ["100.00", "100.00", "50.00", "0.00"]
    assert data["balance"]["approved"] == "250.00"                     # counted once as recovered money
    r = pc.post(f"/v2/payments/{pay}/reverse", P.sup,
                {"expected_revision": revision(db, "Payments", pay), "reason": "cheque bounced"})
    assert r.status_code == 200, r.get_json()
    rows = q(db, "SELECT voided_by::text, void_reason FROM plan_allocations WHERE payment_id=%s", (pay,))
    assert rows == [(P.sup, "payment reversed: cheque bounced")] * 3
    ev = q(db, "SELECT event, source, actor_user_id::text FROM plan_events WHERE payment_id=%s "
               "AND event='allocation_voided'", (pay,))
    assert ev == [("allocation_voided", "api", P.sup)] * 3
    assert q1(db, "SELECT count(*) FROM promise_allocations WHERE payment_id=%s AND voided_at IS NULL", (pay,))[0] == 0
    d = pc.get(f"/v2/plans/{plan['id']}", P.super).get_json()["data"]
    assert d["allocated"] == "0.00" and d["counts"]["paid"] == 0
    assert ledger_invariants(db, case) == []


# --------------------------------------------------------------------------- manual allocation
def test_manual_allocate_and_deallocate(pc, db):
    case = make_case(db, 10_000)
    early = make_payment(db, case, 250, status=True)                 # approved before the plan: unallocated
    big = make_payment(db, case, 1000, status=True)
    plan = created_plan(pc, case, total="300", frequency="weekly", installment_count=3)
    insts = [i["id"] for i in plan["installments"]]
    url = f"/v2/plans/{plan['id']}/allocations"
    r = pc.post(url, P.super, {"payment_id": early})
    assert r.status_code == 422 and r.get_json()["error"] == {"reason": "required"}
    # to one installment, default amount = what both sides allow
    key = str(uuid.uuid4())
    r = pc.post(url, P.super, {"payment_id": early, "installment_id": insts[1], "reason": "receipt 55 for week 2"},
                key=key)
    assert r.status_code == 201, r.get_json()
    data = r.get_json()["data"]
    assert [(a["installment_id"], a["amount"], a["source"], a["note"]) for a in data["allocations"]] == \
        [(insts[1], "100.00", "manual", "receipt 55 for week 2")]
    assert [i["state"] for i in data["plan"]["installments"]] == ["due", "paid", "upcoming"]
    # spread, oldest due first, explicit amount
    r = pc.post(url, P.super, {"payment_id": early, "amount": "120", "reason": "rest of receipt 55"})
    assert r.status_code == 201, r.get_json()
    assert [(a["installment_id"], a["amount"]) for a in r.get_json()["data"]["allocations"]] == \
        [(insts[0], "100.00"), (insts[2], "20.00")]
    # capacity: the payment has 30.00 left
    r = pc.post(url, P.super, {"payment_id": early, "amount": "30.01", "reason": "x"})
    assert (r.status_code, r.get_json()["code"], r.get_json()["error"]) == \
        (409, "payment_capacity_exceeded", {"available": "30.00"})
    # the plan has 80.00 left
    r = pc.post(url, P.super, {"payment_id": big, "amount": "80.01", "reason": "x"})
    assert (r.status_code, r.get_json()["code"], r.get_json()["error"]) == \
        (409, "installment_capacity_exceeded", {"available": "80.00"})
    r = pc.post(url, P.super, {"payment_id": big, "installment_id": insts[0], "reason": "x"})
    assert r.status_code == 409 and r.get_json()["code"] == "nothing_to_allocate"
    assert r.get_json()["error"] == {"payment_available": "1000.00", "installments_available": "0.00"}
    # the payment must be approved, on the same case, the installment in this plan
    pending = make_payment(db, case, 10)
    r = pc.post(url, P.super, {"payment_id": pending, "reason": "x"})
    assert (r.status_code, r.get_json()["code"], r.get_json()["error"]) == (409, "payment_not_allocatable",
                                                                            {"status": "pending"})
    elsewhere = make_payment(db, make_case(db, 100), 10, status=True)
    assert pc.post(url, P.super, {"payment_id": elsewhere, "reason": "x"}).get_json()["code"] == "case_mismatch"
    r = pc.post(url, P.super, {"payment_id": big, "installment_id": str(uuid.uuid4()), "reason": "x"})
    assert r.status_code == 404 and r.get_json()["code"] == "installment_not_found"
    r = pc.post(url, P.super, {"payment_id": str(uuid.uuid4()), "reason": "x"})
    assert r.status_code == 404 and r.get_json()["code"] == "payment_not_found"
    events = q(db, "SELECT event, reason, source, idempotency_key FROM plan_events WHERE plan_id=%s "
                   "AND event='allocated' ORDER BY id LIMIT 1", (plan["id"],))
    assert events == [("allocated", "receipt 55 for week 2", "api", key)]
    assert q1(db, "SELECT count(*) FROM audit_events WHERE entity_id=%s AND action='plan.allocate'",
              (plan["id"],))[0] == 2

    # deallocate
    aid = data["allocations"][0]["id"]
    durl = f"/v2/plan-allocations/{aid}/deallocate"
    assert pc.post(durl, P.super, {}).get_json()["error"] == {"reason": "required"}
    r = pc.post(durl, P.super, {"reason": "wrong week"})
    assert r.status_code == 200, r.get_json()
    d = r.get_json()["data"]
    assert d["allocation_id"] == aid and [i["state"] for i in d["plan"]["installments"]][1] == "upcoming"
    voided = [a for a in d["plan"]["installments"][1]["allocations"] if a["id"] == aid][0]
    assert (voided["active"], voided["void_reason"], voided["voided_by"]) == (False, "wrong week", P.super)
    assert d["plan"]["events"][-1]["event"] == "deallocated" and d["plan"]["events"][-1]["reason"] == "wrong week"
    r = pc.post(durl, P.super, {"reason": "again"})
    assert r.status_code == 409 and r.get_json()["code"] == "allocation_already_voided"
    assert pc.post(f"/v2/plan-allocations/{uuid.uuid4()}/deallocate", P.super,
                   {"reason": "x"}).get_json()["code"] == "allocation_not_found"
    # a cancelled plan takes no manual allocation
    assert cancel(pc, db, plan["id"]).status_code == 200
    r = pc.post(url, P.super, {"payment_id": big, "reason": "x"})
    assert r.status_code == 409 and r.get_json()["code"] == "plan_not_allocatable"
    assert ledger_invariants(db, case) == []
