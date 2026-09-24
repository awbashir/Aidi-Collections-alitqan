-- 0059 Phase 6 Q0: the AI data policy (docs/phase6-spec.md hard rule 3, review/phase6-foundation.md).
--
-- ai_data_policies   one row per REVISION of the single, global AI data policy. The current
--                    policy is the row with the highest revision. Rows are append-only: a
--                    change (including a suspension) is a new revision written by
--                    PUT /v2/ai/policy (ai_admin, expected_revision, Idempotency-Key, reason),
--                    together with an `ai.policy.update` audit_events row in the same
--                    transaction. No row: no policy, so every provider call is refused
--                    (`ai_policy_required`).
--
-- A provider call is allowed only when the current revision is `approved`, names the configured
-- provider, and allows every data category the call sends. Masking says which customer
-- identifier classes are replaced by placeholders before sending. The retention periods drive
-- the AI retention task (services/ai/retention.py); `provider_log_days` applies to
-- ai_provider_calls (0060).
--
-- New objects only: instant.

CREATE TABLE IF NOT EXISTS ai_data_policies (
    revision              integer     PRIMARY KEY CHECK (revision >= 1),
    state                 text        NOT NULL CHECK (state IN ('approved', 'suspended')),
    provider              text        NOT NULL CHECK (provider IN ('openai')),
    allowed_categories    text[]      NOT NULL DEFAULT '{}'
        CHECK (allowed_categories <@ ARRAY['case_facts', 'notes', 'transcripts', 'audio', 'employee_names']::text[]
               AND array_position(allowed_categories, NULL) IS NULL),
    mask_names            boolean     NOT NULL,
    mask_identity_numbers boolean     NOT NULL,
    mask_phones           boolean     NOT NULL,
    mask_accounts         boolean     NOT NULL,
    transcript_days       integer     NOT NULL CHECK (transcript_days BETWEEN 1 AND 3650),
    audio_days            integer     NOT NULL CHECK (audio_days BETWEEN 1 AND 3650),
    draft_days            integer     NOT NULL CHECK (draft_days BETWEEN 1 AND 3650),
    provider_log_days     integer     NOT NULL CHECK (provider_log_days BETWEEN 30 AND 3650),
    approved_by           uuid        NOT NULL,          -- the ai_admin who recorded this revision (no FK: users are hard-deleted by legacy code)
    approved_at           timestamptz NOT NULL DEFAULT now(),
    reason                text        NOT NULL CHECK (length(btrim(reason)) BETWEEN 1 AND 2000),
    request_id            text,
    idempotency_key       text
);

CREATE INDEX IF NOT EXISTS ix_ai_data_policies_approved_at ON ai_data_policies (approved_at DESC);

-- Append-only, and revisions are consecutive (1, 2, 3, ...). The service serialises writers with
-- an advisory lock and compares expected_revision; the primary key backs it up.
CREATE OR REPLACE FUNCTION ai_data_policies_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    latest integer;
BEGIN
    IF TG_OP = 'INSERT' THEN
        SELECT coalesce(max(revision), 0) INTO latest FROM ai_data_policies;
        IF NEW.revision <> latest + 1 THEN
            RAISE EXCEPTION 'AI data policy revision must be % (got %)', latest + 1, NEW.revision
                USING ERRCODE = 'AI001';
        END IF;
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'AI data policy revisions are append-only' USING ERRCODE = 'AI001';
END $$;

DROP TRIGGER IF EXISTS ai_data_policies_guard ON ai_data_policies;
CREATE TRIGGER ai_data_policies_guard
    BEFORE INSERT OR UPDATE OR DELETE ON ai_data_policies
    FOR EACH ROW EXECUTE FUNCTION ai_data_policies_guard();

CREATE OR REPLACE FUNCTION ai_data_policies_no_truncate() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'AI data policy revisions are append-only' USING ERRCODE = 'AI001';
END $$;

DROP TRIGGER IF EXISTS ai_data_policies_no_truncate ON ai_data_policies;
CREATE TRIGGER ai_data_policies_no_truncate
    BEFORE TRUNCATE ON ai_data_policies
    FOR EACH STATEMENT EXECUTE FUNCTION ai_data_policies_no_truncate();

COMMENT ON TABLE ai_data_policies IS
    'Phase 6 Q0: revisions of the global AI data policy (append-only). Current = highest revision.';
COMMENT ON COLUMN ai_data_policies.allowed_categories IS
    'Data categories a provider call may send: case_facts, notes, transcripts, audio, employee_names.';
COMMENT ON COLUMN ai_data_policies.provider_log_days IS
    'Retention of ai_provider_calls rows (0060), in days.';
