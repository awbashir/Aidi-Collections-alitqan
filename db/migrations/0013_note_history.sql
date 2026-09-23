-- 0013 note history (Phase 1, stream C): legacy note changes are audited, v2 corrections
-- keep the original text.
--
-- 1. `cawp_hist_ctx(name)`: reads the transaction-local audit context that v2 code sets
--    with set_config('cawp_hist.<name>', value, true) (services/case_history_common.py):
--    actor, action, reason, request_id, idempotency_key, source. Legacy writers never set
--    it, so their rows are recorded with actor NULL (unknown stays unknown), the default
--    action and source 'db_trigger'.
-- 2. Statement-level AFTER UPDATE and AFTER DELETE triggers on "Comments" (transition
--    tables) write one `audit_events` row per changed/deleted note with the full before
--    (and after) image. A legacy hard delete (DELETE /comments/<id>, or the cascade from
--    DELETE /clints) therefore leaves a record. No-op updates are skipped. INSERT is not
--    audited here (stream B owns an AFTER INSERT trigger on "Comments"; the new row
--    itself is the record of an insert).
-- 3. `note_revisions`: append-only. The first v2 correction stores the original text as
--    revision 0 ('original'), then each correction as 1, 2, ... ('correction', reason
--    required). "Comments".comment always holds the latest text, so every legacy reader
--    shows the corrected text; user_id and create_at of the note never change.
--    No FK to "Comments": the legacy API hard-deletes notes and the history must survive.
--
-- Locks: CREATE TRIGGER takes SHARE ROW EXCLUSIVE on "Comments" for milliseconds (the
-- runner's lock_timeout of 5 s makes it fail fast on a busy table; re-run). New table and
-- functions otherwise. No table rewrite, no backfill.

CREATE OR REPLACE FUNCTION cawp_hist_ctx(p_name text) RETURNS text
LANGUAGE sql STABLE AS $$
    SELECT nullif(current_setting('cawp_hist.' || p_name, true), '')
$$;

COMMENT ON FUNCTION cawp_hist_ctx(text) IS
    'Transaction-local audit context set by v2 code (cawp_hist.actor/action/reason/request_id/'
    'idempotency_key/source); NULL when unset (legacy writers).';

-- ---------------------------------------------------------------------------------------
-- "Comments" UPDATE / DELETE -> audit_events
-- ---------------------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION cawp_hist_audit_comments_upd() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO audit_events (actor_user_id, action, entity_type, entity_id, case_id,
                              before, after, reason, request_id, idempotency_key, source)
    SELECT cawp_hist_ctx('actor')::uuid,
           coalesce(cawp_hist_ctx('action'), 'comment.update'),
           'comment', n.id::text, n.clint_id,
           to_jsonb(o), to_jsonb(n),
           cawp_hist_ctx('reason'), cawp_hist_ctx('request_id'), cawp_hist_ctx('idempotency_key'),
           coalesce(cawp_hist_ctx('source'), 'db_trigger')
      FROM cawp_hist_old o
      JOIN cawp_hist_new n ON n.id = o.id
     WHERE (o.*) IS DISTINCT FROM (n.*);
    RETURN NULL;
END $$;

CREATE OR REPLACE FUNCTION cawp_hist_audit_comments_del() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO audit_events (actor_user_id, action, entity_type, entity_id, case_id,
                              before, after, reason, request_id, idempotency_key, source)
    SELECT cawp_hist_ctx('actor')::uuid,
           coalesce(cawp_hist_ctx('action'), 'comment.delete'),
           'comment', o.id::text, o.clint_id,
           to_jsonb(o), NULL,
           cawp_hist_ctx('reason'), cawp_hist_ctx('request_id'), cawp_hist_ctx('idempotency_key'),
           coalesce(cawp_hist_ctx('source'), 'db_trigger')
      FROM cawp_hist_old o;
    RETURN NULL;
END $$;

DROP TRIGGER IF EXISTS comments_hist_audit_upd ON "Comments";
CREATE TRIGGER comments_hist_audit_upd
    AFTER UPDATE ON "Comments"
    REFERENCING OLD TABLE AS cawp_hist_old NEW TABLE AS cawp_hist_new
    FOR EACH STATEMENT EXECUTE FUNCTION cawp_hist_audit_comments_upd();

DROP TRIGGER IF EXISTS comments_hist_audit_del ON "Comments";
CREATE TRIGGER comments_hist_audit_del
    AFTER DELETE ON "Comments"
    REFERENCING OLD TABLE AS cawp_hist_old
    FOR EACH STATEMENT EXECUTE FUNCTION cawp_hist_audit_comments_del();

-- ---------------------------------------------------------------------------------------
-- note_revisions (append-only)
-- ---------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS note_revisions (
    id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    comment_id       uuid        NOT NULL,
    case_id          uuid        NOT NULL,
    revision_no      integer     NOT NULL CHECK (revision_no >= 0),
    kind             text        NOT NULL CHECK (kind IN ('original', 'correction')),
    text             text        NOT NULL,
    reason           text,
    corrected_by     uuid,
    corrected_at     timestamptz,
    recorded_at      timestamptz NOT NULL DEFAULT now(),
    request_id       text,
    idempotency_key  text,
    CONSTRAINT note_revisions_comment_revision_uq UNIQUE (comment_id, revision_no),
    CONSTRAINT note_revisions_original_is_zero CHECK ((kind = 'original') = (revision_no = 0)),
    CONSTRAINT note_revisions_correction_fields CHECK (
        kind = 'original'
        OR (reason IS NOT NULL AND btrim(reason) <> '' AND corrected_by IS NOT NULL
            AND corrected_at IS NOT NULL))
);

COMMENT ON TABLE note_revisions IS
    'Text history of a note ("Comments"). revision 0 = original text (stored at the first '
    'correction), 1.. = v2 corrections with reason. "Comments".comment holds the latest text. '
    'Append-only; no FK so the history survives legacy hard deletes.';

CREATE INDEX IF NOT EXISTS ix_note_revisions_case ON note_revisions (case_id, comment_id);

CREATE OR REPLACE FUNCTION cawp_hist_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% is append-only: % is not allowed', TG_TABLE_NAME, TG_OP
        USING ERRCODE = 'insufficient_privilege';
END $$;

DROP TRIGGER IF EXISTS note_revisions_append_only ON note_revisions;
CREATE TRIGGER note_revisions_append_only
    BEFORE UPDATE OR DELETE ON note_revisions
    FOR EACH ROW EXECUTE FUNCTION cawp_hist_append_only();

DROP TRIGGER IF EXISTS note_revisions_no_truncate ON note_revisions;
CREATE TRIGGER note_revisions_no_truncate
    BEFORE TRUNCATE ON note_revisions
    FOR EACH STATEMENT EXECUTE FUNCTION cawp_hist_append_only();
