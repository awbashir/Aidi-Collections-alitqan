-- 0033 repayment plan state views (Phase 3, stream B).
--
--   cawp_plan_broken_threshold()  the "broken" threshold: number of overdue installments that
--                                 makes an active plan report as broken. Default 2. Configurable
--                                 per database (ALTER DATABASE ... SET cawp.plan_broken_threshold
--                                 = '3') or per transaction (set_config(..., true); the API does
--                                 this from PLAN_BROKEN_THRESHOLD). Anything but 1..9999 -> 2.
--   v_plan_installment_state      one row per installment. state, first match wins:
--                                   paid            allocated >= amount (a paid installment of a
--                                                   cancelled plan stays paid: it is evidence)
--                                   cancelled       the plan is cancelled
--                                   overdue         due_date < Riyadh today (partly paid or not)
--                                   partially_paid  allocated > 0 (due today or later)
--                                   due             due_date = Riyadh today
--                                   upcoming        otherwise
--   v_plan_state                  one row per plan. state, first match wins:
--                                   cancelled  stored status cancelled
--                                   completed  every installment paid
--                                   broken     overdue installments >= threshold (reported
--                                              only: nothing is changed automatically)
--                                   active     otherwise
--
-- Riyadh today is cawp_riyadh_today() (0008). Both views are plain, inlinable SQL.
-- Locks: none (views and a function). Instant.

CREATE OR REPLACE FUNCTION cawp_plan_broken_threshold() RETURNS integer
LANGUAGE sql STABLE PARALLEL SAFE AS $$
    SELECT CASE WHEN coalesce(current_setting('cawp.plan_broken_threshold', true), '') ~ '^[1-9][0-9]{0,3}$'
                THEN current_setting('cawp.plan_broken_threshold', true)::integer
                ELSE 2 END
$$;

COMMENT ON FUNCTION cawp_plan_broken_threshold() IS
    'Overdue installments that make an active plan "broken" (reported only). GUC cawp.plan_broken_threshold, default 2.';

CREATE OR REPLACE VIEW v_plan_installment_state AS
SELECT i.id                                                   AS installment_id,
       i.plan_id                                              AS plan_id,
       i.case_id                                              AS case_id,
       i.seq                                                  AS seq,
       i.due_date                                             AS due_date,
       i.amount                                               AS amount,
       coalesce(al.allocated, 0)::numeric(14,2)               AS allocated,
       greatest(i.amount - coalesce(al.allocated, 0), 0)::numeric(14,2) AS remaining,
       CASE
           WHEN coalesce(al.allocated, 0) >= i.amount THEN 'paid'
           WHEN p.status = 'cancelled' THEN 'cancelled'
           WHEN i.due_date < cawp_riyadh_today() THEN 'overdue'
           WHEN coalesce(al.allocated, 0) > 0 THEN 'partially_paid'
           WHEN i.due_date = cawp_riyadh_today() THEN 'due'
           ELSE 'upcoming'
       END::text                                              AS state,
       CASE WHEN coalesce(al.allocated, 0) >= i.amount THEN al.last_allocated_at END AS paid_at,
       p.status                                               AS plan_status
  FROM plan_installments i
  JOIN repayment_plans p ON p.id = i.plan_id
  LEFT JOIN LATERAL (
        SELECT sum(a.amount) AS allocated, max(a.created_at) AS last_allocated_at
          FROM plan_allocations a
         WHERE a.installment_id = i.id AND a.voided_at IS NULL
  ) al ON true;

COMMENT ON VIEW v_plan_installment_state IS
    'Installment state contract (Phase 3 B): installment_id, plan_id, case_id, seq, due_date (Riyadh civil), '
    'amount, allocated, remaining, state (upcoming|due|overdue|partially_paid|paid|cancelled), paid_at, plan_status.';

CREATE OR REPLACE VIEW v_plan_state AS
SELECT p.id                                                   AS plan_id,
       p.case_id                                              AS case_id,
       p.status                                               AS status,
       CASE
           WHEN p.status = 'cancelled' THEN 'cancelled'
           WHEN s.paid_count = p.installment_count THEN 'completed'
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
       CASE WHEN p.status = 'active' AND s.paid_count = p.installment_count
            THEN s.last_paid_at END                           AS completed_at,
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
               max(st.due_date)                                                   AS last_due_date,
               max(st.paid_at)                                                    AS last_paid_at
          FROM v_plan_installment_state st
         WHERE st.plan_id = p.id
  ) s;

COMMENT ON VIEW v_plan_state IS
    'Plan state contract (Phase 3 B): state cancelled > completed > broken (overdue_count >= '
    'cawp_plan_broken_threshold(), reported only) > active, with per-state installment counts and exact totals.';
