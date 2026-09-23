-- 0005 idempotency_keys: stored responses for the Idempotency-Key header (core/idempotency.py).
--
-- One row per (user, key). The first request reserves the key (state 'in_progress'); a
-- 2xx response is stored in the same transaction as the route's own writes
-- (state 'completed') and replayed for a retry with the same key and the same request
-- hash. A different request with the same key is rejected. Rows expire (expires_at) and
-- are purged lazily by the decorator and by core.idempotency.purge_expired().
--
-- user_id is text (the JWT identity string) and has no FK: keys are short-lived and must
-- not block the legacy DELETE /users.
--
-- Locks: new table + indexes on the empty table. No lock on any existing table. Instant.

CREATE TABLE IF NOT EXISTS idempotency_keys (
    id                bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id           text        NOT NULL,
    key               text        NOT NULL CHECK (length(key) BETWEEN 1 AND 255),
    method            text        NOT NULL,
    route             text        NOT NULL,
    request_hash      text        NOT NULL,
    state             text        NOT NULL DEFAULT 'in_progress'
                      CHECK (state IN ('in_progress', 'completed')),
    response_status   integer,
    response_body     text,
    response_content_type text,
    created_at        timestamptz NOT NULL DEFAULT now(),
    completed_at      timestamptz,
    expires_at        timestamptz NOT NULL,
    CONSTRAINT uq_idempotency_keys_user_key UNIQUE (user_id, key),
    CONSTRAINT ck_idempotency_keys_completed CHECK (
        state = 'in_progress' OR (response_status IS NOT NULL AND completed_at IS NOT NULL))
);

COMMENT ON TABLE idempotency_keys IS
    'Idempotency-Key reservations and stored 2xx responses for new mutating routes (core/idempotency.py).';

CREATE INDEX IF NOT EXISTS ix_idempotency_keys_expires ON idempotency_keys (expires_at);
