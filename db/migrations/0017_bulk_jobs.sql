-- 0017 bulk_jobs (Phase 2, stream F): one reviewed bulk run (docs/phase2-spec.md).
--
-- Lifecycle (enforced here by CHECKs, driven by core/bulk and the worker):
--
--   phase 'prepare':  queued -> validating -> ready (preview) | failed
--                     (a read-only kind ends its preview as succeeded)
--                     ready -> expired after BULK_PREVIEW_TTL_HOURS
--   phase 'execute':  confirmed -> running -> succeeded | partially_succeeded | failed | cancelled
--   either phase:     validating/running -> retrying when the worker's lease expired (crash or
--                     restart); a worker resumes it (attempt + 1) until failed_attempts reaches
--                     max_attempts, then it is failed. queued/ready/confirmed/retrying can be
--                     cancelled at once; validating/running only between batches
--                     (cancel_requested_at).
--
-- Claiming: `FOR UPDATE SKIP LOCKED` over status IN (queued, confirmed, retrying) with no live
-- lease; the claimer sets locked_by / lease_until and renews the lease by heartbeat. A row in
-- validating/running always holds a lease, any other row never does.
--
-- `revision` is bumped by cawp_bump_revision() on every real change except the lease columns
-- (heartbeats must not make a preview stale). Confirmation compares it (expected_revision).
--
-- No FK to clints/users (legacy hard deletes); FKs only between the new bulk tables.
-- Locks: new table only. Instant.

CREATE TABLE IF NOT EXISTS bulk_jobs (
    id                   uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    kind                 text        NOT NULL CHECK (kind ~ '^[a-z][a-z0-9_]*$'),
    source               text        NOT NULL
                         CHECK (source IN ('selection', 'filter', 'spreadsheet', 'portfolio', 'query')),
    source_ref           jsonb       NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(source_ref) = 'object'),
    upload_id            uuid        REFERENCES bulk_uploads (id),
    sheet                text,
    mapping              jsonb       CHECK (mapping IS NULL OR jsonb_typeof(mapping) = 'object'),
    params               jsonb       NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(params) = 'object'),
    read_only            boolean     NOT NULL DEFAULT false,
    status               text        NOT NULL DEFAULT 'queued'
                         CHECK (status IN ('queued', 'validating', 'ready', 'expired', 'confirmed', 'running',
                                           'retrying', 'succeeded', 'partially_succeeded', 'failed', 'cancelled')),
    phase                text        NOT NULL DEFAULT 'prepare' CHECK (phase IN ('prepare', 'execute')),

    created_by           uuid        NOT NULL,
    created_at           timestamptz NOT NULL DEFAULT now(),
    updated_at           timestamptz NOT NULL DEFAULT now(),
    reopened_from        uuid        REFERENCES bulk_jobs (id),

    total_rows           integer     NOT NULL DEFAULT 0 CHECK (total_rows >= 0),
    prepared_rows        integer     NOT NULL DEFAULT 0 CHECK (prepared_rows >= 0),
    eligible_rows        integer     NOT NULL DEFAULT 0 CHECK (eligible_rows >= 0),
    excluded_rows        integer     NOT NULL DEFAULT 0 CHECK (excluded_rows >= 0),
    error_rows           integer     NOT NULL DEFAULT 0 CHECK (error_rows >= 0),
    processed_rows       integer     NOT NULL DEFAULT 0 CHECK (processed_rows >= 0),
    succeeded_rows       integer     NOT NULL DEFAULT 0 CHECK (succeeded_rows >= 0),
    skipped_rows         integer     NOT NULL DEFAULT 0 CHECK (skipped_rows >= 0),
    failed_rows          integer     NOT NULL DEFAULT 0 CHECK (failed_rows >= 0),

    preview_ready_at     timestamptz,
    preview_expires_at   timestamptz,
    confirmed_by         uuid,
    confirmed_at         timestamptz,
    accept_partial       boolean,
    retried_by           uuid,
    retried_at           timestamptz,
    retry_count          integer     NOT NULL DEFAULT 0 CHECK (retry_count >= 0),
    cancel_requested_by  uuid,
    cancel_requested_at  timestamptz,
    cancel_reason        text,
    cancelled_at         timestamptz,
    execution_started_at timestamptz,
    started_at           timestamptz,
    finished_at          timestamptz,

    attempt              integer     NOT NULL DEFAULT 0 CHECK (attempt >= 0),
    max_attempts         integer     NOT NULL DEFAULT 3 CHECK (max_attempts >= 1),
    failed_attempts      integer     NOT NULL DEFAULT 0 CHECK (failed_attempts >= 0),
    locked_by            text,
    lease_until          timestamptz,
    heartbeat_at         timestamptz,
    error_code           text,
    error_message        text,
    revision             integer     NOT NULL DEFAULT 0,

    CONSTRAINT bulk_jobs_spreadsheet_has_upload CHECK ((source = 'spreadsheet') = (upload_id IS NOT NULL)),
    CONSTRAINT bulk_jobs_phase_of_status CHECK (
        (status NOT IN ('queued', 'validating', 'ready', 'expired') OR phase = 'prepare')
        AND (status NOT IN ('confirmed', 'running', 'partially_succeeded') OR phase = 'execute')),
    CONSTRAINT bulk_jobs_execute_is_confirmed CHECK (
        phase <> 'execute' OR (confirmed_by IS NOT NULL AND confirmed_at IS NOT NULL AND accept_partial IS NOT NULL)),
    CONSTRAINT bulk_jobs_lease_while_working CHECK (
        (status IN ('validating', 'running')) = (locked_by IS NOT NULL AND lease_until IS NOT NULL)),
    CONSTRAINT bulk_jobs_finished_when_terminal CHECK (
        (status IN ('succeeded', 'partially_succeeded', 'failed', 'cancelled', 'expired')) = (finished_at IS NOT NULL)),
    CONSTRAINT bulk_jobs_ready_has_preview CHECK (
        status <> 'ready' OR (preview_ready_at IS NOT NULL AND preview_expires_at IS NOT NULL)),
    CONSTRAINT bulk_jobs_cancelled_at CHECK ((status = 'cancelled') = (cancelled_at IS NOT NULL))
);

COMMENT ON TABLE bulk_jobs IS
    'Reviewed bulk jobs (Phase 2): source -> validation -> preview -> confirmation -> '
    'bounded idempotent execution. Driven by the worker service (python -m worker).';
COMMENT ON COLUMN bulk_jobs.source_ref IS
    'selection: {case_ids[]}; filter: {list, query[[k,v]...]}; portfolio: {portfolio_id}; '
    'spreadsheet: {upload_id, sheet}; query: kind-defined';
COMMENT ON COLUMN bulk_jobs.failed_attempts IS
    'attempts that ended by crash (lease expired) or error; a graceful shutdown does not count. '
    'Reset by a manual retry.';

-- jobs screen: newest first, per creator, per kind; the worker's claim and lease recovery
CREATE INDEX IF NOT EXISTS ix_bulk_jobs_created ON bulk_jobs (created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS ix_bulk_jobs_creator ON bulk_jobs (created_by, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_bulk_jobs_kind ON bulk_jobs (kind, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_bulk_jobs_claimable
    ON bulk_jobs (created_at) WHERE status IN ('queued', 'confirmed', 'retrying');
CREATE INDEX IF NOT EXISTS ix_bulk_jobs_leased
    ON bulk_jobs (lease_until) WHERE status IN ('validating', 'running');
CREATE INDEX IF NOT EXISTS ix_bulk_jobs_ready_expiry
    ON bulk_jobs (preview_expires_at) WHERE status = 'ready';
CREATE INDEX IF NOT EXISTS ix_bulk_jobs_upload ON bulk_jobs (upload_id) WHERE upload_id IS NOT NULL;

DROP TRIGGER IF EXISTS bulk_jobs_bump_revision ON bulk_jobs;
CREATE TRIGGER bulk_jobs_bump_revision
    BEFORE UPDATE ON bulk_jobs
    FOR EACH ROW EXECUTE FUNCTION cawp_bump_revision('locked_by', 'lease_until', 'heartbeat_at', 'updated_at');
