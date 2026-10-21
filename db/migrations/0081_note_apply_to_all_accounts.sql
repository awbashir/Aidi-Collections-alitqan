-- 0081 one note for every account of the same debtor (customer feedback 2026-10-21).
--
-- "A client with several accounts: the note is recorded on each account separately; it should
-- be recorded once and apply to all the client's accounts." Every note form (legacy note form,
-- call wrap-up / call result, manual call log, follow-up completion, AI save-as-note) offers
-- "تطبيق على جميع حسابات العميل" (default on in the UI): the same note is then written, in the
-- note's own transaction, to every other ACTIVE account (not archived, not locked) whose
-- identity key equals this account's (services/same_identity.IDENTITY_KEY_SQL, the 0072 index),
-- whatever its owner. services/note_siblings.py.
--
-- "Comments".applied_from_comment_id: on such a copy, the note it was applied from (the note
--   written on the account the collector worked on). NULL for every other note. The copy has
--   the same author, status, sub-status, reachability, contact method, number, text and next
--   contact date; call_id stays NULL (the call belongs to the main account), and it opens no
--   follow-up (the note trigger is switched off for the copies: the follow-up, like the
--   promise, stays on the main account only).
--   No FK: legacy DELETE /comments/<id> hard-deletes notes, and a copy outlives its source.
--   Not mapped in models.py (Comments.as_dict() / model_cols() are legacy wire contracts);
--   read and written with SQL, like comment_sub_status_id (0068).
-- ix_comments_applied_from: partial, only the copies (tiny); the timeline finds the copies of
--   a case's notes through it ("applied to" on the main note).
--
-- Locks / duration ("Comments" ~1.27M rows on the largest install):
--   * ADD COLUMN (nullable, no default) is a catalog change; ACCESS EXCLUSIVE for milliseconds;
--   * the partial index scans the table once inside this transaction (about a second) and
--     blocks writes to "Comments" for that long: run during a deploy (as 0068 did).
-- lock_timeout 5 s (runner default): on a busy server it fails fast and rolls back; re-run.
-- Re-runnable: IF NOT EXISTS.

ALTER TABLE "Comments" ADD COLUMN IF NOT EXISTS applied_from_comment_id uuid;

CREATE INDEX IF NOT EXISTS ix_comments_applied_from
    ON "Comments" (applied_from_comment_id) WHERE applied_from_comment_id IS NOT NULL;

COMMENT ON COLUMN "Comments".applied_from_comment_id IS
    'On a note applied to every account of the same debtor (0081): the note it was copied from (on the main account). NULL otherwise.';
