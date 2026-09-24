-- 0051 human calling foundation (Phase 5 P0): reviewed wrap-ups and calling blocks.
-- docs/phase5-spec.md (hard rule 5), PROJECT-NOTES §2.9, review/phase5-foundation.md.
--
-- call_wrapups     ONE per attempt (unique attempt_id), immutable once saved. It records the
--                  human-reviewed outcome and links what the same transaction wrote:
--                    note_id       the legacy "Comments" row (so every note trigger fires:
--                                  last-comment, follow-up from the callback, history)
--                    follow_up_id  the follow-up the callback opened (comments_follow_up_trg)
--                    promise_id    an EXPLICITLY agreed promise, created through the Phase 1
--                                  promise service; only with outcome `connected`.
--                                  `promise_discussed` never creates a promise (CHECK below).
--                    contact_blocked  wrong number blocked the contact (Phase 1 is_blocked)
--                    calling_block_id do-not-call blocked the case (calling_blocks)
--                  `reviewed_by` is the person who reviewed and saved it (a human, always).
-- calling_blocks   case-level calling blocks. Today only `do_not_call` (opt-out): one ACTIVE
--                  block per case and kind; released only explicitly, once, with a reason.
--                  Contact-level blocks (wrong number) reuse the Phase 1 "ClintContacts".is_blocked
--                  flag, which the Phase 1 contact screens already show and manage.
--
-- No FK to clints/users/"Comments" (legacy hard-deletes). Locks: new tables only. Instant.

CREATE TABLE IF NOT EXISTS calling_blocks (
    id              uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    case_id         uuid        NOT NULL,
    kind            text        NOT NULL DEFAULT 'do_not_call' CHECK (kind IN ('do_not_call')),
    reason          text        NOT NULL CHECK (btrim(reason) <> '' AND length(reason) <= 1000),
    source          text        NOT NULL CHECK (source IN ('wrapup', 'manual')),
    attempt_id      uuid        REFERENCES call_attempts (id),
    created_by      uuid        NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    released_at     timestamptz,
    released_by     uuid,
    release_reason  text,
    CONSTRAINT calling_blocks_release_fields CHECK (
        (released_at IS NULL AND released_by IS NULL AND release_reason IS NULL)
        OR (released_at IS NOT NULL AND released_by IS NOT NULL
            AND release_reason IS NOT NULL AND btrim(release_reason) <> '' AND length(release_reason) <= 1000))
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_calling_blocks_active
    ON calling_blocks (case_id, kind) WHERE released_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_calling_blocks_case ON calling_blocks (case_id, created_at DESC);

CREATE OR REPLACE FUNCTION calling_blocks_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'calling blocks are never deleted' USING ERRCODE = 'CL001';
    END IF;
    IF OLD.released_at IS NOT NULL
       OR (NEW.id, NEW.case_id, NEW.kind, NEW.reason, NEW.source, NEW.attempt_id, NEW.created_by, NEW.created_at)
          IS DISTINCT FROM
          (OLD.id, OLD.case_id, OLD.kind, OLD.reason, OLD.source, OLD.attempt_id, OLD.created_by, OLD.created_at) THEN
        RAISE EXCEPTION 'only the release of a calling block can be recorded, once' USING ERRCODE = 'CL001';
    END IF;
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS calling_blocks_guard ON calling_blocks;
CREATE TRIGGER calling_blocks_guard
    BEFORE UPDATE OR DELETE ON calling_blocks
    FOR EACH ROW EXECUTE FUNCTION calling_blocks_guard();

COMMENT ON TABLE calling_blocks IS
    'Phase 5: do-not-call on a case (opt-out). Dispatch (P1/P2) refuses a case with an active block.';

-- --------------------------------------------------------------------------- wrap-ups
CREATE TABLE IF NOT EXISTS call_wrapups (
    id                uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    attempt_id        uuid        NOT NULL UNIQUE REFERENCES call_attempts (id),
    case_id           uuid        NOT NULL,
    outcome           text        NOT NULL CHECK (outcome IN (
                          'connected', 'no_answer', 'busy', 'promise_discussed', 'dispute',
                          'wrong_number', 'do_not_call')),
    note_id           uuid        NOT NULL,
    follow_up_id      uuid,
    callback_at       timestamptz,
    promise_id        uuid,
    contact_id        uuid,
    contact_blocked   boolean     NOT NULL DEFAULT false,
    calling_block_id  uuid        REFERENCES calling_blocks (id),
    reviewed_by       uuid        NOT NULL,
    reviewed_at       timestamptz NOT NULL DEFAULT now(),
    request_id        text,
    idempotency_key   text,
    CONSTRAINT call_wrapups_promise_only_when_agreed CHECK (promise_id IS NULL OR outcome = 'connected'),
    CONSTRAINT call_wrapups_dnc_blocks_case CHECK (outcome <> 'do_not_call' OR calling_block_id IS NOT NULL),
    CONSTRAINT call_wrapups_block_only_dnc CHECK (calling_block_id IS NULL OR outcome = 'do_not_call'),
    CONSTRAINT call_wrapups_contact_block_only_wrong_number CHECK (NOT contact_blocked OR outcome = 'wrong_number'),
    CONSTRAINT call_wrapups_follow_up_needs_callback CHECK (follow_up_id IS NULL OR callback_at IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS ix_call_wrapups_case ON call_wrapups (case_id, reviewed_at DESC);
CREATE INDEX IF NOT EXISTS ix_call_wrapups_reviewer ON call_wrapups (reviewed_by, reviewed_at DESC);

DROP TRIGGER IF EXISTS call_wrapups_append_only ON call_wrapups;
CREATE TRIGGER call_wrapups_append_only
    BEFORE UPDATE OR DELETE ON call_wrapups
    FOR EACH ROW EXECUTE FUNCTION calling_reject_change();

COMMENT ON TABLE call_wrapups IS
    'Phase 5: one immutable reviewed wrap-up per call attempt (note + callback + agreed promise + blocks).';
