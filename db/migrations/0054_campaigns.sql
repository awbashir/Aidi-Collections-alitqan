-- 0054 progressive calling campaigns (Phase 5 P2): campaigns, their roster and the members'
-- readiness leases. docs/phase5-spec.md ("progressive only": one call per ready collector, no
-- predictive or speculative dialing), review/phase5-campaigns.md.
--
-- campaigns            a team calling campaign. The case POOL is a definition (contracts,
--                      portfolios and/or a saved clients-list filter) that is evaluated at every
--                      dispatch, never a frozen list. Lifecycle (campaigns_guard):
--
--                          draft -> running | stopped
--                          running -> paused | draining | stopped
--                          paused -> running | draining | stopped
--                          draining -> stopped      (by the calling service once no campaign call
--                                                    is outstanding, or by a manager)
--                          stopped: terminal, nothing changes any more
--
--                      pause / drain / stop never cut a live call (the server cannot hang up:
--                      P0 decision 7); they stop NEW dispatches. Settings: automatic or manual
--                      next call, wrap-up delay, max attempts per case per Riyadh day, per-case
--                      cooldown, readiness lease length.
-- campaign_members     the roster: one row per (campaign, collector), deactivated (never deleted)
--                      with who and why. An inactive member gets no call.
-- campaign_readiness   a per-member heartbeat LEASE: the declared state (ready | paused | wrap_up
--                      | offline), `lease_until` (a lapsed lease = offline for dispatch), the manual
--                      "next" request and the last dispatch. Nothing here ever releases a
--                      reservation or touches a live call.
--
-- No FK to clints/users (legacy hard-deletes). Locks: new tables only. Instant.

-- --------------------------------------------------------------------------- campaigns
CREATE TABLE IF NOT EXISTS campaigns (
    id                    uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    name                  text        NOT NULL CHECK (btrim(name) <> '' AND length(name) <= 200),
    status                text        NOT NULL DEFAULT 'draft'
                          CHECK (status IN ('draft', 'running', 'paused', 'draining', 'stopped')),
    pool                  jsonb       NOT NULL CHECK (jsonb_typeof(pool) = 'object'),
    next_mode             text        NOT NULL DEFAULT 'auto' CHECK (next_mode IN ('auto', 'manual')),
    wrapup_delay_seconds  integer     NOT NULL DEFAULT 10 CHECK (wrapup_delay_seconds BETWEEN 0 AND 600),
    max_attempts_per_day  integer     NOT NULL DEFAULT 3 CHECK (max_attempts_per_day BETWEEN 1 AND 50),
    cooldown_minutes      integer     NOT NULL DEFAULT 60 CHECK (cooldown_minutes BETWEEN 0 AND 10080),
    lease_seconds         integer     NOT NULL DEFAULT 30 CHECK (lease_seconds BETWEEN 10 AND 300),
    created_by            uuid        NOT NULL,
    created_at            timestamptz NOT NULL DEFAULT now(),
    updated_at            timestamptz NOT NULL DEFAULT now(),
    started_at            timestamptz,
    paused_at             timestamptz,
    drain_requested_at    timestamptz,
    stopped_at            timestamptz,
    stopped_by            uuid,
    stop_reason           text        CHECK (stop_reason IS NULL OR stop_reason ~ '^[a-z][a-z0-9_]{0,63}$'),
    roster_changed_at     timestamptz,             -- set by every roster change, so it bumps the revision
    revision              integer     NOT NULL DEFAULT 0,
    CONSTRAINT campaigns_stopped_fields CHECK ((status = 'stopped') = (stopped_at IS NOT NULL)),
    CONSTRAINT campaigns_draining_fields CHECK (status <> 'draining' OR drain_requested_at IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS ix_campaigns_active ON campaigns (status, created_at)
    WHERE status IN ('running', 'paused', 'draining');
CREATE INDEX IF NOT EXISTS ix_campaigns_created ON campaigns (created_at DESC, id DESC);

CREATE OR REPLACE FUNCTION campaigns_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'campaigns are never deleted (stop them)' USING ERRCODE = 'CL001';
    END IF;
    IF (NEW.id, NEW.created_by, NEW.created_at) IS DISTINCT FROM (OLD.id, OLD.created_by, OLD.created_at) THEN
        RAISE EXCEPTION 'campaign identity columns are immutable' USING ERRCODE = 'CL002';
    END IF;
    IF OLD.status = 'stopped' THEN
        RAISE EXCEPTION 'a stopped campaign never changes' USING ERRCODE = 'CL003';
    END IF;
    IF NEW.status IS DISTINCT FROM OLD.status AND NOT (
           (OLD.status = 'draft'    AND NEW.status IN ('running', 'stopped'))
        OR (OLD.status = 'running'  AND NEW.status IN ('paused', 'draining', 'stopped'))
        OR (OLD.status = 'paused'   AND NEW.status IN ('running', 'draining', 'stopped'))
        OR (OLD.status = 'draining' AND NEW.status = 'stopped')) THEN
        RAISE EXCEPTION 'campaign transition % -> % is not allowed', OLD.status, NEW.status
            USING ERRCODE = 'CL003';
    END IF;
    IF (OLD.started_at IS NOT NULL AND NEW.started_at IS DISTINCT FROM OLD.started_at)
       OR (OLD.drain_requested_at IS NOT NULL AND NEW.drain_requested_at IS DISTINCT FROM OLD.drain_requested_at) THEN
        RAISE EXCEPTION 'campaign start/drain times are set once' USING ERRCODE = 'CL002';
    END IF;
    NEW.updated_at := now();
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS campaigns_guard ON campaigns;
CREATE TRIGGER campaigns_guard
    BEFORE UPDATE OR DELETE ON campaigns
    FOR EACH ROW EXECUTE FUNCTION campaigns_guard();

DROP TRIGGER IF EXISTS campaigns_revision ON campaigns;
CREATE TRIGGER campaigns_revision
    BEFORE UPDATE ON campaigns
    FOR EACH ROW EXECUTE FUNCTION cawp_bump_revision('updated_at');

CREATE OR REPLACE FUNCTION campaigns_no_truncate() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% is never truncated', TG_TABLE_NAME USING ERRCODE = 'CL001';
END $$;

DROP TRIGGER IF EXISTS campaigns_no_truncate ON campaigns;
CREATE TRIGGER campaigns_no_truncate BEFORE TRUNCATE ON campaigns
    FOR EACH STATEMENT EXECUTE FUNCTION campaigns_no_truncate();

COMMENT ON TABLE campaigns IS
    'Phase 5 P2: progressive calling campaign (one call per ready collector; the pool is evaluated at dispatch).';

-- --------------------------------------------------------------------------- roster
CREATE TABLE IF NOT EXISTS campaign_members (
    campaign_id    uuid        NOT NULL REFERENCES campaigns (id),
    user_id        uuid        NOT NULL,
    active         boolean     NOT NULL DEFAULT true,
    added_by       uuid        NOT NULL,
    added_at       timestamptz NOT NULL DEFAULT now(),
    changed_by     uuid,
    changed_at     timestamptz,
    change_reason  text        CHECK (change_reason IS NULL OR length(change_reason) <= 1000),
    PRIMARY KEY (campaign_id, user_id),
    CONSTRAINT campaign_members_change_fields CHECK ((changed_at IS NULL) = (changed_by IS NULL))
);

CREATE INDEX IF NOT EXISTS ix_campaign_members_user ON campaign_members (user_id) WHERE active;

CREATE OR REPLACE FUNCTION campaign_members_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'campaign members are deactivated, never deleted' USING ERRCODE = 'CL001';
    END IF;
    IF (NEW.campaign_id, NEW.user_id, NEW.added_by, NEW.added_at)
       IS DISTINCT FROM (OLD.campaign_id, OLD.user_id, OLD.added_by, OLD.added_at) THEN
        RAISE EXCEPTION 'campaign member identity columns are immutable' USING ERRCODE = 'CL002';
    END IF;
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS campaign_members_guard ON campaign_members;
CREATE TRIGGER campaign_members_guard
    BEFORE UPDATE OR DELETE ON campaign_members
    FOR EACH ROW EXECUTE FUNCTION campaign_members_guard();

DROP TRIGGER IF EXISTS campaign_members_no_truncate ON campaign_members;
CREATE TRIGGER campaign_members_no_truncate BEFORE TRUNCATE ON campaign_members
    FOR EACH STATEMENT EXECUTE FUNCTION campaigns_no_truncate();

-- --------------------------------------------------------------------------- readiness leases
CREATE TABLE IF NOT EXISTS campaign_readiness (
    campaign_id        uuid        NOT NULL,
    user_id            uuid        NOT NULL,
    state              text        NOT NULL CHECK (state IN ('ready', 'paused', 'wrap_up', 'offline')),
    state_since        timestamptz NOT NULL DEFAULT now(),
    state_reason       text        CHECK (state_reason IS NULL OR state_reason ~ '^[a-z][a-z0-9_]{0,63}$'),
    lease_until        timestamptz NOT NULL,
    heartbeat_at       timestamptz NOT NULL DEFAULT now(),
    next_requested_at  timestamptz,
    last_dispatch_at   timestamptz,
    last_attempt_id    uuid,
    block_reason       text        CHECK (block_reason IS NULL OR block_reason ~ '^[a-z][a-z0-9_]{0,63}$'),
    block_detail       jsonb       CHECK (block_detail IS NULL OR jsonb_typeof(block_detail) = 'object'),
    block_checked_at   timestamptz,
    PRIMARY KEY (campaign_id, user_id),
    FOREIGN KEY (campaign_id, user_id) REFERENCES campaign_members (campaign_id, user_id),
    CONSTRAINT campaign_readiness_lease CHECK (lease_until <= heartbeat_at + interval '10 minutes')
);

CREATE INDEX IF NOT EXISTS ix_campaign_readiness_ready ON campaign_readiness (campaign_id, lease_until)
    WHERE state IN ('ready', 'wrap_up');
CREATE INDEX IF NOT EXISTS ix_campaign_readiness_user ON campaign_readiness (user_id, lease_until);

COMMENT ON TABLE campaign_readiness IS
    'Phase 5 P2: per-member heartbeat lease. A lapsed lease gets no new call; a live call is never cut.';
