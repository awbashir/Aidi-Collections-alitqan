-- Denormalise "latest follow-up" onto clints so the grids / neglect report can sort and
-- filter without a LATERAL over 1.27M comments per request.
--   clints.last_comment_at  = max(create_at) of non-deleted comments
--   clints.last_comment_id  = id of that comment (join by PK for status/colour)
-- Maintained by a row-level trigger on "Comments" (insert/update/delete). Idempotent.
\timing on

ALTER TABLE clints ADD COLUMN IF NOT EXISTS last_comment_at timestamptz;
ALTER TABLE clints ADD COLUMN IF NOT EXISTS last_comment_id uuid;

CREATE OR REPLACE FUNCTION clints_refresh_last_comment(p_clint uuid) RETURNS void
LANGUAGE sql AS $$
  UPDATE clints c
     SET last_comment_at = lc.create_at,
         last_comment_id = lc.id
    FROM (SELECT p_clint AS clint_id,
                 (SELECT m.create_at FROM "Comments" m WHERE m.clint_id = p_clint AND m.is_deleted = false
                   ORDER BY m.create_at DESC, m.id DESC LIMIT 1) AS create_at,
                 (SELECT m.id FROM "Comments" m WHERE m.clint_id = p_clint AND m.is_deleted = false
                   ORDER BY m.create_at DESC, m.id DESC LIMIT 1) AS id) lc
   WHERE c.id = lc.clint_id
     AND (c.last_comment_at IS DISTINCT FROM lc.create_at OR c.last_comment_id IS DISTINCT FROM lc.id);
$$;

CREATE OR REPLACE FUNCTION trg_comments_last_comment() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    PERFORM clints_refresh_last_comment(OLD.clint_id);
    RETURN OLD;
  END IF;
  PERFORM clints_refresh_last_comment(NEW.clint_id);
  IF TG_OP = 'UPDATE' AND NEW.clint_id IS DISTINCT FROM OLD.clint_id THEN
    PERFORM clints_refresh_last_comment(OLD.clint_id);
  END IF;
  RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS comments_last_comment_trg ON "Comments";
CREATE TRIGGER comments_last_comment_trg
AFTER INSERT OR UPDATE OF create_at, is_deleted, clint_id OR DELETE ON "Comments"
FOR EACH ROW EXECUTE FUNCTION trg_comments_last_comment();

-- Backfill (one statement, uses ix_comments_clint_created)
UPDATE clints c
   SET last_comment_at = lc.create_at, last_comment_id = lc.id
  FROM (SELECT DISTINCT ON (clint_id) clint_id, id, create_at
          FROM "Comments" WHERE is_deleted = false
         ORDER BY clint_id, create_at DESC, id DESC) lc
 WHERE lc.clint_id = c.id
   AND (c.last_comment_at IS DISTINCT FROM lc.create_at OR c.last_comment_id IS DISTINCT FROM lc.id);

CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_clints_last_comment_live
    ON clints (last_comment_at DESC NULLS LAST) WHERE is_archive = false;
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_clints_user_last_comment_live
    ON clints (user_id, last_comment_at DESC NULLS LAST) WHERE is_archive = false;
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_clints_last_comment_id
    ON clints (last_comment_id) WHERE last_comment_id IS NOT NULL;

VACUUM (ANALYZE) clints;

-- ASC walk for the neglect report's default order (oldest follow-up first); ASC is NULLS LAST by default
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_clints_last_comment_live_asc
    ON clints (last_comment_at ASC NULLS LAST) WHERE is_archive = false;
