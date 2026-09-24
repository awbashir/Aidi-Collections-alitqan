-- 0055 progressive calling campaigns (Phase 5 P2): campaign attempts and per-case pool state.
-- review/phase5-campaigns.md.
--
-- campaign_attempts    one row per call a campaign dispatched: it links the call_attempts row
--                      (mode 'campaign', created by the SAME dispatch code as POST /v2/calls:
--                      rules, attempt, reservation, dispatch queue row) to the campaign, the member,
--                      the case, the chosen contact (default or a documented fallback) and the pool
--                      priority it was picked with. Append-only. The trigger checks that the call
--                      attempt really is this campaign's call for this member and case.
--                      "One outstanding attempt per collector" is enforced by P0's partial unique
--                      index on ACTIVE call_reservations (collector), which every campaign call holds
--                      until its wrap-up (or an evidence-proven no-contact release).
-- campaign_pool_state  per (campaign, case) facts kept by the dispatcher: last tried, attempts on
--                      the Riyadh day, and the last exclusion found by the dispatch rules with a
--                      re-check time (so a paid or uncallable case is not re-evaluated every tick).
--                      The rules are ALWAYS re-evaluated at dispatch; this row is a cache and a
--                      view for the supervisor, never an authorization.
--
-- Also an index on call_attempts for the per-case attempt counts (max attempts per day, cooldown)
-- is not needed: ix_call_attempts_case (case_id, created_at DESC, id DESC) from 0049 serves them.
--
-- No FK to clints/users. Locks: new tables only. Instant.

CREATE TABLE IF NOT EXISTS campaign_attempts (
    attempt_id      uuid        PRIMARY KEY REFERENCES call_attempts (id),
    campaign_id     uuid        NOT NULL REFERENCES campaigns (id),
    user_id         uuid        NOT NULL,
    case_id         uuid        NOT NULL,
    contact_id      uuid        NOT NULL,
    contact_choice  text        NOT NULL CHECK (contact_choice IN ('default', 'fallback')),
    priority        text        NOT NULL CHECK (priority IN ('callback_due', 'normal')),
    dispatched_by   text        NOT NULL CHECK (btrim(dispatched_by) <> '' AND length(dispatched_by) <= 200),
    created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_campaign_attempts_member ON campaign_attempts (campaign_id, user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_campaign_attempts_campaign ON campaign_attempts (campaign_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_campaign_attempts_case ON campaign_attempts (case_id, created_at DESC);

CREATE OR REPLACE FUNCTION campaign_attempts_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    a record;
BEGIN
    IF TG_OP <> 'INSERT' THEN
        RAISE EXCEPTION 'campaign attempts are append-only' USING ERRCODE = 'CL001';
    END IF;
    SELECT mode, campaign_id, collector_user_id, case_id, contact_id INTO a
      FROM call_attempts WHERE id = NEW.attempt_id;
    IF a.mode IS DISTINCT FROM 'campaign' OR a.campaign_id IS DISTINCT FROM NEW.campaign_id
       OR a.collector_user_id IS DISTINCT FROM NEW.user_id OR a.case_id IS DISTINCT FROM NEW.case_id
       OR a.contact_id IS DISTINCT FROM NEW.contact_id THEN
        RAISE EXCEPTION 'campaign attempt % does not match its call attempt', NEW.attempt_id
            USING ERRCODE = 'CL002';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM campaign_members m WHERE m.campaign_id = NEW.campaign_id
                      AND m.user_id = NEW.user_id AND m.active) THEN
        RAISE EXCEPTION 'campaign attempt for a user who is not an active member' USING ERRCODE = 'CL002';
    END IF;
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS campaign_attempts_guard ON campaign_attempts;
CREATE TRIGGER campaign_attempts_guard
    BEFORE INSERT OR UPDATE OR DELETE ON campaign_attempts
    FOR EACH ROW EXECUTE FUNCTION campaign_attempts_guard();

DROP TRIGGER IF EXISTS campaign_attempts_no_truncate ON campaign_attempts;
CREATE TRIGGER campaign_attempts_no_truncate BEFORE TRUNCATE ON campaign_attempts
    FOR EACH STATEMENT EXECUTE FUNCTION campaigns_no_truncate();

COMMENT ON TABLE campaign_attempts IS
    'Phase 5 P2: the calls a campaign dispatched (call_attempts mode campaign). Append-only.';

-- --------------------------------------------------------------------------- pool state
CREATE TABLE IF NOT EXISTS campaign_pool_state (
    campaign_id      uuid        NOT NULL REFERENCES campaigns (id),
    case_id          uuid        NOT NULL,
    last_tried_at    timestamptz,
    last_attempt_id  uuid,
    attempts_day     date,
    attempts_today   integer     NOT NULL DEFAULT 0 CHECK (attempts_today >= 0),
    total_attempts   integer     NOT NULL DEFAULT 0 CHECK (total_attempts >= 0),
    excluded_reason  text        CHECK (excluded_reason IS NULL OR excluded_reason ~ '^[a-z][a-z0-9_]{0,63}$'),
    excluded_detail  jsonb       CHECK (excluded_detail IS NULL OR jsonb_typeof(excluded_detail) = 'object'),
    excluded_at      timestamptz,
    recheck_after    timestamptz,
    updated_at       timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (campaign_id, case_id),
    CONSTRAINT campaign_pool_state_exclusion CHECK ((excluded_reason IS NULL) = (excluded_at IS NULL))
);

CREATE INDEX IF NOT EXISTS ix_campaign_pool_state_recheck ON campaign_pool_state (campaign_id, recheck_after)
    WHERE recheck_after IS NOT NULL;

COMMENT ON TABLE campaign_pool_state IS
    'Phase 5 P2: per-case dispatcher facts (last tried, attempts today, last exclusion). A cache, never an authorization.';
