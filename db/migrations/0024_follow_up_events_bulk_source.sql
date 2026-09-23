-- 0024 follow_up_events.source accepts 'bulk_job' (Phase 2, stream C).
--
-- The reviewed close-out of late imported follow-ups (services/bulk_followups.py, kind
-- `followup_closeout`) runs in the bulk worker, not in an API request. Its `completed`
-- events are recorded with source 'bulk_job' and request_id = the bulk job id, the same
-- source value audit_events uses for bulk jobs (core/bulk/common.audit_many). The CHECK
-- from 0010 only allowed 'api', 'trigger' and 'migration'.
--
-- The new constraint only widens the allowed set, so every existing row satisfies it
-- (validated below; the table holds ~3.3k rows on cawp-db, milliseconds). Re-runnable: the
-- old constraint is dropped by name if present, and the new one is only added when missing.
--
-- Locks: DROP/ADD CONSTRAINT take an ACCESS EXCLUSIVE lock on follow_up_events (a
-- new-code-only table) until the migration's transaction commits, which is milliseconds.
-- lock_timeout 5 s (runner default): on a busy server it fails fast and rolls back
-- completely; re-run.

ALTER TABLE follow_up_events DROP CONSTRAINT IF EXISTS follow_up_events_source_check;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conrelid = 'follow_up_events'::regclass
                      AND conname = 'follow_up_events_source_check_v2') THEN
        ALTER TABLE follow_up_events
            ADD CONSTRAINT follow_up_events_source_check_v2
            CHECK (source IN ('api', 'trigger', 'migration', 'bulk_job')) NOT VALID;
    END IF;
END $$;

ALTER TABLE follow_up_events VALIDATE CONSTRAINT follow_up_events_source_check_v2;
