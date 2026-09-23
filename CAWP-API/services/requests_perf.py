"""Stream A (Phase 3) measurement harness: EXPLAIN (ANALYZE, BUFFERS) of the request list,
counts, detail and the reported open_request attention set, on a large synthetic volume.

It NEVER commits. Everything runs in one transaction on one connection that is rolled
back at the end: migrations 0028-0030 (when not applied), the synthetic requests and
events, ANALYZE, and the EXPLAINs. Only new tables are written; legacy tables are read.

    docker exec cawp-api python -m services.requests_perf --out /tmp/requests_perf.txt \\
        --requests 200000 --user aidi.dev --user s.almalki --user sara.m --user Tamara
"""
from __future__ import annotations

import argparse
import os
import re
import statistics
import time
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.orm import Session

APPLY = ("0028", "0029", "0030")


def _app(url):
    from flask import Flask

    from extensions import db
    app = Flask("requests_perf")
    app.config.update(SQLALCHEMY_DATABASE_URI=url, SQLALCHEMY_TRACK_MODIFICATIONS=False)
    db.init_app(app)
    return app


class Harness:
    def __init__(self, conn, out):
        self.conn = conn
        self.raw = conn.connection.dbapi_connection
        self.out = out

    def log(self, *parts):
        line = " ".join(str(p) for p in parts)
        print(line, flush=True)
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

    def explain(self, label, stmt):
        compiled = stmt.compile(dialect=self.conn.dialect, compile_kwargs={"render_postcompile": True})
        rows = self.sql("EXPLAIN (ANALYZE, BUFFERS) " + str(compiled), compiled.params)
        plan = "\n".join(r[0] for r in rows)
        m = re.search(r"Execution Time: ([\d.]+) ms", plan)
        ms = float(m.group(1)) if m else None
        self.log(f"\n=== {label}: {ms} ms\n{plan}")
        return ms


def _median_ms(fn, n=5):
    xs = []
    for _ in range(n):
        t0 = time.perf_counter()
        fn()
        xs.append((time.perf_counter() - t0) * 1000)
    return round(statistics.median(xs), 1)


SYNTH = """
CREATE TEMP TABLE rq_cases ON COMMIT DROP AS
  SELECT row_number() OVER (ORDER BY id) AS rn, id, user_id
    FROM clints WHERE user_id IS NOT NULL AND is_archive = false;
CREATE TEMP TABLE rq_reviewers ON COMMIT DROP AS
  SELECT row_number() OVER (ORDER BY id) AS rn, id FROM users ORDER BY id LIMIT 12;
INSERT INTO requests (id, case_id, department, subject, body, amount, related_date, status, requested_by,
                      created_at, reviewer_id, review_started_at, resolved_by, resolved_at, resolution,
                      cancelled_by, cancelled_at, cancel_reason, reopen_count, last_event_at)
SELECT gen_random_uuid(), c.id,
       (ARRAY['legal','finance','operations'])[1 + (g % 3)],
       'synthetic subject ' || g, 'synthetic body ' || g,
       CASE WHEN g % 3 = 1 THEN round((random() * 50000)::numeric + 1, 2) END,
       CASE WHEN g % 5 = 0 THEN (now() + (g % 90) * interval '1 day')::date END,
       s.status, c.user_id, s.created_at,
       CASE WHEN s.status IN ('in_review','resolved') THEN rv.id END,
       CASE WHEN s.status IN ('in_review','resolved') THEN s.created_at + interval '2 hours' END,
       CASE WHEN s.status = 'resolved' THEN rv.id END,
       CASE WHEN s.status = 'resolved' THEN s.created_at + interval '2 days' END,
       CASE WHEN s.status = 'resolved' THEN 'synthetic resolution' END,
       CASE WHEN s.status = 'cancelled' THEN c.user_id END,
       CASE WHEN s.status = 'cancelled' THEN s.created_at + interval '1 hour' END,
       CASE WHEN s.status = 'cancelled' THEN 'synthetic cancel' END,
       0, s.created_at
  FROM (SELECT g, 1 + floor(random() * (SELECT count(*) FROM rq_cases))::int AS rn,
               now() - random() * interval '730 days' AS created_at,
               CASE WHEN g % 20 < 2 THEN 'open' WHEN g % 20 < 4 THEN 'in_review'
                    WHEN g % 20 < 6 THEN 'cancelled' ELSE 'resolved' END AS status
          FROM generate_series(1, %(n)s) g) s
  JOIN rq_cases c ON c.rn = s.rn
  JOIN LATERAL (SELECT r.id FROM rq_reviewers r WHERE r.id <> c.user_id
                 ORDER BY (r.rn + s.g) % 12 LIMIT 1) rv ON true;
INSERT INTO request_events (request_id, case_id, event, actor_user_id, occurred_at, status_before, status_after)
  SELECT id, case_id, 'created', requested_by, created_at, NULL, 'open' FROM requests;
INSERT INTO request_events (request_id, case_id, event, actor_user_id, occurred_at, status_before, status_after)
  SELECT id, case_id, 'taken', reviewer_id, review_started_at, 'open', 'in_review' FROM requests
   WHERE status IN ('in_review', 'resolved');
INSERT INTO request_events (request_id, case_id, event, actor_user_id, occurred_at, status_before, status_after, body)
  SELECT id, case_id, 'commented', requested_by, created_at + interval '1 day', status, status, 'synthetic comment'
    FROM requests WHERE status <> 'cancelled' AND abs(hashtext(id::text)) % 3 = 0;
INSERT INTO request_events (request_id, case_id, event, actor_user_id, occurred_at, status_before, status_after, reason)
  SELECT id, case_id, 'resolved', resolved_by, resolved_at, 'in_review', 'resolved', resolution FROM requests
   WHERE status = 'resolved';
INSERT INTO request_events (request_id, case_id, event, actor_user_id, occurred_at, status_before, status_after, reason)
  SELECT id, case_id, 'cancelled', cancelled_by, cancelled_at, 'open', 'cancelled', cancel_reason FROM requests
   WHERE status = 'cancelled';
"""


def run(args):
    from services import requests_attention as ra, requests_common as rc, requests_service as svc
    from services import worklist_common as wc
    from services.clients_scope import SUPER, Scope, resolve_scope

    url = args.database_url or os.environ["DATABASE_URL"]
    app = _app(url)
    engine = sa.create_engine(url)
    out = open(args.out, "w", encoding="utf-8")
    with app.app_context(), engine.connect() as conn:
        trans = conn.begin()
        h = Harness(conn, out)
        session = Session(bind=conn)
        try:
            h.sql("SET LOCAL statement_timeout = '600s'")
            applied = {r[0] for r in h.sql("SELECT version FROM schema_migrations")}
            for p in sorted((Path(args.db_dir) / "migrations").glob("*.sql")):
                v = p.name[:4]
                if v in APPLY and v not in applied:
                    ms, _ = h.timed(p.read_text(encoding="utf-8"))
                    h.log(f"applied {p.name} in {ms:.1f} ms (inside the rolled-back transaction)")
            ms, _ = h.timed(SYNTH.replace("%(n)s", str(int(args.requests))))
            n_req = h.sql("SELECT count(*) FROM requests")[0][0]
            n_ev = h.sql("SELECT count(*) FROM request_events")[0][0]
            h.log(f"synthetic: {n_req} requests, {n_ev} events in {ms:.0f} ms")
            ms, _ = h.timed("ANALYZE requests; ANALYZE request_events")
            h.log(f"analyze: {ms:.0f} ms")
            dist = h.sql("SELECT status, count(*) FROM requests GROUP BY 1 ORDER BY 1")
            h.log("status distribution:", dist)
            size = h.sql("SELECT pg_size_pretty(pg_total_relation_size('requests')), "
                         "pg_size_pretty(pg_total_relation_size('request_events'))")
            h.log("size requests / request_events:", size)

            users = dict(h.sql("SELECT user_name, id::text FROM users WHERE user_name = ANY(%s)",
                               (list(args.user),)))
            viewers = [("SuperAdmin (synthetic), requests + all departments",
                        rc.Viewer("00000000-0000-0000-0000-000000000000",
                                  Scope(SUPER, "00000000-0000-0000-0000-000000000000"), True, rc.DEPARTMENTS))]
            for name in args.user:
                uid = users.get(name)
                if uid is None:
                    h.log(f"user {name} not found; skipped")
                    continue
                sc = resolve_scope(uid)
                viewers.append((f"{name} ({sc.kind}), requests only", rc.Viewer(uid, sc, True, ())))
                viewers.append((f"{name} ({sc.kind}), legal reviewer only", rc.Viewer(uid, sc, False, ("legal",))))
                if sc.kind != "collector":
                    viewers.append((f"{name} ({sc.kind}), requests + all departments",
                                    rc.Viewer(uid, sc, True, rc.DEPARTMENTS)))
            summary = []
            sample = str(h.sql("SELECT id FROM requests WHERE status = 'resolved' LIMIT 1")[0][0])
            busiest_case = str(h.sql("SELECT case_id FROM requests GROUP BY 1 ORDER BY count(*) DESC LIMIT 1")[0][0])
            for label, v in viewers:
                h.log(f"\n##### {label}")
                vis = rc.visibility(session, v)
                row = {"viewer": label}
                flt = svc.ListFilter(v)
                row["counts"] = h.explain(f"{label}: counts (no filter)", svc.count_statement(flt, vis))
                row["page1"] = h.explain(f"{label}: page 1", svc.page_statement(flt, vis, 50, 0))
                total = session.execute(svc.count_statement(flt, vis)).mappings().one()["total"]
                row["visible"] = int(total)
                deep = max(0, (int(total) // 50 - 1) * 50)
                row["last_page"] = h.explain(f"{label}: last page (offset {deep})",
                                             svc.page_statement(flt, vis, 50, deep))
                f_open = svc.ListFilter(v, statuses=("open", "in_review"), departments=("legal",))
                row["counts_open_legal"] = h.explain(f"{label}: counts status=open,in_review dept=legal",
                                                     svc.count_statement(f_open, vis))
                row["page_open_legal"] = h.explain(f"{label}: page status=open,in_review dept=legal",
                                                   svc.page_statement(f_open, vis, 50, 0))
                f_me = svc.ListFilter(v, requester=v.identity)
                row["page_mine"] = h.explain(f"{label}: page requester=me", svc.page_statement(f_me, vis, 50, 0))
                ids = [str(r[0]) for r in session.execute(svc.page_statement(flt, vis, 50, 0))]
                row["hydrate50"] = h.explain(f"{label}: hydrate 50", svc.hydrate_statement(ids or [sample]))
                row["e2e_list"] = _median_ms(lambda: svc.list_requests(session, v, svc.ListFilter(v), page=1,
                                                                       page_size=50))
                row["e2e_list_open"] = _median_ms(lambda: svc.list_requests(
                    session, v, svc.ListFilter(v, statuses=("open",)), page=1, page_size=50))
                if v.departments:
                    cf = wc.CaseFilter(v.scope)
                    stmt = ra.count_statement(cf, None, v.departments)
                    row["attention_count"] = h.explain(f"{label}: attention open_request count", stmt)
                summary.append(row)

            # detail of one request, and the busiest case's section, as the super viewer
            _l, v = viewers[0]
            h.log(f"\ndetail e2e (super): {_median_ms(lambda: svc.detail(session, v, sample))} ms")
            h.explain("events of one request", sa.select(rc.EVENTS).where(rc.EVENTS.c.request_id == sample)
                      .order_by(rc.EVENTS.c.id))
            h.log(f"case section e2e (super, busiest case): "
                  f"{_median_ms(lambda: svc.list_requests(session, v, svc.ListFilter(v, case_id=busiest_case)))} ms")

            h.log("\n##### summary (execution ms; e2e = service call median of 5)")
            keys = ["visible", "counts", "page1", "last_page", "counts_open_legal", "page_open_legal", "page_mine",
                    "hydrate50", "e2e_list", "e2e_list_open", "attention_count"]
            h.log("| viewer | " + " | ".join(keys) + " |")
            for r in summary:
                h.log("| " + r["viewer"] + " | " + " | ".join(str(r.get(k, "")) for k in keys) + " |")
        finally:
            session.close()
            trans.rollback()
            h.log("\nrolled back")
    out.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--database-url")
    ap.add_argument("--db-dir", default=os.environ.get("CAWP_DB_DIR", "/db"))
    ap.add_argument("--out", default="/tmp/requests_perf.txt")
    ap.add_argument("--requests", type=int, default=200_000)
    ap.add_argument("--user", action="append", default=[])
    run(ap.parse_args())


if __name__ == "__main__":
    main()
