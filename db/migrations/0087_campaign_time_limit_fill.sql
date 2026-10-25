-- 0087 campaign end time and random fill (customer feedback 2026-10-25,
-- services/calling/campaign.py, campaign_dispatch.py, campaign_pool.py, campaign_rules.py).
--
-- campaigns.ends_at      "add a time limit to the campaign": the date and time the campaign stops
--   by itself. The calling service moves a running or paused campaign whose ends_at has passed to
--   `draining` (no new call; calls in progress finish and are wrapped up; then `stopped`, the
--   Drain path), and the claim refuses a call queued after it. A campaign whose ends_at has passed
--   cannot be started. NULL = no limit.
-- campaigns.fill_random  "when the user finished all the clients assigned to him, randomly select
--   from the campaign's list even if the client is assigned to another user": an agent with no
--   callable case of their own is dispatched a RANDOM case of the campaign's pool (any owner,
--   inside the creator's scope, every calling rule), with the campaign grant for that call.
-- campaign_case_assignments.previous_user_id  "if the user records a promise to pay, transfer
--   that client to the user": in a fill_random campaign a promise moves the case to the agent who
--   made the call even when another collector owned it; the previous owner is kept here (NULL =
--   it was unassigned).
--
-- Catalog-only column additions (constant default, PG 11+): no table rewrite, brief lock.
-- Re-runnable.

ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS ends_at timestamptz;
ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS fill_random boolean NOT NULL DEFAULT false;
ALTER TABLE campaign_case_assignments ADD COLUMN IF NOT EXISTS previous_user_id uuid;

COMMENT ON COLUMN campaigns.ends_at IS
    'The campaign drains (no new call) and then stops by itself at this time; NULL = no limit (0087).';
COMMENT ON COLUMN campaigns.fill_random IS
    'An agent with no callable case of their own gets a random case of the pool, any owner (0087).';
COMMENT ON COLUMN campaign_case_assignments.previous_user_id IS
    'The collector the case was taken from by a promise in a fill_random campaign; NULL = unassigned (0087).';
