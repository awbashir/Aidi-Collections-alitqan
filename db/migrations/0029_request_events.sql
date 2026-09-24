-- 0029 request trail (Phase 3, stream A): request_events and request_attachments.
--
--   request_events       immutable event trail of a request: every transition and every
--                        comment, with the actor and the time. UPDATE, DELETE and TRUNCATE
--                        are rejected for every role (like audit_events, 0004).
--                        event        created | commented | taken | resolved | reopened | cancelled
--                        reason       required for resolved / reopened / cancelled
--                        body         the comment text (commented)
--                        status_before / status_after   the transition (equal for a comment)
--   request_attachments  references to existing case documents ("clint_documents" rows of
--                        the same case), added on create or with a comment. No new file
--                        storage. No FK to clint_documents (a case hard delete cascades its
--                        documents; the reference stays and is shown as missing). Append-only.
--
-- The services write the event in the same transaction as the row change, and also an
-- audit_events row (request.create / .comment / .take / .resolve / .reopen / .cancel).
--
-- Locks: new tables only. Instant.

CREATE TABLE IF NOT EXISTS request_events (
    id               bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    request_id       uuid        NOT NULL REFERENCES requests(id),
    case_id          uuid        NOT NULL,
    event            text        NOT NULL
                     CHECK (event IN ('created', 'commented', 'taken', 'resolved', 'reopened', 'cancelled')),
    actor_user_id    uuid        NOT NULL,
    occurred_at      timestamptz NOT NULL DEFAULT now(),
    status_before    text        CHECK (status_before IN ('open', 'in_review', 'resolved', 'cancelled')),
    status_after     text        NOT NULL CHECK (status_after IN ('open', 'in_review', 'resolved', 'cancelled')),
    reason           text,
    body             text,
    details          jsonb,
    source           text        NOT NULL DEFAULT 'api' CHECK (source IN ('api', 'migration')),
    request_ref      text,       -- X-Request-ID of the HTTP call
    idempotency_key  text,
    CONSTRAINT request_events_reason_required CHECK (
        event NOT IN ('resolved', 'reopened', 'cancelled') OR (reason IS NOT NULL AND btrim(reason) <> '')),
    CONSTRAINT request_events_comment_body CHECK (
        event <> 'commented' OR (body IS NOT NULL AND btrim(body) <> '' AND length(body) <= 5000)),
    CONSTRAINT request_events_transition_shape CHECK (
        CASE event
            WHEN 'created'   THEN status_before IS NULL AND status_after = 'open'
            WHEN 'commented' THEN status_before = status_after
            WHEN 'taken'     THEN status_before = 'open' AND status_after = 'in_review'
            WHEN 'resolved'  THEN status_before = 'in_review' AND status_after = 'resolved'
            WHEN 'reopened'  THEN status_before = 'resolved' AND status_after = 'in_review'
            WHEN 'cancelled' THEN status_before = 'open' AND status_after = 'cancelled'
        END)
);

COMMENT ON TABLE request_events IS
    'Append-only trail of requests (db/migrations/0029): transitions and comments with actor and time. '
    'UPDATE/DELETE/TRUNCATE rejected.';

CREATE TABLE IF NOT EXISTS request_attachments (
    id           bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    request_id   uuid        NOT NULL REFERENCES requests(id),
    document_id  uuid        NOT NULL,            -- "clint_documents".id of the same case (checked by the service)
    event_id     bigint      NOT NULL REFERENCES request_events(id),
    added_by     uuid        NOT NULL,
    added_at     timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT request_attachments_once UNIQUE (request_id, document_id)
);

COMMENT ON TABLE request_attachments IS
    'References from a request to existing case documents (clint_documents), added on create or with a '
    'comment (event_id). No file storage, no FK to clint_documents. Append-only.';

CREATE OR REPLACE FUNCTION requests_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% is append-only: % is not allowed', TG_TABLE_NAME, TG_OP
        USING ERRCODE = 'insufficient_privilege',
              HINT = 'Add a new event instead.';
END $$;

DROP TRIGGER IF EXISTS request_events_no_update_delete ON request_events;
CREATE TRIGGER request_events_no_update_delete
    BEFORE UPDATE OR DELETE ON request_events
    FOR EACH ROW EXECUTE FUNCTION requests_append_only();

DROP TRIGGER IF EXISTS request_events_no_truncate ON request_events;
CREATE TRIGGER request_events_no_truncate
    BEFORE TRUNCATE ON request_events
    FOR EACH STATEMENT EXECUTE FUNCTION requests_append_only();

DROP TRIGGER IF EXISTS request_attachments_no_update_delete ON request_attachments;
CREATE TRIGGER request_attachments_no_update_delete
    BEFORE UPDATE OR DELETE ON request_attachments
    FOR EACH ROW EXECUTE FUNCTION requests_append_only();

DROP TRIGGER IF EXISTS request_attachments_no_truncate ON request_attachments;
CREATE TRIGGER request_attachments_no_truncate
    BEFORE TRUNCATE ON request_attachments
    FOR EACH STATEMENT EXECUTE FUNCTION requests_append_only();
