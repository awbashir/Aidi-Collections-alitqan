"""Stream R-B reconciliation and timing harness for RPT-09 ... RPT-16 (read-only; not a test).

    docker exec -w /app -e PYTHONPATH=/app cawp-api python tests/test_reports_v2_b_reconcile.py reconcile [--json out.json]
    docker exec -w /app -e PYTHONPATH=/app cawp-api python tests/test_reports_v2_b_reconcile.py timings [--json out.json]

It never writes: the connections are opened with `default_transaction_read_only=on`
(core.reports.perf.make_app) and every v2 query runs in the framework's REPEATABLE READ READ
ONLY snapshot. pytest collects this file (the name matches the stream's pattern) but it
defines no test.

Method (same as R0's `core.reports.perf reconcile`):
1. call the LEGACY endpoint for an actor: `/payment_date*` and `/clints/*` in-process (their
   blueprints, JWT for the actor); `/portfolios` lives in app.py, so it is called over HTTP on
   the running cawp-api (a GET, with a token for the actor);
2. rebuild the legacy row set with an SQL replica of its predicates and check that the
   replica's count equals the endpoint's `countRow` (or list length);
3. run the v2 report (engine.compose) for the same window and actor;
4. put every differing row in a named category; `other` must be 0.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import statistics
import sys
import tempfile
import time
import urllib.request
from decimal import Decimal

from sqlalchemy import text

ACTORS_TEAM = ("aidi.dev", "maher.j", "bader.b", "marwan.a", "qasim.s")
ACTORS_MINE = ("rasha.s", "omnia.m", "ghofran.b", "marwan.a", "aidi.dev")
PROMISE_WINDOWS = (("2025-01-01", "2025-12-31"), ("2025-09-01", "2026-08-31"), ("2026-09-01", "2026-09-30"),
                   ("2026-09-24", "2026-09-24"))
CALLBACK_WINDOWS = (("2026-09-01", "2026-09-30"), ("2026-08-01", "2026-08-31"), ("2025-09-01", "2026-09-30"))
HTTP_BASE = os.environ.get("REPB_LEGACY_HTTP", "http://127.0.0.1:9797")


# ============================================================================ plumbing
def make_app():
    from core.reports.perf import make_app as base
    app = base()
    import api.clients_reports
    app.register_blueprint(api.clients_reports.bp)
    return app


def token(app, user_id):
    from flask_jwt_extended import create_access_token
    with app.app_context():
        return create_access_token(identity=user_id)


def legacy_get(app, path, user_id, params):
    r = app.test_client().get(path, headers={"Authorization": f"Bearer {token(app, user_id)}"}, query_string=params)
    body = r.get_json()
    if r.status_code != 200 or not isinstance(body, dict):
        raise RuntimeError(f"{path} -> {r.status_code} {str(body)[:300]}")
    return body


def legacy_http(app, path, user_id, params):
    from urllib.parse import urlencode
    req = urllib.request.Request(f"{HTTP_BASE}{path}?{urlencode(params, doseq=True)}",
                                 headers={"Authorization": f"Bearer {token(app, user_id)}"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8"))


def uid_map(names):
    from core.reports.perf import user_ids
    return user_ids(names)


def v2_ids(conn, report, user_id, params, id_expr):
    from core.reports import engine
    from services.clients_scope import resolve_scope
    filters = engine.normalize_filters(report, params)
    ctx = engine.make_context(report, user_id, resolve_scope(user_id), filters)
    from_sql, where_sql, p = engine.compose(report, ctx)
    ids = {r[0] for r in conn.execute(text(f"SELECT ({id_expr})::text FROM {from_sql} WHERE {where_sql}"), p)}
    tot = conn.execute(text(engine.totals_sql(report, from_sql, where_sql)), p).mappings().one()
    return ids, engine.format_totals(report, tot)


def scope_pred(user_id, alias="c"):
    from core.reports import engine
    from services.clients_scope import resolve_scope
    return engine.scope_sql(resolve_scope(user_id), alias, "lg")


def utc(day, plus=0):
    return _dt.datetime.fromisoformat(day).replace(tzinfo=_dt.timezone.utc) + _dt.timedelta(days=plus)


def emit(rows, row):
    rows.append(row)
    print(json.dumps(row, ensure_ascii=False, default=str), flush=True)


# ============================================================================ promises
def promise_sum(conn, ids):
    return conn.execute(text('SELECT coalesce(sum(round(amount::numeric, 2)), 0) FROM "paymentDates" '
                             'WHERE id = ANY(CAST(:i AS uuid[]))'), {"i": sorted(ids)}).scalar()


def categorize_promises(conn, only_legacy, only_v2, user_id):
    sc, sp = scope_pred(user_id)
    rows = conn.execute(text(f"""
        SELECT d.id::text AS id, round(d.amount::numeric, 2) AS amount, d.is_deleted, c.is_archive,
               ({sc}) AS in_scope
          FROM "paymentDates" d JOIN clints c ON c.id = d.clint_id
         WHERE d.id = ANY(CAST(:ids AS uuid[]))"""), {**sp, "ids": sorted(only_legacy | only_v2)}).mappings().all()
    out = {}
    for r in rows:
        if r["id"] in only_legacy:
            cat = ("legacy_only:deleted_promise" if r["is_deleted"] else
                   "legacy_only:unscoped_legacy" if not r["in_scope"] else "legacy_only:other")
        else:
            cat = "v2_only:other"
        c = out.setdefault(cat, {"n": 0, "amount": Decimal(0)})
        c["n"] += 1
        c["amount"] += r["amount"]
    return {k: {"n": v["n"], "amount": str(v["amount"])} for k, v in sorted(out.items())}


def reconcile_promises(app, rows):
    from core.reports import engine, registry
    names = set(ACTORS_TEAM) | set(ACTORS_MINE)
    uid = uid_map(names)
    for start, end in PROMISE_WINDOWS:
        window = [("customeFilterDateValue[]", start), ("customeFilterDateValue[]", end), ("page", "1"), ("pageSize", "1")]
        for rid, path, actors, extra in (("RPT-09", "/payment_date", ACTORS_TEAM, ""),
                                         ("RPT-10", "/payment_date/currentUser", ACTORS_MINE, " AND d.user_id = CAST(:me AS uuid)")):
            report = registry.get(rid)
            for name in actors:
                user = uid[name]
                legacy = legacy_get(app, path, user, window)
                with engine.snapshot() as conn:
                    rep = {r[0] for r in conn.execute(text(
                        f'SELECT d.id::text FROM "paymentDates" d WHERE d.is_deleted = false '
                        f'AND d.payment_date >= :lo AND d.payment_date < :hi{extra}'),
                        {"lo": utc(start), "hi": utc(end, 1), "me": user})}
                    v2, tot = v2_ids(conn, report, user, {"due_date_from": start, "due_date_to": end}, "pd.id")
                    only_l, only_v = rep - v2, v2 - rep
                    cats = categorize_promises(conn, only_l, only_v, user) if (only_l or only_v) else {}
                    emit(rows, {"report": rid, "legacy": path, "actor": name, "window": f"{start}..{end}",
                                "legacy_count": legacy["countRow"], "replica_count": len(rep),
                                "replica_matches": len(rep) == legacy["countRow"],
                                "legacy_promised_exact": str(promise_sum(conn, rep)),
                                "legacy_totalAmount": legacy.get("totalAmount"),
                                "v2_count": tot["count"], "v2_promised": tot["promised_amount"],
                                "v2_states": {s: tot[f"{s}_count"] for s in ("open", "overdue", "fulfilled", "cancelled")},
                                "v2_allocated": tot["allocated_amount"], "categories": cats})
    # RPT-11 vs /payment_date/user (no date filter, no paging)
    report = registry.get("RPT-11")
    for name in ACTORS_MINE:
        user = uid[name]
        legacy = legacy_get(app, "/payment_date/user", user, [])
        leg_ids = {str(x["id"]) for x in legacy["payment_dates"]}
        with engine.snapshot() as conn:
            rep = {r[0] for r in conn.execute(text(
                'SELECT d.id::text FROM "paymentDates" d JOIN clints c ON c.id = d.clint_id '
                'WHERE c.user_id = CAST(:me AS uuid) AND c.is_archive = false'), {"me": user})}
            v2, tot = v2_ids(conn, report, user, {}, "pd.id")
            only_l, only_v = leg_ids - v2, v2 - leg_ids
            cats = categorize_promises(conn, only_l, only_v, user) if (only_l or only_v) else {}
            emit(rows, {"report": "RPT-11", "legacy": "/payment_date/user", "actor": name, "window": "all dates",
                        "legacy_count": len(leg_ids), "replica_count": len(rep), "replica_matches": rep == leg_ids,
                        "legacy_promised_exact": str(promise_sum(conn, leg_ids)), "legacy_totalAmount": None,
                        "v2_count": tot["count"], "v2_promised": tot["promised_amount"],
                        "v2_states": {s: tot[f"{s}_count"] for s in ("open", "overdue", "fulfilled", "cancelled")},
                        "v2_allocated": tot["allocated_amount"], "categories": cats})


# ============================================================================ RPT-12 portfolios
def reconcile_portfolios(app, rows):
    from core.reports import engine, registry
    report = registry.get("RPT-12")
    uid = uid_map(("aidi.dev", "maher.j", "bader.b", "qasim.s", "rasha.s"))
    for name in ("aidi.dev", "maher.j", "bader.b", "qasim.s", "rasha.s"):
        user = uid[name]
        for mode, legacy_flag, v2_state in (("all", None, None), ("unarchived", "false", "active"),
                                            ("archived", "true", "archived")):
            params = [("page", "1"), ("pageSize", "1000")]
            if legacy_flag:
                params.append(("customeFilterArchivingStatusesValue[]", legacy_flag))
            legacy = legacy_http(app, "/portfolios", user, params)
            leg = {p["id"]: p for p in legacy["portfolios"]}
            with engine.snapshot() as conn:
                filters = engine.normalize_filters(report, {"case_state": v2_state} if v2_state else {})
                from services.clients_scope import resolve_scope
                ctx = engine.make_context(report, user, resolve_scope(user), filters)
                fs, ws, p = engine.compose(report, ctx)
                v2 = {r["id"]: r for r in conn.execute(text(
                    f"SELECT pf.id::text AS id, coalesce(ca.cases, 0) AS cases, coalesce(ca.debt, 0) AS debt, "
                    f"coalesce(pa.recorded, 0) AS recorded, coalesce(pa.approved, 0) AS approved, "
                    f"coalesce(pa.payment_count, 0) AS n FROM {fs} WHERE {ws}"), p).mappings()}
                tot = engine.format_totals(report, conn.execute(text(engine.totals_sql(report, fs, ws)), p).mappings().one())
                arch = "" if not legacy_flag else f" AND c.is_archive = {legacy_flag}"
                # exact numeric replica of the legacy sums (all payment rows, deleted included; raw amounts)
                exact = conn.execute(text(f"""
                    SELECT (SELECT coalesce(sum(c.amount::numeric), 0) FROM clints c WHERE TRUE{arch}) AS debt_raw,
                           (SELECT coalesce(sum(round(c.amount::numeric, 2)), 0) FROM clints c WHERE TRUE{arch}) AS debt_rounded,
                           (SELECT count(*) FROM clints c WHERE TRUE{arch}) AS cases,
                           (SELECT coalesce(sum(p.amount::numeric), 0) FROM "Payments" p JOIN clints c ON c.id = p.clint_id
                             WHERE TRUE{arch}) AS pay_raw,
                           (SELECT coalesce(sum(round(p.amount::numeric, 2)), 0) FROM "Payments" p JOIN clints c ON c.id = p.clint_id
                             WHERE TRUE{arch}) AS pay_rounded,
                           (SELECT coalesce(sum(round(p.amount::numeric, 2)), 0) FROM "Payments" p JOIN clints c ON c.id = p.clint_id
                             WHERE p.is_deleted AND NOT EXISTS (SELECT 1 FROM payment_reversals r WHERE r.payment_id = p.id){arch})
                             AS pay_deleted_unreversed""")).mappings().one()
            leg_cases = sum(int(x["countClints"] or 0) for x in leg.values())
            leg_debt = sum(float(x["countClintsAmount"] or 0) for x in leg.values())
            leg_pay = sum(float(x["payments"] or 0) for x in leg.values())
            only_l = set(leg) - set(v2)
            differing = [k for k in set(leg) & set(v2)
                         if int(leg[k]["countClints"] or 0) != v2[k]["cases"]]
            same = [k for k in set(leg) & set(v2) if k not in differing]
            debt_diff = [k for k in same if abs(float(leg[k]["countClintsAmount"] or 0) - float(v2[k]["debt"])) > 0.005]
            pay_diff = [k for k in same if abs(float(leg[k]["payments"] or 0) - float(v2[k]["recorded"])) > 0.005]
            emit(rows, {"report": "RPT-12", "legacy": "/portfolios", "actor": name, "cases": mode,
                        "legacy_portfolios": legacy["countRow"], "v2_portfolios": tot["count"],
                        "legacy_only_portfolios": len(only_l),
                        "portfolios_with_other_case_count": len(differing),
                        "same_count_portfolios_debt_differs": len(debt_diff),
                        "same_count_portfolios_payments_differ": len(pay_diff),
                        "legacy_cases": leg_cases, "v2_cases": tot["case_count"], "db_cases_all": exact["cases"],
                        "legacy_debt_float": round(leg_debt, 2), "legacy_debt_exact_raw": str(exact["debt_raw"]),
                        "legacy_debt_rounded_per_case": str(exact["debt_rounded"]), "v2_debt": tot["original_debt"],
                        "legacy_payments_float": round(leg_pay, 2), "legacy_payments_exact_raw": str(exact["pay_raw"]),
                        "legacy_payments_rounded": str(exact["pay_rounded"]),
                        "legacy_deleted_unreversed": str(exact["pay_deleted_unreversed"]),
                        "v2_recorded": tot["recorded_amount"], "v2_approved": tot["approved_amount"],
                        "v2_pending": tot["pending_amount"], "v2_rejected": tot["rejected_amount"],
                        "v2_reversed": tot["reversed_amount"], "v2_outstanding": tot["outstanding_amount"],
                        "v2_empty": tot["empty_portfolio_count"]})


# ============================================================================ RPT-13/14/15
def reconcile_cases(app, rows):
    from core.reports import engine, registry
    uid = uid_map(set(ACTORS_TEAM) | {"rasha.s"})
    plan = (("RPT-13", "/clints/have_payments", "coalesce(ps.n, 0) > 0"),
            ("RPT-14", "/clints/not_have_payments", "coalesce(ps.n, 0) = 0"),
            ("RPT-15", "/clints/neglect", "TRUE"))
    for rid, path, cond in plan:
        report = registry.get(rid)
        for name in ACTORS_TEAM + ("rasha.s",):
            user = uid[name]
            legacy = legacy_get(app, path, user, [("page", "1"), ("pageSize", "1")])
            with engine.snapshot() as conn:
                sc, sp = scope_pred(user)
                rep = {r[0] for r in conn.execute(text(f"""
                    SELECT c.id::text FROM clints c
                      LEFT JOIN (SELECT clint_id, count(*) AS n FROM "Payments" GROUP BY clint_id) ps ON ps.clint_id = c.id
                     WHERE c.is_archive = false AND ({sc}) AND {cond}"""), sp)}
                v2, tot = v2_ids(conn, report, user, {}, "c.id")
                only_l, only_v = rep - v2, v2 - rep
                cats = {}
                if only_l or only_v:
                    for r in conn.execute(text("""
                        SELECT c.id::text AS id,
                               EXISTS (SELECT 1 FROM "Payments" p WHERE p.clint_id = c.id AND p.is_deleted
                                        AND NOT EXISTS (SELECT 1 FROM payment_reversals r WHERE r.payment_id = p.id)) AS del
                          FROM clints c WHERE c.id = ANY(CAST(:i AS uuid[]))"""),
                            {"i": sorted(only_l | only_v)}).mappings():
                        side = "legacy_only" if r["id"] in only_l else "v2_only"
                        cat = f"{side}:deleted_payment_record" if r["del"] else f"{side}:other"
                        cats[cat] = cats.get(cat, 0) + 1
                leg_debt_exact = conn.execute(text(
                    "SELECT coalesce(sum(round(amount::numeric, 2)), 0) FROM clints WHERE id = ANY(CAST(:i AS uuid[]))"),
                    {"i": sorted(rep)}).scalar()
                extra = {}
                if rid == "RPT-15":
                    extra = {"legacy_null_age": conn.execute(text(
                        "SELECT count(*) FROM clints WHERE id = ANY(CAST(:i AS uuid[])) AND last_comment_at IS NULL"),
                        {"i": sorted(rep)}).scalar(), "v2_without_note": tot["without_note_count"]}
                if rid == "RPT-13":
                    extra = {"v2_recorded": tot["recorded_amount"], "v2_approved": tot["approved_amount"],
                             "v2_payment_count": tot["payment_count"], "v2_unapproved_only": tot["cases_unapproved_only"]}
                emit(rows, {"report": rid, "legacy": path, "actor": name, "legacy_count": legacy["countRow"],
                            "replica_count": len(rep), "replica_matches": len(rep) == legacy["countRow"],
                            "legacy_totalAmount": legacy.get("totalAmount"), "legacy_debt_exact": str(leg_debt_exact),
                            "v2_count": tot["count"], "v2_debt": tot["original_debt"], "categories": cats, **extra})


# ============================================================================ RPT-16
# real callbacks stored late in the UTC day (22:51Z on 17 Aug 2026 = 18 Aug in Riyadh)
CALLBACK_BOUNDARY = (("tabby.1", "2026-08-17", "2026-08-17"), ("tabby.1", "2026-08-18", "2026-08-18"))


def reconcile_callbacks(app, rows, plan=None):
    from core.reports import engine, registry
    report = registry.get("RPT-16")
    plan = plan or [(n, s, e) for s, e in CALLBACK_WINDOWS for n in ACTORS_MINE]
    uid = uid_map({n for n, _, _ in plan})
    for name, start, end in plan:
        user = uid[name]
        legacy = legacy_get(app, "/clints/currentUser/have_nextCalls", user,
                            [("customeFilterDateValue[]", start), ("customeFilterDateValue[]", end),
                             ("page", "1"), ("pageSize", "1")])
        with engine.snapshot() as conn:
            rep = {r[0] for r in conn.execute(text("""
                SELECT n.id::text FROM clints c
                  JOIN portfolios pf ON pf.id = c.portfolios_id JOIN contracts ct ON ct.id = pf.contract_id
                  JOIN "Comments" n ON n.clint_id = c.id
                 WHERE c.is_archive = false AND c.user_id = CAST(:me AS uuid) AND n.next_contact_date IS NOT NULL
                   AND (n.next_contact_date AT TIME ZONE 'UTC')::date BETWEEN CAST(:s AS date) AND CAST(:e AS date)"""),
                {"me": user, "s": start, "e": end})}
            v2, tot = v2_ids(conn, report, user, {"callback_from": start, "callback_to": end}, "n.id")
            only_l, only_v = rep - v2, v2 - rep
            cats = {}
            if only_l or only_v:
                for r in conn.execute(text("""
                    SELECT n.id::text AS id, n.is_deleted,
                           (n.next_contact_date AT TIME ZONE 'UTC')::date AS d_utc,
                           (n.next_contact_date AT TIME ZONE 'Asia/Riyadh')::date AS d_riyadh
                      FROM "Comments" n WHERE n.id = ANY(CAST(:i AS uuid[]))"""),
                        {"i": sorted(only_l | only_v)}).mappings():
                    s, e = _dt.date.fromisoformat(start), _dt.date.fromisoformat(end)
                    if r["id"] in only_l:
                        cat = ("legacy_only:retired_note" if r["is_deleted"] else
                               "legacy_only:riyadh_day" if not (s <= r["d_riyadh"] <= e) else "legacy_only:other")
                    else:
                        cat = "v2_only:riyadh_day" if not (s <= r["d_utc"] <= e) else "v2_only:other"
                    cats[cat] = cats.get(cat, 0) + 1
            emit(rows, {"report": "RPT-16", "legacy": "/clints/currentUser/have_nextCalls", "actor": name,
                        "window": f"{start}..{end}", "legacy_count": legacy["countRow"],
                        "replica_count": len(rep), "replica_matches": len(rep) == legacy["countRow"],
                        "legacy_totalAmount_debt_per_row": legacy.get("totalAmount"),
                        "v2_count": tot["count"], "v2_cases": tot["case_count"],
                        "v2_status": {k: tot[f"{k}_count"] for k in ("open", "late", "completed", "superseded",
                                                                      "cancelled", "untracked")},
                        "categories": cats})


def callbacks_all_owners(app, rows):
    """RPT-16 is per caller (owner = me); this replica-level row compares the legacy rule with
    the v2 rule over EVERY owner at once, so the rare categories show up."""
    from core.reports import engine
    for start, end in CALLBACK_WINDOWS:
        with engine.snapshot() as conn:
            r = conn.execute(text("""
                WITH base AS (
                    SELECT n.id, n.is_deleted,
                           (n.next_contact_date AT TIME ZONE 'UTC')::date BETWEEN CAST(:s AS date) AND CAST(:e AS date) AS in_utc,
                           (n.next_contact_date AT TIME ZONE 'Asia/Riyadh')::date BETWEEN CAST(:s AS date) AND CAST(:e AS date) AS in_riyadh
                      FROM clints c JOIN "Comments" n ON n.clint_id = c.id
                     WHERE c.is_archive = false AND c.user_id IS NOT NULL AND n.next_contact_date IS NOT NULL)
                SELECT count(*) FILTER (WHERE in_utc) AS legacy_rule,
                       count(*) FILTER (WHERE in_riyadh AND NOT is_deleted) AS v2_rule,
                       count(*) FILTER (WHERE in_utc AND is_deleted) AS legacy_only_retired,
                       count(*) FILTER (WHERE in_utc AND NOT in_riyadh AND NOT is_deleted) AS legacy_only_riyadh_day,
                       count(*) FILTER (WHERE in_riyadh AND NOT in_utc AND NOT is_deleted) AS v2_only_riyadh_day
                  FROM base"""), {"s": start, "e": end}).mappings().one()
        emit(rows, {"report": "RPT-16", "legacy": "replica, all owners", "actor": "(every owner)",
                    "window": f"{start}..{end}", **dict(r),
                    "balanced": r["legacy_rule"] - r["legacy_only_retired"] - r["legacy_only_riyadh_day"]
                    + r["v2_only_riyadh_day"] == r["v2_rule"]})


def reconcile(app, as_json=None):
    rows = []
    with app.app_context():
        reconcile_promises(app, rows)
        reconcile_portfolios(app, rows)
        reconcile_cases(app, rows)
        reconcile_callbacks(app, rows)
        reconcile_callbacks(app, rows, list(CALLBACK_BOUNDARY))
        callbacks_all_owners(app, rows)
    if as_json:
        with open(as_json, "w") as f:
            json.dump(rows, f, ensure_ascii=False, indent=1, default=str)
    return rows


# ============================================================================ timings
def _median_ms(fn, n=5):
    samples = []
    for _ in range(n):
        t = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - t) * 1000)
    return round(statistics.median(samples), 1), round(min(samples), 1), round(max(samples), 1)


PREVIEWS = (
    ("RPT-09", "aidi.dev", {}), ("RPT-09", "aidi.dev", {"due_date_from": "2025-09-01", "due_date_to": "2026-08-31"}),
    ("RPT-09", "bader.b", {}), ("RPT-09", "aidi.dev", {"sort": "due_date", "page": "100"}),
    ("RPT-09", "aidi.dev", {"state": "overdue", "q": "محمد"}),
    ("RPT-10", "rasha.s", {}), ("RPT-11", "rasha.s", {}), ("RPT-11", "marwan.a", {}),
    ("RPT-12", "aidi.dev", {}), ("RPT-12", "aidi.dev", {"case_state": "active"}), ("RPT-12", "bader.b", {}),
    ("RPT-12", "rasha.s", {}),
    ("RPT-13", "aidi.dev", {}), ("RPT-13", "rasha.s", {}), ("RPT-13", "aidi.dev", {"sort": "-approved_amount"}),
    ("RPT-14", "aidi.dev", {}), ("RPT-14", "rasha.s", {}), ("RPT-14", "aidi.dev", {"sort": "customer_name", "page": "500"}),
    ("RPT-15", "aidi.dev", {}), ("RPT-15", "rasha.s", {}), ("RPT-15", "aidi.dev", {"age": "181_plus"}),
    ("RPT-15", "aidi.dev", {"sort": "-age_days"}),
    ("RPT-16", "rasha.s", {"callback_from": "2026-09-01", "callback_to": "2026-09-30"}),
    ("RPT-16", "rasha.s", {}), ("RPT-16", "marwan.a", {}),
)
OPTIONS = (("RPT-09", "recorder"), ("RPT-09", "portfolio"), ("RPT-13", "owner"), ("RPT-13", "note_status"),
           ("RPT-15", "owner"), ("RPT-12", "portfolio"), ("RPT-16", "recorder"))
EXPORTS = (("RPT-09", "aidi.dev", {}), ("RPT-12", "aidi.dev", {}), ("RPT-13", "aidi.dev", {}),
           ("RPT-14", "aidi.dev", {}), ("RPT-15", "aidi.dev", {}), ("RPT-16", "rasha.s", {}),
           ("RPT-10", "rasha.s", {}), ("RPT-11", "rasha.s", {}))


def timings(app, as_json=None, formats=("csv", "xlsx", "xlsx_ar")):
    from core.reports import engine, exports, options, privacy, registry
    from services.clients_scope import resolve_scope
    out = {}
    with app.app_context():
        uid = uid_map({n for _, n, _ in PREVIEWS} | {"aidi.dev", "rasha.s"})
        for rid, name, args in PREVIEWS:
            report, user = registry.get(rid), uid[name]
            res = {}

            def run():
                res["d"] = engine.preview(report, args, user, resolve_scope(user), privacy.FULL)

            med, lo, hi = _median_ms(run)
            key = f"{rid} {name} {json.dumps(args, ensure_ascii=False) if args else ''}".strip()
            out[key] = {"preview_ms_median": med, "min": lo, "max": hi, "rows": res["d"]["total"]}
            print(key, out[key], flush=True)
        for rid, field in OPTIONS:
            report, user = registry.get(rid), uid["rasha.s" if rid == "RPT-16" else "aidi.dev"]
            res = {}

            def run_opt():
                res["d"] = options.options(report, field, {"report": rid}, user, resolve_scope(user))

            med, lo, hi = _median_ms(run_opt, 3)
            out[f"options {rid} {field}"] = {"ms_median": med, "min": lo, "max": hi, "total": res["d"]["total"]}
            print(f"options {rid} {field}", out[f"options {rid} {field}"], flush=True)
        for rid, name, params in EXPORTS:
            report, user = registry.get(rid), uid[name]
            for fmt in formats:
                if fmt not in report.formats():
                    continue
                fd, path = tempfile.mkstemp(suffix="." + ("csv" if fmt == "csv" else "xlsx"))
                os.close(fd)
                t = time.perf_counter()
                with engine.snapshot() as conn:
                    res = exports.generate(conn, report, {"params": params}, user, resolve_scope(user), privacy.FULL,
                                           fmt, path, batch=500, meta={"export_id": "perf", "requested_by": user})
                elapsed = time.perf_counter() - t
                size = os.path.getsize(path)
                os.remove(path)
                k = f"export {rid} {name} {fmt}"
                out[k] = {"seconds": round(elapsed, 2), "rows": res["row_count"], "bytes": size,
                          "rows_per_s": round(res["row_count"] / elapsed) if elapsed else None}
                print(k, out[k], flush=True)
    if as_json:
        with open(as_json, "w") as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
    return out


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="tests/test_reports_v2_b_reconcile.py")
    p.add_argument("what", choices=("reconcile", "timings"))
    p.add_argument("--json")
    a = p.parse_args(argv)
    app = make_app()
    if a.what == "reconcile":
        reconcile(app, a.json)
    else:
        timings(app, a.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
