"""Stream B measurement harness: backfill timing, EXPLAIN (ANALYZE, BUFFERS) of every
follow-up / attention query per scope, end-to-end service timings and trigger overhead.

It NEVER commits. Everything runs in one transaction on one connection that is rolled back
at the end: the pending migrations (stream A's 0007-0009 for the real v_promise_state, and
this stream's 0010-0012; CONCURRENTLY is dropped because it cannot run in a transaction),
the benchmarks (each inside a savepoint that is rolled back) and the EXPLAINs.

    docker exec cawp-api python -m services.worklist_perf --out /tmp/worklist_perf.txt \\
        --user aidi.dev --user sara.m --user aryam.g --user Tamara --bench-rows 5000

While it runs it holds the CREATE TRIGGER locks of the migrations (SHARE ROW EXCLUSIVE on
"Comments", clints, "Payments", "paymentDates"): writers to those tables wait, readers do
not. Use it on a development copy, not on production.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
import time
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.orm import Session

APPLY = ("0007", "0008", "0009", "0010", "0011", "0012")


def _app(url):
    from flask import Flask

    from extensions import db
    app = Flask("worklist_perf")
    app.config.update(SQLALCHEMY_DATABASE_URI=url, SQLALCHEMY_TRACK_MODIFICATIONS=False)
    db.init_app(app)
    return app


class Harness:
    def __init__(self, conn, out):
        self.conn = conn
        self.raw = conn.connection.dbapi_connection
        self.out = out
        self.summary = {}

    def log(self, *parts):
        line = " ".join(str(p) for p in parts)
        print(line)
        self.out.write(line + "\n")
        self.out.flush()

    def sql(self, statement, params=None):
        with self.raw.cursor() as cur:
            cur.execute(statement, params) if params is not None else cur.execute(statement)
            try:
                return cur.fetchall()
            except Exception:  # noqa: BLE001 - no result set
                return None

    def timed(self, statement, params=None):
        t0 = time.perf_counter()
        rows = self.sql(statement, params)
        return (time.perf_counter() - t0) * 1000, rows

    # -- migrations ---------------------------------------------------------------------
    def apply_pending(self, db_dir: Path, versions=APPLY, only=None):
        applied = {r[0] for r in self.sql("SELECT version FROM schema_migrations")}
        out = {}
        for p in sorted((db_dir / "migrations").glob("*.sql")):
            v = p.name[:4]
            if v in applied or v not in versions or (only and v not in only):
                continue
            text = p.read_text(encoding="utf-8").replace("CREATE INDEX CONCURRENTLY", "CREATE INDEX")
            ms, _ = self.timed(text)
            out[p.name] = round(ms, 1)
            self.log(f"applied {p.name} in {ms:.1f} ms (inside the rolled-back transaction)")
        return out

    # -- EXPLAIN ------------------------------------------------------------------------
    def explain(self, label, stmt):
        compiled = stmt.compile(dialect=self.conn.dialect, compile_kwargs={"render_postcompile": True})
        rows = self.sql("EXPLAIN (ANALYZE, BUFFERS) " + str(compiled), compiled.params)
        plan = "\n".join(r[0] for r in rows)
        m = re.search(r"Execution Time: ([\d.]+) ms", plan)
        ms = float(m.group(1)) if m else None
        self.log(f"\n=== {label}: {ms} ms\n{plan}")
        return ms


class _Stop(Exception):
    pass


def _median_ms(fn, n=5):
    xs = []
    for _ in range(n):
        t0 = time.perf_counter()
        fn()
        xs.append((time.perf_counter() - t0) * 1000)
    return round(statistics.median(xs), 1)


def run(args):
    from services import worklist_attention as att, worklist_common as wc, worklist_follow_ups as fus
    from services.clients_scope import SUPER, Scope, resolve_scope

    url = args.database_url or os.environ["DATABASE_URL"]
    app = _app(url)
    engine = sa.create_engine(url)
    out = open(args.out, "w", encoding="utf-8")
    with app.app_context(), engine.connect() as conn:
        trans = conn.begin()
        h = Harness(conn, out)
        try:
            h.sql("SET LOCAL statement_timeout = '300s'")
            # take every lock this run needs up front (other sessions on a shared dev copy
            # otherwise interleave with the CREATE TRIGGERs below and deadlock)
            h.sql("SET LOCAL lock_timeout = '300s'")
            t0 = time.perf_counter()
            h.sql('LOCK TABLE "Comments", clints, "Payments", "paymentDates" IN SHARE ROW EXCLUSIVE MODE')
            h.log(f"locks acquired after {time.perf_counter() - t0:.1f} s")
            h.sql("SET LOCAL lock_timeout = '10s'")
            session = Session(bind=conn)

            # ---------------------------------------------------------- stream A first
            h.summary["migrations_a"] = h.apply_pending(Path(args.db_dir), only=("0007", "0008", "0009"))

            # ---------------------------------------------------------- trigger overhead: legacy only
            bench = None
            if not args.skip_bench:
                bench = Bench(h, args.bench_rows)
                bench.prepare()
                h.summary["bench_before"] = bench.run_all(with_stream_b=False)

            # ---------------------------------------------------------- stream B
            h.summary["migrations_b"] = h.apply_pending(Path(args.db_dir), only=("0010", "0011", "0012"))
            h.summary["backfill"] = dict(zip(
                ("follow_ups", "late", "today", "upcoming", "owners", "events"),
                h.sql("""SELECT count(*),
                                count(*) FILTER (WHERE worklist_riyadh_date(due_at) < worklist_riyadh_date(now())),
                                count(*) FILTER (WHERE worklist_riyadh_date(due_at) = worklist_riyadh_date(now())),
                                count(*) FILTER (WHERE worklist_riyadh_date(due_at) > worklist_riyadh_date(now())),
                                count(DISTINCT owner_user_id),
                                (SELECT count(*) FROM follow_up_events)
                           FROM follow_ups""")[0]))
            h.log("backfill:", h.summary["backfill"])
            if bench is not None:
                h.summary["bench_after"] = bench.run_all(with_stream_b=True)
            if args.skip_explain:
                raise _Stop
            h.sql("ANALYZE clints")

            # ---------------------------------------------------------- EXPLAIN per scope
            users = dict(h.sql("SELECT user_name, id::text FROM users WHERE user_name = ANY(%s)", (args.user,)))
            scopes = [("SuperAdmin (unrestricted)", Scope(SUPER, users.get(args.user[0], "x")))]
            for name in args.user:
                if name in users:
                    s = resolve_scope(users[name])
                    scopes.append((f"{name} ({s.kind}, {len(s.user_ids or [])} users, "
                                   f"{len(s.contract_ids or [])} contracts)", s))
            day = wc.Day()
            results = {}
            for label, scope in scopes:
                flt = wc.CaseFilter(scope)
                r = results[label] = {}
                r["follow_ups.counts"] = h.explain(f"{label} | follow-ups counts", fus.count_statement(flt, day))
                r["follow_ups.page(all)"] = h.explain(f"{label} | follow-ups page 1 (all buckets)",
                                                      fus.page_statement(flt, day, None, 50, 0))
                r["follow_ups.page(late)"] = h.explain(f"{label} | follow-ups page 1 (late)",
                                                       fus.page_statement(flt, day, "late", 50, 0))
                stmts = att.count_statements(flt, day)
                for k, stmt in stmts.items():
                    r[f"count.{k}"] = h.explain(f"{label} | attention count {k}", stmt)
                combined = sa.select(*(s.scalar_subquery().label(k) for k, s in stmts.items()))
                r["counts.combined"] = h.explain(f"{label} | attention counts, combined statement", combined)
                c = att.counts(session, flt, day)
                r["_counts"] = c
                r["tier1.page1"] = h.explain(f"{label} | attention tier 1 page 1", att.tier1_statement(flt, day).limit(50))
                r["tier2.page1"] = h.explain(f"{label} | attention tier 2 (unscheduled) page 1",
                                             att.tier2_statement(flt, exclude_pending=True).limit(50))
                deep = max(0, c["unscheduled"] - 50)
                r["tier2.last_page"] = h.explain(f"{label} | attention tier 2 last page (offset {deep})",
                                                 att.tier2_statement(flt, exclude_pending=True).limit(50).offset(deep))
                for reason in att.REASONS[:4]:
                    r[f"reason_page.{reason}"] = h.explain(f"{label} | attention reason={reason} page 1",
                                                           att.tier1_statement(flt, day, (reason,)).limit(50))
                # end to end (Python + all statements of one request), median of 5
                r["e2e.attention.page1"] = _median_ms(lambda: att.attention(session, flt, page=1, page_size=50))
                r["e2e.attention.unscheduled.page1"] = _median_ms(
                    lambda: att.attention(session, flt, reason="unscheduled", page=1, page_size=50))
                r["e2e.follow_ups.page1"] = _median_ms(lambda: fus.list_follow_ups(session, flt, page=1, page_size=50))
                h.log(f"\n### {label}: counts {c}")
                h.log(json.dumps({k: v for k, v in r.items() if k != "_counts"}, indent=1))
            h.summary["explain"] = results
        except _Stop:
            pass
        finally:
            trans.rollback()
            h.log("\nROLLED BACK - nothing was committed")
    out.write("\nSUMMARY\n" + json.dumps(h.summary, indent=1, default=str) + "\n")
    out.close()
    print(json.dumps(h.summary, indent=1, default=str))


class Bench:
    """Server-side cost of legacy note inserts and case reassignments, before and after the
    stream B triggers. Each measurement runs in a savepoint that is rolled back."""

    def __init__(self, h: Harness, n: int):
        self.h, self.n = h, n

    def prepare(self):
        h = self.h
        # live, assigned cases: half without any callback history (create path), and, after
        # 0011, the backfilled ones have an open follow-up (supersede path)
        h.sql("""CREATE TEMP TABLE wl_bench_cases ON COMMIT DROP AS
                 SELECT c.id, c.user_id FROM clints c
                  WHERE c.is_archive = false AND c.user_id IS NOT NULL
                    AND NOT EXISTS (SELECT 1 FROM "Comments" m WHERE m.clint_id = c.id AND m.next_contact_date IS NOT NULL)
                  ORDER BY c.id LIMIT %s""", (self.n,))
        h.sql("""CREATE TEMP TABLE wl_bench_cb_cases ON COMMIT DROP AS
                 SELECT DISTINCT ON (m.clint_id) m.clint_id AS id, c.user_id FROM "Comments" m
                   JOIN clints c ON c.id = m.clint_id
                  WHERE m.next_contact_date IS NOT NULL AND c.is_archive = false AND c.user_id IS NOT NULL
                  ORDER BY m.clint_id LIMIT %s""", (self.n,))
        self.status, self.method = h.sql('SELECT (SELECT id FROM "CommentStatuses" LIMIT 1), '
                                         '(SELECT id FROM "ContactMethods" LIMIT 1)')[0]
        self.other_user = h.sql("SELECT id FROM users ORDER BY created_at LIMIT 1")[0][0]
        self.sizes = h.sql("SELECT (SELECT count(*) FROM wl_bench_cases), (SELECT count(*) FROM wl_bench_cb_cases)")[0]
        h.log(f"bench: {self.sizes[0]} cases without callbacks, {self.sizes[1]} cases with callback history")

    def _savepoint(self, label, statement, params=None, repeat=3):
        xs = []
        for _ in range(repeat):
            self.h.sql("SAVEPOINT wl_bench")
            ms, _ = self.h.timed(statement, params)
            xs.append(ms)
            self.h.sql("ROLLBACK TO SAVEPOINT wl_bench")
        med = statistics.median(xs)
        self.h.log(f"bench {label}: median {med:.1f} ms over {repeat} runs {['%.1f' % x for x in xs]}")
        return round(med, 1)

    def _notes(self, table, callback: bool):
        ncd = "now() + interval '3 days'" if callback else "NULL"
        return (f'INSERT INTO "Comments" (clint_id, user_id, "contactMethod_id", comment, comment_status_id, '
                f"next_contact_date) SELECT id, coalesce(user_id, %s), %s, 'bench', %s, {ncd} FROM {table}",
                (self.other_user, self.method, self.status))

    def run_all(self, with_stream_b: bool):
        tag = "with stream B triggers" if with_stream_b else "legacy triggers only"
        out = {}
        s, p = self._notes("wl_bench_cases", True)
        out["callback_notes_new_cases"] = self._savepoint(f"{self.sizes[0]} callback notes, fresh cases ({tag})", s, p)
        s, p = self._notes("wl_bench_cb_cases", True)
        out["callback_notes_cases_with_history"] = self._savepoint(
            f"{self.sizes[1]} callback notes, cases with an (imported) open follow-up ({tag})", s, p)
        s, p = self._notes("wl_bench_cases", False)
        out["plain_notes"] = self._savepoint(f"{self.sizes[0]} plain notes ({tag})", s, p)
        out["reassign_cases_with_history"] = self._savepoint(
            f"reassign {self.sizes[1]} cases with callback history ({tag})",
            "UPDATE clints c SET user_id = %s FROM wl_bench_cb_cases b WHERE c.id = b.id", (self.other_user,))
        out["reassign_cases_no_history"] = self._savepoint(
            f"reassign {self.sizes[0]} cases without follow-ups ({tag})",
            "UPDATE clints c SET user_id = %s FROM wl_bench_cases b WHERE c.id = b.id", (self.other_user,))
        return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--database-url")
    ap.add_argument("--db-dir", default=os.environ.get("CAWP_DB_DIR", "/db"))
    ap.add_argument("--out", default="/tmp/worklist_perf.txt")
    ap.add_argument("--user", action="append", default=[], help="user_name to measure (repeatable)")
    ap.add_argument("--bench-rows", type=int, default=5000)
    ap.add_argument("--skip-bench", action="store_true",
                    help="no trigger benchmark (its rolled-back rows bloat the tables and skew the EXPLAINs)")
    ap.add_argument("--skip-explain", action="store_true", help="only the trigger benchmark")
    args = ap.parse_args(argv)
    if not args.user:
        args.user = ["aidi.dev"]
    run(args)


if __name__ == "__main__":
    sys.exit(main())
