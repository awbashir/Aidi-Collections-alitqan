"""Retention task (run periodically by the worker, one worker at a time).

1. `ready` previews past `preview_expires_at` become `expired`.
2. Upload files past `expires_at` are deleted and `purged_at` is set, unless a job that still
   needs the file (queued / validating / ready / confirmed / running / retrying) uses it. The
   `bulk_uploads` row stays; the sample rows kept for the mapping screen are removed with the file.
3. Artifact files past `expires_at` are deleted and `purged_at` is set; the row stays.
4. Expired `idempotency_keys` are purged (`core.idempotency.purge_expired`).
5. Stray temp files of interrupted uploads (older than a day) are removed.

Rows of `bulk_jobs` / `bulk_job_rows` / `bulk_job_attempts` and `audit_events` are never deleted.
Every purge writes an `audit_events` row (source `bulk_retention`).
"""
from __future__ import annotations

import logging
import os
import time

from sqlalchemy import text

from core.bulk import storage
from core.bulk.common import dumps

log = logging.getLogger("cawp.bulk.retention")

ADVISORY_KEY = 7_236_401_016          # "one retention run at a time" across workers
LIMIT = 500
TMP_MAX_AGE = 24 * 3600

_AUDIT = text("""
    INSERT INTO audit_events (actor_user_id, action, entity_type, entity_id, before, after, reason, source)
    SELECT NULL, e->>'action', e->>'entity_type', e->>'entity_id', NULL, e->'after', e->>'reason', 'bulk_retention'
      FROM jsonb_array_elements(CAST(:events AS jsonb)) e
""")


def run(session=None) -> dict:
    from extensions import db
    session = session or db.session
    report = {"expired_previews": 0, "purged_uploads": 0, "purged_artifacts": 0,
              "purged_idempotency_keys": 0, "removed_tmp_files": 0, "skipped": False}
    got = session.execute(text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": ADVISORY_KEY}).scalar()
    if not got:
        session.rollback()
        report["skipped"] = True
        return report

    report["expired_previews"] = session.execute(text("""
        UPDATE bulk_jobs SET status = 'expired', finished_at = now(), updated_at = now()
         WHERE status = 'ready' AND preview_expires_at <= now()
    """)).rowcount

    uploads = session.execute(text("""
        SELECT u.id::text, u.stored_path FROM bulk_uploads u
         WHERE u.purged_at IS NULL AND u.expires_at <= now()
           AND NOT EXISTS (SELECT 1 FROM bulk_jobs j WHERE j.upload_id = u.id
                            AND j.status IN ('queued', 'validating', 'ready', 'confirmed', 'running', 'retrying'))
         ORDER BY u.expires_at LIMIT :lim
         FOR UPDATE OF u SKIP LOCKED
    """), {"lim": LIMIT}).all()
    arts = session.execute(text("""
        SELECT a.id::text, a.stored_path, a.job_id::text, a.kind FROM bulk_job_artifacts a
         WHERE a.purged_at IS NULL AND a.expires_at <= now()
         ORDER BY a.expires_at LIMIT :lim
         FOR UPDATE SKIP LOCKED
    """), {"lim": LIMIT}).all()

    events = []
    for uid, path in uploads:
        storage.delete(path)
        events.append({"action": "bulk_upload.purge", "entity_type": "bulk_upload", "entity_id": uid,
                       "after": {"purged": True}, "reason": "retention: upload expired"})
    for aid, path, job_id, kind in arts:
        storage.delete(path)
        events.append({"action": "bulk_artifact.purge", "entity_type": "bulk_job", "entity_id": job_id,
                       "after": {"artifact_id": aid, "kind": kind, "purged": True},
                       "reason": "retention: artifact expired"})
    if uploads:
        session.execute(text("""
            UPDATE bulk_uploads
               SET purged_at = now(),
                   sheets = coalesce((SELECT jsonb_agg(s - 'sample') FROM jsonb_array_elements(sheets) s), '[]'::jsonb)
             WHERE id = ANY(CAST(:ids AS uuid[]))
        """), {"ids": [u[0] for u in uploads]})
    if arts:
        session.execute(text("UPDATE bulk_job_artifacts SET purged_at = now() WHERE id = ANY(CAST(:ids AS uuid[]))"),
                        {"ids": [a[0] for a in arts]})
    if events:
        session.execute(_AUDIT, {"events": dumps(events)})
    report["purged_uploads"], report["purged_artifacts"] = len(uploads), len(arts)
    session.commit()

    try:
        from core.idempotency import purge_expired
        report["purged_idempotency_keys"] = purge_expired()
    except Exception:  # noqa: BLE001 - housekeeping must not stop the worker
        log.exception("idempotency purge failed")
    report["removed_tmp_files"] = _clean_tmp()
    if any(v for k, v in report.items() if k != "skipped"):
        log.info("bulk retention: %s", report)
    return report


def _clean_tmp() -> int:
    tmp = os.path.join(storage.root(), "tmp")
    if not os.path.isdir(tmp):
        return 0
    n, now = 0, time.time()
    for name in os.listdir(tmp):
        p = os.path.join(tmp, name)
        try:
            if os.path.isfile(p) and now - os.path.getmtime(p) > TMP_MAX_AGE:
                os.remove(p)
                n += 1
        except OSError:
            pass
    return n
