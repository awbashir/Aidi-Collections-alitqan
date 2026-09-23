-- 0036 server-side session revocation (Phase 3, stream C).
--
--   users.sessions_valid_after   timestamptz, NULL = never revoked. NOT mapped in models.py
--                                (legacy serializers iterate mapped columns only, so no
--                                legacy response changes). A JWT whose issue time is
--                                earlier is rejected by the blocklist loader
--                                (services/sessions.py, wired in app.py).
--   users_sessions_revoke        BEFORE UPDATE on users. Moves sessions_valid_after to
--                                clock_timestamp() (never backwards) when the account is
--                                disabled (is_active true -> false), its role changes, or its
--                                password hash changes. This covers every writer: the legacy
--                                PUT /uesrs/<id> and /user/inactive/<id>, v2, and plain SQL.
--                                An explicit move of sessions_valid_after (v2 "sign out
--                                everywhere") is recorded too.
--                                Each revocation appends one audit_events row
--                                ('user.sessions.revoke', entity 'user') with the reasons;
--                                actor/reason/source come from cawp_admin_ctx() (0034) when
--                                the writer set them, else actor NULL and source 'db_trigger'.
--                                Re-activating an account does NOT revive old tokens.
--   users_sessions_deleted       AFTER DELETE on users: audit row (a deleted user's tokens are
--                                rejected because the row is gone).
--   revoked_tokens               one row per token ended by POST /logout (jti, user, expiry).
--                                The Redis key jwt:block:<jti> is still written, but Redis runs
--                                without persistence, so the database row is what makes a
--                                logout survive a Redis restart or outage. Rows past their
--                                expiry are useless and are purged by the logout path.
--
-- Locks: ALTER TABLE users ADD COLUMN (nullable, no default) is metadata-only but takes a
-- brief ACCESS EXCLUSIVE lock on users (~300 rows; lock_timeout 5s from the runner).
-- CREATE TRIGGER takes SHARE ROW EXCLUSIVE for milliseconds. No backfill.

ALTER TABLE users ADD COLUMN IF NOT EXISTS sessions_valid_after timestamptz;

COMMENT ON COLUMN users.sessions_valid_after IS
    'Tokens issued before this instant are rejected (server-side sign-out). NULL = never revoked. '
    'Unmapped in models.py on purpose.';

CREATE OR REPLACE FUNCTION cawp_sessions_revoke() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    reasons text[] := '{}';
    explicit boolean := NEW.sessions_valid_after IS DISTINCT FROM OLD.sessions_valid_after;
BEGIN
    IF coalesce(OLD.is_active, true) AND NOT coalesce(NEW.is_active, true) THEN
        reasons := reasons || 'disabled'::text;
    END IF;
    IF NEW.role_id IS DISTINCT FROM OLD.role_id THEN
        reasons := reasons || 'role_changed'::text;
    END IF;
    IF NEW.password IS DISTINCT FROM OLD.password THEN
        reasons := reasons || 'password_changed'::text;
    END IF;
    IF explicit THEN
        reasons := reasons || coalesce(cawp_admin_ctx('revoke_reason'), 'sign_out');
    END IF;
    IF cardinality(reasons) = 0 THEN
        RETURN NEW;
    END IF;

    -- never move backwards (an explicit value in the past is raised to now)
    NEW.sessions_valid_after := greatest(coalesce(OLD.sessions_valid_after, '-infinity'::timestamptz),
                                         coalesce(NEW.sessions_valid_after, '-infinity'::timestamptz),
                                         clock_timestamp());

    INSERT INTO audit_events (actor_user_id, action, entity_type, entity_id, before, after,
                              reason, request_id, idempotency_key, source)
    VALUES (cawp_admin_ctx('actor')::uuid, 'user.sessions.revoke', 'user', NEW.id::text,
            jsonb_build_object('sessions_valid_after', OLD.sessions_valid_after,
                               'is_active', OLD.is_active, 'role_id', OLD.role_id),
            jsonb_build_object('sessions_valid_after', NEW.sessions_valid_after,
                               'is_active', NEW.is_active, 'role_id', NEW.role_id,
                               'reasons', to_jsonb(reasons)),
            coalesce(cawp_admin_ctx('reason'), array_to_string(reasons, ',')),
            cawp_admin_ctx('request_id'), cawp_admin_ctx('idempotency_key'),
            coalesce(cawp_admin_ctx('source'), 'db_trigger'));
    RETURN NEW;
END $$;

COMMENT ON FUNCTION cawp_sessions_revoke() IS
    'BEFORE UPDATE on users: disable / role change / password change / explicit sign-out move '
    'sessions_valid_after forward and append an audit_events row.';

DROP TRIGGER IF EXISTS users_sessions_revoke ON users;
CREATE TRIGGER users_sessions_revoke
    BEFORE UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION cawp_sessions_revoke();

CREATE OR REPLACE FUNCTION cawp_sessions_deleted() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO audit_events (actor_user_id, action, entity_type, entity_id, before, after,
                              reason, request_id, idempotency_key, source)
    VALUES (cawp_admin_ctx('actor')::uuid, 'user.sessions.revoke', 'user', OLD.id::text,
            jsonb_build_object('sessions_valid_after', OLD.sessions_valid_after,
                               'is_active', OLD.is_active, 'role_id', OLD.role_id),
            jsonb_build_object('reasons', jsonb_build_array('deleted')),
            coalesce(cawp_admin_ctx('reason'), 'deleted'),
            cawp_admin_ctx('request_id'), cawp_admin_ctx('idempotency_key'),
            coalesce(cawp_admin_ctx('source'), 'db_trigger'));
    RETURN NULL;
END $$;

DROP TRIGGER IF EXISTS users_sessions_deleted ON users;
CREATE TRIGGER users_sessions_deleted
    AFTER DELETE ON users
    FOR EACH ROW EXECUTE FUNCTION cawp_sessions_deleted();

-- ------------------------------------------------------------------ revoked_tokens
CREATE TABLE IF NOT EXISTS revoked_tokens (
    jti         text        PRIMARY KEY,
    user_id     uuid        NOT NULL,
    expires_at  timestamptz NOT NULL,
    revoked_at  timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE revoked_tokens IS
    'Tokens ended by the user''s own logout (per token). Checked with the user''s session state '
    '(services/sessions.py); rows past expires_at can be deleted at any time.';

CREATE INDEX IF NOT EXISTS ix_revoked_tokens_user ON revoked_tokens (user_id, expires_at);
