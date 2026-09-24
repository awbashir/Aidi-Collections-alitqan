"""Retention of the calling evidence (Phase 5 P4): raw `call_events` and imported CDR evidence
(`pbx_cdr_imported`). review/phase5-evidence.md §Retention.

**Default: keep everything.** Nothing is deleted until an operator sets a period. The rebuild's
retention reference (docs/reference/rebuild-retention.md) leaves call evidence to "its own
retention policies", so the periods are an approved deployment decision, not a code default.

| Setting | Default | Accepted | Meaning |
|---|---|---|---|
| `CALL_EVENTS_RETENTION_DAYS` | unset = keep | 30-36500 | delete `call_events` received before now - N days |
| `PBX_CDR_IMPORTED_RETENTION_DAYS` | unset = keep | 30-36500 | delete `pbx_cdr_imported` records whose call started before now - N days |
| `CALLING_RETENTION_BATCH` | 5000 | 100-50000 | rows per delete transaction |
| `CALLING_RETENTION_MAX_BATCHES` | 20 | 1-1000 | batches per table per run (the rest waits for the next run) |
| `CALLING_RETENTION_INTERVAL_SECONDS` | 3600 | 60-86400 | how often `worker_tick()` runs it |

A value outside the accepted range is ignored (logged, treated as "keep"), so a typo such as
`1` can never wipe the evidence.

What is never deleted, whatever the period:
* call_events of an attempt that is not over (state not ended/failed/cancelled), that holds an
  active reservation, or that has an open review flag (P1 `call_review_flags`, when present);
* call_attempts, call_legs, call_wrapups, call_agi_decisions, pbx_sessions, bulk jobs/rows and
  audit_events (other policies; the audit trail stays).

Mechanics: the evidence tables refuse DELETE (0057/0058 guards) unless the transaction ran
`SET LOCAL cawp.retention_purge = '<table>'`, which only this task does. Each batch is one
transaction holding a transaction-scoped advisory lock (a concurrent runner skips) and writes
its own `audit_events` row (`calling_retention.purge`, source `calling_retention`) with the
cutoff and the count, so a deletion and its audit commit together.

Running it:
* `python -m services.calling_cdr_retention` (once; `--dry-run` counts only) - e.g.
  `docker compose run --rm worker python -m services.calling_cdr_retention`;
* from the worker loop: `calling_cdr_retention.worker_tick()` (self-throttled by
  CALLING_RETENTION_INTERVAL_SECONDS; the lead wires it into worker/main.py).
"""
from __future__ import annotations

import datetime as _dt
import json
import logging
import os
import time

from sqlalchemy import text

log = logging.getLogger("cawp.calling.retention")

ADVISORY_KEY = 7_236_405_057
TABLES = ("call_events", "pbx_cdr_imported")
_ENV_DAYS = {"call_events": "CALL_EVENTS_RETENTION_DAYS", "pbx_cdr_imported": "PBX_CDR_IMPORTED_RETENTION_DAYS"}
MIN_DAYS, MAX_DAYS = 30, 36500


def _int_env(name, default, lo, hi):
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        v = int(raw)
    except ValueError:
        log.warning("%s=%r is not a whole number; using %s", name, raw, default)
        return default
    if not lo <= v <= hi:
        log.warning("%s=%s is outside %s-%s; using %s", name, v, lo, hi, default)
        return default
    return v


def settings() -> dict:
    return {
        "days": {t: _int_env(_ENV_DAYS[t], None, MIN_DAYS, MAX_DAYS) for t in TABLES},
        "batch": _int_env("CALLING_RETENTION_BATCH", 5000, 100, 50000),
        "max_batches": _int_env("CALLING_RETENTION_MAX_BATCHES", 20, 1, 1000),
        "interval": _int_env("CALLING_RETENTION_INTERVAL_SECONDS", 3600, 60, 86400),
    }


def _exists(session, table) -> bool:
    return bool(session.execute(text("SELECT to_regclass(:t) IS NOT NULL"), {"t": f"public.{table}"}).scalar())


def _candidates_sql(session, table) -> str:
    """SELECT id ... of the rows `table` may lose (older than :cutoff), oldest first, :n at most."""
    if table == "pbx_cdr_imported":
        return "SELECT id FROM pbx_cdr_imported WHERE call_at < :cutoff ORDER BY call_at, id LIMIT :n"
    keep = ["""(e.attempt_id IS NULL OR NOT EXISTS (
                    SELECT 1 FROM call_attempts a WHERE a.id = e.attempt_id
                       AND a.state NOT IN ('ended', 'failed', 'cancelled')))""",
            """(e.attempt_id IS NULL OR NOT EXISTS (
                    SELECT 1 FROM call_reservations r WHERE r.attempt_id = e.attempt_id AND r.released_at IS NULL))"""]
    if _exists(session, "call_review_flags"):
        keep.append("""(e.attempt_id IS NULL OR NOT EXISTS (
                    SELECT 1 FROM call_review_flags f WHERE f.attempt_id = e.attempt_id AND f.resolved_at IS NULL))""")
    return f"""SELECT e.id FROM call_events e
                WHERE e.received_at < :cutoff AND {' AND '.join(keep)}
                ORDER BY e.received_at, e.id LIMIT :n"""


def run(session=None, *, now: _dt.datetime | None = None, dry_run: bool = False) -> dict:
    """One retention pass. Returns {table: {configured, days, cutoff, deleted | would_delete}}.
    Each batch is one transaction: transaction-scoped advisory lock (a second runner skips),
    SET LOCAL cawp.retention_purge, DELETE, and its audit_events row."""
    from core import audit
    from extensions import db
    session = session or db.session
    cfg = settings()
    now = now or _dt.datetime.now(_dt.timezone.utc)
    report: dict = {"skipped": False}
    for table in TABLES:
        days = cfg["days"][table]
        entry = {"configured": days is not None, "days": days, "cutoff": None, "deleted": 0}
        report[table] = entry
        if days is None or not _exists(session, table):
            session.rollback()
            continue
        cutoff = now - _dt.timedelta(days=days)
        entry["cutoff"] = cutoff.isoformat()
        sql = _candidates_sql(session, table)
        if dry_run:
            entry["would_delete"] = session.execute(
                text(f"SELECT count(*) FROM ({sql.replace(' LIMIT :n', '')}) x"), {"cutoff": cutoff}).scalar()
            entry.pop("deleted")
            session.rollback()
            continue
        for _ in range(cfg["max_batches"]):
            if not session.execute(text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": ADVISORY_KEY}).scalar():
                session.rollback()
                report["skipped"] = True
                break
            session.execute(text("SELECT set_config('cawp.retention_purge', :t, true)"), {"t": table})
            n = session.execute(text(f"DELETE FROM {table} WHERE id IN ({sql})"),
                                {"cutoff": cutoff, "n": cfg["batch"]}).rowcount
            if n:
                entry["deleted"] += n
                audit.record("calling_retention.purge", "retention", table, actor_user_id=None, request_id=None,
                             idempotency_key=None, source="calling_retention", session=session,
                             after={"table": table, "days": days, "cutoff": entry["cutoff"], "deleted": n},
                             reason=f"{_ENV_DAYS[table]}={days}")
            session.commit()
            if n < cfg["batch"]:
                break
        if entry["deleted"]:
            log.info("calling retention: %s rows of %s older than %s deleted", entry["deleted"], table,
                     entry["cutoff"])
    return report


_next_run = {"at": 0.0}


def worker_tick(session=None) -> dict | None:
    """For the worker loop: runs `run()` at most every CALLING_RETENTION_INTERVAL_SECONDS and
    never raises (housekeeping must not stop the worker)."""
    now = time.monotonic()
    if now < _next_run["at"]:
        return None
    _next_run["at"] = now + settings()["interval"]
    try:
        return run(session)
    except Exception:  # noqa: BLE001
        log.exception("calling retention failed")
        try:
            from extensions import db
            (session or db.session).rollback()
        except Exception:  # noqa: BLE001
            pass
        return None


def main(argv=None) -> int:
    import argparse
    p = argparse.ArgumentParser(prog="python -m services.calling_cdr_retention",
                                description="Delete calling evidence older than the configured retention")
    p.add_argument("--dry-run", action="store_true", help="count what would be deleted, delete nothing")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    from worker.main import create_app
    app = create_app()
    with app.app_context():
        print(json.dumps(run(dry_run=args.dry_run), default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
