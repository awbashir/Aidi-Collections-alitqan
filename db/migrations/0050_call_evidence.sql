-- 0050 human calling foundation (Phase 5 P0): PBX sessions and call evidence.
-- docs/phase5-spec.md ("Evidence, not inference"), review/phase5-foundation.md.
--
-- pbx_sessions        one row per authenticated AMI session of the `calling` service, with the
--                     PBX identity it resolved: `epoch` = "<COLLECTIONS_HUMAN_PBX_EPOCH uuid>@
--                     <CoreStartupDate>T<CoreStartupTime>". A different epoch = a different
--                     Asterisk process; `pbx_restarted` records that the previous session saw
--                     another one. Only the disconnect (time + fixed code) is ever filled in later.
-- call_events         RAW evidence, append-only: correlated AMI events (UserEvent CollectionsCall,
--                     channel/dial/bridge events of app-issued `cawp-<attempt>` channels and
--                     their linked customer channels, OriginateResponse), PBX lifecycle events
--                     (FullyBooted, Shutdown, Reload), FastAGI decisions, and consumer markers
--                     (`EvidenceGap` when observation was lost). Only allowlisted, sanitized
--                     fields are kept (no caller-id names, no raw frames). `pbx_ts` is the PBX's
--                     own Timestamp header; ordering never uses receipt time. `dedupe_key`
--                     makes redelivery idempotent.
-- call_legs           PROJECTED evidence per PBX channel (epoch + Uniqueid): role (agent =
--                     the app-issued channel; customer = the destination of a DialBegin from
--                     it; unknown otherwise), PBX times of creation/ringing/answer/hangup, the
--                     bridge it joined and the hangup cause. Facts are set once; a role can
--                     only move from unknown to agent/customer. Two answered legs are NOT a
--                     conversation: P1's projection requires bridge membership of both.
-- call_agi_decisions  every FastAGI authorization answer (allow/deny + fixed reason), append-only.
--
-- attempt_id has no FK: evidence may arrive for an id that is not (yet) known, and is kept.
-- Locks: new tables only. Instant.

CREATE TABLE IF NOT EXISTS pbx_sessions (
    id               uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    ami_session_id   text        NOT NULL,
    host             text        NOT NULL,
    epoch            text        NOT NULL CHECK (length(epoch) <= 100),
    epoch_uuid       text,
    startup          text,
    epoch_strength   text        NOT NULL CHECK (epoch_strength IN ('uuid+startup', 'startup_only', 'unknown')),
    previous_epoch   text,
    pbx_restarted    boolean     NOT NULL DEFAULT false,
    ami_version      text,
    worker_id        text,
    connected_at     timestamptz NOT NULL DEFAULT now(),
    disconnected_at  timestamptz,
    disconnect_code  text        CHECK (disconnect_code IS NULL OR disconnect_code ~ '^[a-z][a-z0-9_]{0,63}$')
);

CREATE INDEX IF NOT EXISTS ix_pbx_sessions_connected ON pbx_sessions (connected_at DESC);
CREATE INDEX IF NOT EXISTS ix_pbx_sessions_epoch ON pbx_sessions (epoch);

CREATE OR REPLACE FUNCTION calling_pbx_sessions_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'pbx sessions are never deleted' USING ERRCODE = 'CL001';
    END IF;
    IF OLD.disconnected_at IS NOT NULL
       OR (NEW.id, NEW.ami_session_id, NEW.host, NEW.epoch, NEW.epoch_uuid, NEW.startup, NEW.epoch_strength,
           NEW.previous_epoch, NEW.pbx_restarted, NEW.ami_version, NEW.worker_id, NEW.connected_at)
          IS DISTINCT FROM
          (OLD.id, OLD.ami_session_id, OLD.host, OLD.epoch, OLD.epoch_uuid, OLD.startup, OLD.epoch_strength,
           OLD.previous_epoch, OLD.pbx_restarted, OLD.ami_version, OLD.worker_id, OLD.connected_at) THEN
        RAISE EXCEPTION 'only the disconnect of a pbx session can be recorded, once' USING ERRCODE = 'CL001';
    END IF;
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS pbx_sessions_guard ON pbx_sessions;
CREATE TRIGGER pbx_sessions_guard
    BEFORE UPDATE OR DELETE ON pbx_sessions
    FOR EACH ROW EXECUTE FUNCTION calling_pbx_sessions_guard();

-- --------------------------------------------------------------------------- raw evidence
CREATE TABLE IF NOT EXISTS call_events (
    id              bigserial   PRIMARY KEY,
    received_at     timestamptz NOT NULL DEFAULT now(),
    source          text        NOT NULL CHECK (source IN ('ami', 'agi', 'consumer')),
    pbx_session_id  uuid,
    pbx_epoch       text,
    attempt_id      uuid,
    event           text        NOT NULL CHECK (event ~ '^[A-Za-z][A-Za-z0-9_]{0,63}$'),
    state           text        CHECK (state IS NULL OR state ~ '^[a-z][a-z0-9_]{0,63}$'),
    pbx_ts          numeric(20, 6),
    uniqueid        text        CHECK (uniqueid IS NULL OR length(uniqueid) <= 150),
    linkedid        text        CHECK (linkedid IS NULL OR length(linkedid) <= 150),
    channel         text        CHECK (channel IS NULL OR length(channel) <= 200),
    payload         jsonb       NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(payload) = 'object'),
    dedupe_key      text        NOT NULL UNIQUE CHECK (length(dedupe_key) <= 300)
);

CREATE INDEX IF NOT EXISTS ix_call_events_attempt ON call_events (attempt_id, id) WHERE attempt_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_call_events_channel ON call_events (pbx_epoch, uniqueid) WHERE uniqueid IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_call_events_received ON call_events (received_at);

DROP TRIGGER IF EXISTS call_events_append_only ON call_events;
CREATE TRIGGER call_events_append_only
    BEFORE UPDATE OR DELETE ON call_events
    FOR EACH ROW EXECUTE FUNCTION calling_reject_change();

COMMENT ON TABLE call_events IS
    'Phase 5: raw, sanitized, append-only PBX/AGI evidence. pbx_ts is the PBX clock; epoch-scoped.';

-- --------------------------------------------------------------------------- projected legs
CREATE TABLE IF NOT EXISTS call_legs (
    id                 uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    pbx_epoch          text        NOT NULL,
    uniqueid           text        NOT NULL CHECK (length(uniqueid) <= 150),
    linkedid           text,
    attempt_id         uuid,
    role               text        NOT NULL DEFAULT 'unknown' CHECK (role IN ('agent', 'customer', 'unknown')),
    channel            text,
    created_pbx_ts     numeric(20, 6),
    ringing_pbx_ts     numeric(20, 6),
    answered_pbx_ts    numeric(20, 6),
    bridged_pbx_ts     numeric(20, 6),
    bridge_id          text,
    hungup_pbx_ts      numeric(20, 6),
    hangup_cause       integer,
    first_event_id     bigint,
    last_event_id      bigint,
    updated_at         timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT call_legs_channel UNIQUE (pbx_epoch, uniqueid)
);

CREATE INDEX IF NOT EXISTS ix_call_legs_attempt ON call_legs (attempt_id) WHERE attempt_id IS NOT NULL;

CREATE OR REPLACE FUNCTION calling_call_legs_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'call legs are never deleted' USING ERRCODE = 'CL001';
    END IF;
    IF (NEW.id, NEW.pbx_epoch, NEW.uniqueid) IS DISTINCT FROM (OLD.id, OLD.pbx_epoch, OLD.uniqueid)
       OR (OLD.attempt_id IS NOT NULL AND NEW.attempt_id IS DISTINCT FROM OLD.attempt_id)
       OR (OLD.linkedid IS NOT NULL AND NEW.linkedid IS DISTINCT FROM OLD.linkedid)
       OR (OLD.role <> 'unknown' AND NEW.role IS DISTINCT FROM OLD.role)
       OR (OLD.created_pbx_ts IS NOT NULL AND NEW.created_pbx_ts IS DISTINCT FROM OLD.created_pbx_ts)
       OR (OLD.ringing_pbx_ts IS NOT NULL AND NEW.ringing_pbx_ts IS DISTINCT FROM OLD.ringing_pbx_ts)
       OR (OLD.answered_pbx_ts IS NOT NULL AND NEW.answered_pbx_ts IS DISTINCT FROM OLD.answered_pbx_ts)
       OR (OLD.bridged_pbx_ts IS NOT NULL AND NEW.bridged_pbx_ts IS DISTINCT FROM OLD.bridged_pbx_ts)
       OR (OLD.bridge_id IS NOT NULL AND NEW.bridge_id IS DISTINCT FROM OLD.bridge_id)
       OR (OLD.hungup_pbx_ts IS NOT NULL AND NEW.hungup_pbx_ts IS DISTINCT FROM OLD.hungup_pbx_ts)
       OR (OLD.hangup_cause IS NOT NULL AND NEW.hangup_cause IS DISTINCT FROM OLD.hangup_cause)
       OR (OLD.first_event_id IS NOT NULL AND NEW.first_event_id IS DISTINCT FROM OLD.first_event_id) THEN
        RAISE EXCEPTION 'call leg facts are set once' USING ERRCODE = 'CL002';
    END IF;
    NEW.updated_at := now();
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS call_legs_guard ON call_legs;
CREATE TRIGGER call_legs_guard
    BEFORE UPDATE OR DELETE ON call_legs
    FOR EACH ROW EXECUTE FUNCTION calling_call_legs_guard();

-- --------------------------------------------------------------------------- AGI decisions
CREATE TABLE IF NOT EXISTS call_agi_decisions (
    id                bigserial   PRIMARY KEY,
    occurred_at       timestamptz NOT NULL DEFAULT now(),
    decision          text        NOT NULL CHECK (decision IN ('allow', 'deny')),
    reason            text        NOT NULL CHECK (reason ~ '^[a-z][a-z0-9_]{0,63}$'),
    attempt_id        uuid,
    token_id          uuid,
    peer              text,
    channel_id        text        CHECK (channel_id IS NULL OR length(channel_id) <= 150),
    context           text        CHECK (context IS NULL OR length(context) <= 80),
    destination       text        CHECK (destination IS NULL OR length(destination) <= 50),
    pbx_epoch_uuid    text        CHECK (pbx_epoch_uuid IS NULL OR length(pbx_epoch_uuid) <= 100),
    max_call_seconds  integer,
    expires_at        timestamptz,
    CONSTRAINT call_agi_decisions_allow_shape CHECK (
        decision = 'deny' OR (attempt_id IS NOT NULL AND token_id IS NOT NULL AND max_call_seconds IS NOT NULL))
);

CREATE INDEX IF NOT EXISTS ix_call_agi_decisions_attempt ON call_agi_decisions (attempt_id, id) WHERE attempt_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS ux_call_agi_decisions_one_allow ON call_agi_decisions (token_id) WHERE decision = 'allow';

DROP TRIGGER IF EXISTS call_agi_decisions_append_only ON call_agi_decisions;
CREATE TRIGGER call_agi_decisions_append_only
    BEFORE UPDATE OR DELETE ON call_agi_decisions
    FOR EACH ROW EXECUTE FUNCTION calling_reject_change();

DROP TRIGGER IF EXISTS call_events_no_truncate ON call_events;
CREATE TRIGGER call_events_no_truncate
    BEFORE TRUNCATE ON call_events
    FOR EACH STATEMENT EXECUTE FUNCTION calling_reject_change();

DROP TRIGGER IF EXISTS call_agi_decisions_no_truncate ON call_agi_decisions;
CREATE TRIGGER call_agi_decisions_no_truncate
    BEFORE TRUNCATE ON call_agi_decisions
    FOR EACH STATEMENT EXECUTE FUNCTION calling_reject_change();
