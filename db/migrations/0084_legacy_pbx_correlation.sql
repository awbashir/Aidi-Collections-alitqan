-- 0084 click-to-call and campaigns on Asterisk 11 / chan_sip (Alitqan Issabel 4, 2026-10-21).
--
-- Asterisk <= 11 ignores the Originate ChannelId, so the agent channel's Uniqueid is not the
-- app-issued `cawp-<attempt>` but an ordinary "<epoch>.<seq>" id, and its events carry no Linkedid.
-- With CALLING_PBX_EVENTS=legacy the calling service binds the agent leg from what the PBX itself
-- says (services/calling/legacy_correlation.py):
--
-- call_dispatch_queue.ami_action_id   the ActionID of the ONE Originate of the dispatch, recorded in
--                                     the claim's commit BEFORE it is sent; its OriginateResponse
--                                     (same ActionID) names the agent channel. Set once; unique.
-- call_dispatch_tokens.bound_uniqueid the agent channel Uniqueid the FastAGI hook allowed (set in the
--                                     same UPDATE that consumes the token; the 0049 guard already
--                                     freezes a consumed token).
-- ix_call_legs_agent_uniqueid         agent leg -> attempt lookups by Uniqueid (call control).
--
-- Additive: new nullable columns (metadata only), a partial unique index and a partial index on
-- small tables, a set-once trigger. Re-runnable. Instant.

ALTER TABLE call_dispatch_queue ADD COLUMN IF NOT EXISTS ami_action_id text
    CHECK (ami_action_id IS NULL OR ami_action_id ~ '^[A-Za-z0-9-]{1,100}:[0-9]{1,18}$');
CREATE UNIQUE INDEX IF NOT EXISTS ux_call_dispatch_queue_ami_action_id
    ON call_dispatch_queue (ami_action_id) WHERE ami_action_id IS NOT NULL;

ALTER TABLE call_dispatch_tokens ADD COLUMN IF NOT EXISTS bound_uniqueid text
    CHECK (bound_uniqueid IS NULL OR bound_uniqueid ~ '^[0-9]{1,12}\.[0-9]{1,9}$');

CREATE INDEX IF NOT EXISTS ix_call_legs_agent_uniqueid ON call_legs (uniqueid) WHERE role = 'agent';

CREATE OR REPLACE FUNCTION calling_dispatch_queue_action_id_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.ami_action_id IS NOT NULL AND NEW.ami_action_id IS DISTINCT FROM OLD.ami_action_id THEN
        RAISE EXCEPTION 'the Originate ActionID of a dispatch is set once' USING ERRCODE = 'CL002';
    END IF;
    IF OLD.ami_action_id IS NULL AND NEW.ami_action_id IS NOT NULL
       AND NOT (OLD.state = 'queued' AND NEW.state = 'sending') THEN
        RAISE EXCEPTION 'the Originate ActionID is recorded only by the claim' USING ERRCODE = 'CL002';
    END IF;
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS call_dispatch_queue_action_id_guard ON call_dispatch_queue;
CREATE TRIGGER call_dispatch_queue_action_id_guard
    BEFORE UPDATE ON call_dispatch_queue
    FOR EACH ROW EXECUTE FUNCTION calling_dispatch_queue_action_id_guard();

COMMENT ON COLUMN call_dispatch_queue.ami_action_id IS
    'Asterisk <= 11: ActionID of the dispatch''s Originate, recorded before sending; its OriginateResponse binds the agent leg.';
COMMENT ON COLUMN call_dispatch_tokens.bound_uniqueid IS
    'Asterisk <= 11: the agent channel Uniqueid the FastAGI hook allowed (the token itself names cawp-<attempt>).';
