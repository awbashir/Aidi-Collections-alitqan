-- 0011 backfill: one open follow-up per live case from the legacy "next calls" rule.
--
-- Legacy rule (services/clients_query.build_next_calls_query, route
-- /clints/currentUser/have_nextCalls): a case is on its owner's next-calls list when it is
-- not archived and one of its notes carries a callback (`"Comments".next_contact_date IS NOT
-- NULL`). The paged screen lists every such note (one row per callback, filtered by the
-- callback's date range, default: the current month); later notes without a callback do not
-- remove it. The new model keeps one open follow-up per case, so each live case gets the
-- callback of its most recently recorded callback note (create_at DESC, id DESC) - the same
-- choice the note trigger (0010) makes when a newer callback note arrives. Deleted notes are
-- skipped (there are none today; the trigger skips them too).
--
--   source 'imported', created_by NULL (system), due_at = original_due_at = next_contact_date,
--   source_comment_id = that note, owner = clints.user_id, one 'imported' event each.
--
-- Old callbacks stay: a callback due before today is imported as an open, late follow-up
-- (a missed callback stays a recorded fact), exactly as the legacy report still lists it.
--
-- Idempotent and safe next to the live trigger: a case that already has any follow-up (for
-- example one opened by a note saved between 0010 and 0011) is skipped, and the insert uses
-- ON CONFLICT on the one-open index. Re-running `SELECT worklist_backfill_follow_ups()`
-- inserts nothing the second time.
--
-- Locks / duration: reads "Comments" (partial index ix_comments_next_contact, ~23k rows) and
-- clints by primary key with plain SELECTs (ACCESS SHARE, no row locks); writes only the new
-- tables. Measured on the full local copy (474k cases, 1.27M notes): see
-- review/phase1-worklist.md (about 0.2 s for ~3.2k follow-ups).

CREATE OR REPLACE FUNCTION worklist_backfill_follow_ups() RETURNS integer
LANGUAGE plpgsql AS $$
DECLARE
    n integer;
BEGIN
    WITH candidates AS (
        SELECT DISTINCT ON (m.clint_id)
               m.clint_id, m.id AS comment_id, m.next_contact_date
          FROM "Comments" m
         WHERE m.next_contact_date IS NOT NULL
           AND m.is_deleted = false
         ORDER BY m.clint_id, m.create_at DESC, m.id DESC
    ),
    ins AS (
        INSERT INTO follow_ups (case_id, owner_user_id, due_at, original_due_at, status, source,
                                source_comment_id, created_by)
        SELECT k.clint_id, c.user_id, k.next_contact_date, k.next_contact_date, 'open', 'imported',
               k.comment_id, NULL
          FROM candidates k
          JOIN clints c ON c.id = k.clint_id
         WHERE c.is_archive = false
           AND NOT EXISTS (SELECT 1 FROM follow_ups f WHERE f.case_id = k.clint_id)
        ON CONFLICT (case_id) WHERE status = 'open' DO NOTHING
        RETURNING id, case_id, due_at, source_comment_id
    ),
    ev AS (
        INSERT INTO follow_up_events (follow_up_id, case_id, event, due_at_after, was_late, comment_id,
                                      reason, source)
        SELECT ins.id, ins.case_id, 'imported', ins.due_at,
               worklist_riyadh_date(ins.due_at) < worklist_riyadh_date(now()), ins.source_comment_id,
               'imported from the legacy next-calls rule (latest callback note of a live case)', 'migration'
          FROM ins
        RETURNING 1
    )
    SELECT count(*) INTO n FROM ev;
    RETURN n;
END $$;

COMMENT ON FUNCTION worklist_backfill_follow_ups() IS
    'Stream B backfill (0011): one open imported follow-up per live case from its latest callback note; '
    'skips cases that already have a follow-up. Returns the number inserted.';

SELECT worklist_backfill_follow_ups();

ANALYZE follow_ups;
ANALYZE follow_up_events;
