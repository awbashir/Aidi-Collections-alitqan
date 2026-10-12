-- The wrap-up note becomes optional (customer decision 2026-10-11; services/calling/wrapup.py).
--
-- 0053 allowed a wrap-up without a legacy "Comments" row only for a locked/archived case, and
-- then required its text. Now a collector may save a wrap-up with no note status (no legacy
-- note row: Comments.comment_status_id stays NOT NULL and untouched), with or without text.
-- The replacement only checks that any stored text is non-empty and within the note limit.
-- Nothing else changes: the review constraints of 0053 still hold.
--
-- Locks: ALTER TABLE ... DROP/ADD CONSTRAINT on call_wrapups (small, Phase 5 table), brief
-- ACCESS EXCLUSIVE lock; ADD CONSTRAINT scans the table once (every existing row satisfies it).

ALTER TABLE call_wrapups DROP CONSTRAINT IF EXISTS call_wrapups_note_recorded;
ALTER TABLE call_wrapups DROP CONSTRAINT IF EXISTS call_wrapups_note_text_valid;
ALTER TABLE call_wrapups ADD CONSTRAINT call_wrapups_note_text_valid CHECK (
    note_text IS NULL OR (btrim(note_text) <> '' AND length(note_text) <= 5000));

COMMENT ON COLUMN call_wrapups.note_text IS
    'The wrap-up note text when no legacy "Comments" row was written (no note status, or a locked/archived case); NULL when the note is on the Comments row or no text was given.';
