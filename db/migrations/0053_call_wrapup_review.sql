-- 0053 wrap-up on a locked or archived case (Phase 5 P1, the lead's decision 2026-09-24).
-- docs/phase5-spec.md, review/phase5-click-to-call.md.
--
-- The call happened, so its facts are never blocked:
--   * the wrap-up is RECORDED (outcome, the note text in call_wrapups.note_text) and the
--     reservation is released (source `wrapup`);
--   * the protected case mutations are NOT applied while the case is locked or archived: no
--     legacy "Comments" row (note_id stays NULL), no callback follow-up, no promise, no contact
--     block (wrong number) and no case calling block (do-not-call). What the collector asked
--     for is kept verbatim in `deferred_actions`, for the supervisor;
--   * the wrap-up is flagged `needs_review` with `review_reason` (case_locked | case_archived),
--     and a call_review_flags row (kind wrapup_needs_review) lists it for supervisors. When a
--     do-not-call or wrong number was deferred, that flag blocks calling the case until resolved.
--
-- call_wrapups stays append-only (its trigger rejects UPDATE/DELETE); these columns are set at
-- insert only. The do-not-call CHECK is relaxed for flagged wrap-ups only.
-- Locks: ALTER TABLE on call_wrapups (small, new table). Instant.

ALTER TABLE call_wrapups ALTER COLUMN note_id DROP NOT NULL;
ALTER TABLE call_wrapups ADD COLUMN IF NOT EXISTS needs_review boolean NOT NULL DEFAULT false;
ALTER TABLE call_wrapups ADD COLUMN IF NOT EXISTS review_reason text;
ALTER TABLE call_wrapups ADD COLUMN IF NOT EXISTS note_text text;
ALTER TABLE call_wrapups ADD COLUMN IF NOT EXISTS deferred_actions jsonb;

ALTER TABLE call_wrapups DROP CONSTRAINT IF EXISTS call_wrapups_review_reason;
ALTER TABLE call_wrapups ADD CONSTRAINT call_wrapups_review_reason CHECK (
    (needs_review AND review_reason IN ('case_locked', 'case_archived'))
    OR (NOT needs_review AND review_reason IS NULL AND deferred_actions IS NULL));

ALTER TABLE call_wrapups DROP CONSTRAINT IF EXISTS call_wrapups_note_recorded;
ALTER TABLE call_wrapups ADD CONSTRAINT call_wrapups_note_recorded CHECK (
    note_id IS NOT NULL
    OR (needs_review AND note_text IS NOT NULL AND btrim(note_text) <> '' AND length(note_text) <= 5000));

ALTER TABLE call_wrapups DROP CONSTRAINT IF EXISTS call_wrapups_review_applies_nothing;
ALTER TABLE call_wrapups ADD CONSTRAINT call_wrapups_review_applies_nothing CHECK (
    NOT needs_review
    OR (note_id IS NULL AND follow_up_id IS NULL AND promise_id IS NULL AND NOT contact_blocked
        AND calling_block_id IS NULL AND jsonb_typeof(deferred_actions) = 'object'));

ALTER TABLE call_wrapups DROP CONSTRAINT IF EXISTS call_wrapups_dnc_blocks_case;
ALTER TABLE call_wrapups ADD CONSTRAINT call_wrapups_dnc_blocks_case CHECK (
    outcome <> 'do_not_call' OR calling_block_id IS NOT NULL OR needs_review);

CREATE INDEX IF NOT EXISTS ix_call_wrapups_needs_review ON call_wrapups (reviewed_at DESC) WHERE needs_review;

COMMENT ON COLUMN call_wrapups.note_text IS
    'The wrap-up note text when no legacy "Comments" row could be written (locked/archived case).';
COMMENT ON COLUMN call_wrapups.deferred_actions IS
    'The case changes the collector asked for but that were NOT applied because the case was locked/archived.';
