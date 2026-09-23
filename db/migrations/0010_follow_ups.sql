-- 0010 first-class follow-ups (Phase 1, stream B; docs/phase1-spec.md "Stream B").
--
-- New tables
--   follow_ups         one row per scheduled callback / next action on a case. At most ONE
--                      open follow-up per case (partial unique index ux_follow_ups_one_open).
--                      Closing is explicit: completed (outcome note + next follow-up or a
--                      "no further action" reason), superseded (a newer callback replaced it),
--                      cancelled. `due_at` is never rewritten on completion, so a missed
--                      callback stays a recorded fact (`completed_late`, `original_due_at`).
--   follow_up_events   append-only history (UPDATE rejected; DELETE only through the case
--                      cascade, so the legacy hard delete of a case keeps working).
--
-- Legacy capture (the old note form keeps working and feeds the new model)
--   comments_follow_up_trg   AFTER INSERT ON "Comments", only for rows with a callback
--                            (next_contact_date IS NOT NULL) that are not deleted: supersedes
--                            the case's open follow-up and opens a new one (source 'note').
--                            A note WITHOUT a callback changes nothing: the legacy next-calls
--                            report (/clints/currentUser/have_nextCalls) keeps listing every
--                            callback comment regardless of later notes, so a plain note never
--                            completed a callback in legacy semantics.
--                            Transaction-local escape hatch: set_config('cawp.skip_note_follow_up',
--                            'on', true) - used by the v2 complete route, which writes its own
--                            outcome note and opens the next follow-up itself.
--   clints_follow_up_owner_trg  AFTER UPDATE OF user_id ON clints (only when it really
--                            changed): the open follow-up's owner follows the case owner, the
--                            same way the legacy next-calls report follows clints.user_id.
--
-- Concurrency: the note trigger takes the case row lock (SELECT ... FOR NO KEY UPDATE, the
-- same lock the existing comments_last_comment_trg UPDATE takes a moment later) before it
-- touches follow_ups, so two notes saved at once on one case serialise instead of the second
-- failing on the one-open unique index. The v2 routes lock in the same order (case, then
-- follow-up).
--
-- Locks: CREATE TABLE/INDEX on the new (empty) tables; CREATE TRIGGER takes a brief
-- SHARE ROW EXCLUSIVE lock on "Comments" and clints (blocks writers, not readers, for
-- milliseconds). lock_timeout 5s (runner default), so on a busy server it fails fast and
-- rolls back completely; re-run it. No data is read or written here (backfill is 0011).

CREATE TABLE IF NOT EXISTS follow_ups (
    id                       uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    case_id                  uuid        NOT NULL REFERENCES clints(id) ON DELETE CASCADE,
    owner_user_id            uuid,                    -- = clints.user_id while open (no FK: legacy hard-deletes users)
    due_at                   timestamptz NOT NULL,
    original_due_at          timestamptz NOT NULL,    -- due_at when first scheduled (reschedules move due_at)
    next_action              text,
    status                   text        NOT NULL DEFAULT 'open'
                             CHECK (status IN ('open', 'completed', 'cancelled', 'superseded')),
    source                   text        NOT NULL CHECK (source IN ('note', 'manual', 'imported')),
    source_comment_id        uuid,                    -- the callback note (no FK: legacy hard-deletes notes)
    created_by               uuid,                    -- NULL = system (imported)
    created_at               timestamptz NOT NULL DEFAULT now(),
    completed_by             uuid,
    completed_at             timestamptz,
    completed_late           boolean,                 -- Riyadh civil date of completion > civil due date
    outcome_comment_id       uuid,                    -- the outcome note written on completion
    no_further_action_reason text,
    next_follow_up_id        uuid,                    -- the follow-up opened by this completion
    closed_at                timestamptz,             -- completed / superseded / cancelled at
    closed_by                uuid,
    superseded_by            uuid,
    close_reason             text,
    reschedule_count         integer     NOT NULL DEFAULT 0,
    updated_at               timestamptz NOT NULL DEFAULT now(),
    revision                 integer     NOT NULL DEFAULT 0,
    CONSTRAINT follow_ups_closed_has_time CHECK (status = 'open' OR closed_at IS NOT NULL),
    CONSTRAINT follow_ups_open_not_closed CHECK (status <> 'open' OR (closed_at IS NULL AND completed_at IS NULL)),
    CONSTRAINT follow_ups_completion_complete CHECK (
        status <> 'completed'
        OR (completed_at IS NOT NULL AND completed_by IS NOT NULL AND outcome_comment_id IS NOT NULL
            AND (next_follow_up_id IS NOT NULL OR no_further_action_reason IS NOT NULL))),
    CONSTRAINT follow_ups_nfa_reason_not_blank CHECK (
        no_further_action_reason IS NULL OR btrim(no_further_action_reason) <> ''),
    CONSTRAINT follow_ups_note_has_comment CHECK (source <> 'note' OR source_comment_id IS NOT NULL)
);

COMMENT ON TABLE follow_ups IS
    'Phase 1 follow-ups (db/migrations/0010). At most one open row per case (ux_follow_ups_one_open). '
    'Opened by callback notes (trigger on "Comments"), by /v2 routes, or imported from the legacy next-calls rule (0011).';

-- the invariant: at most one open follow-up per case
CREATE UNIQUE INDEX IF NOT EXISTS ux_follow_ups_one_open ON follow_ups (case_id) WHERE status = 'open';
-- worklist buckets (late / today / upcoming) and per-owner lists
CREATE INDEX IF NOT EXISTS ix_follow_ups_open_due       ON follow_ups (due_at) WHERE status = 'open';
CREATE INDEX IF NOT EXISTS ix_follow_ups_open_owner_due ON follow_ups (owner_user_id, due_at) WHERE status = 'open';
-- case history
CREATE INDEX IF NOT EXISTS ix_follow_ups_case_created   ON follow_ups (case_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_follow_ups_source_comment ON follow_ups (source_comment_id) WHERE source_comment_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS follow_up_events (
    id                   bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    follow_up_id         uuid        NOT NULL REFERENCES follow_ups(id) ON DELETE CASCADE,
    case_id              uuid        NOT NULL,
    event                text        NOT NULL CHECK (event IN ('created', 'imported', 'superseded', 'rescheduled',
                                                               'completed', 'cancelled', 'owner_changed')),
    actor_user_id        uuid,
    occurred_at          timestamptz NOT NULL DEFAULT now(),
    due_at_before        timestamptz,
    due_at_after         timestamptz,
    was_late             boolean,     -- the follow-up was already late (Riyadh civil date) when this happened
    reason               text,
    comment_id           uuid,        -- source note (created) / outcome note (completed) / new note (superseded)
    related_follow_up_id uuid,        -- superseded_by / next follow-up
    source               text        NOT NULL DEFAULT 'api' CHECK (source IN ('api', 'trigger', 'migration')),
    request_id           text,
    details              jsonb
);

CREATE INDEX IF NOT EXISTS ix_follow_up_events_follow_up ON follow_up_events (follow_up_id, id);
CREATE INDEX IF NOT EXISTS ix_follow_up_events_case      ON follow_up_events (case_id, occurred_at DESC, id DESC);

-- ---------------------------------------------------------------------------------------
-- helpers
-- ---------------------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION worklist_riyadh_date(ts timestamptz) RETURNS date
LANGUAGE sql STABLE PARALLEL SAFE AS $$ SELECT (ts AT TIME ZONE 'Asia/Riyadh')::date $$;

COMMENT ON FUNCTION worklist_riyadh_date(timestamptz) IS 'Asia/Riyadh civil date of an instant (stream B).';

-- history is append-only; DELETE is allowed only so the ON DELETE CASCADE from a legacy
-- hard delete of the case (DELETE /clints) keeps working
CREATE OR REPLACE FUNCTION worklist_follow_up_events_no_update() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'follow_up_events is append-only (UPDATE rejected)'
        USING ERRCODE = 'insufficient_privilege';
END $$;

DROP TRIGGER IF EXISTS follow_up_events_no_update ON follow_up_events;
CREATE TRIGGER follow_up_events_no_update
    BEFORE UPDATE ON follow_up_events
    FOR EACH ROW EXECUTE FUNCTION worklist_follow_up_events_no_update();

-- revision column: same generic function as the legacy tables (0006)
DROP TRIGGER IF EXISTS follow_ups_bump_revision ON follow_ups;
CREATE TRIGGER follow_ups_bump_revision
    BEFORE UPDATE ON follow_ups
    FOR EACH ROW EXECUTE FUNCTION cawp_bump_revision('updated_at');

-- ---------------------------------------------------------------------------------------
-- legacy capture 1: a callback note opens the case's follow-up
-- ---------------------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION worklist_note_follow_up() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_owner uuid;
    v_new   uuid := gen_random_uuid();
BEGIN
    IF coalesce(current_setting('cawp.skip_note_follow_up', true), '') = 'on' THEN
        RETURN NULL;
    END IF;

    -- serialise concurrent notes on the same case (see header); also gives the owner
    SELECT c.user_id INTO v_owner FROM clints c WHERE c.id = NEW.clint_id FOR NO KEY UPDATE;
    IF NOT FOUND THEN
        RETURN NULL;
    END IF;

    WITH s AS (
        UPDATE follow_ups f
           SET status = 'superseded', closed_at = now(), closed_by = NEW.user_id,
               superseded_by = v_new, close_reason = 'superseded by a newer callback note',
               updated_at = now()
         WHERE f.case_id = NEW.clint_id AND f.status = 'open'
        RETURNING f.id, f.due_at
    )
    INSERT INTO follow_up_events (follow_up_id, case_id, event, actor_user_id, due_at_before, due_at_after,
                                  was_late, comment_id, related_follow_up_id, source)
    SELECT s.id, NEW.clint_id, 'superseded', NEW.user_id, s.due_at, NEW.next_contact_date,
           worklist_riyadh_date(s.due_at) < worklist_riyadh_date(now()), NEW.id, v_new, 'trigger'
      FROM s;

    INSERT INTO follow_ups (id, case_id, owner_user_id, due_at, original_due_at, status, source,
                            source_comment_id, created_by)
    VALUES (v_new, NEW.clint_id, v_owner, NEW.next_contact_date, NEW.next_contact_date, 'open', 'note',
            NEW.id, NEW.user_id);

    INSERT INTO follow_up_events (follow_up_id, case_id, event, actor_user_id, due_at_after, comment_id, source)
    VALUES (v_new, NEW.clint_id, 'created', NEW.user_id, NEW.next_contact_date, NEW.id, 'trigger');

    RETURN NULL;
END $$;

COMMENT ON FUNCTION worklist_note_follow_up() IS
    'AFTER INSERT ON "Comments" (callback notes only): supersede the open follow-up, open a new one (source note).';

DROP TRIGGER IF EXISTS comments_follow_up_trg ON "Comments";
CREATE TRIGGER comments_follow_up_trg
    AFTER INSERT ON "Comments"
    FOR EACH ROW
    WHEN (NEW.next_contact_date IS NOT NULL AND NEW.is_deleted IS NOT TRUE)
    EXECUTE FUNCTION worklist_note_follow_up();

-- ---------------------------------------------------------------------------------------
-- legacy capture 2: the open follow-up follows the case owner
-- ---------------------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION worklist_case_owner_changed() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    WITH u AS (
        UPDATE follow_ups f
           SET owner_user_id = NEW.user_id, updated_at = now()
         WHERE f.case_id = NEW.id AND f.status = 'open'
        RETURNING f.id
    )
    INSERT INTO follow_up_events (follow_up_id, case_id, event, actor_user_id, source, details)
    SELECT u.id, NEW.id, 'owner_changed', NULL, 'trigger',
           jsonb_build_object('before', OLD.user_id, 'after', NEW.user_id)
      FROM u;
    RETURN NULL;
END $$;

DROP TRIGGER IF EXISTS clints_follow_up_owner_trg ON clints;
CREATE TRIGGER clints_follow_up_owner_trg
    AFTER UPDATE OF user_id ON clints
    FOR EACH ROW
    WHEN (OLD.user_id IS DISTINCT FROM NEW.user_id)
    EXECUTE FUNCTION worklist_case_owner_changed();
