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
-- The agent-leg index on call_legs (the large, busy table) is built online by 0085
-- (CREATE INDEX CONCURRENTLY, no-transaction).
--
-- Locks: new nullable columns without default (metadata only); their CHECK constraints are added
-- NOT VALID (no scan under the ADD) and then validated (one scan of rows that are all NULL; the two
-- tables hold one row per dispatch and are pruned). A partial unique index on the small dispatch
-- queue, a set-once trigger. The ALTERs take a brief ACCESS EXCLUSIVE lock on call_dispatch_queue /
-- call_dispatch_tokens until the commit (lock_timeout 5 s from the runner: on a busy server it
-- fails fast and rolls back; re-run). Nothing here locks call_legs or call_events. Re-runnable.

ALTER TABLE call_dispatch_queue ADD COLUMN IF NOT EXISTS ami_action_id text;
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conrelid = 'call_dispatch_queue'::regclass AND conname = 'call_dispatch_queue_ami_action_id_format') THEN
        ALTER TABLE call_dispatch_queue ADD CONSTRAINT call_dispatch_queue_ami_action_id_format
            CHECK (ami_action_id IS NULL OR ami_action_id ~ '^[A-Za-z0-9-]{1,100}:[0-9]{1,18}$') NOT VALID;
    END IF;
END $$;
ALTER TABLE call_dispatch_queue VALIDATE CONSTRAINT call_dispatch_queue_ami_action_id_format;
CREATE UNIQUE INDEX IF NOT EXISTS ux_call_dispatch_queue_ami_action_id
    ON call_dispatch_queue (ami_action_id) WHERE ami_action_id IS NOT NULL;

ALTER TABLE call_dispatch_tokens ADD COLUMN IF NOT EXISTS bound_uniqueid text;
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conrelid = 'call_dispatch_tokens'::regclass AND conname = 'call_dispatch_tokens_bound_uniqueid_format') THEN
        ALTER TABLE call_dispatch_tokens ADD CONSTRAINT call_dispatch_tokens_bound_uniqueid_format
            CHECK (bound_uniqueid IS NULL OR bound_uniqueid ~ '^[0-9]{1,12}\.[0-9]{1,9}$') NOT VALID;
    END IF;
END $$;
ALTER TABLE call_dispatch_tokens VALIDATE CONSTRAINT call_dispatch_tokens_bound_uniqueid_format;

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
