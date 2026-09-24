"""Phase 6 Q2 retention hooks (registered with services/ai/retention.py; review/phase6-quality.md).

    q2.qc_audio        category `audio`        QC audio files older than the policy's audio_days
    q2.qc_transcripts  category `transcripts`  QC transcript text + segments older than transcript_days,
                                               and the same transcript inside approval snapshots

Audio (files): one transaction records a durable purge intent (`qc_file_purges`), clears the
recording's `stored_path` and sets `audio_purged_at`; the file is unlinked only AFTER that commit
(an after-commit callback that re-reads the committed intents on its own connection, so a rolled
back batch never removes a file). A crash between the commit and the unlink leaves a pending
intent that the next run completes (`sweep()`). The recording row, its sha256, duration and
reviews stay; playback answers 410 `audio_purged`.

Transcripts: the text and segments are cleared (`purged_at` set) on the version and in every
approval snapshot that holds that version (`transcript_purged_at`), under
SET LOCAL cawp.retention_purge = 'qc_quality', which the 0063 guards require. Review results
(reasons and the short quotations that are their evidence) are kept.

A recording being processed (a provider call in flight) is skipped until it finishes.
"""
from __future__ import annotations

import logging

from sqlalchemy import event, text

from services.ai.retention import register

log = logging.getLogger("cawp.ai.retention.quality")
PURGE_SETTING = "qc_quality"


def _ready(session) -> bool:
    return bool(session.execute(text("SELECT to_regclass('public.qc_file_purges') IS NOT NULL "
                                     "AND to_regclass('public.qc_approvals') IS NOT NULL")).scalar())


def _real_session(session):
    from sqlalchemy.orm import scoped_session
    return session() if isinstance(session, scoped_session) else session


def _unlink_committed(engine, intent_ids) -> int:
    """Unlink the files of committed, pending intents and mark them done. Returns files removed."""
    from services.ai import quality_audio as qa
    if not intent_ids:
        return 0
    done = 0
    with engine.begin() as conn:
        rows = conn.execute(text("SELECT id, stored_path FROM qc_file_purges WHERE id = ANY(:ids) AND done_at IS NULL "
                                 "FOR UPDATE SKIP LOCKED"), {"ids": list(intent_ids)}).all()
        for iid, path in rows:
            try:
                qa.delete(path)
            except (OSError, ValueError):
                log.exception("could not remove QC audio file for purge intent %s", iid)
                continue
            conn.execute(text("UPDATE qc_file_purges SET done_at = now() WHERE id = :id"), {"id": iid})
            done += 1
    return done


def sweep(session, limit: int = 500) -> int:
    """Complete pending intents left by an interrupted run (they are committed already)."""
    ids = [r[0] for r in session.execute(text(
        "SELECT id FROM qc_file_purges WHERE done_at IS NULL ORDER BY recorded_at, id LIMIT :n"), {"n": limit}).all()]
    if not ids:
        return 0
    return _unlink_committed(session.get_bind(), ids)


def purge_audio(session, *, cutoff, limit):
    if not _ready(session):
        return 0
    sweep(session)
    rows = session.execute(text("""
        SELECT id::text, stored_path, case_id::text FROM qc_recordings
         WHERE audio_purged_at IS NULL AND stored_path IS NOT NULL AND processing IS NULL AND created_at < :c
         ORDER BY created_at, id LIMIT :n
         FOR UPDATE SKIP LOCKED"""), {"c": cutoff, "n": limit}).all()
    if not rows:
        return 0
    intent_ids = []
    for rec_id, path, case_id in rows:
        intent_ids.append(session.execute(text("""
            INSERT INTO qc_file_purges (recording_id, stored_path, reason)
            VALUES (CAST(:r AS uuid), :p, 'ai_retention: audio_days') RETURNING id"""), {"r": rec_id, "p": path}).scalar())
        session.execute(text("""
            UPDATE qc_recordings SET stored_path = NULL, audio_purged_at = now(), updated_at = now()
             WHERE id = CAST(:r AS uuid)"""), {"r": rec_id})
        from core import audit
        audit.record("qc.audio.purge", "qc_recording", rec_id, case_id=case_id, actor_user_id=None, request_id=None,
                     idempotency_key=None, source="ai_retention", session=session,
                     after={"purged": True, "cutoff": cutoff.isoformat()}, reason="AI data policy: audio retention")
    engine = session.get_bind()
    real = _real_session(session)

    def after_commit(_sess):
        try:
            _unlink_committed(engine, intent_ids)
        except Exception:  # noqa: BLE001 - the next run's sweep() completes the intents
            log.exception("QC audio unlink after commit failed; the next retention run retries")

    event.listen(real, "after_commit", after_commit, once=True)
    return len(rows)


def purge_transcripts(session, *, cutoff, limit):
    if not _ready(session):
        return 0
    session.execute(text("SELECT set_config('cawp.retention_purge', :v, true)"), {"v": PURGE_SETTING})
    ids = [r[0] for r in session.execute(text("""
        SELECT t.id::text FROM qc_transcripts t
          JOIN qc_recordings r ON r.id = t.recording_id
         WHERE t.purged_at IS NULL AND t.created_at < :c AND r.processing IS NULL
         ORDER BY t.created_at, t.id LIMIT :n
         FOR UPDATE OF t SKIP LOCKED"""), {"c": cutoff, "n": limit}).all()]
    if not ids:
        return 0
    session.execute(text("UPDATE qc_transcripts SET text = NULL, segments = NULL, purged_at = now() "
                         "WHERE id = ANY(CAST(:ids AS uuid[]))"), {"ids": ids})
    session.execute(text("""
        UPDATE qc_approvals
           SET snapshot = jsonb_set(snapshot, '{transcript}',
                                    (snapshot->'transcript') || '{"text": null, "segments": null, "purged": true}'::jsonb),
               transcript_purged_at = now()
         WHERE transcript_purged_at IS NULL AND jsonb_typeof(snapshot->'transcript') = 'object'
           AND snapshot->'transcript'->>'id' = ANY(:ids)"""), {"ids": ids})
    return len(ids)


register("q2.qc_audio", "audio", purge_audio, "QC recording audio files (intent recorded, file removed after commit)")
register("q2.qc_transcripts", "transcripts", purge_transcripts,
         "QC transcript text and segments, also inside approval snapshots")
