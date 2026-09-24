-- 0060 Phase 6 Q0: audit of AI provider calls (docs/phase6-spec.md hard rule 4,
-- review/phase6-foundation.md).
--
-- ai_provider_calls  one row per provider action the gateway (services/ai/gateway.py) was asked
--                    to perform, including the ones it refused (not configured, no policy, a data
--                    category the policy does not allow). It records WHO asked, for WHAT purpose and
--                    entity, WHICH model and policy revision, token counts, latency, the number of
--                    HTTP attempts and the outcome. It never stores prompt, input, transcript,
--                    audio, output, client secret or provider error text: there is deliberately no
--                    column for them.
--
-- Lifecycle: the gateway commits a `started` row (own transaction) before the first HTTP attempt
-- and finishes it once (`succeeded` / `failed`). Refusals are inserted final. A row left
-- `started` by a crash or restart is marked `interrupted` by the AI retention task; the work
-- itself is never replayed.
--
-- Retention: rows are deleted only by the AI retention task, after the current policy's
-- provider_log_days, inside a transaction that ran SET LOCAL cawp.retention_purge =
-- 'ai_provider_calls'. The guard is a protection against application mistakes, not against a
-- database owner.
--
-- No FKs (users are hard-deleted by legacy code; policy revisions are referenced by number).
-- New objects only: instant.

CREATE TABLE IF NOT EXISTS ai_provider_calls (
    id                  uuid          PRIMARY KEY DEFAULT gen_random_uuid(),
    started_at          timestamptz   NOT NULL DEFAULT now(),
    finished_at         timestamptz,
    purpose             text          NOT NULL CHECK (purpose ~ '^[a-z][a-z0-9_]{1,63}$'),
    capability          text          NOT NULL CHECK (capability IN ('generate_structured', 'transcribe', 'realtime_session')),
    provider            text          NOT NULL CHECK (provider ~ '^[a-z][a-z0-9_]{1,31}$'),
    model               text          CHECK (length(model) <= 200),
    policy_revision     integer,
    data_categories     text[]        NOT NULL DEFAULT '{}'
        CHECK (data_categories <@ ARRAY['case_facts', 'notes', 'transcripts', 'audio', 'employee_names']::text[]),
    masked              boolean       NOT NULL DEFAULT false,
    masked_count        integer       CHECK (masked_count >= 0),
    outcome             text          NOT NULL CHECK (outcome IN ('started', 'succeeded', 'failed', 'refused', 'interrupted')),
    error_code          text          CHECK (error_code ~ '^[a-z][a-z0-9_]{1,63}$'),
    provider_error_type text          CHECK (provider_error_type ~ '^[a-z0-9_.]{1,64}$'),
    http_status         integer       CHECK (http_status BETWEEN 100 AND 599),
    attempts            smallint      NOT NULL DEFAULT 0 CHECK (attempts BETWEEN 0 AND 20),
    input_tokens        integer       CHECK (input_tokens >= 0),
    output_tokens       integer       CHECK (output_tokens >= 0),
    total_tokens        integer       CHECK (total_tokens >= 0),
    audio_seconds       numeric(10,3) CHECK (audio_seconds >= 0),
    latency_ms          integer       CHECK (latency_ms >= 0),
    input_bytes         integer       CHECK (input_bytes >= 0),   -- size of what was sent, not the content
    actor_user_id       uuid,
    entity_type         text          CHECK (entity_type ~ '^[a-z][a-z0-9_]{1,63}$'),
    entity_id           text          CHECK (length(entity_id) <= 100),
    case_id             uuid,
    request_id          text          CHECK (length(request_id) <= 200),
    provider_request_id text          CHECK (length(provider_request_id) <= 200),
    CONSTRAINT ai_provider_calls_finished CHECK ((outcome = 'started') = (finished_at IS NULL)),
    CONSTRAINT ai_provider_calls_error CHECK (outcome IN ('started', 'succeeded') OR error_code IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS ix_ai_provider_calls_started_at ON ai_provider_calls (started_at);
CREATE INDEX IF NOT EXISTS ix_ai_provider_calls_open ON ai_provider_calls (started_at) WHERE outcome = 'started';
CREATE INDEX IF NOT EXISTS ix_ai_provider_calls_entity ON ai_provider_calls (entity_type, entity_id, started_at DESC);
CREATE INDEX IF NOT EXISTS ix_ai_provider_calls_case ON ai_provider_calls (case_id, started_at DESC) WHERE case_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_ai_provider_calls_actor ON ai_provider_calls (actor_user_id, started_at DESC);

-- INSERT: `started` or a final `refused`. UPDATE: exactly once, from `started` to a final outcome,
-- with the identifying fields unchanged. DELETE: final rows, by the retention task only.
CREATE OR REPLACE FUNCTION ai_provider_calls_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.outcome NOT IN ('started', 'refused') THEN
            RAISE EXCEPTION 'an AI provider call starts as started or refused' USING ERRCODE = 'AI002';
        END IF;
        RETURN NEW;
    END IF;
    IF TG_OP = 'DELETE' THEN
        IF coalesce(current_setting('cawp.retention_purge', true), '') <> 'ai_provider_calls' THEN
            RAISE EXCEPTION 'AI provider call rows are deleted by the AI retention task only' USING ERRCODE = 'AI001';
        END IF;
        IF OLD.outcome = 'started' THEN
            RAISE EXCEPTION 'an unfinished AI provider call is never deleted' USING ERRCODE = 'AI001';
        END IF;
        RETURN OLD;
    END IF;
    IF OLD.outcome <> 'started' THEN
        RAISE EXCEPTION 'a finished AI provider call is immutable' USING ERRCODE = 'AI001';
    END IF;
    IF NEW.outcome NOT IN ('succeeded', 'failed', 'interrupted') THEN
        RAISE EXCEPTION 'an AI provider call finishes as succeeded, failed or interrupted' USING ERRCODE = 'AI002';
    END IF;
    IF (NEW.id, NEW.started_at, NEW.purpose, NEW.capability, NEW.provider, NEW.policy_revision,
        NEW.data_categories, NEW.actor_user_id, NEW.entity_type, NEW.entity_id, NEW.case_id, NEW.request_id)
       IS DISTINCT FROM
       (OLD.id, OLD.started_at, OLD.purpose, OLD.capability, OLD.provider, OLD.policy_revision,
        OLD.data_categories, OLD.actor_user_id, OLD.entity_type, OLD.entity_id, OLD.case_id, OLD.request_id) THEN
        RAISE EXCEPTION 'the identifying fields of an AI provider call are immutable' USING ERRCODE = 'AI001';
    END IF;
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS ai_provider_calls_guard ON ai_provider_calls;
CREATE TRIGGER ai_provider_calls_guard
    BEFORE INSERT OR UPDATE OR DELETE ON ai_provider_calls
    FOR EACH ROW EXECUTE FUNCTION ai_provider_calls_guard();

CREATE OR REPLACE FUNCTION ai_provider_calls_no_truncate() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'AI provider call rows are deleted by the AI retention task only' USING ERRCODE = 'AI001';
END $$;

DROP TRIGGER IF EXISTS ai_provider_calls_no_truncate ON ai_provider_calls;
CREATE TRIGGER ai_provider_calls_no_truncate
    BEFORE TRUNCATE ON ai_provider_calls
    FOR EACH STATEMENT EXECUTE FUNCTION ai_provider_calls_no_truncate();

COMMENT ON TABLE ai_provider_calls IS
    'Phase 6 Q0: audit of AI provider calls (metadata only, never prompt/response content).';
