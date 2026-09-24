"""Measurement and reconciliation harness for the v2 reports (never writes).

    docker exec -w /app cawp-api python -m core.reports.perf reconcile [--json out.json]
    docker exec -w /app cawp-api python -m core.reports.perf timings [--export-user <uuid>]

Every connection is opened with `default_transaction_read_only=on`, and the v2 queries run in
the framework's REPEATABLE READ READ ONLY snapshot, so nothing can be written to the database.

`reconcile` runs the LEGACY endpoints (`api/reports.py`: /payments, /payments/approval_status,
/payments/user) in-process with a token for each actor, rebuilds their row sets with an SQL
replica of their predicates (the replica's count must equal the endpoint's `countRow`), runs
the v2 report for the same window, and explains every row that differs:

    date_first_day   civil date = window start, stored at Riyadh midnight (21:00Z the eve):
                     the legacy `between '<start> 00:00Z' and '<end> 00:00Z'` misses it
    date_after_end   stored after '<end> 00:00Z' but still on the last civil day (legacy misses it)
    reversed         a reversed payment (legacy hides it: is_deleted); v2 RPT-06 lists it as reversed
    scope_recorder   legacy includes it through the recorder (supervised users / self); the case is
                     not in the actor's current case scope
    scope_case       v2 includes it through the current case scope (owner in the supervised users);
                     the legacy recorder/contract rule does not
    other            anything else (must be 0)

`timings` measures the preview (page 1 + exact totals, one snapshot), the selector options and
a full export of RPT-06 for a year in CSV, XLSX and Arabic XLSX (written to /tmp and deleted).
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
import tracemalloc
from decimal import Decimal

from sqlalchemy import text

ACTORS_06 = {  # user_name -> why
    "aidi.dev": "ADMIN, supervisor of all 15 contracts",
    "maher.j": "project manager, supervisor of 12 contracts",
    "bader.b": "supervisor (مشرف2) of 3 contracts, owns 149 cases",
    "marwan.a": "supervisor (مشرف2) of 1 contract + 1 user, owns 4,642 cases",
    "qasim.s": "supervisor (مشرف2) of 0 contracts + 1 user, owns 916 cases",
}
ACTORS_07 = {
    "rasha.s": "collector, most payments recorded in 2025-26 by a collector",
    "ghofran.b": "collector",
    "tabby.1": "processing role (معالجة), 3,581 payments recorded",
    "marwan.a": "supervisor who also owns cases",
}
WINDOWS = (("2025-01-01", "2025-12-31"), ("2025-09-01", "2026-08-31"), ("2026-08-01", "2026-08-31"),
           ("2026-07-01", "2026-07-31"),    # 4,460 rows stored at Riyadh midnight (21:00Z) in July 2026
           ("2026-07-27", "2026-07-27"),    # one day: 645 of them
           ("2026-09-01", "2026-09-30"))    # the 29 reversed payments


def make_app():
    from flask import Flask
    from flask_jwt_extended import JWTManager

    from core.config import Config
    from extensions import db

    app = Flask("cawp-report-perf")
    app.config.from_object(Config)
    opts = dict(Config.SQLALCHEMY_ENGINE_OPTIONS)
    ca = dict(opts.get("connect_args") or {})
    ca["options"] = (ca.get("options", "") + " -c default_transaction_read_only=on").strip()
    ca["application_name"] = "cawp-report-perf"
    opts["connect_args"] = ca
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = opts
    db.init_app(app)
    JWTManager(app)
    import api.reports
    app.register_blueprint(api.reports.bp)
    return app


def user_ids(names):
    from extensions import db
    rows = db.session.execute(text("SELECT user_name, id::text FROM users WHERE user_name = ANY(:n)"),
                              {"n": list(names)}).all()
    db.session.rollback()
    return dict(rows)


# --------------------------------------------------------------------------- legacy side
def legacy_call(app, path, user_id, start, end):
    from flask_jwt_extended import create_access_token
    with app.app_context():
        token = create_access_token(identity=user_id)
    r = app.test_client().get(path, headers={"Authorization": f"Bearer {token}"},
                              query_string=[("customeFilterMoveDateValue[]", start),
                                            ("customeFilterMoveDateValue[]", end),
                                            ("page", "1"), ("pageSize", "1")])
    body = r.get_json()
    if r.status_code != 200 or not body or "countRow" not in body:
        raise RuntimeError(f"{path} -> {r.status_code} {str(body)[:300]}")
    return body


def legacy_ids(conn, report_id, user_id, start, end):
    """SQL replica of the legacy predicates (api/reports.py + services/report_scope.py)."""
    from extensions import db
    from services.report_scope import supervisor_scope
    lo = _dt.datetime.fromisoformat(start).replace(tzinfo=_dt.timezone.utc)
    hi = _dt.datetime.fromisoformat(end).replace(tzinfo=_dt.timezone.utc)
    where = ["p.is_deleted = false", "p.payment_date BETWEEN :lo AND :hi"]
    params = {"lo": lo, "hi": hi, "me": user_id}
    if report_id == "RPT-07":
        where += ["p.user_id = CAST(:me AS uuid)", "p.approval_status = true"]
    else:
        sc = supervisor_scope(user_id)
        db.session.rollback()
        if not sc.is_supervisor:
            where.append("p.user_id = CAST(:me AS uuid)")
        elif sc.contract_ids:
            where.append("(p.user_id = ANY(CAST(:uids AS uuid[])) OR pf.contract_id = ANY(CAST(:cids AS uuid[])))")
            params.update(uids=[str(u) for u in sc.user_ids], cids=[str(c) for c in sc.contract_ids])
        else:
            where.append("p.user_id = ANY(CAST(:uids AS uuid[]))")
            params["uids"] = [str(u) for u in sc.user_ids]
        if report_id == "RPT-08":
            where.append("p.approval_status = true")
    sql = f"""SELECT p.id::text FROM "Payments" p JOIN clints c ON c.id = p.clint_id
              LEFT JOIN portfolios pf ON pf.id = c.portfolios_id WHERE {' AND '.join(where)}"""
    return {r[0] for r in conn.execute(text(sql), params)}


# --------------------------------------------------------------------------- v2 side
def v2_ids_and_totals(conn, report, user_id, start, end):
    from core.reports import engine
    from services.clients_scope import resolve_scope
    filters = engine.normalize_filters(report, {"payment_date_from": start, "payment_date_to": end})
    ctx = engine.make_context(report, user_id, resolve_scope(user_id), filters)
    from_sql, where_sql, params = engine.compose(report, ctx)
    ids = {r[0] for r in conn.execute(text(f"SELECT p.id::text FROM {from_sql} WHERE {where_sql}"), params)}
    tot = conn.execute(text(engine.totals_sql(report, from_sql, where_sql)), params).mappings().one()
    return ids, engine.format_totals(report, tot)


def categorize(conn, only_legacy, only_v2, user_id, start, end):
    """-> {category: {"n": int, "amount": Decimal}} for both directions."""
    from core.reports import engine
    from services.clients_scope import resolve_scope
    scope_pred, sparams = engine.scope_sql(resolve_scope(user_id), "c")
    lo_leg = _dt.datetime.fromisoformat(start).replace(tzinfo=_dt.timezone.utc)
    hi_leg = _dt.datetime.fromisoformat(end).replace(tzinfo=_dt.timezone.utc)
    rows = conn.execute(text(f"""
        SELECT p.id::text, round(p.amount::numeric, 2) AS amount,
               (p.payment_date AT TIME ZONE 'Asia/Riyadh')::date AS civil,
               p.payment_date, EXISTS (SELECT 1 FROM payment_reversals r WHERE r.payment_id = p.id) AS reversed,
               ({scope_pred}) AS in_scope
          FROM "Payments" p JOIN clints c ON c.id = p.clint_id
         WHERE p.id = ANY(CAST(:ids AS uuid[]))
    """), {**sparams, "ids": sorted(only_legacy | only_v2)}).mappings().all()
    s, e = _dt.date.fromisoformat(start), _dt.date.fromisoformat(end)
    out = {}
    for r in rows:
        if r["id"] in only_v2:
            if r["reversed"]:
                cat = "v2_only:reversed"
            elif r["civil"] == s and r["payment_date"] < lo_leg:
                cat = "v2_only:date_first_day"
            elif r["payment_date"] > hi_leg and r["civil"] <= e:
                cat = "v2_only:date_after_end"
            elif r["in_scope"]:
                cat = "v2_only:scope_case"
            else:
                cat = "v2_only:other"
        else:
            if not r["in_scope"]:
                cat = "legacy_only:scope_recorder"
            elif not (s <= r["civil"] <= e):
                cat = "legacy_only:date"
            else:
                cat = "legacy_only:other"
        c = out.setdefault(cat, {"n": 0, "amount": Decimal("0")})
        c["n"] += 1
        c["amount"] += r["amount"]
    return out


def reconcile(app, as_json=None):
    from core.reports import engine, registry
    names = set(ACTORS_06) | set(ACTORS_07)
    results = []
    with app.app_context():
        uid = user_ids(names)
        plan = [("RPT-06", "/payments", n) for n in ACTORS_06] + \
               [("RPT-08", "/payments/approval_status", n) for n in ACTORS_06] + \
               [("RPT-07", "/payments/user", n) for n in ACTORS_07]
        for start, end in WINDOWS:
            for rid, path, name in plan:
                user = uid[name]
                legacy = legacy_call(app, path, user, start, end)
                report = registry.get(rid)
                with engine.snapshot() as conn:
                    rep_ids = legacy_ids(conn, rid, user, start, end)
                    v2_ids, totals = v2_ids_and_totals(conn, report, user, start, end)
                    only_l, only_v = rep_ids - v2_ids, v2_ids - rep_ids
                    cats = categorize(conn, only_l, only_v, user, start, end) if (only_l or only_v) else {}
                    legacy_sum = conn.execute(text(
                        'SELECT coalesce(sum(round(amount::numeric, 2)), 0) FROM "Payments" WHERE id = ANY(CAST(:i AS uuid[]))'),
                        {"i": sorted(rep_ids)}).scalar()
                row = {
                    "report": rid, "legacy_endpoint": path, "actor": name, "window": f"{start}..{end}",
                    "legacy_count": legacy["countRow"], "legacy_total_amount": legacy.get("totalAmount"),
                    "replica_count": len(rep_ids), "replica_matches": len(rep_ids) == legacy["countRow"],
                    "legacy_sum_exact": str(legacy_sum),
                    "v2_count": totals["count"], "v2_recorded": totals["recorded_amount"],
                    "v2_approved": totals["approved_amount"], "v2_totals": totals,
                    "only_legacy": len(only_l), "only_v2": len(only_v),
                    "categories": {k: {"n": v["n"], "amount": str(v["amount"])} for k, v in sorted(cats.items())},
                }
                results.append(row)
                print(json.dumps(row, ensure_ascii=False), flush=True)
    if as_json:
        with open(as_json, "w") as f:
            json.dump(results, f, ensure_ascii=False, indent=1)
    return results


# --------------------------------------------------------------------------- timings
def _median_ms(fn, n=5):
    samples = []
    for _ in range(n):
        t = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - t) * 1000)
    return round(statistics.median(samples), 1), round(min(samples), 1), round(max(samples), 1)


def timings(app, export_user=None):
    from core.reports import engine, exports, options, privacy, registry
    from services.clients_scope import resolve_scope
    out = {}
    start, end = "2025-09-01", "2026-08-31"
    with app.app_context():
        uid = user_ids(set(ACTORS_06) | set(ACTORS_07))
        cases = [("RPT-06", "aidi.dev", {}), ("RPT-06", "bader.b", {}), ("RPT-06", "qasim.s", {}),
                 ("RPT-08", "aidi.dev", {}), ("RPT-07", "rasha.s", {}),
                 ("RPT-06", "aidi.dev", {"q": "محمد"}), ("RPT-06", "aidi.dev", {"sort": "customer_name"}),
                 ("RPT-06", "aidi.dev", {"sort": "-amount", "page": "200"})]
        for rid, name, extra in cases:
            report = registry.get(rid)
            user = uid[name]
            scope = resolve_scope(user)
            args = {"payment_date_from": start, "payment_date_to": end, **extra}
            res = {}

            def run():
                res["d"] = engine.preview(report, args, user, scope, privacy.FULL)

            med, lo, hi = _median_ms(run)
            key = f"{rid} {name} {json.dumps(extra, ensure_ascii=False) if extra else ''}".strip()
            out[key] = {"preview_ms_median": med, "min": lo, "max": hi, "rows": res["d"]["total"],
                        "approved_amount": res["d"]["totals"]["approved_amount"]}
            print(key, out[key], flush=True)
        for field in ("creator", "contract", "portfolio", "creator_branch"):
            report = registry.get("RPT-06")
            user = uid["aidi.dev"]
            res = {}

            def run_opt():
                res["d"] = options.options(report, field, {"report": "RPT-06"}, user, resolve_scope(user))

            med, lo, hi = _median_ms(run_opt)
            out[f"options {field} aidi.dev"] = {"ms_median": med, "min": lo, "max": hi, "total": res["d"]["total"]}
            print(field, out[f"options {field} aidi.dev"], flush=True)

        # full export of RPT-06 for a year (the snapshot + cursor + writer; no report_exports row)
        user = export_user or uid["aidi.dev"]
        report = registry.get("RPT-06")
        definition = {"params": {"payment_date_from": start, "payment_date_to": end}, "sort": "-created_at"}
        for fmt in ("csv", "xlsx", "xlsx_ar"):
            runs = {}
            for traced in (False, True):          # wall time without tracemalloc; heap peak with it
                fd, path = tempfile.mkstemp(suffix="." + ("csv" if fmt == "csv" else "xlsx"))
                os.close(fd)
                batches = []
                if traced:
                    tracemalloc.start()
                t = time.perf_counter()
                with engine.snapshot() as conn:
                    res = exports.generate(conn, report, definition, user, resolve_scope(user), privacy.FULL, fmt,
                                           path, batch=500, on_batch=lambda n, x: batches.append(time.perf_counter()),
                                           meta={"export_id": "perf", "requested_by": user})
                elapsed = time.perf_counter() - t
                peak = tracemalloc.get_traced_memory()[1] if traced else None
                if traced:
                    tracemalloc.stop()
                size = os.path.getsize(path)
                os.remove(path)
                gaps = [b - a for a, b in zip(batches[1:], batches[2:])]
                runs[traced] = (elapsed, peak, size, res, gaps)
            elapsed, _, size, res, gaps = runs[False]
            out[f"export RPT-06 {fmt}"] = {"seconds": round(elapsed, 2), "rows": res["row_count"], "bytes": size,
                                           "python_peak_mb": round(runs[True][1] / 1e6, 1),
                                           "batch_ms_median": round(statistics.median(gaps) * 1000, 1) if gaps else None,
                                           "rows_per_s": round(res["row_count"] / elapsed),
                                           "approved_amount": res["totals"]["approved_amount"]}
            print(fmt, out[f"export RPT-06 {fmt}"], flush=True)
    return out


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python -m core.reports.perf")
    p.add_argument("what", choices=("reconcile", "timings"))
    p.add_argument("--json")
    p.add_argument("--export-user")
    a = p.parse_args(argv)
    app = make_app()
    if a.what == "reconcile":
        reconcile(app, a.json)
    else:
        r = timings(app, a.export_user)
        if a.json:
            with open(a.json, "w") as f:
                json.dump(r, f, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
