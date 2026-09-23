-- 0014 contact history and block flag (Phase 1, stream C).
--
-- 1. Block flag. `"ClintContacts".contact_status` is NOT a block flag: on the full local
--    copy every one of the 1,797,999 rows holds 'new' (the value the importers write), no
--    code reads it, and no other value exists. So the block state is new, unmapped
--    columns (models.py is unchanged, so no legacy response shows them):
--      is_blocked boolean NOT NULL DEFAULT false, blocked_at, blocked_by, block_reason.
--    A CHECK (NOT VALID: existing rows are all unblocked, and validating would scan 1.8M
--    rows under the ALTER's ACCESS EXCLUSIVE lock) makes every new/updated blocked row
--    carry when/who/why.
-- 2. Statement-level AFTER INSERT / UPDATE / DELETE triggers on "ClintContacts" write one
--    `audit_events` row per inserted / really changed / deleted contact row, with the
--    full before/after image and the transaction-local v2 context (cawp_hist_ctx, 0013).
--    Legacy writes (POST /clint_contact(s), DELETE /clint_contact/<id>, the default
--    routes, the Excel imports) are recorded with actor NULL and source 'db_trigger'.
-- 3. An expression index on audit_events finds a debtor's contact history by
--    identityNumber (contacts are linked to the debtor, not to a case, so case_id is NULL).
--
-- Locks: ADD COLUMN with constant defaults is metadata-only (no rewrite) but takes a brief
-- ACCESS EXCLUSIVE lock on "ClintContacts"; CREATE TRIGGER takes SHARE ROW EXCLUSIVE.
-- lock_timeout 5 s (runner default): on a busy server it fails fast and rolls back
-- completely; re-run. The index is on audit_events (small, new-code-only table).

ALTER TABLE "ClintContacts" ADD COLUMN IF NOT EXISTS is_blocked   boolean NOT NULL DEFAULT false;
ALTER TABLE "ClintContacts" ADD COLUMN IF NOT EXISTS blocked_at   timestamptz;
ALTER TABLE "ClintContacts" ADD COLUMN IF NOT EXISTS blocked_by   uuid;
ALTER TABLE "ClintContacts" ADD COLUMN IF NOT EXISTS block_reason text;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conname = 'clintcontacts_block_fields'
                      AND conrelid = '"ClintContacts"'::regclass) THEN
        ALTER TABLE "ClintContacts" ADD CONSTRAINT clintcontacts_block_fields
            CHECK (NOT is_blocked
                   OR (blocked_at IS NOT NULL AND block_reason IS NOT NULL AND btrim(block_reason) <> ''))
            NOT VALID;
    END IF;
END $$;

COMMENT ON COLUMN "ClintContacts".is_blocked IS
    'v2 block flag (do not use this number). Not mapped in models.py. contact_status is not a block flag.';

-- ---------------------------------------------------------------------------------------
-- "ClintContacts" INSERT / UPDATE / DELETE -> audit_events
-- ---------------------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION cawp_hist_audit_contacts_ins() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO audit_events (actor_user_id, action, entity_type, entity_id, case_id,
                              before, after, reason, request_id, idempotency_key, source)
    SELECT cawp_hist_ctx('actor')::uuid,
           coalesce(cawp_hist_ctx('action'), 'contact.insert'),
           'contact', n.id::text, NULL,
           NULL, to_jsonb(n),
           cawp_hist_ctx('reason'), cawp_hist_ctx('request_id'), cawp_hist_ctx('idempotency_key'),
           coalesce(cawp_hist_ctx('source'), 'db_trigger')
      FROM cawp_hist_new n;
    RETURN NULL;
END $$;

CREATE OR REPLACE FUNCTION cawp_hist_audit_contacts_upd() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO audit_events (actor_user_id, action, entity_type, entity_id, case_id,
                              before, after, reason, request_id, idempotency_key, source)
    SELECT cawp_hist_ctx('actor')::uuid,
           coalesce(cawp_hist_ctx('action'), 'contact.update'),
           'contact', n.id::text, NULL,
           to_jsonb(o), to_jsonb(n),
           cawp_hist_ctx('reason'), cawp_hist_ctx('request_id'), cawp_hist_ctx('idempotency_key'),
           coalesce(cawp_hist_ctx('source'), 'db_trigger')
      FROM cawp_hist_old o
      JOIN cawp_hist_new n ON n.id = o.id
     WHERE (o.*) IS DISTINCT FROM (n.*);
    RETURN NULL;
END $$;

CREATE OR REPLACE FUNCTION cawp_hist_audit_contacts_del() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO audit_events (actor_user_id, action, entity_type, entity_id, case_id,
                              before, after, reason, request_id, idempotency_key, source)
    SELECT cawp_hist_ctx('actor')::uuid,
           coalesce(cawp_hist_ctx('action'), 'contact.delete'),
           'contact', o.id::text, NULL,
           to_jsonb(o), NULL,
           cawp_hist_ctx('reason'), cawp_hist_ctx('request_id'), cawp_hist_ctx('idempotency_key'),
           coalesce(cawp_hist_ctx('source'), 'db_trigger')
      FROM cawp_hist_old o;
    RETURN NULL;
END $$;

DROP TRIGGER IF EXISTS clintcontacts_hist_audit_ins ON "ClintContacts";
CREATE TRIGGER clintcontacts_hist_audit_ins
    AFTER INSERT ON "ClintContacts"
    REFERENCING NEW TABLE AS cawp_hist_new
    FOR EACH STATEMENT EXECUTE FUNCTION cawp_hist_audit_contacts_ins();

DROP TRIGGER IF EXISTS clintcontacts_hist_audit_upd ON "ClintContacts";
CREATE TRIGGER clintcontacts_hist_audit_upd
    AFTER UPDATE ON "ClintContacts"
    REFERENCING OLD TABLE AS cawp_hist_old NEW TABLE AS cawp_hist_new
    FOR EACH STATEMENT EXECUTE FUNCTION cawp_hist_audit_contacts_upd();

DROP TRIGGER IF EXISTS clintcontacts_hist_audit_del ON "ClintContacts";
CREATE TRIGGER clintcontacts_hist_audit_del
    AFTER DELETE ON "ClintContacts"
    REFERENCING OLD TABLE AS cawp_hist_old
    FOR EACH STATEMENT EXECUTE FUNCTION cawp_hist_audit_contacts_del();

-- Debtor contact history: WHERE entity_type = 'contact' AND <expr> = :identity ORDER BY occurred_at DESC, id DESC
CREATE INDEX IF NOT EXISTS ix_audit_events_contact_identity
    ON audit_events ((coalesce(after ->> 'identityNumber', before ->> 'identityNumber')),
                     occurred_at DESC, id DESC)
    WHERE entity_type = 'contact';
