-- 0008 money rules in the database (Phase 1, stream A).
--
--   cawp_riyadh_today()          Asia/Riyadh civil date of now().
--   promise_allocations_guard    BEFORE INSERT/UPDATE on promise_allocations:
--                                  * INSERT: locks the case row (FOR NO KEY UPDATE), then
--                                    checks same case, payment approved / not deleted / not
--                                    reversed, promise not deleted / not cancelled, and
--                                    capacity on BOTH sides:
--                                      sum(active allocations of the payment) <= payment amount
--                                      sum(active allocations of the promise) <= promise amount
--                                    Every allocation on a case serializes on that one row
--                                    lock, so concurrent inserts cannot both pass the check.
--                                  * UPDATE: only voiding an active row is allowed.
--   money_auto_allocate(p, actor)  allocates a payment's remaining capacity to its case's
--                                  open/overdue promises, oldest due date first (source auto).
--   money_void_allocations(...)    voids the active allocations of a payment or a promise.
--   "Payments" triggers            BEFORE UPDATE: a reversed payment cannot be re-approved,
--                                  un-deleted or edited.
--                                  AFTER INSERT/UPDATE/DELETE: keeps allocations consistent
--                                  (auto-allocate on approval, void when a payment stops
--                                  being approved / shrinks / is deleted) and, for LEGACY
--                                  writes, records payment_events + audit_events with
--                                  before/after (approve, reject, edit, hard delete, create).
--   "paymentDates" trigger         AFTER INSERT/UPDATE/DELETE: voids allocations of a
--                                  deleted promise; records legacy writes.
--   v_promise_state                the promise-state contract used by stream B.
--
-- "Legacy write" = a write in a transaction that did not run
-- `SELECT set_config('cawp.app_write', 'on', true)`. The v2 service (services/money.py)
-- sets it and writes its own, richer events and audit rows (actor, reason, request id,
-- idempotency key), so nothing is recorded twice.
--
-- Error codes raised here (mapped to HTTP by services/money.py):
--   CW001 payment capacity exceeded      CW002 promise capacity exceeded
--   CW003 payment not allocatable        CW004 promise not allocatable
--   CW005 payment/promise on another case CW006 row not found
--   CW007 allocation is immutable        CW008 reversed payment is frozen
--
-- Locks: CREATE TRIGGER on "Payments" and "paymentDates" takes SHARE ROW EXCLUSIVE
-- briefly (lock_timeout 5s). Functions and the view take no table locks. Instant.

-- ------------------------------------------------------------------ helpers
CREATE OR REPLACE FUNCTION cawp_riyadh_today() RETURNS date
LANGUAGE sql STABLE PARALLEL SAFE AS $$
    SELECT (now() AT TIME ZONE 'Asia/Riyadh')::date
$$;

COMMENT ON FUNCTION cawp_riyadh_today() IS 'Asia/Riyadh civil date of now() (transaction start).';

CREATE OR REPLACE FUNCTION cawp_money_app_write() RETURNS boolean
LANGUAGE sql STABLE AS $$
    SELECT coalesce(current_setting('cawp.app_write', true), '') = 'on'
$$;

COMMENT ON FUNCTION cawp_money_app_write() IS
    'true inside a v2 money transaction (set_config(''cawp.app_write'', ''on'', true)); '
    'triggers then skip their legacy event/audit rows.';

CREATE OR REPLACE FUNCTION cawp_jsonb_diff(old_row jsonb, new_row jsonb, OUT before jsonb, OUT after jsonb)
LANGUAGE sql IMMUTABLE AS $$
    SELECT coalesce(jsonb_object_agg(k, old_row -> k), '{}'::jsonb),
           coalesce(jsonb_object_agg(k, new_row -> k), '{}'::jsonb)
      FROM (SELECT k FROM jsonb_object_keys(old_row || new_row) AS k
             WHERE (old_row -> k) IS DISTINCT FROM (new_row -> k)) d
$$;

-- ------------------------------------------------------------------ allocation guard
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
      INTO pay FROM "Payments" p WHERE p.id = NEW.payment_id;
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

DROP TRIGGER IF EXISTS promise_allocations_guard ON promise_allocations;
CREATE TRIGGER promise_allocations_guard
    BEFORE INSERT OR UPDATE ON promise_allocations
    FOR EACH ROW EXECUTE FUNCTION promise_allocations_guard();

-- ------------------------------------------------------------------ void / auto-allocate
CREATE OR REPLACE FUNCTION money_void_allocations(p_payment_id uuid, p_promise_id uuid,
                                                  p_actor uuid, p_reason text,
                                                  p_event text DEFAULT 'allocation_voided')
RETURNS integer
LANGUAGE plpgsql AS $$
DECLARE
    n integer;
BEGIN
    WITH v AS (
        UPDATE promise_allocations a
           SET voided_at = now(), voided_by = p_actor, void_reason = p_reason
         WHERE a.voided_at IS NULL
           AND ((p_payment_id IS NOT NULL AND a.payment_id = p_payment_id)
             OR (p_promise_id IS NOT NULL AND a.promise_id = p_promise_id))
        RETURNING a.id, a.promise_id, a.payment_id, a.case_id, a.amount
    ), e AS (
        INSERT INTO promise_events (promise_id, case_id, event, actor_user_id, amount, reason,
                                    allocation_id, payment_id, source)
        SELECT v.promise_id, v.case_id, p_event, p_actor, v.amount, p_reason, v.id, v.payment_id,
               CASE WHEN cawp_money_app_write() THEN 'api' ELSE 'legacy' END
          FROM v
        RETURNING 1
    )
    SELECT count(*) INTO n FROM e;
    RETURN n;
END $$;

COMMENT ON FUNCTION money_void_allocations(uuid, uuid, uuid, text, text) IS
    'Void the active allocations of a payment (p_payment_id) or a promise (p_promise_id); one '
    'promise_events row per voided allocation. Returns the number voided.';

CREATE OR REPLACE FUNCTION money_auto_allocate(p_payment_id uuid, p_actor uuid)
RETURNS integer
LANGUAGE plpgsql AS $$
DECLARE
    pay        record;
    prom       record;
    remaining  numeric(14,2);
    take       numeric(14,2);
    alloc_id   uuid;
    n          integer := 0;
    src        text := CASE WHEN cawp_money_app_write() THEN 'api' ELSE 'legacy' END;
BEGIN
    SELECT p.id, p.clint_id, round(p.amount::numeric, 2) AS amount, p.approval_status, p.is_deleted
      INTO pay FROM "Payments" p WHERE p.id = p_payment_id;
    IF NOT FOUND OR pay.approval_status IS NOT TRUE OR pay.is_deleted
       OR EXISTS (SELECT 1 FROM payment_reversals r WHERE r.payment_id = p_payment_id) THEN
        RETURN 0;
    END IF;
    PERFORM 1 FROM clints WHERE id = pay.clint_id FOR NO KEY UPDATE;

    SELECT pay.amount - coalesce(sum(a.amount), 0) INTO remaining
      FROM promise_allocations a WHERE a.payment_id = p_payment_id AND a.voided_at IS NULL;

    FOR prom IN
        SELECT d.id,
               round(d.amount::numeric, 2)
               - coalesce((SELECT sum(a.amount) FROM promise_allocations a
                            WHERE a.promise_id = d.id AND a.voided_at IS NULL), 0) AS capacity
          FROM "paymentDates" d
         WHERE d.clint_id = pay.clint_id
           AND d.is_deleted = false
           AND d.cancelled_at IS NULL
         ORDER BY d.payment_date, d.create_at, d.id
    LOOP
        EXIT WHEN remaining <= 0;
        CONTINUE WHEN prom.capacity <= 0;
        take := least(remaining, prom.capacity);
        INSERT INTO promise_allocations (promise_id, payment_id, case_id, amount, source, created_by)
        VALUES (prom.id, p_payment_id, pay.clint_id, take, 'auto', p_actor)
        RETURNING id INTO alloc_id;
        INSERT INTO promise_events (promise_id, case_id, event, actor_user_id, amount,
                                    allocation_id, payment_id, source)
        VALUES (prom.id, pay.clint_id, 'allocated', p_actor, take, alloc_id, p_payment_id, src);
        remaining := remaining - take;
        n := n + 1;
    END LOOP;
    RETURN n;
END $$;

COMMENT ON FUNCTION money_auto_allocate(uuid, uuid) IS
    'Allocate an approved payment''s remaining capacity to its case''s open/overdue promises, oldest '
    'due date first (source auto). Returns the number of allocations created.';

-- ------------------------------------------------------------------ "Payments" triggers
CREATE OR REPLACE FUNCTION payments_reversal_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF (NEW.approval_status IS TRUE OR NEW.is_deleted = false
        OR NEW.amount IS DISTINCT FROM OLD.amount OR NEW.clint_id IS DISTINCT FROM OLD.clint_id)
       AND EXISTS (SELECT 1 FROM payment_reversals r WHERE r.payment_id = OLD.id) THEN
        RAISE EXCEPTION 'payment % was reversed and cannot be re-approved or edited', OLD.id
            USING ERRCODE = 'CW008';
    END IF;
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS payments_reversal_guard ON "Payments";
CREATE TRIGGER payments_reversal_guard
    BEFORE UPDATE ON "Payments"
    FOR EACH ROW EXECUTE FUNCTION payments_reversal_guard();

CREATE OR REPLACE FUNCTION payments_money_trg() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    app       boolean := cawp_money_app_write();
    was_ok    boolean;
    is_ok     boolean;
    diff      record;
    ev        text;
    act       text;
    actor     uuid;
    at        timestamptz;
    cmt       text;
BEGIN
    IF TG_OP = 'DELETE' THEN
        PERFORM money_void_allocations(OLD.id, NULL, NULL, 'payment hard-deleted');
        IF NOT app THEN
            INSERT INTO payment_events (payment_id, case_id, event, actor_user_id, amount, source)
            VALUES (OLD.id, OLD.clint_id, 'deleted', NULL, round(OLD.amount::numeric, 2), 'legacy');
            INSERT INTO audit_events (actor_user_id, action, entity_type, entity_id, case_id,
                                      before, after, source)
            VALUES (NULL, 'payment.legacy.delete', 'payment', OLD.id::text, OLD.clint_id,
                    to_jsonb(OLD), NULL, 'legacy_trigger');
        END IF;
        RETURN NULL;
    END IF;

    IF TG_OP = 'INSERT' THEN
        IF NOT app THEN
            INSERT INTO payment_events (payment_id, case_id, event, occurred_at, actor_user_id,
                                        amount, source)
            VALUES (NEW.id, NEW.clint_id, 'submitted', NEW.create_at, NEW.user_id,
                    round(NEW.amount::numeric, 2), 'legacy');
            IF NEW.approval_status IS NOT NULL THEN   -- legacy POST /payment accepts a decided row
                INSERT INTO payment_events (payment_id, case_id, event, occurred_at, actor_user_id,
                                            amount, comment, source)
                VALUES (NEW.id, NEW.clint_id,
                        CASE WHEN NEW.approval_status THEN 'approved' ELSE 'rejected' END,
                        coalesce(NEW.approval_at, now()), NEW.approval_by,
                        round(NEW.amount::numeric, 2), NEW.approval_comment, 'legacy');
            END IF;
            INSERT INTO audit_events (actor_user_id, action, entity_type, entity_id, case_id,
                                      before, after, source)
            VALUES (NULL, 'payment.legacy.create', 'payment', NEW.id::text, NEW.clint_id,
                    NULL, to_jsonb(NEW), 'legacy_trigger');
        END IF;
        IF NEW.approval_status IS TRUE AND NOT NEW.is_deleted THEN
            PERFORM money_auto_allocate(NEW.id, NEW.approval_by);
        END IF;
        RETURN NULL;
    END IF;

    -- UPDATE: keep the allocation ledger consistent first (legacy and v2 alike).
    was_ok := OLD.approval_status IS TRUE AND NOT OLD.is_deleted;
    is_ok  := NEW.approval_status IS TRUE AND NOT NEW.is_deleted;
    IF was_ok AND (NOT is_ok OR NEW.clint_id <> OLD.clint_id) THEN
        PERFORM money_void_allocations(NEW.id, NULL,
                                       CASE WHEN NEW.approval_status IS FALSE THEN NEW.approval_by END,
                                       'payment no longer approved');
    ELSIF is_ok AND round(NEW.amount::numeric, 2) IS DISTINCT FROM round(OLD.amount::numeric, 2)
          AND round(NEW.amount::numeric, 2) < (SELECT coalesce(sum(a.amount), 0)
                                                 FROM promise_allocations a
                                                WHERE a.payment_id = NEW.id AND a.voided_at IS NULL) THEN
        PERFORM money_void_allocations(NEW.id, NULL, NULL, 'payment amount reduced below its allocations');
    END IF;
    IF is_ok AND NOT was_ok THEN
        PERFORM money_auto_allocate(NEW.id, NEW.approval_by);
    END IF;

    IF app THEN
        RETURN NULL;
    END IF;
    SELECT * INTO diff FROM cawp_jsonb_diff(to_jsonb(OLD) - 'revision', to_jsonb(NEW) - 'revision');
    IF diff.after = '{}'::jsonb THEN
        RETURN NULL;                                    -- no-op UPDATE
    END IF;

    IF NEW.approval_status IS NOT NULL
       AND (NEW.approval_status IS DISTINCT FROM OLD.approval_status
            OR NEW.approval_at IS DISTINCT FROM OLD.approval_at) THEN
        ev    := CASE WHEN NEW.approval_status THEN 'approved' ELSE 'rejected' END;
        act   := 'payment.legacy.' || CASE WHEN NEW.approval_status THEN 'approve' ELSE 'reject' END;
        actor := NEW.approval_by;
        at    := coalesce(NEW.approval_at, now());
        cmt   := NEW.approval_comment;
    ELSE
        ev := 'edited'; act := 'payment.legacy.update'; actor := NULL; at := now(); cmt := NULL;
    END IF;

    INSERT INTO payment_events (payment_id, case_id, event, occurred_at, actor_user_id, amount,
                                comment, source)
    VALUES (NEW.id, NEW.clint_id, ev, at, actor, round(NEW.amount::numeric, 2), cmt, 'legacy');
    INSERT INTO audit_events (actor_user_id, action, entity_type, entity_id, case_id, before, after,
                              source)
    VALUES (actor, act, 'payment', NEW.id::text, NEW.clint_id, diff.before, diff.after,
            'legacy_trigger');
    RETURN NULL;
END $$;

COMMENT ON FUNCTION payments_money_trg() IS
    'AFTER row trigger on "Payments": allocation consistency always; payment_events + audit_events '
    'for legacy writes (cawp.app_write not set).';

DROP TRIGGER IF EXISTS payments_money_trg ON "Payments";
CREATE TRIGGER payments_money_trg
    AFTER INSERT OR UPDATE OR DELETE ON "Payments"
    FOR EACH ROW EXECUTE FUNCTION payments_money_trg();

-- ------------------------------------------------------------------ "paymentDates" trigger
CREATE OR REPLACE FUNCTION paymentdates_money_trg() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    app   boolean := cawp_money_app_write();
    diff  record;
BEGIN
    IF TG_OP = 'DELETE' THEN
        PERFORM money_void_allocations(NULL, OLD.id, NULL, 'promise hard-deleted');
        IF NOT app THEN
            INSERT INTO promise_events (promise_id, case_id, event, amount, source)
            VALUES (OLD.id, OLD.clint_id, 'deleted', round(OLD.amount::numeric, 2), 'legacy');
            INSERT INTO audit_events (actor_user_id, action, entity_type, entity_id, case_id,
                                      before, after, source)
            VALUES (NULL, 'promise.legacy.delete', 'promise', OLD.id::text, OLD.clint_id,
                    to_jsonb(OLD), NULL, 'legacy_trigger');
        END IF;
        RETURN NULL;
    END IF;

    IF TG_OP = 'INSERT' THEN
        IF NOT app THEN
            INSERT INTO promise_events (promise_id, case_id, event, occurred_at, actor_user_id,
                                        amount, source)
            VALUES (NEW.id, NEW.clint_id, 'created', NEW.create_at, NEW.user_id,
                    round(NEW.amount::numeric, 2), 'legacy');
            INSERT INTO audit_events (actor_user_id, action, entity_type, entity_id, case_id,
                                      before, after, source)
            VALUES (NULL, 'promise.legacy.create', 'promise', NEW.id::text, NEW.clint_id,
                    NULL, to_jsonb(NEW), 'legacy_trigger');
        END IF;
        RETURN NULL;
    END IF;

    -- UPDATE
    IF (NEW.is_deleted AND NOT OLD.is_deleted) OR NEW.clint_id <> OLD.clint_id THEN
        PERFORM money_void_allocations(NULL, NEW.id, NULL, 'promise deleted or moved');
    ELSIF round(NEW.amount::numeric, 2) IS DISTINCT FROM round(OLD.amount::numeric, 2)
          AND round(NEW.amount::numeric, 2) < (SELECT coalesce(sum(a.amount), 0)
                                                 FROM promise_allocations a
                                                WHERE a.promise_id = NEW.id AND a.voided_at IS NULL) THEN
        PERFORM money_void_allocations(NULL, NEW.id, NULL, 'promise amount reduced below its allocations');
    END IF;
    IF app THEN
        RETURN NULL;
    END IF;
    SELECT * INTO diff FROM cawp_jsonb_diff(to_jsonb(OLD) - 'revision', to_jsonb(NEW) - 'revision');
    IF diff.after = '{}'::jsonb THEN
        RETURN NULL;
    END IF;
    INSERT INTO promise_events (promise_id, case_id, event, amount, source)
    VALUES (NEW.id, NEW.clint_id, 'edited', round(NEW.amount::numeric, 2), 'legacy');
    INSERT INTO audit_events (actor_user_id, action, entity_type, entity_id, case_id, before, after,
                              source)
    VALUES (NULL, 'promise.legacy.update', 'promise', NEW.id::text, NEW.clint_id,
            diff.before, diff.after, 'legacy_trigger');
    RETURN NULL;
END $$;

DROP TRIGGER IF EXISTS paymentdates_money_trg ON "paymentDates";
CREATE TRIGGER paymentdates_money_trg
    AFTER INSERT OR UPDATE OR DELETE ON "paymentDates"
    FOR EACH ROW EXECUTE FUNCTION paymentdates_money_trg();

-- ------------------------------------------------------------------ v_promise_state
-- Contract for stream B (attention queue). Columns and meaning are frozen:
--   due_date   Asia/Riyadh civil date of "paymentDates".payment_date
--   amount     round(amount::numeric, 2)
--   allocated  sum of ACTIVE allocations (voided ones do not count)
--   state      cancelled if cancelled; else fulfilled if allocated > 0 and allocated >= amount;
--              else overdue if due_date < Riyadh today; else open
--   fulfilled_at  time of the allocation that completed the promise (NULL unless fulfilled)
-- Legacy is_deleted promises are excluded. "allocated > 0" keeps the 29 legacy promises with
-- amount <= 0 from counting as fulfilled without any payment evidence.
CREATE OR REPLACE VIEW v_promise_state AS
SELECT d.id                                                   AS promise_id,
       d.clint_id                                             AS case_id,
       d.user_id                                              AS recorder_user_id,
       (d.payment_date AT TIME ZONE 'Asia/Riyadh')::date      AS due_date,
       round(d.amount::numeric, 2)::numeric(14,2)             AS amount,
       coalesce(al.allocated, 0)::numeric(14,2)               AS allocated,
       CASE
           WHEN d.cancelled_at IS NOT NULL THEN 'cancelled'
           WHEN coalesce(al.allocated, 0) > 0
                AND coalesce(al.allocated, 0) >= round(d.amount::numeric, 2) THEN 'fulfilled'
           WHEN (d.payment_date AT TIME ZONE 'Asia/Riyadh')::date < cawp_riyadh_today() THEN 'overdue'
           ELSE 'open'
       END::text                                              AS state,
       d.cancelled_at                                         AS cancelled_at,
       CASE
           WHEN d.cancelled_at IS NULL
                AND coalesce(al.allocated, 0) > 0
                AND coalesce(al.allocated, 0) >= round(d.amount::numeric, 2) THEN al.last_allocated_at
       END                                                    AS fulfilled_at
  FROM "paymentDates" d
  LEFT JOIN LATERAL (
        SELECT sum(a.amount) AS allocated, max(a.created_at) AS last_allocated_at
          FROM promise_allocations a
         WHERE a.promise_id = d.id AND a.voided_at IS NULL
  ) al ON true
 WHERE d.is_deleted = false;

COMMENT ON VIEW v_promise_state IS
    'Promise state contract (Phase 1 A -> B): promise_id, case_id, recorder_user_id, due_date (Riyadh), '
    'amount, allocated, state (open|overdue|fulfilled|cancelled), cancelled_at, fulfilled_at.';
