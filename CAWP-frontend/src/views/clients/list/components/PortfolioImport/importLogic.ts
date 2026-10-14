/**
 * Pure helpers of the portfolio upload wizard: the mapping problems the wizard can see before
 * the API checks the mapping again, and the error / code texts.
 */
import { describeV2Error, isV2Error } from '@/services/v2'
import type { Translate } from '@/services/v2'
import type { ImportField, ImportMapping, ImportWarning } from '@/services/v2/portfolioImportApi'

export const EMPTY_MAPPING: ImportMapping = { fields: {}, contacts: [] }

/** Problems shown under the mapping step: `required:<field>`, `twice:<column>`, `contact_incomplete`. */
export function mappingProblems(fields: ImportField[], mapping: ImportMapping): string[] {
    const problems: string[] = []
    fields.forEach((f) => {
        if (f.required && !mapping.fields[f.key]) problems.push(`required:${f.key}`)
    })
    const used = [
        ...Object.values(mapping.fields).filter((c): c is string => !!c),
        ...mapping.contacts.map((c) => c.column).filter(Boolean),
    ]
    const seen = new Set<string>()
    used.forEach((c) => {
        if (seen.has(c) && !problems.includes(`twice:${c}`)) problems.push(`twice:${c}`)
        seen.add(c)
    })
    if (mapping.contacts.some((c) => !c.column || !c.contact_type_id)) problems.push('contact_incomplete')
    return problems
}

/** The mapping as the API expects it: no empty field entries, complete contact rows only. */
export function cleanMapping(mapping: ImportMapping): ImportMapping {
    const fields: ImportMapping['fields'] = {}
    Object.entries(mapping.fields).forEach(([k, v]) => {
        if (v) fields[k as keyof ImportMapping['fields']] = v
    })
    return { fields, contacts: mapping.contacts.filter((c) => c.column && c.contact_type_id) }
}

/** Keeps only guessed columns that exist in `headers` (a sheet change re-guesses on the server). */
export function guessFor(guess: ImportMapping | undefined, headers: string[]): ImportMapping {
    if (!guess) return EMPTY_MAPPING
    const set = new Set(headers)
    const fields: ImportMapping['fields'] = {}
    Object.entries(guess.fields || {}).forEach(([k, v]) => {
        if (v && set.has(v)) fields[k as keyof ImportMapping['fields']] = v
    })
    return { fields, contacts: (guess.contacts || []).filter((c) => set.has(c.column)) }
}

const KNOWN_ERRORS = new Set([
    'invalid_mapping',
    'already_imported',
    'import_in_progress',
    'portfolio_name_exists',
    'nothing_to_import',
    'upload_purged',
    'upload_expired',
    'upload_not_found',
    'upload_purpose_mismatch',
    'import_failed',
    'cases_changed',
    'file_changed',
    'too_many_rows',
    'report_not_ready',
    'file_too_large',
    'unsupported_file_type',
    'not_a_workbook',
    'type_mismatch',
    'not_utf8',
    'empty_file',
    'no_data',
])

/** Text for a failed call of this wizard (falls back to the shared v2 messages). */
export function describeImportError(error: unknown, t: Translate): string {
    if (isV2Error(error) && KNOWN_ERRORS.has(error.code)) return t(`p7.import.err.${error.code}`)
    return describeV2Error(error, t)
}

/** Text for an import's stored failure `{code, message}`. */
export function describeFailure(err: { code: string; message: string | null } | null, t: Translate): string {
    if (!err) return ''
    if (KNOWN_ERRORS.has(err.code)) return t(`p7.import.err.${err.code}`)
    return t('p7.import.err.generic', { message: err.message || err.code })
}

/** Row code label (falls back to the server's Arabic message). */
export function codeLabel(code: string, message: string | null, t: Translate): string {
    const key = `p7.import.code.${code}`
    const text = t(key)
    return text === key ? message || code : text
}

export function warningLabel(w: ImportWarning, t: Translate): string {
    const key = `p7.import.warning.${w.code}`
    const text = t(key)
    const label = text === key ? w.code : text
    const value = w.value || w.previous || w.column
    return value ? `${label}: ${value}` : label
}
