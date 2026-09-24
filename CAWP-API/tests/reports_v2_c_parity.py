"""Stream R-C reconciliation and timings on cawp-db (never writes; not collected by pytest).

    docker exec -w /app cawp-api python tests/reports_v2_c_parity.py reconcile [--json /tmp/rc.json]
    docker exec -w /app cawp-api python tests/reports_v2_c_parity.py timings

Read-only: the engine connects with `default_transaction_read_only=on` (core.reports.perf) and
the v2 queries run in the framework's REPEATABLE READ READ ONLY snapshot. The legacy dashboard
handlers (api/dashboard.py) run in-process with a JWT for the actor; `_today()` is patched to
the reconciled day where the handler only knows "today". The legacy handlers write their
usual short-lived Redis cache keys; nothing else is written.

The CDR is NEVER the real PBX: before anything runs, Config.CDR_* is pointed at the local
fixture (CDR_FIXTURE_HOST, default cdr-test-db; the run aborts if that is 10.10.0.220).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import statistics
import sys
import tempfile
import time
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.config import Config  # noqa: E402

_FIXTURE = os.environ.get("CDR_FIXTURE_HOST", "cdr-test-db")
if _FIXTURE.strip() == "10.10.0.220":
    raise SystemExit("refusing: the CDR fixture host is the real PBX")
Config.CDR_HOST, Config.CDR_DB, Config.CDR_USER, Config.CDR_PASSWORD = _FIXTURE, "asteriskcdrdb", "crm", "cdr-test-only"
Config.CDR_ENABLED, Config.CDR_CONNECT_TIMEOUT = True, 3

from sqlalchemy import text  # noqa: E402

CENT = Decimal("0.01")


def D(v) -> Decimal:
    return Decimal(str(v or 0)).quantize(CENT)


def make_app():
    from core.reports.perf import make_app as base
    app = base()
    import api.dashboard
    app.register_blueprint(api.dashboard.bp)
    return app


APP = None


def ids(names):
    from extensions import db
    rows = db.session.execute(text("SELECT user_name, id::text FROM users WHERE user_name = ANY(:n)"),
                              {"n": list(names)}).all()
    db.session.rollback()
    return dict(rows)


def legacy(path, user_id, params=None):
    from flask import g
    from flask_jwt_extended import create_access_token
    # core.user_privacy memoises the viewer on `g`; the in-process calls share one app context
    g.pop("_cawp_user_privacy", None)
    token = create_access_token(identity=user_id)
    r = APP.test_client().get(path, headers={"Authorization": f"Bearer {token}"}, query_string=params or {})
    body = r.get_json()
    if r.status_code != 200:
        raise RuntimeError(f"{path} -> {r.status_code} {str(body)[:300]}")
    return body


def v2(report_id, user_id, params, all_pages=True):
    from core.reports import engine, privacy, registry
    from services.clients_scope import resolve_scope
    rep = registry.get(report_id)
    items, page = [], 1
    while True:
        out = engine.preview(rep, {**params, "page": page, "page_size": 200}, user_id, resolve_scope(user_id),
                             privacy.FULL)
        items.extend(out["items"])
        if not all_pages or page >= out["pages"] or not out["items"]:
            return items, out["totals"]
        page += 1


def sql(q, **p):
    from extensions import db
    r = db.session.execute(text(q), p).mappings().all()
    db.session.rollback()
    return r


def riyadh_midnight_epoch(day: str) -> int:
    from core import riyadh
    return int(riyadh.day_bounds(day)[0].timestamp())


# =========================================================================== RPT-17
def rec_17(out):
    actors = ids(["rasha.s", "ghofran.b", "tabby.1", "marwan.a", "bader.b", "hanadi.a"])
    for name, uid in actors.items():
        leg = legacy("/dashbord/user", uid)
        _, t = v2("RPT-17", uid, {})
        cat = sql("""SELECT (SELECT count(*) FROM "Comments" m WHERE m.user_id = CAST(:u AS uuid) AND m.is_deleted) AS del_notes,
                            (SELECT count(*) FROM "Comments" m WHERE m.user_id = CAST(:u AS uuid)
                                AND NOT EXISTS (SELECT 1 FROM clints c WHERE c.id = m.clint_id)) AS orphan_notes,
                            (SELECT count(*) FROM "Payments" p WHERE p.user_id = CAST(:u AS uuid) AND p.is_deleted
                                AND NOT EXISTS (SELECT 1 FROM payment_reversals r WHERE r.payment_id = p.id)) AS del_pay,
                            (SELECT coalesce(sum(round(p.amount::numeric, 2)), 0) FROM "Payments" p
                              WHERE p.user_id = CAST(:u AS uuid) AND p.is_deleted
                                AND NOT EXISTS (SELECT 1 FROM payment_reversals r WHERE r.payment_id = p.id)) AS del_amt,
                            (SELECT coalesce(sum(round(p.amount::numeric, 2)), 0) FROM "Payments" p
                              WHERE p.user_id = CAST(:u AS uuid)) AS legacy_exact""", u=uid)[0]
        out.append({"report": "RPT-17", "legacy": "GET /dashbord/user", "actor": name,
                    "legacy_cases": leg["clintsCount"], "v2_cases": t["assigned_cases"],
                    "legacy_notes": leg["commentsCount"], "v2_notes": t["notes_recorded"],
                    "legacy_payments": leg["paymentCount"], "v2_payments": t["payments_recorded"],
                    "legacy_sum_float": leg["paymentSum"], "legacy_sum_exact": str(D(cat["legacy_exact"])),
                    "v2_recorded": t["recorded_amount"], "v2_approved": t["approved_amount"],
                    "diff": {"-retired notes": cat["del_notes"], "-orphan notes": cat["orphan_notes"],
                             "-deleted, not reversed payments": [cat["del_pay"], str(D(cat["del_amt"]))]},
                    "explained": (leg["clintsCount"] == t["assigned_cases"]
                                  and leg["commentsCount"] - cat["del_notes"] - cat["orphan_notes"] == t["notes_recorded"]
                                  and leg["paymentCount"] - cat["del_pay"] == t["payments_recorded"]
                                  and D(cat["legacy_exact"]) - D(cat["del_amt"]) == D(t["recorded_amount"]))})


# =========================================================================== RPT-18
def rec_18(out):
    actors = ids(["rasha.s", "ghofran.b", "hanadi.a"])
    for d0, d1 in (("2026-09-01", "2026-09-15"), ("2026-07-27", "2026-07-27"), ("2026-08-01", "2026-08-31")):
        for name, uid in actors.items():
            leg = legacy("/dashbord/collector", uid, {"startDate": riyadh_midnight_epoch(d0),
                                                      "endDate": riyadh_midnight_epoch(d1)})
            _, t = v2("RPT-18", uid, {"activity_date_from": d0, "activity_date_to": d1})
            from core import riyadh
            lo, hi = riyadh.range_bounds(d0, d1)
            c = sql("""
                SELECT (SELECT count(*) FROM "Comments" m WHERE m.user_id = CAST(:u AS uuid) AND m.is_deleted
                           AND m.create_at >= :lo AND m.create_at < :hi) AS del_notes,
                       -- legacy BETWEEN [Riyadh 00:00 of d0, Riyadh 00:00 of d1]: the last civil day is missed
                       (SELECT count(*) FROM "Payments" p WHERE p.user_id = CAST(:u AS uuid) AND p.approval_status
                           AND p.is_deleted = false AND p.payment_date > :last0 AND p.payment_date < :hi) AS last_day,
                       (SELECT coalesce(sum(round(p.amount::numeric, 2)), 0) FROM "Payments" p
                         WHERE p.user_id = CAST(:u AS uuid) AND p.approval_status AND p.is_deleted = false
                           AND p.payment_date > :last0 AND p.payment_date < :hi) AS last_day_amt,
                       (SELECT count(*) FROM "Payments" p WHERE p.user_id = CAST(:u AS uuid) AND p.approval_status
                           AND p.is_deleted AND p.payment_date BETWEEN :lo AND :last0) AS del_pay""",
                    u=uid, lo=lo, hi=hi, last0=riyadh.day_bounds(d1)[0])[0]
            leg_amt = sum(D(p["amount"]) for p in leg["payments"])
            out.append({"report": "RPT-18", "legacy": "GET /dashbord/collector", "actor": name, "window": f"{d0}..{d1}",
                        "legacy_cases": leg["clients"], "v2_cases": [t["assigned_cases"], t["assigned_unarchived"]],
                        "legacy_notes": len(leg["comments"]), "v2_notes": t["notes_total"],
                        "legacy_receipts": len(leg["payments"]), "legacy_amount": str(leg_amt),
                        "v2_receipts": t["receipts_count"], "v2_amount": t["approved_amount"],
                        "diff": {"-retired notes": c["del_notes"], "+last civil day": [c["last_day"], str(D(c["last_day_amt"]))],
                                 "-deleted approved": c["del_pay"]},
                        "explained": (len(leg["comments"]) - c["del_notes"] == t["notes_total"]
                                      and len(leg["payments"]) + c["last_day"] - c["del_pay"] == t["receipts_count"]
                                      and leg["clients"]["allClients"] == t["assigned_cases"])})


# =========================================================================== RPT-19
def rec_19(out):
    from extensions import db
    from services import targets as tsvc
    admin = ids(["aidi.dev"])["aidi.dev"]
    for month in ("2026-09", "2026-08"):
        svc = tsvc.achievement(db.session, tsvc.parse_month(month))
        db.session.rollback()
        items, t = v2("RPT-19", admin, {"month": month})
        mine = {i["user_name"]: i for i in items}
        mism = []
        for s in svc["collectors"]:
            m = mine.get(s["user_name"])
            key = lambda x: (x["target_amount"], x["actual"], x["actual_recorded"], x["achievement_percent"], x["remaining"], x["rank"])  # noqa: E731
            if m is None or key(m) != (s["target"]["amount"], s["actual"], s["actual_recorded"], s["achievement_percent"],
                                       s["remaining"], s["rank"]):
                mism.append(s["user_name"])
        out.append({"report": "RPT-19", "legacy": "GET /v2/targets/achievement (Phase 3 service)", "actor": "aidi.dev",
                    "window": month, "legacy_rows": len(svc["collectors"]), "v2_rows": len(items),
                    "legacy_target": svc["totals"]["target"], "v2_target": t["target_total"],
                    "legacy_unknown": svc["totals"]["targets_unknown"], "v2_unknown": t["targets_unknown"],
                    "legacy_actual": svc["totals"]["actual"], "v2_actual": t["actual_total"],
                    "legacy_recorded": svc["totals"]["actual_recorded"], "v2_recorded": t["actual_recorded_total"],
                    "diff": {"rows differing": mism}, "explained": not mism})
    # legacy KPICollector (own card, dashbord_user): own contract only, recorder-based, float
    for name, uid in ids(["rasha.s", "ghofran.b", "hanadi.a", "Ibrahim.m"]).items():
        leg = legacy("/dashbord/collector/v2", uid)
        u, ct = leg["user"], leg["contract"] or {}
        base = sum(float(c["amount"]) for c in leg["clients"])

        def legacy_target():
            if (u.get("target_amount") or 0) > 0 or (u.get("target_percent") or 0) > 0:
                if u.get("target_is_percentage") and (u.get("target_percent") or 0) > 0:
                    return round(base * u["target_percent"] / 100)
                if not u.get("target_is_percentage") and (u.get("target_amount") or 0) > 0:
                    return u["target_amount"]
            if u.get("contract_id") and ct:
                if (ct.get("monthly_target_percent") or 0) > 0:
                    return round(base * ct["monthly_target_percent"] / 100)
                if (ct.get("monthly_target_amount") or 0) > 0:
                    return ct["monthly_target_amount"]
            return 0
        from core import riyadh
        m0 = riyadh.today().replace(day=1)
        achieved = sum(float(p["amount"]) for p in leg["payments"]
                       if riyadh.civil_date(dt.datetime.strptime(p["payment_date"], "%a, %d %b %Y %H:%M:%S %Z")
                                            .replace(tzinfo=dt.timezone.utc)) >= m0)
        items, _ = v2("RPT-19", uid, {"month": m0.strftime("%Y-%m")})
        row = {i["user_name"]: i for i in items}.get(name, {})
        t0, t1 = riyadh.range_bounds(m0, riyadh.today())
        tend = riyadh.range_bounds(m0, tsvc.next_month(m0) - dt.timedelta(days=1))[1]
        from services.clients_scope import resolve_scope
        from core.reports.engine import scope_sql
        sc, sp = scope_sql(resolve_scope(uid), "c", "k19")
        c = sql(f"""
            WITH leg AS (SELECT p.id, round(p.amount::numeric, 2) a FROM "Payments" p JOIN clints c ON c.id = p.clint_id
                          JOIN portfolios pf ON pf.id = c.portfolios_id
                         WHERE p.user_id = CAST(:u AS uuid) AND p.approval_status = true
                           AND pf.contract_id = (SELECT contract_id FROM users WHERE id = CAST(:u AS uuid))
                           AND p.payment_date >= :t0 AND p.payment_date < :t1),
                 v AS (SELECT p.id, round(p.amount::numeric, 2) a FROM "Payments" p JOIN clints c ON c.id = p.clint_id
                        WHERE p.user_id = CAST(:u AS uuid) AND p.approval_status AND p.is_deleted = false
                          AND NOT EXISTS (SELECT 1 FROM payment_reversals r WHERE r.payment_id = p.id)
                          AND p.payment_date >= :t0 AND p.payment_date < :tend AND {sc})
            SELECT (SELECT coalesce(sum(a), 0) FROM leg) AS leg_a,
                   (SELECT coalesce(sum(a), 0) FROM leg WHERE id NOT IN (SELECT id FROM v)) AS minus_scope,
                   (SELECT coalesce(sum(v.a), 0) FROM v JOIN "Payments" p ON p.id = v.id
                     WHERE v.id NOT IN (SELECT id FROM leg) AND p.payment_date >= :t1) AS plus_future,
                   (SELECT coalesce(sum(v.a), 0) FROM v JOIN "Payments" p ON p.id = v.id
                     WHERE v.id NOT IN (SELECT id FROM leg) AND p.payment_date < :t1) AS plus_other""",
                u=uid, t0=t0, t1=t1, tend=tend, **sp)[0]
        out.append({"report": "RPT-19", "legacy": "GET /dashbord/collector/v2 + KPICollector", "actor": name,
                    "window": m0.strftime("%Y-%m"), "legacy_target": legacy_target(), "v2_target": row.get("target_amount"),
                    "legacy_achieved_float": achieved, "legacy_achieved_exact": str(D(c["leg_a"])),
                    "v2_actual_recorded": row.get("actual_recorded"), "v2_actual_owner": row.get("actual"),
                    "diff": {"-recorded on cases outside the current case scope (reassigned)": str(D(c["minus_scope"])),
                             "+recorded on other contracts (legacy counts the own contract only)": str(D(c["plus_other"])),
                             "+payment dated later this month (legacy stops at now)": str(D(c["plus_future"]))},
                    "explained": D(c["leg_a"]) - D(c["minus_scope"]) + D(c["plus_other"]) + D(c["plus_future"])
                    == D(row.get("actual_recorded"))
                    and abs(D(achieved) - D(c["leg_a"])) <= CENT and D(legacy_target()) == D(row.get("target_amount"))})


# =========================================================================== RPT-20 / RPT-21
def rec_20_21(out):
    import api.dashboard
    admin = ids(["aidi.dev"])["aidi.dev"]
    real_today = api.dashboard._today
    try:
        for day in ("2026-07-27", "2026-08-15", "2026-09-14", "2026-09-21"):
            api.dashboard._today = lambda d=day: dt.date.fromisoformat(d)
            from core import riyadh
            lo, hi = riyadh.day_bounds(day)
            # ---- RPT-20
            leg = legacy("/dashbord/admin/dailyPayments/v2", admin)["daily_payments"]
            items, t = v2("RPT-20", admin, {"payment_date_from": day, "payment_date_to": day})
            c = sql("""
                WITH leg AS (SELECT p.id, round(p.amount::numeric, 2) a FROM "Payments" p JOIN clints c ON c.id = p.clint_id
                              JOIN users u ON u.id = c.user_id JOIN portfolios pf ON pf.id = c.portfolios_id
                             WHERE p.approval_status = true AND (p.payment_date AT TIME ZONE 'UTC')::date = :d),
                     v AS (SELECT p.id, round(p.amount::numeric, 2) a, c.user_id FROM "Payments" p JOIN clints c ON c.id = p.clint_id
                            WHERE p.approval_status AND p.is_deleted = false
                              AND NOT EXISTS (SELECT 1 FROM payment_reversals r WHERE r.payment_id = p.id)
                              AND p.payment_date >= :lo AND p.payment_date < :hi)
                SELECT (SELECT count(*) FROM leg) AS leg_n, (SELECT coalesce(sum(a), 0) FROM leg) AS leg_a,
                       (SELECT count(*) FROM v WHERE v.id NOT IN (SELECT id FROM leg) AND v.user_id IS NOT NULL) AS plus_day,
                       (SELECT coalesce(sum(a), 0) FROM v WHERE v.id NOT IN (SELECT id FROM leg) AND v.user_id IS NOT NULL) AS plus_day_a,
                       (SELECT count(*) FROM v WHERE v.user_id IS NULL) AS unassigned,
                       (SELECT count(*) FROM leg WHERE leg.id NOT IN (SELECT id FROM v)) AS minus_day,
                       (SELECT coalesce(sum(a), 0) FROM leg WHERE leg.id NOT IN (SELECT id FROM v)) AS minus_day_a""",
                    d=day, lo=lo, hi=hi)[0]
            leg_sum = sum(D(x["amount"]) for x in leg)
            out.append({"report": "RPT-20", "legacy": "GET /dashbord/admin/dailyPayments/v2", "actor": "aidi.dev",
                        "window": day, "legacy_groups": len(leg), "v2_groups": t["count"], "legacy_amount": str(leg_sum),
                        "legacy_exact": str(D(c["leg_a"])), "v2_amount": t["approved_amount"], "v2_receipts": t["receipts"],
                        "diff": {"+Riyadh civil day (stored 21:00-24:00Z the eve)": [c["plus_day"], str(D(c["plus_day_a"]))],
                                 "-UTC date is this day, Riyadh date the next": [c["minus_day"], str(D(c["minus_day_a"]))],
                                 "+unassigned cases": c["unassigned"]},
                        "explained": D(c["leg_a"]) - D(c["minus_day_a"]) + D(c["plus_day_a"]) == D(t["approved_amount"]) - D(t["unassigned_amount"])
                        and abs(leg_sum - D(c["leg_a"])) <= CENT})
            # ---- RPT-21
            leg = legacy("/dashbord/admin/dailyPaymentDates/v2", admin)["payment_dates"]
            items, t = v2("RPT-21", admin, {"due_date_from": day, "due_date_to": day})
            c = sql("""
                WITH leg AS (SELECT d.id, round(d.amount::numeric, 2) a, d.is_deleted FROM "paymentDates" d
                              JOIN clints c ON c.id = d.clint_id JOIN users u ON u.id = c.user_id
                              JOIN portfolios pf ON pf.id = c.portfolios_id
                             WHERE (d.payment_date AT TIME ZONE 'UTC')::date = :d),
                     v AS (SELECT s.promise_id AS id, s.amount a, c.user_id FROM v_promise_state s JOIN clints c ON c.id = s.case_id
                            WHERE s.due_date = CAST(:d AS date))
                SELECT (SELECT coalesce(sum(a), 0) FROM leg) AS leg_a,
                       (SELECT count(*) FROM leg WHERE is_deleted) AS deleted, (SELECT coalesce(sum(a), 0) FROM leg WHERE is_deleted) AS deleted_a,
                       (SELECT coalesce(sum(a), 0) FROM leg WHERE NOT is_deleted AND id NOT IN (SELECT id FROM v)) AS minus_day_a,
                       (SELECT count(*) FROM leg WHERE NOT is_deleted AND id NOT IN (SELECT id FROM v)) AS minus_day,
                       (SELECT coalesce(sum(a), 0) FROM v WHERE id NOT IN (SELECT id FROM leg) AND user_id IS NOT NULL) AS plus_day_a,
                       (SELECT count(*) FROM v WHERE id NOT IN (SELECT id FROM leg) AND user_id IS NOT NULL) AS plus_day,
                       (SELECT coalesce(sum(a), 0) FROM v WHERE user_id IS NULL) AS unassigned_a""", d=day)[0]
            leg_sum = sum(D(x["amount"]) for x in leg)
            out.append({"report": "RPT-21", "legacy": "GET /dashbord/admin/dailyPaymentDates/v2", "actor": "aidi.dev",
                        "window": day, "legacy_groups": len(leg), "v2_groups": t["count"], "legacy_amount": str(leg_sum),
                        "legacy_exact": str(D(c["leg_a"])), "v2_amount": t["promised_amount"],
                        "v2_split": {k: t[f"{k}_amount"] for k in ("open", "overdue", "fulfilled", "cancelled")},
                        "diff": {"-deleted promises": [c["deleted"], str(D(c["deleted_a"]))],
                                 "+Riyadh due day (stored 21:00-24:00Z the eve)": [c["plus_day"], str(D(c["plus_day_a"]))],
                                 "-UTC date is this day, Riyadh date the next": [c["minus_day"], str(D(c["minus_day_a"]))],
                                 "+unassigned cases": str(D(c["unassigned_a"]))},
                        "explained": D(c["leg_a"]) - D(c["deleted_a"]) - D(c["minus_day_a"]) + D(c["plus_day_a"])
                        + D(c["unassigned_a"]) == D(t["promised_amount"]) and abs(leg_sum - D(c["leg_a"])) <= CENT})
    finally:
        api.dashboard._today = real_today


# =========================================================================== RPT-22
def rec_22(out):
    from core import riyadh
    admin = ids(["aidi.dev"])["aidi.dev"]
    leg = legacy("/dashbord/Allcollector/v2", admin, {"nocache": "1"})
    contracts = {c["id"]: c for c in leg["contracts"]}
    base, pay = {}, {}
    for c in leg["clients"]:
        base[c["user_id"]] = base.get(c["user_id"], 0.0) + float(c["amount"])
    today = riyadh.today()
    for p in leg["payments"]:
        when = dt.datetime.fromisoformat(p["payment_date"])
        when = when if when.tzinfo else when.replace(tzinfo=dt.timezone.utc)
        if riyadh.civil_date(when).replace(day=1) == today.replace(day=1):     # the browser's month (Riyadh)
            pay[p["user_id"]] = pay.get(p["user_id"], 0.0) + float(p["amount"])
    items, t = v2("RPT-22", admin, {"as_of": today.isoformat()})
    mine = {i["user_name"]: i for i in items}
    names = dict((r["id"], r["user_name"]) for r in sql("SELECT id::text, user_name FROM users"))
    tgt_diff, ach_diff = [], []
    leg_target_total = 0
    for u in leg["users"]:
        b = base.get(u["id"], 0.0)
        ct = contracts.get(u["contract_id"]) or {}
        if (u["target_amount"] or 0) > 0 or (u["target_percent"] or 0) > 0:
            if u["target_is_percentage"] and (u["target_percent"] or 0) > 0:
                target = round(b * u["target_percent"] / 100)
            elif not u["target_is_percentage"] and (u["target_amount"] or 0) > 0:
                target = u["target_amount"]
            else:
                target = None
        else:
            target = None
        if target is None:
            target = (round(b * ct["monthly_target_percent"] / 100) if (ct.get("monthly_target_percent") or 0) > 0
                      else ct.get("monthly_target_amount") or 0 if (ct.get("monthly_target_amount") or 0) > 0 else 0)
        leg_target_total += target
        m = mine.get(names[u["id"]])
        if m is None or D(target) != D(m["target_amount"]):
            tgt_diff.append(names[u["id"]])
        if m is None or abs(D(pay.get(u["id"], 0.0)) - D(m["recovered_recorded"])) > CENT:
            ach_diff.append([names[u["id"]], str(D(pay.get(u["id"], 0.0))), m and m["recovered_recorded"]])
    out.append({"report": "RPT-22", "legacy": "GET /dashbord/Allcollector/v2 + AllCollectorsKPI", "actor": "aidi.dev",
                "window": f"{today.replace(day=1)}..{today}", "legacy_rows": len(leg["users"]), "v2_rows": len(items),
                "legacy_target_total": str(D(leg_target_total)), "v2_target_total": t["target_total"],
                "legacy_achieved_total_float": sum(pay.values()), "v2_recorded_total": t["recovered_recorded"],
                "v2_owner_total": t["recovered_owner"],
                "diff": {"users whose target differs": tgt_diff, "users whose recorded recovery differs": ach_diff},
                "explained": not tgt_diff and not ach_diff})


# =========================================================================== RPT-23 (notes)
def rec_23(out):
    import api.dashboard
    from core import riyadh
    admin = ids(["aidi.dev"])["aidi.dev"]
    real_today = api.dashboard._today
    try:
        for day in ("2026-09-14", "2026-09-13", "2026-08-20"):
            # the legacy handler's own day is the UTC date; CommentsReportDaily then buckets by the browser's
            # (Riyadh) hour 08..17, all of which lie inside the same UTC date
            api.dashboard._today = lambda d=day: dt.date.fromisoformat(d)
            leg = legacy("/dashbord/commentsReport/v2", admin, {"nocache": "1"})
            counts = {}
            for c in leg["comments"]:
                when = riyadh.to_riyadh(dt.datetime.strptime(c["create_at"], "%a, %d %b %Y %H:%M:%S %Z"))
                if 8 <= when.hour <= 17 and when.date().isoformat() == day:
                    counts[c["user_id"]] = counts.get(c["user_id"], 0) + 1
            users = {u["id"] for u in leg["users"]}
            leg_total = sum(v for k, v in counts.items() if k in users)
            items, t = v2("RPT-23", admin, {"day": day})
            mine = {i["user_name"]: i["total"] for i in items}
            names = dict((r["id"], r["user_name"]) for r in sql("SELECT id::text, user_name FROM users"))
            diff = [[names[u], counts.get(u, 0), mine.get(names[u])] for u in users if counts.get(u, 0) != mine.get(names[u], 0)]
            out.append({"report": "RPT-23", "legacy": "GET /dashbord/commentsReport/v2 + CommentsReportDaily (notes)",
                        "actor": "aidi.dev", "window": f"{day} 08-17", "legacy_rows": len(users), "v2_rows": len(items),
                        "legacy_total": leg_total, "v2_total": t["total"], "diff": {"users differing": diff},
                        "explained": not diff and leg_total == t["total"]})
    finally:
        api.dashboard._today = real_today


# =========================================================================== RPT-24
def rec_24(out):
    admin = ids(["aidi.dev"])["aidi.dev"]
    for d0, d1 in (("2026-09-01", "2026-09-15"), ("2026-07-27", "2026-07-27")):
        leg = legacy("/dashboard", admin, {"startDate": riyadh_midnight_epoch(d0), "endDate": riyadh_midnight_epoch(d1),
                                           "nocache": "1"})
        items, t = v2("RPT-24", admin, {"activity_date_from": d0, "activity_date_to": d1, "section": "contracts"})
        leg_pay = sum(D(p["amount"]) for p in leg["payments"])
        by_ct = {}
        for p in leg["payments"]:
            by_ct[p["contract_id"]] = by_ct.get(p["contract_id"], Decimal(0)) + D(p["amount"])
        ct_diff = [[i["entity_name"], str(by_ct.get(i["entity_id"], Decimal(0))), i["approved_amount"]] for i in items[1:]
                   if abs(by_ct.get(i["entity_id"], Decimal(0)) - D(i["approved_amount"])) > CENT]
        out.append({"report": "RPT-24", "legacy": "GET /dashboard", "actor": "aidi.dev", "window": f"{d0}..{d1}",
                    "legacy_cases": leg["clients"]["allClients"], "v2_cases": t["cases_total"],
                    "legacy_unarchived": leg["clients"]["allActiveClients"], "v2_unarchived": t["cases_unarchived"],
                    "legacy_notes": leg["countRow"], "v2_notes": t["notes_total"],
                    "legacy_approved": str(leg_pay), "v2_approved": t["approved_amount"],
                    "legacy_payments": len(leg["payments"]), "v2_payments": t["approved_count"],
                    "diff": {"contracts differing": ct_diff},
                    "explained": (leg["clients"]["allClients"] == t["cases_total"] and leg["countRow"] == t["notes_total"]
                                  and abs(leg_pay - D(t["approved_amount"])) <= CENT and not ct_diff)})


def reconcile(args):
    out = []
    with APP.app_context():
        for f in (rec_17, rec_18, rec_19, rec_20_21, rec_22, rec_23, rec_24):
            t0 = time.time()
            try:
                f(out)
            except Exception as e:  # noqa: BLE001 - report and continue
                import traceback
                traceback.print_exc()
                out.append({"report": f.__name__, "error": f"{type(e).__name__}: {e}"})
            print(f"{f.__name__}: {time.time() - t0:.1f}s", file=sys.stderr)
    for r in out:
        print(json.dumps(r, ensure_ascii=False, default=str))
    if args.json:
        with open(args.json, "w") as fh:
            json.dump(out, fh, ensure_ascii=False, indent=1, default=str)
    bad = [r for r in out if not r.get("explained")]
    print(f"\n{len(out)} comparisons, {len(bad)} not fully explained", file=sys.stderr)


# =========================================================================== timings
def _time(fn, n=5):
    xs = []
    for _ in range(n):
        t0 = time.perf_counter()
        fn()
        xs.append((time.perf_counter() - t0) * 1000)
    return statistics.median(xs), min(xs), max(xs)


def timings(args):
    from core.reports import engine, exports, privacy, registry
    from services.clients_scope import resolve_scope
    from services import reports_v2_cdr
    who = ids(["aidi.dev", "marwan.a", "qasim.s", "rasha.s", "bader.b"])
    cases = [
        ("RPT-17", "rasha.s", {}), ("RPT-17", "aidi.dev", {}),
        ("RPT-18", "rasha.s", {"activity_date_from": "2026-09-01", "activity_date_to": "2026-09-15"}),
        ("RPT-19", "aidi.dev", {"month": "2026-09"}), ("RPT-19", "marwan.a", {"month": "2026-09"}),
        ("RPT-19", "rasha.s", {"month": "2026-09"}),
        ("RPT-20", "aidi.dev", {"payment_date_from": "2026-07-01", "payment_date_to": "2026-07-31"}),
        ("RPT-20", "bader.b", {"payment_date_from": "2026-07-01", "payment_date_to": "2026-07-31"}),
        ("RPT-21", "aidi.dev", {"due_date_from": "2026-09-01", "due_date_to": "2026-09-30"}),
        ("RPT-22", "aidi.dev", {"as_of": "2026-09-15"}),
        ("RPT-23", "aidi.dev", {"day": "2026-09-14"}), ("RPT-23", "qasim.s", {"day": "2026-09-14"}),
        ("RPT-24", "aidi.dev", {"activity_date_from": "2026-09-01", "activity_date_to": "2026-09-15"}),
        ("RPT-24", "aidi.dev", {"activity_date_from": "2026-09-01", "activity_date_to": "2026-09-15", "section": "collectors"}),
        ("RPT-24", "aidi.dev", {"activity_date_from": "2025-09-01", "activity_date_to": "2026-08-31", "section": "trend"}),
        ("RPT-24", "marwan.a", {"activity_date_from": "2026-09-01", "activity_date_to": "2026-09-15"}),
        ("RPT-25", "aidi.dev", {"call_date_from": args.cdr_day, "call_date_to": args.cdr_day}),
        ("RPT-25", "marwan.a", {"call_date_from": args.cdr_day, "call_date_to": args.cdr_day}),
        ("RPT-25", "rasha.s", {"call_date_from": args.cdr_day, "call_date_to": args.cdr_day}),
        ("RPT-23", "aidi.dev", {"day": args.cdr_day, "mode": "calls"}),
    ]
    res = []
    if args.only:
        cases = [c for c in cases if c[0] in args.only.split(",")]
    with APP.app_context():
        for rid, name, params in cases:
            rep = registry.get(rid)
            uid = who[name]
            scope = resolve_scope(uid)

            def run():
                reports_v2_cdr.clear_cache()
                return engine.preview(rep, params, uid, scope, privacy.FULL)
            first = run()
            med, lo, hi = _time(run, args.n)
            row = {"report": rid, "actor": name, "params": params, "rows": first["total"],
                   "preview_ms": round(med), "min_max": [round(lo), round(hi)]}
            # export: CSV of the full result through the worker's generate() in a fresh snapshot
            tmp = tempfile.mktemp(suffix=".csv")
            definition = {"params": engine.filters_to_params(rep, engine.normalize_filters(rep, params)),
                          "sort": rep.default_sort}
            t0 = time.perf_counter()
            reports_v2_cdr.clear_cache()
            with engine.snapshot(600_000) as conn:
                out = exports.generate(conn, rep, definition, uid, scope, privacy.FULL, "csv", tmp, batch=500)
            row["export_ms"] = round((time.perf_counter() - t0) * 1000)
            row["export_rows"] = out["row_count"]
            row["export_bytes"] = os.path.getsize(tmp)
            os.remove(tmp)
            res.append(row)
            print(json.dumps(row, ensure_ascii=False), flush=True)
    return res


def main():
    global APP
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=("reconcile", "timings"))
    ap.add_argument("--json")
    ap.add_argument("--n", type=int, default=5)
    ap.add_argument("--cdr-day", default="2026-09-20")
    ap.add_argument("--only", help="comma-separated report ids")
    args = ap.parse_args()
    APP = make_app()
    if args.cmd == "reconcile":
        with APP.test_request_context():
            reconcile(args)
    else:
        with APP.app_context():
            timings(args)


if __name__ == "__main__":
    main()
