-- 0075 call control (customer request 2026-10-20; services/calling/call_control.py,
-- api/calling_control.py, PBX dialplan cawp-callctl.conf).
--
-- Server-controlled call control on the PBX, never SIP REFER from a browser (every PJSIP
-- endpoint keeps allow_transfer = no):
--   transfer_blind     the agent's peer (the customer) is sent to another app user's extension
--   transfer_attended  the customer waits on hold while the agent consults the target, then the
--                      agent completes (customer joins the target, agent leaves) or cancels
--   conference         agent + customer move into a ConfBridge room and another extension is invited
--   spy                a supervisor listens to an agent's live call (ChanSpy), whispers or barges
--
-- call_controls is the record (who, from which extension, to which, when, which attempt/case,
-- the PBX channels, the outcome); call_control_ops is the queue the `calling` service (the only
-- AMI owner) claims FOR UPDATE SKIP LOCKED, exactly like call_dispatch_queue: one row per command
-- (start, complete, cancel, remove, mode, stop; `release` = the service frees a customer left
-- alone in a room), sent only on the PBX epoch it was enqueued for,
-- never retried. AMI Success means SUBMITTED; channel facts come from the live channel list.
--
-- Additive only: two new tables, no change to existing ones.

CREATE TABLE IF NOT EXISTS call_controls (
    id                 uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    kind               text        NOT NULL CHECK (kind IN ('transfer_blind', 'transfer_attended', 'conference', 'spy')),
    state              text        NOT NULL DEFAULT 'queued'
                                   CHECK (state IN ('queued', 'active', 'completed', 'cancelled', 'failed', 'ended')),
    mode               text        CHECK (mode IS NULL OR mode IN ('listen', 'whisper', 'barge')),
    requested_by       uuid        NOT NULL REFERENCES users(id),
    from_extension     text        NOT NULL CHECK (from_extension ~ '^[0-9]{2,6}$'),
    target_extension   text        NOT NULL CHECK (target_extension ~ '^[0-9]{2,6}$'),
    target_user_id     uuid        REFERENCES users(id),
    attempt_id         uuid        REFERENCES call_attempts(id),
    case_id            uuid,                                   -- no FK: a case can be hard-deleted
    linkedid           text,
    agent_uniqueid     text,                                   -- the agent's channel (spy: the spied agent's)
    agent_channel      text,
    customer_uniqueid  text,                                   -- the agent's peer (transfer / conference)
    customer_channel   text,
    target_uniqueid    text,                                   -- an originated leg: invitee / consult / spy
    room               text        CHECK (room IS NULL OR room ~ '^cawp-[0-9a-f]{32}$'),
    pbx_epoch          text        NOT NULL,
    result_code        text        CHECK (result_code IS NULL OR result_code ~ '^[a-z][a-z0-9_]{0,63}$'),
    detail             jsonb       NOT NULL DEFAULT '{}'::jsonb,
    created_at         timestamptz NOT NULL DEFAULT now(),
    started_at         timestamptz,                            -- the start command was submitted
    completed_at       timestamptz,
    ended_at           timestamptz,                            -- no channel of it is left on the PBX
    updated_at         timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT call_controls_spy_mode CHECK ((kind = 'spy') = (mode IS NOT NULL))
);

-- one open control per requester at a time (an agent's transfer/conference, a supervisor's listening)
CREATE UNIQUE INDEX IF NOT EXISTS call_controls_one_open
    ON call_controls (requested_by, (kind = 'spy')) WHERE state IN ('queued', 'active');
CREATE INDEX IF NOT EXISTS call_controls_requested ON call_controls (requested_by, created_at DESC);
CREATE INDEX IF NOT EXISTS call_controls_target ON call_controls (target_user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS call_controls_attempt ON call_controls (attempt_id) WHERE attempt_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS call_controls_open ON call_controls (created_at) WHERE ended_at IS NULL;

CREATE TABLE IF NOT EXISTS call_control_ops (
    id              uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    control_id      uuid        NOT NULL REFERENCES call_controls(id),
    op              text        NOT NULL CHECK (op IN ('start', 'complete', 'cancel', 'remove', 'mode', 'stop',
                                                       'release')),
    mode            text        CHECK (mode IS NULL OR mode IN ('listen', 'whisper', 'barge')),
    state           text        NOT NULL DEFAULT 'queued'
                                CHECK (state IN ('queued', 'sending', 'done', 'rejected', 'unacknowledged',
                                                 'refused', 'expired')),
    requested_by    uuid        REFERENCES users(id),          -- NULL: the calling service itself (auto-return)
    expected_epoch  text        NOT NULL,
    expires_at      timestamptz NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    claimed_at      timestamptz,
    claimed_by      text,
    finished_at     timestamptz,
    result_code     text        CHECK (result_code IS NULL OR result_code ~ '^[a-z][a-z0-9_]{0,63}$'),
    ami             jsonb       NOT NULL DEFAULT '[]'::jsonb   -- [{action, response}] (no values)
);

-- one command in flight per control (claim and the API serialize on it)
CREATE UNIQUE INDEX IF NOT EXISTS call_control_ops_one_open
    ON call_control_ops (control_id) WHERE state IN ('queued', 'sending');
CREATE INDEX IF NOT EXISTS call_control_ops_queued ON call_control_ops (created_at) WHERE state = 'queued';
CREATE INDEX IF NOT EXISTS call_control_ops_control ON call_control_ops (control_id, created_at);

COMMENT ON TABLE call_controls IS
    'Server-controlled call control (2026-10-20): transfers, conferences and supervisor listening. '
    'Who asked (requested_by, from_extension), to whom (target_extension/target_user_id), when, the '
    'click-to-call attempt and case when the call has one, and the PBX channels. The original '
    'collector keeps the wrap-up of a transferred click-to-call attempt.';
COMMENT ON TABLE call_control_ops IS
    'Commands of a call control for the calling service (claimed FOR UPDATE SKIP LOCKED, sent once on '
    'the expected PBX epoch, never retried). done = the PBX accepted every action (submitted).';
