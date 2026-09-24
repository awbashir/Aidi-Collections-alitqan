-- 0052 click-to-call (Phase 5 P1): dispatch handoff, calling-service status, reconciliation
-- log, review flags, and the evidence-proven "no customer contact" reservation release.
-- docs/phase5-spec.md, review/phase5-click-to-call.md.
--
-- call_dispatch_queue      the API -> calling service handoff. POST /v2/calls authorizes the
--                          attempt and enqueues ONE row; the calling service (the only AMI owner)
--                          claims it with FOR UPDATE SKIP LOCKED, rechecks every rule, issues the
--                          one-shot token (plaintext only in its memory), binds the channel and
--                          the PBX epoch, commits, and only then sends the Originate. The row
--                          records what happened to the submission:
--                            queued -> sending -> submitted       (AMI Success: SUBMITTED, never connected)
--                                              -> rejected        (AMI Error: nothing was queued)
--                                              -> unacknowledged  (no/lost answer: the attempt is uncertain)
--                            queued -> refused   (the final recheck failed; never sent)
--                                   -> expired   (not claimed in time; never sent)
--                                   -> cancelled (the collector cancelled before the claim)
--                          The epoch the API saw is kept: a PBX restart in between refuses the send.
-- calling_service_status   a mutable heartbeat of each calling-service process (AMI state, epoch,
--                          dispatcher/reconciler loops). The API only dispatches when a fresh
--                          heartbeat reports a connected AMI session.
-- call_reconciliations     append-only log of every reconciliation decision about an attempt:
--                          automatic (the service: channel inventory + CDR lookup) or a supervisor
--                          (reason required). A decision that keeps the attempt uncertain is logged
--                          only when the observation changes.
-- call_review_flags        things a supervisor must look at: a wrap-up saved on a locked/archived
--                          case (its protected case changes were not applied), late evidence after a
--                          wrap-up or a terminal state, an evidence conflict, an uncertain attempt
--                          reconciliation could not resolve. One OPEN flag per attempt and kind;
--                          resolved once, with who and why. Never deleted.
-- call_reservations        new release source `no_customer_contact`: the attempt FAILED (or was
--                          cancelled) and the evidence proves no customer leg can exist - no AGI
--                          allow was ever recorded for it and no customer leg or customer dialing
--                          was observed (the restricted dialplan dials the customer only after an
--                          AGI allow). Still never a timeout or a restart.
--
-- No FK to clints/users. Locks: new tables; the call_reservations CHECK swap and the guard
-- replacement take a brief ACCESS EXCLUSIVE lock on call_reservations (small table). Instant.

-- --------------------------------------------------------------------------- dispatch queue
CREATE TABLE IF NOT EXISTS call_dispatch_queue (
    id                  uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    attempt_id          uuid        NOT NULL UNIQUE REFERENCES call_attempts (id),
    state               text        NOT NULL DEFAULT 'queued' CHECK (state IN (
                            'queued', 'sending', 'submitted', 'rejected', 'unacknowledged',
                            'refused', 'expired', 'cancelled')),
    expected_epoch      text        NOT NULL CHECK (length(expected_epoch) <= 100),
    context             text        NOT NULL CHECK (context ~ '^collections-human-[a-z0-9_-]{1,50}$'),
    driver              text        NOT NULL DEFAULT 'PJSIP' CHECK (driver IN ('PJSIP', 'SIP')),
    agent_ring_seconds  integer     NOT NULL CHECK (agent_ring_seconds BETWEEN 5 AND 60),
    max_call_seconds    integer     NOT NULL CHECK (max_call_seconds BETWEEN 30 AND 14400),
    created_by          uuid        NOT NULL,
    created_at          timestamptz NOT NULL DEFAULT now(),
    expires_at          timestamptz NOT NULL,
    claimed_at          timestamptz,
    claimed_by          text        CHECK (claimed_by IS NULL OR length(claimed_by) <= 200),
    finished_at         timestamptz,
    result_code         text        CHECK (result_code IS NULL OR result_code ~ '^[a-z][a-z0-9_]{0,63}$'),
    ami_response        text        CHECK (ami_response IS NULL OR ami_response IN ('Success', 'Error')),
    CONSTRAINT call_dispatch_queue_expiry CHECK (expires_at > created_at AND expires_at <= created_at + interval '5 minutes'),
    CONSTRAINT call_dispatch_queue_finished CHECK ((state IN ('queued', 'sending')) = (finished_at IS NULL))
);

CREATE INDEX IF NOT EXISTS ix_call_dispatch_queue_queued ON call_dispatch_queue (created_at) WHERE state = 'queued';
CREATE INDEX IF NOT EXISTS ix_call_dispatch_queue_sending ON call_dispatch_queue (claimed_at) WHERE state = 'sending';

CREATE OR REPLACE FUNCTION calling_dispatch_queue_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'dispatch queue rows are never deleted' USING ERRCODE = 'CL001';
    END IF;
    IF (NEW.id, NEW.attempt_id, NEW.expected_epoch, NEW.context, NEW.driver, NEW.agent_ring_seconds,
        NEW.max_call_seconds, NEW.created_by, NEW.created_at, NEW.expires_at)
       IS DISTINCT FROM
       (OLD.id, OLD.attempt_id, OLD.expected_epoch, OLD.context, OLD.driver, OLD.agent_ring_seconds,
        OLD.max_call_seconds, OLD.created_by, OLD.created_at, OLD.expires_at) THEN
        RAISE EXCEPTION 'dispatch queue identity columns are immutable' USING ERRCODE = 'CL002';
    END IF;
    IF (OLD.claimed_at IS NOT NULL AND NEW.claimed_at IS DISTINCT FROM OLD.claimed_at)
       OR (OLD.claimed_by IS NOT NULL AND NEW.claimed_by IS DISTINCT FROM OLD.claimed_by)
       OR (OLD.finished_at IS NOT NULL AND NEW.finished_at IS DISTINCT FROM OLD.finished_at)
       OR (OLD.result_code IS NOT NULL AND NEW.result_code IS DISTINCT FROM OLD.result_code)
       OR (OLD.ami_response IS NOT NULL AND NEW.ami_response IS DISTINCT FROM OLD.ami_response) THEN
        RAISE EXCEPTION 'dispatch queue facts are set once' USING ERRCODE = 'CL002';
    END IF;
    IF NEW.state IS DISTINCT FROM OLD.state AND NOT (
           (OLD.state = 'queued' AND NEW.state IN ('sending', 'refused', 'expired', 'cancelled'))
        OR (OLD.state = 'sending' AND NEW.state IN ('submitted', 'rejected', 'unacknowledged'))) THEN
        RAISE EXCEPTION 'dispatch queue transition % -> % is not allowed', OLD.state, NEW.state
            USING ERRCODE = 'CL003';
    END IF;
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS call_dispatch_queue_guard ON call_dispatch_queue;
CREATE TRIGGER call_dispatch_queue_guard
    BEFORE UPDATE OR DELETE ON call_dispatch_queue
    FOR EACH ROW EXECUTE FUNCTION calling_dispatch_queue_guard();

COMMENT ON TABLE call_dispatch_queue IS
    'Phase 5 P1: API -> calling service click-to-call handoff (claimed FOR UPDATE SKIP LOCKED). '
    'submitted = AMI accepted the Originate: SUBMITTED, never connected.';

-- --------------------------------------------------------------------------- service status
CREATE TABLE IF NOT EXISTS calling_service_status (
    worker_id       text        PRIMARY KEY CHECK (length(worker_id) <= 200),
    host            text,
    started_at      timestamptz NOT NULL DEFAULT now(),
    heartbeat_at    timestamptz NOT NULL DEFAULT now(),
    ami_connected   boolean     NOT NULL DEFAULT false,
    epoch           text        CHECK (epoch IS NULL OR length(epoch) <= 100),
    epoch_uuid      text        CHECK (epoch_uuid IS NULL OR length(epoch_uuid) <= 100),
    dispatcher      boolean     NOT NULL DEFAULT false,
    reconciler      boolean     NOT NULL DEFAULT false,
    stopped_at      timestamptz,
    detail          jsonb       NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(detail) = 'object')
);

CREATE INDEX IF NOT EXISTS ix_calling_service_status_heartbeat ON calling_service_status (heartbeat_at DESC);

COMMENT ON TABLE calling_service_status IS
    'Phase 5 P1: heartbeat of each calling service process. A fresh row with ami_connected is required to dispatch.';

-- --------------------------------------------------------------------------- reconciliations
CREATE TABLE IF NOT EXISTS call_reconciliations (
    id                    bigserial   PRIMARY KEY,
    attempt_id            uuid        NOT NULL REFERENCES call_attempts (id),
    occurred_at           timestamptz NOT NULL DEFAULT now(),
    source                text        NOT NULL CHECK (source IN ('service', 'supervisor')),
    actor_user_id         uuid,
    prior_state           text        NOT NULL,
    prior_reason          text,
    decision              text        NOT NULL CHECK (decision IN ('ended', 'failed', 'kept_uncertain')),
    code                  text        NOT NULL CHECK (code ~ '^[a-z][a-z0-9_]{0,63}$'),
    reason                text        CHECK (reason IS NULL OR length(reason) <= 1000),
    inventory             jsonb,
    cdr                   jsonb,
    evidence              jsonb,
    reservation_released  boolean     NOT NULL DEFAULT false,
    CONSTRAINT call_reconciliations_supervisor CHECK (
        source <> 'supervisor' OR (actor_user_id IS NOT NULL AND btrim(coalesce(reason, '')) <> ''))
);

CREATE INDEX IF NOT EXISTS ix_call_reconciliations_attempt ON call_reconciliations (attempt_id, id);

DROP TRIGGER IF EXISTS call_reconciliations_append_only ON call_reconciliations;
CREATE TRIGGER call_reconciliations_append_only
    BEFORE UPDATE OR DELETE ON call_reconciliations
    FOR EACH ROW EXECUTE FUNCTION calling_reject_change();

DROP TRIGGER IF EXISTS call_reconciliations_no_truncate ON call_reconciliations;
CREATE TRIGGER call_reconciliations_no_truncate
    BEFORE TRUNCATE ON call_reconciliations
    FOR EACH STATEMENT EXECUTE FUNCTION calling_reject_change();

COMMENT ON TABLE call_reconciliations IS
    'Phase 5 P1: append-only reconciliation decisions (service: channel inventory + CDR; supervisor: with reason).';

-- --------------------------------------------------------------------------- review flags
CREATE TABLE IF NOT EXISTS call_review_flags (
    id                 uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    attempt_id         uuid        NOT NULL REFERENCES call_attempts (id),
    case_id            uuid        NOT NULL,
    kind               text        NOT NULL CHECK (kind IN (
                           'wrapup_needs_review', 'late_evidence', 'evidence_conflict', 'reconciliation_unresolved')),
    reason             text        NOT NULL CHECK (reason ~ '^[a-z][a-z0-9_]{0,63}$'),
    detail             jsonb       NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(detail) = 'object'),
    event_id           bigint,
    blocks_calling     boolean     NOT NULL DEFAULT false,
    created_at         timestamptz NOT NULL DEFAULT now(),
    resolved_at        timestamptz,
    resolved_by        uuid,
    resolution_reason  text,
    CONSTRAINT call_review_flags_resolution CHECK (
        (resolved_at IS NULL AND resolved_by IS NULL AND resolution_reason IS NULL)
        OR (resolved_at IS NOT NULL AND resolved_by IS NOT NULL
            AND btrim(coalesce(resolution_reason, '')) <> '' AND length(resolution_reason) <= 1000))
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_call_review_flags_open
    ON call_review_flags (attempt_id, kind) WHERE resolved_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_call_review_flags_open_case
    ON call_review_flags (case_id) WHERE resolved_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_call_review_flags_created ON call_review_flags (created_at DESC);

CREATE OR REPLACE FUNCTION calling_review_flags_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'review flags are never deleted' USING ERRCODE = 'CL001';
    END IF;
    IF OLD.resolved_at IS NOT NULL
       OR (NEW.id, NEW.attempt_id, NEW.case_id, NEW.kind, NEW.reason, NEW.detail, NEW.event_id, NEW.blocks_calling,
           NEW.created_at)
          IS DISTINCT FROM
          (OLD.id, OLD.attempt_id, OLD.case_id, OLD.kind, OLD.reason, OLD.detail, OLD.event_id, OLD.blocks_calling,
           OLD.created_at) THEN
        RAISE EXCEPTION 'only the resolution of a review flag can be recorded, once' USING ERRCODE = 'CL001';
    END IF;
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS call_review_flags_guard ON call_review_flags;
CREATE TRIGGER call_review_flags_guard
    BEFORE UPDATE OR DELETE ON call_review_flags
    FOR EACH ROW EXECUTE FUNCTION calling_review_flags_guard();

COMMENT ON TABLE call_review_flags IS
    'Phase 5 P1: supervisor review items (locked/archived wrap-up, late evidence, conflicts, unresolved uncertainty).';
COMMENT ON COLUMN call_review_flags.blocks_calling IS
    'While open, dispatch refuses the case (e.g. a do-not-call or wrong number deferred on a locked case).';

-- --------------------------------------------------------------------------- reservations
ALTER TABLE call_reservations DROP CONSTRAINT IF EXISTS call_reservations_release_source_check;
ALTER TABLE call_reservations ADD CONSTRAINT call_reservations_release_source_check
    CHECK (release_source IS NULL OR release_source IN ('wrapup', 'cancel_unsent', 'reconciled', 'no_customer_contact'));

CREATE OR REPLACE FUNCTION calling_reservations_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    a record;
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'call reservations are never deleted' USING ERRCODE = 'CL001';
    END IF;
    IF OLD.released_at IS NOT NULL THEN
        RAISE EXCEPTION 'a released reservation is immutable' USING ERRCODE = 'CL001';
    END IF;
    IF (NEW.id, NEW.attempt_id, NEW.collector_user_id, NEW.case_id, NEW.extension, NEW.reserved_at)
       IS DISTINCT FROM
       (OLD.id, OLD.attempt_id, OLD.collector_user_id, OLD.case_id, OLD.extension, OLD.reserved_at) THEN
        RAISE EXCEPTION 'only the release of a reservation can be recorded' USING ERRCODE = 'CL001';
    END IF;
    IF NEW.released_at IS NOT NULL THEN
        SELECT state, dispatched_at, wrapped_up_at, customer_dialing_at INTO a
          FROM call_attempts WHERE id = NEW.attempt_id;
        IF NEW.release_source = 'wrapup' AND a.wrapped_up_at IS NULL THEN
            RAISE EXCEPTION 'a reservation is released by wrap-up only after the wrap-up is saved'
                USING ERRCODE = 'CL004';
        END IF;
        IF NEW.release_source = 'cancel_unsent' AND NOT (a.state = 'cancelled' AND a.dispatched_at IS NULL) THEN
            RAISE EXCEPTION 'only a cancelled, never-dispatched attempt releases by cancellation'
                USING ERRCODE = 'CL004';
        END IF;
        IF NEW.release_source = 'no_customer_contact' AND NOT (
               a.state IN ('failed', 'cancelled') AND a.customer_dialing_at IS NULL
               AND NOT EXISTS (SELECT 1 FROM call_agi_decisions d
                                WHERE d.attempt_id = NEW.attempt_id AND d.decision = 'allow')
               AND NOT EXISTS (SELECT 1 FROM call_legs l
                                WHERE l.attempt_id = NEW.attempt_id AND l.role = 'customer')) THEN
            RAISE EXCEPTION 'no_customer_contact needs a failed attempt with no AGI allow and no customer leg'
                USING ERRCODE = 'CL004';
        END IF;
    END IF;
    RETURN NEW;
END $$;
