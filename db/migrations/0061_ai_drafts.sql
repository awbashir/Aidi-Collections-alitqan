-- 0061 Phase 6 Q1: AI assistance drafts (docs/phase6-spec.md Q1, review/phase6-drafts.md).
--
-- ai_drafts   one row per generated draft: a call script, case guidance, a narrative, a call
--             summary, coaching or a follow-up message text. A draft is ONLY a draft: it never
--             changes a case, books a payment, creates a promise, authorizes a call or sends a
--             message. The one follow-up action is an explicit "save as note" by a human, which
--             writes a legacy "Comments" row (so the existing note triggers fire) and links it
--             here (saved_note_id).
--
-- Provenance without the prompt: the draft keeps WHICH data categories were sent, a sha256 of
-- the unmasked input (input_hash) and of the masked text actually sent (masked_input_fingerprint),
-- the provider-call audit id (ai_provider_calls, 0060), the model and the policy revision. The
-- raw prompt is never stored. `content` / `generated_text` / `text` hold what the user sees
-- (identifiers mapped back from the placeholders); they are cleared on discard and by the AI
-- retention task (services/ai/retention_hooks/drafts.py) after the policy's draft_days.
--
-- Lifecycle: generated -> edited (any number of times) -> saved_as_note | discarded (final).
-- `revision` is bumped by the shared cawp_bump_revision() trigger on every real change and is
-- checked by PATCH / discard / save-as-note (expected_revision).
--
-- Guard: identity/provenance columns are immutable; a final draft may only have its content
-- purged; DELETE only inside a transaction that ran SET LOCAL cawp.retention_purge = 'ai_drafts'
-- (the retention task). No TRUNCATE.
--
-- No FKs (legacy code hard-deletes cases, users and notes; provider-call rows are purged after
-- provider_log_days). New objects only: instant.

CREATE TABLE IF NOT EXISTS ai_drafts (
    id                        uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    kind                      text        NOT NULL CHECK (kind IN (
                                  'call_script', 'case_guidance', 'narrative', 'call_summary', 'coaching',
                                  'followup_message')),
    case_id                   uuid        NOT NULL,
    attempt_id                uuid,
    requested_by              uuid        NOT NULL,
    language                  text        NOT NULL CHECK (language IN ('ar', 'en')),
    options                   jsonb       NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(options) = 'object'),
    input_categories          text[]      NOT NULL
        CHECK (input_categories <@ ARRAY['case_facts', 'notes', 'transcripts', 'audio', 'employee_names']::text[]
               AND array_position(input_categories, NULL) IS NULL),
    input_hash                text        NOT NULL CHECK (input_hash ~ '^[0-9a-f]{64}$'),
    masked_input_fingerprint  text        NOT NULL CHECK (masked_input_fingerprint ~ '^[0-9a-f]{64}$'),
    input_summary             jsonb       NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(input_summary) = 'object'),
    schema_name               text        NOT NULL CHECK (schema_name ~ '^[a-z][a-z0-9_]{1,63}$'),
    schema_version            integer     NOT NULL CHECK (schema_version >= 1),
    content                   jsonb       CHECK (content IS NULL OR jsonb_typeof(content) = 'object'),
    generated_text            text        CHECK (generated_text IS NULL OR length(generated_text) <= 50000),
    text                      text        CHECK (text IS NULL OR length(text) <= 50000),
    provider_call_id          uuid        NOT NULL,
    model                     text        CHECK (model IS NULL OR length(model) <= 200),
    policy_revision           integer     NOT NULL CHECK (policy_revision >= 1),
    masked                    boolean     NOT NULL DEFAULT true,
    masked_count              integer     CHECK (masked_count IS NULL OR masked_count >= 0),
    status                    text        NOT NULL DEFAULT 'generated'
                                  CHECK (status IN ('generated', 'edited', 'saved_as_note', 'discarded')),
    edit_count                integer     NOT NULL DEFAULT 0 CHECK (edit_count >= 0),
    edited_at                 timestamptz,
    edited_by                 uuid,
    saved_note_id             uuid,
    saved_at                  timestamptz,
    saved_by                  uuid,
    discarded_at              timestamptz,
    discarded_by              uuid,
    revision                  integer     NOT NULL DEFAULT 1,
    created_at                timestamptz NOT NULL DEFAULT now(),
    updated_at                timestamptz NOT NULL DEFAULT now(),
    expires_at                timestamptz,
    purged_at                 timestamptz,
    request_id                text        CHECK (request_id IS NULL OR length(request_id) <= 200),
    idempotency_key           text        CHECK (idempotency_key IS NULL OR length(idempotency_key) <= 255),
    request_fingerprint       text        CHECK (request_fingerprint IS NULL OR request_fingerprint ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ai_drafts_attempt_kinds CHECK (
        kind NOT IN ('call_summary', 'coaching') OR attempt_id IS NOT NULL),
    CONSTRAINT ai_drafts_saved_shape CHECK (
        (status = 'saved_as_note') = (saved_note_id IS NOT NULL)
        AND (saved_note_id IS NULL) = (saved_at IS NULL)
        AND (saved_note_id IS NULL) = (saved_by IS NULL)),
    CONSTRAINT ai_drafts_discarded_shape CHECK (
        (status = 'discarded') = (discarded_at IS NOT NULL)
        AND (discarded_at IS NULL) = (discarded_by IS NULL)),
    CONSTRAINT ai_drafts_discarded_has_no_content CHECK (
        status <> 'discarded' OR (content IS NULL AND generated_text IS NULL AND text IS NULL)),
    CONSTRAINT ai_drafts_live_has_content CHECK (
        status NOT IN ('generated', 'edited')
        OR (purged_at IS NULL AND content IS NOT NULL AND generated_text IS NOT NULL AND text IS NOT NULL)),
    CONSTRAINT ai_drafts_purged_has_no_content CHECK (
        purged_at IS NULL OR (content IS NULL AND generated_text IS NULL AND text IS NULL)),
    CONSTRAINT ai_drafts_edited_shape CHECK (
        (status = 'edited') <= (edit_count > 0)
        AND (edit_count = 0) = (edited_at IS NULL))
);

CREATE INDEX IF NOT EXISTS ix_ai_drafts_case ON ai_drafts (case_id, created_at DESC, id);
CREATE INDEX IF NOT EXISTS ix_ai_drafts_attempt ON ai_drafts (attempt_id, created_at DESC) WHERE attempt_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_ai_drafts_requester ON ai_drafts (requested_by, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_ai_drafts_created ON ai_drafts (created_at, id);
CREATE UNIQUE INDEX IF NOT EXISTS ux_ai_drafts_idempotency ON ai_drafts (requested_by, idempotency_key)
    WHERE idempotency_key IS NOT NULL;

CREATE OR REPLACE FUNCTION ai_drafts_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.status <> 'generated' OR NEW.edit_count <> 0 OR NEW.purged_at IS NOT NULL THEN
            RAISE EXCEPTION 'an AI draft starts as generated' USING ERRCODE = 'AI011';
        END IF;
        RETURN NEW;
    END IF;
    IF TG_OP = 'DELETE' THEN
        IF coalesce(current_setting('cawp.retention_purge', true), '') <> 'ai_drafts' THEN
            RAISE EXCEPTION 'AI drafts are deleted by the AI retention task only' USING ERRCODE = 'AI010';
        END IF;
        RETURN OLD;
    END IF;
    IF (NEW.id, NEW.kind, NEW.case_id, NEW.attempt_id, NEW.requested_by, NEW.language, NEW.options,
        NEW.input_categories, NEW.input_hash, NEW.masked_input_fingerprint, NEW.input_summary, NEW.schema_name,
        NEW.schema_version, NEW.provider_call_id, NEW.model, NEW.policy_revision, NEW.masked, NEW.masked_count,
        NEW.created_at, NEW.expires_at, NEW.request_id, NEW.idempotency_key, NEW.request_fingerprint)
       IS DISTINCT FROM
       (OLD.id, OLD.kind, OLD.case_id, OLD.attempt_id, OLD.requested_by, OLD.language, OLD.options,
        OLD.input_categories, OLD.input_hash, OLD.masked_input_fingerprint, OLD.input_summary, OLD.schema_name,
        OLD.schema_version, OLD.provider_call_id, OLD.model, OLD.policy_revision, OLD.masked, OLD.masked_count,
        OLD.created_at, OLD.expires_at, OLD.request_id, OLD.idempotency_key, OLD.request_fingerprint) THEN
        RAISE EXCEPTION 'the provenance of an AI draft is immutable' USING ERRCODE = 'AI010';
    END IF;
    IF NEW.content IS DISTINCT FROM OLD.content AND NEW.content IS NOT NULL THEN
        RAISE EXCEPTION 'generated AI draft content is never rewritten (only cleared)' USING ERRCODE = 'AI010';
    END IF;
    IF NEW.generated_text IS DISTINCT FROM OLD.generated_text AND NEW.generated_text IS NOT NULL THEN
        RAISE EXCEPTION 'generated AI draft text is never rewritten (only cleared)' USING ERRCODE = 'AI010';
    END IF;
    IF OLD.status IN ('saved_as_note', 'discarded') THEN
        -- a final draft: only the retention task's content purge is allowed
        IF NEW.status IS DISTINCT FROM OLD.status
           OR (NEW.saved_note_id, NEW.saved_at, NEW.saved_by, NEW.discarded_at, NEW.discarded_by, NEW.edit_count,
               NEW.edited_at, NEW.edited_by)
              IS DISTINCT FROM
              (OLD.saved_note_id, OLD.saved_at, OLD.saved_by, OLD.discarded_at, OLD.discarded_by, OLD.edit_count,
               OLD.edited_at, OLD.edited_by)
           OR (NEW.text IS DISTINCT FROM OLD.text AND NEW.text IS NOT NULL)
           OR (OLD.purged_at IS NOT NULL AND NEW.purged_at IS DISTINCT FROM OLD.purged_at) THEN
            RAISE EXCEPTION 'AI draft % is %: it can no longer change', OLD.id, OLD.status USING ERRCODE = 'AI012';
        END IF;
    ELSIF NEW.status IS DISTINCT FROM OLD.status
          AND NOT (NEW.status = ANY (CASE OLD.status
                                        WHEN 'generated' THEN ARRAY['edited', 'saved_as_note', 'discarded']
                                        WHEN 'edited' THEN ARRAY['saved_as_note', 'discarded']
                                        ELSE ARRAY[]::text[] END)) THEN
        RAISE EXCEPTION 'AI draft transition % -> % is not allowed', OLD.status, NEW.status USING ERRCODE = 'AI012';
    END IF;
    NEW.updated_at := now();
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS ai_drafts_guard ON ai_drafts;
CREATE TRIGGER ai_drafts_guard
    BEFORE INSERT OR UPDATE OR DELETE ON ai_drafts
    FOR EACH ROW EXECUTE FUNCTION ai_drafts_guard();

-- after the guard (alphabetical order): bump the revision on real changes only
DROP TRIGGER IF EXISTS ai_drafts_revision ON ai_drafts;
CREATE TRIGGER ai_drafts_revision
    BEFORE UPDATE ON ai_drafts
    FOR EACH ROW EXECUTE FUNCTION cawp_bump_revision('updated_at');

CREATE OR REPLACE FUNCTION ai_drafts_no_truncate() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'AI drafts are deleted by the AI retention task only' USING ERRCODE = 'AI010';
END $$;

DROP TRIGGER IF EXISTS ai_drafts_no_truncate ON ai_drafts;
CREATE TRIGGER ai_drafts_no_truncate
    BEFORE TRUNCATE ON ai_drafts
    FOR EACH STATEMENT EXECUTE FUNCTION ai_drafts_no_truncate();

COMMENT ON TABLE ai_drafts IS
    'Phase 6 Q1: AI assistance drafts. A draft never changes a case; a human may save its edited text '
    'as a legacy "Comments" note (saved_note_id). No raw prompt is stored.';
COMMENT ON COLUMN ai_drafts.input_hash IS 'sha256 of the canonical unmasked input (kind, language, options, data)';
COMMENT ON COLUMN ai_drafts.masked_input_fingerprint IS
    'sha256 of the masked instructions and messages as sent to the provider (the text itself is not stored)';
COMMENT ON COLUMN ai_drafts.content IS
    'the structured draft (strict JSON schema), identifiers mapped back for the user; NULL once discarded/purged';
COMMENT ON COLUMN ai_drafts.expires_at IS 'created_at + the policy''s draft_days at generation (informational)';
COMMENT ON COLUMN ai_drafts.purged_at IS 'when the AI retention task removed the content (saved drafts keep their row)';
