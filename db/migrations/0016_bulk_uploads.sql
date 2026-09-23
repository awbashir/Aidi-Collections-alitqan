-- 0016 bulk_uploads (Phase 2, stream F): private spreadsheet uploads for reviewed bulk jobs.
--
-- One row per uploaded file. The bytes live under <UPLOAD_DIR>/private/bulk/uploads/
-- (BULK_STORAGE_DIR), never under a public URL; `stored_path` is relative to that
-- directory. The row records what the inspection found (core/bulk/inspect.py):
--
--   detected_type   'xlsx' (a real zip/OOXML workbook) or 'csv' (UTF-8 text). Nothing else
--                   is accepted, whatever the file name says.
--   sheets          [{name, header_row, headers[], rows, columns, formula_cells, sample[]}]
--   formula_cells   formula cells in the whole file. They are counted, never evaluated;
--                   a formula in a mapped column makes that source row an error.
--   scan_status     'not_scanned': there is no antivirus yet and the file is never claimed
--                   to be clean. The other values exist for the future scanner.
--   expires_at      uploaded_at + BULK_UPLOAD_TTL_DAYS (default 7). The retention task in
--                   the worker deletes the file and sets purged_at; the row stays.
--
-- Locks: new table only. Instant.

CREATE TABLE IF NOT EXISTS bulk_uploads (
    id             uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    purpose        text        NOT NULL CHECK (btrim(purpose) <> ''),
    original_name  text        NOT NULL,
    stored_path    text        NOT NULL,
    size_bytes     bigint      NOT NULL CHECK (size_bytes >= 0),
    sha256         text        NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    detected_type  text        NOT NULL CHECK (detected_type IN ('xlsx', 'csv')),
    encoding       text,
    delimiter      text,
    sheets         jsonb       NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(sheets) = 'array'),
    row_count      integer     NOT NULL DEFAULT 0 CHECK (row_count >= 0),
    header_row     integer     NOT NULL DEFAULT 1 CHECK (header_row >= 1),
    formula_cells  integer     NOT NULL DEFAULT 0 CHECK (formula_cells >= 0),
    scan_status    text        NOT NULL DEFAULT 'not_scanned'
                   CHECK (scan_status IN ('not_scanned', 'clean', 'infected', 'error')),
    uploaded_by    uuid        NOT NULL,
    uploaded_at    timestamptz NOT NULL DEFAULT now(),
    expires_at     timestamptz NOT NULL,
    purged_at      timestamptz,
    CONSTRAINT bulk_uploads_expiry_after_upload CHECK (expires_at > uploaded_at)
);

COMMENT ON TABLE bulk_uploads IS
    'Private spreadsheet uploads for reviewed bulk jobs (Phase 2). The file is stored under '
    'BULK_STORAGE_DIR/uploads; purged_at is set when the retention task deleted it. '
    'scan_status stays not_scanned until an antivirus exists (never claimed clean).';
COMMENT ON COLUMN bulk_uploads.stored_path IS 'path relative to BULK_STORAGE_DIR (never served directly)';
COMMENT ON COLUMN bulk_uploads.formula_cells IS 'formula cells counted at inspection; never evaluated';

CREATE INDEX IF NOT EXISTS ix_bulk_uploads_uploader
    ON bulk_uploads (uploaded_by, uploaded_at DESC);
CREATE INDEX IF NOT EXISTS ix_bulk_uploads_expiry
    ON bulk_uploads (expires_at) WHERE purged_at IS NULL;
