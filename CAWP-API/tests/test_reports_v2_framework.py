"""Phase 4 R0: report framework (registry, definitions, validation, preview snapshot, stable
paging, selector options and scope, protected employee fields)."""
from __future__ import annotations

import uuid

import pytest

from test_reports_v2_support import *  # noqa: F401,F403
from test_reports_v2_support import R, make_case, make_contract, make_payment, make_portfolio, q


# --------------------------------------------------------------------------- a test-only report
def _probe():
    from core.reports import Built, Column, Ids, ReportDefinition, Sort

    class UsersProbe(ReportDefinition):
        id = "TST-R0"
        family = "test"
        label_en, label_ar = "Users probe", "اختبار المستخدمين"
        permissions = ("collections_users",)
        columns = (
            Column("user_id", "User id", "المعرف", "uuid", "u.id"),
            Column("user_name", "Login", "الدخول", "text", "u.user_name"),
            Column("mobile", "Mobile", "الجوال", "text", "u.mobile", protected="identity", subject="user_id"),
            Column("target", "Target", "الهدف", "money", "round(u.target_amount::numeric, 2)",
                   protected="target", subject="user_id"),
        )
        filters = (Ids("user", "User", "المستخدم", "user", "u.id"),)
        sorts = (Sort("user_name", "Login", "الدخول", "u.user_name"),)
        default_sort = "user_name"
        id_sql = "u.id"

        def build(self, ctx):
            return Built("users u", ["u.user_name LIKE 'r.%'"], {})

    return UsersProbe()


@pytest.fixture(scope="module")
def probe(rep_app):
    from core.reports import registry
    p = _probe()
    registry.reset_for_tests([p])
    return p


# --------------------------------------------------------------------------- registry / definitions
def test_definition_validation_rejects_bad_definitions():
    from core.reports import Built, Column, ReportDefinition, Sort

    class Bad(ReportDefinition):
        id, family, permissions, id_sql = "TST-BAD", "test", ("x",), "t.id"
        columns = (Column("a", "A", "أ", "text", "t.a"), Column("a", "A", "أ", "text", "t.b"))
        sorts = (Sort("a", "A", "أ", "t.a"),)
        default_sort = "a"

        def build(self, ctx):
            return Built("t", [], {})

    with pytest.raises(ValueError, match="duplicate column"):
        Bad().validate()
    Bad.columns = (Column("a", "A", "أ", "text", "t.a"),)
    Bad.default_sort = "nope"
    with pytest.raises(ValueError, match="default sort"):
        Bad().validate()
    with pytest.raises(ValueError):
        Column("x", "X", "س", "float", "1")


def test_reports_list_follows_screen_keys(rc):
    ids = lambda user: [i["id"] for i in rc.get("/v2/reports", user).get_json()["data"]["items"]]  # noqa: E731
    assert {"RPT-06", "RPT-07", "RPT-08"} <= set(ids(R.sup))
    col = ids(R.col1)
    assert "RPT-07" in col and "RPT-06" not in col and "RPT-08" not in col
    acct = ids(R.acct)
    assert "RPT-08" in acct and "RPT-06" not in acct and "RPT-07" not in acct
    assert [i for i in ids(R.none) if i.startswith("RPT-")] == []
    fams = rc.get("/v2/reports", R.sup).get_json()["data"]["families"]
    assert any(f["family"] == "collections" and "RPT-06" in f["reports"] for f in fams)


def test_definition_endpoint(rc):
    r = rc.get("/v2/reports/RPT-06", R.sup)
    assert r.status_code == 200
    d = r.get_json()["data"]
    assert d["id"] == "RPT-06" and d["family"] == "collections"
    assert d["labels"] == {"en": "All collections", "ar": "تحصيلات المستخدمين"}
    assert d["permissions"] == ["collections_users"]
    assert d["formats"] == ["csv", "xlsx", "xlsx_ar"]
    keys = [c["key"] for c in d["columns"]]
    assert "recorder_id" not in keys                         # hidden column never described
    assert keys[:4] == ["payment_id", "status", "amount", "payment_date"]
    amount = next(c for c in d["columns"] if c["key"] == "amount")
    assert amount["money"] is True and amount["currency"] == "SAR" and amount["type"] == "money"
    status = next(c for c in d["columns"] if c["key"] == "status")
    assert {e["value"] for e in status["enum"]} == {"pending", "approved", "rejected", "reversed"}
    f = {x["key"]: x for x in d["filters"]}
    assert f["payment_date"]["params"] == ["payment_date_from", "payment_date_to"]
    assert f["payment_date"]["semantics"] == "inclusive_civil_dates"
    assert f["portfolio"]["selector"]["endpoint"] == "/v2/report-options/portfolio"
    assert f["portfolio"]["depends_on"] == ["contract"]
    assert f["account"]["semantics"] == "literal_contains_case_insensitive"
    assert d["default_sort"] == "-created_at"
    assert d["totals"][0]["key"] == "count"
    assert {t["key"] for t in d["totals"]} >= {"recorded_amount", "approved_amount", "pending_amount",
                                                "rejected_amount", "reversed_amount", "case_count"}
    assert d["legacy"]["key"] == "collections_users"
    # permission and existence
    r = rc.get("/v2/reports/RPT-06", R.col1)
    assert r.status_code == 403 and r.get_json()["code"] == "forbidden"
    assert r.get_json()["error"] == {"required_any": ["collections_users"]}
    r = rc.get("/v2/reports/RPT-99", R.sup)
    assert r.status_code == 404 and r.get_json()["code"] == "report_not_found"
    assert rc.get("/v2/reports/rpt-06", R.sup).status_code == 200      # ids are case-insensitive


# --------------------------------------------------------------------------- validation
@pytest.mark.parametrize("params, errors", [
    ({"payment_date_from": "2026-13-01"}, {"payment_date_from": "invalid"}),
    ({"payment_date_from": "2026-03-10", "payment_date_to": "2026-03-01"}, {"payment_date_to": "reversed_range"}),
    ({"status": "approved,paid"}, {"status": "unknown_value"}),
    ({"creator": "not-a-uuid"}, {"creator": "invalid"}),
    ({"contract": ",".join(str(uuid.uuid4()) for _ in range(201))}, {"contract": "too_many"}),
    ({"amount_from": "1.001"}, {"amount_from": "too_precise"}),
    ({"amount_from": "-1"}, {"amount_from": "negative"}),
    ({"amount_from": "abc"}, {"amount_from": "invalid"}),
    ({"amount_from": "10", "amount_to": "9.99"}, {"amount_to": "reversed_range"}),
    ({"account": "x" * 101}, {"account": "too_long"}),
    ({"sort": "colour"}, {"sort": "unknown_value"}),
    ({"sort": "-nope"}, {"sort": "unknown_value"}),
    ({"page_size": "201"}, {"page_size": "invalid"}),
    ({"page": "0"}, {"page": "invalid"}),
    ({"page": "x"}, {"page": "invalid"}),
    ({"foo": "1"}, {"foo": "unknown_parameter"}),
])
def test_rows_validation_422(rc, params, errors):
    r = rc.get("/v2/reports/RPT-06/rows", R.sup, params)
    assert r.status_code == 422, r.get_json()
    body = r.get_json()
    assert body["code"] == "validation_failed" and body["status"] is False
    for k, v in errors.items():
        assert body["error"][k] == v


def test_validation_lists_every_bad_parameter(rc):
    r = rc.get("/v2/reports/RPT-06/rows", R.sup, {"payment_date_from": "x", "status": "nope", "creator": "1",
                                                   "bogus": "y"})
    assert r.status_code == 422
    assert r.get_json()["error"] == {"payment_date_from": "invalid", "status": "unknown_value",
                                     "creator": "invalid", "bogus": "unknown_parameter"}


def test_rpt07_rejects_filters_it_does_not_offer(rc):
    r = rc.get("/v2/reports/RPT-07/rows", R.col1, {"creator": R.col2, "status": "pending"})
    assert r.status_code == 422
    assert r.get_json()["error"] == {"creator": "unknown_parameter", "status": "unknown_parameter"}


def test_normalized_filters_echo_and_params_round_trip(rc, db):
    pf = make_portfolio(db)
    d = rc.rows("RPT-06", R.sup, {"portfolio": pf, "status": "rejected,approved", "payment_date_from": "2026-03-01",
                                  "amount_to": "10", "account": " RACC ", "sort": "amount"})
    assert d["filters"]["status"] == ["approved", "rejected"]          # canonical order
    assert d["filters"]["payment_date"] == {"from": "2026-03-01", "to": None}
    assert d["filters"]["amount"] == {"from": None, "to": "10.00"}
    assert d["filters"]["account"] == "RACC"
    assert d["params"] == {"payment_date_from": "2026-03-01", "status": "approved,rejected", "portfolio": pf,
                           "amount_to": "10.00", "account": "RACC"}
    again = rc.rows("RPT-06", R.sup, {**d["params"], "sort": "amount"})
    assert again["filters"] == d["filters"] and again["sort"] == "amount"


# --------------------------------------------------------------------------- snapshot totals
def test_totals_and_page_come_from_one_snapshot(rep_app, db):
    """A payment committed between the totals query and the page query is invisible to both."""
    from core.reports import engine, privacy, registry
    from services.clients_scope import resolve_scope
    pf = make_portfolio(db)
    case = make_case(db, pf)
    for amt in ("10.00", "20.00", "30.00"):
        make_payment(db, case, amt, "approved")
    report = registry.get("RPT-06")

    def sneak_in(_conn):
        make_payment(db, case, "999.99", "approved")          # autocommit: committed now

    with rep_app.app_context():
        out = engine.preview(report, {"portfolio": pf, "page_size": "50"}, R.super, resolve_scope(R.super),
                             privacy.FULL, hook=sneak_in)
        assert out["total"] == 3 and len(out["items"]) == 3
        assert out["totals"]["approved_amount"] == "60.00"
        assert "999.99" not in {i["amount"] for i in out["items"]}
        later = engine.preview(report, {"portfolio": pf}, R.super, resolve_scope(R.super), privacy.FULL)
        assert later["total"] == 4 and later["totals"]["approved_amount"] == "1059.99"
        assert out["snapshot_at"] <= later["snapshot_at"]


def test_exact_money_is_a_string_and_sums_exactly(rc, db):
    pf = make_portfolio(db)
    case = make_case(db, pf, amount=12345.67)
    for _ in range(10):
        make_payment(db, case, 0.1, "approved")                # 10 x 0.1 in float is 0.9999999999999999
    d = rc.rows("RPT-06", R.sup, {"portfolio": pf})
    assert d["totals"]["approved_amount"] == "1.00" and d["totals"]["recorded_amount"] == "1.00"
    assert {i["amount"] for i in d["items"]} == {"0.10"}
    assert {i["original_debt"] for i in d["items"]} == {"12345.67"}
    assert isinstance(d["totals"]["count"], int)


# --------------------------------------------------------------------------- stable paging
def test_stable_paging_across_110_tied_rows(rc, db):
    pf = make_portfolio(db)
    cases = [make_case(db, pf, name="Same name") for _ in range(5)]
    ids = {make_payment(db, cases[i % 5], "50.00", "approved", when="2026-03-10T21:00:00Z",
                        created="2026-03-12T10:00:00Z") for i in range(110)}
    for sort in ("-created_at", "created_at", "amount", "-payment_date", "customer_name", "-recorder_name"):
        seen = []
        for page in range(1, 7):
            d = rc.rows("RPT-06", R.sup, {"portfolio": pf, "sort": sort, "page": page, "page_size": 20})
            assert d["total"] == 110 and d["pages"] == 6
            seen.extend(i["payment_id"] for i in d["items"])
        assert len(seen) == 110 and set(seen) == ids, sort          # no row lost, none repeated
        whole = [i["payment_id"] for i in rc.rows("RPT-06", R.sup, {"portfolio": pf, "sort": sort,
                                                                  "page_size": 200})["items"]]
        assert seen == whole, sort                                  # same order as one big page
        desc = sort.startswith("-")
        assert whole == sorted(whole, reverse=desc), sort           # ties broken by payment id
    beyond = rc.rows("RPT-06", R.sup, {"portfolio": pf, "page": 7, "page_size": 20})
    assert beyond["items"] == [] and beyond["total"] == 110 and beyond["totals"]["approved_amount"] == "5500.00"


# --------------------------------------------------------------------------- selector options
def _make_user(db, first, last, login, branch=R.branch1):
    uid = str(uuid.uuid4())
    q(db, """INSERT INTO users (id, email, frist_name, last_name, mobile, password, user_name, role_id,
                                nationality_id, branch_id)
             VALUES (%s, %s, %s, %s, '0500000000', 'x', %s, %s, '00000000-0000-4000-8000-000000000101', %s)""",
      (uid, login + "@test.invalid", first, last, login, R.role_col, branch))
    return uid


def test_selector_pages_search_and_selected_beyond_page_one(rc, db):
    contract = make_contract(db, "Selector contract")
    pf = make_portfolio(db, contract)
    case = make_case(db, pf, owner=R.col1)
    tag = uuid.uuid4().hex[:6]
    users = []
    for i in range(53):
        first = {0: "100%", 1: "under_score", 2: "سارة"}.get(i, f"Sel{i:02d}")
        users.append(_make_user(db, first, f"{tag}", f"sel.{tag}.{i:02d}"))
        make_payment(db, case, "1.00", "approved", recorder=users[-1])
    base = {"report": "RPT-06", "contract": contract}
    p1 = rc.get("/v2/report-options/creator", R.super, base).get_json()["data"]
    assert p1["total"] == 53 and p1["pages"] == 2 and len(p1["items"]) == 50 and p1["page_size"] == 50
    assert p1["entity"] == "user"
    p2 = rc.get("/v2/report-options/creator", R.super, {**base, "page": 2}).get_json()["data"]
    assert len(p2["items"]) == 3
    assert {i["id"] for i in p1["items"]} | {i["id"] for i in p2["items"]} == set(users)
    last = p2["items"][-1]
    # a choice from page 2 is resolved on page 1 (reload / search)
    d = rc.get("/v2/report-options/creator", R.super, {**base, "selected": last["id"]}).get_json()["data"]
    assert d["selected"] == [last] and last not in d["items"]
    # literal search: % and _ are not wildcards; Arabic works
    s = rc.get("/v2/report-options/creator", R.super, {**base, "q": "%"}).get_json()["data"]
    assert [i["label"] for i in s["items"]] == [f"100% {tag}"]
    s = rc.get("/v2/report-options/creator", R.super, {**base, "q": "S_l"}).get_json()["data"]
    assert s["total"] == 0                                      # as a wildcard "_" would match "Sel.."
    s = rc.get("/v2/report-options/creator", R.super, {**base, "q": "_sc"}).get_json()["data"]
    assert [i["label"] for i in s["items"]] == [f"under_score {tag}"]
    s = rc.get("/v2/report-options/creator", R.super, {**base, "q": "سار"}).get_json()["data"]
    assert [i["label"] for i in s["items"]] == [f"سارة {tag}"]
    s = rc.get("/v2/report-options/creator", R.super, {**base, "q": f"SEL.{tag}.07"}).get_json()["data"]
    assert s["total"] == 1 and s["items"][0]["secondary"] == f"sel.{tag}.07"       # login searched too
    # unknown field / not a selector / RPT-07 fixes the creator
    r = rc.get("/v2/report-options/nope", R.super, {"report": "RPT-06"})
    assert r.status_code == 404 and r.get_json()["code"] == "unknown_option_field"
    assert rc.get("/v2/report-options/creator", R.col1, {"report": "RPT-07"}).status_code == 404
    assert rc.get("/v2/report-options/creator", R.super, {}).status_code == 422
    assert rc.get("/v2/report-options/creator", R.super, {**base, "selected": "x"}).status_code == 422


def test_selector_options_are_scoped_and_dependent(rc, db):
    pa = make_portfolio(db, R.contract_a, "Scoped A portfolio")
    pb = make_portfolio(db, R.contract_b, "Scoped B portfolio")
    ca = make_case(db, pa, owner=R.col1)
    cb = make_case(db, pb, owner=R.col2)
    make_payment(db, ca, "5.00", recorder=R.col2)          # col2 recorded on col1's case
    make_payment(db, cb, "6.00", recorder=R.col2)

    def ids(user, field, report="RPT-06", **extra):
        r = rc.get(f"/v2/report-options/{field}", user, {"report": report, **extra})
        assert r.status_code == 200, r.get_json()
        d = r.get_json()["data"]
        return {i["id"] for i in d["items"]}, d

    # supervisor of contract A: contract B and its portfolio never appear
    contracts, _ = ids(R.sup, "contract")
    assert R.contract_a in contracts and R.contract_b not in contracts
    portfolios, _ = ids(R.sup, "portfolio")
    assert pa in portfolios and pb not in portfolios
    # super: both; the portfolio list follows the chosen contract
    portfolios, d = ids(R.super, "portfolio", contract=R.contract_b)
    assert pb in portfolios and pa not in portfolios and d["depends_on"] == {"contract": [R.contract_b]}
    # a selected id outside the scope is not resolved (no existence leak)
    _, d = ids(R.sup, "portfolio", selected=f"{pa},{pb}")
    assert [s["id"] for s in d["selected"]] == [pa]
    # RPT-07 (collector): contract options come from the collector's own approved receipts only,
    # whoever owns the case (recorder rule)
    mine_c, theirs_c = make_contract(db), make_contract(db)
    on_col2_case = make_case(db, make_portfolio(db, mine_c), owner=R.col2)
    make_payment(db, on_col2_case, "1.00", "approved", recorder=R.col1)
    make_payment(db, make_case(db, make_portfolio(db, theirs_c), owner=R.col1), "1.00", "approved", recorder=R.col2)
    contracts, _ = ids(R.col1, "contract", report="RPT-07")
    assert mine_c in contracts and theirs_c not in contracts
    # the creator branch narrows the creators
    creators, _ = ids(R.super, "creator", creator_branch=R.branch2)
    assert R.col2 in creators and R.col1 not in creators
    # no report permission -> 403
    r = rc.get("/v2/report-options/contract", R.col1, {"report": "RPT-06"})
    assert r.status_code == 403


# --------------------------------------------------------------------------- protected fields
def test_protected_employee_fields_follow_user_privacy(rc, probe):
    def rows(user):
        d = rc.rows("TST-R0", user, {"page_size": 50})
        return {i["user_name"]: i for i in d["items"]}

    sup_view = rows(R.sup)                                      # no users_details
    assert sup_view["r.col1"]["mobile"] is None and sup_view["r.col1"]["target"] is None
    assert sup_view["r.sup"]["mobile"] == "0500005002"          # the viewer's own record
    full = rows(R.super)                                        # users_details
    assert full["r.col1"]["mobile"] == "0500005003" and full["r.col1"]["target"] == "0.00"
