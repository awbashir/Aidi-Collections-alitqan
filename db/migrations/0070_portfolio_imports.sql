-- 0070 portfolio upload ("رفع المحافظ", customer request 2026-10-14; services/portfolio_import.py,
-- api/portfolio_import.py).
--
-- portfolio_imports       one row per uploaded portfolio file. `upload_id` is UNIQUE: one upload
--                         can create at most one portfolio, and a failed import is retried on
--                         the SAME row (never a second portfolio). The portfolio and all its
--                         cases are written in ONE transaction; `portfolio_id` is set only when
--                         that transaction commits. A failure rolls everything back and marks
--                         the row failed in a separate transaction.
--                         status: queued -> running -> succeeded | failed (failed -> queued on retry).
-- portfolio_import_rows   the import report: one row per source row (sheet row number) with its
--                         outcome: created | created_from_archive | skipped_active_exists |
--                         rejected, a stable code, an Arabic message and warnings (e.g. an
--                         unknown collector username). Written in the import transaction.
-- case_lineage            new case -> the archived case it replaces (customer decision: link and
--                         show history, never copy it). The archived case keeps all its notes,
--                         payments, promises and follow-ups; because "accountNumber" is globally
--                         unique its account number is renamed to `<A>-أرشيف<n>` (`renamed_to`)
--                         and the new case keeps `<A>` (`previous_account_number`). Nothing
--                         about money moves: the previous case's payments never reduce the new
--                         case's balance. previous_case_id has no FK (legacy hard-deletes
--                         portfolios with their cases; the link must not block that).
--
-- New tables only: instant, no lock on existing tables (the FKs take a brief SHARE ROW
-- EXCLUSIVE lock on clints / portfolios / contracts / bulk_uploads while being created).

CREATE TABLE IF NOT EXISTS portfolio_imports (
    id              uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    upload_id       uuid        NOT NULL UNIQUE REFERENCES bulk_uploads(id),
    sheet           text,
    contract_id     uuid        NOT NULL REFERENCES contracts(id),
    portfolio_name  text        NOT NULL CHECK (btrim(portfolio_name) <> '' AND length(portfolio_name) <= 200),
    portfolio_id    uuid        REFERENCES portfolios(id) ON DELETE SET NULL,
    mapping         jsonb       NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(mapping) = 'object'),
    status          text        NOT NULL DEFAULT 'queued'
                    CHECK (status IN ('queued', 'running', 'succeeded', 'failed')),
    counts          jsonb       NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(counts) = 'object'),
    error_code      text,
    error_message   text,
    attempts        integer     NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    created_by      uuid        NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    started_at      timestamptz,
    finished_at     timestamptz,
    CONSTRAINT portfolio_imports_portfolio_only_on_success
        CHECK (portfolio_id IS NULL OR status = 'succeeded')
);

COMMENT ON TABLE portfolio_imports IS
    'Portfolio file imports (0070). upload_id UNIQUE = one portfolio per upload; a failed import is '
    'retried on the same row. portfolio_id is set only by the committing transaction.';

CREATE UNIQUE INDEX IF NOT EXISTS ux_portfolio_imports_portfolio
    ON portfolio_imports (portfolio_id) WHERE portfolio_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_portfolio_imports_creator
    ON portfolio_imports (created_by, created_at DESC);

CREATE TABLE IF NOT EXISTS portfolio_import_rows (
    import_id         uuid    NOT NULL REFERENCES portfolio_imports(id) ON DELETE CASCADE,
    row_number        integer NOT NULL CHECK (row_number >= 1),
    account_number    text,
    outcome           text    NOT NULL
                      CHECK (outcome IN ('created', 'created_from_archive', 'skipped_active_exists', 'rejected')),
    code              text    NOT NULL,
    message           text,
    case_id           uuid,          -- the new case (created / created_from_archive) or the existing one (skipped)
    previous_case_id  uuid,          -- created_from_archive: the archived case now renamed
    warnings          jsonb   NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(warnings) = 'array'),
    PRIMARY KEY (import_id, row_number)
);

COMMENT ON TABLE portfolio_import_rows IS
    'Per source row outcome of a portfolio import (the downloadable report). No FK to clints: '
    'the report outlives a later portfolio delete.';

CREATE INDEX IF NOT EXISTS ix_portfolio_import_rows_outcome
    ON portfolio_import_rows (import_id, outcome, row_number);

CREATE TABLE IF NOT EXISTS case_lineage (
    case_id                  uuid        PRIMARY KEY REFERENCES clints(id) ON DELETE CASCADE,
    previous_case_id         uuid        NOT NULL,
    previous_account_number  text        NOT NULL,
    renamed_to               text        NOT NULL,
    import_id                uuid        REFERENCES portfolio_imports(id) ON DELETE SET NULL,
    created_by               uuid,
    created_at               timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT case_lineage_not_self CHECK (case_id <> previous_case_id)
);

COMMENT ON TABLE case_lineage IS
    'New case -> the archived case it replaced on a portfolio re-import (0070). History is linked and '
    'shown read-only, never copied; previous money never counts on the new case.';

-- one archived case is replaced at most once (after the rename the next import finds the new case)
CREATE UNIQUE INDEX IF NOT EXISTS ux_case_lineage_previous ON case_lineage (previous_case_id);
CREATE INDEX IF NOT EXISTS ix_case_lineage_import ON case_lineage (import_id) WHERE import_id IS NOT NULL;
