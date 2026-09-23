-- 0007 money ledger tables (Phase 1, stream A): payment reversals, payment and promise
-- event trails, promise allocations, promise cancellation columns.
--
-- Money in every new table is numeric(14,2). Legacy amounts ("Payments".amount,
-- "paymentDates".amount, clints.amount) stay double precision and are read as
-- round(amount::numeric, 2).
--
--   payment_reversals    one compensating event per approved payment (UNIQUE payment_id).
--                        The "Payments" row is never deleted: the reversal service also
--                        sets approval_status = false and is_deleted = true on it so every
--                        legacy total (which count approval_status = true, some also
--                        is_deleted = false) stops counting it. See review/phase1-money.md.
--   payment_events       submitted / approved / rejected / reversed / edited / deleted.
--                        Written by the v2 service (source 'api') and by the "Payments"
--                        trigger for legacy writes (source 'legacy', migration 0008).
--   promise_events       created / cancelled / allocated / deallocated / allocation_voided /
--                        edited / deleted.
--   promise_allocations  capacity ledger between an approved payment and a promise on the
--                        same case. Never deleted; voided (voided_at/by/reason) instead.
--                        Capacity on both sides is enforced by a trigger (0008).
--   "paymentDates"       + cancelled_at, cancelled_by, cancel_reason (NOT mapped in
--                        models.py, like `revision`; legacy serializers iterate mapped
--                        columns only, so no legacy response changes).
--
-- No foreign keys from the ledger tables to "Payments" / "paymentDates" / clints on
-- purpose (same reason as audit_events): the legacy DELETE /payments/<id> and DELETE
-- /clints (ON DELETE CASCADE) hard-delete those rows, and an FK would either make them
-- fail or silently delete money history. Integrity on insert is checked by triggers
-- (0008); a legacy hard delete voids the affected allocations and leaves the history.
--
-- Event and reversal tables are append-only (UPDATE / DELETE / TRUNCATE rejected).
--
-- Backfill ("imported baseline"): one 'submitted' event per existing payment, one
-- 'approved'/'rejected' event per decided payment, one 'created' event per existing
-- promise, all with source 'imported' and the legacy timestamps/actors. Existing
-- approved payments are NOT allocated to existing promises: old promises start with no
-- allocations.
--
-- Locks: new tables; the backfill only reads "Payments"/"paymentDates" (ACCESS SHARE):
-- 31,392 + 30,865 + 11,824 inserted rows on the full local copy, 0.33-0.52 s in total.
-- ALTER TABLE "paymentDates" ADD COLUMN (nullable, no default) is metadata-only but takes
-- an ACCESS EXCLUSIVE lock (lock_timeout 5s from the runner); it runs last, so the lock is
-- held only until the commit right after it. The CHECK constraint is added NOT VALID and
-- then validated (12k rows, all NULL).

-- ------------------------------------------------------------------ helpers
CREATE OR REPLACE FUNCTION cawp_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% is append-only: % is not allowed', TG_TABLE_NAME, TG_OP
        USING ERRCODE = 'insufficient_privilege',
              HINT = 'Record a new, compensating row instead.';
END $$;

COMMENT ON FUNCTION cawp_append_only() IS
    'BEFORE UPDATE/DELETE (row) and BEFORE TRUNCATE (statement) guard for append-only ledger tables.';

-- ------------------------------------------------------------------ payment_reversals
CREATE TABLE IF NOT EXISTS payment_reversals (
    id               uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    payment_id       uuid        NOT NULL,
    case_id          uuid        NOT NULL,
    amount           numeric(14,2) NOT NULL,
    reason           text        NOT NULL CHECK (btrim(reason) <> ''),
    reversed_by      uuid        NOT NULL,
    reversed_at      timestamptz NOT NULL DEFAULT now(),
    idempotency_key  text,
    request_id       text,
    CONSTRAINT uq_payment_reversals_payment UNIQUE (payment_id)
);

COMMENT ON TABLE payment_reversals IS
    'Compensating event for an approved payment (one per payment). The "Payments" row is kept '
    '(approval_status=false, is_deleted=true are set so legacy totals exclude it).';
COMMENT ON COLUMN payment_reversals.amount IS 'round("Payments".amount::numeric, 2) at reversal time';

CREATE INDEX IF NOT EXISTS ix_payment_reversals_case ON payment_reversals (case_id, reversed_at DESC);
CREATE INDEX IF NOT EXISTS ix_payment_reversals_reversed_at ON payment_reversals (reversed_at DESC);

-- ------------------------------------------------------------------ payment_events
CREATE TABLE IF NOT EXISTS payment_events (
    id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    payment_id       uuid        NOT NULL,
    case_id          uuid        NOT NULL,
    event            text        NOT NULL CHECK (event IN
                         ('submitted', 'approved', 'rejected', 'reversed', 'edited', 'deleted')),
    occurred_at      timestamptz NOT NULL DEFAULT now(),
    actor_user_id    uuid,
    amount           numeric(14,2),
    comment          text,
    source           text        NOT NULL CHECK (source IN ('api', 'legacy', 'imported')),
    request_id       text,
    idempotency_key  text
);

COMMENT ON TABLE payment_events IS
    'Payment lifecycle trail. source: api (v2 routes), legacy (trigger on "Payments"), imported (0007 backfill).';
COMMENT ON COLUMN payment_events.actor_user_id IS
    'approver for approved/rejected, recorder for submitted, reverser for reversed; NULL when a legacy '
    'edit/delete does not identify its caller';

CREATE INDEX IF NOT EXISTS ix_payment_events_payment ON payment_events (payment_id, occurred_at, id);
CREATE INDEX IF NOT EXISTS ix_payment_events_case ON payment_events (case_id, occurred_at DESC, id DESC);

-- ------------------------------------------------------------------ promise_events
CREATE TABLE IF NOT EXISTS promise_events (
    id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    promise_id       uuid        NOT NULL,
    case_id          uuid        NOT NULL,
    event            text        NOT NULL CHECK (event IN
                         ('created', 'cancelled', 'allocated', 'deallocated', 'allocation_voided',
                          'edited', 'deleted')),
    occurred_at      timestamptz NOT NULL DEFAULT now(),
    actor_user_id    uuid,
    amount           numeric(14,2),
    reason           text,
    allocation_id    uuid,
    payment_id       uuid,
    source           text        NOT NULL CHECK (source IN ('api', 'legacy', 'imported')),
    request_id       text,
    idempotency_key  text,
    CONSTRAINT ck_promise_events_cancel_reason CHECK (
        event NOT IN ('cancelled', 'deallocated') OR btrim(coalesce(reason, '')) <> '')
);

COMMENT ON TABLE promise_events IS
    'Promise-to-pay ("paymentDates") lifecycle and allocation trail. Cancelling and deallocating need a reason.';

CREATE INDEX IF NOT EXISTS ix_promise_events_promise ON promise_events (promise_id, occurred_at, id);
CREATE INDEX IF NOT EXISTS ix_promise_events_case ON promise_events (case_id, occurred_at DESC, id DESC);

-- ------------------------------------------------------------------ promise_allocations
CREATE TABLE IF NOT EXISTS promise_allocations (
    id               uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    promise_id       uuid        NOT NULL,
    payment_id       uuid        NOT NULL,
    case_id          uuid        NOT NULL,
    amount           numeric(14,2) NOT NULL CHECK (amount > 0),
    source           text        NOT NULL CHECK (source IN ('auto', 'manual')),
    note             text,
    created_by       uuid,
    created_at       timestamptz NOT NULL DEFAULT clock_timestamp(),
    voided_at        timestamptz,
    voided_by        uuid,
    void_reason      text,
    CONSTRAINT ck_promise_allocations_void CHECK (
        (voided_at IS NULL AND voided_by IS NULL AND void_reason IS NULL)
        OR (voided_at IS NOT NULL AND btrim(coalesce(void_reason, '')) <> ''))
);

COMMENT ON TABLE promise_allocations IS
    'Approved-payment evidence allocated to a promise on the same case. Per payment and per promise the '
    'sum of active (voided_at IS NULL) allocations never exceeds the amount (trigger, 0008). Rows are '
    'voided, never deleted.';
COMMENT ON COLUMN promise_allocations.created_at IS
    'clock_timestamp(): distinct, ordered times for the allocations one approval creates';
COMMENT ON COLUMN promise_allocations.source IS 'auto: on approval, oldest due promise first; manual: v2 allocate';
COMMENT ON COLUMN promise_allocations.voided_by IS 'NULL when voided by a legacy change (payment deleted/rejected/edited)';

CREATE INDEX IF NOT EXISTS ix_promise_allocations_promise_active
    ON promise_allocations (promise_id) INCLUDE (amount, created_at) WHERE voided_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_promise_allocations_payment_active
    ON promise_allocations (payment_id) INCLUDE (amount) WHERE voided_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_promise_allocations_promise ON promise_allocations (promise_id, created_at, id);
CREATE INDEX IF NOT EXISTS ix_promise_allocations_payment ON promise_allocations (payment_id, created_at, id);
CREATE INDEX IF NOT EXISTS ix_promise_allocations_case ON promise_allocations (case_id, created_at DESC);

-- ------------------------------------------------------------------ append-only guards
DROP TRIGGER IF EXISTS payment_reversals_append_only ON payment_reversals;
CREATE TRIGGER payment_reversals_append_only
    BEFORE UPDATE OR DELETE ON payment_reversals
    FOR EACH ROW EXECUTE FUNCTION cawp_append_only();
DROP TRIGGER IF EXISTS payment_reversals_no_truncate ON payment_reversals;
CREATE TRIGGER payment_reversals_no_truncate
    BEFORE TRUNCATE ON payment_reversals
    FOR EACH STATEMENT EXECUTE FUNCTION cawp_append_only();

DROP TRIGGER IF EXISTS payment_events_append_only ON payment_events;
CREATE TRIGGER payment_events_append_only
    BEFORE UPDATE OR DELETE ON payment_events
    FOR EACH ROW EXECUTE FUNCTION cawp_append_only();
DROP TRIGGER IF EXISTS payment_events_no_truncate ON payment_events;
CREATE TRIGGER payment_events_no_truncate
    BEFORE TRUNCATE ON payment_events
    FOR EACH STATEMENT EXECUTE FUNCTION cawp_append_only();

DROP TRIGGER IF EXISTS promise_events_append_only ON promise_events;
CREATE TRIGGER promise_events_append_only
    BEFORE UPDATE OR DELETE ON promise_events
    FOR EACH ROW EXECUTE FUNCTION cawp_append_only();
DROP TRIGGER IF EXISTS promise_events_no_truncate ON promise_events;
CREATE TRIGGER promise_events_no_truncate
    BEFORE TRUNCATE ON promise_events
    FOR EACH STATEMENT EXECUTE FUNCTION cawp_append_only();

-- allocations: UPDATE is allowed only to void (checked in 0008); DELETE/TRUNCATE never.
DROP TRIGGER IF EXISTS promise_allocations_no_delete ON promise_allocations;
CREATE TRIGGER promise_allocations_no_delete
    BEFORE DELETE ON promise_allocations
    FOR EACH ROW EXECUTE FUNCTION cawp_append_only();
DROP TRIGGER IF EXISTS promise_allocations_no_truncate ON promise_allocations;
CREATE TRIGGER promise_allocations_no_truncate
    BEFORE TRUNCATE ON promise_allocations
    FOR EACH STATEMENT EXECUTE FUNCTION cawp_append_only();

-- ------------------------------------------------------------------ imported baseline
INSERT INTO payment_events (payment_id, case_id, event, occurred_at, actor_user_id, amount, source)
SELECT p.id, p.clint_id, 'submitted', p.create_at, p.user_id, round(p.amount::numeric, 2), 'imported'
  FROM "Payments" p
 WHERE NOT EXISTS (SELECT 1 FROM payment_events e
                    WHERE e.payment_id = p.id AND e.event = 'submitted');

INSERT INTO payment_events (payment_id, case_id, event, occurred_at, actor_user_id, amount, comment, source)
SELECT p.id, p.clint_id,
       CASE WHEN p.approval_status THEN 'approved' ELSE 'rejected' END,
       coalesce(p.approval_at, p.update_at), p.approval_by, round(p.amount::numeric, 2),
       p.approval_comment, 'imported'
  FROM "Payments" p
 WHERE p.approval_status IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM payment_events e
                    WHERE e.payment_id = p.id AND e.event IN ('approved', 'rejected'));

INSERT INTO promise_events (promise_id, case_id, event, occurred_at, actor_user_id, amount, source)
SELECT d.id, d.clint_id, 'created', d.create_at, d.user_id, round(d.amount::numeric, 2), 'imported'
  FROM "paymentDates" d
 WHERE NOT EXISTS (SELECT 1 FROM promise_events e
                    WHERE e.promise_id = d.id AND e.event = 'created');

-- ------------------------------------------------------------------ promise cancellation
-- Last on purpose: ALTER TABLE takes ACCESS EXCLUSIVE on "paymentDates" and holds it until
-- commit, so it comes after the backfill and is held for milliseconds only.
ALTER TABLE "paymentDates" ADD COLUMN IF NOT EXISTS cancelled_at  timestamptz;
ALTER TABLE "paymentDates" ADD COLUMN IF NOT EXISTS cancelled_by  uuid;
ALTER TABLE "paymentDates" ADD COLUMN IF NOT EXISTS cancel_reason text;

COMMENT ON COLUMN "paymentDates".cancelled_at IS 'v2 promise cancellation (not mapped in models.py)';

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conname = 'ck_paymentdates_cancel'
                      AND conrelid = '"paymentDates"'::regclass) THEN
        ALTER TABLE "paymentDates" ADD CONSTRAINT ck_paymentdates_cancel CHECK (
            (cancelled_at IS NULL AND cancelled_by IS NULL AND cancel_reason IS NULL)
            OR (cancelled_at IS NOT NULL AND btrim(coalesce(cancel_reason, '')) <> '')) NOT VALID;
    END IF;
END $$;
ALTER TABLE "paymentDates" VALIDATE CONSTRAINT ck_paymentdates_cancel;
