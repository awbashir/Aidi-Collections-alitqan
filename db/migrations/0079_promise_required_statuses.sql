-- 0079 a promise-to-pay note needs a promise (customer decision 2026-10-20).
--
-- Production 2026-10-20: the promises page and every promise report were empty ("paymentDates"
-- had 0 rows) although collectors chose the note status «وعد بالسداد» 327 times and the call
-- outcome promise_discussed 97 times: a promise was only created from the case's promises panel,
-- and the wrap-up could only LINK an existing one. Now choosing such a status (or the outcome
-- promise_discussed) needs the promise AMOUNT and DUE DATE, and the promise is created in the
-- same transaction as the note / wrap-up (services/promise_required.py). An open promise of the
-- case may be linked instead of a new one.
--
-- "CommentStatuses".requires_promise: the settings flag ("يتطلب وعد سداد (مبلغ وتاريخ)"). A
--   sub-status always carries its main status's flag (copied on create/update and propagated
--   by services/comment_status_tree.py, like contract_id and the reachability). Set here for the
--   live main statuses named «وعد بالسداد» (trimmed) and their sub-statuses.
-- "paymentDates".source / source_comment_id: where an app-created promise came from
--   (call_wrapup = a wrap-up or a manual call log, note = the legacy note form POST /comments,
--   follow_up = a follow-up completion) and the note written with it. NULL = the promises panel
--   (POST /v2/cases/<id>/promises), the legacy POST /payment_date and every existing row.
--   Not mapped in models.py (PaymentDates.as_dict() is a legacy wire contract).
-- call_wrapups: a promise may now be linked to a promise_discussed wrap-up as well as to a
--   connected one (the 0051 CHECK allowed `connected` only).
--
-- Locks / duration: ADD COLUMN with a constant default is a catalog change (PG 11+);
-- "CommentStatuses" is a small settings table; "paymentDates" is small; the new call_wrapups
-- CHECK is added NOT VALID and validated in the same transaction (a few thousand rows).
-- Re-runnable: IF NOT EXISTS / guarded constraints; the UPDATEs only touch rows that differ.

ALTER TABLE "CommentStatuses" ADD COLUMN IF NOT EXISTS requires_promise boolean NOT NULL DEFAULT false;

UPDATE "CommentStatuses"
   SET requires_promise = true
 WHERE parent_id IS NULL AND deleted_at IS NULL AND btrim(name) = 'وعد بالسداد' AND NOT requires_promise;

UPDATE "CommentStatuses" c
   SET requires_promise = p.requires_promise
  FROM "CommentStatuses" p
 WHERE c.parent_id = p.id AND c.requires_promise IS DISTINCT FROM p.requires_promise;

ALTER TABLE "paymentDates" ADD COLUMN IF NOT EXISTS source text;
ALTER TABLE "paymentDates" ADD COLUMN IF NOT EXISTS source_comment_id uuid;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conrelid = '"paymentDates"'::regclass AND conname = 'paymentdates_source_check') THEN
        ALTER TABLE "paymentDates"
            ADD CONSTRAINT paymentdates_source_check
            CHECK (source IS NULL OR source IN ('call_wrapup', 'note', 'follow_up'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conrelid = 'call_wrapups'::regclass AND conname = 'call_wrapups_promise_outcome') THEN
        ALTER TABLE call_wrapups
            ADD CONSTRAINT call_wrapups_promise_outcome
            CHECK (promise_id IS NULL OR outcome IN ('connected', 'promise_discussed')) NOT VALID;
        ALTER TABLE call_wrapups VALIDATE CONSTRAINT call_wrapups_promise_outcome;
    END IF;
END $$;

ALTER TABLE call_wrapups DROP CONSTRAINT IF EXISTS call_wrapups_promise_only_when_agreed;

COMMENT ON COLUMN "CommentStatuses".requires_promise IS
    'A note with this status needs a promise (amount + due date), created with the note (0079). A sub-status carries its main status''s flag.';
COMMENT ON COLUMN "paymentDates".source IS
    'Where an app-created promise came from: call_wrapup | note | follow_up; NULL = the promises panel, legacy POST /payment_date, older rows (0079).';
COMMENT ON COLUMN "paymentDates".source_comment_id IS
    'The "Comments" note written in the same transaction as this promise (0079), when there is one.';
