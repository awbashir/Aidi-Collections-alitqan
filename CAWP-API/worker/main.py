"""The bulk-job and report-export worker: `python -m worker` (docker-compose service `worker`).

Loop:
    1. every BULK_RETENTION_INTERVAL_SECONDS: the retention task (core/bulk/retention.py);
    2. recover jobs and report exports whose lease expired (crash / restart of another
       worker) -> retrying/failed;
    3. claim the oldest runnable bulk job (queued, confirmed, retrying) or report export
       (queued, retrying) with FOR UPDATE SKIP LOCKED. The two queues alternate which is
       tried first after each piece of work, so neither starves the other. Report exports
       are skipped while their table (migration 0039-0040) does not exist yet;
    4. process it (bulk: validation or execution; export: snapshot, write, publish) while a
       heartbeat thread renews the lease every BULK_LEASE_SECONDS / 3 on its own connection;
       if the lease is lost, the engine stops before its next commit;
    5. idle: sleep BULK_WORKER_POLL_SECONDS.

SIGTERM / SIGINT: the current batch (one transaction) finishes, the job is released as
`retrying` (the attempt is recorded as `interrupted` and does not count toward max_attempts),
and the process exits 0. Give the container a stop grace period longer than one batch.

Health: the loop touches BULK_WORKER_HEALTH_FILE (JSON: pid, worker id, time, current job)
between batches and while idle. `python -m worker --healthcheck` exits 0 while the file is
younger than BULK_WORKER_HEALTH_MAX_AGE seconds and the database answers, else 1.

Several workers may run side by side (replicas): claims never overlap (SKIP LOCKED) and a
batch commit requires the lease.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import socket
import sys
import threading
import time
import uuid

log = logging.getLogger("cawp.worker")

TARGET_SYNC_SECONDS = int(os.getenv("TARGET_SYNC_SECONDS", "3600"))


def create_app():
    """A minimal Flask app (config + SQLAlchemy only), like the test harness; not app.py."""
    from flask import Flask

    from core.config import Config
    from extensions import db

    app = Flask("cawp-worker")
    app.config.from_object(Config)
    opts = dict(Config.SQLALCHEMY_ENGINE_OPTIONS)
    opts["pool_size"] = int(os.getenv("WORKER_DB_POOL_SIZE", "4"))
    opts["max_overflow"] = 2
    connect_args = dict(opts.get("connect_args") or {})
    connect_args["application_name"] = "cawp-worker"
    opts["connect_args"] = connect_args
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = opts
    db.init_app(app)
    return app


class Heartbeat(threading.Thread):
    """Renews the lease of one job (or report export: `renew`) on its own connection until stopped."""

    def __init__(self, app, job_id, worker_id, interval: float, renew=None, what: str = "bulk job"):
        super().__init__(name=f"heartbeat-{str(job_id)[:8]}", daemon=True)
        self.app, self.job_id, self.worker_id, self.interval = app, job_id, worker_id, interval
        self.renew, self.what = renew, what
        self.lost = threading.Event()
        self._halt = threading.Event()

    def run(self):
        from core.bulk import engine
        from extensions import db
        renew = self.renew or engine.renew_lease
        with self.app.app_context():
            while not self._halt.wait(self.interval):
                try:
                    with db.engine.begin() as conn:
                        if not renew(conn, self.job_id, self.worker_id):
                            log.warning("%s %s: lease lost", self.what, self.job_id)
                            self.lost.set()
                            return
                except Exception:  # noqa: BLE001 - a DB blip: the next beat retries
                    log.exception("heartbeat for %s %s failed", self.what, self.job_id)

    def stop(self):
        self._halt.set()
        self.join(timeout=5)


class Worker:
    def __init__(self, app, worker_id: str | None = None):
        from core.bulk import settings
        self.app = app
        self.id = worker_id or f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:6]}"
        self.stop = threading.Event()
        self.settings = settings.get()
        self.current_job = None
        self.exports_first = False          # alternates after each piece of work (fairness)

    # -- health -------------------------------------------------------------------------
    def touch(self):
        path = self.settings.health_file
        try:
            tmp = path + ".tmp"
            with open(tmp, "w") as f:
                json.dump({"pid": os.getpid(), "worker_id": self.id, "at": time.time(),
                           "job_id": self.current_job}, f)
            os.replace(tmp, path)
        except OSError:
            log.exception("cannot write the health file %s", path)

    # -- loop ---------------------------------------------------------------------------
    def install_signals(self):
        def handler(signum, _frame):
            log.info("signal %s: finishing the current batch, then exiting", signum)
            self.stop.set()
        signal.signal(signal.SIGTERM, handler)
        signal.signal(signal.SIGINT, handler)

    def _sync_targets(self):
        from extensions import db
        from services import targets
        try:
            changed = targets.sync_legacy_columns(db.session)
            db.session.commit()
            if changed:
                log.info("target sync: %d legacy target column(s) updated for the new month", len(changed))
        except Exception:  # noqa: BLE001 - e.g. migration 0034 not applied yet; never stop the worker
            db.session.rollback()
            log.exception("target sync failed")

    def run_forever(self) -> int:
        from core.bulk import engine, registry, retention
        from extensions import db

        with self.app.app_context():
            registry.discover()
            log.info("worker %s started; kinds: %s", self.id, ", ".join(k.key for k in registry.all_kinds()))
            next_retention = 0.0
            next_target_sync = 0.0
            while not self.stop.is_set():
                self.touch()
                try:
                    if time.monotonic() >= next_retention:
                        retention.run()
                        next_retention = time.monotonic() + self.settings.retention_interval_seconds
                    if time.monotonic() >= next_target_sync:
                        # Phase 3: a target set in advance reaches the legacy dashboard columns
                        # when its month starts (idempotent; hourly, so within an hour of rollover)
                        self._sync_targets()
                        next_target_sync = time.monotonic() + TARGET_SYNC_SECONDS
                    engine.recover_expired()
                    kind, work = self.next_work()
                except Exception:  # noqa: BLE001 - DB down / restarting: keep the process alive
                    log.exception("worker loop error; retrying in %ss", self.settings.poll_seconds)
                    db.session.rollback()
                    db.session.remove()
                    engine.sleep_until(time.monotonic() + self.settings.poll_seconds * 5, self.stop.is_set)
                    continue
                if work is None:
                    db.session.remove()
                    engine.sleep_until(time.monotonic() + self.settings.poll_seconds, self.stop.is_set)
                    continue
                self.exports_first = kind != "export"
                if kind == "export":
                    self.run_export(work)
                else:
                    self.run_job(work)
            log.info("worker %s stopped", self.id)
        return 0

    def claim_export(self):
        """Recover expired export leases and claim one export; None while 0039 is not applied."""
        from core.reports import exports
        from extensions import db
        try:
            if not exports.available():
                return None
            exports.recover_expired()
            return exports.claim(self.id)
        except Exception:  # noqa: BLE001 - never let the export queue stop the bulk queue
            log.exception("report export claim failed")
            db.session.rollback()
            return None

    def next_work(self):
        """("bulk", job) | ("export", export) | (None, None)."""
        from core.bulk import engine
        if self.exports_first:
            exp = self.claim_export()
            if exp is not None:
                return "export", exp
            job = engine.claim(self.id)
            return ("bulk", job) if job is not None else (None, None)
        job = engine.claim(self.id)
        if job is not None:
            return "bulk", job
        exp = self.claim_export()
        return ("export", exp) if exp is not None else (None, None)

    def run_export(self, exp) -> str:
        from core.reports import exports
        from extensions import db
        self.current_job = f"export:{exp['id']}"
        hb = Heartbeat(self.app, exp["id"], self.id, max(1.0, self.settings.lease_seconds / 3),
                       renew=exports.renew_lease, what="report export")
        hb.start()
        with self.app.app_context():
            try:
                return exports.process(exp, exports.Control(
                    worker_id=self.id, should_stop=self.stop.is_set, lease_lost=hb.lost.is_set,
                    on_progress=self.touch))
            finally:
                hb.stop()
                db.session.remove()
                self.current_job = None
                self.touch()

    def run_job(self, job) -> str:
        from core.bulk import engine
        from extensions import db
        self.current_job = str(job["id"])
        hb = Heartbeat(self.app, job["id"], self.id, max(1.0, self.settings.lease_seconds / 3))
        hb.start()
        with self.app.app_context():
            try:
                return engine.process(job, engine.RunControl(
                    worker_id=self.id, should_stop=self.stop.is_set, lease_lost=hb.lost.is_set,
                    on_progress=self.touch))
            finally:
                hb.stop()
                db.session.remove()
                self.current_job = None
                self.touch()


def healthcheck(check_db: bool = True) -> int:
    from core.bulk import settings
    s = settings.get()
    try:
        with open(s.health_file) as f:
            data = json.load(f)
        age = time.time() - float(data["at"])
    except (OSError, ValueError, KeyError) as e:
        print(f"unhealthy: no health file ({e})")
        return 1
    if age > s.health_max_age:
        print(f"unhealthy: the worker loop last reported {age:.0f}s ago (max {s.health_max_age}s)")
        return 1
    if check_db:
        try:
            import psycopg2
            from dotenv import load_dotenv

            from manage_db import libpq_dsn
            load_dotenv()
            conn = psycopg2.connect(libpq_dsn(os.environ["DATABASE_URL"]), connect_timeout=3)
            conn.cursor().execute("SELECT 1")
            conn.close()
        except Exception as e:  # noqa: BLE001
            print(f"unhealthy: database unreachable ({type(e).__name__})")
            return 1
    print(f"healthy: {data.get('worker_id')} reported {age:.0f}s ago, job {data.get('job_id')}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m worker", description="CAWP bulk-job and report-export worker")
    parser.add_argument("--healthcheck", action="store_true", help="exit 0 when the running worker is alive")
    parser.add_argument("--no-db", action="store_true", help="healthcheck without the database ping")
    parser.add_argument("--retention-once", action="store_true", help="run the retention task once and exit")
    args = parser.parse_args(argv)
    if args.healthcheck:
        return healthcheck(check_db=not args.no_db)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)-7s [%(threadName)s] %(name)s: %(message)s")
    app = create_app()
    if args.retention_once:
        from core.bulk import retention
        with app.app_context():
            print(json.dumps(retention.run()))
        return 0
    w = Worker(app)
    w.install_signals()
    return w.run_forever()


if __name__ == "__main__":
    sys.exit(main())
