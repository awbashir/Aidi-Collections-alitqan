-- 0049 human calling foundation (Phase 5 P0): call attempts, reservations, one-shot dispatch
-- tokens. docs/phase5-spec.md, review/phase5-foundation.md.
--
-- call_attempts   one row per call, manual or PBX. Lifecycle (call_attempts_guard):
--
--     prepared -> authorized -> dispatched -> agent_ringing -> agent_answered
--         -> customer_dialing -> customer_answered -> bridged -> ended
--     plus failed, cancelled and uncertain:
--       prepared/authorized  -> cancelled | failed | uncertain
--       dispatched .. customer_dialing -> ended | failed | uncertain
--       customer_answered, bridged     -> ended | uncertain
--       uncertain -> ended | failed    (reconciliation only, P1; never by a timer)
--     ended, failed and cancelled are terminal. A manual log is inserted directly as `ended`.
--   Steps may be skipped forward (e.g. dispatched -> agent_answered) because the PBX may not
--   report every intermediate state; they never go backwards. Step timestamps are set once.
--   Identity columns (case, contact, destination, collector, extension, mode, campaign,
--   creator) never change; pbx_epoch / pbx_channel_id are set once (the epoch of the PBX
--   process that received the dispatch; evidence from another epoch is never mixed in).
--   `duration_seconds` NULL means unknown, never 0; `duration_source` says who reported it.
--
-- call_reservations  one ACTIVE reservation per collector, per case and per extension (partial
--   unique indexes). Released only once, and only by: `wrapup` (the attempt has a saved
--   wrap-up), `cancel_unsent` (the attempt was cancelled before dispatch) or `reconciled`
--   (a named person with a reason, P1). There is deliberately no timeout/restart source, so
--   no process restart or timer can release a reservation.
--
-- call_dispatch_tokens  the one-shot authorization the FastAGI hook consumes before any customer
--   leg is dialled: only the SHA-256 of the token is stored; one token per attempt, consumed
--   once (consumed_at set once), bound to the app-issued agent channel id, context,
--   destination, extension and (optionally) the PBX epoch; expires quickly.
--
-- No FK to clints/users (legacy hard-deletes them). Locks: new tables only. Instant.

CREATE TABLE IF NOT EXISTS call_attempts (
    id                   uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    mode                 text        NOT NULL CHECK (mode IN ('manual', 'click_to_call', 'campaign')),
    direction            text        NOT NULL DEFAULT 'outbound' CHECK (direction IN ('outbound', 'inbound')),
    state                text        NOT NULL CHECK (state IN (
                             'prepared', 'authorized', 'dispatched', 'agent_ringing', 'agent_answered',
                             'customer_dialing', 'customer_answered', 'bridged', 'ended',
                             'failed', 'cancelled', 'uncertain')),
    case_id              uuid        NOT NULL,
    contact_id           uuid,
    destination          text        CHECK (destination IS NULL OR length(destination) <= 50),
    collector_user_id    uuid        NOT NULL,
    extension            text        CHECK (extension IS NULL OR extension ~ '^[0-9]{2,6}$'),
    campaign_id          uuid,
    created_by           uuid        NOT NULL,
    created_at           timestamptz NOT NULL DEFAULT now(),
    started_at           timestamptz,
    authorized_at        timestamptz,
    dispatched_at        timestamptz,
    agent_ringing_at     timestamptz,
    agent_answered_at    timestamptz,
    customer_dialing_at  timestamptz,
    customer_answered_at timestamptz,
    bridged_at           timestamptz,
    ended_at             timestamptz,
    duration_seconds     integer     CHECK (duration_seconds IS NULL OR duration_seconds BETWEEN 0 AND 86400),
    duration_source      text        CHECK (duration_source IS NULL OR duration_source IN ('reported', 'pbx')),
    pbx_epoch            text        CHECK (pbx_epoch IS NULL OR length(pbx_epoch) <= 100),
    pbx_channel_id       text        UNIQUE CHECK (pbx_channel_id IS NULL OR pbx_channel_id ~ '^cawp-[0-9a-f-]{36}$'),
    reason_code          text        CHECK (reason_code IS NULL OR reason_code ~ '^[a-z][a-z0-9_]{0,63}$'),
    hangup_cause         integer     CHECK (hangup_cause IS NULL OR hangup_cause BETWEEN 0 AND 2147483647),
    transcript           text        CHECK (transcript IS NULL OR length(transcript) <= 100000),
    wrapped_up_at        timestamptz,
    revision             integer     NOT NULL DEFAULT 0,
    updated_at           timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT call_attempts_manual_shape CHECK (
        mode <> 'manual' OR (state = 'ended' AND pbx_channel_id IS NULL AND pbx_epoch IS NULL)),
    CONSTRAINT call_attempts_duration_known CHECK ((duration_seconds IS NULL) = (duration_source IS NULL)),
    CONSTRAINT call_attempts_uncertain_reason CHECK (state <> 'uncertain' OR reason_code IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS ix_call_attempts_collector ON call_attempts (collector_user_id, created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS ix_call_attempts_case ON call_attempts (case_id, created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS ix_call_attempts_created ON call_attempts (created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS ix_call_attempts_in_flight ON call_attempts (state)
    WHERE state NOT IN ('ended', 'failed', 'cancelled');
CREATE INDEX IF NOT EXISTS ix_call_attempts_campaign ON call_attempts (campaign_id, created_at DESC)
    WHERE campaign_id IS NOT NULL;

CREATE OR REPLACE FUNCTION calling_attempt_allowed_next(p_from text) RETURNS text[]
LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE p_from
        WHEN 'prepared'          THEN ARRAY['authorized', 'cancelled', 'failed', 'uncertain']
        WHEN 'authorized'        THEN ARRAY['dispatched', 'cancelled', 'failed', 'uncertain']
        WHEN 'dispatched'        THEN ARRAY['agent_ringing', 'agent_answered', 'ended', 'failed', 'uncertain']
        WHEN 'agent_ringing'     THEN ARRAY['agent_answered', 'ended', 'failed', 'uncertain']
        WHEN 'agent_answered'    THEN ARRAY['customer_dialing', 'ended', 'failed', 'uncertain']
        WHEN 'customer_dialing'  THEN ARRAY['customer_answered', 'ended', 'failed', 'uncertain']
        WHEN 'customer_answered' THEN ARRAY['bridged', 'ended', 'uncertain']
        WHEN 'bridged'           THEN ARRAY['ended', 'uncertain']
        WHEN 'uncertain'         THEN ARRAY['ended', 'failed']
        ELSE ARRAY[]::text[]
    END
$$;

CREATE OR REPLACE FUNCTION calling_attempts_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'call attempts are never deleted' USING ERRCODE = 'CL001';
    END IF;
    IF (NEW.id, NEW.mode, NEW.direction, NEW.case_id, NEW.contact_id, NEW.destination, NEW.collector_user_id,
        NEW.extension, NEW.campaign_id, NEW.created_by, NEW.created_at)
       IS DISTINCT FROM
       (OLD.id, OLD.mode, OLD.direction, OLD.case_id, OLD.contact_id, OLD.destination, OLD.collector_user_id,
        OLD.extension, OLD.campaign_id, OLD.created_by, OLD.created_at) THEN
        RAISE EXCEPTION 'call attempt identity columns are immutable' USING ERRCODE = 'CL002';
    END IF;
    IF (OLD.pbx_epoch IS NOT NULL AND NEW.pbx_epoch IS DISTINCT FROM OLD.pbx_epoch)
       OR (OLD.pbx_channel_id IS NOT NULL AND NEW.pbx_channel_id IS DISTINCT FROM OLD.pbx_channel_id)
       OR (OLD.started_at IS NOT NULL AND NEW.started_at IS DISTINCT FROM OLD.started_at)
       OR (OLD.authorized_at IS NOT NULL AND NEW.authorized_at IS DISTINCT FROM OLD.authorized_at)
       OR (OLD.dispatched_at IS NOT NULL AND NEW.dispatched_at IS DISTINCT FROM OLD.dispatched_at)
       OR (OLD.agent_ringing_at IS NOT NULL AND NEW.agent_ringing_at IS DISTINCT FROM OLD.agent_ringing_at)
       OR (OLD.agent_answered_at IS NOT NULL AND NEW.agent_answered_at IS DISTINCT FROM OLD.agent_answered_at)
       OR (OLD.customer_dialing_at IS NOT NULL AND NEW.customer_dialing_at IS DISTINCT FROM OLD.customer_dialing_at)
       OR (OLD.customer_answered_at IS NOT NULL AND NEW.customer_answered_at IS DISTINCT FROM OLD.customer_answered_at)
       OR (OLD.bridged_at IS NOT NULL AND NEW.bridged_at IS DISTINCT FROM OLD.bridged_at)
       OR (OLD.ended_at IS NOT NULL AND NEW.ended_at IS DISTINCT FROM OLD.ended_at)
       OR (OLD.wrapped_up_at IS NOT NULL AND NEW.wrapped_up_at IS DISTINCT FROM OLD.wrapped_up_at) THEN
        RAISE EXCEPTION 'call attempt evidence timestamps are set once' USING ERRCODE = 'CL002';
    END IF;
    IF NEW.state IS DISTINCT FROM OLD.state
       AND NOT (NEW.state = ANY (calling_attempt_allowed_next(OLD.state))) THEN
        RAISE EXCEPTION 'call attempt transition % -> % is not allowed', OLD.state, NEW.state
            USING ERRCODE = 'CL003';
    END IF;
    NEW.updated_at := now();
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS call_attempts_guard ON call_attempts;
CREATE TRIGGER call_attempts_guard
    BEFORE UPDATE OR DELETE ON call_attempts
    FOR EACH ROW EXECUTE FUNCTION calling_attempts_guard();

-- after the guard (trigger order is alphabetical): bump revision on real changes only
DROP TRIGGER IF EXISTS call_attempts_revision ON call_attempts;
CREATE TRIGGER call_attempts_revision
    BEFORE UPDATE ON call_attempts
    FOR EACH ROW EXECUTE FUNCTION cawp_bump_revision('updated_at');

COMMENT ON TABLE call_attempts IS
    'Phase 5: one row per human call (manual or PBX). AMI accepted = submitted, never connected.';

-- --------------------------------------------------------------------------- reservations
CREATE TABLE IF NOT EXISTS call_reservations (
    id                 uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    attempt_id         uuid        NOT NULL UNIQUE REFERENCES call_attempts (id),
    collector_user_id  uuid        NOT NULL,
    case_id            uuid        NOT NULL,
    extension          text        CHECK (extension IS NULL OR extension ~ '^[0-9]{2,6}$'),
    reserved_at        timestamptz NOT NULL DEFAULT now(),
    released_at        timestamptz,
    released_by        uuid,
    release_source     text        CHECK (release_source IS NULL OR release_source IN ('wrapup', 'cancel_unsent', 'reconciled')),
    release_reason     text        CHECK (release_reason IS NULL OR length(release_reason) <= 1000),
    CONSTRAINT call_reservations_release_fields CHECK ((released_at IS NULL) = (release_source IS NULL)),
    CONSTRAINT call_reservations_reconciled_by CHECK (
        release_source IS DISTINCT FROM 'reconciled'
        OR (released_by IS NOT NULL AND btrim(coalesce(release_reason, '')) <> ''))
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_call_reservations_active_collector
    ON call_reservations (collector_user_id) WHERE released_at IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS ux_call_reservations_active_case
    ON call_reservations (case_id) WHERE released_at IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS ux_call_reservations_active_extension
    ON call_reservations (extension) WHERE released_at IS NULL AND extension IS NOT NULL;

CREATE OR REPLACE FUNCTION calling_reservations_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    a record;
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'call reservations are never deleted' USING ERRCODE = 'CL001';
    END IF;
    IF OLD.released_at IS NOT NULL THEN
        RAISE EXCEPTION 'a released reservation is immutable' USING ERRCODE = 'CL001';
    END IF;
    IF (NEW.id, NEW.attempt_id, NEW.collector_user_id, NEW.case_id, NEW.extension, NEW.reserved_at)
       IS DISTINCT FROM
       (OLD.id, OLD.attempt_id, OLD.collector_user_id, OLD.case_id, OLD.extension, OLD.reserved_at) THEN
        RAISE EXCEPTION 'only the release of a reservation can be recorded' USING ERRCODE = 'CL001';
    END IF;
    IF NEW.released_at IS NOT NULL THEN
        SELECT state, dispatched_at, wrapped_up_at INTO a FROM call_attempts WHERE id = NEW.attempt_id;
        IF NEW.release_source = 'wrapup' AND a.wrapped_up_at IS NULL THEN
            RAISE EXCEPTION 'a reservation is released by wrap-up only after the wrap-up is saved'
                USING ERRCODE = 'CL004';
        END IF;
        IF NEW.release_source = 'cancel_unsent' AND NOT (a.state = 'cancelled' AND a.dispatched_at IS NULL) THEN
            RAISE EXCEPTION 'only a cancelled, never-dispatched attempt releases by cancellation'
                USING ERRCODE = 'CL004';
        END IF;
    END IF;
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS call_reservations_guard ON call_reservations;
CREATE TRIGGER call_reservations_guard
    BEFORE UPDATE OR DELETE ON call_reservations
    FOR EACH ROW EXECUTE FUNCTION calling_reservations_guard();

COMMENT ON TABLE call_reservations IS
    'Phase 5: one active reservation per collector/case/extension. Never released by a restart or a timer.';

-- --------------------------------------------------------------------------- dispatch tokens
CREATE TABLE IF NOT EXISTS call_dispatch_tokens (
    id                uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    attempt_id        uuid        NOT NULL UNIQUE REFERENCES call_attempts (id),
    token_sha256      text        NOT NULL UNIQUE CHECK (token_sha256 ~ '^[0-9a-f]{64}$'),
    channel_id        text        NOT NULL CHECK (channel_id ~ '^cawp-[0-9a-f-]{36}$'),
    context           text        NOT NULL CHECK (context ~ '^collections-human-[a-z0-9_-]{1,50}$'),
    destination       text        NOT NULL CHECK (destination ~ '^\+[1-9][0-9]{7,14}$'),
    extension         text        NOT NULL CHECK (extension ~ '^[0-9]{2,6}$'),
    max_call_seconds  integer     NOT NULL CHECK (max_call_seconds BETWEEN 30 AND 14400),
    pbx_epoch_uuid    text        CHECK (pbx_epoch_uuid IS NULL OR pbx_epoch_uuid ~ '^[0-9a-f-]{36}$'),
    created_at        timestamptz NOT NULL DEFAULT now(),
    expires_at        timestamptz NOT NULL,
    consumed_at       timestamptz,
    CONSTRAINT call_dispatch_tokens_expiry CHECK (expires_at > created_at AND expires_at <= created_at + interval '10 minutes')
);

CREATE OR REPLACE FUNCTION calling_dispatch_tokens_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'dispatch tokens are never deleted' USING ERRCODE = 'CL001';
    END IF;
    IF OLD.consumed_at IS NOT NULL
       OR (NEW.id, NEW.attempt_id, NEW.token_sha256, NEW.channel_id, NEW.context, NEW.destination, NEW.extension,
           NEW.max_call_seconds, NEW.pbx_epoch_uuid, NEW.created_at, NEW.expires_at)
          IS DISTINCT FROM
          (OLD.id, OLD.attempt_id, OLD.token_sha256, OLD.channel_id, OLD.context, OLD.destination, OLD.extension,
           OLD.max_call_seconds, OLD.pbx_epoch_uuid, OLD.created_at, OLD.expires_at) THEN
        RAISE EXCEPTION 'a dispatch token is consumed once and never changed' USING ERRCODE = 'CL001';
    END IF;
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS call_dispatch_tokens_guard ON call_dispatch_tokens;
CREATE TRIGGER call_dispatch_tokens_guard
    BEFORE UPDATE OR DELETE ON call_dispatch_tokens
    FOR EACH ROW EXECUTE FUNCTION calling_dispatch_tokens_guard();

COMMENT ON TABLE call_dispatch_tokens IS
    'Phase 5: one-shot customer-leg authorization consumed by the FastAGI hook (hash only; P1 issues them).';
