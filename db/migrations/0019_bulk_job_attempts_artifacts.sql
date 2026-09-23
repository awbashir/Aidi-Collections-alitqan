-- 0019 bulk_job_attempts + bulk_job_artifacts (Phase 2, stream F).
--
-- bulk_job_attempts: one row per worker claim of a job (attempt history for the jobs screen).
--   outcome  running | completed (phase finished) | lease_expired (crash / restart detected by
--            another worker) | error (unexpected exception) | interrupted (graceful shutdown,
--            does not count toward max_attempts) | cancelled
--
-- bulk_job_artifacts: the private CSV files of a job (preview, errors, results), stored under
--   BULK_STORAGE_DIR/artifacts/<job id>/, formula-safe (a leading = + - @ TAB CR is escaped),
--   downloadable only through the API with a permission check. One live row per (job, kind);
--   regenerating replaces the file. expires_at = created_at + BULK_ARTIFACT_TTL_DAYS; the
--   retention task deletes the file and sets purged_at, the row stays.
--
-- Locks: new tables only. Instant.

CREATE TABLE IF NOT EXISTS bulk_job_attempts (
    id              bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    job_id          uuid        NOT NULL REFERENCES bulk_jobs (id) ON DELETE CASCADE,
    attempt         integer     NOT NULL CHECK (attempt >= 1),
    phase           text        NOT NULL CHECK (phase IN ('prepare', 'execute')),
    worker_id       text        NOT NULL,
    started_at      timestamptz NOT NULL DEFAULT now(),
    finished_at     timestamptz,
    outcome         text        NOT NULL DEFAULT 'running'
                    CHECK (outcome IN ('running', 'completed', 'lease_expired', 'error', 'interrupted', 'cancelled')),
    rows_processed  integer     NOT NULL DEFAULT 0 CHECK (rows_processed >= 0),
    error           text,
    CONSTRAINT bulk_job_attempts_unique UNIQUE (job_id, attempt),
    CONSTRAINT bulk_job_attempts_finished CHECK ((outcome = 'running') = (finished_at IS NULL))
);

COMMENT ON TABLE bulk_job_attempts IS 'One row per worker claim of a bulk job (attempt history).';

CREATE TABLE IF NOT EXISTS bulk_job_artifacts (
    id            uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id        uuid        NOT NULL REFERENCES bulk_jobs (id) ON DELETE CASCADE,
    kind          text        NOT NULL CHECK (kind IN ('preview', 'errors', 'results')),
    file_name     text        NOT NULL,
    stored_path   text        NOT NULL,
    content_type  text        NOT NULL DEFAULT 'text/csv; charset=utf-8',
    size_bytes    bigint      NOT NULL CHECK (size_bytes >= 0),
    sha256        text        NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    row_count     integer     NOT NULL DEFAULT 0 CHECK (row_count >= 0),
    created_at    timestamptz NOT NULL DEFAULT now(),
    expires_at    timestamptz NOT NULL,
    purged_at     timestamptz,
    CONSTRAINT bulk_job_artifacts_one_per_kind UNIQUE (job_id, kind),
    CONSTRAINT bulk_job_artifacts_expiry CHECK (expires_at > created_at)
);

COMMENT ON TABLE bulk_job_artifacts IS
    'Private, formula-safe CSV artifacts of a bulk job (preview / errors / results). '
    'Served only by the API after a permission check; purged_at set by the retention task.';

CREATE INDEX IF NOT EXISTS ix_bulk_job_artifacts_expiry
    ON bulk_job_artifacts (expires_at) WHERE purged_at IS NULL;
