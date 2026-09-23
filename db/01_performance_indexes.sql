-- CAWP database performance pass (idempotent, production-safe).
-- Run with:  psql -U postgres -d mydata -f db/01_performance_indexes.sql
-- Uses CREATE INDEX CONCURRENTLY -> must NOT run inside a transaction block (psql default is fine).
-- Findings on the 2026-09-15 dump: 4 exact duplicate index pairs + 2 prefix-redundant indexes
-- (415 MB of indexes on the 255 MB "SepcifyUsers" table), 20 unindexed FKs, no index on
-- "ClintContacts"."identityNumber" (1.8M rows, joined to clints by that column).

\set ON_ERROR_STOP off
\timing on

-------------------------------------------------------------------------------
-- 1. Drop exact duplicates / prefix-redundant indexes
-------------------------------------------------------------------------------
DROP INDEX CONCURRENTLY IF EXISTS "ix_Comments_clint_id";              -- dup of ix_comments_clint_id
DROP INDEX CONCURRENTLY IF EXISTS ix_comments_clint_created_live;      -- dup of ix_comments_clint_created
DROP INDEX CONCURRENTLY IF EXISTS ix_specify_clint_created_live;       -- dup of ix_specify_clint_created
DROP INDEX CONCURRENTLY IF EXISTS ix_specifyusers_user_id;             -- dup of ix_sepcifyusers_user_id
DROP INDEX CONCURRENTLY IF EXISTS ix_sepcifyusers_user_id;             -- prefix of ix_sepcifyusers_user_clint(user_id, clint_id)
DROP INDEX CONCURRENTLY IF EXISTS ix_sepcifyusers_clint_id;            -- prefix of ix_specifyusers_clint_notdel(clint_id, is_deleted)

-------------------------------------------------------------------------------
-- 2. Extensions
-------------------------------------------------------------------------------
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-------------------------------------------------------------------------------
-- 3. Missing indexes on hot paths
-------------------------------------------------------------------------------
-- clients
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_clints_identity
    ON clints ("identityNumber");
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_clints_name_trgm
    ON clints USING gin (name gin_trgm_ops);
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_clints_identity_trgm
    ON clints USING gin ("identityNumber" gin_trgm_ops);
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_clints_delinquency
    ON clints ("delinquencyReason_id") WHERE "delinquencyReason_id" IS NOT NULL;
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_clints_archive_create
    ON clints (create_at DESC) WHERE is_archive = true;
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_clints_live_create
    ON clints (create_at DESC) WHERE is_archive = false;

-- client contacts (joined by identityNumber, 12 contacts / client on average)
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_clintcontacts_identity
    ON "ClintContacts" ("identityNumber");
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_clintcontacts_identity_default
    ON "ClintContacts" ("identityNumber") WHERE is_default = true AND is_deleted = false;

-- comments (call notes)
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_comments_user_created
    ON "Comments" (user_id, create_at DESC) WHERE is_deleted = false;
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_comments_create_at
    ON "Comments" (create_at DESC) WHERE is_deleted = false;
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_comments_status
    ON "Comments" (comment_status_id);
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_comments_next_contact
    ON "Comments" (user_id, next_contact_date) WHERE next_contact_date IS NOT NULL AND is_deleted = false;

-- payments
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_payments_user_created
    ON "Payments" (user_id, create_at DESC) WHERE is_deleted = false;
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_payments_create_at
    ON "Payments" (create_at DESC) WHERE is_deleted = false;
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_payments_payment_date
    ON "Payments" (payment_date) WHERE is_deleted = false;
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_payments_approval_status
    ON "Payments" (approval_status, create_at DESC) WHERE is_deleted = false;
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_payments_approval_by
    ON "Payments" (approval_by) WHERE approval_by IS NOT NULL;
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_payments_collection_type
    ON "Payments" (collection_type_id);
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_payments_collection_method
    ON "Payments" (collection_method_id);

-- promises to pay
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_paymentdates_payment_date
    ON "paymentDates" (payment_date) WHERE is_deleted = false;
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_paymentdates_user_date
    ON "paymentDates" (user_id, payment_date) WHERE is_deleted = false;

-- archive records
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_clintarchive_user_id
    ON "clintArchive" (user_id);
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_clintarchive_reason
    ON "clintArchive" (archive_reason_id);
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_clintarchive_create
    ON "clintArchive" (create_at DESC);

-- lock history
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_lockhistory_clint_created
    ON client_lock_history (clint_id, created_at DESC);
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_lockhistory_user
    ON client_lock_history (user_id);

-- notifications / login log
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_notifications_receiver_created
    ON notifications (receiver_user_id, created_at DESC);
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_notifications_receiver_unread
    ON notifications (receiver_user_id) WHERE is_read IS NOT TRUE;
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_notifications_sender
    ON notifications (sender_user_id);
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_loginlog_username_ts
    ON login_log (username, "timestamp" DESC);

-- users (small table, but FK lookups + role/branch filters)
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_users_role_id ON users (role_id);
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_users_branch_id ON users (branch_id);
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_users_nationality_id ON users (nationality_id);

-- comment statuses per contract + reachability
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_commentstatuses_reachability
    ON "CommentStatuses" ("CommentReachability_id");

-------------------------------------------------------------------------------
-- 4. Autovacuum: the big churny tables should be vacuumed far more often than
--    the default 20% dead-tuple threshold
-------------------------------------------------------------------------------
ALTER TABLE clints          SET (autovacuum_vacuum_scale_factor = 0.02, autovacuum_analyze_scale_factor = 0.01);
ALTER TABLE "SepcifyUsers"  SET (autovacuum_vacuum_scale_factor = 0.02, autovacuum_analyze_scale_factor = 0.01);
ALTER TABLE "Comments"      SET (autovacuum_vacuum_scale_factor = 0.02, autovacuum_analyze_scale_factor = 0.01);
ALTER TABLE "ClintContacts" SET (autovacuum_vacuum_scale_factor = 0.02, autovacuum_analyze_scale_factor = 0.01);

ANALYZE;
