-- 0063 Phase 6 Q2: quality workspace, part 2 (review/phase6-quality.md).
--
-- qc_transcripts   every transcript version of a recording: provider transcription (through the
--                  AI gateway), a manual transcript, or a human correction (`based_on_id`).
--                  Speaker labels are the provider's anonymous ones; `speakers` holds the roles a
--                  human verified. Append-only; the one allowed UPDATE is the AI retention purge
--                  (text and segments cleared, `purged_at` set) in a transaction that ran
--                  SET LOCAL cawp.retention_purge = 'qc_quality'.
-- qc_reviews       every saved review revision: an AI draft (origin `ai`, validated server-side:
--                  quotations must be verbatim in the transcript, the evidence rule applies) or a
--                  human save (origin `human`). Immutable.
-- qc_approvals     an explicit human approval of a saved human review revision, with an immutable
--                  snapshot of the template, transcript, segments, evidence (notes, action,
--                  context), review and metrics. Changing the evidence clears the recording's
--                  current approval; earlier approvals stay here. The only allowed UPDATE is the
--                  retention purge of the snapshot's transcript text (same setting as above).
--
-- The dashboard counts current approvals only (qc_recordings.current_approval_id).
-- New objects only: instant.

CREATE TABLE IF NOT EXISTS qc_transcripts (
    id                uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    recording_id      uuid        NOT NULL REFERENCES qc_recordings (id),
    version           integer     NOT NULL CHECK (version >= 1),
    source            text        NOT NULL CHECK (source IN ('provider', 'manual', 'correction')),
    based_on_id       uuid        REFERENCES qc_transcripts (id),
    language          text        CHECK (language ~ '^[a-z]{2}$'),
    diarized          boolean     NOT NULL DEFAULT false,
    model             text        CHECK (length(model) <= 200),
    provider_call_id  uuid,
    text              text,
    segments          jsonb       CHECK (segments IS NULL OR jsonb_typeof(segments) = 'array'),
    speakers          jsonb       NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(speakers) = 'object'),
    segment_count     integer     NOT NULL CHECK (segment_count >= 0),
    text_sha256       text        NOT NULL CHECK (text_sha256 ~ '^[0-9a-f]{64}$'),
    reason            text        CHECK (length(reason) <= 500),
    created_by        uuid,
    created_at        timestamptz NOT NULL DEFAULT now(),
    purged_at         timestamptz,
    UNIQUE (recording_id, version),
    CONSTRAINT qc_transcripts_content CHECK ((purged_at IS NULL) = (text IS NOT NULL AND segments IS NOT NULL)),
    CONSTRAINT qc_transcripts_provider CHECK (source <> 'provider' OR (model IS NOT NULL AND provider_call_id IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS ix_qc_transcripts_retention ON qc_transcripts (created_at) WHERE purged_at IS NULL;

CREATE TABLE IF NOT EXISTS qc_reviews (
    id                     uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    recording_id           uuid        NOT NULL REFERENCES qc_recordings (id),
    version                integer     NOT NULL CHECK (version >= 1),
    origin                 text        NOT NULL CHECK (origin IN ('ai', 'human')),
    based_on_id            uuid        REFERENCES qc_reviews (id),
    recording_revision     integer     NOT NULL CHECK (recording_revision >= 1),
    template_key           text        NOT NULL,
    template_version       integer     NOT NULL,
    transcript_id          uuid        REFERENCES qc_transcripts (id),
    results                jsonb       NOT NULL CHECK (jsonb_typeof(results) = 'array'),
    summary                text        CHECK (length(summary) <= 4000),
    coaching               text        CHECK (length(coaching) <= 4000),
    language               text        CHECK (language IN ('ar', 'en')),
    met                    integer     NOT NULL CHECK (met >= 0),
    needs_improvement      integer     NOT NULL CHECK (needs_improvement >= 0),
    not_applicable         integer     NOT NULL CHECK (not_applicable >= 0),
    insufficient_evidence  integer     NOT NULL CHECK (insufficient_evidence >= 0),
    total                  integer     NOT NULL CHECK (total >= 1),
    model                  text        CHECK (length(model) <= 200),
    provider_call_id       uuid,
    validation             jsonb       NOT NULL DEFAULT '[]'::jsonb,
    created_by             uuid,
    created_at             timestamptz NOT NULL DEFAULT now(),
    UNIQUE (recording_id, version),
    CONSTRAINT qc_reviews_template FOREIGN KEY (template_key, template_version)
        REFERENCES qc_templates (template_key, version),
    CONSTRAINT qc_reviews_counts CHECK (met + needs_improvement + not_applicable + insufficient_evidence = total),
    CONSTRAINT qc_reviews_ai CHECK (origin <> 'ai' OR (model IS NOT NULL AND provider_call_id IS NOT NULL))
);

CREATE TABLE IF NOT EXISTS qc_approvals (
    id                     uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    recording_id           uuid        NOT NULL REFERENCES qc_recordings (id),
    review_id              uuid        NOT NULL REFERENCES qc_reviews (id),
    approved_revision      integer     NOT NULL CHECK (approved_revision >= 1),
    approved_by            uuid        NOT NULL,
    approved_at            timestamptz NOT NULL DEFAULT now(),
    template_key           text        NOT NULL,
    template_version       integer     NOT NULL,
    case_id                uuid,
    contract_id            uuid,
    met                    integer     NOT NULL CHECK (met >= 0),
    needs_improvement      integer     NOT NULL CHECK (needs_improvement >= 0),
    not_applicable         integer     NOT NULL CHECK (not_applicable >= 0),
    insufficient_evidence  integer     NOT NULL CHECK (insufficient_evidence >= 0),
    total                  integer     NOT NULL CHECK (total >= 1),
    snapshot               jsonb       NOT NULL CHECK (jsonb_typeof(snapshot) = 'object'),
    snapshot_sha256        text        NOT NULL CHECK (snapshot_sha256 ~ '^[0-9a-f]{64}$'),
    transcript_purged_at   timestamptz,
    UNIQUE (recording_id, approved_revision),
    CONSTRAINT qc_approvals_template FOREIGN KEY (template_key, template_version)
        REFERENCES qc_templates (template_key, version),
    CONSTRAINT qc_approvals_counts CHECK (met + needs_improvement + not_applicable + insufficient_evidence = total)
);
CREATE INDEX IF NOT EXISTS ix_qc_approvals_recording ON qc_approvals (recording_id, approved_at DESC);
CREATE INDEX IF NOT EXISTS ix_qc_approvals_approved_at ON qc_approvals (approved_at);

ALTER TABLE qc_recordings DROP CONSTRAINT IF EXISTS qc_recordings_current_transcript;
ALTER TABLE qc_recordings ADD CONSTRAINT qc_recordings_current_transcript
    FOREIGN KEY (current_transcript_id) REFERENCES qc_transcripts (id);
ALTER TABLE qc_recordings DROP CONSTRAINT IF EXISTS qc_recordings_current_review;
ALTER TABLE qc_recordings ADD CONSTRAINT qc_recordings_current_review
    FOREIGN KEY (current_review_id) REFERENCES qc_reviews (id);
ALTER TABLE qc_recordings DROP CONSTRAINT IF EXISTS qc_recordings_current_approval;
ALTER TABLE qc_recordings ADD CONSTRAINT qc_recordings_current_approval
    FOREIGN KEY (current_approval_id) REFERENCES qc_approvals (id);

-- ------------------------------------------------------------------------------ guards
-- transcripts: append-only, except the retention purge of their content
CREATE OR REPLACE FUNCTION qc_transcripts_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'UPDATE' AND coalesce(current_setting('cawp.retention_purge', true), '') = 'qc_quality'
       AND NEW.text IS NULL AND NEW.segments IS NULL AND NEW.purged_at IS NOT NULL
       AND (to_jsonb(NEW) - 'text' - 'segments' - 'purged_at') = (to_jsonb(OLD) - 'text' - 'segments' - 'purged_at') THEN
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'QC transcript versions are append-only (a correction is a new version)' USING ERRCODE = 'QC001';
END $$;

DROP TRIGGER IF EXISTS qc_transcripts_guard ON qc_transcripts;
CREATE TRIGGER qc_transcripts_guard BEFORE UPDATE OR DELETE ON qc_transcripts
    FOR EACH ROW EXECUTE FUNCTION qc_transcripts_guard();
DROP TRIGGER IF EXISTS qc_transcripts_no_truncate ON qc_transcripts;
CREATE TRIGGER qc_transcripts_no_truncate BEFORE TRUNCATE ON qc_transcripts
    FOR EACH STATEMENT EXECUTE FUNCTION qc_no_truncate();

-- reviews: immutable
CREATE OR REPLACE FUNCTION qc_reviews_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'QC review revisions are immutable (a change is a new revision)' USING ERRCODE = 'QC001';
END $$;

DROP TRIGGER IF EXISTS qc_reviews_guard ON qc_reviews;
CREATE TRIGGER qc_reviews_guard BEFORE UPDATE OR DELETE ON qc_reviews
    FOR EACH ROW EXECUTE FUNCTION qc_reviews_guard();
DROP TRIGGER IF EXISTS qc_reviews_no_truncate ON qc_reviews;
CREATE TRIGGER qc_reviews_no_truncate BEFORE TRUNCATE ON qc_reviews
    FOR EACH STATEMENT EXECUTE FUNCTION qc_no_truncate();

-- approvals: immutable snapshots, except the retention purge of the snapshot's transcript text
CREATE OR REPLACE FUNCTION qc_approvals_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'UPDATE' AND coalesce(current_setting('cawp.retention_purge', true), '') = 'qc_quality'
       AND NEW.transcript_purged_at IS NOT NULL
       AND (to_jsonb(NEW) - 'snapshot' - 'transcript_purged_at') = (to_jsonb(OLD) - 'snapshot' - 'transcript_purged_at')
       AND (NEW.snapshot - 'transcript') = (OLD.snapshot - 'transcript') THEN
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'QC approval snapshots are immutable' USING ERRCODE = 'QC001';
END $$;

DROP TRIGGER IF EXISTS qc_approvals_guard ON qc_approvals;
CREATE TRIGGER qc_approvals_guard BEFORE UPDATE OR DELETE ON qc_approvals
    FOR EACH ROW EXECUTE FUNCTION qc_approvals_guard();
DROP TRIGGER IF EXISTS qc_approvals_no_truncate ON qc_approvals;
CREATE TRIGGER qc_approvals_no_truncate BEFORE TRUNCATE ON qc_approvals
    FOR EACH STATEMENT EXECUTE FUNCTION qc_no_truncate();

COMMENT ON TABLE qc_transcripts IS 'Phase 6 Q2: transcript versions of a QC recording (append-only; retention clears content).';
COMMENT ON TABLE qc_reviews IS 'Phase 6 Q2: saved QC review revisions, AI drafts and human saves (immutable).';
COMMENT ON TABLE qc_approvals IS 'Phase 6 Q2: explicit human approvals with immutable snapshots (history is kept).';
