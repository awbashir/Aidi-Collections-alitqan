/**
 * Phase 6 wire types: AI status and data policy, the provider-call audit, AI drafts and the
 * quality workspace. Contracts: review/phase6-{foundation,drafts,quality}.md, checked against
 * CAWP-API/api/ai.py, api/quality.py and services/ai/*.py (2026-09-24).
 *
 * Honesty rules (docs/phase6-spec.md): a missing count, model or percentage stays `null` and is
 * shown as unknown / "—", never as 0. AI output is always a draft.
 */
import type { Paged } from './types'

type Iso = string
/** A user reference (the same shape as the Phase 5 `UserRef`). */
type UserRef = { id: string; name: string | null } | null

/* ------------------------------------------------------------------ AI status and policy */

export const AI_CAPABILITIES = ['generate_structured', 'transcribe', 'realtime_session'] as const
export type AiCapabilityName = (typeof AI_CAPABILITIES)[number]

/** The reason codes the gateway reports, in its order. */
export const AI_REASONS = ['ai_not_configured', 'model_not_set', 'ai_policy_required', 'ai_policy_category_denied', 'ai_quota_exceeded'] as const

export type AiCapability = {
    ready: boolean
    reasons: string[]
    required_categories: string[]
    /** ai_admin only; null = not set. */
    model?: string | null
}

export type AiMasking = { name: boolean; identity_number: boolean; phone: boolean; account: boolean }
export type AiRetention = { transcripts: number; audio: number; drafts: number; provider_logs: number }

export type AiPolicy = {
    revision: number
    state: 'approved' | 'suspended'
    provider: string
    allowed_categories: string[]
    masking: AiMasking
    retention_days: AiRetention
    approved_by: UserRef
    approved_at: Iso | null
    reason: string | null
}

/** The summary every AI key sees (`state: missing` when no policy was ever recorded). */
export type AiPolicySummary = {
    state: 'approved' | 'suspended' | 'missing'
    revision: number
    provider: string | null
    allowed_categories: string[]
    masking: AiMasking | null
    retention_days?: AiRetention
    approved_by?: UserRef
    approved_at?: Iso | null
    reason?: string | null
}

export type AiQuota = {
    day: string
    resets_at: Iso
    user: { limit: number; used: number; remaining: number } | null
    total: { limit: number; used: number; remaining: number }
    exceeded: 'user' | 'total' | null
}

export type AiConfiguration = {
    provider: string
    key_configured: boolean
    organization_configured: boolean
    project_configured: boolean
    base_url: string
    base_url_is_default: boolean
    base_url_problem: string | null
    models: Record<AiCapabilityName, string | null>
    timeouts_seconds: Record<AiCapabilityName, number>
    max_retries: number
    max_input_chars: number
    transcribe_max_bytes: number
}

export type AiStatus = {
    provider: string
    capabilities: Record<AiCapabilityName, AiCapability>
    reasons: string[]
    policy: AiPolicySummary
    quota?: AiQuota
    /** ai_admin only. */
    configuration?: AiConfiguration
}

export type AiPolicyOptions = {
    states: string[]
    providers: string[]
    categories: string[]
    masking: string[]
    retention_days: Record<keyof AiRetention, { min: number; max: number }>
}

export type AiPolicyView = { policy: AiPolicy | null; revision: number; options: AiPolicyOptions }

export type AiPolicyBody = {
    expected_revision: number
    state: 'approved' | 'suspended'
    provider: string
    allowed_categories: string[]
    masking: AiMasking
    retention_days: AiRetention
    reason: string
    confirmed: boolean
}

export type AiUsage = {
    input_tokens: number | null
    output_tokens: number | null
    total_tokens: number | null
    audio_seconds: number | null
}

export const AI_CALL_OUTCOMES = ['started', 'succeeded', 'failed', 'refused', 'interrupted'] as const
export type AiCallOutcome = (typeof AI_CALL_OUTCOMES)[number]

export type AiProviderCall = {
    id: string
    started_at: Iso
    finished_at: Iso | null
    purpose: string
    capability: string
    provider: string
    model: string | null
    policy_revision: number | null
    data_categories: string[]
    masked: boolean
    masked_count: number | null
    outcome: AiCallOutcome
    error_code: string | null
    provider_error_type: string | null
    http_status: number | null
    attempts: number | null
    usage: AiUsage
    latency_ms: number | null
    input_bytes: number | null
    actor: UserRef
    entity_type: string | null
    entity_id: string | null
    case_id: string | null
    provider_request_id: string | null
}

export type AiProviderCallQuery = {
    purpose?: string
    capability?: string
    outcome?: string
    entity_type?: string
    case_id?: string
    page?: number
    page_size?: number
}

export type AiCheckResult = {
    ok: boolean
    call_id: string
    model: string | null
    policy_revision: number
    latency_ms: number | null
    attempts: number
    usage: AiUsage
}

/* ------------------------------------------------------------------ AI drafts */

export const DRAFT_KINDS = ['call_script', 'case_guidance', 'narrative', 'followup_message', 'call_summary', 'coaching'] as const
export type DraftKind = (typeof DRAFT_KINDS)[number]
export const CASE_DRAFT_KINDS: DraftKind[] = ['call_script', 'case_guidance', 'narrative', 'followup_message']
export const CALL_DRAFT_KINDS: DraftKind[] = ['call_summary', 'coaching']
export type DraftStatus = 'generated' | 'edited' | 'saved_as_note' | 'discarded'
export type DraftLanguage = 'ar' | 'en'

export type DraftOptions = {
    audience?: 'supervisor' | 'legal'
    channel?: 'sms' | 'whatsapp' | 'email'
    include_transcript?: boolean
}

export type DraftChecks = {
    unknown_fact_ids?: string[]
    unsupported?: Array<string | Record<string, unknown>>
    unverified_quotes?: Array<string | Record<string, unknown>>
    [k: string]: unknown
}

export type AiDraft = {
    id: string
    kind: DraftKind
    case_id: string
    attempt_id: string | null
    language: DraftLanguage
    options: DraftOptions
    status: DraftStatus
    revision: number
    ai_generated: true
    notice: string
    edited: boolean
    edit_count: number
    input: { categories: string[]; hash: string; masked_fingerprint: string; summary: Record<string, number> }
    provenance: {
        provider_call_id: string | null
        model: string | null
        policy_revision: number | null
        masked: boolean
        masked_count: number | null
        schema: string
    }
    requested_by: UserRef
    created_at: Iso
    updated_at?: Iso
    expires_at: Iso | null
    edited_at?: Iso | null
    edited_by?: UserRef
    saved_note_id: string | null
    saved_at?: Iso | null
    saved_by?: UserRef
    discarded_at: Iso | null
    discarded_by?: UserRef
    purged_at: Iso | null
    content_available: boolean
    /** list + full */
    text?: string | null
    /** full only */
    content?: Record<string, unknown> | null
    checks?: DraftChecks | null
    citations?: Record<string, string> | null
    generated_text?: string | null
}

export type DraftCreateBody = {
    kind: DraftKind
    case_id?: string
    attempt_id?: string
    language: DraftLanguage
    options?: DraftOptions
}

export type DraftQuery = { case?: string; attempt?: string; kind?: string; status?: string; mine?: 1; page?: number; page_size?: number }

export type SaveAsNoteBody = {
    expected_revision: number
    text: string
    comment_status_id: string
    contact_method_id: string
    comment_reachability_id?: string
    confirmed: true
}

export type SavedNote = {
    draft: AiDraft
    note: {
        id: string
        case_id: string
        comment_status_id: string
        contact_method_id: string
        comment_reachability_id: string | null
        next_contact_date: null
    }
}

/* ------------------------------------------------------------------ quality */

export const QC_RESULTS = ['met', 'needs_improvement', 'not_applicable', 'insufficient_evidence'] as const
export type QcResult = (typeof QC_RESULTS)[number]
export const QC_STATUSES = ['new', 'transcribed', 'ai_draft', 'human_draft', 'approved'] as const
export type QcStatus = (typeof QC_STATUSES)[number]
export type QcSource = 'import' | 'pbx_call'

export type QcCriterion = {
    id: string
    en: string
    ar: string
    conditional: boolean
    guidance_en: string
    guidance_ar: string
    requires_input: 'notes' | 'recorded_action' | null
    applies_when_en: string | null
    applies_when_ar: string | null
    human_listening_only: boolean
}

export type QcTemplate = {
    key: string
    version: number
    name_en: string
    name_ar: string
    origin_en: string
    origin_ar: string
    criteria: QcCriterion[]
    active: boolean
    created_at: Iso
}

export type QcTemplateRef = { key: string; version: number }

export type QcRatio = { numerator: number; denominator: number; ratio: number; percent: number } | null

export type QcMetrics = {
    met: number
    needs_improvement: number
    not_applicable: number
    insufficient_evidence: number
    total: number
    assessed: number
    has_percentage: boolean
    compliance: QcRatio
    coverage: QcRatio
}

export type QcAudio = {
    format: string
    content_type: string
    size_bytes: number
    sha256: string
    duration_seconds: number
    channels: number | null
    sample_rate: number | null
    bits_per_sample: number | null
    bitrate_kbps: number | null
    available: boolean
    purged_at: Iso | null
    url: string | null
}

export type QcSegment = { id: string; start: number | null; end: number | null; speaker: string | null; text: string }
export type QcSpeakerRole = 'agent' | 'customer' | 'other'

export type QcTranscript = {
    id: string
    version: number
    source: 'provider' | 'manual' | 'correction'
    based_on_id: string | null
    language: string | null
    diarized: boolean
    model: string | null
    provider_call_id: string | null
    text: string | null
    segments: QcSegment[] | null
    segment_count: number
    speakers: Record<string, { role: QcSpeakerRole; verified: boolean; verified_by: UserRef; verified_at: Iso | null }>
    speaker_labels: string[]
    text_sha256: string
    reason: string | null
    purged: boolean
    purged_at: Iso | null
    created_by: UserRef
    created_at: Iso
}

export type QcQuoteEvidence = {
    type: 'quote'
    text: string
    segment_ids?: string[]
    start?: number | null
    end?: number | null
}
export type QcAudioEvidence = { type: 'audio'; start: number; end?: number | null; human_reviewed: true }
export type QcEvidence = QcQuoteEvidence | QcAudioEvidence

export type QcReviewItem = {
    criterion_id: string
    result: QcResult
    reason: string
    evidence: QcEvidence[]
    /** AI drafts only: what the model said, and the server's repair codes. */
    ai_result?: QcResult | null
    validation?: string[]
}

export type QcReview = {
    id: string
    version: number
    origin: 'ai' | 'human'
    status: 'ai_draft' | 'human_draft'
    based_on_id: string | null
    recording_revision: number
    template: QcTemplateRef
    transcript_id: string | null
    results: QcReviewItem[]
    summary: string | null
    coaching: string | null
    language: string | null
    metrics: QcMetrics
    model: string | null
    provider_call_id: string | null
    validation: Array<{ criterion_id: string | null; codes: string[] }>
    created_by: UserRef
    created_at: Iso
}

export type QcApproval = {
    id: string
    recording_id: string
    review_id: string
    approved_revision: number
    approved_by: UserRef
    approved_at: Iso
    template: QcTemplateRef
    metrics: QcMetrics
    snapshot_sha256: string
    transcript_purged: boolean
    current: boolean
    snapshot?: QcSnapshot
}

export type QcSnapshot = {
    schema: number
    recording: {
        id: string
        title: string
        source: QcSource
        audio: Omit<QcAudio, 'url'>
        case_id: string | null
        attempt_id: string | null
        provenance: Record<string, unknown>
    }
    template: QcTemplate
    transcript: (QcTranscript & { purged?: boolean }) | null
    evidence: QcEvidenceFields & { template: QcTemplateRef }
    review: QcReview
    metrics: QcMetrics
    approval: { approved_revision: number; approved_by: UserRef; approved_at: Iso }
}

export type QcEvidenceFields = {
    employee_label: string | null
    notes: string | null
    recorded_action: string | null
    context: string | null
}

export type QcRecording = {
    id: string
    revision: number
    title: string
    source: QcSource
    status: QcStatus
    audio: QcAudio
    link: { case_id: string | null; attempt_id: string | null }
    provenance: Record<string, unknown>
    evidence: QcEvidenceFields & { template: QcTemplateRef | null }
    transcript: QcTranscript | null
    review: QcReview | null
    approval: QcApproval | null
    processing: { kind: string; started_at: Iso | null; stale: boolean } | null
    last_error: string | null
    history: { transcripts: number; reviews: number; approvals: number }
    created_by: UserRef
    created_at: Iso
    updated_at: Iso
    note: string
}

export type QcRecordingRow = {
    id: string
    revision: number
    title: string
    source: QcSource
    status: QcStatus
    duration_seconds: number
    format: string
    audio_available: boolean
    case_id: string | null
    attempt_id: string | null
    template: QcTemplateRef | null
    has_transcript: boolean
    review_origin: 'ai' | 'human' | null
    metrics: QcMetrics | null
    approved: boolean
    approved_at: Iso | null
    processing: string | null
    created_at: Iso
}

export type QcRecordingQuery = {
    status?: string
    source?: string
    case_id?: string
    attempt_id?: string
    template?: string
    page?: number
    page_size?: number
}

export type QcImportResult = { duplicate: boolean; recording: QcRecording }

export type QcOverview = {
    templates: QcTemplate[]
    ai: { transcribe: AiCapability; draft: AiCapability; policy: AiPolicySummary }
    counts: { recordings: number; approved: number; drafts: number; with_audio: number }
    limits: { max_bytes: number; max_seconds: number; formats: string[]; sample_rate: [number, number] }
    results: QcResult[]
}

export type QcEvidenceBody = {
    expected_revision: number
    title?: string
    template?: QcTemplateRef | null
    employee_label?: string | null
    notes?: string | null
    recorded_action?: string | null
    context?: string | null
}

export type QcTranscriptBody = {
    expected_revision: number
    text?: string
    segments?: Array<{ start: number | null; end: number | null; speaker: string | null; text: string }>
    speakers?: Record<string, QcSpeakerRole | null>
    language?: 'ar' | 'en'
    reason?: string
}

export type QcReviewBody = {
    expected_revision: number
    results: Array<{ criterion_id: string; result: QcResult | ''; reason: string; evidence: QcEvidence[] }>
    summary?: string
    coaching?: string
    language?: 'ar' | 'en'
    based_on_review_id?: string
}

export type QcDashboardEntry = QcMetrics & {
    reviews: number
    reviews_with_percentage: number
    reviews_without_percentage: number
}

export type QcDashboard = {
    basis: string
    note: string
    filters: {
        template: string | null
        template_version: number | null
        contract_id: string | null
        from: string | null
        to: string | null
        group: 'day' | 'week' | 'month'
        timezone: string
        date_basis: string
    }
    totals: QcDashboardEntry
    by_template: Array<QcDashboardEntry & { template: QcTemplateRef & { name_en: string; name_ar: string } }>
    by_criterion: Array<
        QcDashboardEntry & {
            template: QcTemplateRef
            criterion_id: string
            name_en: string | null
            name_ar: string | null
            human_listening_only: boolean
        }
    >
    by_period: Array<QcDashboardEntry & { period: string }>
    by_contract: Array<QcDashboardEntry & { contract: { id: string; name: string | null } | null }>
}

export type QcDashboardQuery = {
    template?: string
    template_version?: number
    contract_id?: string
    from?: string
    to?: string
    group?: 'day' | 'week' | 'month'
}

export type QcRecordingList = Paged<QcRecordingRow>
