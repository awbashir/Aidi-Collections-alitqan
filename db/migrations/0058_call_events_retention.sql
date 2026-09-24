-- 0058 retention path for the raw call evidence (Phase 5 P4). review/phase5-evidence.md.
--
-- call_events (0050) is append-only through calling_reject_change(): UPDATE, DELETE and
-- TRUNCATE are refused for everybody. Retention needs one controlled way to delete OLD rows,
-- so the row trigger now uses calling_evidence_retention_guard() (0057):
--   * UPDATE is still refused (CL001);
--   * DELETE is refused (CL001) unless the transaction ran
--       SET LOCAL cawp.retention_purge = 'call_events';
--     which only the retention task (services/calling_cdr_retention.py) does, and only for
--     rows older than the configured CALL_EVENTS_RETENTION_DAYS of attempts that are over
--     (no retention configured = nothing is ever deleted);
--   * TRUNCATE stays refused (the statement trigger call_events_no_truncate is unchanged).
-- The guard is a protection against application mistakes, not against a database owner.
--
-- Locks: DROP/CREATE TRIGGER takes a brief ACCESS EXCLUSIVE lock on call_events. Instant.

DROP TRIGGER IF EXISTS call_events_append_only ON call_events;
CREATE TRIGGER call_events_append_only
    BEFORE UPDATE OR DELETE ON call_events
    FOR EACH ROW EXECUTE FUNCTION calling_evidence_retention_guard();
