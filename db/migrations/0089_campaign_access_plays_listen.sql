-- 0089 campaign follow-ups (customer request 2026-10-29).
--
-- campaigns.message_plays   message campaigns: how many times the message plays when no key is
--                           pressed (1..5; 2 = the behaviour so far). Sent to the PBX on the
--                           Originate (COLLECTIONS_MSG_PLAYS_MAX, re-checked by the dialplan).
-- message_notes.listened    (jsonb key, no DDL) {seconds, status_id, sub_status_id?}: a client who
--                           stays on the line more than `seconds` after answering gets that status
--                           note instead of the per-result one, whatever key they pressed.
-- campaign_case_assignments.message_call_id
--                           "a promise moves the client" now also covers a client a message
--                           campaign transferred to a member (the member saves an ordinary note
--                           with a promise): such a row names the message call instead of a call
--                           attempt, so attempt_id becomes nullable and exactly one of the two is set.
--
-- Catalog-only column additions; DROP NOT NULL is a catalog change; the CHECKs are added NOT VALID
-- then validated (every existing row has attempt_id and the default plays). Re-runnable.

ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS message_plays smallint NOT NULL DEFAULT 2;

ALTER TABLE campaign_case_assignments ALTER COLUMN attempt_id DROP NOT NULL;
ALTER TABLE campaign_case_assignments ADD COLUMN IF NOT EXISTS message_call_id uuid UNIQUE REFERENCES message_calls (id);

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'campaigns_message_plays_check') THEN
        ALTER TABLE campaigns ADD CONSTRAINT campaigns_message_plays_check CHECK (message_plays BETWEEN 1 AND 5) NOT VALID;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'campaign_case_assignments_source_check') THEN
        ALTER TABLE campaign_case_assignments ADD CONSTRAINT campaign_case_assignments_source_check
            CHECK ((attempt_id IS NULL) <> (message_call_id IS NULL)) NOT VALID;
    END IF;
END $$;
ALTER TABLE campaigns VALIDATE CONSTRAINT campaigns_message_plays_check;
ALTER TABLE campaign_case_assignments VALIDATE CONSTRAINT campaign_case_assignments_source_check;
