"""Stream R-A reconciliation and timing harness for RPT-01 … RPT-05 (never writes). No REPORTS here.

    docker exec -w /app cawp-api python -m services.reports_v2_a_perf reconcile [--json out.json]
    docker exec -w /app cawp-api python -m services.reports_v2_a_perf timings [--json out.json]

Every connection runs with `default_transaction_read_only=on` (core.reports.perf.make_app) and the
v2 queries run in the framework's REPEATABLE READ READ ONLY snapshot.

`reconcile` calls the REAL legacy handlers in-process with a token per actor:

    RPT-01  GET /uesrs/export   (api/reports.py; the same count_clints/total_amount computation as
                                 GET /uesrs, app.py:713-741)
    RPT-02  GET /clints         (api/clients_reports.py)
    RPT-03  GET /clints/archive/
    RPT-04  GET /comments       (api/reports.py)
    RPT-05  GET /comments/current_user

and checks an SQL replica of each legacy predicate against the endpoint's own count, then runs the v2
report for the same window/actor and classifies every row that differs (the categories are listed in
review/phase4-reports-a.md §4). `other` must be 0 everywhere.
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
from decimal import Decimal

from sqlalchemy import text

ACTORS = {
    "aidi.dev": "ADMIN, supervisor of all 15 contracts, users_details",
    "maher.j": "project manager, 12 contracts, users_details",
    "s.almalki": "collection manager, 13 contracts, users_details",
    "Tamara": "processing role (معالجة), collector scope (97k own cases), users + users_details",
    "bader.b": "supervisor (مشرف2), 3 contracts",
    "marwan.a": "supervisor (مشرف2), 1 contract + 1 supervised user",
    "qasim.s": "supervisor (مشرف2), 0 contracts + 1 supervised user",
    "rasha.s": "collector",
    "asma.k": "collector with the most notes",
    "ghofran.b": "collector",
}
R01_ACTORS = ("aidi.dev", "maher.j", "s.almalki", "Tamara")
R02_ACTORS = ("aidi.dev", "maher.j", "bader.b", "marwan.a", "qasim.s", "rasha.s", "Tamara")
R03_ACTORS = ("aidi.dev", "maher.j", "bader.b", "marwan.a", "qasim.s")
R04_ACTORS = ("aidi.dev", "maher.j", "bader.b", "marwan.a", "qasim.s")
R05_ACTORS = ("asma.k", "rasha.s", "ghofran.b", "aidi.dev")
NOTE_WINDOWS = (("2026-08-01", "2026-08-31"), ("2026-07-27", "2026-07-27"), ("2025-09-01", "2026-08-31"),
                ("2026-09-01", "2026-09-30"))


def make_app():
    from core.reports.perf import make_app as _make
    app = _make()                       # read-only connections; api.reports registered
    import api.clients_reports
    app.register_blueprint(api.clients_reports.bp)
    return app


def user_ids(names):
    from extensions import db
    rows = db.session.execute(text("SELECT user_name, id::text FROM users WHERE user_name = ANY(:n)"),
                              {"n": list(names)}).all()
    db.session.rollback()
    return dict(rows)


def legacy(app, path, user_id, params):
    from flask_jwt_extended import create_access_token
    with app.app_context():
        token = create_access_token(identity=user_id)
    r = app.test_client().get(path, headers={"Authorization": f"Bearer {token}"}, query_string=params)
    body = r.get_json()
    if r.status_code != 200 or body is None or body.get("status") is False:
        raise RuntimeError(f"{path} -> {r.status_code} {str(body)[:300]}")
    return body


def v2_totals(conn, report_id, user_id, params):
    from core.reports import engine, registry
    from services.clients_scope import resolve_scope
    report = registry.get(report_id)
    filters = engine.normalize_filters(report, params)
    ctx = engine.make_context(report, user_id, resolve_scope(user_id), filters)
    from_sql, where_sql, p = engine.compose(report, ctx)
    tot = conn.execute(text(engine.totals_sql(report, from_sql, where_sql)), p).mappings().one()
    return engine.format_totals(report, tot)


def scope_pred(user_id, alias="c"):
    from core.reports import engine
    from services.clients_scope import resolve_scope
    sql, params = engine.scope_sql(resolve_scope(user_id), alias, "xs")
    # an unassigned case outside the supervised contracts makes the scope predicate NULL (not in
    # scope for the engine's WHERE); the replica's FILTER/NOT arithmetic needs a real boolean
    return f"coalesce(({sql}), false)", params


def _utc(day: str, plus_days=0):
    return _dt.datetime.fromisoformat(day).replace(tzinfo=_dt.timezone.utc) + _dt.timedelta(days=plus_days)


def _riy(day: str, plus_days=0):
    from core import riyadh
    d = _dt.date.fromisoformat(day) + _dt.timedelta(days=plus_days)
    return riyadh.day_bounds(d.isoformat())[0]


def _s(v):
    return str(v) if isinstance(v, Decimal) else v


# =========================================================================== RPT-01
def reconcile_rpt01(app, uid, conn):
    out = []
    for name in R01_ACTORS:
        user = uid[name]
        sp, spp = scope_pred(user)
        for state, legacy_value in (("all", None), ("unarchived", "false"), ("archived", "true")):
            params = [("customeFilterArchivingStatusesValue[]", legacy_value)] if legacy_value else []
            body = legacy(app, "/uesrs/export", user, params)
            users = body["users"]
            l_count = len(users)
            l_cases = sum(int(u["count_clints"] or 0) for u in users)
            l_float = sum(float(u["total_amount"] or 0) for u in users)
            state_sql = {"all": "TRUE", "unarchived": "NOT c.is_archive", "archived": "c.is_archive"}[state]
            rep = conn.execute(text(f"""
                SELECT count(*) AS cases, coalesce(sum(c.amount::numeric), 0) AS raw,
                       coalesce(sum(round(c.amount::numeric, 2)), 0) AS rounded,
                       count(*) FILTER (WHERE NOT ({sp})) AS out_n,
                       coalesce(sum(round(c.amount::numeric, 2)) FILTER (WHERE NOT ({sp})), 0) AS out_debt
                  FROM clints c JOIN users u ON u.id = c.user_id WHERE {state_sql}"""), spp).mappings().one()
            v2 = v2_totals(conn, "RPT-01", user, {"case_state": state} if state != "all" else {})
            row = {"report": "RPT-01", "legacy": "/uesrs/export", "actor": name, "case_state": state,
                   "legacy_users": l_count, "legacy_cases": l_cases, "legacy_total_amount_float": l_float,
                   "replica_cases": rep["cases"], "replica_matches": rep["cases"] == l_cases,
                   "legacy_debt_exact_raw": str(rep["raw"]), "legacy_debt_rounded_per_case": str(rep["rounded"]),
                   "v2_users": v2["count"], "v2_cases": v2["case_count"], "v2_debt": v2["original_debt"],
                   "minus_case_scope": {"n": rep["out_n"], "debt": str(rep["out_debt"])},
                   "minus_employee_scope": l_count - v2["count"]}
            row["explained"] = (rep["cases"] - rep["out_n"] == v2["case_count"]
                                and rep["rounded"] - rep["out_debt"] == Decimal(v2["original_debt"])
                                and row["minus_employee_scope"] == 0)
            out.append(row)
            print(json.dumps(row, ensure_ascii=False), flush=True)
    # Informational: supervisors WITHOUT the `users` key (so no v2 access) and without identity
    # administration. Legacy GET /uesrs (app.py:678) is JWT-only, so they can read all 289 users there;
    # this shows the employee scope the v2 roster would apply if they were granted `users`.
    for name in ("bader.b", "marwan.a", "qasim.s"):
        user = uid[name]
        from services.clients_scope import resolve_scope
        sc = resolve_scope(user)
        sp, spp = scope_pred(user)
        rep = conn.execute(text(f"""
            SELECT (SELECT count(*) FROM users) AS legacy_users,
                   (SELECT count(*) FROM users u
                     WHERE u.id = CAST(:me AS uuid) OR u.id = ANY(CAST(:uids AS uuid[]))
                        OR u.contract_id = ANY(CAST(:cids AS uuid[]))
                        OR EXISTS (SELECT 1 FROM clints c WHERE c.user_id = u.id AND {sp})) AS scoped_users,
                   (SELECT count(*) FROM clints c WHERE c.user_id IS NOT NULL) AS legacy_cases,
                   (SELECT count(*) FROM clints c WHERE c.user_id IS NOT NULL AND {sp}) AS scoped_cases"""),
            {**spp, "me": user, "uids": sorted(sc.user_ids or []), "cids": sorted(sc.contract_ids or [])}
        ).mappings().one()
        v2 = v2_totals(conn, "RPT-01", user, {})
        row = {"report": "RPT-01", "legacy": "GET /uesrs (JWT-only)", "actor": name, "case_state": "all",
               "informational": "no `users` key: v2 answers 403; engine run directly to show the scope",
               "legacy_users": rep["legacy_users"], "legacy_cases": rep["legacy_cases"],
               "v2_users": v2["count"], "v2_cases": v2["case_count"], "v2_debt": v2["original_debt"],
               "minus_employee_scope": rep["legacy_users"] - v2["count"],
               "minus_case_scope": {"n": rep["legacy_cases"] - rep["scoped_cases"]}}
        row["explained"] = v2["count"] == rep["scoped_users"] and v2["case_count"] == rep["scoped_cases"]
        out.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
    return out


# =========================================================================== RPT-02
def reconcile_rpt02(app, uid, conn):
    out = []
    contracts = conn.execute(text("SELECT id::text, name FROM contracts ORDER BY name LIMIT 2")).all()
    for name in R02_ACTORS:
        user = uid[name]
        variants = [("all", [], {})]
        if name == "aidi.dev":
            cid = contracts[0][0]
            variants.append((f"contract={contracts[0][1]}", [("customeFilterContractValue[]", cid)], {"contract": cid}))
        for label, lparams, vparams in variants:
            body = legacy(app, "/clints", user, lparams + [("page", "1"), ("pageSize", "1")])
            v2 = v2_totals(conn, "RPT-02", user, vparams)
            sp, spp = scope_pred(user)
            extra = "AND pf.contract_id = CAST(:k AS uuid)" if vparams else ""
            rep = conn.execute(text(f"""
                SELECT count(*) AS n, coalesce(sum(c.amount::numeric), 0) AS raw,
                       coalesce(sum(round(c.amount::numeric, 2)), 0) AS rounded
                  FROM clints c JOIN portfolios pf ON pf.id = c.portfolios_id
                 WHERE NOT c.is_archive AND ({sp}) {extra}"""), {**spp, "k": vparams.get("contract")}).mappings().one()
            row = {"report": "RPT-02", "legacy": "/clints", "actor": name, "variant": label,
                   "legacy_count": body["countRow"], "legacy_total_amount_float": body["totalAmount"],
                   "legacy_debt_exact_raw": str(rep["raw"]), "v2_count": v2["count"],
                   "v2_original_debt": v2["original_debt"], "v2_approved": v2["approved_amount"],
                   "v2_pending": v2["pending_amount"], "v2_outstanding": v2["outstanding_amount"],
                   "rounding_delta": str(rep["rounded"] - rep["raw"])}
            row["explained"] = (body["countRow"] == v2["count"] == rep["n"]
                                and Decimal(v2["original_debt"]) == rep["rounded"])
            out.append(row)
            print(json.dumps(row, ensure_ascii=False), flush=True)
    return out


# =========================================================================== RPT-03
def reconcile_rpt03(app, uid, conn):
    out = []
    for name in R03_ACTORS:
        user = uid[name]
        body = legacy(app, "/clints/archive/", user, [("page", "1"), ("pageSize", "1")])
        v2 = v2_totals(conn, "RPT-03", user, {})
        sp, spp = scope_pred(user)
        rep = conn.execute(text(f"""
            SELECT count(*) FILTER (WHERE c."clintArchive_id" IS NOT NULL) AS legacy_n,
                   count(*) FILTER (WHERE ({sp})) AS v2_n,
                   count(*) FILTER (WHERE c."clintArchive_id" IS NOT NULL AND NOT ({sp})) AS minus_scope,
                   coalesce(sum(round(c.amount::numeric, 2)) FILTER (
                       WHERE c."clintArchive_id" IS NOT NULL AND NOT ({sp})), 0) AS minus_scope_debt,
                   count(*) FILTER (WHERE c."clintArchive_id" IS NULL AND ({sp})) AS plus_no_record
              FROM clints c WHERE c.is_archive"""), spp).mappings().one()
        row = {"report": "RPT-03", "legacy": "/clints/archive/", "actor": name,
               "legacy_count": body["countRow"], "replica_matches": rep["legacy_n"] == body["countRow"],
               "v2_count": v2["count"], "v2_original_debt": v2["original_debt"],
               "v2_approved": v2["approved_amount"], "v2_outstanding": v2["outstanding_amount"],
               "minus_case_scope": {"n": rep["minus_scope"], "debt": str(rep["minus_scope_debt"])},
               "plus_no_archive_record": rep["plus_no_record"]}
        row["explained"] = (body["countRow"] - rep["minus_scope"] + rep["plus_no_record"] == v2["count"] == rep["v2_n"])
        out.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
    return out


# =========================================================================== RPT-04 / RPT-05
def _note_categories(conn, start, end, where_actor, sp, spp, params):
    """Legacy (UTC days, `where_actor`) vs v2 (Riyadh days, scope `sp`): counts per category."""
    ulo, uhi = _utc(start), _utc(end, 1)
    rlo, rhi = _riy(start), _riy(end, 1)
    p = {**spp, **params, "ulo": ulo, "uhi": uhi, "rlo": rlo, "rhi": rhi,
         "lo": min(ulo, rlo), "hi": max(uhi, rhi)}
    return conn.execute(text(f"""
        WITH n AS (
            SELECT m.id, m.clint_id, m.create_at,
                   (m.create_at >= :ulo AND m.create_at < :uhi) AS in_leg,
                   ({sp}) AS in_scope,
                   (m.create_at >= :rlo AND m.create_at < :rhi) AS in_riy
              FROM "Comments" m JOIN clints c ON c.id = m.clint_id
             WHERE m.is_deleted = false AND {where_actor} AND m.create_at >= :lo AND m.create_at < :hi),
        k AS (SELECT *, in_riy AND in_scope AS in_v2 FROM n)
        SELECT count(*) FILTER (WHERE in_leg) AS legacy_n,
               count(*) FILTER (WHERE in_v2) AS v2_n,
               count(*) FILTER (WHERE in_leg AND in_v2) AS both_n,
               count(*) FILTER (WHERE in_v2 AND NOT in_leg AND create_at < :ulo) AS plus_riyadh_first_day,
               count(*) FILTER (WHERE in_v2 AND NOT in_leg AND create_at >= :ulo) AS plus_other,
               count(*) FILTER (WHERE in_leg AND NOT in_v2 AND NOT in_scope) AS minus_case_scope,
               count(*) FILTER (WHERE in_leg AND NOT in_v2 AND in_scope AND create_at >= :rhi) AS minus_utc_last_evening,
               count(*) FILTER (WHERE in_leg AND NOT in_v2 AND in_scope AND create_at < :rhi) AS minus_other,
               count(DISTINCT clint_id) FILTER (WHERE in_leg) AS legacy_cases,
               count(DISTINCT clint_id) FILTER (WHERE in_v2) AS v2_cases
          FROM k"""), p).mappings().one()


def reconcile_notes(app, uid, conn, report_id):
    out = []
    actors = R04_ACTORS if report_id == "RPT-04" else R05_ACTORS
    path = "/comments" if report_id == "RPT-04" else "/comments/current_user"
    for start, end in NOTE_WINDOWS:
        for name in actors:
            user = uid[name]
            body = legacy(app, path, user, [("customeFilterDateValue[]", start), ("customeFilterDateValue[]", end),
                                            ("page", "1"), ("pageSize", "1")])
            v2 = v2_totals(conn, report_id, user, {"created_from": start, "created_to": end})
            if report_id == "RPT-04":
                sp, spp = scope_pred(user)
                where_actor, params = "TRUE", {}
            else:
                sp, spp = "TRUE", {}
                where_actor, params = "m.user_id = CAST(:me AS uuid)", {"me": user}
            cat = _note_categories(conn, start, end, where_actor, sp, spp, params)
            row = {"report": report_id, "legacy": path, "actor": name, "window": f"{start}..{end}",
                   "legacy_count": body["countRow"], "replica_matches": cat["legacy_n"] == body["countRow"],
                   "v2_count": v2["count"], "v2_case_count": v2["case_count"],
                   "categories": {k: cat[k] for k in ("plus_riyadh_first_day", "minus_case_scope",
                                                      "minus_utc_last_evening", "plus_other", "minus_other")}}
            if report_id == "RPT-05":
                row["legacy_total_amount_float"] = body.get("totalAmount")
            row["explained"] = (cat["v2_n"] == v2["count"] and cat["plus_other"] == 0 and cat["minus_other"] == 0
                                and body["countRow"] + cat["plus_riyadh_first_day"] - cat["minus_case_scope"]
                                - cat["minus_utc_last_evening"] == v2["count"])
            if report_id == "RPT-04" and (start, end) == ("2026-08-01", "2026-08-31"):
                latest = v2_totals(conn, report_id, user, {"created_from": start, "created_to": end,
                                                           "note_selection": "latest_per_case"})
                ties = conn.execute(text(f"""
                    SELECT count(*) FROM (
                        SELECT m.clint_id FROM "Comments" m JOIN clints c ON c.id = m.clint_id
                         WHERE m.is_deleted = false AND ({sp}) AND m.create_at >= :rlo AND m.create_at < :rhi
                           AND m.create_at = (SELECT max(m2.create_at) FROM "Comments" m2
                                               WHERE m2.clint_id = m.clint_id AND m2.is_deleted = false
                                                 AND m2.create_at >= :rlo AND m2.create_at < :rhi)
                         GROUP BY m.clint_id HAVING count(*) > 1) t"""),
                    {**spp, "rlo": _riy(start), "rhi": _riy(end, 1)}).scalar()
                row["latest_per_case"] = {"legacy_js_dedup_count": cat["legacy_cases"], "v2_count": latest["count"],
                                          "v2_case_count": latest["case_count"],
                                          "cases_with_tied_latest_notes": ties}
                row["explained"] = row["explained"] and latest["count"] == cat["v2_cases"] == v2["case_count"]
            out.append(row)
            print(json.dumps(row, ensure_ascii=False), flush=True)
    return out


def reconcile(app, as_json=None):
    from core.reports import engine
    results = {}
    with app.app_context():
        uid = user_ids(ACTORS)
        with engine.snapshot() as conn:
            results["RPT-01"] = reconcile_rpt01(app, uid, conn)
            results["RPT-02"] = reconcile_rpt02(app, uid, conn)
            results["RPT-03"] = reconcile_rpt03(app, uid, conn)
            results["RPT-04"] = reconcile_notes(app, uid, conn, "RPT-04")
            results["RPT-05"] = reconcile_notes(app, uid, conn, "RPT-05")
    bad = [r for rows in results.values() for r in rows if not r["explained"]]
    print(f"rows: {sum(len(v) for v in results.values())}, unexplained: {len(bad)}", flush=True)
    if as_json:
        with open(as_json, "w") as f:
            json.dump(results, f, ensure_ascii=False, indent=1, default=_s)
    return results


# =========================================================================== timings
def _median_ms(fn, n=5):
    samples = []
    for _ in range(n):
        t = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - t) * 1000)
    return round(statistics.median(samples), 1), round(min(samples), 1), round(max(samples), 1)


def timings(app, as_json=None):
    from core.reports import engine, exports, options, privacy, registry
    from services.clients_scope import resolve_scope
    out = {}
    month = {"created_from": "2026-08-01", "created_to": "2026-08-31"}
    year = {"created_from": "2025-09-01", "created_to": "2026-08-31"}
    with app.app_context():
        uid = user_ids(ACTORS)
        cases = [
            ("RPT-01", "aidi.dev", {}), ("RPT-01", "aidi.dev", {"case_state": "unarchived"}),
            ("RPT-01", "Tamara", {}),
            ("RPT-02", "aidi.dev", {}), ("RPT-02", "marwan.a", {}), ("RPT-02", "rasha.s", {}),
            ("RPT-02", "aidi.dev", {"q": "محمد"}), ("RPT-02", "aidi.dev", {"sort": "-outstanding_amount"}),
            ("RPT-02", "aidi.dev", {"sort": "last_note_at", "page": "200"}),
            ("RPT-03", "aidi.dev", {}), ("RPT-03", "marwan.a", {}),
            ("RPT-03", "aidi.dev", {"archived_from": "2026-09-01", "archived_to": "2026-09-30"}),
            ("RPT-04", "aidi.dev", month), ("RPT-04", "aidi.dev", {**month, "note_selection": "latest_per_case"}),
            ("RPT-04", "aidi.dev", year), ("RPT-04", "aidi.dev", {**year, "note_selection": "latest_per_case"}),
            ("RPT-04", "marwan.a", month), ("RPT-04", "aidi.dev", {**month, "text": "وعد"}),
            ("RPT-05", "asma.k", year), ("RPT-05", "asma.k", month),
        ]
        for rid, name, args in cases:
            report, user = registry.get(rid), uid[name]
            scope = resolve_scope(user)
            res = {}

            def run():
                res["d"] = engine.preview(report, args, user, scope, privacy.FULL)

            med, lo, hi = _median_ms(run)
            key = f"{rid} {name} {json.dumps(args, ensure_ascii=False) if args else ''}".strip()
            out[key] = {"preview_ms_median": med, "min": lo, "max": hi, "rows": res["d"]["total"]}
            print(key, out[key], flush=True)
        for rid, field, args in (("RPT-02", "owner", {}), ("RPT-02", "note_status", {}), ("RPT-03", "archived_by", {}),
                                 ("RPT-03", "archive_reason", {}), ("RPT-04", "recorder", month),
                                 ("RPT-04", "status", month), ("RPT-01", "role", {})):
            report, user = registry.get(rid), uid["aidi.dev"]
            res = {}

            def run_opt():
                res["d"] = options.options(report, field, {"report": rid, **args}, user, resolve_scope(user))

            med, lo, hi = _median_ms(run_opt)
            out[f"options {rid} {field} aidi.dev"] = {"ms_median": med, "min": lo, "max": hi,
                                                      "total": res["d"]["total"]}
            print(rid, field, out[f"options {rid} {field} aidi.dev"], flush=True)

        for rid, name, params, fmts in (("RPT-01", "aidi.dev", {}, ("csv", "xlsx_ar")),
                                        ("RPT-02", "aidi.dev", {}, ("csv", "xlsx", "xlsx_ar")),
                                        ("RPT-03", "aidi.dev", {}, ("csv", "xlsx_ar")),
                                        ("RPT-04", "aidi.dev", month, ("csv", "xlsx_ar")),
                                        ("RPT-04", "aidi.dev", {**month, "note_selection": "latest_per_case"}, ("csv",)),
                                        ("RPT-05", "asma.k", year, ("csv", "xlsx_ar"))):
            report, user = registry.get(rid), uid[name]
            definition = {"params": params, "sort": report.default_sort}
            for fmt in fmts:
                fd, path = tempfile.mkstemp(suffix=".csv" if fmt == "csv" else ".xlsx")
                os.close(fd)
                t = time.perf_counter()
                with engine.snapshot() as conn:
                    res = exports.generate(conn, report, definition, user, resolve_scope(user), privacy.FULL, fmt,
                                           path, batch=500, meta={"export_id": "perf", "requested_by": user})
                elapsed = time.perf_counter() - t
                size = os.path.getsize(path)
                os.remove(path)
                key = f"export {rid} {name} {fmt} {json.dumps(params, ensure_ascii=False) if params else ''}".strip()
                out[key] = {"seconds": round(elapsed, 2), "rows": res["row_count"], "bytes": size,
                            "rows_per_s": round(res["row_count"] / elapsed) if elapsed else None}
                print(key, out[key], flush=True)
    if as_json:
        with open(as_json, "w") as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
    return out


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python -m services.reports_v2_a_perf")
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
