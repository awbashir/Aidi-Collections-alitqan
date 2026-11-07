-- 0093 report center: self-service BI and SQL builder (customer feedback 2026-11-04,
-- docs/report-builder-plan.md).
--
-- schema bi                curated read-only views: the only objects the BI page and the SQL page can
--                          read. They expose safe columns only (no identity numbers, no employee mobile,
--                          e-mail, password, target or token columns, no secret tables). Descriptions in
--                          Arabic and English live in CAWP-API/services/bi_catalog.py (a test checks that
--                          every view column has one).
-- bi_saved_reports         saved BI layouts (owner, name, spec, shared flag)
-- sql_saved_queries        saved SQL texts (owner, name, sql, shared flag)
-- report_builder_runs      every BI run, SQL run and export: who, what, rows, time, outcome
--
-- Money: bi.payments.status follows RPT-06 (reversed > pending > approved > rejected);
-- counts_as_collected is true only for approved, not deleted and not reversed money.
-- Civil dates are Asia/Riyadh. Views call no functions outside pg_catalog, so a login that can read
-- only schema bi can use them.
--
-- The SQL page runs on its own login, alitqan_sql_reader (created once by ops as postgres:
-- db/ops/sql_reader_role.sql). If that role already exists the grants at the end of this file are
-- applied; otherwise they are skipped and the ops script applies them. New objects only: instant.
-- Re-runnable.

CREATE SCHEMA IF NOT EXISTS bi;

-- --------------------------------------------------------------------------- lookups
CREATE OR REPLACE VIEW bi.contracts AS
SELECT ct.id                                         AS contract_id,
       ct.name                                       AS contract_name,
       ct.is_active                                  AS is_active,
       ct.created_at                                 AS created_at
  FROM public.contracts ct;

CREATE OR REPLACE VIEW bi.portfolios AS
SELECT pf.id                                         AS portfolio_id,
       pf.name                                       AS portfolio_name,
       pf.contract_id                                AS contract_id,
       ct.name                                       AS contract_name,
       pf.is_deleted                                 AS is_deleted,
       pf.create_at                                  AS created_at,
       (pf.create_at AT TIME ZONE 'Asia/Riyadh')::date AS created_date
  FROM public.portfolios pf
  LEFT JOIN public.contracts ct ON ct.id = pf.contract_id;

CREATE OR REPLACE VIEW bi.branches AS
SELECT b.id AS branch_id, b.name AS branch_name, b.is_active AS is_active
  FROM public.branches b;

CREATE OR REPLACE VIEW bi.users AS
SELECT u.id                                          AS user_id,
       nullif(btrim(concat_ws(' ', u.frist_name, u.last_name)), '') AS full_name,
       u.user_name                                   AS user_name,
       r.name                                        AS role_name,
       b.name                                        AS branch_name,
       u.contract_id                                 AS contract_id,
       ct.name                                       AS contract_name,
       u.extension                                   AS extension,
       u.is_active                                   AS is_active,
       u.created_at                                  AS created_at
  FROM public.users u
  LEFT JOIN public.roles r      ON r.id = u.role_id
  LEFT JOIN public.branches b   ON b.id = u.branch_id
  LEFT JOIN public.contracts ct ON ct.id = u.contract_id;

CREATE OR REPLACE VIEW bi.comment_statuses AS
SELECT s.id                                          AS status_id,
       s.name                                        AS status_name,
       s.parent_id                                   AS parent_status_id,
       ps.name                                       AS parent_status_name,
       s.contract_id                                 AS contract_id,
       ct.name                                       AS contract_name,
       cr.name                                       AS reachability,
       coalesce(s.is_active, false) AND s.deleted_at IS NULL AS is_active,
       s.requires_promise                            AS requires_promise,
       s.stops_campaigns                             AS stops_campaigns
  FROM public."CommentStatuses" s
  LEFT JOIN public."CommentStatuses" ps     ON ps.id = s.parent_id
  LEFT JOIN public.contracts ct             ON ct.id = s.contract_id
  LEFT JOIN public.comment_reachability cr  ON cr.id = s."CommentReachability_id";

-- --------------------------------------------------------------------------- cases
CREATE OR REPLACE VIEW bi.cases AS
SELECT c.id                                          AS case_id,
       c."accountNumber"                             AS account_number,
       c.name                                        AS client_name,
       round(c.amount::numeric, 2)                   AS debt_amount,
       coalesce(pay.approved, 0)                     AS approved_collected,
       round(c.amount::numeric, 2) - coalesce(pay.approved, 0) AS outstanding_amount,
       coalesce(pay.pending, 0)                      AS pending_amount,
       pf.contract_id                                AS contract_id,
       ct.name                                       AS contract_name,
       c.portfolios_id                               AS portfolio_id,
       pf.name                                       AS portfolio_name,
       c.user_id                                     AS owner_user_id,
       nullif(btrim(concat_ws(' ', ou.frist_name, ou.last_name)), '') AS owner_name,
       (c.user_id IS NOT NULL)                       AS is_assigned,
       c.is_active                                   AS is_active,
       c.is_archive                                  AS is_archived,
       c.is_locked                                   AS is_locked,
       dr.name                                       AS delinquency_reason,
       c.create_at                                   AS created_at,
       (c.create_at AT TIME ZONE 'Asia/Riyadh')::date AS created_date,
       c.last_comment_at                             AS last_note_at,
       (c.last_comment_at AT TIME ZONE 'Asia/Riyadh')::date AS last_note_date,
       ls.name                                       AS last_note_status
  FROM public.clints c
  LEFT JOIN public.portfolios pf            ON pf.id = c.portfolios_id
  LEFT JOIN public.contracts ct             ON ct.id = pf.contract_id
  LEFT JOIN public.users ou                 ON ou.id = c.user_id
  LEFT JOIN public."delinquencyReason" dr   ON dr.id = c."delinquencyReason_id"
  LEFT JOIN public."Comments" lc            ON lc.id = c.last_comment_id
  LEFT JOIN public."CommentStatuses" ls     ON ls.id = lc.comment_status_id
  LEFT JOIN (SELECT p.clint_id,
                    sum(round(p.amount::numeric, 2)) FILTER (
                        WHERE p.approval_status = true AND p.is_deleted = false AND pr.id IS NULL) AS approved,
                    sum(round(p.amount::numeric, 2)) FILTER (
                        WHERE p.approval_status IS NULL AND p.is_deleted = false) AS pending
               FROM public."Payments" p
               LEFT JOIN public.payment_reversals pr ON pr.payment_id = p.id
              GROUP BY p.clint_id) pay ON pay.clint_id = c.id;

-- --------------------------------------------------------------------------- payments
CREATE OR REPLACE VIEW bi.payments AS
SELECT p.id                                          AS payment_id,
       p.clint_id                                    AS case_id,
       c."accountNumber"                             AS account_number,
       pf.contract_id                                AS contract_id,
       ct.name                                       AS contract_name,
       pf.name                                       AS portfolio_name,
       p.user_id                                     AS recorder_user_id,
       nullif(btrim(concat_ws(' ', ru.frist_name, ru.last_name)), '') AS recorder_name,
       c.user_id                                     AS owner_user_id,
       nullif(btrim(concat_ws(' ', ou.frist_name, ou.last_name)), '') AS owner_name,
       round(p.amount::numeric, 2)                   AS amount,
       CASE WHEN r.id IS NOT NULL THEN 'reversed'
            WHEN p.approval_status IS NULL THEN 'pending'
            WHEN p.approval_status THEN 'approved'
            ELSE 'rejected' END                      AS status,
       (p.approval_status = true AND p.is_deleted = false AND r.id IS NULL) AS counts_as_collected,
       kt.name                                       AS collection_type,
       km.name                                       AS collection_method,
       (p.payment_date AT TIME ZONE 'Asia/Riyadh')::date AS payment_date,
       p.create_at                                   AS created_at,
       (p.create_at AT TIME ZONE 'Asia/Riyadh')::date AS created_date,
       nullif(btrim(concat_ws(' ', au.frist_name, au.last_name)), '') AS approved_by_name,
       p.approval_at                                 AS approved_at,
       r.reversed_at                                 AS reversed_at,
       r.reason                                      AS reversal_reason
  FROM public."Payments" p
  JOIN public.clints c                      ON c.id = p.clint_id
  LEFT JOIN public.payment_reversals r      ON r.payment_id = p.id
  LEFT JOIN public.portfolios pf            ON pf.id = c.portfolios_id
  LEFT JOIN public.contracts ct             ON ct.id = pf.contract_id
  LEFT JOIN public.users ru                 ON ru.id = p.user_id
  LEFT JOIN public.users ou                 ON ou.id = c.user_id
  LEFT JOIN public.users au                 ON au.id = p.approval_by
  LEFT JOIN public."CollectionTypes" kt     ON kt.id = p.collection_type_id
  LEFT JOIN public."CollectionMethods" km   ON km.id = p.collection_method_id
 WHERE p.is_deleted = false OR r.id IS NOT NULL;

-- --------------------------------------------------------------------------- notes
CREATE OR REPLACE VIEW bi.notes AS
SELECT n.id                                          AS note_id,
       n.clint_id                                    AS case_id,
       c."accountNumber"                             AS account_number,
       pf.contract_id                                AS contract_id,
       ct.name                                       AS contract_name,
       pf.name                                       AS portfolio_name,
       n.user_id                                     AS author_user_id,
       nullif(btrim(concat_ws(' ', au.frist_name, au.last_name)), '') AS author_name,
       s.name                                        AS status_name,
       ss.name                                       AS sub_status_name,
       cr.name                                       AS reachability,
       cm.name                                       AS contact_method,
       n.comment                                     AS note_text,
       n.create_at                                   AS created_at,
       (n.create_at AT TIME ZONE 'Asia/Riyadh')::date AS created_date,
       extract(hour FROM n.create_at AT TIME ZONE 'Asia/Riyadh')::int AS created_hour,
       n.next_contact_date                           AS next_contact_at,
       (n.applied_from_comment_id IS NOT NULL)       AS is_copy
  FROM public."Comments" n
  JOIN public.clints c                      ON c.id = n.clint_id
  LEFT JOIN public.portfolios pf            ON pf.id = c.portfolios_id
  LEFT JOIN public.contracts ct             ON ct.id = pf.contract_id
  LEFT JOIN public.users au                 ON au.id = n.user_id
  LEFT JOIN public."CommentStatuses" s      ON s.id = n.comment_status_id
  LEFT JOIN public."CommentStatuses" ss     ON ss.id = n.comment_sub_status_id
  LEFT JOIN public.comment_reachability cr  ON cr.id = n.comment_reachability_id
  LEFT JOIN public."ContactMethods" cm      ON cm.id = n."contactMethod_id"
 WHERE n.is_deleted = false;

-- --------------------------------------------------------------------------- promises
-- Same state rule as v_promise_state (0008), written inline (no cawp_riyadh_today() call).
CREATE OR REPLACE VIEW bi.promises AS
SELECT d.id                                          AS promise_id,
       d.clint_id                                    AS case_id,
       c."accountNumber"                             AS account_number,
       pf.contract_id                                AS contract_id,
       ct.name                                       AS contract_name,
       pf.name                                       AS portfolio_name,
       d.user_id                                     AS recorder_user_id,
       nullif(btrim(concat_ws(' ', ru.frist_name, ru.last_name)), '') AS recorder_name,
       c.user_id                                     AS owner_user_id,
       nullif(btrim(concat_ws(' ', ou.frist_name, ou.last_name)), '') AS owner_name,
       round(d.amount::numeric, 2)                   AS amount,
       coalesce(al.allocated, 0)::numeric(14,2)      AS allocated_amount,
       st.state                                      AS state,
       CASE WHEN st.state IN ('open', 'overdue')
            THEN greatest(round(d.amount::numeric, 2) - coalesce(al.allocated, 0), 0) ELSE 0 END AS remaining_amount,
       (d.payment_date AT TIME ZONE 'Asia/Riyadh')::date AS due_date,
       d.create_at                                   AS created_at,
       (d.create_at AT TIME ZONE 'Asia/Riyadh')::date AS created_date,
       d.cancelled_at                                AS cancelled_at
  FROM public."paymentDates" d
  JOIN public.clints c                      ON c.id = d.clint_id
  LEFT JOIN public.portfolios pf            ON pf.id = c.portfolios_id
  LEFT JOIN public.contracts ct             ON ct.id = pf.contract_id
  LEFT JOIN public.users ru                 ON ru.id = d.user_id
  LEFT JOIN public.users ou                 ON ou.id = c.user_id
  LEFT JOIN LATERAL (SELECT sum(a.amount) AS allocated
                       FROM public.promise_allocations a
                      WHERE a.promise_id = d.id AND a.voided_at IS NULL) al ON true
  CROSS JOIN LATERAL (SELECT CASE
           WHEN d.cancelled_at IS NOT NULL THEN 'cancelled'
           WHEN coalesce(al.allocated, 0) > 0
                AND coalesce(al.allocated, 0) >= round(d.amount::numeric, 2) THEN 'fulfilled'
           WHEN (d.payment_date AT TIME ZONE 'Asia/Riyadh')::date < (now() AT TIME ZONE 'Asia/Riyadh')::date THEN 'overdue'
           ELSE 'open' END::text AS state) st
 WHERE d.is_deleted = false;

-- --------------------------------------------------------------------------- follow-ups
CREATE OR REPLACE VIEW bi.follow_ups AS
SELECT f.id                                          AS follow_up_id,
       f.case_id                                     AS case_id,
       c."accountNumber"                             AS account_number,
       pf.contract_id                                AS contract_id,
       ct.name                                       AS contract_name,
       f.owner_user_id                               AS owner_user_id,
       nullif(btrim(concat_ws(' ', ou.frist_name, ou.last_name)), '') AS owner_name,
       f.status                                      AS status,
       f.source                                      AS source,
       f.next_action                                 AS next_action,
       f.due_at                                      AS due_at,
       (f.due_at AT TIME ZONE 'Asia/Riyadh')::date   AS due_date,
       (f.status = 'open'
        AND (f.due_at AT TIME ZONE 'Asia/Riyadh')::date < (now() AT TIME ZONE 'Asia/Riyadh')::date) AS is_late,
       f.completed_at                                AS completed_at,
       f.completed_late                              AS completed_late,
       f.reschedule_count                            AS reschedule_count,
       f.created_at                                  AS created_at
  FROM public.follow_ups f
  JOIN public.clints c                      ON c.id = f.case_id
  LEFT JOIN public.portfolios pf            ON pf.id = c.portfolios_id
  LEFT JOIN public.contracts ct             ON ct.id = pf.contract_id
  LEFT JOIN public.users ou                 ON ou.id = f.owner_user_id;

-- --------------------------------------------------------------------------- calls
-- duration_seconds stays NULL when unknown (never 0).
CREATE OR REPLACE VIEW bi.calls AS
SELECT a.id                                          AS call_id,
       a.case_id                                     AS case_id,
       c."accountNumber"                             AS account_number,
       pf.contract_id                                AS contract_id,
       ct.name                                       AS contract_name,
       a.collector_user_id                           AS collector_user_id,
       nullif(btrim(concat_ws(' ', cu.frist_name, cu.last_name)), '') AS collector_name,
       a.mode                                        AS mode,
       a.direction                                   AS direction,
       a.state                                       AS state,
       (a.customer_answered_at IS NOT NULL)          AS customer_answered,
       (a.bridged_at IS NOT NULL)                    AS connected,
       a.duration_seconds                            AS duration_seconds,
       w.outcome                                     AS outcome,
       a.campaign_id                                 AS campaign_id,
       a.created_at                                  AS created_at,
       (a.created_at AT TIME ZONE 'Asia/Riyadh')::date AS created_date,
       extract(hour FROM a.created_at AT TIME ZONE 'Asia/Riyadh')::int AS created_hour
  FROM public.call_attempts a
  JOIN public.clints c                      ON c.id = a.case_id
  LEFT JOIN public.portfolios pf            ON pf.id = c.portfolios_id
  LEFT JOIN public.contracts ct             ON ct.id = pf.contract_id
  LEFT JOIN public.users cu                 ON cu.id = a.collector_user_id
  LEFT JOIN public.call_wrapups w           ON w.attempt_id = a.id;

-- --------------------------------------------------------------------------- saved items + runs
CREATE TABLE IF NOT EXISTS bi_saved_reports (
    id          uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id    uuid        NOT NULL,                 -- no FK: legacy code hard-deletes users
    name        text        NOT NULL CHECK (btrim(name) <> '' AND length(name) <= 120),
    spec        jsonb       NOT NULL,
    shared      boolean     NOT NULL DEFAULT false,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    revision    integer     NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_bi_saved_reports_owner ON bi_saved_reports (owner_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS sql_saved_queries (
    id          uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id    uuid        NOT NULL,
    name        text        NOT NULL CHECK (btrim(name) <> '' AND length(name) <= 120),
    sql_text    text        NOT NULL CHECK (length(sql_text) <= 20000),
    builder     jsonb,                                -- the visual builder spec, when saved from it
    shared      boolean     NOT NULL DEFAULT false,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    revision    integer     NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_sql_saved_queries_owner ON sql_saved_queries (owner_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS report_builder_runs (
    id           uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      uuid        NOT NULL,
    kind         text        NOT NULL CHECK (kind IN ('bi_query', 'bi_export', 'sql_run', 'sql_export')),
    dataset      text,
    spec         jsonb,
    sql_text     text        CHECK (sql_text IS NULL OR length(sql_text) <= 20000),
    row_count    integer,
    truncated    boolean     NOT NULL DEFAULT false,
    duration_ms  integer,
    outcome      text        NOT NULL CHECK (outcome IN ('ok', 'refused', 'error', 'timeout')),
    error_code   text,
    created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_report_builder_runs_user ON report_builder_runs (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_report_builder_runs_time ON report_builder_runs (created_at DESC);

-- --------------------------------------------------------------------------- reader grants
-- The SQL page's login may read schema bi and nothing else.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'alitqan_sql_reader') THEN
        EXECUTE 'GRANT USAGE ON SCHEMA bi TO alitqan_sql_reader';
        EXECUTE 'GRANT SELECT ON ALL TABLES IN SCHEMA bi TO alitqan_sql_reader';
    END IF;
END $$;
