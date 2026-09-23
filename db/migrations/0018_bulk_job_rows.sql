-- 0018 bulk_job_rows (Phase 2, stream F): one row per source row or selected case of a job.
--
--   row_no             the spreadsheet row number (as Excel shows it) or the 1-based position
--                      in the selection / filter / portfolio / query result
--   raw                the source row as read ({column header: text}); kept for the preview
--                      and the error file. Formula cells hold their formula text.
--   mapped             {target field: text} after the explicit column mapping
--   match_value        the value that identified the case (account number, case id, ...)
--   case_id            the matched case (no FK: legacy hard deletes)
--   eligibility        pending (only while validating) | eligible | excluded | error
--   expected_revision  clints.revision seen by the preview; execution skips the row as
--                      case_changed when it differs (never forced)
--   outcome            pending | succeeded | skipped | failed, written in the SAME transaction
--                      as the business change of its batch, so a re-run skips done rows
--
-- Locks: new table only. Instant.

CREATE TABLE IF NOT EXISTS bulk_job_rows (
    id                   bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    job_id               uuid        NOT NULL REFERENCES bulk_jobs (id) ON DELETE CASCADE,
    row_no               integer     NOT NULL CHECK (row_no >= 1),
    raw                  jsonb       NOT NULL DEFAULT '{}'::jsonb,
    mapped               jsonb       NOT NULL DEFAULT '{}'::jsonb,
    match_value          text,
    case_id              uuid,
    eligibility          text        NOT NULL DEFAULT 'pending'
                         CHECK (eligibility IN ('pending', 'eligible', 'excluded', 'error')),
    eligibility_code     text,
    eligibility_message  text,
    expected_revision    integer,
    details              jsonb,
    outcome              text        NOT NULL DEFAULT 'pending'
                         CHECK (outcome IN ('pending', 'succeeded', 'skipped', 'failed')),
    outcome_code         text,
    outcome_message      text,
    processed_at         timestamptz,
    attempt              integer,
    CONSTRAINT bulk_job_rows_row_no_unique UNIQUE (job_id, row_no),
    CONSTRAINT bulk_job_rows_eligible_has_case CHECK (
        eligibility <> 'eligible' OR (case_id IS NOT NULL AND expected_revision IS NOT NULL)),
    CONSTRAINT bulk_job_rows_not_eligible_has_code CHECK (
        eligibility NOT IN ('excluded', 'error') OR eligibility_code IS NOT NULL),
    CONSTRAINT bulk_job_rows_processed CHECK ((outcome = 'pending') = (processed_at IS NULL)),
    CONSTRAINT bulk_job_rows_success_only_eligible CHECK (outcome <> 'succeeded' OR eligibility = 'eligible'),
    CONSTRAINT bulk_job_rows_outcome_code CHECK (outcome IN ('pending', 'succeeded') OR outcome_code IS NOT NULL)
);

COMMENT ON TABLE bulk_job_rows IS
    'Rows of a reviewed bulk job: source values, matched case, eligibility at preview and the '
    'per-case outcome of execution (written atomically with the business change).';

-- preview tabs / execution batches (row order), case lookup inside a job
CREATE INDEX IF NOT EXISTS ix_bulk_job_rows_eligibility ON bulk_job_rows (job_id, eligibility, row_no);
CREATE INDEX IF NOT EXISTS ix_bulk_job_rows_pending
    ON bulk_job_rows (job_id, row_no) WHERE outcome = 'pending' AND eligibility = 'eligible';
CREATE INDEX IF NOT EXISTS ix_bulk_job_rows_outcome ON bulk_job_rows (job_id, outcome, row_no);
CREATE INDEX IF NOT EXISTS ix_bulk_job_rows_case ON bulk_job_rows (job_id, case_id);
