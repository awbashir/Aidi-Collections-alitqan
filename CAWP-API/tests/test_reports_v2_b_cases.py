"""Stream R-B: RPT-12 portfolios, RPT-13/14 cases with/without payment records, RPT-15
follow-up age and RPT-16 callbacks on my cases: inclusion, exact totals, scope, Riyadh day
bounds, the latest effective note and the Phase 1 follow-up status."""
from __future__ import annotations

import datetime as dt

from test_reports_v2_b_support import *  # noqa: F401,F403
from test_reports_v2_b_support import (B, archive, ids_of, legacy_get, make_case, make_contract, make_note,
                                       make_payment, make_portfolio, q, reassign, retire_note, riyadh_today)


def case_ids(d):
    return ids_of(d, "RPT-13")


def note_ids(d):
    return {i["note_id"] for i in d["items"]}


# =========================================================================== RPT-12 portfolios
def test_portfolio_counts_exact_money_and_case_state(rb, db):
    contract = make_contract(db, "RB portfolio contract")
    q(db, "INSERT INTO supervisor_contracts (id, supervisor_id, contract_id) VALUES (gen_random_uuid(), %s, %s)",
      ("e6e6e6e6-0000-4000-8000-000000000601", contract))
    from core import lookups
    lookups.invalidate()
    pf = make_portfolio(db, contract, name="RB محفظة المبالغ")
    empty = make_portfolio(db, contract, name="RB empty portfolio")
    q(db, "UPDATE portfolios SET create_at = now() - interval '10 days 2 hours' WHERE id = %s", (pf,))
    live1 = make_case(db, pf, owner=B.col1, amount=1000.005)     # a legacy 3-decimal debt: rounded per case
    live2 = make_case(db, pf, owner=B.col2, amount=2000.10)
    gone = make_case(db, pf, owner=B.col1, amount=500.00)
    archive(db, gone)
    make_payment(db, live1, "100.10", "approved", recorder=B.col1, approver=B.sup)
    make_payment(db, live1, "20.20", None, recorder=B.col1)
    make_payment(db, live2, "30.30", "rejected", recorder=B.col2, approver=B.sup)
    make_payment(db, live2, "40.40", "reversed", recorder=B.col2, approver=B.sup)
    make_payment(db, gone, "50.50", "approved", recorder=B.col1, approver=B.sup)
    dele = make_payment(db, live2, "60.60", None, recorder=B.col2)
    q(db, 'UPDATE "Payments" SET is_deleted = true WHERE id = %s', (dele,))      # legacy-deleted: not a record
    params = {"contract": contract}
    d = rb.rows("RPT-12", B.sup, params)
    assert ids_of(d, "RPT-12") == {pf, empty}
    assert d["totals"] == {"count": 2, "case_count": 3, "active_case_count": 2, "archived_case_count": 1,
                           "empty_portfolio_count": 1, "original_debt": "3500.11", "payment_count": 5,
                           "recorded_amount": "241.50", "approved_amount": "150.60", "pending_amount": "20.20",
                           "rejected_amount": "30.30", "reversed_amount": "40.40", "outstanding_amount": "3349.51"}
    row = {i["portfolio_id"]: i for i in d["items"]}
    assert row[pf]["age_days"] == 10 and row[pf]["portfolio_name"] == "RB محفظة المبالغ"
    assert row[pf]["contract_name"] == "RB portfolio contract" and row[pf]["portfolio_deleted"] is False
    assert {k: row[empty][k] for k in ("case_count", "original_debt", "payment_count", "recorded_amount",
                                       "approved_amount", "outstanding_amount")} == {
        "case_count": 0, "original_debt": "0.00", "payment_count": 0, "recorded_amount": "0.00",
        "approved_amount": "0.00", "outstanding_amount": "0.00"}                  # an empty portfolio: exact zeros
    # unarchived cases only: the portfolio stays, the archived case and its payment leave the aggregates
    d = rb.rows("RPT-12", B.sup, {**params, "case_state": "active"})
    assert d["total"] == 2
    assert d["totals"]["case_count"] == 2 and d["totals"]["original_debt"] == "3000.11"
    assert d["totals"]["approved_amount"] == "100.10" and d["totals"]["outstanding_amount"] == "2900.01"
    d = rb.rows("RPT-12", B.sup, {**params, "case_state": "archived"})
    assert d["totals"]["case_count"] == 1 and d["totals"]["approved_amount"] == "50.50"
    assert ids_of(rb.rows("RPT-12", B.sup, {**params, "has_cases": "no"}), "RPT-12") == {empty}
    assert ids_of(rb.rows("RPT-12", B.sup, {**params, "q": "المبالغ"}), "RPT-12") == {pf}
    # the portfolio filter narrows the rows and (pushed down) the aggregates identically
    d = rb.rows("RPT-12", B.sup, {"portfolio": pf})
    assert d["total"] == 1 and d["totals"]["recorded_amount"] == "241.50"


def test_portfolio_visibility_and_aggregates_follow_the_current_scope(rb, db):
    pa = make_portfolio(db, B.contract_a)                  # b.sup: supervised contract
    pa_empty = make_portfolio(db, B.contract_a)            # visible to b.sup although empty
    pb = make_portfolio(db, B.contract_b)                  # b.sup: only b.col2's cases there
    make_case(db, pa, owner=B.col1, amount=100)
    b1 = make_case(db, pb, owner=B.col1, amount=200)
    b2 = make_case(db, pb, owner=B.col2, amount=300)
    make_payment(db, b1, "11.00", "approved", recorder=B.col1, approver=B.sup)
    make_payment(db, b2, "22.00", "approved", recorder=B.col2, approver=B.sup)
    params = {"portfolio": f"{pa},{pa_empty},{pb}"}

    def rows(user):
        d = rb.rows("RPT-12", user, params)
        return {i["portfolio_id"]: (i["case_count"], i["original_debt"], i["approved_amount"]) for i in d["items"]}

    assert rows(B.super) == {pa: (1, "100.00", "0.00"), pa_empty: (0, "0.00", "0.00"), pb: (2, "500.00", "33.00")}
    # supervisor: contract A portfolios (even empty) + pb through the supervised user; only b2 counts in pb
    assert rows(B.sup) == {pa: (1, "100.00", "0.00"), pa_empty: (0, "0.00", "0.00"), pb: (1, "300.00", "22.00")}
    # collector: portfolios holding own cases only, and only own cases counted
    from test_reports_v2_support import set_perm
    set_perm(db, B.role_col, "report_portfolios", True)
    try:
        assert rows(B.col1) == {pa: (1, "100.00", "0.00"), pb: (1, "200.00", "11.00")}
        assert rows(B.col2) == {pb: (1, "300.00", "22.00")}
        # the portfolio selector lists only visible portfolios
        opts = rb.get("/v2/report-options/portfolio", B.col2, {"report": "RPT-12"}).get_json()["data"]
        assert pa not in {i["id"] for i in opts["items"]}
    finally:
        set_perm(db, B.role_col, "report_portfolios", False)
    # the management key alone (`portfolios`) opens the report with the holder's own scope
    assert rows(B.pf) == {}
    reassign(db, b1, B.col2)
    assert rows(B.sup)[pb] == (2, "500.00", "33.00")


def test_portfolio_created_filter_uses_riyadh_days(rb, db):
    contract = make_contract(db)
    early = make_portfolio(db, contract)
    late = make_portfolio(db, contract)
    q(db, "UPDATE portfolios SET create_at = '2026-02-28T20:59:59Z' WHERE id = %s", (early,))   # 28 Feb Riyadh
    q(db, "UPDATE portfolios SET create_at = '2026-02-28T21:00:00Z' WHERE id = %s", (late,))    # 1 Mar Riyadh
    d = rb.rows("RPT-12", B.super, {"contract": contract, "created_from": "2026-03-01", "created_to": "2026-03-01"})
    assert ids_of(d, "RPT-12") == {late}
    d = rb.rows("RPT-12", B.super, {"contract": contract, "created_to": "2026-02-28"})
    assert ids_of(d, "RPT-12") == {early}


# =========================================================================== RPT-13 / RPT-14
def test_cases_with_and_without_payment_records(rb, db):
    pf = make_portfolio(db, B.contract_a)
    approved = make_case(db, pf, owner=B.col1, amount=1000)
    pending_only = make_case(db, pf, owner=B.col1, amount=200.50)
    rejected_only = make_case(db, pf, owner=B.col1, amount=300)
    reversed_only = make_case(db, pf, owner=B.col1, amount=400)
    deleted_only = make_case(db, pf, owner=B.col1, amount=500)
    nothing = make_case(db, pf, owner=B.col1, amount=600.25)
    archived = make_case(db, pf, owner=B.col1, amount=700)
    archived_none = make_case(db, pf, owner=B.col1, amount=800)
    make_payment(db, approved, "100.00", "approved", approver=B.sup, when="2026-03-31T21:00:00Z")
    make_payment(db, approved, "25.50", None)
    make_payment(db, pending_only, "10.00", None)
    make_payment(db, rejected_only, "30.00", "rejected", approver=B.sup)
    make_payment(db, reversed_only, "40.00", "reversed", approver=B.sup)
    gone = make_payment(db, deleted_only, "50.00", None)
    q(db, 'UPDATE "Payments" SET is_deleted = true WHERE id = %s', (gone,))
    make_payment(db, archived, "70.00", "approved", approver=B.sup)
    archive(db, archived)
    archive(db, archived_none)
    d13 = rb.rows("RPT-13", B.sup, {"portfolio": pf})
    assert case_ids(d13) == {approved, pending_only, rejected_only, reversed_only}
    assert d13["totals"] == {"count": 4, "original_debt": "1900.50", "payment_count": 5, "recorded_amount": "205.50",
                             "approved_amount": "100.00", "pending_amount": "35.50", "rejected_amount": "30.00",
                             "reversed_amount": "40.00", "outstanding_amount": "1800.50", "cases_with_approved": 1,
                             "cases_unapproved_only": 3}
    row = {i["case_id"]: i for i in d13["items"]}
    assert row[approved]["payment_count"] == 2 and row[approved]["outstanding_amount"] == "900.00"
    assert row[approved]["last_payment_date"] == "2026-04-01"                    # Riyadh civil date
    assert row[reversed_only]["reversed_amount"] == "40.00" and row[reversed_only]["approved_amount"] == "0.00"
    assert row[reversed_only]["outstanding_amount"] == "400.00"                  # a reversal is not recovery
    assert case_ids(rb.rows("RPT-13", B.sup, {"portfolio": pf, "evidence": "unapproved_only"})) == \
        {pending_only, rejected_only, reversed_only}
    assert case_ids(rb.rows("RPT-13", B.sup, {"portfolio": pf, "evidence": "approved"})) == {approved}
    d14 = rb.rows("RPT-14", B.sup, {"portfolio": pf})
    assert case_ids(d14) == {deleted_only, nothing}                   # regardless of balance; archived excluded
    assert d14["totals"] == {"count": 2, "original_debt": "1100.25", "with_note_count": 0, "without_note_count": 2}
    # a new payment record in any status moves the case from RPT-14 to RPT-13
    make_payment(db, nothing, "1.00", "rejected", approver=B.sup)
    assert nothing in case_ids(rb.rows("RPT-13", B.sup, {"portfolio": pf}))
    assert nothing not in case_ids(rb.rows("RPT-14", B.sup, {"portfolio": pf}))


def test_case_reports_scope_and_filters(rb, db):
    pa = make_portfolio(db, B.contract_a)
    pb = make_portfolio(db, B.contract_b)
    mine = make_case(db, pa, owner=B.col1, name="RB عميل المحصل")
    theirs = make_case(db, pb, owner=B.col2)
    hidden = make_case(db, pb, owner=B.col1)                 # contract B, not supervised user: outside b.sup
    unassigned = make_case(db, pa, owner=None)
    both = {"portfolio": f"{pa},{pb}"}
    for rid in ("RPT-14", "RPT-15"):
        assert case_ids(rb.rows(rid, B.super, both)) == {mine, theirs, hidden, unassigned}, rid
        assert case_ids(rb.rows(rid, B.sup, both)) == {mine, theirs, unassigned}, rid
        assert case_ids(rb.rows(rid, B.sup, {**both, "owner": B.col1})) == {mine}, rid    # never widens
        assert case_ids(rb.rows(rid, B.sup, {**both, "assignment": "unassigned"})) == {unassigned}, rid
        assert case_ids(rb.rows(rid, B.sup, {**both, "owner_branch": B.branch2})) == {theirs}, rid
        assert case_ids(rb.rows(rid, B.sup, {**both, "contract": B.contract_b})) == {theirs}, rid
        assert case_ids(rb.rows(rid, B.sup, {**both, "q": "المحصل"})) == {mine}, rid
    owners = rb.get("/v2/report-options/owner", B.sup, {"report": "RPT-15", **both}).get_json()["data"]
    assert {i["id"] for i in owners["items"]} == {B.col1, B.col2}      # b.col1 through contract A only
    row = {i["case_id"]: i for i in rb.rows("RPT-14", B.sup, both)["items"]}
    assert row[theirs]["owner_name"] == "RB Collector2" and row[theirs]["owner_branch"] == "RB branch 2"
    assert row[unassigned]["owner_name"] is None


def test_case_reports_characterize_the_legacy_endpoints(rb, db, repb_legacy_app):
    """Legacy /clints/have_payments counts a legacy-deleted payment row as a record; v2 does not.
    Both use the same case scope; the legacy totalAmount is the float sum of the debts."""
    contract = make_contract(db)
    pf = make_portfolio(db, contract)
    deleted_only = make_case(db, pf, owner=B.col2, amount=10.10)
    with_pay = make_case(db, pf, owner=B.col2, amount=20.20)
    gone = make_payment(db, deleted_only, "5.00", None, recorder=B.col2)
    q(db, 'UPDATE "Payments" SET is_deleted = true WHERE id = %s', (gone,))
    make_payment(db, with_pay, "5.00", None, recorder=B.col2)
    params = [("customeFilterContractValue[]", contract), ("page", "1"), ("pageSize", "50")]
    have = legacy_get(repb_legacy_app, rb, "/clints/have_payments", B.sup, params)
    assert {c["id"] for c in have["clints"]} == {deleted_only, with_pay}
    assert case_ids(rb.rows("RPT-13", B.sup, {"contract": contract})) == {with_pay}
    assert case_ids(rb.rows("RPT-14", B.sup, {"contract": contract})) == {deleted_only}
    neglect = legacy_get(repb_legacy_app, rb, "/clints/neglect", B.sup, params)
    assert neglect["countRow"] == rb.rows("RPT-15", B.sup, {"contract": contract})["total"] == 2
    assert abs(neglect["totalAmount"] - 30.30) < 1e-9


# =========================================================================== RPT-15 follow-up age
def test_follow_up_age_uses_the_latest_effective_note(rb, db):
    pf = make_portfolio(db, B.contract_a)
    old = make_case(db, pf, owner=B.col1)
    recent = make_case(db, pf, owner=B.col1)
    silent = make_case(db, pf, owner=B.col1)
    future = make_case(db, pf, owner=B.col1)
    make_note(db, old, text="first", created=_ago(days=40, hours=1))
    last_old = make_note(db, old, text="=HYPERLINK(1)", created=_ago(days=10, hours=1))
    make_note(db, recent, user=B.col2, text="today", created=_ago(hours=2))
    make_note(db, future, text="future-dated", created=_ago(days=-2))
    d = rb.rows("RPT-15", B.sup, {"portfolio": pf})
    # oldest first; no note (unknown age) sorts last, never as zero
    assert [i["case_id"] for i in d["items"]] == [old, recent, future, silent]
    row = {i["case_id"]: i for i in d["items"]}
    assert row[old]["age_days"] == 10 and row[old]["last_note_text"] == "=HYPERLINK(1)"
    assert row[recent]["age_days"] == 0 and row[recent]["last_note_recorder_name"] == "RB Collector2"
    assert row[future]["age_days"] == -2                                    # not clamped
    assert row[silent]["age_days"] is None and row[silent]["last_note_at"] is None
    assert d["totals"] == {"count": 4, "with_note_count": 3, "without_note_count": 1, "over_30_days_count": 0,
                           "over_90_days_count": 0, "original_debt": "4000.00"}
    # retiring the latest note moves the case back to its previous effective note
    retire_note(db, last_old)
    row = {i["case_id"]: i for i in rb.rows("RPT-15", B.sup, {"portfolio": pf})["items"]}
    assert row[old]["age_days"] == 40 and row[old]["last_note_text"] == "first"
    d = rb.rows("RPT-15", B.sup, {"portfolio": pf})
    assert d["totals"]["over_30_days_count"] == 1
    # age buckets and the descending age sort (nulls last)
    assert case_ids(rb.rows("RPT-15", B.sup, {"portfolio": pf, "age": "31_90"})) == {old}
    assert case_ids(rb.rows("RPT-15", B.sup, {"portfolio": pf, "age": "0_7"})) == {recent, future}
    assert case_ids(rb.rows("RPT-15", B.sup, {"portfolio": pf, "age": "none"})) == {silent}
    assert case_ids(rb.rows("RPT-15", B.sup, {"portfolio": pf, "age": "none,31_90"})) == {silent, old}
    desc = [i["case_id"] for i in rb.rows("RPT-15", B.sup, {"portfolio": pf, "sort": "-age_days"})["items"]]
    assert desc == [old, recent, future, silent]


def test_follow_up_age_bucket_edges_and_note_date_bounds(rb, db):
    pf = make_portfolio(db, B.contract_a)
    edge7 = make_case(db, pf, owner=B.col1)
    edge8 = make_case(db, pf, owner=B.col1)
    make_note(db, edge7, created=_ago(days=8, minutes=-5))       # 7 days 23:55 -> age 7
    make_note(db, edge8, created=_ago(days=8, minutes=5))        # 8 days 00:05 -> age 8
    row = {i["case_id"]: i["age_days"] for i in rb.rows("RPT-15", B.sup, {"portfolio": pf})["items"]}
    assert row == {edge7: 7, edge8: 8}
    assert case_ids(rb.rows("RPT-15", B.sup, {"portfolio": pf, "age": "0_7"})) == {edge7}
    assert case_ids(rb.rows("RPT-15", B.sup, {"portfolio": pf, "age": "8_30"})) == {edge8}
    pf2 = make_portfolio(db, B.contract_a)
    a = make_case(db, pf2, owner=B.col1)
    b = make_case(db, pf2, owner=B.col1)
    make_note(db, a, created="2026-06-14T20:59:59Z")             # 14 June Riyadh
    make_note(db, b, created="2026-06-14T21:00:00Z")             # 15 June Riyadh
    d = rb.rows("RPT-15", B.sup, {"portfolio": pf2, "last_note_from": "2026-06-15", "last_note_to": "2026-06-15"})
    assert case_ids(d) == {b}


# =========================================================================== RPT-16 callbacks
def test_callbacks_on_my_cases_with_follow_up_status(rb, db):
    today = riyadh_today()
    pf = make_portfolio(db, B.contract_a)
    mine = make_case(db, pf, owner=B.col1)
    other_owner = make_case(db, pf, owner=B.col2)
    archived = make_case(db, pf, owner=B.col1)
    first = make_note(db, mine, text="call back", callback=_riyadh(today + dt.timedelta(days=3), 10),
                      created=_ago(days=2))
    second = make_note(db, mine, user=B.col2, text="by a colleague", callback=_riyadh(today + dt.timedelta(days=5), 11),
                       created=_ago(days=1))                     # supersedes `first`; recorder is someone else
    plain = make_note(db, mine, text="no callback", created=_ago(hours=5))
    retired = make_note(db, mine, text="retired", callback=_riyadh(today + dt.timedelta(days=6), 9), created=_ago(hours=4))
    retire_note(db, retired)
    make_note(db, other_owner, callback=_riyadh(today, 12))
    make_note(db, archived, callback=_riyadh(today, 12))
    archive(db, archived)
    d = rb.rows("RPT-16", B.col1, {"portfolio": pf})
    # one row per non-retired callback note on unarchived cases I own now; `retired` created a
    # follow-up that superseded `second`, which stays superseded after the retirement
    assert note_ids(d) == {first, second}
    assert plain not in note_ids(d)
    row = {i["note_id"]: i for i in d["items"]}
    assert row[first]["follow_up_status"] == "superseded" and row[second]["follow_up_status"] == "superseded"
    assert row[second]["recorder_name"] == "RB Collector2" and row[second]["owner_name"] == "محصل المواعيد"
    assert row[second]["callback_date"] == (today + dt.timedelta(days=5)).isoformat()
    assert row[first]["current_follow_up_at"] == row[second]["current_follow_up_at"] is not None
    assert d["totals"] == {"count": 2, "case_count": 1, "open_count": 0, "late_count": 0, "completed_count": 0,
                           "superseded_count": 2, "cancelled_count": 0, "untracked_count": 0}
    assert note_ids(rb.rows("RPT-16", B.col2, {"portfolio": pf})) != set()
    assert first not in note_ids(rb.rows("RPT-16", B.col2, {"portfolio": pf}))
    # a later callback note: open follow-up; a late one; an untracked (pre-follow-up) one
    newest = make_note(db, mine, callback=_riyadh(today + dt.timedelta(days=9), 9), created=_ago(minutes=30))
    pf2 = make_portfolio(db, B.contract_a)
    late_case = make_case(db, pf2, owner=B.col1)
    late = make_note(db, late_case, callback=_riyadh(today - dt.timedelta(days=2), 9), created=_ago(days=3))
    old_case = make_case(db, pf2, owner=B.col1)
    untracked = make_note(db, old_case, callback="2025-01-05T06:00:00Z", created="2025-01-01T09:00:00Z", track=False)
    d = rb.rows("RPT-16", B.col1, {"portfolio": f"{pf},{pf2}"})
    status = {i["note_id"]: i["follow_up_status"] for i in d["items"]}
    assert status[newest] == "open" and status[late] == "late" and status[untracked] == "untracked"
    assert d["totals"]["open_count"] == 1 and d["totals"]["late_count"] == 1 and d["totals"]["untracked_count"] == 1
    assert note_ids(rb.rows("RPT-16", B.col1, {"portfolio": f"{pf},{pf2}", "follow_up_status": "late,untracked"})) == \
        {late, untracked}
    # reassignment: the rows follow the case's current owner
    reassign(db, mine, B.col2)
    assert not (note_ids(rb.rows("RPT-16", B.col1, {"portfolio": pf})) & {first, second, newest})
    assert {first, second, newest} <= note_ids(rb.rows("RPT-16", B.col2, {"portfolio": pf}))


def test_callback_filter_uses_riyadh_day_bounds(rb, db):
    pf = make_portfolio(db, B.contract_a)
    case = make_case(db, pf, owner=B.col1)
    before = make_note(db, case, callback="2026-11-09T20:59:59Z", created="2026-09-01T08:00:00Z")   # 9 Nov Riyadh
    at = make_note(db, case, callback="2026-11-09T21:00:00Z", created="2026-09-01T08:01:00Z")       # 10 Nov Riyadh
    last = make_note(db, case, callback="2026-11-10T20:59:59Z", created="2026-09-01T08:02:00Z")     # 10 Nov Riyadh
    after = make_note(db, case, callback="2026-11-10T21:00:00Z", created="2026-09-01T08:03:00Z")    # 11 Nov Riyadh
    d = rb.rows("RPT-16", B.col1, {"portfolio": pf, "callback_from": "2026-11-10", "callback_to": "2026-11-10"})
    assert note_ids(d) == {at, last}
    assert {i["callback_date"] for i in d["items"]} == {"2026-11-10"}
    assert note_ids(rb.rows("RPT-16", B.col1, {"portfolio": pf, "callback_to": "2026-11-09"})) == {before}
    assert note_ids(rb.rows("RPT-16", B.col1, {"portfolio": pf, "callback_from": "2026-11-11"})) == {after}
    # the note's own creation date is a separate filter
    assert note_ids(rb.rows("RPT-16", B.col1, {"portfolio": pf, "note_created_from": "2026-09-01",
                                               "note_created_to": "2026-09-01"})) == {before, at, last, after}


def test_callbacks_characterize_the_legacy_endpoint(rb, db, repb_legacy_app):
    """Legacy paged next-calls: one row per callback comment of my unarchived cases, retired
    notes included, UTC date comparison; totalAmount repeats the case debt per row."""
    pf = make_portfolio(db, B.contract_b)
    case = make_case(db, pf, owner=B.col2, amount=1000)
    utc_only = make_note(db, case, user=B.col2, callback="2031-03-09T22:00:00Z")    # UTC 9 Mar, Riyadh 10 Mar
    both = make_note(db, case, user=B.col2, callback="2031-03-09T12:00:00Z")        # 9 Mar in both
    retired = make_note(db, case, user=B.col2, callback="2031-03-09T10:00:00Z")
    retire_note(db, retired)
    legacy = legacy_get(repb_legacy_app, rb, "/clints/currentUser/have_nextCalls", B.col2,
                        [("customeFilterDateValue[]", "2031-03-09"), ("customeFilterDateValue[]", "2031-03-09"),
                         ("page", "1"), ("pageSize", "50")])
    assert {c["comment"]["id"] for c in legacy["clints"]} == {utc_only, both, retired}
    assert legacy["countRow"] == 3 and abs(legacy["totalAmount"] - 3000) < 1e-9   # debt repeated per row
    v2 = rb.rows("RPT-16", B.col2, {"callback_from": "2031-03-09", "callback_to": "2031-03-09"})
    assert note_ids(v2) == {both}
    assert utc_only in note_ids(rb.rows("RPT-16", B.col2, {"callback_from": "2031-03-10", "callback_to": "2031-03-10"}))


# --------------------------------------------------------------------------- helpers
def _ago(days=0, hours=0, minutes=0) -> str:
    t = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days, hours=hours, minutes=minutes)
    return t.isoformat()


def _riyadh(day: dt.date, hour: int) -> str:
    from core import riyadh
    return dt.datetime.combine(day, dt.time(hour, 0), tzinfo=riyadh.RIYADH).astimezone(dt.timezone.utc).isoformat()
