-- 0064 Phase 6 Q3: voice training lab session log (docs/phase6-spec.md hard rule 6,
-- review/phase6-voicelab.md).
--
-- voice_lab_sessions  one row per training session a user started in the voice lab: WHO, WHICH
--                     fictional scenario and language, WHEN it started / ended, how long it lasted,
--                     the gateway's ai_provider_calls id of the mint, and the outcome. The lab is
--                     fictional training: there is no case, contact or call-attempt column.
--                     No audio, transcript, instructions or client secret is ever stored: there is
--                     deliberately no column for them.
--
-- Lifecycle (services/ai/voice_lab.py):
--   minting  -> the row is committed before the provider call (it holds the "one active session
--               per user" slot while the ephemeral secret is minted);
--   active   -> the ephemeral secret was handed to the browser;
--   ended    -> the browser reported the end (user, 5-minute limit, error, navigation);
--   failed   -> the mint was refused (not configured / no policy) or the provider failed;
--   expired  -> nobody reported an end before expires_at (tab closed, crash); the duration then
--               stays unknown (NULL), it is never invented.
-- One open (minting/active) row per user: a partial unique index.
--
-- No FKs (users are hard-deleted by legacy code; ai_provider_calls rows are purged by retention).
-- New objects only: instant.

CREATE TABLE IF NOT EXISTS voice_lab_sessions (
    id                uuid         PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id           uuid         NOT NULL,
    scenario_id       text         NOT NULL CHECK (scenario_id ~ '^[a-z][a-z0-9_]{1,63}$'),
    scenario_version  integer      NOT NULL CHECK (scenario_version >= 1),
    language          text         NOT NULL CHECK (language IN ('ar', 'en')),
    voice             text         CHECK (voice ~ '^[a-z][a-z0-9_-]{0,31}$'),
    state             text         NOT NULL DEFAULT 'minting'
        CHECK (state IN ('minting', 'active', 'ended', 'failed', 'expired')),
    outcome           text
        CHECK (outcome IN ('user_ended', 'time_limit', 'connection_failed', 'setup_failed', 'navigated_away',
                           'error', 'expired', 'mint_refused', 'mint_failed')),
    error_code        text         CHECK (error_code ~ '^[a-z][a-z0-9_]{1,63}$'),
    max_seconds       integer      NOT NULL CHECK (max_seconds BETWEEN 30 AND 300),
    started_at        timestamptz  NOT NULL DEFAULT now(),
    expires_at        timestamptz  NOT NULL,
    activated_at      timestamptz,
    ended_at          timestamptz,
    duration_seconds  integer      CHECK (duration_seconds BETWEEN 0 AND 300),
    connected_seconds integer      CHECK (connected_seconds BETWEEN 0 AND 300),
    provider_call_id  uuid,
    model             text         CHECK (length(model) <= 200),
    policy_revision   integer,
    request_id        text         CHECK (length(request_id) <= 200),
    CONSTRAINT voice_lab_sessions_expiry CHECK (expires_at > started_at),
    CONSTRAINT voice_lab_sessions_final CHECK (
        (state IN ('minting', 'active')) = (ended_at IS NULL AND outcome IS NULL)),
    CONSTRAINT voice_lab_sessions_active CHECK (state <> 'active' OR activated_at IS NOT NULL)
);

-- one open session per user
CREATE UNIQUE INDEX IF NOT EXISTS ux_voice_lab_sessions_open
    ON voice_lab_sessions (user_id) WHERE state IN ('minting', 'active');
-- rate limits and "my sessions"
CREATE INDEX IF NOT EXISTS ix_voice_lab_sessions_user ON voice_lab_sessions (user_id, started_at DESC);
CREATE INDEX IF NOT EXISTS ix_voice_lab_sessions_started_at ON voice_lab_sessions (started_at);

-- Transitions: minting -> active | failed | expired; active -> ended | expired. A final row is
-- immutable, and the identifying columns never change.
CREATE OR REPLACE FUNCTION voice_lab_sessions_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.state <> 'minting' THEN
            RAISE EXCEPTION 'a voice lab session starts as minting' USING ERRCODE = 'VL002';
        END IF;
        RETURN NEW;
    END IF;
    IF (NEW.id, NEW.user_id, NEW.scenario_id, NEW.scenario_version, NEW.language, NEW.started_at,
        NEW.max_seconds, NEW.request_id)
       IS DISTINCT FROM
       (OLD.id, OLD.user_id, OLD.scenario_id, OLD.scenario_version, OLD.language, OLD.started_at,
        OLD.max_seconds, OLD.request_id) THEN
        RAISE EXCEPTION 'the identifying fields of a voice lab session are immutable' USING ERRCODE = 'VL001';
    END IF;
    IF OLD.state IN ('ended', 'failed', 'expired') THEN
        RAISE EXCEPTION 'a finished voice lab session is immutable' USING ERRCODE = 'VL001';
    END IF;
    IF NOT ((OLD.state = 'minting' AND NEW.state IN ('active', 'failed', 'expired'))
            OR (OLD.state = 'active' AND NEW.state IN ('ended', 'expired'))) THEN
        RAISE EXCEPTION 'invalid voice lab session transition % -> %', OLD.state, NEW.state USING ERRCODE = 'VL002';
    END IF;
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS voice_lab_sessions_guard ON voice_lab_sessions;
CREATE TRIGGER voice_lab_sessions_guard
    BEFORE INSERT OR UPDATE ON voice_lab_sessions
    FOR EACH ROW EXECUTE FUNCTION voice_lab_sessions_guard();

COMMENT ON TABLE voice_lab_sessions IS
    'Phase 6 Q3: voice training lab sessions (fictional scenarios; metadata only, never audio, transcript or secret).';
