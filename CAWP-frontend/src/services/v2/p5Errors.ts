/**
 * Bilingual messages for the Phase 5 error and refusal codes (keys under `p5.err.*` and
 * `p5.refusal.*`). Codes the earlier phases already translate (revision_conflict, forbidden,
 * not_found, replacement_default_required, amount_exceeds_balance, invalid_transition...)
 * fall through to their describers, so one error always gets exactly one sentence. The
 * server's own text is only a fallback.
 */
import { describeV2Error } from './errors'
import type { Translate } from './errors'
import { isV2Error } from './http'
import { describeP3Error } from './p3Errors'
import { formatRiyadhDateTime } from '@/utils/v2/format'

/**
 * The click-to-call dispatch rules (services/calling/rules.py), in the order the API checks
 * them. The same codes explain why a contact cannot be called before anyone clicks.
 */
export const DISPATCH_RULES = [
    'actor_inactive',
    'forbidden',
    'no_extension',
    'not_found',
    'case_archived',
    'case_locked',
    'do_not_call',
    'calling_review_pending',
    'contact_not_found',
    'contact_deleted',
    'contact_blocked',
    'destination_invalid',
    'case_paid',
    'callback_scheduled',
    'calling_windows_missing',
    'calling_window_closed',
    'calling_holiday',
    'calling_reserved',
    'transport_not_connected',
] as const
export type DispatchRule = (typeof DISPATCH_RULES)[number]
const RULES = new Set<string>(DISPATCH_RULES)

/** Codes with their own `p5.err.<code>` message. */
export const P5_ERROR_CODES = new Set([
    // cancel / live
    'already_dispatching',
    'call_not_found',
    // wrap-up and manual log
    'conversation_not_evidenced',
    'wrapup_exists',
    'wrapup_not_allowed',
    'promise_discussed_is_not_a_promise',
    'promise_not_agreed',
    'promise_not_allowed',
    'contact_required',
    // supervisor review
    'not_uncertain',
    'call_still_live',
    'flag_not_found',
    'flag_already_resolved',
    // calling admin
    'owner_changed',
    'already_assigned',
    'extension_owned',
    'user_has_extension',
    'extension_in_use',
    'user_inactive',
    'extension_not_owned',
    'legacy_extension_invalid',
    'legacy_extension_changed',
    'block_not_found',
    'block_already_released',
    // campaigns
    'no_members',
    'campaign_stopped',
    'campaign_not_running',
    'member_inactive',
    'ready_in_other_campaign',
    'call_in_progress',
    'member_not_allowed',
    // transport
    'idempotency_request_in_progress',
])

/**
 * Calling supervision codes (2026-10-17) with their own `p8.calling.err.<code>` message:
 * do-not-call is a supervisor decision, supervisor release of a stuck reservation, blocking a
 * client, and the legacy delete/reassign guards for cases with an open call.
 */
export const P8_CALLING_ERROR_CODES = new Set([
    'outcome_not_allowed',
    'release_not_allowed',
    'reservation_not_held',
    'block_exists',
    'case_has_open_call',
])

/** The promise-to-pay rule (2026-10-20, migration 0079) with its own `p10.promise.err.<code>` message. */
export const PROMISE_RULE_CODES = new Set(['promise_required', 'promise_needs_conversation', 'promise_not_linkable'])

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

/**
 * Why a call cannot be placed, from a dispatch rule code and its detail (the API's
 * `error` object, or what the screen knows before the click). Always one sentence.
 */
export function refusalText(code: string, detail: unknown, t: Translate): string {
    const d = (detail && typeof detail === 'object' ? detail : {}) as Record<string, unknown>
    const values: Record<string, string> = { ...flat(d) }
    if (code === 'callback_scheduled') {
        values.due = formatRiyadhDateTime(d.due_at as string | undefined)
        values.from = formatRiyadhDateTime(d.callable_from as string | undefined)
    }
    if (code === 'calling_reserved') {
        const which = String(d.reserved || '')
        return t(`p5.refusal.calling_reserved_${['collector', 'case', 'extension'].includes(which) ? which : 'any'}`)
    }
    if (code === 'no_extension' && d.extension_changed) return t('p5.refusal.extension_changed')
    return t(`p5.refusal.${RULES.has(code) ? code : 'unknown'}`, { ...values, code })
}

/** "Note text: required · Started at: in the future" for the Phase 5 forms. */
export function describeP5Fields(detail: unknown, t: Translate): string {
    if (!detail || typeof detail !== 'object' || Array.isArray(detail)) return ''
    const label = (field: string) => {
        const k = field.replace(/\[\d+\]/g, '').replace(/\./g, '_')
        return t(`p5.fields.${k}`, { defaultValue: t(`p1.fields.${k}`, { defaultValue: field }) })
    }
    const problem = (p: unknown) => {
        const key = String(p)
        return t(`p5.problem.${key}`, { defaultValue: t(`p1.err.problem.${key}`, { defaultValue: key }) })
    }
    const parts: string[] = []
    Object.entries(detail as Record<string, unknown>).forEach(([field, value]) => {
        if (['rule', 'candidates', 'required_any', 'user_id', 'attempt_id', 'reason'].includes(field)) return
        if (value && typeof value === 'object' && !Array.isArray(value)) return
        parts.push(`${label(field)}: ${Array.isArray(value) ? value.join(', ') : problem(value)}`)
    })
    return parts.join(' · ')
}

/**
 * One human sentence for a failed Phase 5 call. `area: 'dispatch'` words the rule codes
 * shared with other phases (case_locked, not_found, forbidden...) as "cannot be called".
 */
export function describeP5Error(err: unknown, t: Translate, area?: 'dispatch'): string {
    if (!isV2Error(err)) return describeV2Error(err, t)
    const detail = err.detail as Record<string, unknown> | null
    const rule = detail && typeof detail === 'object' ? (detail.rule as string | undefined) : undefined
    if (rule && RULES.has(rule)) return refusalText(rule, detail, t)
    if (area === 'dispatch' && RULES.has(err.code) && err.code !== 'forbidden') return refusalText(err.code, detail, t)
    if (PROMISE_RULE_CODES.has(err.code)) return t(`p10.promise.err.${err.code}`)
    if (P8_CALLING_ERROR_CODES.has(err.code)) {
        const values = flat(detail)
        if (err.code === 'release_not_allowed' && values.state === 'uncertain') return t('p8.calling.err.release_uncertain')
        return t(`p8.calling.err.${err.code}`, { ...values, message: err.serverMessage })
    }
    if (P5_ERROR_CODES.has(err.code)) {
        const values = flat(detail)
        if (err.code === 'ready_in_other_campaign') values.campaign = values.campaign_id || ''
        return t(`p5.err.${err.code}`, { ...values, message: err.serverMessage })
    }
    if (err.code === 'validation_failed' || (err.status === 422 && !P5_ERROR_CODES.has(err.code))) {
        const fields = describeP5Fields(err.detail, t)
        if (err.code === 'validation_failed' || fields) {
            return fields ? t('p1.err.validation_failed_fields', { fields }) : t('p1.err.validation_failed')
        }
    }
    if (err.code === 'not_found' && err.status === 404) {
        /* A route missing on the running API (404 without a v2 envelope) reads differently
         * from "this record is not visible to you". */
        return err.serverMessage && /^Not Found/i.test(err.serverMessage)
            ? t('p5.err.route_missing')
            : t('p5.err.not_found')
    }
    return describeP3Error(err, t)
}

/** Bound to one area, for `ActionDialog describeError` / `ErrorState describe`. */
export const describeDispatchError = (err: unknown, t: Translate) => describeP5Error(err, t, 'dispatch')
