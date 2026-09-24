"""Phase 4 R0: the collections reference reports RPT-06 (all), RPT-07 (my approved) and RPT-08
(approved): date boundaries, statuses and exact totals, reversal, scope, filters, protected
fields, and the documented differences from the legacy /payments endpoints."""
from __future__ import annotations

import os

import pytest

from test_reports_v2_support import *  # noqa: F401,F403
from test_reports_v2_support import R, make_case, make_contract, make_payment, make_portfolio, q, reverse


def ids(d):
    return {i["payment_id"] for i in d["items"]}


# --------------------------------------------------------------------------- dates
def test_payment_date_is_an_inclusive_riyadh_civil_date(rc, db):
    pf = make_portfolio(db)
    case = make_case(db, pf)
    stored = {
        "apr30_late": "2026-04-30T20:59:59Z",     # Riyadh 23:59:59 on 30 April -> out
        "may01_riyadh": "2026-04-30T21:00:00Z",   # Riyadh midnight 1 May (v2 writes) -> in
        "may01_utc": "2026-05-01T00:00:00Z",      # legacy date-only row (03:00 Riyadh) -> in
        "may31_riyadh": "2026-05-30T21:00:00Z",   # 31 May -> in
        "may31_utc": "2026-05-31T00:00:00Z",      # 31 May -> in
        "may31_last": "2026-05-31T20:59:59Z",     # Riyadh 23:59:59 on 31 May -> in
        "jun01_riyadh": "2026-05-31T21:00:00Z",   # 1 June -> out
    }
    pid = {k: make_payment(db, case, "1.00", "approved", when=v) for k, v in stored.items()}
    d = rc.rows("RPT-06", R.sup, {"portfolio": pf, "payment_date_from": "2026-05-01",
                                  "payment_date_to": "2026-05-31", "page_size": 50})
    assert ids(d) == {pid[k] for k in ("may01_riyadh", "may01_utc", "may31_riyadh", "may31_utc", "may31_last")}
    civil = {i["payment_id"]: i["payment_date"] for i in d["items"]}
    assert civil[pid["may01_riyadh"]] == civil[pid["may01_utc"]] == "2026-05-01"
    assert civil[pid["may31_last"]] == "2026-05-31"
    one = rc.rows("RPT-06", R.sup, {"portfolio": pf, "payment_date_from": "2026-05-31",
                                    "payment_date_to": "2026-05-31"})
    assert ids(one) == {pid["may31_riyadh"], pid["may31_utc"], pid["may31_last"]}
    since = rc.rows("RPT-06", R.sup, {"portfolio": pf, "payment_date_from": "2026-06-01"})
    assert ids(since) == {pid["jun01_riyadh"]}
    until = rc.rows("RPT-06", R.sup, {"portfolio": pf, "payment_date_to": "2026-04-30"})
    assert ids(until) == {pid["apr30_late"]}
    for report, user in (("RPT-07", R.col1), ("RPT-08", R.sup)):
        d = rc.rows(report, user, {"portfolio": pf, "payment_date_from": "2026-05-01", "payment_date_to": "2026-05-01"})
        assert ids(d) == {pid["may01_riyadh"], pid["may01_utc"]}, report


# --------------------------------------------------------------------------- statuses / totals / reversal
def test_statuses_and_exact_totals_per_report(rc, db):
    pf = make_portfolio(db)
    case = make_case(db, pf, amount=10000)
    p = {
        "approved": make_payment(db, case, "100.10", "approved"),
        "approved2": make_payment(db, case, "0.20", "approved", recorder=R.col2),
        "pending": make_payment(db, case, "50.05", None),
        "rejected": make_payment(db, case, "25.25", "rejected"),
        "reversed": make_payment(db, case, "300.30", "reversed"),
    }
    d = rc.rows("RPT-06", R.sup, {"portfolio": pf})
    assert d["totals"] == {"count": 5, "recorded_amount": "475.90", "approved_amount": "100.30", "approved_count": 2,
                           "pending_amount": "50.05", "pending_count": 1, "rejected_amount": "25.25",
                           "rejected_count": 1, "reversed_amount": "300.30", "reversed_count": 1, "case_count": 1}
    status = {i["payment_id"]: i["status"] for i in d["items"]}
    assert status == {p["approved"]: "approved", p["approved2"]: "approved", p["pending"]: "pending",
                      p["rejected"]: "rejected", p["reversed"]: "reversed"}
    # the status filter
    d = rc.rows("RPT-06", R.sup, {"portfolio": pf, "status": "pending,rejected"})
    assert ids(d) == {p["pending"], p["rejected"]} and d["totals"]["approved_amount"] == "0.00"
    assert d["totals"]["recorded_amount"] == "75.30"
    d = rc.rows("RPT-06", R.sup, {"portfolio": pf, "status": "all"})
    assert d["total"] == 5 and d["filters"]["status"] is None
    # RPT-08: approved only (the reversed one is not approved), not the pending desk
    d = rc.rows("RPT-08", R.sup, {"portfolio": pf})
    assert ids(d) == {p["approved"], p["approved2"]}
    assert d["totals"]["approved_amount"] == "100.30" == d["totals"]["recorded_amount"]
    assert d["totals"]["reversed_amount"] == d["totals"]["pending_amount"] == "0.00"
    # RPT-07: approved and recorded by me (col1)
    d = rc.rows("RPT-07", R.col1, {"portfolio": pf})
    assert ids(d) == {p["approved"]} and d["totals"]["approved_amount"] == "100.10"
    d = rc.rows("RPT-07", R.col2, {"portfolio": pf})
    # col2 recorded approved2 on col1's case: RPT-07 is "recorded by me", whoever owns the case
    assert ids(d) == {p["approved2"]} and d["totals"]["approved_amount"] == "0.20"


def test_reversal_keeps_history_but_never_counts_as_approved(rc, db):
    pf = make_portfolio(db)
    case = make_case(db, pf)
    pid = make_payment(db, case, "80.00", "approved")
    before = rc.rows("RPT-06", R.sup, {"portfolio": pf})
    assert before["totals"]["approved_amount"] == "80.00"
    assert rc.rows("RPT-08", R.sup, {"portfolio": pf})["total"] == 1
    assert rc.rows("RPT-07", R.col1, {"portfolio": pf})["total"] == 1
    reverse(db, pid, case, "80.00", reason="cheque returned")
    after = rc.rows("RPT-06", R.sup, {"portfolio": pf})
    row = after["items"][0]
    assert row["status"] == "reversed"
    assert row["approval_by_name"] == "Report Supervisor" and row["approval_at"] == "2026-03-11T08:00:00Z"
    assert row["reversal_reason"] == "cheque returned" and row["reversed_by_name"] == "Report Supervisor"
    assert row["reversed_at"] is not None
    assert after["totals"]["approved_amount"] == "0.00" and after["totals"]["reversed_amount"] == "80.00"
    assert after["totals"]["recorded_amount"] == "80.00"            # recorded is not recovery
    assert rc.rows("RPT-08", R.sup, {"portfolio": pf})["total"] == 0
    assert rc.rows("RPT-07", R.col1, {"portfolio": pf})["total"] == 0


def test_legacy_deleted_payment_that_was_never_reversed_is_excluded(rc, db):
    pf = make_portfolio(db)
    case = make_case(db, pf)
    keep = make_payment(db, case, "1.00", None)
    gone = make_payment(db, case, "2.00", None)
    q(db, 'UPDATE "Payments" SET is_deleted = true WHERE id = %s', (gone,))
    assert ids(rc.rows("RPT-06", R.sup, {"portfolio": pf})) == {keep}


# --------------------------------------------------------------------------- scope
def test_current_case_scope_is_authoritative(rc, db):
    pa = make_portfolio(db, R.contract_a)
    pb = make_portfolio(db, R.contract_b)
    a_col1 = make_case(db, pa, owner=R.col1)
    a_col2 = make_case(db, pa, owner=R.col2)
    b_col2 = make_case(db, pb, owner=R.col2)
    p1 = make_payment(db, a_col1, "1.00", "approved", recorder=R.col1)
    p2 = make_payment(db, a_col2, "2.00", "approved", recorder=R.col1)      # col1 recorded on col2's case
    p3 = make_payment(db, b_col2, "3.00", "approved", recorder=R.sup)       # sup recorded outside contract A
    both = {"portfolio": f"{pa},{pb}"}
    # supervisor of contract A: every recorder's payments on contract A cases; not contract B
    assert ids(rc.rows("RPT-06", R.sup, both)) == {p1, p2}
    assert ids(rc.rows("RPT-08", R.acct, both)) == {p1, p2}
    # SuperAdmin: all
    assert ids(rc.rows("RPT-06", R.super, both)) == {p1, p2, p3}
    # RPT-07 for col1: every approved payment col1 recorded, whoever owns the case (lead decision):
    # p2 is on col2's case and still col1's own work
    assert ids(rc.rows("RPT-07", R.col1, both)) == {p1, p2}
    # reassigning a_col1 to col2 changes nothing for col1's RPT-07 ...
    q(db, "UPDATE clints SET user_id = %s WHERE id = %s", (R.col2, a_col1))
    from core import lookups
    lookups.invalidate()
    assert ids(rc.rows("RPT-07", R.col1, both)) == {p1, p2}
    # ... and never shows payments recorded by someone else on col1's cases
    assert ids(rc.rows("RPT-07", R.col2, both)) == set()
    # RPT-06/08 keep the current case scope: the supervisor of contract A still sees p1, p2 (contract A)
    assert ids(rc.rows("RPT-06", R.sup, both)) == {p1, p2}
    # creator filters only narrow the scope, never widen it
    assert ids(rc.rows("RPT-06", R.sup, {**both, "creator": R.sup})) == set()
    assert ids(rc.rows("RPT-06", R.super, {**both, "creator": R.sup})) == {p3}
    # the creator branch is the recorder's branch (col1 = R1, sup = R1, col2 = R2)
    assert ids(rc.rows("RPT-06", R.super, {**both, "creator_branch": R.branch1})) == {p1, p2, p3}
    assert ids(rc.rows("RPT-06", R.super, {**both, "creator_branch": R.branch2})) == set()
    # contract filter
    assert ids(rc.rows("RPT-06", R.super, {**both, "contract": R.contract_b})) == {p3}
    # the screen keys
    assert rc.get("/v2/reports/RPT-06/rows", R.col1).status_code == 403
    assert rc.get("/v2/reports/RPT-08/rows", R.col1).status_code == 403
    assert rc.get("/v2/reports/RPT-07/rows", R.acct).status_code == 403
    assert rc.get("/v2/reports/RPT-06/rows", R.none).status_code == 403


def test_text_amount_and_account_filters(rc, db):
    pf = make_portfolio(db)
    c1 = make_case(db, pf, name="محمد عبدالله", account="00%_7\\X", identity="1111111111")
    c2 = make_case(db, pf, name="Sara Ali", account="0017AX", identity="2222222222")
    p1 = make_payment(db, c1, "10.00", "approved")
    p2 = make_payment(db, c2, "10.01", "approved")
    p3 = make_payment(db, c2, "99.99", "approved")
    base = {"portfolio": pf}
    assert ids(rc.rows("RPT-06", R.sup, {**base, "account": "%_7\\"})) == {p1}          # literal, not wildcards
    assert ids(rc.rows("RPT-06", R.sup, {**base, "account": "0017a"})) == {p2, p3}      # case-insensitive
    assert ids(rc.rows("RPT-06", R.sup, {**base, "q": "عبدالله"})) == {p1}
    assert ids(rc.rows("RPT-06", R.sup, {**base, "q": "2222"})) == {p2, p3}
    assert ids(rc.rows("RPT-06", R.sup, {**base, "amount_from": "10.01", "amount_to": "99.99"})) == {p2, p3}
    assert ids(rc.rows("RPT-06", R.sup, {**base, "amount_to": "10"})) == {p1}
    assert ids(rc.rows("RPT-06", R.sup, {**base, "amount_from": "0", "amount_to": "10.00"})) == {p1}


def test_rows_expose_no_protected_employee_fields(rc, db):
    """The collections reports carry no protected employee field (identity number, mobile,
    email, targets): only names/logins of the four roles. Nothing to redact, nothing leaks."""
    pf = make_portfolio(db)
    case = make_case(db, pf)
    make_payment(db, case, "5.00", "approved", recorder=R.col1)
    from core.reports import registry
    for rid in ("RPT-06", "RPT-07", "RPT-08"):
        rep = registry.get(rid)
        keys = {c.key for c in rep.columns}
        assert not keys & {"mobile", "email", "recorder_mobile", "recorder_identity", "target_amount"}
        assert all(c.protected is None for c in rep.columns), rid
    d = rc.rows("RPT-06", R.sup, {"portfolio": pf})
    values = {str(v) for v in d["items"][0].values()}
    assert not values & {"0500005003", "2000005003", "r.col1@test.invalid"}
    assert d["items"][0]["recorder_user_name"] == "r.col1" and d["items"][0]["recorder_name"] == "محصل التقارير"
    assert d["items"][0]["owner_name"] == "محصل التقارير"


def test_rpt08_provision_column(rc, db):
    pf = make_portfolio(db)
    case = make_case(db, pf, attribute={"البروفجن": 125.5})
    make_payment(db, case, "5.00", "approved")
    d = rc.rows("RPT-08", R.sup, {"portfolio": pf})
    assert d["items"][0]["provision"] == "125.5"
    assert "provision" not in rc.rows("RPT-06", R.sup, {"portfolio": pf})["items"][0]


# --------------------------------------------------------------------------- legacy vs v2 (test DB)
@pytest.fixture(scope="module")
def legacy_app(rep_app):
    from flask import Flask
    from sqlalchemy.pool import NullPool

    from conftest import MAIN_DB, db_url
    from extensions import db, jwt

    app = Flask("cawp_reports_legacy")
    app.config.update(TESTING=True, SQLALCHEMY_DATABASE_URI=db_url(MAIN_DB, "psycopg2"),
                      SQLALCHEMY_ENGINE_OPTIONS={"poolclass": NullPool}, SQLALCHEMY_TRACK_MODIFICATIONS=False,
                      JWT_SECRET_KEY=os.environ["JWT_SECRET_KEY"])
    db.init_app(app)
    jwt.init_app(app)
    import api.reports
    app.register_blueprint(api.reports.bp)
    return app


def _legacy(app, path, user, start, end):
    from flask_jwt_extended import create_access_token
    with app.app_context():
        token = create_access_token(identity=user)
    r = app.test_client().get(path, headers={"Authorization": f"Bearer {token}"},
                              query_string=[("customeFilterMoveDateValue[]", start),
                                            ("customeFilterMoveDateValue[]", end),
                                            ("page", "1"), ("pageSize", "1000")])
    assert r.status_code == 200, r.get_data(as_text=True)[:500]
    return r.get_json()


def test_documented_differences_from_the_legacy_endpoints(rc, db, legacy_app):
    """Each reconciliation category of review/phase4-framework.md, reproduced on synthetic rows."""
    contract = make_contract(db, "Legacy diff contract")
    q(db, "INSERT INTO supervisor_contracts (supervisor_id, contract_id) VALUES (%s, %s)",
      ("e5e5e5e5-0000-4000-8000-000000000601", contract))
    from core import lookups
    lookups.invalidate()
    try:
        pf = make_portfolio(db, contract)
        other = make_portfolio(db, R.contract_b)
        case = make_case(db, pf, owner=R.col1)
        foreign = make_case(db, other, owner=R.col2)
        same = make_payment(db, case, "10.00", "approved", when="2026-07-15T00:00:00Z")
        first_day = make_payment(db, case, "20.00", "approved", when="2026-06-30T21:00:00Z")   # civil 1 July
        reversed_ = make_payment(db, case, "30.00", "reversed", when="2026-07-10T00:00:00Z")
        by_sup_elsewhere = make_payment(db, foreign, "40.00", "approved", recorder=R.sup, when="2026-07-11T00:00:00Z")
        after_last = make_payment(db, case, "50.00", "approved", when="2026-07-31T21:00:00Z")  # civil 1 August

        legacy = _legacy(legacy_app, "/payments", R.sup, "2026-07-01", "2026-07-31")
        legacy_ids = {p["id"] for p in legacy["payments"]}
        v2 = ids(rc.rows("RPT-06", R.sup, {"payment_date_from": "2026-07-01", "payment_date_to": "2026-07-31",
                                           "contract": f"{contract},{R.contract_b}", "page_size": 200}))
        assert same in legacy_ids and same in v2
        # 1. inclusive civil dates: stored at Riyadh midnight of the first day (legacy `between` misses it)
        assert first_day not in legacy_ids and first_day in v2
        # 2. reversal: legacy hides it (is_deleted); v2 RPT-06 lists it as reversed (never approved)
        assert reversed_ not in legacy_ids and reversed_ in v2
        # 3. scope: legacy follows the recorder (the supervisor is in its own user list); v2 the current case
        assert by_sup_elsewhere in legacy_ids and by_sup_elsewhere not in v2
        assert after_last not in legacy_ids and after_last not in v2
        # RPT-08 vs /payments/approval_status: the same except the first-day row
        legacy8 = _legacy(legacy_app, "/payments/approval_status", R.sup, "2026-07-01", "2026-07-31")
        l8 = {p["id"] for p in legacy8["payments"]}
        v8 = rc.rows("RPT-08", R.sup, {"payment_date_from": "2026-07-01", "payment_date_to": "2026-07-31",
                                       "contract": contract})
        assert {same, by_sup_elsewhere} <= l8 and first_day not in l8
        assert ids(v8) == {same, first_day}
        # RPT-07 vs /payments/user (recorder col1, approved)
        legacy7 = _legacy(legacy_app, "/payments/user", R.col1, "2026-07-01", "2026-07-31")
        l7 = {p["id"] for p in legacy7["payments"]}
        assert same in l7 and first_day not in l7 and reversed_ not in l7
        assert {same, first_day} <= ids(rc.rows("RPT-07", R.col1, {"payment_date_from": "2026-07-01",
                                                                   "payment_date_to": "2026-07-31",
                                                                   "portfolio": pf}))
    finally:
        q(db, "DELETE FROM supervisor_contracts WHERE contract_id = %s", (contract,))
        lookups.invalidate()
