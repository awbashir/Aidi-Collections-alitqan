"""Measurement harness for the bulk framework. NEVER COMMITS.

    docker exec -w /app cawp-api python -m core.bulk.perf --user <uuid> [--rows 50000] [--batches 10]

Everything runs on ONE database connection inside ONE transaction that is rolled back at
the end: migrations 0016-0019 are applied inside it, the Flask-SQLAlchemy session is bound
to it with `join_transaction_mode="create_savepoint"` (so the engine's commits only release
savepoints), and files go to a temporary directory that is deleted afterwards.

Steps: build an XLSX of real account numbers (+ duplicates + unknown accounts), inspect it
(upload path), validate it as a `lock` job (worker path), confirm, execute N batches of the
lock kind, report timings and memory, roll back.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import random
import resource
import shutil
import tempfile
import time
import uuid
from collections import defaultdict


def _rss_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0    # Linux: KiB


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python -m core.bulk.perf")
    p.add_argument("--user", required=True, help="acting user id (creator and confirmer)")
    p.add_argument("--rows", type=int, default=50_000)
    p.add_argument("--duplicates", type=int, default=1_000)
    p.add_argument("--unknown", type=int, default=1_000)
    p.add_argument("--batches", type=int, default=10, help="execution batches to run (0: none)")
    p.add_argument("--batch-size", type=int, default=500)
    p.add_argument("--migrations", default=os.getenv("CAWP_DB_DIR", "/db") + "/migrations")
    p.add_argument("--tracemalloc", action="store_true",
                   help="report the Python heap peak of inspection and validation (slower)")
    args = p.parse_args(argv)

    tmp = tempfile.mkdtemp(prefix="bulkperf-")
    os.environ["BULK_STORAGE_DIR"] = os.path.join(tmp, "store")
    os.environ["BULK_MAX_ROWS"] = str(max(args.rows + 10, 100_000))
    os.environ["BULK_BATCH_SIZE"] = str(args.batch_size)
    os.environ["BULK_LEASE_SECONDS"] = "3600"
    report: dict = {"rows": args.rows, "user": args.user}
    timings = defaultdict(float)

    from sqlalchemy import text
    from sqlalchemy.orm import scoped_session, sessionmaker

    from worker.main import create_app
    app = create_app()
    with app.app_context():
        from extensions import db
        conn = db.engine.connect()
        trans = conn.begin()
        real_session = db.session
        Session = scoped_session(sessionmaker(bind=conn, join_transaction_mode="create_savepoint"))
        db.session = Session
        try:
            conn.exec_driver_sql("SET LOCAL statement_timeout = 0")
            raw = conn.connection.dbapi_connection
            t = time.perf_counter()
            for f in sorted(glob.glob(os.path.join(args.migrations, "001[6-9]_*.sql"))):
                with raw.cursor() as cur:
                    cur.execute(open(f, encoding="utf-8").read())
            timings["migrations_0016_0019"] = time.perf_counter() - t

            # ---- the spreadsheet: real account numbers + duplicates + unknown ------------
            n_real = args.rows - args.duplicates - args.unknown
            t = time.perf_counter()
            accounts = [r[0] for r in conn.execute(text(
                'SELECT "accountNumber" FROM clints ORDER BY md5(id::text) LIMIT :n'), {"n": n_real})]
            timings["sample_accounts_sql"] = time.perf_counter() - t
            rnd = random.Random(20260923)
            values = accounts + rnd.sample(accounts, args.duplicates) + \
                [f"PERF-NOPE-{i}" for i in range(args.unknown)]
            rnd.shuffle(values)
            import openpyxl
            path = os.path.join(tmp, "accounts.xlsx")
            t = time.perf_counter()
            wb = openpyxl.Workbook(write_only=True)
            ws = wb.create_sheet("Accounts")
            ws.append(["Account No", "Note", "Amount"])
            for i, v in enumerate(values):
                ws.append([v, f"row {i}", i * 1.5])
            wb.save(path)
            timings["xlsx_generation_not_measured_path"] = time.perf_counter() - t
            report["file_bytes"] = os.path.getsize(path)
            report["rss_mb_before_inspection"] = round(_rss_mb(), 1)

            # ---- upload path: receive + detect + inspect ----------------------------------
            from core.bulk import engine, inspect, registry, sources, storage
            del values, accounts
            if args.tracemalloc:
                import tracemalloc
                tracemalloc.start()
            t = time.perf_counter()
            tmp_upload = storage.tmp_path()
            with open(path, "rb") as fh:
                size, sha = inspect.receive(fh, tmp_upload)
            detected = inspect.detect_type(tmp_upload, "accounts.xlsx")
            info = inspect.inspect_file(tmp_upload, detected)
            upload_id = str(uuid.uuid4())
            import datetime as _dt
            rel = storage.upload_relative(upload_id, detected, _dt.datetime.now(_dt.timezone.utc))
            storage.place(tmp_upload, rel)
            timings["upload_inspection"] = time.perf_counter() - t
            report["rss_mb_after_inspection"] = round(_rss_mb(), 1)
            if args.tracemalloc:
                report["heap_peak_mb_inspection"] = round(tracemalloc.get_traced_memory()[1] / 2**20, 1)
                tracemalloc.reset_peak()
            report["inspection"] = {"rows": info["row_count"], "formula_cells": info["formula_cells"],
                                    "headers": info["sheets"][0]["headers"]}
            s = Session()
            s.execute(text("""
                INSERT INTO bulk_uploads (id, purpose, original_name, stored_path, size_bytes, sha256, detected_type,
                                          sheets, row_count, header_row, formula_cells, uploaded_by, expires_at)
                VALUES (CAST(:id AS uuid), 'lock', 'accounts.xlsx', :p, :size, :sha, 'xlsx', CAST(:sheets AS jsonb),
                        :rows, 1, 0, CAST(:u AS uuid), now() + interval '1 day')
            """), {"id": upload_id, "p": rel, "size": size, "sha": sha, "sheets": json.dumps(info["sheets"]),
                   "rows": info["row_count"], "u": args.user})
            job_id = s.execute(text("""
                INSERT INTO bulk_jobs (kind, source, source_ref, upload_id, sheet, mapping, params, created_by)
                VALUES ('lock', 'spreadsheet', CAST(:ref AS jsonb), CAST(:up AS uuid), 'Accounts',
                        '{"account_number": "Account No"}', '{"reason": "perf measurement (rolled back)"}',
                        CAST(:u AS uuid))
                RETURNING id::text
            """), {"ref": json.dumps({"upload_id": upload_id, "sheet": "Accounts"}), "up": upload_id,
                   "u": args.user}).scalar()
            s.commit()

            # ---- instrumentation ----------------------------------------------------------
            def timed(obj, name, key):
                orig = getattr(obj, name)

                def wrapper(*a, **kw):
                    t0 = time.perf_counter()
                    try:
                        return orig(*a, **kw)
                    finally:
                        timings[key] += time.perf_counter() - t0
                setattr(obj, name, wrapper)
                return orig
            kind = registry.get("lock")
            restore = [(sources, "materialise", timed(sources, "materialise", "v.materialise_stream_insert")),
                       (engine, "_match", timed(engine, "_match", "v.match_scope_sql")),
                       (engine, "_mark_duplicates", timed(engine, "_mark_duplicates", "v.duplicates_sql")),
                       (engine, "_write_eligibility", timed(engine, "_write_eligibility", "v.write_eligibility")),
                       (engine.artifacts, "write", timed(engine.artifacts, "write", "v.artifacts_csv"))]
            orig_prepare = type(kind).prepare

            def prepare_timed(self, rows, ctx):
                t0 = time.perf_counter()
                try:
                    return orig_prepare(self, rows, ctx)
                finally:
                    timings["v.kind_prepare_python"] += time.perf_counter() - t0
            type(kind).prepare = prepare_timed

            # ---- validation (worker path) -------------------------------------------------
            t = time.perf_counter()
            job = engine.claim("perf-worker", job_id)
            status = engine.process(job, engine.RunControl("perf-worker"))
            timings["validation_total"] = time.perf_counter() - t
            report["rss_mb_after_validation"] = round(_rss_mb(), 1)
            if args.tracemalloc:
                report["heap_peak_mb_validation"] = round(tracemalloc.get_traced_memory()[1] / 2**20, 1)
                tracemalloc.stop()
            row = Session().execute(text("SELECT status, total_rows, eligible_rows, excluded_rows, error_rows "
                                         "FROM bulk_jobs WHERE id = CAST(:j AS uuid)"), {"j": job_id}).one()
            report["validation"] = {"status": status, "total": row[1], "eligible": row[2], "excluded": row[3],
                                    "error": row[4]}
            report["by_code"] = {f"{r[0]}:{r[1]}": r[2] for r in Session().execute(text("""
                SELECT eligibility, coalesce(eligibility_code, '-'), count(*) FROM bulk_job_rows
                 WHERE job_id = CAST(:j AS uuid) GROUP BY 1, 2 ORDER BY 1, 2"""), {"j": job_id})}
            type(kind).prepare = orig_prepare

            # ---- execution: N batches of lock -------------------------------------------
            if args.batches:
                Session().execute(text("""
                    UPDATE bulk_jobs SET status = 'confirmed', phase = 'execute', confirmed_by = CAST(:u AS uuid),
                           confirmed_at = now(), accept_partial = true WHERE id = CAST(:j AS uuid)
                """), {"u": args.user, "j": job_id})
                Session().commit()
                batch_times = []
                orig_commit = engine._commit_batch
                orig_run = engine._run_batch
                marks = {}

                def run_batch(*a, **kw):
                    marks["t0"] = time.perf_counter()
                    return orig_run(*a, **kw)

                def commit_batch(*a, **kw):
                    out = orig_commit(*a, **kw)
                    batch_times.append(time.perf_counter() - marks["t0"])
                    return out
                engine._run_batch, engine._commit_batch = run_batch, commit_batch
                t = time.perf_counter()
                job = engine.claim("perf-worker", job_id)
                status = engine.process(job, engine.RunControl("perf-worker", max_batches=args.batches))
                total = time.perf_counter() - t
                engine._run_batch, engine._commit_batch = orig_run, orig_commit
                done = Session().execute(text("SELECT count(*) FROM bulk_job_rows WHERE job_id = CAST(:j AS uuid) "
                                              "AND outcome = 'succeeded'"), {"j": job_id}).scalar()
                hist = Session().execute(text("""
                    SELECT count(*) FROM client_lock_history WHERE reason = 'perf measurement (rolled back)'
                """)).scalar()
                bt = sorted(batch_times)
                report["execution"] = {
                    "status_after": status, "batches": len(batch_times), "batch_size": args.batch_size,
                    "rows_succeeded": done, "lock_history_rows": hist,
                    "batch_ms_min": round(bt[0] * 1000, 1) if bt else None,
                    "batch_ms_median": round(bt[len(bt) // 2] * 1000, 1) if bt else None,
                    "batch_ms_max": round(bt[-1] * 1000, 1) if bt else None,
                    "rows_per_second": round(done / sum(batch_times), 0) if batch_times else None,
                    "total_s_incl_start": round(total, 2),
                }
            for obj, name, orig in restore:
                setattr(obj, name, orig)
        finally:
            db.session = real_session
            Session.remove()
            trans.rollback()
            conn.close()
            shutil.rmtree(tmp, ignore_errors=True)
    report["timings_s"] = {k: round(v, 3) for k, v in timings.items()}
    report["rss_mb_peak"] = round(_rss_mb(), 1)
    report["rolled_back"] = True
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
