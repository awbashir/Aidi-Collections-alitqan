/**
 * Bilingual messages for the Phase 6 codes (keys under `p6.err.*` and `p6.reason.*`): the AI
 * gateway's honest refusals, provider failures, the draft lifecycle and the quality workspace
 * (audio inspection, the evidence rule, approval). Codes the earlier phases already translate
 * (revision_conflict, forbidden, case_locked, validation_failed, ...) fall through to their
 * describers, so one error always gets exactly one sentence. The server's text is a fallback.
 */
import { describeV2Error } from './errors'
import type { Translate } from './errors'
import { isV2Error } from './http'
import { describeP5Error } from './p5Errors'
import { formatRiyadhDateTime } from '@/utils/v2/format'

/** The readiness / refusal reasons of the AI gateway (services/ai/gateway.py). */
export const AI_REFUSAL_CODES = new Set([
    'ai_not_configured',
    'model_not_set',
    'ai_policy_required',
    'ai_policy_category_denied',
    'ai_quota_exceeded',
])

/** Codes with their own `p6.err.<code>` message. */
export const P6_ERROR_CODES = new Set([
    // gateway / provider
    'ai_input_too_large',
    'ai_audio_too_large',
    'ai_audit_unavailable',
    'ai_provider_timeout',
    'ai_provider_unreachable',
    'ai_provider_unavailable',
    'ai_provider_rejected',
    'ai_provider_invalid_response',
    'ai_provider_incomplete',
    'ai_provider_rate_limited',
    'ai_provider_quota_exceeded',
    'ai_provider_auth_failed',
    'ai_provider_refused',
    // drafts
    'draft_not_found',
    'draft_saved',
    'draft_discarded',
    'draft_note_only',
    'transcript_required',
    'transcript_not_available',
    'ai_draft_no_source',
    // quality
    'recording_not_found',
    'template_not_found',
    'template_required',
    'approval_not_found',
    'audio_missing',
    'audio_purged',
    'already_approved',
    'duplicate_recording',
    'processing_superseded',
    'recording_processing',
    'audio_too_large',
    'unsupported_audio',
    'audio_too_long',
    'confirmation_required',
    'empty_file',
    'evidence_rule_failed',
    'human_review_required',
    'invalid_audio',
    'recording_path_required',
    'review_required',
    'transcript_empty',
    'transcript_timing_invalid',
    'audio_range_mismatch',
    'audio_size_unknown',
])

const flat = (detail: unknown): Record<string, string> => {
    const out: Record<string, string> = {}
    if (detail && typeof detail === 'object' && !Array.isArray(detail)) {
        Object.entries(detail as Record<string, unknown>).forEach(([k, v]) => {
            if (v === null || v === undefined || typeof v === 'object') return
            out[k] = String(v)
        })
    }
    return out
}

/** A data category in words (`p6.category.<c>`). */
export const categoryLabel = (t: Translate, c: string) => t(`p6.category.${c}`, { defaultValue: c.replace(/_/g, ' ') })

/** One readiness / refusal reason in words, with the refusal's detail when there is one. */
export function aiReasonText(code: string, detail: unknown, t: Translate): string {
    const d = (detail && typeof detail === 'object' ? detail : {}) as Record<string, unknown>
    if (code === 'ai_policy_category_denied') {
        const cats = Array.isArray(d.denied_categories) ? (d.denied_categories as string[]) : []
        return cats.length
            ? t('p6.reason.ai_policy_category_denied_list', { categories: cats.map((c) => categoryLabel(t, c)).join(String(t('p6.listSep'))) })
            : t('p6.reason.ai_policy_category_denied')
    }
    if (code === 'ai_quota_exceeded') {
        const scope = d.scope === 'total' ? 'total' : 'user'
        return t(`p6.reason.ai_quota_exceeded_${scope}`, {
            limit: d.limit ?? '—',
            used: d.used ?? '—',
            resets: formatRiyadhDateTime(d.resets_at as string | undefined),
        })
    }
    return t(`p6.reason.${code}`, { defaultValue: code.replace(/_/g, ' ') })
}

/** Every reason a refusal carries (`error.reasons`), falling back to its code. */
export function refusalReasons(err: unknown): string[] {
    if (!isV2Error(err)) return []
    const d = err.detail as { reasons?: unknown } | null
    const reasons = d && Array.isArray(d.reasons) ? (d.reasons as string[]) : []
    if (AI_REFUSAL_CODES.has(err.code) && !reasons.includes(err.code)) reasons.unshift(err.code)
    return reasons.filter((r) => typeof r === 'string')
}

/** True when the error is the gateway's honest "cannot call the provider" refusal. */
export const isAiRefusal = (err: unknown) => isV2Error(err) && AI_REFUSAL_CODES.has(err.code)

/** The evidence rule's per-criterion errors (`422 evidence_rule_failed`: `{criteria: {id: [codes]}}`). */
export function evidenceErrors(err: unknown): Record<string, string[]> | null {
    if (!isV2Error(err) || err.code !== 'evidence_rule_failed') return null
    const c = (err.detail as { criteria?: Record<string, string[]> } | null)?.criteria
    return c && typeof c === 'object' ? c : {}
}

/** An evidence-rule code in words (`p6.evidence.code.<code>`). */
export const evidenceCodeText = (t: Translate, code: string) =>
    t(`p6.evidence.code.${code}`, { defaultValue: code.replace(/_/g, ' ') })

/** One human sentence for a failed Phase 6 call. */
export function describeP6Error(err: unknown, t: Translate): string {
    if (!isV2Error(err)) return describeV2Error(err, t)
    const detail = err.detail
    if (AI_REFUSAL_CODES.has(err.code)) return aiReasonText(err.code, detail, t)
    if (P6_ERROR_CODES.has(err.code)) {
        const values = flat(detail)
        if (err.code === 'invalid_audio') {
            const reason = values.reason || ''
            values.detail = reason ? t(`p6.audioReason.${reason}`, { defaultValue: reason.replace(/_/g, ' ') }) : ''
        }
        if (err.code === 'evidence_rule_failed') {
            const crit = evidenceErrors(err) || {}
            values.count = String(Object.keys(crit).length)
        }
        return t(`p6.err.${err.code}`, { ...values, message: err.serverMessage })
    }
    if (err.status === 404 && err.serverMessage && /^Not Found/i.test(err.serverMessage)) return t('p5.err.route_missing')
    return describeP5Error(err, t)
}
