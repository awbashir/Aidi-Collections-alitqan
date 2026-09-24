-- 0037 promise allocation guard: lock the payment row FOR SHARE (Phase 3 finding).
--
-- promise_allocations_guard (0008) read the payment without locking it. A manual promise
-- allocation racing a legacy reject of the same payment could therefore commit an active
-- allocation on a payment that the reject had just made non-approved (the reject's void ran
-- before the allocation existed). The repayment-plan guard (0032) closes the same race with
-- FOR SHARE; this applies the identical fix to the promise ledger.
--
-- Lock order stays: case row (FOR NO KEY UPDATE), then payment row (FOR SHARE). The void paths
-- take no case lock, so a reject waits for the allocation to commit and then voids it; no cycle.
-- Only the function body changes (CREATE OR REPLACE); the trigger definition is unchanged.
-- Locks: none beyond the brief function replacement. Instant.

CREATE OR REPLACE FUNCTION promise_allocations_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    pay        record;
    prom       record;
    used_pay   numeric;
    used_prom  numeric;
BEGIN
    IF TG_OP = 'UPDATE' THEN
        IF OLD.voided_at IS NOT NULL THEN
            RAISE EXCEPTION 'allocation % is already voided', OLD.id USING ERRCODE = 'CW007';
        END IF;
        IF NEW.voided_at IS NULL
           OR (NEW.id, NEW.promise_id, NEW.payment_id, NEW.case_id, NEW.amount, NEW.source,
               NEW.note, NEW.created_by, NEW.created_at)
              IS DISTINCT FROM
              (OLD.id, OLD.promise_id, OLD.payment_id, OLD.case_id, OLD.amount, OLD.source,
               OLD.note, OLD.created_by, OLD.created_at) THEN
            RAISE EXCEPTION 'allocation % can only be voided', OLD.id USING ERRCODE = 'CW007';
        END IF;
        RETURN NEW;
    END IF;

    -- INSERT: serialize every allocation of this case on the case row.
    PERFORM 1 FROM clints WHERE id = NEW.case_id FOR NO KEY UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'case % not found', NEW.case_id USING ERRCODE = 'CW006';
    END IF;

    SELECT p.clint_id, round(p.amount::numeric, 2) AS amount, p.approval_status, p.is_deleted,
           EXISTS (SELECT 1 FROM payment_reversals r WHERE r.payment_id = p.id) AS reversed
      INTO pay FROM "Payments" p WHERE p.id = NEW.payment_id
       FOR SHARE OF p;     -- 0037: a concurrent reject/reverse/delete waits, then voids this allocation
    IF NOT FOUND THEN
        RAISE EXCEPTION 'payment % not found', NEW.payment_id USING ERRCODE = 'CW006';
    END IF;
    IF pay.clint_id <> NEW.case_id THEN
        RAISE EXCEPTION 'payment % belongs to another case', NEW.payment_id USING ERRCODE = 'CW005';
    END IF;
    IF pay.approval_status IS NOT TRUE OR pay.is_deleted OR pay.reversed THEN
        RAISE EXCEPTION 'payment % is not an approved, non-reversed payment', NEW.payment_id
            USING ERRCODE = 'CW003';
    END IF;

    SELECT d.clint_id, round(d.amount::numeric, 2) AS amount, d.is_deleted, d.cancelled_at
      INTO prom FROM "paymentDates" d WHERE d.id = NEW.promise_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'promise % not found', NEW.promise_id USING ERRCODE = 'CW006';
    END IF;
    IF prom.clint_id <> NEW.case_id THEN
        RAISE EXCEPTION 'promise % belongs to another case', NEW.promise_id USING ERRCODE = 'CW005';
    END IF;
    IF prom.is_deleted OR prom.cancelled_at IS NOT NULL THEN
        RAISE EXCEPTION 'promise % is deleted or cancelled', NEW.promise_id USING ERRCODE = 'CW004';
    END IF;

    SELECT coalesce(sum(a.amount), 0) INTO used_pay
      FROM promise_allocations a WHERE a.payment_id = NEW.payment_id AND a.voided_at IS NULL;
    IF used_pay + NEW.amount > pay.amount THEN
        RAISE EXCEPTION 'payment capacity exceeded: % already allocated + % > %',
                        used_pay, NEW.amount, pay.amount
            USING ERRCODE = 'CW001', DETAIL = (pay.amount - used_pay)::text;
    END IF;

    SELECT coalesce(sum(a.amount), 0) INTO used_prom
      FROM promise_allocations a WHERE a.promise_id = NEW.promise_id AND a.voided_at IS NULL;
    IF used_prom + NEW.amount > prom.amount THEN
        RAISE EXCEPTION 'promise capacity exceeded: % already allocated + % > %',
                        used_prom, NEW.amount, prom.amount
            USING ERRCODE = 'CW002', DETAIL = (prom.amount - used_prom)::text;
    END IF;
    RETURN NEW;
END $$;

