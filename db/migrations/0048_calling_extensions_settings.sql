-- 0048 human calling foundation (Phase 5 P0): extension ownership, calling preferences and
-- the deployment calling-window policy. docs/phase5-spec.md, review/phase5-foundation.md.
--
-- user_extensions      ownership history of PBX extensions. One ACTIVE owner per extension
--                      and one ACTIVE extension per user (partial unique indexes). A row is
--                      only ever released (once, with a reason); it is never edited or deleted.
--                      Decision: this table is the source of truth for calling. The legacy
--                      `users.extension` column is a MIRROR that the v2 assign/release routes
--                      keep in sync (CDR attribution and legacy screens still read it). Legacy
--                      values are never imported silently: GET /v2/calling/extensions/legacy-drift
--                      lists the differences and an admin adopts them one by one, with a reason.
--   calling_preferences per-user preferences: auto-answer for attempt-tagged calls, device ids.
--   calling_window_policy / _history
--                      the deployment calling windows (Asia/Riyadh days + hours) and optional
--                      holidays, as one reviewed JSON policy (services/calling/windows.py
--                      validates it). No row = never configured = `calling_windows_missing`.
--
-- No FK to users (legacy hard-deletes users), like requests/case_corrections.
-- Locks: new tables only. Instant.

CREATE OR REPLACE FUNCTION calling_reject_change() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% is append-only (% rejected)', TG_TABLE_NAME, TG_OP USING ERRCODE = 'CL001';
END $$;

COMMENT ON FUNCTION calling_reject_change() IS
    'Phase 5 calling: BEFORE UPDATE/DELETE/TRUNCATE guard for append-only evidence and history tables.';

-- --------------------------------------------------------------------------- ownership
CREATE TABLE IF NOT EXISTS user_extensions (
    id              uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    extension       text        NOT NULL CHECK (extension ~ '^[0-9]{2,6}$'),
    user_id         uuid        NOT NULL,
    assigned_at     timestamptz NOT NULL DEFAULT now(),
    assigned_by     uuid,
    assign_reason   text        NOT NULL CHECK (btrim(assign_reason) <> '' AND length(assign_reason) <= 1000),
    source          text        NOT NULL DEFAULT 'admin' CHECK (source IN ('admin', 'legacy_adopt')),
    released_at     timestamptz,
    released_by     uuid,
    release_reason  text,
    CONSTRAINT user_extensions_release_fields CHECK (
        (released_at IS NULL AND released_by IS NULL AND release_reason IS NULL)
        OR (released_at IS NOT NULL AND released_at >= assigned_at
            AND release_reason IS NOT NULL AND btrim(release_reason) <> '' AND length(release_reason) <= 1000))
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_user_extensions_active_extension
    ON user_extensions (extension) WHERE released_at IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS ux_user_extensions_active_user
    ON user_extensions (user_id) WHERE released_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_user_extensions_extension_history
    ON user_extensions (extension, assigned_at DESC);
CREATE INDEX IF NOT EXISTS ix_user_extensions_user_history
    ON user_extensions (user_id, assigned_at DESC);

CREATE OR REPLACE FUNCTION calling_user_extensions_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'user_extensions rows are never deleted' USING ERRCODE = 'CL001';
    END IF;
    IF OLD.released_at IS NOT NULL THEN
        RAISE EXCEPTION 'a released extension assignment is immutable' USING ERRCODE = 'CL001';
    END IF;
    IF (NEW.id, NEW.extension, NEW.user_id, NEW.assigned_at, NEW.assigned_by, NEW.assign_reason, NEW.source)
       IS DISTINCT FROM
       (OLD.id, OLD.extension, OLD.user_id, OLD.assigned_at, OLD.assigned_by, OLD.assign_reason, OLD.source) THEN
        RAISE EXCEPTION 'only the release of an extension assignment can be recorded' USING ERRCODE = 'CL001';
    END IF;
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS user_extensions_guard ON user_extensions;
CREATE TRIGGER user_extensions_guard
    BEFORE UPDATE OR DELETE ON user_extensions
    FOR EACH ROW EXECUTE FUNCTION calling_user_extensions_guard();

COMMENT ON TABLE user_extensions IS
    'Phase 5: PBX extension ownership history; source of truth for calling (users.extension is a mirror).';

-- --------------------------------------------------------------------------- preferences
CREATE TABLE IF NOT EXISTS calling_preferences (
    user_id               uuid        PRIMARY KEY,
    auto_answer_attempts  boolean     NOT NULL DEFAULT true,
    devices               jsonb       NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(devices) = 'object'),
    revision              integer     NOT NULL DEFAULT 0,
    created_at            timestamptz NOT NULL DEFAULT now(),
    updated_at            timestamptz NOT NULL DEFAULT now(),
    updated_by            uuid
);

DROP TRIGGER IF EXISTS calling_preferences_bump_revision ON calling_preferences;
CREATE TRIGGER calling_preferences_bump_revision
    BEFORE UPDATE ON calling_preferences
    FOR EACH ROW EXECUTE FUNCTION cawp_bump_revision('updated_at', 'updated_by');

COMMENT ON COLUMN calling_preferences.auto_answer_attempts IS
    'Softphone auto-answers ONLY calls tagged with our attempt id (click-to-call/campaign). Other calls ring.';
COMMENT ON COLUMN calling_preferences.devices IS
    'Browser device ids: {"microphone": str, "speaker": str, "ringer": str}; no SIP secrets ever.';

-- --------------------------------------------------------------------------- calling windows
CREATE TABLE IF NOT EXISTS calling_window_policy (
    id          smallint    PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    windows     jsonb       NOT NULL CHECK (jsonb_typeof(windows) = 'array'),
    holidays    jsonb       NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(holidays) = 'array'),
    revision    integer     NOT NULL DEFAULT 1 CHECK (revision >= 1),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    updated_by  uuid,
    reason      text        NOT NULL CHECK (btrim(reason) <> '' AND length(reason) <= 1000)
);

CREATE TABLE IF NOT EXISTS calling_window_policy_history (
    id          bigserial   PRIMARY KEY,
    revision    integer     NOT NULL UNIQUE,
    windows     jsonb       NOT NULL,
    holidays    jsonb       NOT NULL,
    changed_at  timestamptz NOT NULL DEFAULT now(),
    changed_by  uuid,
    reason      text        NOT NULL
);

DROP TRIGGER IF EXISTS calling_window_policy_history_append_only ON calling_window_policy_history;
CREATE TRIGGER calling_window_policy_history_append_only
    BEFORE UPDATE OR DELETE ON calling_window_policy_history
    FOR EACH ROW EXECUTE FUNCTION calling_reject_change();

COMMENT ON TABLE calling_window_policy IS
    'Phase 5: deployment calling windows (Asia/Riyadh). Restricts new origination only; never ends a call.';
