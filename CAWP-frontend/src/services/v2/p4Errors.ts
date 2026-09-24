/**
 * Bilingual messages for the Phase 4 report API (keys under `p4.err.*`,
 * `p4.fieldErr.*` and `p4.exportError.*`). Codes Phase 1 already translates
 * (forbidden, network_error, internal_error, ...) fall through to
 * `describeV2Error`, so an error always gets exactly one sentence.
 *
 * A 422 `validation_failed` from the report endpoints carries `error =
 * {param: code}` listing every bad parameter at once (codes: invalid, required,
 * reversed_range, range_too_long, too_long, too_many, unknown_value, negative,
 * too_precise, too_large, unknown_parameter). The viewer shows each one under
 * its own filter; `describeReportError` also names them in one line.
 */
import { describeV2Error } from './errors'
import type { Translate } from './errors'
import { isV2Error } from './http'

/** Codes with their own `p4.err.<code>` message. */
export const P4_ERROR_CODES = new Set([
    'report_not_found',
    'unknown_option_field',
    'export_not_found',
    'export_not_ready',
    'export_failed',
    'export_expired',
    'export_purged',
    'cdr_unavailable',
    'cdr_window_too_large',
])

/** Field problem codes the report framework emits (`p4.fieldErr.<code>`). */
export const P4_FIELD_CODES = [
    'invalid',
    'required',
    'reversed_range',
    'range_too_long',
    'too_long',
    'too_many',
    'unknown_value',
    'negative',
    'too_precise',
    'too_large',
    'unknown_parameter',
] as const

/** Export failure codes (`error.code` of a failed export, `p4.exportError.<code>`). */
export const P4_EXPORT_FAILURES = [
    'permission_denied',
    'too_many_rows',
    'artifact_too_large',
    'unknown_report',
    'invalid_definition',
    'row_count_mismatch',
    'internal_error',
    'max_attempts_exceeded',
    'cdr_unavailable',
    'lease_lost',
] as const

/** `{param: code}` of a 422 validation_failed, else null. */
export function reportFieldErrors(err: unknown): Record<string, string> | null {
    if (!isV2Error(err) || err.status !== 422 || err.code !== 'validation_failed') return null
    const d = err.detail
    if (!d || typeof d !== 'object' || Array.isArray(d)) return null
    const out: Record<string, string> = {}
    Object.entries(d as Record<string, unknown>).forEach(([k, v]) => {
        if (v !== null && v !== undefined && typeof v !== 'object') out[k] = String(v)
    })
    return out
}

/** One field problem in the current language ("The end is before the start"). */
export function fieldErrorText(code: string, t: Translate, values: Record<string, unknown> = {}): string {
    return t(`p4.fieldErr.${code}`, { defaultValue: t('p4.fieldErr.other', { code }), ...values })
}

/** An export's failure code in words. */
export function exportFailureText(code: string | null | undefined, t: Translate, message?: string | null): string {
    if (!code) return ''
    return t(`p4.exportError.${code}`, { defaultValue: t('p4.exportError.other', { code, message: message || '' }) })
}

/**
 * One sentence for a failed report call. `paramLabel` turns a query parameter
 * name into the filter's label in the current language (the viewer knows the
 * definition); without it the parameter name is shown.
 */
export function describeReportError(err: unknown, t: Translate, paramLabel?: (param: string) => string): string {
    if (!isV2Error(err)) return describeV2Error(err, t)
    const fields = reportFieldErrors(err)
    if (fields) {
        const parts = Object.entries(fields).map(
            ([param, code]) => `${paramLabel ? paramLabel(param) : param}: ${fieldErrorText(code, t)}`
        )
        return parts.length ? t('p4.err.validation_failed_fields', { fields: parts.join(' · ') }) : t('p4.err.validation_failed')
    }
    if (P4_ERROR_CODES.has(err.code)) {
        const detail = (err.detail && typeof err.detail === 'object' ? err.detail : {}) as Record<string, unknown>
        const code = typeof detail.code === 'string' ? detail.code : ''
        return t(`p4.err.${err.code}`, {
            status: typeof detail.status === 'string' ? t(`p4.status.${detail.status}`, { defaultValue: detail.status }) : '',
            reason: code ? exportFailureText(code, t) : '',
            message: err.serverMessage,
        })
    }
    return describeV2Error(err, t)
}
