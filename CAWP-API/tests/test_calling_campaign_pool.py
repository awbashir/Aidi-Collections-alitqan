"""Phase 5 P2: the campaign pool - definition validation, parity with the clients-list filter,
deterministic selection order, every exclusion, and the contact choice.

Run: docker compose --profile test run --rm -e TEST_DB_PREFIX=cawp_test_call2 api-test tests/test_calling_campaign*.py
"""
from __future__ import annotations

import pytest

from test_calling_campaign_helpers import (CL, CP, add_contact, auth, case_for, cl, client, cp, cp_app,  # noqa: F401
                                           fences, in_app, make_case, new_campaign, new_contract, new_user, number,
                                           pgc, q1, qall, run, transport, world)


def load(cp_app, cid):
    from services.calling import campaign_dispatch as CD
    return in_app(cp_app, lambda s: CD.load_campaign(s, cid))


# --------------------------------------------------------------------------- validation
@pytest.mark.parametrize("pool,code,field", [
    (None, "validation_failed", "pool"),
    ({}, "validation_failed", "pool"),
    ({"contract_ids": ["nope"]}, "validation_failed", "pool.contract_ids"),
    ({"cases": []}, "validation_failed", "pool"),
    ({"filter": {"customeFilterInquiryClintByExcelValue[file_name]": "x.xlsx"}}, "validation_failed",
     "pool.filter.customeFilterInquiryClintByExcelValue[file_name]"),
    ({"filter": {"customeFilterWhatever": 1}}, "validation_failed", "pool.filter.customeFilterWhatever"),
    ({"filter": {"filterField": "name", "filterOperator": ">", "filterFieldType": "number", "filterValue": "5"}},
     "validation_failed", "pool.filter.filterOperator"),
    ({"filter": {"filterField": "user.name", "filterOperator": "contains", "filterFieldType": "string",
                 "filterValue": "x"}}, "validation_failed", "pool.filter.filterField"),
    ({"filter": {"page": 3, "pageSize": 50}}, "validation_failed", "pool"),       # paging alone is no criterion
])
def test_pool_definition_is_validated(cp_app, pool, code, field):
    from core.api_response import ApiError
    from services.calling import campaign_pool as P
    with pytest.raises(ApiError) as e:
        P.validate_pool(pool)
    assert e.value.status == 422 and e.value.code == code and field in e.value.error, e.value.error


def test_pool_definition_normalises_a_saved_clients_list_filter(cp_app):
    from services.calling import campaign_pool as P
    a, b = "00000000-0000-4000-8000-00000000aa01", "00000000-0000-4000-8000-00000000aa02"
    out = P.validate_pool({"portfolio_ids": [b, a, a], "filter": {
        "customeFilterCommentStatusValue[]": ["0", a], "customeFilterUserValue[]": b, "page": 2, "sort": "desc",
        "customeFilterPortfolioCreateAtFromValue": "2026-01-01T00:00:00Z", "customeFilterAttributeValue": ""}})
    assert out == {"contract_ids": [], "portfolio_ids": [a, b], "filter": {
        "customeFilterCommentStatusValue[]": [a], "customeFilterUserValue[]": [b],
        "customeFilterPortfolioCreateAtFromValue": "2026-01-01"}}


# --------------------------------------------------------------------------- parity with the clients list
def test_pool_matches_the_clients_list_for_the_same_filter(cp_app, pgc, cp):
    """The pool (unarchived part) is exactly what GET /clints returns for the same saved filter."""
    from werkzeug.datastructures import MultiDict

    from conftest import Ids
    from services.calling import campaign_pool as P
    from services.clients_query import build_clients_query
    cid, pid = new_contract(pgc, "cp parity")
    _c2, pid2 = new_contract(pgc, "cp parity 2")
    run(pgc, "UPDATE portfolios SET create_at = '2026-03-10T10:00:00Z' WHERE id = %s", pid2)
    run(pgc, "UPDATE portfolios SET contract_id = %s WHERE id = %s", cid, pid2)        # two portfolios, one contract
    u1, u2 = new_user(pgc, role=CP.collector_role), new_user(pgc, role=CP.collector_role)
    specs = [(u1, pid, 100, "Alpha Beta", '{"tier": "gold"}'), (u1, pid, 900, "Gamma", '{"tier": "silver"}'),
             (u2, pid2, 600, "Beta Delta", '{"tier": "gold"}'), (u2, pid, 50, "Omega", '{}'),
             (u1, pid2, 750, "beta lower", '{"note": "100% sure"}')]
    cases = []
    for owner, portfolio, amount, name, attr in specs:
        c = make_case(pgc, owner, portfolio, amount=amount)
        run(pgc, "UPDATE clints SET name = %s, attribute = %s::jsonb WHERE id = %s", name, attr, c)
        cases.append(c)
    make_case(pgc, u1, pid, archived=True)                                  # archived: not in the list
    run(pgc, """INSERT INTO "Comments" (clint_id, user_id, "contactMethod_id", comment, comment_status_id)
                VALUES (%s, %s, %s, 'note', %s)""", cases[1], u1, CL.method, CL.status)
    accounts = [q1(pgc, 'SELECT "accountNumber" FROM clints WHERE id = %s', c)[0] for c in cases[:2]]
    base = {"customeFilterContractValue[]": [cid]}
    filters = [
        {},
        {"customeFilterPortfoliosValue[]": [pid2]},
        {"customeFilterUserValue[]": [u1]},
        {"customeFilterAttributeValue": "gold"},
        {"customeFilterAttributeValue": "100%"},
        {"customeFilterCommentStatusValue[]": [CL.status]},
        {"customeFilterBranchesValue[]": [Ids.branch]},
        {"customeFilterPortfolioCreateAtFromValue": "2026-03-01", "customeFilterPortfolioCreateAtToValue": "2026-03-31"},
        {"filterField": "amount", "filterOperator": ">", "filterFieldType": "number", "filterValue": "500"},
        {"filterField": "amount", "filterOperator": "<=", "filterFieldType": "number", "filterValue": 100},
        {"filterField": "name", "filterOperator": "contains", "filterFieldType": "string", "filterValue": "beta"},
        {"filterField": "name", "filterOperator": "startsWith", "filterFieldType": "string", "filterValue": "Beta"},
        {"filterField": "name", "filterOperator": "equals", "filterFieldType": "string", "filterValue": "Gamma"},
        {"filterField": "accountNumber", "filterOperator": "isAnyOf", "filterFieldType": "string",
         "filterValue[]": accounts},
        {"filterField": "comment.create_at", "filterOperator": "isEmpty"},
        {"filterField": "contract.name", "filterOperator": "endsWith", "filterFieldType": "string",
         "filterValue": "parity"},
    ]

    def listed(flt):
        md = MultiDict()
        for k, v in {**base, **flt}.items():
            for x in (v if isinstance(v, list) else [v]):
                md.add(k, str(x))
        q, _count, _ = build_clients_query(md, CP.super)
        return {str(r.k_id) for r in q.limit(1000).all()}

    def pooled(flt):
        from sqlalchemy import text

        from extensions import db
        pool = P.validate_pool({"filter": {**base, **flt}})
        joins, where, params = P.definition_sql(pool)
        own, own_params = P.scope_clause(CP.super, "os")
        return {str(r[0]) for r in db.session.execute(text(f"""
            SELECT c.id FROM clints c JOIN portfolios p ON p.id = c.portfolios_id JOIN contracts k ON k.id = p.contract_id
            {joins} WHERE {where} AND {own} AND NOT c.is_archive"""), {**params, **own_params}).all()}

    with cp_app.app_context():
        for flt in filters:
            want, got = listed(flt), pooled(flt)
            assert got == want, (flt, sorted(want), sorted(got))
        assert len(listed({})) == 5


# --------------------------------------------------------------------------- note status + sub-status (2026-10-19)
def note_status(pgc, name, *, contract=None, parent=None, deleted=False, stops=False, blocks=False):
    import uuid
    sid = str(uuid.uuid4())
    run(pgc, """INSERT INTO "CommentStatuses" (id, name, contract_id, "CommentReachability_id", is_active, parent_id,
                                               deleted_at, stops_campaigns, blocks_dialed_number)
                VALUES (%s, %s, %s, %s, true, %s, CASE WHEN %s THEN now() END, %s, %s)""",
        sid, name + " " + sid[:6], contract, CL.reach, parent, deleted, stops, blocks)
    return sid


def note(pgc, case, user, status, sub=None, *, hours_ago=0):
    run(pgc, """INSERT INTO "Comments" (clint_id, user_id, "contactMethod_id", comment, comment_status_id,
                                        comment_sub_status_id, create_at)
                VALUES (%s, %s, %s, 'note', %s, %s, now() - make_interval(hours => %s))""",
        case, user, CL.method, status, sub, hours_ago)


def test_pool_filters_by_the_latest_note_status_and_sub_status(cp_app, pgc, cp):
    """Main status and sub-status of the case's LATEST note (clints.last_comment_id), ANDed, and
    exactly what GET /clints returns for the same filter."""
    from werkzeug.datastructures import MultiDict

    from services.calling import campaign_pool as P
    from services.clients_query import build_clients_query
    cid, pid = new_contract(pgc, "cp status")
    u = new_user(pgc, role=CP.collector_role)
    main = note_status(pgc, "cp main")
    s1, s2 = note_status(pgc, "cp sub1", parent=main), note_status(pgc, "cp sub2", parent=main)
    other = note_status(pgc, "cp other", contract=cid)
    osub = note_status(pgc, "cp other sub", contract=cid, parent=other)
    c = {k: make_case(pgc, u, pid) for k in ("s1", "s2", "main", "other", "none")}
    note(pgc, c["s1"], u, other, osub, hours_ago=5)              # an older note never counts
    note(pgc, c["s1"], u, main, s1, hours_ago=1)
    note(pgc, c["s2"], u, main, s2, hours_ago=1)
    note(pgc, c["main"], u, main, None, hours_ago=1)
    note(pgc, c["other"], u, main, s1, hours_ago=5)
    note(pgc, c["other"], u, other, osub, hours_ago=1)
    base = {"customeFilterContractValue[]": [cid]}
    M, S = "customeFilterCommentStatusValue[]", "customeFilterCommentSubStatusValue[]"
    cases = [
        ({M: [main]}, {"s1", "s2", "main"}),
        ({"customeFilterStatusesValue": main}, {"s1", "s2", "main"}),
        ({M: [main, other]}, {"s1", "s2", "main", "other"}),
        ({S: [s1]}, {"s1"}),
        ({M: [main], S: [s1, s2]}, {"s1", "s2"}),
        ({M: [main, other], S: [s2]}, {"s2"}),                   # ANDed, as the clients list
        ({M: [other], S: [osub]}, {"other"}),
        ({M: [other], S: [s1]}, set()),
    ]

    def listed(flt):
        md = MultiDict()
        for k, v in {**base, **flt}.items():
            for x in (v if isinstance(v, list) else [v]):
                md.add(k, str(x))
        q, _count, _ = build_clients_query(md, CP.super)
        return {str(r.k_id) for r in q.limit(1000).all()}

    def pooled(flt):
        from sqlalchemy import text

        from extensions import db
        pool = P.validate_pool({"filter": {**base, **flt}})
        joins, where, params = P.definition_sql(pool)
        return {str(r[0]) for r in db.session.execute(text(f"""
            SELECT c.id FROM clints c JOIN portfolios p ON p.id = c.portfolios_id JOIN contracts k ON k.id = p.contract_id
            {joins} WHERE {where}"""), params).all()}

    with cp_app.app_context():
        for flt, want in cases:
            want_ids = {c[k] for k in want}
            assert pooled(flt) == want_ids, (flt, want)
            assert listed(flt) == want_ids, (flt, want)


def test_pool_note_statuses_must_fit_the_contracts_in_scope(cp_app, pgc, world):
    from core.api_response import ApiError
    from services.calling import campaign_pool as P
    w = world
    case_for(pgc, w.c1, w.portfolio)                             # the supervisor's scope reaches w.contract
    oc = q1(pgc, "SELECT contract_id::text FROM portfolios WHERE id = %s", w.other_portfolio)[0]
    general = note_status(pgc, "cp general")
    gsub = note_status(pgc, "cp general sub", parent=general)
    own = note_status(pgc, "cp own", contract=w.contract)
    foreign = note_status(pgc, "cp foreign", contract=oc)
    foreign_sub = note_status(pgc, "cp foreign sub", contract=oc, parent=foreign)
    gone = note_status(pgc, "cp deleted", deleted=True)
    M, S = "customeFilterCommentStatusValue[]", "customeFilterCommentSubStatusValue[]"

    def check(user, pool, keep=()):
        def go(_s):
            P.check_pool_statuses(P.validate_pool(pool), user, keep)
        return in_app(cp_app, go)

    def refused(user, pool, field, code, keep=()):
        with pytest.raises(ApiError) as e:
            check(user, pool, keep)
        assert e.value.status == 422 and e.value.error == {f"pool.filter.{field}": code}, e.value.error

    assert in_app(cp_app, lambda s: P.scope_contract_ids(w.supervisor)) == [w.contract]
    assert in_app(cp_app, lambda s: P.scope_contract_ids(CP.super)) is None
    check(w.supervisor, {"contract_ids": [w.contract], "filter": {M: [general, own], S: [gsub]}})
    check(w.supervisor, {"filter": {M: [own]}})                  # no contract named: my scope bounds it
    check(w.supervisor, {"portfolio_ids": [w.portfolio], "filter": {M: [own]}})
    refused(w.supervisor, {"contract_ids": [w.contract], "filter": {M: [foreign]}}, M, "status_not_for_contract")
    refused(w.supervisor, {"filter": {M: [foreign]}}, M, "status_not_for_contract")      # outside my scope
    refused(CP.super, {"contract_ids": [w.contract], "filter": {M: [foreign]}}, M, "status_not_for_contract")
    refused(CP.super, {"portfolio_ids": [w.portfolio], "filter": {S: [foreign_sub]}}, S, "status_not_for_contract")
    check(CP.super, {"filter": {M: [foreign], S: [foreign_sub]}})                       # SuperAdmin: any contract
    refused(w.supervisor, {"contract_ids": [w.contract], "filter": {M: [gsub]}}, M, "is_sub_status")
    refused(w.supervisor, {"contract_ids": [w.contract], "filter": {S: [general]}}, S, "not_a_sub_status")
    refused(w.supervisor, {"contract_ids": [w.contract], "filter": {M: [own], S: [gsub]}}, S, "not_a_sub_status")
    refused(w.supervisor, {"contract_ids": [w.contract], "filter": {M: [gone]}}, M, "status_deleted")
    refused(w.supervisor, {"contract_ids": [w.contract], "filter": {M: [w.contract]}}, M, "unknown")
    check(w.supervisor, {"contract_ids": [w.contract], "filter": {M: [gone, foreign]}}, keep={gone, foreign})


# --------------------------------------------------------------------------- order
def test_selection_order_is_priority_then_oldest_contact_then_id(cp_app, client, auth, world, pgc):
    from services.calling import campaign_pool as P
    w = world
    never_a = case_for(pgc, w.c1, w.portfolio)[0]
    never_b = case_for(pgc, w.c1, w.portfolio)[0]
    old = case_for(pgc, w.c1, w.portfolio)[0]
    recent = case_for(pgc, w.c1, w.portfolio)[0]
    callback = case_for(pgc, w.c1, w.portfolio)[0]
    tried = case_for(pgc, w.c1, w.portfolio)[0]
    run(pgc, "UPDATE clints SET last_comment_at = now() - interval '2 days' WHERE id = %s", old)
    run(pgc, "UPDATE clints SET last_comment_at = now() - interval '1 hour' WHERE id = %s", recent)
    run(pgc, "UPDATE clints SET last_comment_at = now() - interval '5 minutes' WHERE id = %s", callback)
    run(pgc, """INSERT INTO follow_ups (case_id, owner_user_id, due_at, original_due_at, source)
                VALUES (%s, %s, now() - interval '1 hour', now() - interval '1 hour', 'imported')""", callback, w.c1)
    cid = new_campaign(client, auth, w, members=[w.c1], start=False)
    run(pgc, "INSERT INTO campaign_pool_state (campaign_id, case_id, last_tried_at) VALUES (%s, %s, now())", cid, tried)
    camp = load(cp_app, cid)
    got = in_app(cp_app, lambda s: P.candidates(camp, w.c1, limit=10))
    never = sorted([never_a, never_b])
    assert [str(r["case_id"]) for r in got] == [callback, *never, old, recent, tried]
    assert [r["callback_due"] for r in got] == [True, False, False, False, False, False]
    # the supervisor preview lists the same order
    items = client.get(f"/v2/campaigns/{cid}/pool", headers=auth(w.supervisor)).get_json()["data"]["items"]
    assert [i["case"]["id"] for i in items] == [callback, *never, old, recent, tried]
    assert items[0]["priority"] == "callback_due" and items[1]["last_contact_at"] is None


# --------------------------------------------------------------------------- exclusions
def test_every_exclusion_is_reported_and_never_offered(cp_app, client, auth, world, pgc):
    from services.calling import campaign_pool as P
    w = world
    c = {}
    c["eligible"] = case_for(pgc, w.c1, w.portfolio)[0]
    c["case_archived"] = case_for(pgc, w.c1, w.portfolio, archived=True)[0]
    c["case_locked"] = case_for(pgc, w.c1, w.portfolio, locked=True)[0]
    c["do_not_call"] = case_for(pgc, w.c1, w.portfolio)[0]
    run(pgc, "INSERT INTO calling_blocks (case_id, reason, source, created_by) VALUES (%s, 'opt-out', 'manual', %s)",
        c["do_not_call"], w.supervisor)
    c["calling_review_pending"] = case_for(pgc, w.c1, w.portfolio)[0]
    flagged = q1(pgc, """INSERT INTO call_attempts (mode, state, case_id, collector_user_id, created_by, created_at)
                         VALUES ('click_to_call', 'ended', %s, %s, %s, now() - interval '2 days') RETURNING id""",
                 c["calling_review_pending"], w.c1, w.c1)[0]
    run(pgc, """INSERT INTO call_review_flags (attempt_id, case_id, kind, reason, blocks_calling)
                VALUES (%s, %s, 'wrapup_needs_review', 'case_locked', true)""", flagged, c["calling_review_pending"])
    c["in_call"] = case_for(pgc, w.c1, w.portfolio)[0]
    live = q1(pgc, """INSERT INTO call_attempts (mode, state, case_id, collector_user_id, created_by, created_at)
                      VALUES ('click_to_call', 'prepared', %s, %s, %s, now() - interval '2 days') RETURNING id""",
              c["in_call"], w.c2, w.c2)[0]
    run(pgc, "INSERT INTO call_reservations (attempt_id, collector_user_id, case_id) VALUES (%s, %s, %s)",
        live, w.c2, c["in_call"])
    c["no_member_in_scope"] = case_for(pgc, w.outsider, w.portfolio)[0]
    c["case_paid"] = case_for(pgc, w.c1, w.portfolio, amount=100)[0]
    run(pgc, """INSERT INTO "Payments" (clint_id, user_id, collection_type_id, collection_method_id, amount,
                                       payment_date, approval_status) VALUES (%s, %s,
                '00000000-0000-4000-8000-000000000704', '00000000-0000-4000-8000-000000000705', 100, now(), true)""",
        c["case_paid"], w.c1)
    c["no_callable_contact"] = make_case(pgc, w.c1, w.portfolio)
    add_contact(pgc, c["no_callable_contact"], "debtor@example.invalid", default=True)
    add_contact(pgc, c["no_callable_contact"], number(4), blocked=True)
    c["callback_scheduled"] = case_for(pgc, w.c1, w.portfolio)[0]
    run(pgc, """INSERT INTO follow_ups (case_id, owner_user_id, due_at, original_due_at, source)
                VALUES (%s, %s, now() + interval '2 hours', now() + interval '2 hours', 'imported')""",
        c["callback_scheduled"], w.c1)
    c["max_attempts_today"] = case_for(pgc, w.c1, w.portfolio)[0]
    c["cooldown"] = case_for(pgc, w.c1, w.portfolio)[0]
    for case, mins in ((c["max_attempts_today"], 12), (c["max_attempts_today"], 11), (c["cooldown"], 10)):
        run(pgc, """INSERT INTO call_attempts (mode, state, case_id, collector_user_id, created_by, created_at)
                    VALUES ('manual', 'ended', %s, %s, %s, now() - make_interval(mins => %s))""", case, w.c1, w.c1, mins)
    c["recheck_later"] = case_for(pgc, w.c1, w.portfolio)[0]
    c["status_stops_campaigns"] = case_for(pgc, w.c1, w.portfolio)[0]
    note(pgc, c["status_stops_campaigns"], w.c1, note_status(pgc, "cp paid", stops=True))
    # the SuperAdmin owns the campaign so the outsider's case is in the pool (but no member may call it)
    cid = new_campaign(client, auth, w, owner=CP.super, members=[w.c1, w.c2], start=False,
                       settings={"max_attempts_per_day": 2, "cooldown_minutes": 60})
    run(pgc, """INSERT INTO campaign_pool_state (campaign_id, case_id, excluded_reason, excluded_at, recheck_after)
                VALUES (%s, %s, 'destination_invalid', now(), now() + interval '10 minutes')""", cid, c["recheck_later"])
    camp = load(cp_app, cid)
    summary = in_app(cp_app, lambda s: P.summary(camp, [w.c1, w.c2]))
    assert summary["size"] == len(c) and summary["eligible_now"] == 1
    assert summary["excluded"] == {r: 1 for r in c if r != "eligible"}
    assert summary["remaining_today"] == len(c) - len([r for r in c if r in P.HARD_REASONS])
    items = client.get(f"/v2/campaigns/{cid}/pool", headers=auth(CP.super), query_string={"page_size": 50}
                       ).get_json()["data"]
    assert {i["case"]["id"]: i["reason"] for i in items["items"]} == {v: k for k, v in c.items()}
    assert items["summary"]["excluded"] == summary["excluded"]
    only = client.get(f"/v2/campaigns/{cid}/pool", headers=auth(CP.super),
                      query_string={"reason": "cooldown,case_paid"}).get_json()["data"]
    assert only["total"] == 2
    # only the eligible case is ever offered, to either member
    for member in (w.c1, w.c2):
        got = in_app(cp_app, lambda s, m=member: P.candidates(camp, m))
        assert [str(r["case_id"]) for r in got] == ([c["eligible"]] if member == w.c1 else [])


# --------------------------------------------------------------------------- contact choice
def test_contact_choice_default_then_documented_fallbacks(cp_app, pgc, world):
    from services.calling import campaign_pool as P
    w = world

    def choose(case):
        idn = q1(pgc, 'SELECT "identityNumber" FROM clints WHERE id = %s', case)[0]
        return in_app(cp_app, lambda s: P.choose_contact(idn))
    case = make_case(pgc, w.c1, w.portfolio)
    older = add_contact(pgc, case, number(1))
    default = add_contact(pgc, case, number(4), default=True)
    newer = add_contact(pgc, case, "0551234564")                            # national form -> +966
    got, how = choose(case)
    assert (got["id"], how) == (default, "default")
    run(pgc, 'UPDATE "ClintContacts" SET is_blocked = true, blocked_at = now(), block_reason = %s WHERE id = %s',
        "wrong number", default)
    got, how = choose(case)
    assert (got["id"], how, got["destination"]) == (newer, "fallback", "+966551234564")   # newest callable
    run(pgc, 'UPDATE "ClintContacts" SET is_active = false WHERE id = %s', newer)
    got, how = choose(case)
    assert (got["id"], how) == (older, "fallback")
    run(pgc, 'UPDATE "ClintContacts" SET is_archive = true WHERE id = %s', older)
    assert choose(case) == (None, None)
    other = make_case(pgc, w.c1, w.portfolio)
    add_contact(pgc, other, "12345", default=True)                           # never guessed
    valid = add_contact(pgc, other, number(2))
    assert choose(other)[0]["id"] == valid


# --------------------------------------------------------------------------- 2026-10-21 (0080)
def test_unassigned_only_pool_option(cp_app, client, auth, world, pgc):
    """«عملاء غير مخصصين لمحصل فقط»: only cases with no collector, combinable with the latest
    note status, never with a collector filter. Campaign grant (2026-10-21): an unassigned case of
    the pool is offered to every member, plain collectors included."""
    from core.api_response import ApiError
    from services.calling import campaign_pool as P
    w = world
    a = "00000000-0000-4000-8000-00000000aa01"
    assert P.validate_pool({"unassigned_only": True}) == {"contract_ids": [], "portfolio_ids": [], "filter": {},
                                                          "unassigned_only": True}       # a criterion of its own
    assert "unassigned_only" not in P.validate_pool({"contract_ids": [a], "unassigned_only": False})
    for bad, code in (({"contract_ids": [a], "unassigned_only": "yes"}, "invalid"),
                      ({"unassigned_only": True, "filter": {"customeFilterUserValue[]": [a]}},
                       "conflicts_with_collector_filter")):
        with pytest.raises(ApiError) as e:
            P.validate_pool(bad)
        assert e.value.status == 422 and e.value.error == {"pool.unassigned_only": code}, e.value.error
    no_answer = note_status(pgc, "cp no answer")
    other = note_status(pgc, "cp other")
    free_no_answer = make_case(pgc, None, w.portfolio)
    add_contact(pgc, free_no_answer, number(4), default=True)
    note(pgc, free_no_answer, w.c1, no_answer)
    free_other = make_case(pgc, None, w.portfolio)
    add_contact(pgc, free_other, number(4), default=True)
    note(pgc, free_other, w.c1, other)
    owned, _ = case_for(pgc, w.c1, w.portfolio)
    note(pgc, owned, w.c1, no_answer)
    agent = new_supervisor_like(pgc, w)
    pool = {"contract_ids": [w.contract], "unassigned_only": True,
            "filter": {"customeFilterCommentStatusValue[]": [no_answer]}}
    cid = new_campaign(client, auth, w, owner=CP.super, members=[w.c1, agent], pool=pool, start=False)
    camp = load(cp_app, cid)
    assert camp["pool"]["unassigned_only"] is True
    items = client.get(f"/v2/campaigns/{cid}/pool", headers=auth(CP.super)).get_json()["data"]["items"]
    assert [(i["case"]["id"], i["reason"]) for i in items] == [(free_no_answer, "eligible")]
    # the campaign grant: a plain collector member is offered the unassigned case too
    assert [str(r["case_id"]) for r in in_app(cp_app, lambda s: P.candidates(camp, w.c1))] == [free_no_answer]
    assert [str(r["case_id"]) for r in in_app(cp_app, lambda s: P.candidates(camp, agent))] == [free_no_answer]
    only_c1 = in_app(cp_app, lambda s: P.summary(camp, [w.c1]))
    assert not only_c1["excluded"].get("no_member_in_scope")


def new_supervisor_like(pgc, w):
    """A caller whose scope is the whole contract (a supervisors row with the contract), with an
    extension: the members who can reach unassigned cases."""
    from test_calling_c2c_helpers import new_supervisor, own_extension
    u = new_supervisor(pgc, role=CP.collector_role, contracts=[w.contract], name="CP Contract agent")
    own_extension(pgc, u)
    return u


def test_latest_note_with_a_stopping_status_leaves_every_campaign(cp_app, client, auth, world, pgc):
    """stops_campaigns on the LATEST note's main status or sub-status (each row its own flag; a
    flagged main status covers its sub-statuses, a sub-status can be flagged alone)."""
    from services.calling import campaign_pool as P
    w = world
    stop_main = note_status(pgc, "cp paid", stops=True)
    plain_sub = note_status(pgc, "cp paid sub", parent=stop_main)
    plain_main = note_status(pgc, "cp court")
    stop_sub = note_status(pgc, "cp court full", parent=plain_main, stops=True)
    other_sub = note_status(pgc, "cp court part", parent=plain_main)
    c = {k: case_for(pgc, w.c1, w.portfolio)[0] for k in ("main", "sub", "main_plain_sub", "older", "other_sub",
                                                          "none")}
    note(pgc, c["main"], w.c1, stop_main, hours_ago=1)
    note(pgc, c["sub"], w.c1, plain_main, stop_sub, hours_ago=1)
    note(pgc, c["main_plain_sub"], w.c1, stop_main, plain_sub, hours_ago=1)
    note(pgc, c["older"], w.c1, stop_main, hours_ago=5)                  # an older note never counts
    note(pgc, c["older"], w.c1, plain_main, hours_ago=1)
    note(pgc, c["other_sub"], w.c1, plain_main, other_sub, hours_ago=1)
    cid = new_campaign(client, auth, w, members=[w.c1], start=False)
    camp = load(cp_app, cid)
    summary = in_app(cp_app, lambda s: P.summary(camp, [w.c1]))
    assert summary["excluded"] == {"status_stops_campaigns": 3} and summary["eligible_now"] == 3
    assert summary["remaining_today"] == 3                                # a hard exclusion
    got = {str(r["case_id"]) for r in in_app(cp_app, lambda s: P.candidates(camp, w.c1))}
    assert got == {c["older"], c["other_sub"], c["none"]}
    items = client.get(f"/v2/campaigns/{cid}/pool", headers=auth(w.supervisor),
                       query_string={"reason": "status_stops_campaigns"}).get_json()["data"]
    assert {i["case"]["id"] for i in items["items"]} == {c["main"], c["sub"], c["main_plain_sub"]}


def test_paid_off_cases_are_excluded_and_unapproved_money_is_not(cp_app, client, auth, world, pgc):
    """The money rule (services/money.py): approved, non-deleted, non-reversed payments >= the
    debt. Pending, deleted and reversed payments never make a case paid."""
    from services.calling import campaign_pool as P
    w = world

    def pay(case, amount, *, approved=True, deleted=False):
        return q1(pgc, """INSERT INTO "Payments" (clint_id, user_id, collection_type_id, collection_method_id, amount,
                                                   payment_date, approval_status, is_deleted) VALUES (%s, %s,
                         '00000000-0000-4000-8000-000000000704', '00000000-0000-4000-8000-000000000705', %s, now(),
                         %s, %s) RETURNING id""", case, w.c1, amount, approved, deleted)[0]
    paid = case_for(pgc, w.c1, w.portfolio, amount=100)[0]
    pay(paid, 60)
    pay(paid, 40)
    over = case_for(pgc, w.c1, w.portfolio, amount=100)[0]
    pay(over, 150)
    pending = case_for(pgc, w.c1, w.portfolio, amount=100)[0]
    pay(pending, 100, approved=False)
    deleted = case_for(pgc, w.c1, w.portfolio, amount=100)[0]
    pay(deleted, 100, deleted=True)
    reversed_ = case_for(pgc, w.c1, w.portfolio, amount=100)[0]
    rp = pay(reversed_, 100)
    run(pgc, """INSERT INTO payment_reversals (payment_id, case_id, amount, reason, reversed_by)
                VALUES (%s, %s, 100, 'test reversal', %s)""", rp, reversed_, w.supervisor)
    partly = case_for(pgc, w.c1, w.portfolio, amount=100)[0]
    pay(partly, 99.99)
    cid = new_campaign(client, auth, w, members=[w.c1], start=False)
    camp = load(cp_app, cid)
    items = client.get(f"/v2/campaigns/{cid}/pool", headers=auth(w.supervisor)).get_json()["data"]["items"]
    reasons = {i["case"]["id"]: i["reason"] for i in items}
    assert reasons == {paid: "case_paid", over: "case_paid", pending: "eligible", deleted: "eligible",
                       reversed_: "eligible", partly: "eligible"}
    got = {str(r["case_id"]) for r in in_app(cp_app, lambda s: P.candidates(camp, w.c1))}
    assert got == {pending, deleted, reversed_, partly}


def test_callable_numbers_rotation_order_and_blocked_spellings(cp_app, pgc, world):
    """One entry per number (duplicate rows and spellings fold), a number blocked under any
    spelling is skipped, e-mails and invalid values never count; default first, then the last
    bridged on the case, then the newest."""
    from services.calling import campaign_pool as P
    w = world
    case = make_case(pgc, w.c1, w.portfolio)
    idn = q1(pgc, 'SELECT "identityNumber" FROM clints WHERE id = %s', case)[0]
    default_n, old_n, new_n, bad_n = number(1), number(2), number(3), number(4)
    default = add_contact(pgc, case, default_n, default=True)
    add_contact(pgc, case, "0" + default_n[3:])                              # the same number, national form
    old = add_contact(pgc, case, old_n)
    new = add_contact(pgc, case, new_n)
    run(pgc, 'UPDATE "ClintContacts" SET create_at = now() - interval \'2 days\' WHERE id = %s', old)
    run(pgc, 'UPDATE "ClintContacts" SET create_at = now() - interval \'1 day\' WHERE id = %s', new)
    add_contact(pgc, case, bad_n, blocked=True)
    add_contact(pgc, case, "0" + bad_n[3:])                                   # wrong under another spelling
    add_contact(pgc, case, "debtor@example.invalid")
    add_contact(pgc, case, "12345")

    def numbers():
        return [n["id"] for n in in_app(cp_app, lambda s: P.callable_numbers(idn, case))]
    assert numbers() == [default, new, old]
    run(pgc, """INSERT INTO call_attempts (mode, state, case_id, collector_user_id, created_by, destination, bridged_at,
                                           created_at)
                VALUES ('click_to_call', 'ended', %s, %s, %s, %s, now() - interval '3 hours',
                        now() - interval '3 hours')""",
        case, w.c1, w.c1, "+" + old_n)
    assert numbers() == [default, old, new]                                   # the last success comes next
    nxt, tried = P.rotation([{"destination": d} for d in ("a", "b", "c")], ["a", "c"])
    assert nxt == {"destination": "b"} and tried == {"a", "c"}
    nxt, tried = P.rotation([{"destination": d} for d in ("a", "b", "c")], ["a", "c", "b", "a"])
    assert nxt == {"destination": "b"} and tried == {"a"}                     # round 2
    nxt, _ = P.rotation([{"destination": d} for d in ("a", "b", "c")], ["a", "b", "c"])
    assert nxt == {"destination": "a"}                                        # a full round: start again
