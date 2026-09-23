/**
 * Maps v2 error codes to bilingual messages (keys under `p1.err.*` in
 * src/locales/lang/{ar,en}.json). Unknown codes fall back to a generic message
 * plus the server's own text, so nothing is silently swallowed.
 */
import { V2Error, isV2Error } from './http'

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export type Translate = (key: string, options?: any) => string

/** Codes that have their own translated message. */
const KNOWN = new Set([
    'network_error',
    'forbidden',
    'own_request',
    'not_found',
    'case_not_found',
    'payment_not_found',
    'promise_not_found',
    'allocation_not_found',
    'revision_conflict',
    'conflict',
    'stale_correction',
    'follow_up_not_open',
    'open_follow_up_exists',
    'case_archived',
    'case_locked',
    'case_missing',
    'validation_failed',
    'due_in_past',
    'due_date_in_past',
    'due_unchanged',
    'next_or_no_further_action',
    'idempotency_key_required',
    'idempotency_key_reused',
    'idempotency_request_in_progress',
    'invalid_json',
    'payment_already_decided',
    'payment_reversed',
    'payment_deleted',
    'invalid_amount',
    'amount_exceeds_balance',
    'payment_not_approved',
    'payment_already_reversed',
    'promise_not_cancellable',
    'payment_capacity_exceeded',
    'promise_capacity_exceeded',
    'payment_not_allocatable',
    'promise_not_allocatable',
    'case_mismatch',
    'nothing_to_allocate',
    'allocation_already_voided',
    'note_retired',
    'duplicate_contact',
    'contact_not_eligible',
    'replacement_default_required',
    'below_approved_payments',
    'account_number_taken',
    'correction_not_pending',
    'internal_error',
])

const flat = (detail: unknown): Record<string, string> => {
    const out: Record<string, string> = {}
    if (detail && typeof detail === 'object' && !Array.isArray(detail)) {
        Object.entries(detail as Record<string, unknown>).forEach(([k, v]) => {
            if (v === null || v === undefined) return
            if (typeof v === 'object') return
            out[k] = String(v)
        })
    }
    return out
}

/** "reason: required · amount: too many decimals" from a `{field: problem}` map. */
export function describeFieldErrors(detail: unknown, t: Translate): string {
    if (!detail || typeof detail !== 'object' || Array.isArray(detail)) return ''
    const parts: string[] = []
    const walk = (obj: Record<string, unknown>, prefix = '') => {
        Object.entries(obj).forEach(([field, problem]) => {
            const name = prefix ? `${prefix}.${field}` : field
            if (problem && typeof problem === 'object' && !Array.isArray(problem)) {
                walk(problem as Record<string, unknown>, name)
                return
            }
            const label = t(`p1.fields.${name.replace(/\./g, '_')}`, { defaultValue: name })
            const text = Array.isArray(problem)
                ? problem.join(', ')
                : t(`p1.err.problem.${String(problem)}`, { defaultValue: String(problem) })
            parts.push(`${label}: ${text}`)
        })
    }
    walk(detail as Record<string, unknown>)
    return parts.join(' · ')
}

/** One human sentence for a failed v2 call, in the current language. */
export function describeV2Error(err: unknown, t: Translate): string {
    if (!isV2Error(err)) {
        return t('p1.err.generic', { message: String((err as Error)?.message || '') })
    }
    const e = err as V2Error
    const values = flat(e.detail)
    if (e.code === 'validation_failed' || (e.status === 422 && !KNOWN.has(e.code))) {
        const fields = describeFieldErrors(e.detail, t)
        return fields ? t('p1.err.validation_failed_fields', { fields }) : t('p1.err.validation_failed')
    }
    if (KNOWN.has(e.code)) {
        return t(`p1.err.${e.code}`, { ...values, message: e.serverMessage })
    }
    return t('p1.err.generic', { message: e.serverMessage || e.code })
}
