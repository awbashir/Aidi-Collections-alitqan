-- 0028 internal requests (Phase 3, stream A; docs/phase3-spec.md "Stream A", PROJECT-NOTES §2.6).
--
-- A collector raises a case-linked request routed to legal, finance or operations. It has a
-- subject, a body, an optional amount (exact numeric(14,2)) and an optional civil date
-- (Asia/Riyadh) where relevant, and optional references to existing case documents
-- (request_attachments, 0029; no new file storage).
--
-- Lifecycle (enforced here by requests_guard, and by services/requests.py with 409s):
--
--     open ──take──> in_review ──resolve──> resolved
--       │                ^                     │
--       └──cancel──>     └──────reopen─────────┘
--        cancelled
--
--   * resolve, reopen and cancel each need a reason (stored on the event, 0029; the latest
--     resolution / cancel reason is also kept on the row for display);
--   * cancel: by the requester, only while open (CHECK requests_cancel_by_requester);
--   * nobody takes or resolves their own request (CHECK requests_no_self_review);
--   * a reopened request goes back to in_review (reopen_count + 1).
--
-- No FK to clints on purpose (like case_corrections / audit_events): the legacy
-- DELETE /clints hard-deletes cases, and the request and its trail must survive that and
-- must not make the legacy call fail. No FK to users (legacy hard-deletes users).
--
-- The row is never deleted (requests_no_delete): the event trail references it.
-- `revision` is bumped by cawp_bump_revision (0006) on real changes, ignoring
-- `last_event_at` / `updated_at`, so a comment (which only touches last_event_at) never
-- makes a reviewer's pending action stale.
--
-- Locks: new table only. Instant.

CREATE TABLE IF NOT EXISTS requests (
    id              uuid          PRIMARY KEY DEFAULT gen_random_uuid(),
    case_id         uuid          NOT NULL,
    department      text          NOT NULL CHECK (department IN ('legal', 'finance', 'operations')),
    subject         text          NOT NULL CHECK (btrim(subject) <> '' AND length(subject) <= 200),
    body            text          NOT NULL CHECK (btrim(body) <> '' AND length(body) <= 5000),
    amount          numeric(14,2) CHECK (amount IS NULL OR amount > 0),
    related_date    date,         -- a Riyadh civil date the request is about (hearing, deadline, ...)
    status          text          NOT NULL DEFAULT 'open'
                    CHECK (status IN ('open', 'in_review', 'resolved', 'cancelled')),
    requested_by    uuid          NOT NULL,
    created_at      timestamptz   NOT NULL DEFAULT now(),
    reviewer_id     uuid,         -- who took it into review (kept after resolve / reopen)
    review_started_at timestamptz,
    resolved_by     uuid,
    resolved_at     timestamptz,
    resolution      text,         -- the resolve reason
    cancelled_by    uuid,
    cancelled_at    timestamptz,
    cancel_reason   text,
    reopen_count    integer       NOT NULL DEFAULT 0 CHECK (reopen_count >= 0),
    last_event_at   timestamptz   NOT NULL DEFAULT now(),
    updated_at      timestamptz   NOT NULL DEFAULT now(),
    revision        integer       NOT NULL DEFAULT 0,
    CONSTRAINT requests_open_unreviewed CHECK (
        status <> 'open' OR (reviewer_id IS NULL AND resolved_at IS NULL AND cancelled_at IS NULL)),
    CONSTRAINT requests_in_review_has_reviewer CHECK (
        status <> 'in_review' OR (reviewer_id IS NOT NULL AND review_started_at IS NOT NULL
                                  AND resolved_at IS NULL AND cancelled_at IS NULL)),
    CONSTRAINT requests_resolved_fields CHECK (
        (status = 'resolved') = (resolved_at IS NOT NULL AND resolved_by IS NOT NULL
                                 AND resolution IS NOT NULL AND btrim(resolution) <> '')),
    CONSTRAINT requests_cancelled_fields CHECK (
        (status = 'cancelled') = (cancelled_at IS NOT NULL AND cancelled_by IS NOT NULL
                                  AND cancel_reason IS NOT NULL AND btrim(cancel_reason) <> '')),
    CONSTRAINT requests_cancel_by_requester CHECK (status <> 'cancelled' OR cancelled_by = requested_by),
    CONSTRAINT requests_no_self_review CHECK (
        (reviewer_id IS NULL OR reviewer_id <> requested_by)
        AND (resolved_by IS NULL OR resolved_by <> requested_by))
);

COMMENT ON TABLE requests IS
    'Phase 3 internal requests (db/migrations/0028): case-linked, routed to legal / finance / operations. '
    'open -> in_review -> resolved, reopen -> in_review, cancel (requester, while open). '
    'Trail in request_events (append-only). No FK to clints (legacy hard deletes). Never deleted.';
COMMENT ON COLUMN requests.related_date IS 'optional Asia/Riyadh civil date the request concerns';
COMMENT ON COLUMN requests.amount IS 'optional exact amount (SAR), > 0';
COMMENT ON COLUMN requests.last_event_at IS 'time of the latest event (comments included); does not bump revision';

-- ---------------------------------------------------------------------------------------
-- guard: legal status transitions and immutable core fields
-- ---------------------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION requests_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'requests are never deleted (cancel or resolve them instead)'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF (NEW.id, NEW.case_id, NEW.department, NEW.subject, NEW.body, NEW.amount, NEW.related_date,
        NEW.requested_by, NEW.created_at)
       IS DISTINCT FROM
       (OLD.id, OLD.case_id, OLD.department, OLD.subject, OLD.body, OLD.amount, OLD.related_date,
        OLD.requested_by, OLD.created_at) THEN
        RAISE EXCEPTION 'requests: case, department, subject, body, amount, date, requester and '
                        'creation time are immutable'
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.status IS DISTINCT FROM OLD.status AND NOT (
           (OLD.status = 'open'      AND NEW.status IN ('in_review', 'cancelled'))
        OR (OLD.status = 'in_review' AND NEW.status = 'resolved')
        OR (OLD.status = 'resolved'  AND NEW.status = 'in_review')) THEN
        RAISE EXCEPTION 'requests: illegal status transition % -> %', OLD.status, NEW.status
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.status = 'in_review' AND OLD.status = 'resolved' AND NEW.reopen_count <> OLD.reopen_count + 1 THEN
        RAISE EXCEPTION 'requests: a reopen must increment reopen_count'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;

COMMENT ON FUNCTION requests_guard() IS
    'BEFORE UPDATE/DELETE on requests: only open->in_review|cancelled, in_review->resolved, '
    'resolved->in_review (reopen); core fields immutable; DELETE rejected.';

DROP TRIGGER IF EXISTS requests_guard_upd ON requests;
CREATE TRIGGER requests_guard_upd
    BEFORE UPDATE OR DELETE ON requests
    FOR EACH ROW EXECUTE FUNCTION requests_guard();

CREATE OR REPLACE FUNCTION requests_no_truncate() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% is append-only: TRUNCATE is not allowed', TG_TABLE_NAME
        USING ERRCODE = 'insufficient_privilege';
END $$;

DROP TRIGGER IF EXISTS requests_no_truncate ON requests;
CREATE TRIGGER requests_no_truncate
    BEFORE TRUNCATE ON requests
    FOR EACH STATEMENT EXECUTE FUNCTION requests_no_truncate();

-- revision: same generic function as the other tables; comments only touch last_event_at
DROP TRIGGER IF EXISTS requests_bump_revision ON requests;
CREATE TRIGGER requests_bump_revision
    BEFORE UPDATE ON requests
    FOR EACH ROW EXECUTE FUNCTION cawp_bump_revision('last_event_at', 'updated_at');
