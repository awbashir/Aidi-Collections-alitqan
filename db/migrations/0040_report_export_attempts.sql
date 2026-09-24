-- 0040 report_export_attempts (Phase 4, stream R0): one row per worker claim of an export.
--
--   outcome  running | completed (published) | lease_expired (crash / restart detected by
--            another worker) | error (unexpected exception) | interrupted (graceful shutdown,
--            does not count toward max_attempts) | failed (a final business failure such as
--            permission_denied or too_many_rows)
--
-- Locks: new table only. Instant.

CREATE TABLE IF NOT EXISTS report_export_attempts (
    id              bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    export_id       uuid        NOT NULL REFERENCES report_exports (id) ON DELETE CASCADE,
    attempt         integer     NOT NULL CHECK (attempt >= 1),
    worker_id       text        NOT NULL,
    started_at      timestamptz NOT NULL DEFAULT now(),
    finished_at     timestamptz,
    outcome         text        NOT NULL DEFAULT 'running'
                    CHECK (outcome IN ('running', 'completed', 'lease_expired', 'error', 'interrupted', 'failed')),
    rows_written    bigint      NOT NULL DEFAULT 0 CHECK (rows_written >= 0),
    error           text,
    CONSTRAINT report_export_attempts_unique UNIQUE (export_id, attempt),
    CONSTRAINT report_export_attempts_finished CHECK ((outcome = 'running') = (finished_at IS NULL))
);

COMMENT ON TABLE report_export_attempts IS 'One row per worker claim of a report export (attempt history).';
