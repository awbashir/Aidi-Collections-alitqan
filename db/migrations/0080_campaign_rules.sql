-- 0080 campaign rules from customer feedback 2026-10-21 (services/calling/campaign_rules.py).
--
-- (6) "a client whose number is wrong («رقم غير صحيح» recorded) or who has PAID is called again
--     by campaigns". Two settings flags on the note statuses, editable in the statuses settings
--     form and returned by GET /comment_status:
--
-- "CommentStatuses".stops_campaigns       the case's LATEST note with this status (main or
--   sub-status) takes the case out of every campaign pool (pool reason status_stops_campaigns),
--   and the dispatcher re-checks it right before the Originate. E.g. «تم السداد», «مخالصة»,
--   «متوفي».
-- "CommentStatuses".blocks_dialed_number  recording this status on a call (wrap-up, manual call
--   log) blocks THAT dialled contact number exactly like the outcome wrong_number does (the
--   Phase 1 contact block, is_blocked); a legacy note (POST /comments) blocks the note's
--   contactNumber when it is one of the client's contacts. E.g. «رقم التواصل غير صحيح».
--
--   Sub-statuses: UNLIKE requires_promise (0079) nothing is copied or propagated. Each row keeps
--   its own flag and a note's effective flag is (main status flag OR sub-status flag): a flagged
--   main status covers all its sub-statuses, and one sub-status can be flagged alone (e.g. only
--   «كامل المبلغ» under «سداد محكمة»).
--
--   Seed (conservative; editable in settings): by trimmed name, live rows, any level:
--     stops_campaigns       'تم السداد', 'مخالصة', 'متوفي'
--     blocks_dialed_number  'رقم التواصل غير صحيح', 'رقم غير صحيح'
--   ('مسجون' is NOT seeded: the family often pays; a supervisor can tick it.)
--
-- (4) campaign_case_assignments: a campaign call on an UNASSIGNED case wrapped up with a promise
--   (outcome promise_discussed, a requires_promise status, or a promise in the wrap-up) assigns
--   the case to the agent who made the call, in the wrap-up's transaction, through the legacy
--   assignment writes (clints.user_id + "SepcifyUsers"; the 0010 trigger moves the open
--   follow-up). One row per such assignment (append-only), for the campaign pool view and audit.
--
-- (2) campaign_attempts.contact_position / contact_count: the dialled number's place in the
--   debtor's callable numbers (campaign_pool.choose_contact rotation: every number is tried
--   before any is retried) at dispatch, for the campaign views. NULL on older rows.
--
-- Locks / duration: ADD COLUMN with a constant default is a catalog change (PG 11+);
-- "CommentStatuses" is a small settings table; campaign_attempts gets two nullable columns
-- (catalog only); the new table is empty. Re-runnable.

ALTER TABLE "CommentStatuses" ADD COLUMN IF NOT EXISTS stops_campaigns boolean NOT NULL DEFAULT false;
ALTER TABLE "CommentStatuses" ADD COLUMN IF NOT EXISTS blocks_dialed_number boolean NOT NULL DEFAULT false;

UPDATE "CommentStatuses"
   SET stops_campaigns = true
 WHERE deleted_at IS NULL AND btrim(name) IN ('تم السداد', 'مخالصة', 'متوفي') AND NOT stops_campaigns;

UPDATE "CommentStatuses"
   SET blocks_dialed_number = true
 WHERE deleted_at IS NULL AND btrim(name) IN ('رقم التواصل غير صحيح', 'رقم غير صحيح') AND NOT blocks_dialed_number;

COMMENT ON COLUMN "CommentStatuses".stops_campaigns IS
    'The case''s latest note with this status (main or sub-status) excludes the case from every campaign (0080). Not inherited by copy: effective = main OR sub flag.';
COMMENT ON COLUMN "CommentStatuses".blocks_dialed_number IS
    'Recording this status on a call blocks the dialled contact number like the wrong_number outcome (0080). Effective = main OR sub flag.';

ALTER TABLE campaign_attempts ADD COLUMN IF NOT EXISTS contact_position integer
    CHECK (contact_position IS NULL OR contact_position >= 1);
ALTER TABLE campaign_attempts ADD COLUMN IF NOT EXISTS contact_count integer
    CHECK (contact_count IS NULL OR contact_count >= 1);

CREATE TABLE IF NOT EXISTS campaign_case_assignments (
    id           uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    campaign_id  uuid        NOT NULL REFERENCES campaigns (id),
    case_id      uuid        NOT NULL,
    attempt_id   uuid        NOT NULL UNIQUE REFERENCES call_attempts (id),
    user_id      uuid        NOT NULL,                 -- the agent who made the call (the new owner)
    assigned_by  uuid,                                 -- who saved the wrap-up
    promise_id   uuid,
    cause        text        NOT NULL CHECK (cause IN ('outcome', 'status', 'promise')),
    created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_campaign_case_assignments_case
    ON campaign_case_assignments (campaign_id, case_id, created_at DESC);

CREATE OR REPLACE FUNCTION campaign_case_assignments_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'campaign case assignments are append-only' USING ERRCODE = 'CL001';
END $$;

DROP TRIGGER IF EXISTS campaign_case_assignments_append_only ON campaign_case_assignments;
CREATE TRIGGER campaign_case_assignments_append_only
    BEFORE UPDATE OR DELETE ON campaign_case_assignments
    FOR EACH ROW EXECUTE FUNCTION campaign_case_assignments_append_only();

COMMENT ON TABLE campaign_case_assignments IS
    'Unassigned cases a campaign call assigned to its agent because the wrap-up recorded a promise (0080). Append-only.';
