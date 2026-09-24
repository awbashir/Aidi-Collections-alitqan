-- 0062 Phase 6 Q2: quality workspace, part 1 (docs/phase6-spec.md stream Q2,
-- docs/reference/rebuild-quality-workspace.md, review/phase6-quality.md).
--
-- qc_templates     versioned review templates. One row per (template_key, version); a new
--                  version is a new row, so a review or an approval always names the exact
--                  criteria it was judged against. Content is immutable (only `active` may
--                  change); rows are never deleted. Seeded with Collections v1 (bilingual). The
--                  rebuild's "Zain v1" sales script is deliberately NOT shipped.
-- qc_recordings    the private QC recording library: an imported WAV/MP3, or a copy of a Phase 5
--                  call-attempt recording made on an explicit user action (source `pbx_call`,
--                  with provenance). The audio lives under the private quality storage root
--                  (<UPLOAD_DIR>/private/quality by default); only the relative path is stored and
--                  it is never returned. The original filename is not stored at all (it can hold
--                  a customer number). `revision` is the record's optimistic-concurrency counter:
--                  every evidence, transcript, review or approval change bumps it.
--                  sha256 detects duplicates among recordings whose audio is still present.
-- qc_file_purges   durable purge intents for audio files: the retention hook records the intent
--                  and clears `stored_path` in one transaction, and the file is unlinked after
--                  the commit (the Phase 2 pattern); a crash leaves a pending intent that the next
--                  run completes.
--
-- No FKs to users or cases (legacy code hard-deletes users; case links follow case scope at read
-- time). New objects only: instant.

-- ------------------------------------------------------------------------------ templates
CREATE TABLE IF NOT EXISTS qc_templates (
    template_key  text        NOT NULL CHECK (template_key ~ '^[a-z][a-z0-9-]{1,63}$'),
    version       integer     NOT NULL CHECK (version >= 1),
    name_en       text        NOT NULL CHECK (length(btrim(name_en)) BETWEEN 1 AND 200),
    name_ar       text        NOT NULL CHECK (length(btrim(name_ar)) BETWEEN 1 AND 200),
    origin_en     text        NOT NULL CHECK (length(origin_en) <= 2000),
    origin_ar     text        NOT NULL CHECK (length(origin_ar) <= 2000),
    criteria      jsonb       NOT NULL CHECK (jsonb_typeof(criteria) = 'array'
                                              AND jsonb_array_length(criteria) BETWEEN 1 AND 100),
    active        boolean     NOT NULL DEFAULT true,
    created_by    uuid,
    created_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (template_key, version)
);

CREATE OR REPLACE FUNCTION qc_templates_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'UPDATE' THEN
        IF (to_jsonb(NEW) - 'active') IS DISTINCT FROM (to_jsonb(OLD) - 'active') THEN
            RAISE EXCEPTION 'a QC template version is immutable (publish a new version)' USING ERRCODE = 'QC001';
        END IF;
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'QC template versions are never deleted' USING ERRCODE = 'QC001';
END $$;

DROP TRIGGER IF EXISTS qc_templates_guard ON qc_templates;
CREATE TRIGGER qc_templates_guard BEFORE UPDATE OR DELETE ON qc_templates
    FOR EACH ROW EXECUTE FUNCTION qc_templates_guard();

CREATE OR REPLACE FUNCTION qc_no_truncate() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'quality review records cannot be truncated' USING ERRCODE = 'QC001';
END $$;

DROP TRIGGER IF EXISTS qc_templates_no_truncate ON qc_templates;
CREATE TRIGGER qc_templates_no_truncate BEFORE TRUNCATE ON qc_templates
    FOR EACH STATEMENT EXECUTE FUNCTION qc_no_truncate();

-- Collections v1: derived from the rebuild's collections checklist (src/quality-templates.js).
-- Per criterion: id, names and guidance in both languages, the evidence it needs
-- (`human_listening_only`: audible delivery cannot be judged from text), whether it is conditional
-- (and when it applies), and the reviewer-supplied input it compares against (`requires_input`).
INSERT INTO qc_templates (template_key, version, name_en, name_ar, origin_en, origin_ar, criteria)
VALUES ('collections-v1', 1, 'Collections · conversation quality', 'التحصيل · جودة الحوار',
 'Derived from the supplied Arabic collection checklist and dashboard examples (rebuild quality workspace, Collections v1). An operational template for supervisor review; not a legal policy.',
 'مشتق من قائمة التحقق العربية للتحصيل وأمثلة لوحة المتابعة المرفقة (مساحة الجودة في النسخة الجديدة، التحصيل الإصدار 1). قالب تشغيلي لمراجعة المشرفين، وليس سياسة قانونية.',
 $qc$[
  {"id": "intro", "en": "Introduction & purpose", "ar": "التعريف بالنفس والغرض",
   "guidance_en": "Identify the employee, the organization and the reason for calling. Do not infer identity from a filename.",
   "guidance_ar": "التحقق من تعريف الموظف والجهة والغرض من الاتصال. لا تُستنتج هوية الموظف من اسم الملف.",
   "human_listening_only": false, "conditional": false, "applies_when_en": null, "applies_when_ar": null, "requires_input": null},
  {"id": "identity", "en": "Customer verification", "ar": "التحقق من هوية العميل",
   "guidance_en": "Look for customer verification before private debt details are discussed. If verification happened outside the recording, the result is insufficient evidence.",
   "guidance_ar": "التحقق من هوية العميل قبل مناقشة تفاصيل المديونية الخاصة. إذا جرى التحقق خارج التسجيل، فالنتيجة أدلة غير كافية.",
   "human_listening_only": false, "conditional": false, "applies_when_en": null, "applies_when_ar": null, "requires_input": null},
  {"id": "debt", "en": "Clear debt information", "ar": "وضوح تفاصيل المديونية",
   "guidance_en": "Explain the debt information available in the conversation. Do not assert that amounts are correct without account evidence.",
   "guidance_ar": "شرح معلومات المديونية المتاحة في الحوار. لا تؤكد صحة المبالغ دون مستندات الحساب.",
   "human_listening_only": false, "conditional": false, "applies_when_en": null, "applies_when_ar": null, "requires_input": null},
  {"id": "affordability", "en": "Understanding ability to pay", "ar": "فهم القدرة على السداد",
   "guidance_en": "When payment difficulty is raised, respectfully explore the ability to pay without pressure. A salary question is not mandatory.",
   "guidance_ar": "عند طرح صعوبة في السداد، تُناقش القدرة على الدفع باحترام ودون ضغط. السؤال عن الراتب ليس إلزامياً.",
   "human_listening_only": false, "conditional": true,
   "applies_when_en": "The customer raises a payment difficulty. Otherwise: not applicable, with a reason.",
   "applies_when_ar": "عند طرح العميل صعوبة في السداد. وإلا: لا ينطبق، مع ذكر السبب.", "requires_input": null},
  {"id": "listening", "en": "Listening & response", "ar": "الاستماع والاستجابة",
   "guidance_en": "Use explicit dialogue evidence of acknowledging and responding to the customer. No emotion or personality inference.",
   "guidance_ar": "توثيق استجابة واضحة لما يقوله العميل. لا تُستنتج المشاعر أو السمات الشخصية من النص.",
   "human_listening_only": false, "conditional": false, "applies_when_en": null, "applies_when_ar": null, "requires_input": null},
  {"id": "alternatives", "en": "Alternatives after refusal", "ar": "حلول بديلة عند الرفض",
   "guidance_en": "If the customer refuses or cannot pay, look for a relevant alternative or escalation.",
   "guidance_ar": "عند رفض السداد أو تعذره، يُبحث عن بديل مناسب أو تصعيد.",
   "human_listening_only": false, "conditional": true,
   "applies_when_en": "The customer refuses or cannot pay. Otherwise: not applicable, with a reason.",
   "applies_when_ar": "عند رفض العميل السداد أو تعذره. وإلا: لا ينطبق، مع ذكر السبب.", "requires_input": null},
  {"id": "next_steps", "en": "Agreed next steps", "ar": "تلخيص الخطوات القادمة",
   "guidance_en": "Summarize the outcome, any promised payment date and the follow-up. A promise is not money received.",
   "guidance_ar": "تلخيص النتيجة وتاريخ أي وعد بالسداد والمتابعة. الوعد بالسداد لا يُعتبر دفعة محصلة.",
   "human_listening_only": false, "conditional": false, "applies_when_en": null, "applies_when_ar": null, "requires_input": null},
  {"id": "notes", "en": "Notes match the conversation", "ar": "دقة ملاحظات الموظف",
   "guidance_en": "Compare the employee notes with the transcript, quote the relevant passage and explain any contradiction. Missing notes: insufficient evidence.",
   "guidance_ar": "مقارنة ملاحظات الموظف بالنص الفعلي، مع اقتباس الدليل وشرح أي تعارض. غياب الملاحظات يعني أدلة غير كافية.",
   "human_listening_only": false, "conditional": false, "applies_when_en": null, "applies_when_ar": null, "requires_input": "notes"},
  {"id": "action", "en": "Recorded action matches outcome", "ar": "تطابق الإجراء مع النتيجة",
   "guidance_en": "Compare the recorded action with the conversation. Missing action: insufficient evidence.",
   "guidance_ar": "مقارنة الإجراء المسجل بالحوار. غياب الإجراء يعني أدلة غير كافية.",
   "human_listening_only": false, "conditional": false, "applies_when_en": null, "applies_when_ar": null, "requires_input": "recorded_action"},
  {"id": "delivery", "en": "Audible clarity & courtesy", "ar": "وضوح الصوت وأسلوب التواصل",
   "guidance_en": "Human listening only: a reviewer listens and cites an audio timestamp. Tone, emotion or vocal delivery are never inferred from a transcript.",
   "guidance_ar": "استماع بشري فقط مع تحديد توقيت في التسجيل. لا تُستنتج النبرة أو المشاعر أو طريقة الإلقاء من النص.",
   "human_listening_only": true, "conditional": false, "applies_when_en": null, "applies_when_ar": null, "requires_input": null}
 ]$qc$::jsonb)
ON CONFLICT (template_key, version) DO NOTHING;

-- ------------------------------------------------------------------------------ recordings
CREATE TABLE IF NOT EXISTS qc_recordings (
    id                     uuid          PRIMARY KEY DEFAULT gen_random_uuid(),
    revision               integer       NOT NULL DEFAULT 1 CHECK (revision >= 1),
    source                 text          NOT NULL CHECK (source IN ('import', 'pbx_call')),
    title                  text          NOT NULL CHECK (length(btrim(title)) BETWEEN 1 AND 200),
    audio_format           text          NOT NULL CHECK (audio_format IN ('wav', 'mp3')),
    content_type           text          NOT NULL CHECK (content_type IN ('audio/wav', 'audio/mpeg')),
    size_bytes             integer       NOT NULL CHECK (size_bytes > 0),
    sha256                 text          NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    duration_seconds       numeric(10,3) NOT NULL CHECK (duration_seconds > 0 AND duration_seconds <= 1800),
    channels               smallint      CHECK (channels IN (1, 2)),
    sample_rate            integer       CHECK (sample_rate BETWEEN 8000 AND 96000),
    bits_per_sample        smallint,
    bitrate_kbps           integer,
    stored_path            text          CHECK (stored_path !~ '^/' AND stored_path !~ '\.\.'),
    audio_purged_at        timestamptz,
    case_id                uuid,
    attempt_id             uuid,
    provenance             jsonb         NOT NULL DEFAULT '{}'::jsonb,
    template_key           text,
    template_version       integer,
    employee_label         text          CHECK (length(employee_label) <= 100),
    notes                  text          CHECK (length(notes) <= 4000),
    recorded_action        text          CHECK (length(recorded_action) <= 1000),
    context                text          CHECK (length(context) <= 4000),
    current_transcript_id  uuid,
    current_review_id      uuid,
    current_approval_id    uuid,
    processing             text          CHECK (processing IN ('transcribe', 'draft')),
    processing_token       uuid,
    processing_started_at  timestamptz,
    processing_by          uuid,
    last_error             text          CHECK (last_error ~ '^[a-z][a-z0-9_]{1,63}$'),
    created_by             uuid          NOT NULL,
    created_at             timestamptz   NOT NULL DEFAULT now(),
    updated_at             timestamptz   NOT NULL DEFAULT now(),
    CONSTRAINT qc_recordings_template FOREIGN KEY (template_key, template_version)
        REFERENCES qc_templates (template_key, version),
    CONSTRAINT qc_recordings_audio_state CHECK ((audio_purged_at IS NULL) = (stored_path IS NOT NULL)),
    CONSTRAINT qc_recordings_processing CHECK (
        (processing IS NULL) = (processing_token IS NULL) AND (processing IS NULL) = (processing_started_at IS NULL)),
    CONSTRAINT qc_recordings_call_link CHECK (source <> 'pbx_call' OR (attempt_id IS NOT NULL AND case_id IS NOT NULL))
);

-- duplicates: one live copy of the same audio (a purged copy may be imported again)
CREATE UNIQUE INDEX IF NOT EXISTS ux_qc_recordings_sha256_live ON qc_recordings (sha256) WHERE audio_purged_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_qc_recordings_created ON qc_recordings (created_at DESC, id);
CREATE INDEX IF NOT EXISTS ix_qc_recordings_case ON qc_recordings (case_id) WHERE case_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_qc_recordings_attempt ON qc_recordings (attempt_id) WHERE attempt_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_qc_recordings_audio_retention ON qc_recordings (created_at) WHERE audio_purged_at IS NULL;

-- ------------------------------------------------------------------------------ purge intents
CREATE TABLE IF NOT EXISTS qc_file_purges (
    id            bigserial   PRIMARY KEY,
    recording_id  uuid        NOT NULL REFERENCES qc_recordings (id),
    stored_path   text        NOT NULL CHECK (stored_path !~ '^/' AND stored_path !~ '\.\.'),
    reason        text        NOT NULL CHECK (length(reason) BETWEEN 1 AND 200),
    recorded_at   timestamptz NOT NULL DEFAULT now(),
    done_at       timestamptz
);
CREATE INDEX IF NOT EXISTS ix_qc_file_purges_pending ON qc_file_purges (recorded_at) WHERE done_at IS NULL;

COMMENT ON TABLE qc_templates IS 'Phase 6 Q2: versioned QC review templates (content immutable; Collections v1 seeded).';
COMMENT ON TABLE qc_recordings IS
    'Phase 6 Q2: private QC recording library (imported audio or an explicit copy of a call-attempt recording).';
COMMENT ON TABLE qc_file_purges IS 'Phase 6 Q2: durable intents to unlink QC audio files after the purge commits.';
