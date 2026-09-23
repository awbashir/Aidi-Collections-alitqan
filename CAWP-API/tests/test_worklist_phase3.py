"""Attention queue with the Phase 3 reasons: missed_installment (repayment plans) and
open_request (internal requests, only for reviewers of that department). An active plan
makes a case "scheduled"; a case that is both unscheduled and has an open request is listed
and counted once."""
from __future__ import annotations

import datetime as dt

from test_worklist_helpers import *  # noqa: F401,F403


def _plan(conn, case, owner, dues_and_amounts):
    total = sum(a for _d, a in dues_and_amounts)
    stmts = ["BEGIN"]
    stmts.append(f"""INSERT INTO repayment_plans (id, case_id, frequency, start_date, total_amount,
                        installment_count, split_mode, outstanding_at_creation, created_by)
                     VALUES ('{case[:8]}-0000-4000-8000-00000000abcd'::uuid, '{case}', 'weekly',
                             '{dues_and_amounts[0][0]}', {total}, {len(dues_and_amounts)}, 'count', 1000, '{owner}')""")
    for i, (due, amount) in enumerate(dues_and_amounts, start=1):
        stmts.append(f"""INSERT INTO plan_installments (plan_id, case_id, seq, due_date, amount)
                         VALUES ('{case[:8]}-0000-4000-8000-00000000abcd'::uuid, '{case}', {i}, '{due}', {amount})""")
    stmts.append("COMMIT")
    run(conn, ";\n".join(stmts))


def _reviewer(conn, on: bool):
    run(conn, "UPDATE roles SET permission = (permission::jsonb || jsonb_build_object('requests_legal', %s))::json "
              "WHERE id = %s", on, W.super_role)
    invalidate_caches()


def _att(client, h, **params):
    r = client.get("/v2/attention", headers=h, query_string=params)
    assert r.status_code == 200, r.get_json()
    return r.get_json()["data"]


def test_plans_and_requests_in_the_attention_queue(wl_client, headers, pgw):
    contract, pf = new_contract(pgw, "p3 contract")
    c1 = new_user(pgw)
    today = riyadh_today()
    missed = make_case(pgw, c1, pf)
    _plan(pgw, missed, c1, [(today - dt.timedelta(days=10), 100), (today + dt.timedelta(days=4), 100)])
    on_plan = make_case(pgw, c1, pf)
    _plan(pgw, on_plan, c1, [(today + dt.timedelta(days=3), 50)])
    asking = make_case(pgw, c1, pf)
    run(pgw, "INSERT INTO requests (case_id, department, subject, body, requested_by) "
             "VALUES (%s, 'legal', 'court date', 'please advise', %s)", asking, c1)
    by_id = {missed: "missed", on_plan: "on_plan", asking: "asking"}

    try:
        _reviewer(pgw, False)
        data = _att(wl_client, headers(W.super), contract_id=contract)
        got = {by_id[i["case"]["id"]]: i["reasons"] for i in data["items"]}
        assert got == {"missed": ["missed_installment"], "asking": ["unscheduled"]}
        assert data["counts"]["missed_installment"] == 1 and data["counts"]["open_request"] == 0
        assert data["counts"]["unscheduled"] == 1 and data["total"] == 2          # on_plan is scheduled
        fact = next(i for i in data["items"] if i["case"]["id"] == missed)["facts"]["missed_installment"]
        assert fact["count"] == 1

        _reviewer(pgw, True)                                                       # a legal reviewer
        data = _att(wl_client, headers(W.super), contract_id=contract)
        got = {by_id[i["case"]["id"]]: i["reasons"] for i in data["items"]}
        assert got == {"missed": ["missed_installment"], "asking": ["open_request", "unscheduled"]}
        assert data["counts"]["open_request"] == 1 and data["total"] == 2         # listed and counted once
        only = _att(wl_client, headers(W.super), contract_id=contract, reason="open_request")
        assert [i["case"]["id"] for i in only["items"]] == [asking] and only["total"] == 1
    finally:
        _reviewer(pgw, False)
