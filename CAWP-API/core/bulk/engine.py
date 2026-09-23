"""Worker-side engine: claim, lease recovery, validation (prepare) and execution.

Everything here runs in the worker process (`python -m worker`) inside a Flask app context
and uses `extensions.db.session`. Every commit that changes a job is conditional on
`locked_by = <this worker>`: a worker that lost its lease (another worker recovered the job
after it expired) can never commit a batch, so no row is ever processed twice.

Execution batch = ONE transaction:

    rows       SELECT ... FROM bulk_job_rows WHERE pending AND eligible ORDER BY row_no
               LIMIT <batch> FOR UPDATE SKIP LOCKED
    rechecks   confirmer's permission (fresh, not cached) and active user; upload not purged;
               cases locked FOR NO KEY UPDATE in id order; case exists; case in the confirmer's
               current scope; clints.revision == expected_revision; call reservations
    kind       execute_batch(rows that passed, ctx)
    write      audit_events (source bulk_job) + row outcomes + job counters/lease (conditional)
    COMMIT

A re-run (after a crash, retry or restart) only ever selects rows whose outcome is still
`pending`, and the outcome UPDATE itself requires `outcome = 'pending'`.
"""
from __future__ import annotations

import dataclasses
import logging
import time
from typing import Callable

from sqlalchemy import text

from core.bulk import artifacts, registry, sources, storage
from core.bulk import settings as _settings
from core.bulk.common import (NOT_FOUND_OR_NOT_PERMITTED, audit_many, dumps, fresh_scope, scope_sql,
                              user_has_any)
from core.bulk.registry import (FAILED, SKIPPED, SUCCEEDED, BulkRow, Eligibility, ExecuteContext,
                                KindContext, Outcome)

log = logging.getLogger("cawp.bulk.engine")


class LeaseLost(Exception):
    """This worker no longer holds the job (another worker recovered it)."""


class Interrupted(Exception):
    """Graceful stop requested (SIGTERM): release the job for another worker."""


class CancelRequested(Exception):
    pass


class Paused(Exception):
    """Tests / tools: stop after `max_batches` without releasing anything."""


@dataclasses.dataclass
class RunControl:
    worker_id: str
    should_stop: Callable[[], bool] = lambda: False       # graceful shutdown requested
    lease_lost: Callable[[], bool] = lambda: False        # heartbeat thread lost the lease
    on_progress: Callable[[], None] = lambda: None        # liveness (health file)
    max_batches: int | None = None                        # tests: pause after N batches/chunks


def _db():
    from extensions import db
    return db


def _s():
    return _db().session


def load_job(session, job_id, lock: bool = False) -> dict | None:
    row = session.execute(text("SELECT * FROM bulk_jobs WHERE id = CAST(:id AS uuid)"
                                + (" FOR UPDATE" if lock else "")), {"id": str(job_id)}).mappings().first()
    return dict(row) if row else None


# =========================================================================== lease recovery / claim
def recover_expired(session=None) -> list[dict]:
    """validating/running jobs whose lease expired -> retrying (or failed at max_attempts, or
    cancelled when a cancel was requested). Commits. Returns [{id, status}]."""
    session = session or _s()
    rows = session.execute(text("""
        WITH expired AS (
            SELECT id FROM bulk_jobs
             WHERE status IN ('validating', 'running') AND lease_until < now()
             ORDER BY lease_until
             FOR UPDATE SKIP LOCKED
        ), decided AS (
            SELECT j.id,
                   CASE WHEN j.cancel_requested_at IS NOT NULL THEN 'cancelled'
                        WHEN j.failed_attempts + 1 >= j.max_attempts THEN 'failed'
                        ELSE 'retrying' END AS next_status
              FROM bulk_jobs j JOIN expired e ON e.id = j.id
        )
        UPDATE bulk_jobs j
           SET status = d.next_status,
               failed_attempts = j.failed_attempts + 1,
               locked_by = NULL, lease_until = NULL, updated_at = now(),
               finished_at = CASE WHEN d.next_status IN ('failed', 'cancelled') THEN now() END,
               cancelled_at = CASE WHEN d.next_status = 'cancelled' THEN now() END,
               error_code = CASE WHEN d.next_status = 'failed' THEN 'max_attempts_exceeded' ELSE j.error_code END,
               error_message = CASE WHEN d.next_status = 'failed'
                                    THEN 'the worker stopped (lease expired) ' || (j.failed_attempts + 1)
                                         || ' times; the job was given up'
                                    ELSE j.error_message END
          FROM decided d
         WHERE j.id = d.id
        RETURNING j.id::text AS id, j.status, j.phase
    """)).mappings().all()
    if rows:
        ids = [r["id"] for r in rows]
        session.execute(text("""
            UPDATE bulk_job_attempts SET outcome = 'lease_expired', finished_at = now(),
                   error = 'lease expired: the worker crashed or was restarted'
             WHERE job_id = ANY(CAST(:ids AS uuid[])) AND outcome = 'running'
        """), {"ids": ids})
        cancelled = [r["id"] for r in rows if r["status"] == "cancelled" and r["phase"] == "execute"]
        if cancelled:
            _skip_pending(session, cancelled, "cancelled", "the job was cancelled before this row ran")
            _recount(session, cancelled)
        for r in rows:
            log.warning("bulk job %s: lease expired -> %s", r["id"], r["status"])
    session.commit()
    return [dict(r) for r in rows]


def claim(worker_id: str, job_id=None, session=None) -> dict | None:
    """Claim the oldest runnable job (or `job_id`) with FOR UPDATE SKIP LOCKED. Commits."""
    session = session or _s()
    s = _settings.get()
    only = "AND id = CAST(:job_id AS uuid)" if job_id else ""
    row = session.execute(text(f"""
        WITH c AS (
            SELECT id FROM bulk_jobs
             WHERE status IN ('queued', 'confirmed', 'retrying')
               AND (lease_until IS NULL OR lease_until < now()) {only}
             ORDER BY created_at, id
             LIMIT 1
             FOR UPDATE SKIP LOCKED
        )
        UPDATE bulk_jobs j
           SET status = CASE WHEN j.phase = 'prepare' THEN 'validating' ELSE 'running' END,
               locked_by = :me, lease_until = now() + make_interval(secs => :lease), heartbeat_at = now(),
               attempt = j.attempt + 1, started_at = coalesce(j.started_at, now()), updated_at = now()
          FROM c WHERE j.id = c.id
        RETURNING j.*
    """), {"me": worker_id, "lease": s.lease_seconds, "job_id": str(job_id) if job_id else None}).mappings().first()
    if row is None:
        session.commit()
        return None
    job = dict(row)
    session.execute(text("""
        INSERT INTO bulk_job_attempts (job_id, attempt, phase, worker_id) VALUES (:j, :a, :p, :w)
        ON CONFLICT (job_id, attempt) DO NOTHING
    """), {"j": str(job["id"]), "a": job["attempt"], "p": job["phase"], "w": worker_id})
    session.commit()
    log.info("bulk job %s (%s, %s) claimed by %s, attempt %s", job["id"], job["kind"], job["phase"],
             worker_id, job["attempt"])
    return job


def renew_lease(conn, job_id, worker_id) -> bool:
    """Heartbeat on a separate connection. False when the lease is no longer ours."""
    s = _settings.get()
    r = conn.execute(text("""
        UPDATE bulk_jobs SET lease_until = now() + make_interval(secs => :lease), heartbeat_at = now()
         WHERE id = CAST(:id AS uuid) AND locked_by = :me AND status IN ('validating', 'running')
    """), {"id": str(job_id), "me": worker_id, "lease": s.lease_seconds})
    return r.rowcount == 1


def _touch(session, job, worker_id, sets: str = "", params: dict | None = None):
    """Renew the lease and apply `sets` only while this worker holds the job; LeaseLost
    otherwise. Returns (cancel_requested_at, status)."""
    s = _settings.get()
    row = session.execute(text(f"""
        UPDATE bulk_jobs SET lease_until = now() + make_interval(secs => :lease), heartbeat_at = now(),
                             updated_at = now() {sets}
         WHERE id = CAST(:id AS uuid) AND locked_by = :me AND status IN ('validating', 'running')
        RETURNING cancel_requested_at, status
    """), {"id": str(job["id"]), "me": worker_id, "lease": s.lease_seconds, **(params or {})}).first()
    if row is None:
        raise LeaseLost(str(job["id"]))
    return row


def _finish_attempt(session, job, outcome: str, rows: int = 0, error: str | None = None):
    session.execute(text("""
        UPDATE bulk_job_attempts SET outcome = :o, finished_at = now(),
               rows_processed = rows_processed + :n, error = coalesce(:e, error)
         WHERE job_id = CAST(:j AS uuid) AND attempt = :a AND outcome = 'running'
    """), {"o": outcome, "n": rows, "e": error, "j": str(job["id"]), "a": job["attempt"]})


def _attempt_rows(session, job, n):
    session.execute(text("""
        UPDATE bulk_job_attempts SET rows_processed = rows_processed + :n
         WHERE job_id = CAST(:j AS uuid) AND attempt = :a
    """), {"n": n, "j": str(job["id"]), "a": job["attempt"]})


# =========================================================================== process one job
def process(job: dict, ctl: RunControl) -> str:
    """Run the claimed job's current phase to its end. Returns the job's resulting status,
    or 'lease_lost' / 'interrupted' / 'paused'. BaseExceptions (a simulated crash, a killed
    process) propagate without touching the job: its lease simply expires."""
    session = _s()
    kind = registry.get(job["kind"])
    try:
        if kind is None:
            raise sources.JobFailure("unknown_kind", f"no bulk kind {job['kind']!r} is registered")
        if job["phase"] == "prepare":
            return _prepare(session, job, kind, ctl)
        return _execute(session, job, kind, ctl)
    except LeaseLost:
        session.rollback()
        log.warning("bulk job %s: lease lost, stopping without committing", job["id"])
        return "lease_lost"
    except Paused:
        session.rollback()
        return "paused"
    except Interrupted:
        session.rollback()
        return _release(session, job, ctl.worker_id)
    except CancelRequested:
        session.rollback()
        return _cancel(session, job, ctl.worker_id)
    except sources.JobFailure as e:
        session.rollback()
        return _fail(session, job, ctl.worker_id, e.code, e.message)
    except Exception as e:  # noqa: BLE001 - one job must never kill the worker
        session.rollback()
        log.exception("bulk job %s failed with an unexpected error", job["id"])
        return _errored(session, job, ctl.worker_id, e)


def _check(session, job, ctl, cancel_at):
    if ctl.lease_lost():
        raise LeaseLost(str(job["id"]))
    if cancel_at is not None:
        raise CancelRequested()
    if ctl.should_stop():
        raise Interrupted()


def _release(session, job, worker_id) -> str:
    r = session.execute(text("""
        UPDATE bulk_jobs SET status = 'retrying', locked_by = NULL, lease_until = NULL, updated_at = now()
         WHERE id = CAST(:id AS uuid) AND locked_by = :me
    """), {"id": str(job["id"]), "me": worker_id})
    if r.rowcount:
        _finish_attempt(session, job, "interrupted", error="graceful shutdown; resumed by the next worker")
    session.commit()
    log.info("bulk job %s released (graceful shutdown)", job["id"])
    return "interrupted"


def _fail(session, job, worker_id, code, message) -> str:
    r = session.execute(text("""
        UPDATE bulk_jobs SET status = 'failed', locked_by = NULL, lease_until = NULL, finished_at = now(),
               updated_at = now(), error_code = :c, error_message = :m
         WHERE id = CAST(:id AS uuid) AND locked_by = :me
    """), {"id": str(job["id"]), "me": worker_id, "c": code, "m": message})
    if r.rowcount:
        _finish_attempt(session, job, "completed", error=f"{code}: {message}")
    session.commit()
    log.info("bulk job %s failed: %s", job["id"], code)
    return "failed"


def _errored(session, job, worker_id, exc) -> str:
    msg = f"{type(exc).__name__}: {exc}"[:1000]
    row = session.execute(text("""
        UPDATE bulk_jobs j
           SET failed_attempts = j.failed_attempts + 1,
               status = CASE WHEN j.failed_attempts + 1 >= j.max_attempts THEN 'failed' ELSE 'retrying' END,
               finished_at = CASE WHEN j.failed_attempts + 1 >= j.max_attempts THEN now() END,
               error_code = CASE WHEN j.failed_attempts + 1 >= j.max_attempts THEN 'max_attempts_exceeded'
                                 ELSE 'worker_error' END,
               error_message = :m, locked_by = NULL, lease_until = NULL, updated_at = now()
         WHERE j.id = CAST(:id AS uuid) AND j.locked_by = :me
        RETURNING j.status
    """), {"id": str(job["id"]), "me": worker_id, "m": msg}).first()
    if row is not None:
        _finish_attempt(session, job, "error", error=msg)
    session.commit()
    return row[0] if row else "lease_lost"


def _skip_pending(session, job_ids, code, message):
    session.execute(text("""
        UPDATE bulk_job_rows SET outcome = 'skipped', outcome_code = :c, outcome_message = :m, processed_at = now()
         WHERE job_id = ANY(CAST(:ids AS uuid[])) AND outcome = 'pending'
    """), {"ids": [str(j) for j in job_ids], "c": code, "m": message})


def _recount(session, job_ids):
    """Exact job counters from the rows (after a cancel / at the end of a phase)."""
    session.execute(text("""
        UPDATE bulk_jobs j SET
               total_rows = c.total, eligible_rows = c.eligible, excluded_rows = c.excluded, error_rows = c.errors,
               processed_rows = c.processed, succeeded_rows = c.succeeded, skipped_rows = c.skipped,
               failed_rows = c.failed, updated_at = now()
          FROM (SELECT r.job_id,
                       count(*) AS total,
                       count(*) FILTER (WHERE r.eligibility = 'eligible') AS eligible,
                       count(*) FILTER (WHERE r.eligibility = 'excluded') AS excluded,
                       count(*) FILTER (WHERE r.eligibility = 'error') AS errors,
                       count(*) FILTER (WHERE r.eligibility = 'eligible' AND r.outcome <> 'pending') AS processed,
                       count(*) FILTER (WHERE r.eligibility = 'eligible' AND r.outcome = 'succeeded') AS succeeded,
                       count(*) FILTER (WHERE r.eligibility = 'eligible' AND r.outcome = 'skipped') AS skipped,
                       count(*) FILTER (WHERE r.eligibility = 'eligible' AND r.outcome = 'failed') AS failed
                  FROM bulk_job_rows r WHERE r.job_id = ANY(CAST(:ids AS uuid[])) GROUP BY r.job_id) c
         WHERE j.id = c.job_id
    """), {"ids": [str(j) for j in job_ids]})


def _cancel(session, job, worker_id) -> str:
    r = session.execute(text("""
        UPDATE bulk_jobs SET status = 'cancelled', cancelled_at = now(), finished_at = now(),
               locked_by = NULL, lease_until = NULL, updated_at = now()
         WHERE id = CAST(:id AS uuid) AND locked_by = :me
    """), {"id": str(job["id"]), "me": worker_id})
    replaced = []
    if r.rowcount:
        if job["phase"] == "execute":
            _skip_pending(session, [job["id"]], "cancelled", "the job was cancelled before this row ran")
            _recount(session, [job["id"]])
            art = artifacts.write(session, load_job(session, job["id"]), "results")
            replaced.append(art["replaced_path"])
        _finish_attempt(session, job, "cancelled")
    session.commit()
    for p in replaced:
        storage.delete(p)
    log.info("bulk job %s cancelled between batches", job["id"])
    return "cancelled"


# =========================================================================== prepare (validation)
_SNAPSHOT_SQL = """
    SELECT r.id, r.row_no, r.raw, r.mapped, r.match_value, r.case_id::text AS case_id,
           r.expected_revision, r.eligibility, r.eligibility_code, r.details,
           c.revision AS c_revision, c."accountNumber" AS c_account_number,
           c."identityNumber" AS c_identity_number, c.name AS c_name, c.user_id::text AS c_user_id,
           c.portfolios_id::text AS c_portfolio_id, p.contract_id::text AS c_contract_id,
           c.is_locked AS c_is_locked, c.is_archive AS c_is_archive, c.is_active AS c_is_active
      FROM bulk_job_rows r
      JOIN clints c ON c.id = r.case_id
      LEFT JOIN portfolios p ON p.id = c.portfolios_id
"""


def _row_from(m, with_case: bool = True) -> BulkRow:
    row = BulkRow(id=m["id"], row_no=m["row_no"], raw=m["raw"] or {}, mapped=m["mapped"] or {},
                  match_value=m["match_value"], case_id=m["case_id"], expected_revision=m["expected_revision"],
                  eligibility=m["eligibility"], eligibility_code=m["eligibility_code"], details=m["details"])
    if with_case and m.get("c_revision") is not None:
        row.case = {"id": m["case_id"], "revision": m["c_revision"], "account_number": m["c_account_number"],
                    "identity_number": m["c_identity_number"], "name": m["c_name"], "user_id": m["c_user_id"],
                    "portfolio_id": m["c_portfolio_id"], "contract_id": m["c_contract_id"],
                    "is_locked": m["c_is_locked"], "is_archive": m["c_is_archive"], "is_active": m["c_is_active"]}
    return row


def _prepare(session, job, kind, ctl) -> str:
    s = _settings.get()
    job_id = str(job["id"])
    wid = ctl.worker_id
    chunks = [0]

    # restart-safe: a resumed validation starts over (it has no side effects)
    session.execute(text("DELETE FROM bulk_job_rows WHERE job_id = CAST(:j AS uuid)"), {"j": job_id})
    old_files = [r[0] for r in session.execute(text(
        "DELETE FROM bulk_job_artifacts WHERE job_id = CAST(:j AS uuid) RETURNING stored_path"), {"j": job_id})]
    _touch(session, job, wid, """, total_rows = 0, prepared_rows = 0, eligible_rows = 0, excluded_rows = 0,
            error_rows = 0, processed_rows = 0, succeeded_rows = 0, skipped_rows = 0, failed_rows = 0,
            error_code = NULL, error_message = NULL""")
    session.commit()
    for p in old_files:
        storage.delete(p)

    def checkpoint(extra_sets: str = "", params: dict | None = None):
        cancel_at, _status = _touch(session, job, wid, extra_sets, params)
        session.commit()
        ctl.on_progress()
        _check(session, job, ctl, cancel_at)
        chunks[0] += 1
        if ctl.max_batches is not None and chunks[0] >= ctl.max_batches:
            raise Paused()

    creator = str(job["created_by"])
    scope = fresh_scope(creator)
    total = sources.materialise(session, job, kind, scope, checkpoint)
    checkpoint(", total_rows = :t", {"t": total})

    if job["source"] in ("selection", "spreadsheet"):
        _match(session, job, kind, scope)
        checkpoint()
    _mark_duplicates(session, job_id)
    checkpoint()

    kctx = KindContext(session, job, creator, scope, job["params"] or {})
    after = 0
    while True:
        rows = session.execute(text(_SNAPSHOT_SQL + """
             WHERE r.job_id = CAST(:j AS uuid) AND r.eligibility = 'pending' AND r.row_no > :after
             ORDER BY r.row_no LIMIT :n
        """), {"j": job_id, "after": after, "n": s.prepare_chunk}).mappings().all()
        if not rows:
            break
        batch = [_row_from(m) for m in rows]
        decisions = kind.prepare(batch, kctx) or {}
        _write_eligibility(session, batch, decisions)
        after = batch[-1].row_no
        checkpoint(", prepared_rows = prepared_rows + :n", {"n": len(batch)})

    # a case deleted between matching and the snapshot above
    session.execute(text("""
        UPDATE bulk_job_rows SET eligibility = 'error', eligibility_code = :code, case_id = NULL,
               eligibility_message = 'case not found or not in your scope'
         WHERE job_id = CAST(:j AS uuid) AND eligibility = 'pending'
    """), {"j": job_id, "code": NOT_FOUND_OR_NOT_PERMITTED})
    _recount(session, [job_id])
    session.execute(text("UPDATE bulk_jobs SET prepared_rows = total_rows WHERE id = CAST(:j AS uuid)"), {"j": job_id})

    replaced = []
    if kind.read_only:
        session.execute(text("""
            UPDATE bulk_job_rows
               SET outcome = CASE WHEN eligibility = 'eligible' THEN 'succeeded' ELSE 'skipped' END,
                   outcome_code = CASE WHEN eligibility = 'eligible' THEN NULL ELSE 'not_eligible' END,
                   outcome_message = CASE WHEN eligibility = 'eligible' THEN NULL ELSE eligibility_code END,
                   processed_at = now(), attempt = :a
             WHERE job_id = CAST(:j AS uuid) AND outcome = 'pending'
        """), {"j": job_id, "a": job["attempt"]})
        _recount(session, [job_id])
        _touch(session, job, wid, ", preview_ready_at = now()")
        fresh = load_job(session, job_id)
        for k in ("preview", "errors", "results"):
            replaced.append(artifacts.write(session, fresh, k)["replaced_path"])
        final = "succeeded"
        session.execute(text("""
            UPDATE bulk_jobs SET status = 'succeeded', finished_at = now(), locked_by = NULL, lease_until = NULL,
                   updated_at = now()
             WHERE id = CAST(:j AS uuid) AND locked_by = :me
        """), {"j": job_id, "me": wid})
    else:
        _touch(session, job, wid, """, preview_ready_at = now(),
               preview_expires_at = now() + make_interval(hours => :ttl)""", {"ttl": s.preview_ttl_hours})
        fresh = load_job(session, job_id)
        for k in ("preview", "errors"):
            replaced.append(artifacts.write(session, fresh, k)["replaced_path"])
        final = "ready"
        session.execute(text("""
            UPDATE bulk_jobs SET status = 'ready', locked_by = NULL, lease_until = NULL, updated_at = now()
             WHERE id = CAST(:j AS uuid) AND locked_by = :me
        """), {"j": job_id, "me": wid})
    _finish_attempt(session, job, "completed")
    session.commit()
    for p in replaced:
        storage.delete(p)
    log.info("bulk job %s validated: %s", job_id, final)
    return final


def _match(session, job, kind, scope):
    job_id = str(job["id"])
    field = "case_id" if job["source"] == "selection" else kind.match_by
    if field is not None:
        col = registry.MATCH_COLUMNS[field]
        rhs = "CAST(r.match_value AS uuid)" if field == "case_id" else "r.match_value"
        session.execute(text(f"""
            UPDATE bulk_job_rows r SET case_id = c.id
              FROM clints c
             WHERE r.job_id = CAST(:j AS uuid) AND r.eligibility = 'pending' AND r.case_id IS NULL
               AND r.match_value IS NOT NULL AND {col} = {rhs}
        """), {"j": job_id})
    else:
        _kind_match(session, job, kind, scope)
    session.execute(text("""
        UPDATE bulk_job_rows SET eligibility = 'error', eligibility_code = :code,
               eligibility_message = 'case not found or not in your scope'
         WHERE job_id = CAST(:j AS uuid) AND eligibility = 'pending' AND case_id IS NULL
    """), {"j": job_id, "code": NOT_FOUND_OR_NOT_PERMITTED})
    pred, params = scope_sql(session, scope, "c")
    session.execute(text(f"""
        UPDATE bulk_job_rows r SET eligibility = 'error', eligibility_code = :code, case_id = NULL,
               eligibility_message = 'case not found or not in your scope'
          FROM clints c
         WHERE r.job_id = CAST(:j AS uuid) AND r.eligibility = 'pending' AND c.id = r.case_id
           AND NOT ({pred})
    """), {"j": job_id, "code": NOT_FOUND_OR_NOT_PERMITTED, **params})


def _kind_match(session, job, kind, scope):
    s = _settings.get()
    kctx = KindContext(session, job, str(job["created_by"]), scope, job["params"] or {})
    after = 0
    while True:
        rows = session.execute(text("""
            SELECT id, row_no, raw, mapped, match_value, case_id::text AS case_id, expected_revision,
                   eligibility, eligibility_code, details
              FROM bulk_job_rows
             WHERE job_id = CAST(:j AS uuid) AND eligibility = 'pending' AND case_id IS NULL AND row_no > :after
             ORDER BY row_no LIMIT :n
        """), {"j": str(job["id"]), "after": after, "n": s.prepare_chunk}).mappings().all()
        if not rows:
            return
        batch = [_row_from(m, with_case=False) for m in rows]
        found = kind.match_rows(batch, kctx) or {}
        upd = []
        for r in batch:
            v = found.get(r.id)
            if isinstance(v, Eligibility):
                upd.append({"id": r.id, "case_id": None, "eligibility": v.status, "code": v.code,
                            "message": v.message, "details": v.details})
            elif v:
                upd.append({"id": r.id, "case_id": str(v), "eligibility": "pending", "code": None,
                            "message": None, "details": None})
        if upd:
            session.execute(text("""
                UPDATE bulk_job_rows r SET case_id = d.case_id, eligibility = d.eligibility,
                       eligibility_code = d.code, eligibility_message = d.message,
                       details = coalesce(d.details, r.details)
                  FROM jsonb_to_recordset(CAST(:rows AS jsonb))
                       AS d(id bigint, case_id uuid, eligibility text, code text, message text, details jsonb)
                 WHERE r.id = d.id
            """), {"rows": dumps(upd)})
        after = batch[-1].row_no


def _mark_duplicates(session, job_id):
    session.execute(text("""
        UPDATE bulk_job_rows r
           SET eligibility = 'excluded', eligibility_code = 'duplicate_case',
               eligibility_message = 'the same case is on an earlier row',
               details = jsonb_build_object('first_row_no', d.first_row)
          FROM (SELECT id, row_no, min(row_no) OVER (PARTITION BY case_id) AS first_row
                  FROM bulk_job_rows
                 WHERE job_id = CAST(:j AS uuid) AND eligibility = 'pending' AND case_id IS NOT NULL) d
         WHERE r.id = d.id AND d.row_no > d.first_row
    """), {"j": job_id})


def _write_eligibility(session, rows: list[BulkRow], decisions: dict):
    data = []
    for r in rows:
        e = decisions.get(r.id) or Eligibility.eligible()
        if not isinstance(e, Eligibility) or e.status not in (registry.ELIGIBLE, registry.EXCLUDED, registry.ERROR):
            raise TypeError(f"prepare() returned {e!r} for row {r.row_no}")
        if e.status != registry.ELIGIBLE and not e.code:
            raise ValueError(f"prepare(): row {r.row_no} is {e.status} without a code")
        data.append({"id": r.id, "e": e.status, "c": e.code, "m": e.message, "d": e.details,
                     "rev": r.case["revision"] if r.case else None})
    session.execute(text("""
        UPDATE bulk_job_rows r SET eligibility = d.e, eligibility_code = d.c, eligibility_message = d.m,
               details = coalesce(d.d, r.details), expected_revision = d.rev
          FROM jsonb_to_recordset(CAST(:rows AS jsonb)) AS d(id bigint, e text, c text, m text, d jsonb, rev int)
         WHERE r.id = d.id
    """), {"rows": dumps(data)})


# =========================================================================== execute
_LOCK_CASES = """
    SELECT c.id::text AS id, c.revision, c."accountNumber" AS account_number, c."identityNumber" AS identity_number,
           c.name, c.user_id::text AS user_id, c.portfolios_id::text AS portfolio_id,
           p.contract_id::text AS contract_id, c.is_locked, c.is_archive, c.is_active
      FROM clints c LEFT JOIN portfolios p ON p.id = c.portfolios_id
     WHERE c.id = ANY(CAST(:ids AS uuid[]))
     ORDER BY c.id
     FOR NO KEY UPDATE OF c
"""


def acting_user(job) -> str:
    return str(job["retried_by"] or job["confirmed_by"])


def _execute(session, job, kind, ctl) -> str:
    s = _settings.get()
    job_id = str(job["id"])
    wid = ctl.worker_id
    acting = acting_user(job)

    if job["execution_started_at"] is None:
        expired = session.execute(text("SELECT CAST(:e AS timestamptz) <= now()"),
                                  {"e": job["preview_expires_at"]}).scalar()
        if expired:
            raise sources.JobFailure("preview_expired", "the preview expired before execution started; reopen the job")
    session.execute(text("""
        UPDATE bulk_job_rows SET outcome = 'skipped', outcome_code = 'not_eligible',
               outcome_message = coalesce(eligibility_code, eligibility), processed_at = now(), attempt = :a
         WHERE job_id = CAST(:j AS uuid) AND eligibility <> 'eligible' AND outcome = 'pending'
    """), {"j": job_id, "a": job["attempt"]})
    cancel_at, _ = _touch(session, job, wid, ", execution_started_at = coalesce(execution_started_at, now())")
    session.commit()

    batch_size = kind.batch_size or s.batch_size
    batches = 0
    while True:
        ctl.on_progress()
        _check(session, job, ctl, cancel_at)
        if ctl.max_batches is not None and batches >= ctl.max_batches:
            raise Paused()
        rows = session.execute(text("""
            SELECT id, row_no, raw, mapped, match_value, case_id::text AS case_id, expected_revision,
                   eligibility, eligibility_code, details
              FROM bulk_job_rows
             WHERE job_id = CAST(:j AS uuid) AND eligibility = 'eligible' AND outcome = 'pending'
             ORDER BY row_no LIMIT :n
             FOR UPDATE SKIP LOCKED
        """), {"j": job_id, "n": batch_size}).mappings().all()
        if not rows:
            session.rollback()
            break
        batch = [_row_from(m, with_case=False) for m in rows]
        try:
            outcomes, events = _run_batch(session, job, kind, acting, batch)
            cancel_at = _commit_batch(session, job, wid, batch, outcomes, events)
        except (LeaseLost, Paused, Interrupted, CancelRequested):
            raise
        except Exception:  # noqa: BLE001 - isolate the bad row(s)
            session.rollback()
            log.exception("bulk job %s: batch at row %s failed; retrying it row by row", job_id, batch[0].row_no)
            for row in batch:
                cancel_at = _run_single(session, job, kind, acting, row, wid)
        batches += 1

    _recount(session, [job_id])
    c = session.execute(text("""
        SELECT succeeded_rows, skipped_rows, failed_rows, eligible_rows FROM bulk_jobs WHERE id = CAST(:j AS uuid)
    """), {"j": job_id}).first()
    succeeded, skipped, failed, eligible = c
    if skipped + failed == 0:
        final, code, msg = "succeeded", None, None
    elif succeeded > 0:
        final, code, msg = "partially_succeeded", None, None
    else:
        final, code, msg = "failed", "nothing_applied", "no row was applied (see the row outcomes)"
    r = session.execute(text("""
        UPDATE bulk_jobs SET status = :st, finished_at = now(), locked_by = NULL, lease_until = NULL,
               updated_at = now(), error_code = :c, error_message = :m
         WHERE id = CAST(:j AS uuid) AND locked_by = :me
    """), {"st": final, "c": code, "m": msg, "j": job_id, "me": wid})
    if r.rowcount != 1:
        session.rollback()
        raise LeaseLost(job_id)
    art = artifacts.write(session, load_job(session, job_id), "results")
    _finish_attempt(session, job, "completed")
    session.commit()
    storage.delete(art["replaced_path"])
    log.info("bulk job %s executed: %s (%s ok, %s skipped, %s failed of %s)", job_id, final,
             succeeded, skipped, failed, eligible)
    return final


def _run_batch(session, job, kind, acting, rows: list[BulkRow]):
    """Rechecks + kind.execute_batch inside the caller's (batch) transaction."""
    from services.bulk_common import call_reservations

    out: dict[int, Outcome] = {}
    if not user_has_any(session, acting, kind.permissions):
        for r in rows:
            out[r.id] = Outcome.failed("permission_denied",
                                       "the confirming user no longer has the permission for this action",
                                       {"required_any": list(kind.permissions)})
        return out, []
    if job["source"] == "spreadsheet":
        purged = session.execute(text("""
            SELECT purged_at IS NOT NULL FROM bulk_uploads WHERE id = CAST(:u AS uuid)
        """), {"u": str(job["upload_id"])}).scalar()
        if purged is not False:
            for r in rows:
                out[r.id] = Outcome.failed("upload_purged", "the source upload was purged")
            return out, []

    ids = sorted({r.case_id for r in rows})
    cases = {m["id"]: dict(m) for m in session.execute(text(_LOCK_CASES), {"ids": ids}).mappings()}
    scope = fresh_scope(acting)
    pred, params = scope_sql(session, scope, "c")
    in_scope = {r[0] for r in session.execute(text(
        f"SELECT c.id::text FROM clints c WHERE c.id = ANY(CAST(:ids AS uuid[])) AND {pred}"),
        {"ids": list(cases), **params})} if cases else set()
    reserved = {str(x) for x in (call_reservations(list(cases)) or ())}

    runnable = []
    for r in rows:
        snap = cases.get(r.case_id)
        if snap is None:
            out[r.id] = Outcome.skipped("case_missing", "the case no longer exists")
        elif r.case_id not in in_scope:
            out[r.id] = Outcome.failed("out_of_scope", "the case is no longer in the confirming user's scope")
        elif snap["revision"] != r.expected_revision:
            out[r.id] = Outcome.skipped("case_changed", "the case changed after the preview; nothing was forced",
                                        {"expected_revision": r.expected_revision, "current_revision": snap["revision"]})
        elif r.case_id in reserved:
            out[r.id] = Outcome.skipped("call_reserved", "the case has an active or uncertain call reservation")
        else:
            r.case = snap
            runnable.append(r)

    ctx = ExecuteContext(session, job, acting, scope, job["params"] or {}, job["attempt"])
    if runnable:
        result = kind.execute_batch(runnable, ctx) or {}
        for r in runnable:
            o = result.get(r.id)
            if o is None:
                o = Outcome.failed("no_outcome", "the action reported no outcome for this row")
            if not isinstance(o, Outcome) or o.status not in (SUCCEEDED, SKIPPED, FAILED):
                raise TypeError(f"execute_batch() returned {o!r} for row {r.row_no}")
            if o.status != SUCCEEDED and not o.code:
                raise ValueError(f"execute_batch(): row {r.row_no} is {o.status} without a code")
            out[r.id] = o
    return out, ctx.events


def _commit_batch(session, job, wid, rows, outcomes, events):
    data = [{"id": r.id, "o": outcomes[r.id].status, "c": outcomes[r.id].code, "m": outcomes[r.id].message,
             "d": outcomes[r.id].details} for r in rows]
    audit_many(session, events)
    n = session.execute(text("""
        UPDATE bulk_job_rows r SET outcome = d.o, outcome_code = d.c, outcome_message = d.m,
               details = CASE WHEN d.d IS NULL THEN r.details ELSE coalesce(r.details, '{}'::jsonb) || d.d END,
               processed_at = now(), attempt = :a
          FROM jsonb_to_recordset(CAST(:rows AS jsonb)) AS d(id bigint, o text, c text, m text, d jsonb)
         WHERE r.id = d.id AND r.outcome = 'pending'
    """), {"rows": dumps(data), "a": job["attempt"]}).rowcount
    if n != len(rows):
        raise RuntimeError(f"expected to record {len(rows)} outcomes, recorded {n}")
    counts = {"s": 0, "k": 0, "f": 0}
    for d in data:
        counts[{"succeeded": "s", "skipped": "k", "failed": "f"}[d["o"]]] += 1
    cancel_at, _ = _touch(session, job, wid, """, processed_rows = processed_rows + :p,
            succeeded_rows = succeeded_rows + :s, skipped_rows = skipped_rows + :k, failed_rows = failed_rows + :f""",
                          {"p": len(rows), **counts})
    _attempt_rows(session, job, len(rows))
    session.commit()
    return cancel_at


def _run_single(session, job, kind, acting, row: BulkRow, wid):
    """One row in its own transaction (after its batch raised)."""
    row.case = None
    try:
        locked = session.execute(text("""
            SELECT 1 FROM bulk_job_rows WHERE id = :id AND outcome = 'pending' FOR UPDATE SKIP LOCKED
        """), {"id": row.id}).first()
        if locked is None:
            session.rollback()
            return None
        outcomes, events = _run_batch(session, job, kind, acting, [row])
        return _commit_batch(session, job, wid, [row], outcomes, events)
    except (LeaseLost, Paused, Interrupted, CancelRequested):
        raise
    except Exception as e:  # noqa: BLE001
        session.rollback()
        log.exception("bulk job %s row %s failed", job["id"], row.row_no)
        msg = f"{type(e).__name__}: {e}"[:500]
        return _commit_batch(session, job, wid, [row],
                             {row.id: Outcome.failed("internal_error", msg)}, [])


def run_one(worker_id: str, job_id=None, ctl: RunControl | None = None) -> str | None:
    """Recover expired leases, claim one job and process it (used by the worker loop and tests)."""
    recover_expired()
    job = claim(worker_id, job_id)
    if job is None:
        return None
    return process(job, ctl or RunControl(worker_id))


def wait_seconds() -> float:
    return float(_settings.get().poll_seconds)


def sleep_until(deadline: float, should_stop: Callable[[], bool]) -> None:
    while time.monotonic() < deadline and not should_stop():
        time.sleep(0.2)
