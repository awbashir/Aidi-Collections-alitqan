-- 0088 message campaigns (customer request 2026-10-25, docs/alitqan-message-campaigns-plan.md,
-- services/calling/message_campaign.py).
--
-- "Campaigns with an auto message played to the client when they pick up, instead of
--  transferring the call; press 1 to transfer to an agent, 2 to close."
--
-- campaigns.kind            'agent' (every campaign so far: extension-first, one call per ready
--                           member) | 'message' (the PBX dials the client and plays the message).
-- campaigns.max_concurrent  message campaigns: at most this many clients dialled at once (1..20).
-- campaigns.message_transfer  message campaigns: key 1 rings a free campaign member.
-- campaigns.message_notes   message campaigns: the note recorded on the case for each result,
--                           {"no_agent"|"closed"|"no_choice": {"status_id", "sub_status_id"},
--                            "contact_method_id": ...}; a result without a status records no note.
-- campaign_sounds           the uploaded audio (converted to 8 kHz mono 16-bit WAV), one per kind;
--                           pbx_installed_at = when the PBX accepted it.
-- message_calls             one row per dial: the one-shot token (SHA-256 only), the PBX gate, the
--                           state the PBX reported (CollectionsMessage user events), the key, the
--                           member a "1" rang, the result and the note it recorded. Never deleted.
--
-- New tables + catalog-only column additions; the kind CHECK is added NOT VALID then validated
-- (every existing row is 'agent' by the default). Re-runnable.

ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS kind text NOT NULL DEFAULT 'agent';
ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS max_concurrent integer NOT NULL DEFAULT 5;
ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS message_transfer boolean NOT NULL DEFAULT true;
ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS message_notes jsonb;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'campaigns_kind_check') THEN
        ALTER TABLE campaigns ADD CONSTRAINT campaigns_kind_check CHECK (kind IN ('agent', 'message')) NOT VALID;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'campaigns_max_concurrent_check') THEN
        ALTER TABLE campaigns ADD CONSTRAINT campaigns_max_concurrent_check
            CHECK (max_concurrent BETWEEN 1 AND 20) NOT VALID;
    END IF;
END $$;
ALTER TABLE campaigns VALIDATE CONSTRAINT campaigns_kind_check;
ALTER TABLE campaigns VALIDATE CONSTRAINT campaigns_max_concurrent_check;

CREATE TABLE IF NOT EXISTS campaign_sounds (
    campaign_id       uuid        NOT NULL REFERENCES campaigns (id),
    kind              text        NOT NULL CHECK (kind IN ('message', 'callback', 'goodbye')),
    original_name     text        CHECK (original_name IS NULL OR length(original_name) <= 200),
    audio             bytea       NOT NULL CHECK (octet_length(audio) <= 4000000),
    duration_seconds  numeric(6,1) NOT NULL CHECK (duration_seconds > 0 AND duration_seconds <= 120),
    sha256            text        NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    uploaded_by       uuid,
    uploaded_at       timestamptz NOT NULL DEFAULT now(),
    pbx_installed_at  timestamptz,
    PRIMARY KEY (campaign_id, kind)
);

CREATE TABLE IF NOT EXISTS message_calls (
    id                 uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    campaign_id        uuid        NOT NULL REFERENCES campaigns (id),
    case_id            uuid        NOT NULL,
    contact_id         uuid,
    destination        text        NOT NULL CHECK (destination ~ '^\+[1-9][0-9]{7,14}$'),
    state              text        NOT NULL DEFAULT 'sending'
                                   CHECK (state IN ('sending', 'dialing', 'answered', 'transferring', 'talking',
                                                    'ended', 'failed', 'uncertain')),
    result             text        CHECK (result IS NULL OR result IN (
                                       'no_answer', 'busy', 'unavailable', 'congestion', 'failed', 'denied',
                                       'no_trunk', 'hung_up', 'no_choice', 'closed', 'no_agent', 'transferred',
                                       'unknown')),
    dial_status        text        CHECK (dial_status IS NULL OR dial_status ~ '^[A-Z_]{1,20}$'),
    ivr_key            text        CHECK (ivr_key IS NULL OR ivr_key ~ '^[0-9*#]$'),
    member_user_id     uuid,
    member_extension   text,
    token_hash         text        CHECK (token_hash IS NULL OR token_hash ~ '^[0-9a-f]{64}$'),
    token_expires_at   timestamptz,
    token_used_at      timestamptz,
    pbx_epoch          text,
    recording          text        CHECK (recording IS NULL OR length(recording) <= 300),
    note_id            uuid,
    reason_code        text        CHECK (reason_code IS NULL OR reason_code ~ '^[a-z][a-z0-9_]{0,63}$'),
    created_at         timestamptz NOT NULL DEFAULT now(),
    dialing_at         timestamptz,
    answered_at        timestamptz,
    transferred_at     timestamptz,
    member_answered_at timestamptz,
    ended_at           timestamptz,
    updated_at         timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_message_calls_campaign ON message_calls (campaign_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_message_calls_case ON message_calls (case_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_message_calls_live ON message_calls (campaign_id)
    WHERE state IN ('sending', 'dialing', 'answered', 'transferring', 'talking');
CREATE INDEX IF NOT EXISTS ix_message_calls_member ON message_calls (member_user_id, transferred_at DESC)
    WHERE member_user_id IS NOT NULL;

COMMENT ON TABLE message_calls IS
    'One dial of a message campaign: token gate, PBX-reported state, key, member, result (0088). Never deleted.';
COMMENT ON TABLE campaign_sounds IS
    'Uploaded message-campaign audio converted to 8 kHz mono 16-bit WAV, one per kind (0088).';
