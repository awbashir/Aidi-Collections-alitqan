"""Phase 4 stream R-C: RPT-17 ... RPT-24 (the CDR parts of RPT-23/25 are in
test_reports_v2_c_cdr.py). Fixtures: tests/fixtures/seed_reports_c.sql, per-test contracts
for the daily reports. Every amount is compared as an exact decimal string."""
from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal as D

import pytest

from test_reports_v2_c_support import *  # noqa: F401,F403
from test_reports_v2_c_support import C, by, make_case, make_payment, make_portfolio, q, q1, read_csv, run_export, set_perm


def rows(rc, report, user, **params):
    items, first = rc.all_rows(report, user, params)
    return items, first["totals"]


def legacy(rc, path, user, params=None):
    r = rc.get(path, user, params)
    assert r.status_code == 200, (r.status_code, r.get_data(as_text=True)[:300])
    return r.get_json()


def csv_equals_preview(rc, report, user, params, fmt="csv"):
    items, first = rc.all_rows(report, user, params)
    exp = rc.export(report, user, params, fmt=fmt)
    assert run_export(rc.app, exp["id"]) == "succeeded"
    done = rc.export_get(exp["id"], user)
    assert done["row_count"] == len(items) and done["totals"] == first["totals"]
    header, body = read_csv(rc.download(exp["id"], user).data)
    from core.bulk.artifacts import safe_cell
    from core.reports import registry
    types = {c.key: c.type for c in registry.get(report).visible_columns(for_export=True)}
    assert header == list(types)                     # the definition's column order (JSON keys are sorted)
    if items:
        assert set(header) == set(items[0])

    def cell(k, v):
        if v is None:
            return ""
        if isinstance(v, bool):
            return "true" if v else "false"
        return safe_cell(v) if types[k] in ("text", "enum", "json") else str(v)
    assert body == [[cell(k, it[k]) for k in header] for it in items]
    return items, first


# =========================================================================== registry, keys
def test_keys_and_catalogue(rc):
    from core.reports import registry
    keys = {r.id: r.permissions for r in registry.all_reports() if r.id >= "RPT-17"}
    assert keys == {"RPT-17": ("dashbord_user",), "RPT-18": ("dashbord_user",),
                    "RPT-19": ("dashbord_user", "dashbord_admin"), "RPT-20": ("dashbord_admin",),
                    "RPT-21": ("dashbord_admin",), "RPT-22": ("dashbord_admin",), "RPT-23": ("dashbord_admin",),
                    "RPT-24": ("reports_performance",), "RPT-25": ("clints_callsRecords", "reports_calls")}

    def listed(user):
        return {i["id"] for i in rc.get("/v2/reports", user).get_json()["data"]["items"] if i["id"] >= "RPT-17"}
    assert listed(C.col1) == {"RPT-17", "RPT-18", "RPT-19", "RPT-25"}
    assert listed(C.sup) == {"RPT-19", "RPT-20", "RPT-21", "RPT-22", "RPT-23", "RPT-24", "RPT-25"}
    assert listed(C.none) == set()
    for rid in ("RPT-20", "RPT-21", "RPT-22", "RPT-23", "RPT-24"):
        body = rc.rows(rid, C.col1, expect=403)
        assert body["code"] == "forbidden" and body["error"]["required_any"]
    for rid in ("RPT-17", "RPT-18"):
        assert rc.rows(rid, C.sup, expect=403)["code"] == "forbidden"
    for rid in [f"RPT-{n}" for n in range(17, 26)]:
        assert rc.rows(rid, C.none, expect=403)["code"] == "forbidden"
        assert rc.post(f"/v2/reports/{rid}/exports", C.none, {"format": "csv"}).status_code == 403


def test_definitions(rc):
    d = rc.get("/v2/reports/RPT-23", C.sup).get_json()["data"]
    f = {x["key"]: x for x in d["filters"]}
    assert f["day"]["default"] == "today" and f["day"]["semantics"] == "riyadh_day_bounds"
    assert f["hours"]["params"] == ["from_hour", "to_hour"] and f["hours"]["default"] == {"from": 8, "to": 17}
    assert f["mode"]["default"] == ["notes"] and f["mode"]["multi"] is False
    assert [c["key"] for c in d["columns"]][:4] == ["position", "collector_name", "user_name", "extension"]
    d19 = rc.get("/v2/reports/RPT-19", C.sup).get_json()["data"]
    cols = {c["key"]: c for c in d19["columns"]}
    assert cols["user_target_value"]["protected"] == "target" and cols["target_amount"]["money"]
    assert cols["actual"]["role"] == "owner" and cols["actual_recorded"]["role"] == "recorder"
    assert {x["key"]: x for x in d19["filters"]}["month"]["default"] == "current_month"
    d25 = rc.get("/v2/reports/RPT-25", C.sup).get_json()["data"]
    f25 = {x["key"]: x for x in d25["filters"]}
    assert f25["call_date"]["max_days"] == 31 and f25["call_date"]["semantics"] == "riyadh_day_bounds"
    assert f25["owner"]["depends_on"] == ["call_date"]


@pytest.mark.parametrize("report,params,errors", [
    ("RPT-19", {"month": "2026-13"}, {"month": "invalid"}),
    ("RPT-19", {"month": "June"}, {"month": "invalid"}),
    ("RPT-22", {"as_of": "2026-02-30"}, {"as_of": "invalid"}),
    ("RPT-23", {"from_hour": "18", "to_hour": "8"}, {"to_hour": "reversed_range"}),
    ("RPT-23", {"from_hour": "24"}, {"from_hour": "invalid"}),
    ("RPT-23", {"mode": "sms"}, {"mode": "unknown_value"}),
    ("RPT-23", {"mode": "notes,calls"}, {"mode": "too_many"}),
    ("RPT-20", {"payment_date_from": "2026-01-01", "payment_date_to": "2026-06-30"}, {"payment_date_to": "range_too_long"}),
    ("RPT-21", {"due_date_from": "2026-06-30", "due_date_to": "2026-06-01"}, {"due_date_to": "reversed_range"}),
    ("RPT-21", {"state": "paid"}, {"state": "unknown_value"}),
    ("RPT-24", {"section": "calls"}, {"section": "unknown_value"}),
    ("RPT-24", {"activity_date_from": "2025-01-01", "activity_date_to": "2026-06-30"}, {"activity_date_to": "range_too_long"}),
    ("RPT-17", {"contract": "nope"}, {"contract": "invalid"}),
])
def test_validation(rc, report, params, errors):
    body = rc.rows(report, C.super, params, expect=422)
    assert body["code"] == "validation_failed" and body["error"] == errors


# =========================================================================== RPT-17
def test_rpt17_my_summary_exact_and_recorder_rule(rc, cdb):
    items, t = rows(rc, "RPT-17", C.col1)
    assert t == {"count": 2, "assigned_cases": 4, "assigned_unarchived": 3, "assigned_archived": 1,
                 "unarchived_debt": "17000.00", "notes_recorded": 6, "payments_recorded": 7,
                 "recorded_amount": "4090.75", "approved_amount": "3850.75", "approved_count": 4,
                 "pending_amount": "100.00", "pending_count": 1, "rejected_amount": "60.00", "rejected_count": 1,
                 "reversed_amount": "80.00", "reversed_count": 1, "promises_recorded": 1, "promised_amount": "1200.00"}
    a, b = (by(items, "contract_id")[C.contract_a], by(items, "contract_id")[C.contract_b])
    assert (a["assigned_cases"], a["notes_recorded"], a["approved_amount"], a["recorded_amount"]) == (4, 5, "3200.50", "3440.50")
    # B: col1 owns nothing there, but recorded a note and two approved payments on col2's B1 (recorder rule)
    assert (b["assigned_cases"], b["notes_recorded"], b["approved_amount"], b["approved_count"]) == (0, 1, "650.25", 2)
    # legacy /dashbord/user: all-time, recorder = me, no status/deleted filter
    leg = legacy(rc, "/dashbord/user", C.col1)
    assert leg["clintsCount"] == t["assigned_cases"]
    assert leg["commentsCount"] == t["notes_recorded"] + 1                  # + the retired note (legacy counts it)
    assert leg["paymentCount"] == t["payments_recorded"]                    # reversed rows are kept by both
    assert D(str(leg["paymentSum"])).quantize(D("0.01")) == D(t["recorded_amount"])
    # reassign A2 to col2: the assignment moves, the recorded work stays with col1
    q(cdb, "UPDATE clints SET user_id = %s WHERE id = %s", (C.col2, C.A2))
    try:
        _, t2 = rows(rc, "RPT-17", C.col1)
        assert (t2["assigned_cases"], t2["notes_recorded"], t2["payments_recorded"]) == (3, 6, 7)
        _, t3 = rows(rc, "RPT-17", C.col2)
        assert t3["notes_recorded"] == 0 and t3["assigned_cases"] == 3
    finally:
        q(cdb, "UPDATE clints SET user_id = %s WHERE id = %s", (C.col1, C.A2))


# =========================================================================== RPT-18
def test_rpt18_dated_dashboard_day_bounds_and_sections(rc):
    items, t = rows(rc, "RPT-18", C.col1, activity_date_from=C.DAY, activity_date_to=C.DAY)
    assert t == {"count": len(items), "assigned_cases": 4, "assigned_unarchived": 3, "assigned_archived": 1,
                 "notes_total": 5, "notes_active": 2, "notes_inactive": 3, "notes_unknown": 0,
                 "receipts_count": 2, "approved_amount": "950.75"}
    sec = {}
    for it in items:
        sec.setdefault(it["section"], []).append(it)
    # status and reachability each partition the same notes
    assert sum(i["notes"] for i in sec["status"]) == sum(i["notes"] for i in sec["reachability"]) == 5
    reach = {(i["item_code"] or i["item_label"]): i["notes"] for i in sec["reachability"]}
    assert reach == {"تم التوصل": 2, "unspecified": 3}
    receipts = {i["item_id"]: i["amount"] for i in sec["receipts"]}
    assert receipts == {C.contract_a: "700.50", C.contract_b: "250.25"}       # 700.50 is stored at 21:00Z on the 19th
    assert [i["position"] for i in items] == list(range(1, len(items) + 1))
    # the previous Riyadh day: only the 23:59:59 note, no receipt
    _, t19 = rows(rc, "RPT-18", C.col1, activity_date_from="2026-09-19", activity_date_to="2026-09-19")
    assert (t19["notes_total"], t19["receipts_count"], t19["assigned_cases"]) == (1, 0, 4)
    # legacy /dashbord/collector for a browser in Riyadh (timestamps of Riyadh midnight)
    start = int(dt.datetime(2026, 9, 19, 21, tzinfo=dt.timezone.utc).timestamp())
    leg = legacy(rc, "/dashbord/collector", C.col1, {"startDate": start, "endDate": start})
    assert leg["clients"] == {"allClients": 4, "allActiveClients": 3, "allInactiveClients": 1}
    assert len(leg["comments"]) == t["notes_total"] + 1                        # + the retired note
    # legacy `payment_date BETWEEN start AND end` (both Riyadh midnight of the day): only the row stored
    # exactly at midnight survives; the 250.25 paid later that day is missed
    assert sorted(float(p["amount"]) for p in leg["payments"]) == [700.5]


# =========================================================================== RPT-19
def _r19(rc, user, month, **kw):
    items, t = rows(rc, "RPT-19", user, month=month, **kw)
    return by(items, "user_name"), t


def test_rpt19_targets_exact_rank_and_equal_to_target_service(rc, rc_app):
    r, t = _r19(rc, C.super, "2026-06")
    c1, c2, c3, c4 = r["rc.col1"], r["rc.col2"], r["rc.col3"], r["rc.col4"]
    assert (c1["target_source"], c1["target_amount"], c1["actual"], c1["achievement_percent"], c1["remaining"]) == \
        ("user", "5000.00", "3500.00", "70.00", "1500.00")
    assert (c1["actual_recorded"], c1["payments_count"], c1["recorded_count"]) == ("2900.00", 2, 2)
    # contract B: 10 % of col2's current unarchived debt (20000 + 3000)
    assert (c2["target_source"], c2["target_amount"], c2["percent_base"], c2["actual"], c2["achievement_percent"]) == \
        ("contract", "2300.00", "23000.00", "1550.00", "67.39")
    assert (c3["target_source"], c3["target_amount"], c3["actual"], c3["actual_recorded"], c3["achievement_percent"]) == \
        ("contract", "3000.00", "0.00", "1000.00", "0.00")
    assert (c4["target_amount"], c4["achievement_percent"], c4["remaining"]) == ("0.00", None, "0.00")
    assert (c1["rank"], c2["rank"], c3["rank"]) == (1, 2, 3) and c4["rank"] > 3
    assert (c1["contract_rank"], c3["contract_rank"], c2["contract_rank"]) == (1, 2, 1)
    assert (c1["user_target_kind"], c1["user_target_value"], c1["user_target_from_month"], c1["user_target_record"]) == \
        ("amount", "5000.00", "2026-06", "v2")
    assert (c2["contract_target_kind"], c2["contract_target_value"]) == ("percent", "10")
    # the same numbers as the Phase 3 target-vs-actual service (every collector in the database)
    from extensions import db
    from services import targets as tsvc
    with rc_app.app_context():
        svc = tsvc.achievement(db.session, dt.date(2026, 6, 1))
        db.session.remove()
    items, _ = rows(rc, "RPT-19", C.super, month="2026-06")
    mine = {i["user_name"]: i for i in items}
    assert len(mine) == len(svc["collectors"])
    for s in svc["collectors"]:
        m = mine[s["user_name"]]
        assert (m["target_known"], m["target_amount"], m["actual"], m["actual_recorded"], m["achievement_percent"],
                m["remaining"], m["rank"], m["payments_count"]) == \
            (s["target"]["known"], s["target"]["amount"], s["actual"], s["actual_recorded"], s["achievement_percent"],
             s["remaining"], s["rank"], s["payments_count"]), s["user_name"]
    assert t["target_total"] == svc["totals"]["target"] and t["targets_unknown"] == svc["totals"]["targets_unknown"]
    assert t["actual_total"] == svc["totals"]["actual"]


def test_rpt19_unknown_target_is_not_zero(rc):
    r, t = _r19(rc, C.super, "2026-05")                                     # before any history of the RC users
    c1 = r["rc.col1"]
    assert (c1["target_known"], c1["target_source"], c1["target_amount"], c1["achievement_percent"], c1["remaining"],
            c1["user_target_kind"]) == (False, "unknown", None, None, None, None)
    assert t["targets_unknown"] >= 4
    r7, _ = _r19(rc, C.super, "2026-07")                                    # carried forward from June
    assert (r7["rc.col1"]["target_amount"], r7["rc.col1"]["user_target_from_month"]) == ("5000.00", "2026-06")
    assert rc.rows("RPT-19", C.super, {"month": "2026-06", "target_source": "unknown", "contract": C.contract_a})["total"] == 0


def test_rpt19_scope_and_protected_target_fields(rc):
    r, t = _r19(rc, C.sup, "2026-06")
    assert set(r) == {"rc.col1", "rc.col3"}                                  # users configured on contract A
    # col1's 400 recorded on B1 (contract B) is outside the supervisor's case scope
    assert (r["rc.col1"]["actual"], r["rc.col1"]["actual_recorded"]) == ("3500.00", "2500.00")
    # no users_details / users_edit / users_edit_target: personal target settings are hidden,
    # the effective target and achievement stay (the legacy KPI card showed them)
    assert r["rc.col1"]["user_target_value"] is None and r["rc.col1"]["user_target_kind"] is None
    assert r["rc.col1"]["target_amount"] == "5000.00" and r["rc.col3"]["contract_target_value"] == "3000.00"
    own, _ = _r19(rc, C.col1, "2026-06")                                     # dashbord_user: own row only
    assert set(own) == {"rc.col1"} and own["rc.col1"]["user_target_value"] == "5000.00"
    assert own["rc.col1"]["rank"] == 1


# =========================================================================== RPT-22
def test_rpt22_comparison_as_of_and_provenance(rc):
    items, t = rows(rc, "RPT-22", C.super, as_of="2026-06-15", contract=f"{C.contract_a},{C.contract_b}")
    r = by(items, "user_name")
    c1, c2 = r["rc.col1"], r["rc.col2"]
    assert (c1["as_of"], c1["recovered_recorded"], c1["recorded_count"], c1["recorded_cases"], c1["recorded_contracts"],
            c1["recorded_in_contract"], c1["recorded_other_contracts"]) == \
        ("2026-06-15", "2900.00", 2, 2, 2, "2500.00", "400.00")
    assert (c1["recovered_owner"], c1["achievement_percent"], c1["achievement_recorded_percent"], c1["remaining"],
            c1["remaining_recorded"]) == ("2500.00", "50.00", "58.00", "2500.00", "2100.00")
    assert (c1["assigned_cases"], c1["assigned_in_contract"], c1["assigned_debt"]) == (3, 3, "17000.00")
    assert (c2["recovered_recorded"], c2["recovered_owner"], c2["target_amount"]) == ("1150.00", "1550.00", "2300.00")
    assert t["recovered_recorded"] == "4050.00" and t["recovered_owner"] == "4050.00" and t["count"] == 4
    # the last day of the window counts in full (A2's 1000 on 20 June enters with as_of = 20 June)
    items20, _ = rows(rc, "RPT-22", C.super, as_of="2026-06-20", collector=C.col1)
    assert items20[0]["recovered_owner"] == "3500.00"
    items19, _ = rows(rc, "RPT-22", C.super, as_of="2026-06-19", collector=C.col1)
    assert items19[0]["recovered_owner"] == "2500.00"


# =========================================================================== RPT-20 / RPT-21
def test_rpt20_daily_recovery_groups_bounds_and_statuses(rc, cdb, monkeypatch):
    from test_reports_v2_support import make_contract
    ct = make_contract(cdb, "RC daily " + uuid.uuid4().hex[:6])
    pf = make_portfolio(cdb, ct)
    k1 = make_case(cdb, pf, owner=C.col1)
    k2 = make_case(cdb, pf, owner=C.col2)
    k3 = make_case(cdb, pf, owner=None)                                     # unassigned
    arch = make_case(cdb, pf, owner=C.col1)
    q(cdb, "UPDATE clints SET is_archive = true WHERE id = %s", (arch,))
    day = "2026-05-14"
    make_payment(cdb, k1, 100.10, "approved", recorder=C.col2, when="2026-05-13T21:00:00Z")   # Riyadh 00:00 of the 14th
    make_payment(cdb, k1, 50, "approved", recorder=C.col1, when="2026-05-14T20:59:59Z")       # 23:59:59 Riyadh
    make_payment(cdb, k1, 999, "approved", recorder=C.col1, when="2026-05-14T21:00:00Z")      # the 15th
    make_payment(cdb, k2, 40, "approved", recorder=C.col2, when="2026-05-14T09:00:00Z")
    make_payment(cdb, k2, 7, None, recorder=C.col2, when="2026-05-14T09:00:00Z")              # pending
    make_payment(cdb, k2, 8, "rejected", recorder=C.col2, when="2026-05-14T09:00:00Z")
    make_payment(cdb, k2, 9, "reversed", recorder=C.col2, when="2026-05-14T09:00:00Z")
    make_payment(cdb, k3, 5.55, "approved", recorder=C.col1, when="2026-05-14T09:00:00Z")
    make_payment(cdb, arch, 1.45, "approved", recorder=C.col1, when="2026-05-14T09:00:00Z")
    items, t = rows(rc, "RPT-20", C.super, payment_date_from=day, payment_date_to=day, contract=ct)
    g = {(i["owner_user_name"], i["day"]): i for i in items}
    assert g[("rc.col1", day)]["approved_amount"] == "151.55"                 # current owner, not recorder; archived counts
    assert (g[("rc.col1", day)]["receipts"], g[("rc.col1", day)]["cases"]) == (3, 2)
    assert g[("rc.col2", day)]["approved_amount"] == "40.00"
    assert g[(None, day)]["approved_amount"] == "5.55"
    assert t == {"count": 3, "approved_amount": "197.10", "receipts": 5, "unassigned_amount": "5.55"}
    assert [i["approved_amount"] for i in items] == ["151.55", "40.00", "5.55"]   # default -approved_amount
    # a supervisor of contract A only does not see this contract
    assert rc.rows("RPT-20", C.sup, {"payment_date_from": day, "payment_date_to": day, "contract": ct})["total"] == 0
    # legacy /dashbord/admin/dailyPayments/v2 for "today" = the 14th: SQL date() of the UTC instant misses the
    # Riyadh-midnight row and still counts the 15th's 21:00Z row; unassigned cases are dropped (inner join)
    import api.dashboard
    monkeypatch.setattr(api.dashboard, "_today", lambda: dt.date(2026, 5, 14))
    leg = [x for x in legacy(rc, "/dashbord/admin/dailyPayments/v2", C.sup)["daily_payments"]
           if x["contract_name"].startswith("RC daily")]
    by_user = {x["user_id"]: D(str(x["amount"])).quantize(D("0.01")) for x in leg}
    assert by_user == {C.col1: D("1050.45"), C.col2: D("40.00")}


def test_rpt21_promised_by_resolution(rc, cdb, monkeypatch):
    from test_reports_v2_support import make_contract
    ct = make_contract(cdb, "RC promised " + uuid.uuid4().hex[:6])
    pf = make_portfolio(cdb, ct)
    k1 = make_case(cdb, pf, owner=C.col1)
    k2 = make_case(cdb, pf, owner=C.col2)
    from core import riyadh
    today = riyadh.today()
    first, past, future = (today - dt.timedelta(days=6)).isoformat(), (today - dt.timedelta(days=5)).isoformat(), \
        (today + dt.timedelta(days=20)).isoformat()

    def promise(case, amount, day, recorder=C.col3):
        # stored at 00:00Z of the civil date, like the legacy promise route
        pid = str(uuid.uuid4())
        q(cdb, """INSERT INTO "paymentDates" (id, clint_id, user_id, payment_date, amount)
                  VALUES (%s, %s, %s, CAST(%s AS date)::timestamp AT TIME ZONE 'UTC', %s)""",
          (pid, case, recorder, day, amount))
        return pid
    ful = promise(k1, 60, first)                                            # oldest: fulfilled below
    promise(k1, 100.25, past)                                               # overdue
    can = promise(k2, 70, past)                                             # cancelled
    promise(k2, 30, future)                                                 # open
    gone = promise(k2, 500, past)                                           # deleted: excluded
    q(cdb, 'UPDATE "paymentDates" SET is_deleted = true WHERE id = %s', (gone,))
    q(cdb, 'UPDATE "paymentDates" SET cancelled_at = now(), cancelled_by = %s, cancel_reason = %s WHERE id = %s',
      (C.sup, "test", can))
    make_payment(cdb, k1, 60, "approved", recorder=C.col1, when=f"{past}T09:00:00Z")  # allocates to the oldest open
    st = q(cdb, "SELECT promise_id::text, state FROM v_promise_state WHERE case_id = %s", (k1,))
    assert dict(st)[ful] == "fulfilled"
    items, t = rows(rc, "RPT-21", C.super, due_date_from=first, due_date_to=future, contract=ct)
    assert t["promised_amount"] == "260.25" and t["promises"] == 4
    assert D(t["open_amount"]) + D(t["overdue_amount"]) + D(t["fulfilled_amount"]) + D(t["cancelled_amount"]) == D("260.25")
    assert (t["open_amount"], t["cancelled_amount"]) == ("30.00", "70.00")
    assert (t["fulfilled_count"], t["fulfilled_amount"], t["overdue_amount"]) == (1, "60.00", "100.25")
    assert t["allocated_amount"] == "60.00"
    g = {(i["owner_user_name"], i["day"]): i for i in items}
    assert g[("rc.col2", past)]["promised_amount"] == "70.00" and g[("rc.col2", future)]["open_count"] == 1
    # state narrows the promises before grouping
    _, t2 = rows(rc, "RPT-21", C.super, due_date_from=first, due_date_to=future, contract=ct, state="cancelled,open")
    assert (t2["promised_amount"], t2["promises"], t2["overdue_amount"]) == ("100.00", 2, "0.00")
    # legacy /dashbord/admin/dailyPaymentDates/v2 for the past day counts the deleted promise too
    import api.dashboard
    monkeypatch.setattr(api.dashboard, "_today", lambda: dt.date.fromisoformat(past))
    leg = [x for x in legacy(rc, "/dashbord/admin/dailyPaymentDates/v2", C.sup)["payment_dates"]
           if x["contract_name"] == q1(cdb, "SELECT name FROM contracts WHERE id = %s", (ct,))[0]]
    assert sorted(D(str(x["amount"])).quantize(D("0.01")) for x in leg) == [D("100.25"), D("570.00")]


# =========================================================================== RPT-23 notes
def test_rpt23_notes_hours_scope_and_classification(rc):
    items, t = rows(rc, "RPT-23", C.super, day=C.DAY, contract=f"{C.contract_a},{C.contract_b}")
    r = by(items, "user_name")
    c1 = r["rc.col1"]
    assert (c1["mode"], c1["total"], c1["h08"], c1["h10"], c1["h09"], c1["h00"], c1["h18"]) == ("notes", 3, 2, 1, 0, None, None)
    assert (c1["notes_active"], c1["notes_inactive"], c1["notes_unknown"], c1["calls_billed"]) == (2, 1, 0, None)
    assert (r["rc.col3"]["total"], r["rc.col3"]["h13"], r["rc.col3"]["notes_unknown"]) == (1, 1, 1)
    assert r["rc.col2"]["total"] == 0 and r["rc.col4"]["total"] == 0         # zero rows kept
    # position: the ordinal over the caller's whole scope by total (desc), then user id; filters do not renumber
    assert c1["position"] < r["rc.col3"]["position"]
    full, _ = rows(rc, "RPT-23", C.super, day=C.DAY)
    ordered = sorted(full, key=lambda i: i["position"])
    assert [i["position"] for i in ordered] == list(range(1, len(full) + 1))
    assert all(a["total"] >= b["total"] for a, b in zip(ordered, ordered[1:]))
    all_day, _ = rows(rc, "RPT-23", C.super, day=C.DAY, from_hour=0, to_hour=23, collector=C.col1)
    assert (all_day[0]["total"], all_day[0]["h00"], all_day[0]["h18"]) == (5, 1, 1)
    # supervisor of contract A: the note col1 wrote on col2's B1 is outside the case scope
    sup, _ = rows(rc, "RPT-23", C.sup, day=C.DAY)
    assert {i["user_name"]: i["total"] for i in sup} == {"rc.col1": 2, "rc.col3": 1}


def test_rpt23_calls_mode_needs_call_permission(rc, cdb):
    set_perm(cdb, C.role_sup, "clints_callsRecords", False)
    set_perm(cdb, C.role_sup, "reports_calls", False)
    try:
        body = rc.rows("RPT-23", C.sup, {"day": C.DAY, "mode": "calls"}, expect=403)
        assert body["error"]["required_any"] == ["clints_callsRecords", "reports_calls"]
        # refused up front for an export too (the worker would also refuse: permission_denied)
        assert rc.export("RPT-23", C.sup, {"day": C.DAY, "mode": "calls"}, expect=403)["code"] == "forbidden"
        assert rc.get("/v2/report-options/collector", C.sup, {"report": "RPT-23", "mode": "calls"}).status_code == 403
        assert rc.rows("RPT-23", C.sup, {"day": C.DAY})["total"] == 2
    finally:
        set_perm(cdb, C.role_sup, "clints_callsRecords", True)
        set_perm(cdb, C.role_sup, "reports_calls", True)


# =========================================================================== RPT-24
def test_rpt24_sections_totals_scope(rc):
    p = {"activity_date_from": C.DAY, "activity_date_to": C.DAY}
    summ, t = rows(rc, "RPT-24", C.sup, **p)
    assert len(summ) == 1 and summ[0]["section"] == "summary"
    assert t == {"count": 1, "cases_total": 5, "cases_unarchived": 4, "cases_archived": 1, "unarchived_debt": "18000.00",
                 "notes_total": 5, "notes_active": 1, "notes_inactive": 3, "notes_unknown": 1,
                 "approved_amount": "700.50", "approved_count": 1, "collectors": 2, "contracts": 1}
    for section in ("trend", "contracts", "collectors"):
        items, t2 = rows(rc, "RPT-24", C.sup, section=section, **p)
        assert {k: v for k, v in t2.items() if k != "count"} == {k: v for k, v in t.items() if k != "count"}
        assert items[0]["section"] == "summary" and all(i["section"] == section for i in items[1:])
        if section == "contracts":
            assert [(i["entity_id"], i["cases_total"], i["notes_total"], i["approved_amount"]) for i in items[1:]] == \
                [(C.contract_a, 5, 5, "700.50")]
        if section == "trend":
            assert [(i["day"], i["notes_total"], i["approved_amount"]) for i in items[1:]] == [(C.DAY, 5, "700.50")]
        if section == "collectors":
            c = by(items[1:], "user_name")
            assert set(c) == {"rc.col1", "rc.col3"}
            assert (c["rc.col1"]["cases_total"], c["rc.col1"]["notes_total"], c["rc.col1"]["approved_amount"]) == (4, 4, "700.50")
    # the contract filter narrows every metric (SuperAdmin, contract B)
    _, tb = rows(rc, "RPT-24", C.super, contract=C.contract_b, **p)
    assert (tb["cases_total"], tb["unarchived_debt"], tb["notes_total"], tb["approved_amount"], tb["contracts"]) == \
        (2, "23000.00", 1, "250.25", 1)
    # the contract selector only lists contracts the caller may see
    opts = rc.get("/v2/report-options/contract", C.sup, {"report": "RPT-24"}).get_json()["data"]
    assert [o["id"] for o in opts["items"]] == [C.contract_a]


# =========================================================================== exports
@pytest.mark.parametrize("report,user,params", [
    ("RPT-17", C.col1, {}),
    ("RPT-18", C.col1, {"activity_date_from": C.DAY, "activity_date_to": C.DAY}),
    ("RPT-19", C.super, {"month": "2026-06"}),
    ("RPT-22", C.sup, {"as_of": "2026-06-30"}),
    ("RPT-23", C.super, {"day": C.DAY, "contract": f"{C.contract_a},{C.contract_b}"}),
    ("RPT-24", C.sup, {"activity_date_from": C.DAY, "activity_date_to": C.DAY, "section": "collectors"}),
])
def test_full_export_equals_preview(rc, report, user, params):
    items, _ = csv_equals_preview(rc, report, user, params)
    assert items


def test_export_protected_fields_follow_requester(rc):
    exp = rc.export("RPT-19", C.sup, {"month": "2026-06"})
    assert run_export(rc.app, exp["id"]) == "succeeded"
    header, body = read_csv(rc.download(exp["id"], C.sup).data)
    col = {h: i for i, h in enumerate(header)}
    rc1 = [r for r in body if r[col["user_name"]] == "rc.col1"][0]
    assert rc1[col["user_target_value"]] == "" and rc1[col["target_amount"]] == "5000.00"


def test_arabic_xlsx_is_not_offered_but_xlsx_works(rc):
    assert rc.export("RPT-20", C.super, {}, fmt="xlsx_ar", expect=422)["error"] == {"format": "unknown_value"}
    exp = rc.export("RPT-20", C.super, {"payment_date_from": "2026-06-01", "payment_date_to": "2026-06-30"}, fmt="xlsx")
    assert run_export(rc.app, exp["id"]) == "succeeded"
