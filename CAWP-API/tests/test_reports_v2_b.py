"""Stream R-B: definitions, screen keys, validation and the promise reports RPT-09 (team),
RPT-10 (my recorded) and RPT-11 (my cases): inclusive civil due dates, v_promise_state
states with allocated approved evidence, exact totals, the recorder rule and scope."""
from __future__ import annotations

import datetime as dt

import pytest

from test_reports_v2_b_support import *  # noqa: F401,F403
from test_reports_v2_b_support import (ALL_B, B, archive, cancel_promise, make_case, make_payment,
                                       make_portfolio, make_promise, mark_imported, q, reassign, riyadh_today)

def pids(d):
    return {i["promise_id"] for i in d["items"]}


# =========================================================================== definitions / keys
EXPECTED_KEYS = {
    "RPT-09": ["report_users_paymentDates"],
    "RPT-10": ["report_collectors_currentUser_paymentDates"],
    "RPT-11": ["reports_performance", "report_collectors_currentUser_paymentDates"],
    "RPT-12": ["report_portfolios", "portfolios"],
    "RPT-13": ["report_clintes_active"],
    "RPT-14": ["report_clintes_inactive"],
    "RPT-15": ["report_quality_neglect"],
    "RPT-16": ["report_collectors_currentUser_nextCalls"],
}


@pytest.mark.parametrize("rid", ALL_B)
def test_definition_endpoint(rb, rid):
    r = rb.get(f"/v2/reports/{rid}", B.super)
    assert r.status_code == 200, r.get_json()
    d = r.get_json()["data"]
    assert d["id"] == rid and d["permissions"] == EXPECTED_KEYS[rid]
    assert d["formats"] == (["csv", "xlsx"] if rid == "RPT-11" else ["csv", "xlsx", "xlsx_ar"])
    assert d["labels"]["ar"] and d["labels"]["en"] and d["description"]["ar"]
    assert d["legacy"]["endpoint"].startswith("GET /")
    keys = [c["key"] for c in d["columns"]]
    assert len(keys) == len(set(keys))
    # hidden subject columns never leave the server
    assert "recorder_id" not in keys and "owner_id" not in keys
    for c in d["columns"]:
        assert c["labels"]["ar"], c["key"]
        if c["type"] == "money":
            assert c["money"] is True and c["currency"] == "SAR"
    assert d["totals"][0]["key"] == "count"
    for f in d["filters"]:
        if f["type"] == "date_range":
            assert f["semantics"] == "inclusive_civil_dates" and f["timezone"] == "Asia/Riyadh"
        if f["type"] == "timestamp_range":
            assert f["semantics"] == "riyadh_day_bounds"


def test_reports_list_follows_screen_keys(rb):
    def ids(user):
        return {i["id"] for i in rb.get("/v2/reports", user).get_json()["data"]["items"]} & set(ALL_B)
    assert ids(B.super) == set(ALL_B)
    assert ids(B.sup) == set(ALL_B)
    assert ids(B.col1) == {"RPT-10", "RPT-11", "RPT-16"}
    assert ids(B.pf) == {"RPT-12"}                    # the portfolio screen's key alone opens RPT-12
    assert ids(B.none) == set()
    fams = {f["family"]: set(f["reports"]) for f in rb.get("/v2/reports", B.super).get_json()["data"]["families"]}
    assert {"RPT-09", "RPT-10", "RPT-11"} <= fams["promises"]
    assert {"RPT-13", "RPT-14", "RPT-15"} <= fams["cases"]
    assert "RPT-12" in fams["portfolios"] and "RPT-16" in fams["callbacks"]


@pytest.mark.parametrize("user,rid", [(B.col1, "RPT-09"), (B.col1, "RPT-12"), (B.col1, "RPT-13"),
                                      (B.col1, "RPT-14"), (B.col1, "RPT-15"), (B.pf, "RPT-13"),
                                      (B.none, "RPT-10"), (B.none, "RPT-11"), (B.none, "RPT-16"), (B.pf, "RPT-09")])
def test_screen_key_is_required(rb, user, rid):
    r = rb.get(f"/v2/reports/{rid}/rows", user)
    assert r.status_code == 403
    body = r.get_json()
    assert body["code"] == "forbidden" and body["error"]["required_any"] == EXPECTED_KEYS[rid]
    assert rb.get(f"/v2/reports/{rid}", user).status_code == 403
    r = rb.post(f"/v2/reports/{rid}/exports", user, {"format": "csv", "params": {}})
    assert r.status_code == 403
    assert rb.get("/v2/report-options/contract", user, {"report": rid}).status_code == 403


def test_permission_revoked_takes_effect(rb, db):
    from test_reports_v2_support import set_perm
    assert rb.get("/v2/reports/RPT-16/rows", B.col1).status_code == 200
    set_perm(db, B.role_col, "report_collectors_currentUser_nextCalls", False)
    try:
        assert rb.get("/v2/reports/RPT-16/rows", B.col1).status_code == 403
    finally:
        set_perm(db, B.role_col, "report_collectors_currentUser_nextCalls", True)


def test_no_protected_employee_fields(rb, db):
    from core.reports import registry
    for rid in ALL_B:
        rep = registry.get(rid)
        for c in rep.columns:
            assert c.protected is None, (rid, c.key)
            assert not any(w in c.key for w in ("mobile", "email", "target")), (rid, c.key)
            if "identity" in c.key:
                assert c.sql == 'c."identityNumber"', (rid, c.key)     # the customer's, never an employee's
    pf = make_portfolio(db, B.contract_a)
    case = make_case(db, pf, owner=B.col1)
    make_promise(db, case, "10.00", riyadh_today(), recorder=B.col1)
    d = rb.rows("RPT-09", B.sup, {"portfolio": pf})
    blob = str(d["items"])
    for secret in ("0500006003", "rb.col1@test.invalid", "2000006003"):   # the recorder's mobile, email, ID
        assert secret not in blob


@pytest.mark.parametrize("rid,params,errors", [
    ("RPT-09", {"due_date_from": "2026-05-31", "due_date_to": "2026-05-01", "state": "paid"},
     {"due_date_to": "reversed_range", "state": "unknown_value"}),
    ("RPT-09", {"due_date_from": "31/05/2026"}, {"due_date_from": "invalid"}),
    ("RPT-10", {"recorder": B.col2}, {"recorder": "unknown_parameter"}),
    ("RPT-11", {"owner": B.col2}, {"owner": "unknown_parameter"}),
    ("RPT-12", {"case_state": "active,archived"}, {"case_state": "too_many"}),
    ("RPT-12", {"created_from": "2026-13-01"}, {"created_from": "invalid"}),
    ("RPT-13", {"evidence": "maybe", "owner": "not-a-uuid"}, {"evidence": "unknown_value", "owner": "invalid"}),
    ("RPT-15", {"age": "1000_plus", "sort": "age"}, None),
    ("RPT-16", {"callback_from": "2026-09-30", "callback_to": "2026-09-01", "follow_up_status": "done"},
     {"callback_to": "reversed_range", "follow_up_status": "unknown_value"}),
    ("RPT-09", {"amount_from": "10.001"}, {"amount_from": "too_precise"}),
])
def test_validation_lists_every_bad_parameter(rb, rid, params, errors):
    r = rb.get(f"/v2/reports/{rid}/rows", B.super, params)
    assert r.status_code == 422, r.get_json()
    body = r.get_json()
    assert body["code"] == "validation_failed"
    if errors is not None:
        assert body["error"] == errors
    else:
        assert body["error"] == {"age": "unknown_value"}


# =========================================================================== promises: dates
def test_due_date_is_an_inclusive_riyadh_civil_date(rb, db):
    pf = make_portfolio(db, B.contract_a)
    case = make_case(db, pf, owner=B.col1)
    stored = {
        "apr30_late": "2026-04-30T20:59:59Z",    # Riyadh 23:59:59 on 30 April -> out
        "may01_riyadh": "2026-04-30T21:00:00Z",  # Riyadh midnight 1 May -> in
        "may01_utc": "2026-05-01T00:00:00Z",     # legacy POST /payment_date storage (03:00 Riyadh) -> in
        "may31_utc": "2026-05-31T00:00:00Z",     # -> in
        "may31_last": "2026-05-31T20:59:59Z",    # Riyadh 23:59:59 on 31 May -> in
        "jun01_riyadh": "2026-05-31T21:00:00Z",  # 1 June -> out
    }
    pid = {k: make_promise(db, case, "1.00", v, recorder=B.col1) for k, v in stored.items()}
    inside = {pid[k] for k in ("may01_riyadh", "may01_utc", "may31_utc", "may31_last")}
    window = {"portfolio": pf, "due_date_from": "2026-05-01", "due_date_to": "2026-05-31"}
    for rid, user in (("RPT-09", B.sup), ("RPT-10", B.col1), ("RPT-11", B.col1)):
        d = rb.rows(rid, user, window)
        assert pids(d) == inside, rid
        due = {i["promise_id"]: i["due_date"] for i in d["items"]}
        assert due[pid["may01_riyadh"]] == due[pid["may01_utc"]] == "2026-05-01"
        assert due[pid["may31_last"]] == "2026-05-31"
        one = rb.rows(rid, user, {"portfolio": pf, "due_date_from": "2026-05-31", "due_date_to": "2026-05-31"})
        assert pids(one) == {pid["may31_utc"], pid["may31_last"]}, rid
        assert pids(rb.rows(rid, user, {"portfolio": pf, "due_date_from": "2026-06-01"})) == {pid["jun01_riyadh"]}
        assert pids(rb.rows(rid, user, {"portfolio": pf, "due_date_to": "2026-04-30"})) == {pid["apr30_late"]}


def test_entered_filter_uses_riyadh_day_bounds(rb, db):
    pf = make_portfolio(db, B.contract_a)
    case = make_case(db, pf, owner=B.col1)
    early = make_promise(db, case, "1.00", "2026-10-01", created="2026-03-09T20:59:59Z")   # 9 March Riyadh
    late = make_promise(db, case, "1.00", "2026-10-01", created="2026-03-09T21:00:00Z")    # 10 March Riyadh
    d = rb.rows("RPT-09", B.sup, {"portfolio": pf, "created_from": "2026-03-10", "created_to": "2026-03-10"})
    assert pids(d) == {late}
    d = rb.rows("RPT-10", B.col1, {"portfolio": pf, "created_to": "2026-03-09"})
    assert pids(d) == {early}


# =========================================================================== promises: states / totals
def test_states_come_from_allocated_approved_evidence_with_exact_totals(rb, db):
    today = riyadh_today()
    pf = make_portfolio(db, B.contract_a)
    c1, c2, c3, c4, c5 = (make_case(db, pf, owner=B.col1, amount=5000) for _ in range(5))
    p = {
        "open": make_promise(db, c1, "100.10", today + dt.timedelta(days=10)),
        "today": make_promise(db, c1, "1.00", today),                               # due today: still open
        "overdue": make_promise(db, c1, "200.20", today - dt.timedelta(days=1)),
        "fulfilled": make_promise(db, c2, "300.30", today + dt.timedelta(days=5)),
        "partial": make_promise(db, c3, "400.00", today + dt.timedelta(days=3)),
        "cancelled": make_promise(db, c4, "50.00", today + dt.timedelta(days=3)),
        "imported": make_promise(db, c5, "70.00", today - dt.timedelta(days=30)),
    }
    deleted = make_promise(db, c4, "999.00", today, deleted=True)
    make_payment(db, c2, "300.30", "approved", recorder=B.col1, approver=B.sup)    # auto-allocated: fulfils
    make_payment(db, c3, "150.00", "approved", recorder=B.col1, approver=B.sup)    # partial evidence
    make_payment(db, c1, "999.00", None, recorder=B.col1)                          # pending: never evidence
    cancel_promise(db, p["cancelled"])
    mark_imported(db, p["imported"], c5)
    d = rb.rows("RPT-09", B.sup, {"portfolio": pf})
    assert deleted not in pids(d)
    assert d["totals"] == {
        "count": 7, "promised_amount": "1121.60", "allocated_amount": "450.30", "remaining_amount": "621.30",
        "open_count": 3, "open_amount": "501.10", "overdue_count": 2, "overdue_amount": "270.20",
        "fulfilled_count": 1, "fulfilled_amount": "300.30", "cancelled_count": 1, "cancelled_amount": "50.00",
        "imported_baseline_count": 1, "case_count": 5}
    row = {i["promise_id"]: i for i in d["items"]}
    assert {k: row[v]["state"] for k, v in p.items()} == {
        "open": "open", "today": "open", "overdue": "overdue", "fulfilled": "fulfilled", "partial": "open",
        "cancelled": "cancelled", "imported": "overdue"}
    assert row[p["fulfilled"]]["allocated"] == "300.30" and row[p["fulfilled"]]["remaining"] == "0.00"
    assert row[p["fulfilled"]]["fulfilled_at"] is not None
    assert row[p["partial"]]["allocated"] == "150.00" and row[p["partial"]]["remaining"] == "250.00"
    assert row[p["open"]]["allocated"] == "0.00" and row[p["open"]]["fulfilled_at"] is None
    assert row[p["cancelled"]]["remaining"] == "0.00" and row[p["cancelled"]]["cancel_reason"] == "customer withdrew"
    assert row[p["cancelled"]]["cancelled_by_name"] == "RB Supervisor" and row[p["cancelled"]]["cancelled_at"]
    assert row[p["imported"]]["imported_baseline"] is True and row[p["open"]]["imported_baseline"] is False
    assert row[p["today"]]["due_date"] == today.isoformat()
    assert row[p["open"]]["original_debt"] == "5000.00"             # per-row context, never summed
    assert row[p["open"]]["recorder_name"] == "محصل المواعيد" and row[p["open"]]["owner_name"] == "محصل المواعيد"
    # the state filter narrows the rows and the totals together
    d = rb.rows("RPT-09", B.sup, {"portfolio": pf, "state": "open,fulfilled"})
    assert pids(d) == {p["open"], p["today"], p["partial"], p["fulfilled"]}
    assert d["totals"]["promised_amount"] == "801.40" and d["totals"]["overdue_count"] == 0
    assert rb.rows("RPT-09", B.sup, {"portfolio": pf, "state": "all"})["total"] == 7
    # the same totals on the other two variants (col1 recorded them all and owns every case)
    for rid in ("RPT-10", "RPT-11"):
        t = rb.rows(rid, B.col1, {"portfolio": pf})["totals"]
        assert t["count"] == 7 and t["promised_amount"] == "1121.60" and t["allocated_amount"] == "450.30", rid
    # reversing the fulfilling payment voids its allocation: the promise is open again
    pay = q(db, 'SELECT id FROM "Payments" WHERE clint_id = %s', (c2,))[0][0]
    from test_reports_v2_support import reverse
    reverse(db, pay, c2, "300.30")
    d = rb.rows("RPT-09", B.sup, {"portfolio": pf, "state": "fulfilled"})
    assert d["total"] == 0
    assert rb.rows("RPT-09", B.sup, {"portfolio": pf})["totals"]["allocated_amount"] == "150.00"


def test_amount_and_text_filters(rb, db):
    pf = make_portfolio(db, B.contract_a)
    case = make_case(db, pf, owner=B.col1, name="عميل البحث", account="RB-AC%_7")
    other = make_case(db, pf, owner=B.col1)
    a = make_promise(db, case, "10.00", "2026-10-01")
    b = make_promise(db, case, "10.01", "2026-10-01")
    c = make_promise(db, other, "9.99", "2026-10-01")
    assert pids(rb.rows("RPT-09", B.sup, {"portfolio": pf, "amount_from": "10.00", "amount_to": "10.00"})) == {a}
    assert pids(rb.rows("RPT-09", B.sup, {"portfolio": pf, "amount_to": "10.00"})) == {a, c}
    assert pids(rb.rows("RPT-09", B.sup, {"portfolio": pf, "account": "ac%_7"})) == {a, b}   # literal, case-insensitive
    assert pids(rb.rows("RPT-10", B.col1, {"portfolio": pf, "q": "البحث"})) == {a, b}


# =========================================================================== recorder rule / scope
def test_recorder_rule_and_current_scope(rb, db):
    pa = make_portfolio(db, B.contract_a)
    pb = make_portfolio(db, B.contract_b)
    a1 = make_case(db, pa, owner=B.col1)            # contract A: in b.sup's contract scope
    b2 = make_case(db, pb, owner=B.col2)            # contract B, owned by b.col2 (supervised by b.sup)
    b1 = make_case(db, pb, owner=B.col1)            # contract B, owned by b.col1: outside b.sup's scope
    p1 = make_promise(db, a1, "1.00", "2026-10-01", recorder=B.col1)
    p2 = make_promise(db, b2, "2.00", "2026-10-01", recorder=B.col1)   # col1 recorded on col2's case
    p3 = make_promise(db, b1, "3.00", "2026-10-01", recorder=B.col2)   # col2 recorded on col1's case
    p4 = make_promise(db, b1, "4.00", "2026-10-01", recorder=B.sup)
    both = {"portfolio": f"{pa},{pb}"}
    # RPT-10: recorder = me, whatever the case's owner or my scope (p2 is on col2's case)
    assert pids(rb.rows("RPT-10", B.col1, both)) == {p1, p2}
    assert pids(rb.rows("RPT-10", B.col2, both)) == {p3}
    assert pids(rb.rows("RPT-10", B.sup, both)) == {p4}               # even though b1 is outside b.sup's scope
    # RPT-11: cases I own now, any recorder
    assert pids(rb.rows("RPT-11", B.col1, both)) == {p1, p3, p4}
    assert pids(rb.rows("RPT-11", B.col2, both)) == {p2}
    assert pids(rb.rows("RPT-11", B.col1, {**both, "recorder": B.sup})) == {p4}
    # RPT-09: the current case scope (contract A OR supervised users' cases)
    assert pids(rb.rows("RPT-09", B.sup, both)) == {p1, p2}
    assert pids(rb.rows("RPT-09", B.super, both)) == {p1, p2, p3, p4}
    # filters only narrow: a recorder or owner outside the scope gives nothing more
    assert pids(rb.rows("RPT-09", B.sup, {**both, "recorder": B.col1})) == {p1, p2}
    assert pids(rb.rows("RPT-09", B.sup, {**both, "recorder": B.sup})) == set()
    assert pids(rb.rows("RPT-09", B.sup, {**both, "owner": B.col1})) == {p1}
    # selectors stay inside the scope as well: only recorders of promises b.sup may see
    def recorders():
        d = rb.get("/v2/report-options/recorder", B.sup, {"report": "RPT-09", **both}).get_json()["data"]
        return {i["id"] for i in d["items"]}
    assert recorders() == {B.col1}
    # reassignment: RPT-11 follows the new owner, RPT-10 keeps the recorder's rows, RPT-09 widens
    reassign(db, b1, B.col2)
    assert recorders() == {B.col1, B.col2, B.sup}
    assert pids(rb.rows("RPT-11", B.col1, both)) == {p1}
    assert pids(rb.rows("RPT-11", B.col2, both)) == {p2, p3, p4}
    assert pids(rb.rows("RPT-10", B.col1, both)) == {p1, p2}
    assert pids(rb.rows("RPT-09", B.sup, both)) == {p1, p2, p3, p4}


def test_rpt11_is_unarchived_only_and_rpt09_can_filter_archive_state(rb, db):
    pf = make_portfolio(db, B.contract_a)
    live = make_case(db, pf, owner=B.col1)
    gone = make_case(db, pf, owner=B.col1)
    p_live = make_promise(db, live, "5.00", "2026-10-01")
    p_gone = make_promise(db, gone, "6.00", "2026-10-01")
    archive(db, gone)
    assert pids(rb.rows("RPT-11", B.col1, {"portfolio": pf})) == {p_live}
    assert pids(rb.rows("RPT-10", B.col1, {"portfolio": pf})) == {p_live, p_gone}    # my recorded work stays
    d = rb.rows("RPT-09", B.sup, {"portfolio": pf})
    assert pids(d) == {p_live, p_gone}
    assert {i["promise_id"]: i["case_archived"] for i in d["items"]} == {p_live: False, p_gone: True}
    assert pids(rb.rows("RPT-09", B.sup, {"portfolio": pf, "case_state": "active"})) == {p_live}
    assert pids(rb.rows("RPT-09", B.sup, {"portfolio": pf, "case_state": "archived"})) == {p_gone}


def test_latest_note_status_is_the_latest_effective_note(rb, db):
    from test_reports_v2_b_support import make_note, retire_note
    pf = make_portfolio(db, B.contract_a)
    case = make_case(db, pf, owner=B.col1)
    make_promise(db, case, "5.00", "2026-10-01")
    q(db, """INSERT INTO "CommentStatuses" (id, name, contract_id, is_active)
             VALUES ('e6e6e6e6-0000-4000-8000-000000000701', 'RB second status', NULL, true) ON CONFLICT DO NOTHING""")
    make_note(db, case, text="older", created="2026-09-01T09:00:00Z")
    newer = make_note(db, case, text="newer", created="2026-09-02T09:00:00Z",
                      status="e6e6e6e6-0000-4000-8000-000000000701")
    row = rb.rows("RPT-09", B.sup, {"portfolio": pf})["items"][0]
    assert row["last_note_status"] == "RB second status" and row["last_note_at"] == "2026-09-02T09:00:00Z"
    retire_note(db, newer)
    row = rb.rows("RPT-09", B.sup, {"portfolio": pf})["items"][0]
    assert row["last_note_status"] == "Promise" and row["last_note_at"] == "2026-09-01T09:00:00Z"


def test_legacy_endpoints_characterized(rb, db, repb_legacy_app):
    """The real legacy handlers on synthetic rows: /payment_date is unscoped (v2 RPT-09 applies
    the case scope), /payment_date/currentUser sums original debts in totalAmount (v2 sums the
    promised amounts), and /payment_date/user lists deleted promises (v2 excludes them)."""
    from test_reports_v2_b_support import legacy_get
    pb = make_portfolio(db, B.contract_b)
    b1 = make_case(db, pb, owner=B.col1, amount=7777.77)
    p1 = make_promise(db, b1, "12.34", "2031-01-15", recorder=B.col1)
    p_del = make_promise(db, b1, "5.00", "2031-01-15", recorder=B.col1, deleted=True)
    win = [("customeFilterDateValue[]", "2031-01-15"), ("customeFilterDateValue[]", "2031-01-15"),
           ("page", "1"), ("pageSize", "50")]
    legacy = legacy_get(repb_legacy_app, rb, "/payment_date", B.sup, win)
    assert p1 in {x["id"] for x in legacy["payment_dates"]}             # b.sup sees col1's contract-B promise
    assert p1 not in pids(rb.rows("RPT-09", B.sup, {"due_date_from": "2031-01-15", "due_date_to": "2031-01-15"}))
    mine = legacy_get(repb_legacy_app, rb, "/payment_date/currentUser", B.col1, win)
    assert mine["countRow"] == 1 and abs(mine["totalAmount"] - 7777.77) < 1e-6      # the case's debt
    v2 = rb.rows("RPT-10", B.col1, {"due_date_from": "2031-01-15", "due_date_to": "2031-01-15"})
    assert v2["totals"]["promised_amount"] == "12.34" and pids(v2) == {p1}
    owned = legacy_get(repb_legacy_app, rb, "/payment_date/user", B.col1)
    assert {p1, p_del} <= {x["id"] for x in owned["payment_dates"]}
    assert p_del not in pids(rb.rows("RPT-11", B.col1, {"portfolio": pb}))
