-- Post-call IVR survey (customer request 2026-10-13; services/calling/survey.py).
--
-- contract_call_settings  one row per contract whose calling settings were changed in the app.
--                         post_call_survey turns the 1-5 rating survey on for calls on that
--                         contract's cases: a click-to-call's case, or for an incoming call the
--                         case matched by the caller's number. No row = off. Every change keeps
--                         its revision, who and why (and an audit_events row).
-- call_surveys            one row per survey the PBX offered (FastAGI collections-survey-offer),
--                         completed by collections-survey-result as answered (score 1-5),
--                         no_input or invalid. At most one survey per click-to-call attempt.
--                         Scores are what the customer pressed; nothing is inferred.
--
-- New tables only: instant, no lock on existing tables.

CREATE TABLE IF NOT EXISTS contract_call_settings (
    contract_id      uuid        PRIMARY KEY,
    post_call_survey boolean     NOT NULL DEFAULT false,
    revision         integer     NOT NULL DEFAULT 1 CHECK (revision >= 1),
    updated_at       timestamptz NOT NULL DEFAULT now(),
    updated_by       uuid,
    reason           text        CHECK (reason IS NULL OR length(reason) <= 1000)
);

CREATE TABLE IF NOT EXISTS call_surveys (
    id               uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    source           text        NOT NULL CHECK (source IN ('click_to_call', 'inbound')),
    attempt_id       uuid,
    case_id          uuid        NOT NULL,
    contract_id      uuid        NOT NULL,
    agent_user_id    uuid,
    agent_extension  text        CHECK (agent_extension IS NULL OR agent_extension ~ '^[0-9]{2,6}$'),
    caller_number    text        CHECK (caller_number IS NULL OR length(caller_number) <= 32),
    channel_id       text        NOT NULL CHECK (length(channel_id) BETWEEN 1 AND 150),
    language         text        NOT NULL DEFAULT 'ar' CHECK (language IN ('ar', 'en')),
    status           text        NOT NULL DEFAULT 'offered'
                                 CHECK (status IN ('offered', 'answered', 'no_input', 'invalid')),
    score            smallint    CHECK (score BETWEEN 1 AND 5),
    offered_at       timestamptz NOT NULL DEFAULT now(),
    completed_at     timestamptz,
    CONSTRAINT call_surveys_score_when_answered CHECK ((status = 'answered') = (score IS NOT NULL)),
    CONSTRAINT call_surveys_completed_when_done CHECK ((status = 'offered') = (completed_at IS NULL)),
    CONSTRAINT call_surveys_attempt_for_click_to_call CHECK ((source = 'click_to_call') = (attempt_id IS NOT NULL))
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_call_surveys_attempt ON call_surveys (attempt_id) WHERE attempt_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_call_surveys_offered ON call_surveys (offered_at DESC);
CREATE INDEX IF NOT EXISTS ix_call_surveys_contract ON call_surveys (contract_id, offered_at DESC);
CREATE INDEX IF NOT EXISTS ix_call_surveys_agent ON call_surveys (agent_user_id, offered_at DESC);

COMMENT ON TABLE call_surveys IS
    'Post-call IVR survey answers (migration 0066): offered by the PBX, completed with the digit the customer pressed.';
