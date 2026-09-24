-- 0038 repayment plans close automatically when fully paid (Phase 3 B, decision 2026-09-24).
--
-- A fully allocated plan used to stay stored 'active' (derived 'completed') and blocked a new
-- plan on the case until someone cancelled it. Now:
--
--   * repayment_plans.status gains 'completed', with completed_at (the time of the allocation
--     that completed it) and evidence_voided_at (see below).
--   * plan_allocations_state_trg (AFTER INSERT / AFTER UPDATE OF voided_at on plan_allocations):
--       - INSERT on an active plan that is now fully allocated -> status 'completed', event
--         'completed'. It fires for every allocation path: auto on approval (v2 and legacy),
--         manual allocate, direct SQL.
--       - a VOID of an allocation of a completed plan (reversal, legacy reject / delete / edit,
--         manual deallocate, direct SQL):
--           * the case has no other active plan -> back to 'active', completed_at cleared,
--             event 'reopened_unpaid' (actor = the voider, reason = the void reason);
--           * a newer active plan exists (one active plan per case) -> stays 'completed',
--             evidence_voided_at is set once, event 'completed_evidence_voided'.
--       Both branches lock the plan row (FOR NO KEY UPDATE) before deciding, so completion and reopen
--       serialize per plan (the plan row is always the LAST lock: case -> payment -> ledger
--       rows -> plan row; no path takes a payment or the case after the plan row).
--       The guard only allows 'completed' when fully allocated, and reopen / flag when not.
--         The decision is made by the unique index uq_repayment_plans_one_active itself: the
--         reopen UPDATE runs in a sub-block and a unique_violation turns it into the flag. So a
--         reopen that races a plan creation never fails and never takes the case row: the void
--         paths still lock only the payment row, then ledger rows, then the plan row (lock
--         order unchanged; the plan row is always taken last).
--   * The uq_repayment_plans_one_active index (WHERE status = 'active') no longer counts a
--     completed plan, so a new plan can be created at once.
--   * repayment_plans_guard: allowed transitions are active -> cancelled, active -> completed,
--     completed -> active (reopen, not for a flagged plan), and completed -> completed with the
--     evidence flag set once. Every allowed change bumps revision. Anything else is CW108.
--   * v_plan_state: 'completed' and completed_at now come from the stored status; 'broken'
--     stays derived for active plans. Columns unchanged.
--   * The three new events use occurred_at = clock_timestamp(), so they sort after the
--     'allocated' / 'allocation_voided' / 'deallocated' event (now()) that caused them.
--   * plan_events: new events completed / reopened_unpaid / completed_evidence_voided, new
--     source 'migration' (backfill below).
--   * Backfill: an active plan whose installments are all fully allocated is completed now
--     (completed_at = its last allocation), with a 'completed' event, source 'migration'.
--
-- Locks: ALTER TABLE on repayment_plans / plan_events (ACCESS EXCLUSIVE, milliseconds: the
-- constraints are added NOT VALID and validated after), CREATE TRIGGER on plan_allocations.
-- Re-runnable by hand.

-- ------------------------------------------------------------------ columns and constraints
ALTER TABLE repayment_plans ADD COLUMN IF NOT EXISTS completed_at timestamptz;
ALTER TABLE repayment_plans ADD COLUMN IF NOT EXISTS evidence_voided_at timestamptz;

COMMENT ON COLUMN repayment_plans.completed_at IS
    'time of the allocation that fully paid the plan (status completed); NULL otherwise';
COMMENT ON COLUMN repayment_plans.evidence_voided_at IS
    'a completed plan lost allocation evidence while a newer active plan existed on the case, so it '
    'could not be reopened (flag, reported; set once)';

ALTER TABLE repayment_plans DROP CONSTRAINT IF EXISTS repayment_plans_status_check;
ALTER TABLE repayment_plans ADD CONSTRAINT repayment_plans_status_check
    CHECK (status IN ('active', 'completed', 'cancelled')) NOT VALID;
ALTER TABLE repayment_plans VALIDATE CONSTRAINT repayment_plans_status_check;

ALTER TABLE repayment_plans DROP CONSTRAINT IF EXISTS ck_repayment_plans_cancel;
ALTER TABLE repayment_plans ADD CONSTRAINT ck_repayment_plans_cancel CHECK (
    (status IN ('active', 'completed') AND cancelled_at IS NULL AND cancelled_by IS NULL AND cancel_reason IS NULL)
    OR (status = 'cancelled' AND cancelled_at IS NOT NULL AND btrim(coalesce(cancel_reason, '')) <> '')) NOT VALID;
ALTER TABLE repayment_plans VALIDATE CONSTRAINT ck_repayment_plans_cancel;

ALTER TABLE repayment_plans DROP CONSTRAINT IF EXISTS ck_repayment_plans_completed;
ALTER TABLE repayment_plans ADD CONSTRAINT ck_repayment_plans_completed CHECK (
    (status = 'completed') = (completed_at IS NOT NULL)
    AND (evidence_voided_at IS NULL OR status = 'completed')) NOT VALID;
ALTER TABLE repayment_plans VALIDATE CONSTRAINT ck_repayment_plans_completed;

ALTER TABLE plan_events DROP CONSTRAINT IF EXISTS plan_events_event_check;
ALTER TABLE plan_events ADD CONSTRAINT plan_events_event_check CHECK (event IN
    ('created', 'cancelled', 'allocated', 'deallocated', 'allocation_voided',
     'completed', 'reopened_unpaid', 'completed_evidence_voided')) NOT VALID;
ALTER TABLE plan_events VALIDATE CONSTRAINT plan_events_event_check;

ALTER TABLE plan_events DROP CONSTRAINT IF EXISTS plan_events_source_check;
ALTER TABLE plan_events ADD CONSTRAINT plan_events_source_check
    CHECK (source IN ('api', 'legacy', 'migration')) NOT VALID;
ALTER TABLE plan_events VALIDATE CONSTRAINT plan_events_source_check;

CREATE INDEX IF NOT EXISTS ix_repayment_plans_evidence_voided
    ON repayment_plans (evidence_voided_at) WHERE evidence_voided_at IS NOT NULL;

-- ------------------------------------------------------------------ helper
CREATE OR REPLACE FUNCTION plans_fully_allocated(p_plan_id uuid) RETURNS boolean
LANGUAGE sql STABLE AS $$
    SELECT NOT EXISTS (
        SELECT 1 FROM plan_installments i
         WHERE i.plan_id = p_plan_id
           AND i.amount > (SELECT coalesce(sum(a.amount), 0) FROM plan_allocations a
                            WHERE a.installment_id = i.id AND a.voided_at IS NULL))
$$;

-- ------------------------------------------------------------------ plan guard (replaces 0032)
CREATE OR REPLACE FUNCTION repayment_plans_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    keep text[] := ARRAY['status', 'cancelled_at', 'cancelled_by', 'cancel_reason', 'completed_at',
                         'evidence_voided_at', 'revision'];
    ok   boolean;
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.status <> 'active' OR NEW.completed_at IS NOT NULL OR NEW.evidence_voided_at IS NOT NULL THEN
            RAISE EXCEPTION 'a repayment plan is created active' USING ERRCODE = 'CW108';
        END IF;
        NEW.revision := 0;
        RETURN NEW;
    END IF;
    ok := (to_jsonb(NEW) - keep) IS NOT DISTINCT FROM (to_jsonb(OLD) - keep)
          AND (
              -- cancel
              (OLD.status = 'active' AND NEW.status = 'cancelled'
               AND NEW.completed_at IS NULL AND NEW.evidence_voided_at IS NULL)
              -- automatic completion
           OR (OLD.status = 'active' AND NEW.status = 'completed'
               AND NEW.completed_at IS NOT NULL AND NEW.evidence_voided_at IS NULL
               AND plans_fully_allocated(OLD.id))
              -- reopen after lost evidence (never a flagged plan)
           OR (OLD.status = 'completed' AND NEW.status = 'active' AND OLD.evidence_voided_at IS NULL
               AND NEW.completed_at IS NULL AND NEW.evidence_voided_at IS NULL
               AND NOT plans_fully_allocated(OLD.id))
              -- flag: lost evidence while a newer active plan exists (set once)
           OR (OLD.status = 'completed' AND NEW.status = 'completed' AND OLD.evidence_voided_at IS NULL
               AND NEW.evidence_voided_at IS NOT NULL AND NEW.completed_at = OLD.completed_at
               AND NEW.cancelled_at IS NULL AND NOT plans_fully_allocated(OLD.id)));
    IF NOT ok THEN
        RAISE EXCEPTION 'repayment plan %: % -> % is not an allowed change', OLD.id, OLD.status, NEW.status
            USING ERRCODE = 'CW108';
    END IF;
    NEW.revision := OLD.revision + 1;
    RETURN NEW;
END $$;

-- ------------------------------------------------------------------ complete / reopen


CREATE OR REPLACE FUNCTION plan_allocations_state_trg() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    p      record;
    other  uuid;
    src    text := CASE WHEN cawp_money_app_write() THEN 'api' ELSE 'legacy' END;
BEGIN
    IF TG_OP = 'INSERT' THEN
        -- cheap exits (no lock needed: this transaction holds the case row, so no concurrent
        -- allocation can complete the plan, and voids only remove evidence): the installment
        -- just allocated, and the plan's last-due installment (filled last, oldest first)
        IF EXISTS (SELECT 1 FROM plan_installments i
                    WHERE i.id IN (NEW.installment_id,
                                   (SELECT l.id FROM plan_installments l WHERE l.plan_id = NEW.plan_id
                                     ORDER BY l.due_date DESC, l.seq DESC LIMIT 1))
                      AND i.amount > (SELECT coalesce(sum(a.amount), 0) FROM plan_allocations a
                                       WHERE a.installment_id = i.id AND a.voided_at IS NULL)) THEN
            RETURN NULL;
        END IF;
        -- lock the plan row first, then decide on a fresh snapshot: a concurrent void (which
        -- holds no case lock) is either committed and seen here, or waits for this row and
        -- then sees 'completed' and reopens
        SELECT rp.id, rp.case_id, rp.status, rp.total_amount INTO p
          FROM repayment_plans rp WHERE rp.id = NEW.plan_id FOR NO KEY UPDATE;
        IF p.status <> 'active' OR NOT plans_fully_allocated(NEW.plan_id) THEN
            RETURN NULL;
        END IF;
        UPDATE repayment_plans SET status = 'completed', completed_at = NEW.created_at
         WHERE id = NEW.plan_id;
        INSERT INTO plan_events (plan_id, case_id, event, occurred_at, actor_user_id, amount, installment_id,
                                 allocation_id, payment_id, details, source)
        VALUES (NEW.plan_id, p.case_id, 'completed', clock_timestamp(), NEW.created_by, p.total_amount, NEW.installment_id,
                NEW.id, NEW.payment_id, jsonb_build_object('completed_at', NEW.created_at), src);
        RETURN NULL;
    END IF;

    -- UPDATE OF voided_at: only a void (active -> voided) matters
    IF OLD.voided_at IS NOT NULL OR NEW.voided_at IS NULL THEN
        RETURN NULL;
    END IF;
    SELECT rp.id, rp.case_id, rp.status, rp.completed_at, rp.evidence_voided_at INTO p
      FROM repayment_plans rp WHERE rp.id = NEW.plan_id FOR NO KEY UPDATE;
    IF p.status <> 'completed' THEN
        RETURN NULL;
    END IF;
    IF p.evidence_voided_at IS NOT NULL THEN
        RETURN NULL;                                  -- already flagged; the void has its own event
    END IF;
    SELECT rp.id INTO other FROM repayment_plans rp
     WHERE rp.case_id = p.case_id AND rp.status = 'active' AND rp.id <> p.id;
    IF other IS NULL THEN
        BEGIN
            UPDATE repayment_plans SET status = 'active', completed_at = NULL WHERE id = p.id;
            INSERT INTO plan_events (plan_id, case_id, event, occurred_at, actor_user_id, amount, reason,
                                     installment_id, allocation_id, payment_id, details, source)
            VALUES (p.id, p.case_id, 'reopened_unpaid', clock_timestamp(), NEW.voided_by, NEW.amount, NEW.void_reason,
                    NEW.installment_id, NEW.id, NEW.payment_id,
                    jsonb_build_object('completed_at', p.completed_at), src);
            RETURN NULL;
        EXCEPTION WHEN unique_violation THEN
            -- a new plan was created concurrently (it committed first): flag instead
            SELECT rp.id INTO other FROM repayment_plans rp
             WHERE rp.case_id = p.case_id AND rp.status = 'active' AND rp.id <> p.id;
        END;
    END IF;
    UPDATE repayment_plans SET evidence_voided_at = now() WHERE id = p.id;
    INSERT INTO plan_events (plan_id, case_id, event, occurred_at, actor_user_id, amount, reason,
                             installment_id, allocation_id, payment_id, details, source)
    VALUES (p.id, p.case_id, 'completed_evidence_voided', clock_timestamp(), NEW.voided_by, NEW.amount, NEW.void_reason,
            NEW.installment_id, NEW.id, NEW.payment_id,
            jsonb_build_object('completed_at', p.completed_at, 'active_plan_id', other), src);
    RETURN NULL;
END $$;

COMMENT ON FUNCTION plan_allocations_state_trg() IS
    'AFTER INSERT / UPDATE OF voided_at on plan_allocations: completes a fully allocated active plan; '
    'reopens a completed plan that lost evidence, or flags it when a newer active plan exists.';

DROP TRIGGER IF EXISTS plan_allocations_state_trg ON plan_allocations;
CREATE TRIGGER plan_allocations_state_trg
    AFTER INSERT OR UPDATE OF voided_at ON plan_allocations
    FOR EACH ROW EXECUTE FUNCTION plan_allocations_state_trg();

-- ------------------------------------------------------------------ v_plan_state (replaces 0033)
-- Same columns, same order and types as 0033 (so re-running 0033 by hand still works);
-- completed / completed_at now come from the stored status. The evidence flag is read from
-- repayment_plans.evidence_voided_at.
CREATE OR REPLACE VIEW v_plan_state AS
SELECT p.id                                                   AS plan_id,
       p.case_id                                              AS case_id,
       p.status                                               AS status,
       CASE
           WHEN p.status = 'cancelled' THEN 'cancelled'
           WHEN p.status = 'completed' THEN 'completed'
           WHEN s.overdue_count >= cawp_plan_broken_threshold() THEN 'broken'
           ELSE 'active'
       END::text                                              AS state,
       p.frequency                                            AS frequency,
       p.start_date                                           AS start_date,
       s.last_due_date                                        AS end_date,
       p.total_amount                                         AS total_amount,
       p.installment_count                                    AS installment_count,
       s.allocated::numeric(14,2)                             AS allocated,
       greatest(p.total_amount - s.allocated, 0)::numeric(14,2) AS remaining,
       s.upcoming_count, s.due_count, s.overdue_count, s.partially_paid_count, s.paid_count,
       s.cancelled_count,
       s.overdue_amount::numeric(14,2)                        AS overdue_amount,
       s.oldest_overdue_date                                  AS oldest_overdue_date,
       s.next_due_date                                        AS next_due_date,
       p.completed_at                                         AS completed_at,
       p.created_by, p.created_at, p.cancelled_at
  FROM repayment_plans p
  CROSS JOIN LATERAL (
        SELECT coalesce(sum(st.allocated), 0)                                   AS allocated,
               count(*) FILTER (WHERE st.state = 'upcoming')                      AS upcoming_count,
               count(*) FILTER (WHERE st.state = 'due')                           AS due_count,
               count(*) FILTER (WHERE st.state = 'overdue')                       AS overdue_count,
               count(*) FILTER (WHERE st.state = 'partially_paid')                AS partially_paid_count,
               count(*) FILTER (WHERE st.state = 'paid')                          AS paid_count,
               count(*) FILTER (WHERE st.state = 'cancelled')                     AS cancelled_count,
               coalesce(sum(st.remaining) FILTER (WHERE st.state = 'overdue'), 0) AS overdue_amount,
               min(st.due_date) FILTER (WHERE st.state = 'overdue')               AS oldest_overdue_date,
               min(st.due_date) FILTER (WHERE st.state IN ('due', 'upcoming', 'partially_paid')) AS next_due_date,
               max(st.due_date)                                                   AS last_due_date
          FROM v_plan_installment_state st
         WHERE st.plan_id = p.id
  ) s;

COMMENT ON VIEW v_plan_state IS
    'Plan state contract (Phase 3 B, 0038): state cancelled | completed (stored) | broken (active with '
    'overdue_count >= cawp_plan_broken_threshold(), reported only) | active. '
    'repayment_plans.evidence_voided_at flags a completed plan that lost evidence while a newer plan was active.';

-- ------------------------------------------------------------------ backfill
WITH done AS (
    SELECT rp.id, rp.case_id, rp.total_amount,
           (SELECT max(a.created_at) FROM plan_allocations a
             WHERE a.plan_id = rp.id AND a.voided_at IS NULL) AS last_at
      FROM repayment_plans rp
     WHERE rp.status = 'active' AND plans_fully_allocated(rp.id)
), upd AS (
    UPDATE repayment_plans rp SET status = 'completed', completed_at = done.last_at
      FROM done WHERE rp.id = done.id
    RETURNING rp.id, rp.case_id, rp.total_amount, rp.completed_at
)
INSERT INTO plan_events (plan_id, case_id, event, amount, details, source)
SELECT upd.id, upd.case_id, 'completed', upd.total_amount,
       jsonb_build_object('completed_at', upd.completed_at, 'backfill', '0038'), 'migration'
  FROM upd;
