/**
 * Bilingual messages for the Phase 3 error codes (keys under `p3.err.*`).
 * Codes Phase 1 already translates (revision_conflict, forbidden, own_request,
 * not_found, case_locked, ...) fall through to `describeV2Error`, so one error
 * always gets exactly one sentence. The server's own text is only a fallback.
 */
import { describeV2Error } from './errors'
import type { Translate } from './errors'
import { isV2Error } from './http'

/** Codes with their own `p3.err.<code>` message. */
export const P3_ERROR_CODES = new Set([
    // requests
    'invalid_transition',
    'request_cancelled',
    // plans
    'start_date_in_past',
    'start_date_too_far',
    'installment_too_small',
    'too_many_installments',
    'installment_amount_exceeds_total',
    'plan_already_active',
    'plan_not_cancellable',
    'plan_not_allocatable',
    'plan_not_found',
    'installment_not_found',
    'installment_capacity_exceeded',
    'plan_amount_exceeds_balance',
    'plan_payment_not_allocatable',
    'plan_nothing_to_allocate',
    'plan_case_mismatch',
    // admin
    'month_locked',
    'already_inactive',
    'already_active',
    'cannot_deactivate_self',
    'requester_only',
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

/** "Subject: required · Documents (4d33…): from another case". */
export function describeP3Fields(detail: unknown, t: Translate): string {
    if (!detail || typeof detail !== 'object' || Array.isArray(detail)) return ''
    const label = (field: string) =>
        t(`p3.fields.${field}`, { defaultValue: t(`p1.fields.${field}`, { defaultValue: field }) })
    const problem = (p: unknown) => {
        const key = String(p)
        return t(`p3.problem.${key}`, { defaultValue: t(`p1.err.problem.${key}`, { defaultValue: key }) })
    }
    const parts: string[] = []
    Object.entries(detail as Record<string, unknown>).forEach(([field, value]) => {
        if (value && typeof value === 'object' && !Array.isArray(value)) {
            // {"document_ids": {"<id>": "other_case"}}
            Object.entries(value as Record<string, unknown>).forEach(([id, p]) => {
                parts.push(`${label(field)} (${id.slice(0, 8)}…): ${problem(p)}`)
            })
            return
        }
        parts.push(`${label(field)}: ${Array.isArray(value) ? value.join(', ') : problem(value)}`)
    })
    return parts.join(' · ')
}

/**
 * One human sentence for a failed Phase 3 call. `area` picks the wording for
 * codes shared with Phase 1 whose Phase 1 message talks about promises.
 */
export function describeP3Error(err: unknown, t: Translate, area?: 'plan'): string {
    if (!isV2Error(err)) return describeV2Error(err, t)
    const values = flat(err.detail)
    if (err.code === 'validation_failed' || (err.status === 422 && !P3_ERROR_CODES.has(err.code) && err.code !== 'case_mismatch')) {
        const fields = describeP3Fields(err.detail, t)
        if (fields) return t('p1.err.validation_failed_fields', { fields })
    }
    if (err.code === 'forbidden' && (err.detail as { requester_only?: boolean } | null)?.requester_only) {
        return t('p3.err.requester_only')
    }
    const planCode = area === 'plan' ? `plan_${err.code}` : ''
    if (P3_ERROR_CODES.has(err.code) || P3_ERROR_CODES.has(planCode)) {
        const extra: Record<string, string> = {}
        const d = err.detail as { allowed_from?: string[] } | null
        if (d?.allowed_from) extra.allowed_from = d.allowed_from.map((s) => t(`p3.req.status.${s}`)).join(', ')
        if (values.status)
            extra.status_label = t(`p3.req.status.${values.status}`, {
                defaultValue: t(`p1.paymentStatus.${values.status}`, { defaultValue: values.status }),
            })
        if (values.action) extra.action_label = t(`p3.req.action.${values.action}`, { defaultValue: values.action })
        if (values.state) extra.state_label = t(`p3.plan.state.${values.state}`, { defaultValue: values.state })
        return t(`p3.err.${P3_ERROR_CODES.has(planCode) ? planCode : err.code}`, { ...values, ...extra, message: err.serverMessage })
    }
    return describeV2Error(err, t)
}
