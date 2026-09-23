-- 0031 repayment plans (Phase 3, stream B): tables.
--
--   repayment_plans    one plan on one case: total, frequency, start date, installment count
--                      (or the requested installment amount), optional note. Stored status is
--                      'active' or 'cancelled' (with a reason); 'completed' and 'broken' are
--                      derived states (v_plan_state, 0033). At most ONE active plan per case
--                      (partial unique index uq_repayment_plans_one_active).
--   plan_installments  the generated schedule (seq 1..n, due date, exact amount). Immutable.
--                      The sum of a plan's installments equals its total and their number
--                      equals installment_count (deferred constraint trigger, 0032).
--   plan_allocations   capacity ledger between an approved payment and an installment of the
--                      same case. Separate from promise_allocations (0007): the same payment
--                      can be allocated in both ledgers. Never deleted; voided instead.
--                      Capacity on both sides is enforced by a trigger (0032):
--                        sum(active plan allocations of a payment)     <= payment amount
--                        sum(active plan allocations of an installment) <= installment amount
--   plan_events        created / cancelled / allocated / deallocated / allocation_voided.
--                      Append-only. Cancelling and deallocating need a reason.
--
-- Money is numeric(14,2). Plans do not change the debt and never count as recovered money:
-- nothing here writes to clints / "Payments".
--
-- No foreign keys to clints / "Payments" on purpose, as in 0007: legacy DELETE /payments/<id>
-- and DELETE /clints (ON DELETE CASCADE) hard-delete those rows; an FK would either break
-- those calls or cascade-delete plan history. A hard-deleted payment voids its plan
-- allocations through the "Payments" trigger (0032). Foreign keys inside the plan tables
-- (installment -> plan, allocation -> installment/plan) are kept: plan rows are never deleted.
--
-- Locks: new tables only. Instant.

-- ------------------------------------------------------------------ repayment_plans
CREATE TABLE IF NOT EXISTS repayment_plans (
    id                       uuid          PRIMARY KEY DEFAULT gen_random_uuid(),
    case_id                  uuid          NOT NULL,
    status                   text          NOT NULL DEFAULT 'active'
                                           CHECK (status IN ('active', 'cancelled')),
    frequency                text          NOT NULL CHECK (frequency IN ('weekly', 'monthly')),
    start_date               date          NOT NULL,
    total_amount             numeric(14,2) NOT NULL CHECK (total_amount > 0),
    installment_count        integer       NOT NULL CHECK (installment_count BETWEEN 1 AND 240),
    split_mode               text          NOT NULL CHECK (split_mode IN ('count', 'amount')),
    installment_amount       numeric(14,2) CHECK (installment_amount > 0),
    outstanding_at_creation  numeric(14,2) NOT NULL,
    note                     text,
    created_by               uuid,
    created_at               timestamptz   NOT NULL DEFAULT now(),
    cancelled_at             timestamptz,
    cancelled_by             uuid,
    cancel_reason            text,
    revision                 integer       NOT NULL DEFAULT 0,
    request_id               text,
    idempotency_key          text,
    CONSTRAINT ck_repayment_plans_within_balance CHECK (total_amount <= outstanding_at_creation),
    CONSTRAINT ck_repayment_plans_split CHECK ((split_mode = 'amount') = (installment_amount IS NOT NULL)),
    CONSTRAINT ck_repayment_plans_cancel CHECK (
        (status = 'active' AND cancelled_at IS NULL AND cancelled_by IS NULL AND cancel_reason IS NULL)
        OR (status = 'cancelled' AND cancelled_at IS NOT NULL AND btrim(coalesce(cancel_reason, '')) <> ''))
);

COMMENT ON TABLE repayment_plans IS
    'Repayment plan on one case (Phase 3 B). status active|cancelled; completed/broken are derived '
    '(v_plan_state). One active plan per case. Plans never change the debt.';
COMMENT ON COLUMN repayment_plans.split_mode IS
    'count: installment_count given, halalas spread one at a time from the first installment; '
    'amount: installment_amount given, every installment equals it except a smaller last one';
COMMENT ON COLUMN repayment_plans.installment_amount IS 'the requested installment amount (split_mode amount only)';
COMMENT ON COLUMN repayment_plans.outstanding_at_creation IS
    'exact outstanding balance (original - approved, non-reversed) when the plan was created';

CREATE UNIQUE INDEX IF NOT EXISTS uq_repayment_plans_one_active
    ON repayment_plans (case_id) WHERE status = 'active';
CREATE INDEX IF NOT EXISTS ix_repayment_plans_case ON repayment_plans (case_id, created_at DESC, id);
CREATE INDEX IF NOT EXISTS ix_repayment_plans_created ON repayment_plans (created_at DESC, id);

-- ------------------------------------------------------------------ plan_installments
CREATE TABLE IF NOT EXISTS plan_installments (
    id        uuid          PRIMARY KEY DEFAULT gen_random_uuid(),
    plan_id   uuid          NOT NULL REFERENCES repayment_plans (id),
    case_id   uuid          NOT NULL,
    seq       integer       NOT NULL CHECK (seq >= 1),
    due_date  date          NOT NULL,
    amount    numeric(14,2) NOT NULL CHECK (amount > 0),
    CONSTRAINT uq_plan_installments_seq UNIQUE (plan_id, seq)
);

COMMENT ON TABLE plan_installments IS
    'Generated schedule of a repayment plan (Asia/Riyadh civil due dates, exact amounts). Immutable.';

CREATE INDEX IF NOT EXISTS ix_plan_installments_due ON plan_installments (plan_id, due_date, seq);
CREATE INDEX IF NOT EXISTS ix_plan_installments_case_due ON plan_installments (case_id, due_date);

-- ------------------------------------------------------------------ plan_allocations
CREATE TABLE IF NOT EXISTS plan_allocations (
    id              uuid          PRIMARY KEY DEFAULT gen_random_uuid(),
    plan_id         uuid          NOT NULL REFERENCES repayment_plans (id),
    installment_id  uuid          NOT NULL REFERENCES plan_installments (id),
    payment_id      uuid          NOT NULL,
    case_id         uuid          NOT NULL,
    amount          numeric(14,2) NOT NULL CHECK (amount > 0),
    source          text          NOT NULL CHECK (source IN ('auto', 'manual')),
    note            text,
    created_by      uuid,
    created_at      timestamptz   NOT NULL DEFAULT clock_timestamp(),
    voided_at       timestamptz,
    voided_by       uuid,
    void_reason     text,
    CONSTRAINT ck_plan_allocations_void CHECK (
        (voided_at IS NULL AND voided_by IS NULL AND void_reason IS NULL)
        OR (voided_at IS NOT NULL AND btrim(coalesce(void_reason, '')) <> '')),
    CONSTRAINT ck_plan_allocations_manual_reason CHECK (
        source <> 'manual' OR btrim(coalesce(note, '')) <> '')
);

COMMENT ON TABLE plan_allocations IS
    'Approved-payment evidence allocated to a plan installment on the same case. Separate capacity '
    'ledger from promise_allocations. Per payment and per installment the sum of active allocations '
    'never exceeds the amount (trigger, 0032). Rows are voided, never deleted.';
COMMENT ON COLUMN plan_allocations.source IS 'auto: on approval, oldest due installment first; manual: v2 allocate (note = reason)';
COMMENT ON COLUMN plan_allocations.created_at IS 'clock_timestamp(): distinct, ordered times within one approval';

-- Every per-installment read is of ACTIVE rows (state views, capacity guard), so there is only
-- the partial covering index for installments; history is read per plan (ix_plan_allocations_plan).
CREATE INDEX IF NOT EXISTS ix_plan_allocations_installment_active
    ON plan_allocations (installment_id) INCLUDE (amount, created_at) WHERE voided_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_plan_allocations_payment_active
    ON plan_allocations (payment_id) INCLUDE (amount) WHERE voided_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_plan_allocations_payment ON plan_allocations (payment_id, created_at, id);
CREATE INDEX IF NOT EXISTS ix_plan_allocations_plan ON plan_allocations (plan_id, created_at, id);

-- ------------------------------------------------------------------ plan_events
CREATE TABLE IF NOT EXISTS plan_events (
    id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    plan_id          uuid          NOT NULL,
    case_id          uuid          NOT NULL,
    event            text          NOT NULL CHECK (event IN
                         ('created', 'cancelled', 'allocated', 'deallocated', 'allocation_voided')),
    occurred_at      timestamptz   NOT NULL DEFAULT now(),
    actor_user_id    uuid,
    amount           numeric(14,2),
    reason           text,
    installment_id   uuid,
    allocation_id    uuid,
    payment_id       uuid,
    details          jsonb,
    source           text          NOT NULL CHECK (source IN ('api', 'legacy')),
    request_id       text,
    idempotency_key  text,
    CONSTRAINT ck_plan_events_reason CHECK (
        event NOT IN ('cancelled', 'deallocated') OR btrim(coalesce(reason, '')) <> '')
);

COMMENT ON TABLE plan_events IS
    'Repayment plan trail. source api (v2 routes, and triggers inside a v2 money transaction) or legacy '
    '(triggers fired by a legacy payment write). Append-only.';

CREATE INDEX IF NOT EXISTS ix_plan_events_plan ON plan_events (plan_id, occurred_at, id);
CREATE INDEX IF NOT EXISTS ix_plan_events_case ON plan_events (case_id, occurred_at DESC, id DESC);

-- ------------------------------------------------------------------ append-only guards
-- cawp_append_only() comes from 0007.
DROP TRIGGER IF EXISTS plan_events_append_only ON plan_events;
CREATE TRIGGER plan_events_append_only
    BEFORE UPDATE OR DELETE ON plan_events
    FOR EACH ROW EXECUTE FUNCTION cawp_append_only();
DROP TRIGGER IF EXISTS plan_events_no_truncate ON plan_events;
CREATE TRIGGER plan_events_no_truncate
    BEFORE TRUNCATE ON plan_events
    FOR EACH STATEMENT EXECUTE FUNCTION cawp_append_only();

DROP TRIGGER IF EXISTS plan_installments_append_only ON plan_installments;
CREATE TRIGGER plan_installments_append_only
    BEFORE UPDATE OR DELETE ON plan_installments
    FOR EACH ROW EXECUTE FUNCTION cawp_append_only();
DROP TRIGGER IF EXISTS plan_installments_no_truncate ON plan_installments;
CREATE TRIGGER plan_installments_no_truncate
    BEFORE TRUNCATE ON plan_installments
    FOR EACH STATEMENT EXECUTE FUNCTION cawp_append_only();

-- plans: UPDATE only to cancel (checked in 0032); DELETE / TRUNCATE never.
DROP TRIGGER IF EXISTS repayment_plans_no_delete ON repayment_plans;
CREATE TRIGGER repayment_plans_no_delete
    BEFORE DELETE ON repayment_plans
    FOR EACH ROW EXECUTE FUNCTION cawp_append_only();
DROP TRIGGER IF EXISTS repayment_plans_no_truncate ON repayment_plans;
CREATE TRIGGER repayment_plans_no_truncate
    BEFORE TRUNCATE ON repayment_plans
    FOR EACH STATEMENT EXECUTE FUNCTION cawp_append_only();

-- allocations: UPDATE only to void (checked in 0032); DELETE / TRUNCATE never.
DROP TRIGGER IF EXISTS plan_allocations_no_delete ON plan_allocations;
CREATE TRIGGER plan_allocations_no_delete
    BEFORE DELETE ON plan_allocations
    FOR EACH ROW EXECUTE FUNCTION cawp_append_only();
DROP TRIGGER IF EXISTS plan_allocations_no_truncate ON plan_allocations;
CREATE TRIGGER plan_allocations_no_truncate
    BEFORE TRUNCATE ON plan_allocations
    FOR EACH STATEMENT EXECUTE FUNCTION cawp_append_only();
