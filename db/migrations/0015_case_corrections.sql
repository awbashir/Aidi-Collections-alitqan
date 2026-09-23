-- 0015 case_corrections (Phase 1, stream C): reviewed corrections to a case, separate from
-- ordinary edits.
--
--   kind     identity (identityNumber, accountNumber) | profile (name) |
--            attribute (keys of clints.attribute) | original_debt (clints.amount) |
--            delinquency ("delinquencyReason_id")
--   changes  {field: {"before": <value at request time, read from the DB>, "after": <new>}}
--   status   pending -> approved | rejected | cancelled (terminal)
--
-- Approval (services/case_history_corrections.py) locks the case row, re-reads every
-- field and refuses (409 stale) unless it still equals `before`, applies the change and
-- stores the case revision it produced in `applied_revision`. The table enforces the
-- lifecycle shape and that nobody approves their own request.
--
-- No FK to clints on purpose (the legacy DELETE /clints hard-deletes cases; the review
-- history must survive and must not make that legacy call fail), like audit_events.
--
-- Locks: new table only. Instant.

CREATE TABLE IF NOT EXISTS case_corrections (
    id                uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    case_id           uuid        NOT NULL,
    kind              text        NOT NULL
                      CHECK (kind IN ('identity', 'profile', 'attribute', 'original_debt', 'delinquency')),
    changes           jsonb       NOT NULL
                      CHECK (jsonb_typeof(changes) = 'object' AND changes <> '{}'::jsonb),
    reason            text        NOT NULL CHECK (btrim(reason) <> ''),
    status            text        NOT NULL DEFAULT 'pending'
                      CHECK (status IN ('pending', 'approved', 'rejected', 'cancelled')),
    requested_by      uuid        NOT NULL,
    requested_at      timestamptz NOT NULL DEFAULT now(),
    base_revision     integer,
    reviewed_by       uuid,
    reviewed_at       timestamptz,
    review_note       text,
    applied_revision  integer,
    revision          integer     NOT NULL DEFAULT 0,
    CONSTRAINT case_corrections_pending_unreviewed CHECK (
        status <> 'pending' OR (reviewed_by IS NULL AND reviewed_at IS NULL AND applied_revision IS NULL)),
    CONSTRAINT case_corrections_decided_fields CHECK (
        status = 'pending' OR (reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL)),
    CONSTRAINT case_corrections_applied_only_when_approved CHECK (
        (status = 'approved') = (applied_revision IS NOT NULL)),
    CONSTRAINT case_corrections_no_self_approval CHECK (
        status <> 'approved' OR reviewed_by <> requested_by),
    CONSTRAINT case_corrections_reject_note CHECK (
        status <> 'rejected' OR (review_note IS NOT NULL AND btrim(review_note) <> '')),
    CONSTRAINT case_corrections_cancel_by_requester CHECK (
        status <> 'cancelled' OR reviewed_by = requested_by)
);

COMMENT ON TABLE case_corrections IS
    'Reviewed case corrections (identity/profile/attribute/original_debt/delinquency). '
    'changes = {field: {before, after}}; reviewed_by/at = the approver/rejecter, or the '
    'requester for a cancellation. No FK to clints (legacy hard deletes).';
COMMENT ON COLUMN case_corrections.base_revision IS 'clints.revision when the request was made (informational)';
COMMENT ON COLUMN case_corrections.applied_revision IS 'clints.revision produced by applying the approved change';

-- review desk: status filter, newest first; case timeline; "my requests"
CREATE INDEX IF NOT EXISTS ix_case_corrections_status
    ON case_corrections (status, requested_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS ix_case_corrections_case
    ON case_corrections (case_id, requested_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS ix_case_corrections_requester
    ON case_corrections (requested_by, requested_at DESC);

DROP TRIGGER IF EXISTS case_corrections_bump_revision ON case_corrections;
CREATE TRIGGER case_corrections_bump_revision
    BEFORE UPDATE ON case_corrections
    FOR EACH ROW EXECUTE FUNCTION cawp_bump_revision();
