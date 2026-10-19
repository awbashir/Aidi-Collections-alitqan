-- 0074 previous statuses import ("استيراد الحالات السابقة", customer request 2026-10-19;
-- services/previous_status_import.py, api/previous_status_import.py).
--
-- The customer sent one spreadsheet per portfolio with each account's main and sub status from
-- the old system. An admin uploads it, previews the matching (account -> case, status names ->
-- live general/contract statuses) and applies it: every ready row becomes ONE ordinary legacy
-- "Comments" note on the case (comment_status_id = main, comment_sub_status_id = sub, a fixed
-- marker text, create_at = a date the admin picks before today). No new business column.
--
-- previous_status_imports      one row per uploaded file. `upload_id` is UNIQUE: a failed import
--                              is retried on the SAME row and a succeeded one never runs again.
--                              All notes (and any sub-statuses created) are written in ONE
--                              transaction; a failure rolls everything back and marks the row
--                              failed in a separate transaction.
--                              status: queued -> running -> succeeded | failed (failed -> queued).
--                              `options`: noted_at, contact_method_id, note, create_missing_sub_statuses,
--                              include_archived. `created_sub_statuses`: [{id, name, parent_id, parent_name}].
-- previous_status_import_rows  the report: one row per source row with its outcome (imported |
--                              skipped), a stable code, an Arabic message, the case and the note.
--
-- Re-running another upload of the same file is safe on its own: a case that already has an
-- imported note with the same main + sub status (same marker text) is skipped (`already_imported`).
--
-- New tables only: instant, no lock on existing tables (the FK takes a brief SHARE ROW EXCLUSIVE
-- lock on bulk_uploads while being created).

CREATE TABLE IF NOT EXISTS previous_status_imports (
    id                    uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    upload_id             uuid        NOT NULL UNIQUE REFERENCES bulk_uploads(id),
    sheet                 text,
    mapping               jsonb       NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(mapping) = 'object'),
    options               jsonb       NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(options) = 'object'),
    status                text        NOT NULL DEFAULT 'queued'
                          CHECK (status IN ('queued', 'running', 'succeeded', 'failed')),
    counts                jsonb       NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(counts) = 'object'),
    created_sub_statuses  jsonb       NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(created_sub_statuses) = 'array'),
    error_code            text,
    error_message         text,
    attempts              integer     NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    created_by            uuid        NOT NULL,
    created_at            timestamptz NOT NULL DEFAULT now(),
    updated_at            timestamptz NOT NULL DEFAULT now(),
    started_at            timestamptz,
    finished_at           timestamptz
);

COMMENT ON TABLE previous_status_imports IS
    'Previous statuses imports (0074): one per upload (upload_id UNIQUE); a failed import is retried on '
    'the same row. The notes are written in one transaction.';

CREATE INDEX IF NOT EXISTS ix_previous_status_imports_creator
    ON previous_status_imports (created_by, created_at DESC);

CREATE TABLE IF NOT EXISTS previous_status_import_rows (
    import_id       uuid    NOT NULL REFERENCES previous_status_imports(id) ON DELETE CASCADE,
    row_number      integer NOT NULL CHECK (row_number >= 1),
    account_number  text,
    main_status     text,
    sub_status      text,
    outcome         text    NOT NULL CHECK (outcome IN ('imported', 'skipped')),
    code            text    NOT NULL,
    message         text,
    case_id         uuid,
    comment_id      uuid,
    PRIMARY KEY (import_id, row_number)
);

COMMENT ON TABLE previous_status_import_rows IS
    'Per source row outcome of a previous statuses import (the downloadable report). No FK to clints '
    'or "Comments": the report outlives later deletes. comment_id = the note written for the row.';

CREATE INDEX IF NOT EXISTS ix_previous_status_import_rows_outcome
    ON previous_status_import_rows (import_id, outcome, row_number);
