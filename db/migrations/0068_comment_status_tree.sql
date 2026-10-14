-- 0068 contract note statuses ("حالات العقود"): general statuses, one level of sub-statuses,
-- soft delete, and an optional sub-status on a note (customer request 2026-10).
--
-- "CommentStatuses":
--   * contract_id NULL already exists and now means "a general status for every contract"
--     (every note writer accepts it; the pickers list it next to the contract's own);
--   * parent_id: a sub-status points at its main status. ONE level only (a parent is a
--     top-level status; a status with children cannot become a child). A sub-status always
--     carries its parent's contract_id and "CommentReachability_id": the API copies them on
--     create/update and propagates a parent's change to its children
--     (services/comment_status_tree.py). Not enforced by a trigger: the settings API is the
--     only writer of this small table;
--   * deleted_at / deleted_by: DELETE /comment_status/<id> is a SOFT delete (of the status and
--     its sub-statuses). Old notes keep pointing at the row, so timelines and reports still
--     show its name; new notes refuse it (`status_deleted`); the lists hide it.
--   `is_active` keeps its legacy meaning (the "inactive" outcome class read by reports and
--   dashboards), it is not an enabled flag.
--
-- "Comments".comment_sub_status_id: the optional sub-status of a note, a live child of the
-- note's comment_status_id at write time (checked by the API, not the database). NULL for every
-- existing note and for notes written without one. comment_status_id stays the MAIN status, so
-- every existing report, filter and dashboard keeps grouping by it unchanged.
--
-- Neither column is mapped in models.py: as_dict()/model_cols() of both models feed legacy wire
-- contracts (and the parity harnesses), so new keys are added only where a route asks for them.
--
-- Locks / duration ("Comments" holds ~1.27M rows on cawp-db):
--   * ADD COLUMN (nullable, no default) is a catalog change; ACCESS EXCLUSIVE for milliseconds;
--   * the FK is added NOT VALID (no scan) and left NOT VALID: the column is new and NULL in
--     every existing row, so there is nothing to validate, and every new or updated row is
--     checked anyway. A VALIDATE here would scan 1.27M rows while this transaction still holds
--     the ACCESS EXCLUSIVE lock from ADD COLUMN (it can be validated later, online, with
--     `ALTER TABLE "Comments" VALIDATE CONSTRAINT comments_sub_status_fk`);
--   * the partial index on "Comments" scans the table once inside this transaction (about a
--     second on cawp-db) and blocks writes to "Comments" for that long: run during a deploy.
--     It holds only the notes that have a sub-status, so it stays tiny;
--   * "CommentStatuses" is a small settings table.
-- lock_timeout 5 s (runner default): on a busy server it fails fast and rolls back; re-run.
-- Re-runnable: IF NOT EXISTS everywhere, the constraints are added only when missing.

ALTER TABLE "CommentStatuses" ADD COLUMN IF NOT EXISTS parent_id uuid;
ALTER TABLE "CommentStatuses" ADD COLUMN IF NOT EXISTS deleted_at timestamptz;
ALTER TABLE "CommentStatuses" ADD COLUMN IF NOT EXISTS deleted_by uuid;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conrelid = '"CommentStatuses"'::regclass
                      AND conname = 'commentstatuses_parent_fk') THEN
        ALTER TABLE "CommentStatuses"
            ADD CONSTRAINT commentstatuses_parent_fk
            FOREIGN KEY (parent_id) REFERENCES "CommentStatuses"(id);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conrelid = '"CommentStatuses"'::regclass
                      AND conname = 'commentstatuses_not_own_parent') THEN
        ALTER TABLE "CommentStatuses"
            ADD CONSTRAINT commentstatuses_not_own_parent CHECK (parent_id IS NULL OR parent_id <> id);
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS ix_commentstatuses_parent
    ON "CommentStatuses" (parent_id) WHERE parent_id IS NOT NULL;

ALTER TABLE "Comments" ADD COLUMN IF NOT EXISTS comment_sub_status_id uuid;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conrelid = '"Comments"'::regclass
                      AND conname = 'comments_sub_status_fk') THEN
        ALTER TABLE "Comments"
            ADD CONSTRAINT comments_sub_status_fk
            FOREIGN KEY (comment_sub_status_id) REFERENCES "CommentStatuses"(id) NOT VALID;
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS ix_comments_sub_status
    ON "Comments" (comment_sub_status_id) WHERE comment_sub_status_id IS NOT NULL;

COMMENT ON COLUMN "CommentStatuses".contract_id IS
    'The contract this status belongs to; NULL = a general status usable by every contract.';
COMMENT ON COLUMN "CommentStatuses".parent_id IS
    'The main status of a sub-status (one level only; copies the parent''s contract and reachability). NULL = a main status.';
COMMENT ON COLUMN "CommentStatuses".deleted_at IS
    'Soft delete: hidden from the lists and refused for new notes; existing notes keep showing it.';
COMMENT ON COLUMN "CommentStatuses".deleted_by IS 'The user who deleted the status (users.id).';
COMMENT ON COLUMN "Comments".comment_sub_status_id IS
    'Optional sub-status of the note: a child of comment_status_id (the main status) when the note was written.';
