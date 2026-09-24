-- 0032 repayment plan rules in the database (Phase 3, stream B).
--
--   repayment_plans_guard         BEFORE INSERT/UPDATE on repayment_plans: a plan is inserted
--                                 'active'; the only UPDATE allowed is active -> cancelled (with
--                                 cancelled_at/by/reason), which bumps `revision`. (CW108)
--   plan_installments_guard       BEFORE INSERT on plan_installments: the plan exists, same case,
--                                 1 <= seq <= installment_count. With UNIQUE (plan_id, seq) a
--                                 plan can never get more installments than it declares. (CW109)
--   repayment_plans_schedule_check  DEFERRED constraint trigger (end of the creating
--                                 transaction): count = installment_count, sum = total_amount,
--                                 seq 1 is due on start_date, due dates strictly increase. (CW109)
--   plan_allocations_guard        BEFORE INSERT/UPDATE on plan_allocations:
--                                  * INSERT: locks the case row (FOR NO KEY UPDATE), then the
--                                    payment row FOR SHARE, then checks: same case and plan;
--                                    payment approved / not deleted / not reversed; plan active;
--                                    capacity on BOTH sides:
--                                      sum(active plan allocations of the payment)     <= payment amount
--                                      sum(active plan allocations of the installment) <= installment amount
--                                  * UPDATE: only voiding an active row is allowed.
--   plans_auto_allocate(p, actor) allocates a payment's remaining PLAN capacity to the case's
--                                 active plan, oldest due installment first (source auto).
--   plans_void_allocations(...)   voids the active plan allocations of a payment.
--   payments_plans_trg            AFTER INSERT/UPDATE/DELETE on "Payments": auto-allocate on
--                                 approval (legacy and v2), void on reversal / rejection of an
--                                 approved payment / hard delete / case move / amount shrink.
--
-- Coexistence with the promise ledger (0008). The same approval allocates to BOTH ledgers,
-- independently: payments_money_trg fires first (triggers of one event fire in name order:
-- "payments_money_trg" < "payments_plans_trg"), then this one. Neither reads the other's
-- ledger. Lock order is the same everywhere: the case row first (every v2 money write and the
-- fixed legacy approve lock it before the payment row), then the payment row, then ledger
-- rows. The void paths take no case lock (voiding never needs capacity), exactly like 0008,
-- so a legacy reject/delete that holds the payment row never waits for the case row.
--
-- The payment row is locked FOR SHARE by the allocation guard. That closes a race the
-- promise guard leaves open: a manual allocation reads an approved payment while a concurrent
-- legacy reject of that payment voids "all active allocations" before the new row is visible.
-- With FOR SHARE the reject's UPDATE waits for the allocation to commit (and then voids it),
-- or the allocation waits for the reject and then sees the payment is no longer approved.
--
-- Error codes (mapped to HTTP by services/plans.py):
--   CW101 payment plan-capacity exceeded   CW102 installment capacity exceeded
--   CW103 payment not allocatable          CW104 installment not allocatable (plan cancelled)
--   CW105 payment/installment/plan on another case or plan
--   CW106 row not found                    CW107 allocation is immutable
--   CW108 plan is immutable                CW109 schedule does not match the plan
--
-- Locks: CREATE TRIGGER on "Payments" takes SHARE ROW EXCLUSIVE briefly (lock_timeout 5s).
-- Everything else is new objects. Instant.

-- ------------------------------------------------------------------ plan guard
CREATE OR REPLACE FUNCTION repayment_plans_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    keep text[] := ARRAY['status', 'cancelled_at', 'cancelled_by', 'cancel_reason', 'revision'];
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.status <> 'active' THEN
            RAISE EXCEPTION 'a repayment plan is created active' USING ERRCODE = 'CW108';
        END IF;
        NEW.revision := 0;
        RETURN NEW;
    END IF;
    IF OLD.status <> 'active' OR NEW.status <> 'cancelled'
       OR (to_jsonb(NEW) - keep) IS DISTINCT FROM (to_jsonb(OLD) - keep) THEN
        RAISE EXCEPTION 'repayment plan % can only be cancelled once', OLD.id USING ERRCODE = 'CW108';
    END IF;
    NEW.revision := OLD.revision + 1;
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS repayment_plans_guard ON repayment_plans;
CREATE TRIGGER repayment_plans_guard
    BEFORE INSERT OR UPDATE ON repayment_plans
    FOR EACH ROW EXECUTE FUNCTION repayment_plans_guard();

-- ------------------------------------------------------------------ schedule integrity
CREATE OR REPLACE FUNCTION plan_installments_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    p record;
BEGIN
    SELECT rp.case_id, rp.installment_count INTO p FROM repayment_plans rp WHERE rp.id = NEW.plan_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'plan % not found', NEW.plan_id USING ERRCODE = 'CW106';
    END IF;
    IF NEW.case_id <> p.case_id OR NEW.seq > p.installment_count THEN
        RAISE EXCEPTION 'installment % does not fit plan % (case or seq)', NEW.seq, NEW.plan_id
            USING ERRCODE = 'CW109';
    END IF;
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS plan_installments_guard ON plan_installments;
CREATE TRIGGER plan_installments_guard
    BEFORE INSERT ON plan_installments
    FOR EACH ROW EXECUTE FUNCTION plan_installments_guard();

CREATE OR REPLACE FUNCTION repayment_plans_schedule_check() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    p record;
    s record;
BEGIN
    SELECT rp.id, rp.total_amount, rp.installment_count, rp.start_date INTO p
      FROM repayment_plans rp WHERE rp.id = NEW.id;
    SELECT count(*) AS n, coalesce(sum(i.amount), 0) AS total,
           min(i.due_date) FILTER (WHERE i.seq = 1) AS first_due,
           bool_and(i.due_date > i.prev_due OR i.prev_due IS NULL) AS increasing
      INTO s
      FROM (SELECT seq, amount, due_date, lag(due_date) OVER (ORDER BY seq) AS prev_due
              FROM plan_installments WHERE plan_id = p.id) i;
    IF s.n <> p.installment_count OR s.total <> p.total_amount
       OR s.first_due IS DISTINCT FROM p.start_date OR NOT s.increasing THEN
        RAISE EXCEPTION 'schedule of plan % does not match it: % installments totalling % (expected % / %)',
                        p.id, s.n, s.total, p.installment_count, p.total_amount
            USING ERRCODE = 'CW109';
    END IF;
    RETURN NULL;
END $$;

DROP TRIGGER IF EXISTS repayment_plans_schedule_check ON repayment_plans;
CREATE CONSTRAINT TRIGGER repayment_plans_schedule_check
    AFTER INSERT ON repayment_plans
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION repayment_plans_schedule_check();

-- ------------------------------------------------------------------ allocation guard
CREATE OR REPLACE FUNCTION plan_allocations_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    pay        record;
    inst       record;
    used_pay   numeric;
    used_inst  numeric;
BEGIN
    IF TG_OP = 'UPDATE' THEN
        IF OLD.voided_at IS NOT NULL THEN
            RAISE EXCEPTION 'plan allocation % is already voided', OLD.id USING ERRCODE = 'CW107';
        END IF;
        IF NEW.voided_at IS NULL
           OR (NEW.id, NEW.plan_id, NEW.installment_id, NEW.payment_id, NEW.case_id, NEW.amount,
               NEW.source, NEW.note, NEW.created_by, NEW.created_at)
              IS DISTINCT FROM
              (OLD.id, OLD.plan_id, OLD.installment_id, OLD.payment_id, OLD.case_id, OLD.amount,
               OLD.source, OLD.note, OLD.created_by, OLD.created_at) THEN
            RAISE EXCEPTION 'plan allocation % can only be voided', OLD.id USING ERRCODE = 'CW107';
        END IF;
        RETURN NEW;
    END IF;

    -- INSERT. Lock order: case row, then payment row (same as every money writer).
    PERFORM 1 FROM clints WHERE id = NEW.case_id FOR NO KEY UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'case % not found', NEW.case_id USING ERRCODE = 'CW106';
    END IF;

    SELECT p.clint_id, round(p.amount::numeric, 2) AS amount, p.approval_status, p.is_deleted
      INTO pay FROM "Payments" p WHERE p.id = NEW.payment_id FOR SHARE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'payment % not found', NEW.payment_id USING ERRCODE = 'CW106';
    END IF;
    IF pay.clint_id <> NEW.case_id THEN
        RAISE EXCEPTION 'payment % belongs to another case', NEW.payment_id USING ERRCODE = 'CW105';
    END IF;
    IF pay.approval_status IS NOT TRUE OR pay.is_deleted
       OR EXISTS (SELECT 1 FROM payment_reversals r WHERE r.payment_id = NEW.payment_id) THEN
        RAISE EXCEPTION 'payment % is not an approved, non-reversed payment', NEW.payment_id
            USING ERRCODE = 'CW103';
    END IF;

    SELECT i.plan_id, i.case_id, i.amount, rp.status
      INTO inst
      FROM plan_installments i JOIN repayment_plans rp ON rp.id = i.plan_id
     WHERE i.id = NEW.installment_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'installment % not found', NEW.installment_id USING ERRCODE = 'CW106';
    END IF;
    IF inst.case_id <> NEW.case_id OR inst.plan_id <> NEW.plan_id THEN
        RAISE EXCEPTION 'installment % belongs to another case or plan', NEW.installment_id
            USING ERRCODE = 'CW105';
    END IF;
    IF inst.status <> 'active' THEN
        RAISE EXCEPTION 'plan % is cancelled', NEW.plan_id USING ERRCODE = 'CW104';
    END IF;

    SELECT coalesce(sum(a.amount), 0) INTO used_pay
      FROM plan_allocations a WHERE a.payment_id = NEW.payment_id AND a.voided_at IS NULL;
    IF used_pay + NEW.amount > pay.amount THEN
        RAISE EXCEPTION 'payment plan-capacity exceeded: % already allocated + % > %',
                        used_pay, NEW.amount, pay.amount
            USING ERRCODE = 'CW101', DETAIL = greatest(pay.amount - used_pay, 0)::text;
    END IF;

    SELECT coalesce(sum(a.amount), 0) INTO used_inst
      FROM plan_allocations a WHERE a.installment_id = NEW.installment_id AND a.voided_at IS NULL;
    IF used_inst + NEW.amount > inst.amount THEN
        RAISE EXCEPTION 'installment capacity exceeded: % already allocated + % > %',
                        used_inst, NEW.amount, inst.amount
            USING ERRCODE = 'CW102', DETAIL = greatest(inst.amount - used_inst, 0)::text;
    END IF;
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS plan_allocations_guard ON plan_allocations;
CREATE TRIGGER plan_allocations_guard
    BEFORE INSERT OR UPDATE ON plan_allocations
    FOR EACH ROW EXECUTE FUNCTION plan_allocations_guard();

-- ------------------------------------------------------------------ void / auto-allocate
CREATE OR REPLACE FUNCTION plans_void_allocations(p_payment_id uuid, p_actor uuid, p_reason text,
                                                  p_event text DEFAULT 'allocation_voided')
RETURNS integer
LANGUAGE plpgsql AS $$
DECLARE
    n integer;
BEGIN
    WITH v AS (
        UPDATE plan_allocations a
           SET voided_at = now(), voided_by = p_actor, void_reason = p_reason
         WHERE a.payment_id = p_payment_id AND a.voided_at IS NULL
        RETURNING a.id, a.plan_id, a.installment_id, a.payment_id, a.case_id, a.amount
    ), e AS (
        INSERT INTO plan_events (plan_id, case_id, event, actor_user_id, amount, reason,
                                 installment_id, allocation_id, payment_id, source)
        SELECT v.plan_id, v.case_id, p_event, p_actor, v.amount, p_reason, v.installment_id, v.id,
               v.payment_id, CASE WHEN cawp_money_app_write() THEN 'api' ELSE 'legacy' END
          FROM v
        RETURNING 1
    )
    SELECT count(*) INTO n FROM e;
    RETURN n;
END $$;

COMMENT ON FUNCTION plans_void_allocations(uuid, uuid, text, text) IS
    'Void the active plan allocations of a payment; one plan_events row per voided allocation. '
    'Takes no case lock (voiding never needs capacity). Returns the number voided.';

CREATE OR REPLACE FUNCTION plans_auto_allocate(p_payment_id uuid, p_actor uuid)
RETURNS integer
LANGUAGE plpgsql AS $$
DECLARE
    pay        record;
    inst       record;
    v_plan     uuid;
    remaining  numeric(14,2);
    take       numeric(14,2);
    alloc_id   uuid;
    n          integer := 0;
    src        text := CASE WHEN cawp_money_app_write() THEN 'api' ELSE 'legacy' END;
BEGIN
    SELECT p.id, p.clint_id, round(p.amount::numeric, 2) AS amount, p.approval_status, p.is_deleted
      INTO pay FROM "Payments" p WHERE p.id = p_payment_id;
    IF NOT FOUND OR pay.approval_status IS NOT TRUE OR pay.is_deleted OR pay.amount <= 0
       OR EXISTS (SELECT 1 FROM payment_reversals r WHERE r.payment_id = p_payment_id) THEN
        RETURN 0;
    END IF;
    -- case row first (already held by every approval path; money_auto_allocate took it too)
    PERFORM 1 FROM clints WHERE id = pay.clint_id FOR NO KEY UPDATE;
    SELECT rp.id INTO v_plan FROM repayment_plans rp
     WHERE rp.case_id = pay.clint_id AND rp.status = 'active';
    IF NOT FOUND THEN
        RETURN 0;
    END IF;

    SELECT pay.amount - coalesce(sum(a.amount), 0) INTO remaining
      FROM plan_allocations a WHERE a.payment_id = p_payment_id AND a.voided_at IS NULL;

    FOR inst IN
        SELECT i.id,
               i.amount - coalesce((SELECT sum(a.amount) FROM plan_allocations a
                                     WHERE a.installment_id = i.id AND a.voided_at IS NULL), 0) AS capacity
          FROM plan_installments i
         WHERE i.plan_id = v_plan
         ORDER BY i.due_date, i.seq
    LOOP
        EXIT WHEN remaining <= 0;
        CONTINUE WHEN inst.capacity <= 0;
        take := least(remaining, inst.capacity);
        INSERT INTO plan_allocations (plan_id, installment_id, payment_id, case_id, amount, source, created_by)
        VALUES (v_plan, inst.id, p_payment_id, pay.clint_id, take, 'auto', p_actor)
        RETURNING id INTO alloc_id;
        INSERT INTO plan_events (plan_id, case_id, event, actor_user_id, amount, installment_id,
                                 allocation_id, payment_id, source)
        VALUES (v_plan, pay.clint_id, 'allocated', p_actor, take, inst.id, alloc_id, p_payment_id, src);
        remaining := remaining - take;
        n := n + 1;
    END LOOP;
    RETURN n;
END $$;

COMMENT ON FUNCTION plans_auto_allocate(uuid, uuid) IS
    'Allocate an approved payment''s remaining plan capacity to its case''s active plan, oldest due '
    'installment first (source auto). Independent of the promise ledger. Returns the number created.';

-- ------------------------------------------------------------------ "Payments" trigger
CREATE OR REPLACE FUNCTION payments_plans_trg() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    was_ok  boolean;
    is_ok   boolean;
    rev     record;
    actor   uuid;
    why     text;
BEGIN
    IF TG_OP = 'DELETE' THEN
        PERFORM plans_void_allocations(OLD.id, NULL, 'payment hard-deleted');
        RETURN NULL;
    END IF;

    IF TG_OP = 'INSERT' THEN
        IF NEW.approval_status IS TRUE AND NOT NEW.is_deleted THEN   -- legacy POST /payment accepts a decided row
            PERFORM plans_auto_allocate(NEW.id, NEW.approval_by);
        END IF;
        RETURN NULL;
    END IF;

    was_ok := OLD.approval_status IS TRUE AND NOT OLD.is_deleted;
    is_ok  := NEW.approval_status IS TRUE AND NOT NEW.is_deleted;
    IF NOT was_ok AND NOT is_ok THEN
        RETURN NULL;                                    -- pending / rejected edits: nothing to do
    END IF;

    IF was_ok AND (NOT is_ok OR NEW.clint_id <> OLD.clint_id) THEN
        SELECT r.reversed_by, r.reason INTO rev FROM payment_reversals r WHERE r.payment_id = NEW.id;
        IF FOUND THEN                                   -- v2 reversal (the reversal row is written first)
            actor := rev.reversed_by;
            why   := 'payment reversed: ' || rev.reason;
        ELSIF NOT is_ok THEN                            -- legacy reject of an approved payment, or delete flag
            actor := CASE WHEN NEW.approval_status IS FALSE THEN NEW.approval_by END;
            why   := 'payment no longer approved';
        ELSE
            actor := NULL;
            why   := 'payment moved to another case';
        END IF;
        PERFORM plans_void_allocations(NEW.id, actor, why);
    ELSIF is_ok AND round(NEW.amount::numeric, 2) < round(OLD.amount::numeric, 2)
          AND round(NEW.amount::numeric, 2) < (SELECT coalesce(sum(a.amount), 0)
                                                 FROM plan_allocations a
                                                WHERE a.payment_id = NEW.id AND a.voided_at IS NULL) THEN
        PERFORM plans_void_allocations(NEW.id, NULL, 'payment amount reduced below its allocations');
    END IF;

    IF is_ok AND NOT was_ok THEN
        PERFORM plans_auto_allocate(NEW.id, NEW.approval_by);
    END IF;
    RETURN NULL;
END $$;

COMMENT ON FUNCTION payments_plans_trg() IS
    'AFTER row trigger on "Payments": plan allocation consistency (auto-allocate on approval, void on '
    'reversal / rejection / delete / move / shrink). Fires after payments_money_trg (name order).';

DROP TRIGGER IF EXISTS payments_plans_trg ON "Payments";
CREATE TRIGGER payments_plans_trg
    AFTER INSERT OR UPDATE OR DELETE ON "Payments"
    FOR EACH ROW EXECUTE FUNCTION payments_plans_trg();
