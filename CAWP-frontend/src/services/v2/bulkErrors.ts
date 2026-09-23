/**
 * Bilingual messages for the Phase 2 bulk-job API (keys under `p2.*` in
 * src/locales/lang/{ar,en}.json). Codes the bulk API shares with Phase 1 fall
 * through to `describeV2Error` (`p1.err.*`).
 */
import { isV2Error } from './http'
import { describeV2Error } from './errors'
import type { Translate } from './errors'

/** API error codes (not row codes) with a `p2.err.*` message. */
export const BULK_ERROR_CODES = new Set([
    'file_too_large',
    'workbook_too_large',
    'not_a_workbook',
    'type_mismatch',
    'unsupported_file_type',
    'macro_not_allowed',
    'encrypted_workbook',
    'not_text',
    'not_utf8',
    'empty_file',
    'no_data',
    'unknown_kind',
    'portfolio_not_found',
    'upload_not_found',
    'upload_purged',
    'upload_expired',
    'upload_purpose_mismatch',
    'mapping_invalid',
    'job_not_ready',
    'preview_expired',
    'nothing_to_execute',
    'read_only_kind',
    'accept_partial_required',
    'job_not_cancellable',
    'job_not_retryable',
    'nothing_to_retry',
    'job_not_reopenable',
    'job_not_found',
    'artifact_not_found',
    'artifact_not_ready',
    'artifact_purged',
    'revision_conflict',
])

const scalars = (detail: unknown): Record<string, string> => {
    const out: Record<string, string> = {}
    if (detail && typeof detail === 'object' && !Array.isArray(detail)) {
        Object.entries(detail as Record<string, unknown>).forEach(([k, v]) => {
            if (v === null || v === undefined || typeof v === 'object') return
            out[k] = String(v)
        })
    }
    return out
}

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

/** "Reason: required · Collectors (…0504): outside your scope" from a nested `{field: problem}` map. */
export function describeBulkFields(detail: unknown, t: Translate): string {
    if (!detail || typeof detail !== 'object' || Array.isArray(detail)) return ''
    const parts: string[] = []
    const label = (name: string) =>
        t(`p2.field.${name}`, { defaultValue: t(`p1.fields.${name}`, { defaultValue: name }) })
    const problem = (code: string) =>
        t(`p2.problem.${code}`, { defaultValue: t(`p1.err.problem.${code}`, { defaultValue: code }) })
    const walk = (obj: Record<string, unknown>, parent?: string) => {
        Object.entries(obj).forEach(([field, value]) => {
            if (field === 'headers' || field === 'allowed' || field === 'max_rows' || field === 'kinds') return
            if (value && typeof value === 'object' && !Array.isArray(value)) {
                walk(value as Record<string, unknown>, field)
                return
            }
            if (Array.isArray(value)) return
            const name = UUID.test(field) && parent ? `${label(parent)} (…${field.slice(-4)})` : parent && !['params', 'fields', 'error'].includes(parent) ? `${label(parent)} · ${label(field)}` : label(field)
            parts.push(`${name}: ${problem(String(value))}`)
        })
    }
    walk(detail as Record<string, unknown>)
    return parts.join(' · ')
}

/** One sentence for a failed bulk call, in the current language. */
export function describeBulkError(err: unknown, t: Translate): string {
    if (!isV2Error(err)) return describeV2Error(err, t)
    const e = err
    if (e.code === 'validation_failed' || e.code === 'mapping_invalid') {
        const fields = describeBulkFields(e.detail, t)
        if (fields) return t('p2.err.fields', { fields })
    }
    if (e.code === 'revision_conflict') {
        return t('p2.err.revision_conflict', { ...scalars(e.detail) })
    }
    if (BULK_ERROR_CODES.has(e.code)) {
        const values = scalars(e.detail)
        if (values.status) values.status = t(`p2.status.${values.status}`, { defaultValue: values.status })
        return t(`p2.err.${e.code}`, { ...values, message: e.serverMessage })
    }
    return describeV2Error(err, t)
}

/** Bilingual label for a row eligibility / outcome code; the server's message is the fallback. */
export const bulkCodeLabel = (t: Translate, code: string | null | undefined, message?: string | null) =>
    code ? t(`p2.code.${code}`, { defaultValue: message || code }) : ''
