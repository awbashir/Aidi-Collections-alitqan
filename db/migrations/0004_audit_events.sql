-- 0004 audit_events: append-only business audit trail for new (Phase 0+) code.
--
-- Written by core/audit.py inside the caller's transaction, so an event exists exactly
-- when the change it describes was committed. Rows are never updated or deleted: the
-- triggers below reject UPDATE, DELETE and TRUNCATE for every role, including the
-- superuser the API connects as. (Only `session_replication_role = replica`, which needs
-- superuser, bypasses triggers; do not use it on this table.)
--
-- No foreign keys on purpose: actor/case/entity ids must survive the hard deletes the
-- legacy API still performs (DELETE /clints, DELETE /users), and an FK would make those
-- legacy deletes fail.
--
-- Locks: creates a new table, three functions and indexes on the empty table. No lock on
-- any existing table. Instant.

CREATE TABLE IF NOT EXISTS audit_events (
    id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    occurred_at      timestamptz NOT NULL DEFAULT now(),
    actor_user_id    uuid,
    action           text        NOT NULL CHECK (action <> ''),
    entity_type      text        NOT NULL CHECK (entity_type <> ''),
    entity_id        text,
    case_id          uuid,
    before           jsonb,
    after            jsonb,
    reason           text,
    request_id       text,
    idempotency_key  text,
    source           text        NOT NULL DEFAULT 'api' CHECK (source <> '')
);

COMMENT ON TABLE audit_events IS
    'Append-only audit trail (core/audit.py). UPDATE/DELETE/TRUNCATE are rejected by triggers.';
COMMENT ON COLUMN audit_events.case_id IS 'clints.id when the event concerns a case (no FK: legacy hard deletes)';
COMMENT ON COLUMN audit_events.source IS 'api | job | migration | manage_db | import | ...';

-- Case timeline, entity history, "what did this user do", time-range scans.
CREATE INDEX IF NOT EXISTS ix_audit_events_case
    ON audit_events (case_id, occurred_at DESC, id DESC) WHERE case_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_audit_events_entity
    ON audit_events (entity_type, entity_id, occurred_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS ix_audit_events_actor
    ON audit_events (actor_user_id, occurred_at DESC) WHERE actor_user_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_audit_events_occurred
    ON audit_events (occurred_at DESC);
CREATE INDEX IF NOT EXISTS ix_audit_events_request
    ON audit_events (request_id) WHERE request_id IS NOT NULL;

CREATE OR REPLACE FUNCTION audit_events_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'audit_events is append-only: % is not allowed', TG_OP
        USING ERRCODE = 'insufficient_privilege',
              HINT = 'Record a new, compensating audit event instead.';
END $$;

DROP TRIGGER IF EXISTS audit_events_no_update_delete ON audit_events;
CREATE TRIGGER audit_events_no_update_delete
    BEFORE UPDATE OR DELETE ON audit_events
    FOR EACH ROW EXECUTE FUNCTION audit_events_append_only();

DROP TRIGGER IF EXISTS audit_events_no_truncate ON audit_events;
CREATE TRIGGER audit_events_no_truncate
    BEFORE TRUNCATE ON audit_events
    FOR EACH STATEMENT EXECUTE FUNCTION audit_events_append_only();
