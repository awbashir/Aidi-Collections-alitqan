-- migrate:no-transaction
-- 0085 agent leg -> attempt lookups by Uniqueid on Asterisk 11 (CALLING_PBX_EVENTS=legacy;
-- services/calling/legs.attempt_of_agent, call control). Split out of 0084 so that the busy
-- call_legs table is never locked against writes while the index builds.
--
-- Partial: only agent legs (one per click-to-call / campaign attempt).
-- CREATE INDEX CONCURRENTLY: no lock that blocks reads or writes; it must run outside a
-- transaction (hence the header) and is idempotent (IF NOT EXISTS). If a build is
-- interrupted it leaves an INVALID index: drop it (DROP INDEX CONCURRENTLY) and re-run.
-- The lookups work without it (a scan of call_legs).

CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_call_legs_agent_uniqueid ON call_legs (uniqueid) WHERE role = 'agent';
