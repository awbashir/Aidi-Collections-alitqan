-- 0092 AI calling agent test (customer request 2026-11-04, docs/alitqan-ai-calling-agent-plan.md,
-- services/ai/test_calls.py, the AI voice service `python -m ai_voice`).
--
-- ai_test_calls  one row per test call: the number typed in, the outbound number (outbound_numbers,
--                NULL = automatic), the voice and script, the one-shot token of the PBX gate
--                (SHA-256 only, issued by the AI voice service when it dials), the state the service
--                and the PBX reported, the SIP final status, and the conversation transcript
--                ([{"role": "agent"|"client", "text", "at"}]). No case, no client: a test only.
--                ai_call_id = the ai_provider_calls row of the Realtime session (the gateway's audit).
--
-- New table only. Re-runnable.

CREATE TABLE IF NOT EXISTS ai_test_calls (
    id                uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    created_by        uuid        NOT NULL,
    destination       text        NOT NULL CHECK (destination ~ '^[+]966[0-9]{9}$'),
    caller_id         text        REFERENCES outbound_numbers (number),
    voice             text        NOT NULL CHECK (voice ~ '^[a-z]{2,20}$'),
    instructions      text        NOT NULL CHECK (length(btrim(instructions)) BETWEEN 1 AND 4000),
    state             text        NOT NULL DEFAULT 'queued'
                                  CHECK (state IN ('queued', 'preparing', 'dialing', 'ringing', 'answered', 'ended', 'failed')),
    result            text        CHECK (result IS NULL OR result IN (
                                      'completed', 'no_answer', 'busy', 'congestion', 'unavailable', 'denied',
                                      'cancelled', 'ai_refused', 'ai_failed', 'sip_failed', 'service_stopped',
                                      'time_limit')),
    sip_status        integer     CHECK (sip_status IS NULL OR sip_status BETWEEN 100 AND 699),
    reason_code       text        CHECK (reason_code IS NULL OR reason_code ~ '^[a-z][a-z0-9_]{0,63}$'),
    token_hash        text        CHECK (token_hash IS NULL OR token_hash ~ '^[0-9a-f]{64}$'),
    token_expires_at  timestamptz,
    token_used_at     timestamptz,
    model             text        CHECK (model IS NULL OR length(model) <= 80),
    ai_call_id        uuid,
    transcript        jsonb       NOT NULL DEFAULT '[]'::jsonb,
    stop_requested_at timestamptz,
    created_at        timestamptz NOT NULL DEFAULT now(),
    dialing_at        timestamptz,
    answered_at       timestamptz,
    ended_at          timestamptz,
    updated_at        timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_ai_test_calls_user ON ai_test_calls (created_by, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_ai_test_calls_open ON ai_test_calls (state)
    WHERE state IN ('queued', 'preparing', 'dialing', 'ringing', 'answered');

COMMENT ON TABLE ai_test_calls IS
    'AI calling agent test calls (0092): number, script, PBX gate token hash, state, transcript. No case link.';

-- the AI voice service's heartbeat (one row; ai_voice/main.py), read by the test page
CREATE TABLE IF NOT EXISTS ai_voice_status (
    id          integer     PRIMARY KEY CHECK (id = 1),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    detail      jsonb       NOT NULL DEFAULT '{}'::jsonb
);
