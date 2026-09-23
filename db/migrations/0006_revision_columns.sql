-- 0006 revision columns for optimistic concurrency (core/revisions.py).
--
-- Adds `revision integer NOT NULL DEFAULT 0` to the mutable core tables the later phases
-- edit, and a BEFORE UPDATE trigger that sets NEW.revision = OLD.revision + 1 whenever
-- the row really changed (and keeps OLD.revision on a no-op UPDATE). The trigger owns the
-- column: a value written by a client is ignored.
--
--   clints           cases            (ignores last_comment_at / last_comment_id, which the
--                                      comments_last_comment_trg trigger rewrites on every
--                                      new note; a note must not make a case form stale)
--   "Comments"       notes
--   "Payments"       payments
--   "ClintContacts"  debtor contacts
--   "paymentDates"   promise-to-pay dates
--
-- The columns are deliberately NOT mapped in models.py: every legacy serializer
-- (`as_dict()`, services/clients_query.py) iterates the mapped columns only, and no
-- legacy query selects `*`, so they never reach a legacy response. New code reads and
-- checks them with core/revisions.py (raw SQL).
--
-- Locks: each ALTER TABLE ... ADD COLUMN with a constant default is metadata-only on
-- PostgreSQL >= 11 (no table rewrite, no backfill) but takes a brief ACCESS EXCLUSIVE
-- lock; CREATE TRIGGER takes SHARE ROW EXCLUSIVE. The runner sets lock_timeout = 5s, so
-- on a busy server the migration fails fast (and rolls back completely) instead of
-- queueing requests behind the lock; simply re-run it. Expected duration: milliseconds.

CREATE OR REPLACE FUNCTION cawp_bump_revision() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    new_row jsonb := to_jsonb(NEW) - 'revision';
    old_row jsonb := to_jsonb(OLD) - 'revision';
BEGIN
    -- Trigger arguments name columns whose changes must not bump the revision.
    IF TG_NARGS > 0 THEN
        new_row := new_row - TG_ARGV;
        old_row := old_row - TG_ARGV;
    END IF;
    IF new_row IS DISTINCT FROM old_row THEN
        NEW.revision := OLD.revision + 1;
    ELSE
        NEW.revision := OLD.revision;
    END IF;
    RETURN NEW;
END $$;

COMMENT ON FUNCTION cawp_bump_revision() IS
    'BEFORE UPDATE row trigger: revision = OLD.revision + 1 when any column other than revision '
    '(and the columns named in the trigger arguments) changed.';

ALTER TABLE clints          ADD COLUMN IF NOT EXISTS revision integer NOT NULL DEFAULT 0;
ALTER TABLE "Comments"      ADD COLUMN IF NOT EXISTS revision integer NOT NULL DEFAULT 0;
ALTER TABLE "Payments"      ADD COLUMN IF NOT EXISTS revision integer NOT NULL DEFAULT 0;
ALTER TABLE "ClintContacts" ADD COLUMN IF NOT EXISTS revision integer NOT NULL DEFAULT 0;
ALTER TABLE "paymentDates"  ADD COLUMN IF NOT EXISTS revision integer NOT NULL DEFAULT 0;

DROP TRIGGER IF EXISTS clints_bump_revision ON clints;
CREATE TRIGGER clints_bump_revision
    BEFORE UPDATE ON clints
    FOR EACH ROW EXECUTE FUNCTION cawp_bump_revision('last_comment_at', 'last_comment_id');

DROP TRIGGER IF EXISTS comments_bump_revision ON "Comments";
CREATE TRIGGER comments_bump_revision
    BEFORE UPDATE ON "Comments"
    FOR EACH ROW EXECUTE FUNCTION cawp_bump_revision();

DROP TRIGGER IF EXISTS payments_bump_revision ON "Payments";
CREATE TRIGGER payments_bump_revision
    BEFORE UPDATE ON "Payments"
    FOR EACH ROW EXECUTE FUNCTION cawp_bump_revision();

DROP TRIGGER IF EXISTS clintcontacts_bump_revision ON "ClintContacts";
CREATE TRIGGER clintcontacts_bump_revision
    BEFORE UPDATE ON "ClintContacts"
    FOR EACH ROW EXECUTE FUNCTION cawp_bump_revision();

DROP TRIGGER IF EXISTS paymentdates_bump_revision ON "paymentDates";
CREATE TRIGGER paymentdates_bump_revision
    BEFORE UPDATE ON "paymentDates"
    FOR EACH ROW EXECUTE FUNCTION cawp_bump_revision();
