-- migrate:no-transaction
-- 0027 index for the spreadsheet inquiry's mobile matching (Phase 2, stream D).
--
-- The inquiry matches a spreadsheet mobile against "ClintContacts".contact through
-- `cawp_norm_mobile(contact)` (0026). "ClintContacts" (1.8M rows on cawp-db) has no index on
-- `contact`, so without this every inquiry chunk would scan the table. Partial: only live
-- (not deleted) contacts whose value is a Saudi mobile (~1.26M entries on cawp-db); e-mails,
-- landlines and junk are not indexed. Queries use
--     cawp_norm_mobile(cc.contact) = ANY(:mobiles) AND cc.is_deleted = false
-- which implies the predicate (the equality is strict, so the value is NOT NULL).
--
-- Write cost: one function evaluation (a few regular expressions, microseconds) and one index
-- insertion per inserted or contact-changing "ClintContacts" row.
--
-- CREATE INDEX CONCURRENTLY: no lock that blocks reads or writes; it must run outside a
-- transaction (hence the header) and is idempotent (IF NOT EXISTS). If a build is interrupted
-- it leaves an INVALID index: drop it (DROP INDEX CONCURRENTLY) and re-run.

CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_clintcontacts_mobile_norm
    ON "ClintContacts" (cawp_norm_mobile(contact))
    WHERE is_deleted = false AND cawp_norm_mobile(contact) IS NOT NULL;
