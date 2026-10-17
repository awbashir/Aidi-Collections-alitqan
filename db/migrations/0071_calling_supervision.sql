-- 0071 calling supervision (customer feedback 2026-10-17; services/calling/wrapup.py, review.py,
-- guards.py).
--
-- A wrap-up is authorized from the ATTEMPT (its collector, or calls_manage), not from the case's
-- current scope. When the case was hard-deleted (legacy DELETE /clint, /clints, /portfolios) or
-- reassigned to another collector after the call, the wrap-up is still recorded and the
-- reservation released (source `wrapup`), but - as for a locked/archived case (0053) - no case
-- change is applied: it is saved `needs_review` with review_reason case_deleted | case_reassigned,
-- the requested changes kept in `deferred_actions`, and a wrapup_needs_review flag raised.
--
-- Only the CHECK on review_reason changes (two more values). call_review_flags.reason is free
-- text already; supervisor release of a stuck reservation uses the existing `reconciled` source;
-- supervisor-created calling blocks use the existing `manual` source.
--
-- Locks: ALTER TABLE ... DROP/ADD CONSTRAINT on call_wrapups (small, Phase 5 table), brief
-- ACCESS EXCLUSIVE lock; ADD CONSTRAINT scans the table once (every existing row satisfies it).

ALTER TABLE call_wrapups DROP CONSTRAINT IF EXISTS call_wrapups_review_reason;
ALTER TABLE call_wrapups ADD CONSTRAINT call_wrapups_review_reason CHECK (
    (needs_review AND review_reason IN ('case_locked', 'case_archived', 'case_deleted', 'case_reassigned'))
    OR (NOT needs_review AND review_reason IS NULL AND deferred_actions IS NULL));

COMMENT ON COLUMN call_wrapups.review_reason IS
    'Why the wrap-up was saved for supervisor review without applying case changes: case_locked, '
    'case_archived, case_deleted (the case row no longer exists) or case_reassigned (the case left '
    'the collector''s scope after the call).';
