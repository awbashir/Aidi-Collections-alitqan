-- Convert clints.attribute from json to jsonb (binary, indexable, faster to read).
-- Rewrites the 619 MB clints table under an ACCESS EXCLUSIVE lock: ~1-3 minutes.
-- In production run during a maintenance window. Idempotent.
\timing on
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.columns
             WHERE table_schema='public' AND table_name='clints'
               AND column_name='attribute' AND data_type='json') THEN
    ALTER TABLE clints ALTER COLUMN attribute TYPE jsonb USING attribute::jsonb;
  END IF;
END $$;
VACUUM (ANALYZE) clints;
