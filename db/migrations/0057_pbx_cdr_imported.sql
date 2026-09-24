-- 0057 PBX CDR evidence imported from a reviewed spreadsheet (Phase 5 P4).
-- docs/phase5-spec.md (stream P4), review/phase5-evidence.md.
--
-- pbx_cdr_imported   one row per Issabel CDR record that a reviewed bulk job of kind
--                    `pbx_cdr_import` (services/bulk_pbx_cdr.py) imported after an explicit
--                    confirmation. It is EVIDENCE next to the live PBX CDR (MySQL
--                    asteriskcdrdb.cdr, read through GET /cdr*): it never changes a call
--                    attempt, a call leg or a case. Rows are immutable; only the retention
--                    task (services/calling_cdr_retention.py) may delete them, and only when
--                    retention is configured (default: keep everything).
--
--   source identity  source_name (the PBX the export came from, a job parameter) +
--                    source_key = sha256(uniqueid | call instant | src | dst). The same PBX
--                    record imported twice (same file again, an overlapping export, two jobs at
--                    once) is refused by UNIQUE (source_name, source_key); `payload_hash` tells
--                    an identical duplicate from a conflicting one.
--   provenance       bulk_job_id + row_no (the sheet row), upload id, file name, file sha256,
--                    sheet, and the raw source row as read.
--   the record       the PBX wall-clock text as given (call_at_source) and the instant it was
--                    read as (call_at, with source_timezone); duration/billsec NULL = unknown,
--                    never 0.
--   attribution      at import time, in the importer's scope: `attempt_tag` (the exact
--                    cawp:<attempt> / cawp-<attempt> tag), `phone_time` (the number matched a
--                    case and one call attempt of that case to the same number within the
--                    time window), `phone` (the number matched exactly one case; no attempt).
--                    Unmatched and ambiguous records are not imported (the job's preview and
--                    error file keep them with their source row).
--
-- No FK to clints/users/call_attempts/call_legs (legacy hard deletes; evidence outlives them).
-- Locks: new table only. Instant.

CREATE OR REPLACE FUNCTION calling_evidence_retention_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    -- The retention task deletes old evidence inside a transaction that ran
    --   SET LOCAL cawp.retention_purge = '<table name>';
    -- Everything else (UPDATE always, DELETE without that setting, TRUNCATE) is refused.
    IF TG_OP = 'DELETE' AND current_setting('cawp.retention_purge', true) = TG_TABLE_NAME THEN
        RETURN OLD;
    END IF;
    RAISE EXCEPTION '% is append-only (% rejected)', TG_TABLE_NAME, TG_OP USING ERRCODE = 'CL001';
END $$;

COMMENT ON FUNCTION calling_evidence_retention_guard() IS
    'Phase 5 P4: append-only evidence guard; DELETE only for the retention task (SET LOCAL cawp.retention_purge = <table>).';

CREATE TABLE IF NOT EXISTS pbx_cdr_imported (
    id                uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    source_name       text        NOT NULL CHECK (source_name ~ '^[A-Za-z0-9][A-Za-z0-9 ._-]{0,99}$'),
    source_key        text        NOT NULL CHECK (source_key ~ '^[0-9a-f]{64}$'),
    payload_hash      text        NOT NULL CHECK (payload_hash ~ '^[0-9a-f]{64}$'),
    -- provenance
    bulk_job_id       uuid        NOT NULL REFERENCES bulk_jobs (id),
    row_no            integer     NOT NULL CHECK (row_no >= 1),
    upload_id         uuid        REFERENCES bulk_uploads (id),
    file_name         text        NOT NULL CHECK (length(file_name) <= 500),
    file_sha256       text        NOT NULL CHECK (file_sha256 ~ '^[0-9a-f]{64}$'),
    sheet             text,
    raw               jsonb       NOT NULL CHECK (jsonb_typeof(raw) = 'object'),
    -- the PBX record
    uniqueid          text        NOT NULL CHECK (length(uniqueid) BETWEEN 1 AND 150),
    linkedid          text        CHECK (linkedid IS NULL OR length(linkedid) <= 150),
    userfield         text        CHECK (userfield IS NULL OR length(userfield) <= 255),
    call_at           timestamptz NOT NULL,
    call_at_source    text        NOT NULL CHECK (length(call_at_source) <= 50),
    source_timezone   text        NOT NULL CHECK (length(source_timezone) <= 64),
    src               text        CHECK (src IS NULL OR length(src) <= 80),
    dst               text        CHECK (dst IS NULL OR length(dst) <= 80),
    direction         text        NOT NULL CHECK (direction IN ('outbound', 'inbound', 'internal', 'unknown')),
    external_number   text        CHECK (external_number IS NULL OR length(external_number) <= 80),
    agent_extension   text        CHECK (agent_extension IS NULL OR agent_extension ~ '^[0-9]{2,6}$'),
    channel           text        CHECK (channel IS NULL OR length(channel) <= 200),
    dstchannel        text        CHECK (dstchannel IS NULL OR length(dstchannel) <= 200),
    disposition       text        CHECK (disposition IS NULL OR length(disposition) <= 45),
    recordingfile     text        CHECK (recordingfile IS NULL OR length(recordingfile) <= 255),
    duration_seconds  integer     CHECK (duration_seconds IS NULL OR duration_seconds BETWEEN 0 AND 604800),
    billsec           integer     CHECK (billsec IS NULL OR billsec BETWEEN 0 AND 604800),
    -- attribution decided at import (in the importer's scope)
    case_id           uuid        NOT NULL,
    attribution       text        NOT NULL CHECK (attribution IN ('attempt_tag', 'phone_time', 'phone')),
    attempt_id        uuid,
    attempt_link      text        CHECK (attempt_link IS NULL OR attempt_link IN (
                                      'userfield', 'uniqueid', 'linkedid', 'uniqueid_prefix', 'phone_time')),
    leg_id            uuid,
    leg_role          text        CHECK (leg_role IS NULL OR leg_role IN ('agent', 'customer', 'unknown')),
    peer_leg_id       uuid,
    peer_leg_role     text        CHECK (peer_leg_role IS NULL OR peer_leg_role IN ('agent', 'customer', 'unknown')),
    agent_user_id     uuid,
    agent_basis       text        CHECK (agent_basis IS NULL OR agent_basis IN (
                                      'attempt', 'extension', 'ambiguous', 'unknown_extension', 'none')),
    matched_cases     integer     CHECK (matched_cases IS NULL OR matched_cases >= 0),
    details           jsonb       NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(details) = 'object'),
    imported_by       uuid        NOT NULL,
    imported_at       timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT pbx_cdr_imported_billsec_le_duration CHECK (
        billsec IS NULL OR duration_seconds IS NULL OR billsec <= duration_seconds),
    CONSTRAINT pbx_cdr_imported_attempt_shape CHECK (
        (attribution = 'phone') = (attempt_id IS NULL)
        AND (attempt_id IS NULL) = (attempt_link IS NULL)
        AND (attribution <> 'phone_time' OR attempt_link = 'phone_time')
        AND (attribution <> 'attempt_tag' OR attempt_link <> 'phone_time')),
    CONSTRAINT ux_pbx_cdr_imported_source UNIQUE (source_name, source_key),
    CONSTRAINT ux_pbx_cdr_imported_job_row UNIQUE (bulk_job_id, row_no)
);

CREATE INDEX IF NOT EXISTS ix_pbx_cdr_imported_case ON pbx_cdr_imported (case_id, call_at DESC, id);
CREATE INDEX IF NOT EXISTS ix_pbx_cdr_imported_attempt ON pbx_cdr_imported (attempt_id, call_at)
    WHERE attempt_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_pbx_cdr_imported_call_at ON pbx_cdr_imported (call_at);
CREATE INDEX IF NOT EXISTS ix_pbx_cdr_imported_uniqueid ON pbx_cdr_imported (uniqueid);

DROP TRIGGER IF EXISTS pbx_cdr_imported_append_only ON pbx_cdr_imported;
CREATE TRIGGER pbx_cdr_imported_append_only
    BEFORE UPDATE OR DELETE ON pbx_cdr_imported
    FOR EACH ROW EXECUTE FUNCTION calling_evidence_retention_guard();

DROP TRIGGER IF EXISTS pbx_cdr_imported_no_truncate ON pbx_cdr_imported;
CREATE TRIGGER pbx_cdr_imported_no_truncate
    BEFORE TRUNCATE ON pbx_cdr_imported
    FOR EACH STATEMENT EXECUTE FUNCTION calling_reject_change();

COMMENT ON TABLE pbx_cdr_imported IS
    'Phase 5 P4: PBX CDR records imported through a reviewed bulk job; immutable evidence, never changes attempts.';
