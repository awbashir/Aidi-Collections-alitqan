/**
 * Column-mapping helpers of the bulk wizard (step 3): header guesses and the
 * problems the wizard can see before the API checks the mapping again.
 */
import type { BulkKind, MappingField } from '@/services/v2'

export type AttrRow = { key: string; column: string }

const norm = (s: string) => s.toLowerCase().replace(/[\s_\-.:/]+/g, '')

/* Header names a field is likely to sit under (English and Arabic). */
const GUESSES: Record<string, string[]> = {
    account_number: ['account', 'accountnumber', 'accountno', 'acc', 'رقمالحساب', 'الحساب'],
    login: ['login', 'username', 'user', 'collector', 'اسمالمستخدم', 'المحصل'],
    identity_number: ['identity', 'identitynumber', 'id', 'nationalid', 'رقمالهوية', 'الهوية'],
    contact_number: ['mobile', 'phone', 'contact', 'contactnumber', 'رقمالجوال', 'الجوال', 'الهاتف'],
}

export function guessMapping(fields: MappingField[], headers: string[]): Record<string, string> {
    const out: Record<string, string> = {}
    fields.forEach((f) => {
        const wanted = new Set([norm(f.key), norm(f.label_en), norm(f.label_ar), ...(GUESSES[f.key] || [])])
        const hit = headers.find((h) => wanted.has(norm(h)))
        if (hit && !Object.values(out).includes(hit)) out[f.key] = hit
    })
    return out
}

/** Obvious matches are prefilled by the wizard when a sheet is chosen (`guessMapping`); every choice stays editable. */

/** The mapping problems the wizard knows before sending (the API checks again). */
export function mappingProblems(kind: BulkKind, mapping: Record<string, string>, attrs: AttrRow[]): string[] {
    const problems: string[] = []
    kind.mapping_fields.forEach((f) => {
        if (f.required && !mapping[f.key]) problems.push(`required:${f.key}`)
    })
    if (kind.match_by === null && kind.mapping_fields.some((f) => f.match) && !kind.mapping_fields.some((f) => f.match && mapping[f.key])) {
        problems.push('match')
    }
    if (kind.key === 'attribute_corrections') {
        const filled = attrs.filter((a) => a.key.trim() || a.column)
        if (filled.length === 0) problems.push('attributes')
        if (filled.some((a) => !a.key.trim() || !a.column)) problems.push('attributes_incomplete')
        const keys = filled.map((a) => a.key.trim())
        if (new Set(keys).size !== keys.length) problems.push('attributes_duplicate')
    }
    return problems
}
