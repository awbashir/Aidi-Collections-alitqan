/**
 * Portfolio upload ("رفع المحافظ") endpoints, checked against CAWP-API/api/portfolio_import.py.
 *
 * Every function returns the envelope's `data` or throws `V2Error` (./http.ts).
 * Mutations take the caller's per-action Idempotency-Key. Preview writes nothing but is a POST
 * (the body carries the mapping); it still sends a key because every v2 POST does.
 */
import BaseService from '../BaseService'
import { V2Error, v2Get, v2Post, v2Request } from './http'
import type { BulkUpload } from './bulkTypes'
import type { FollowUpStatus, PaymentStatus, PromiseState } from './types'

const enc = encodeURIComponent

export type ImportFieldKey = 'name' | 'identity_number' | 'account_number' | 'amount' | 'collector_username'

export type ImportField = { key: ImportFieldKey; label_en: string; label_ar: string; required: boolean }
export type ContactType = { id: string; name: string }
export type ContactMapping = { column: string; contact_type_id: string }
export type ImportMapping = { fields: Partial<Record<ImportFieldKey, string>>; contacts: ContactMapping[] }

export type ImportUpload = BulkUpload & { guess: ImportMapping; guess_sheet: string }

export type ImportCatalogue = {
    fields: ImportField[]
    contact_types: ContactType[]
    sheet?: string
    headers?: string[]
    guess?: ImportMapping
}

export type ImportOutcome = 'created' | 'created_from_archive' | 'skipped_active_exists' | 'rejected'

export type ImportWarning = { code: string; value?: string; previous?: string; column?: string; amount?: string }

export type ImportCounts = {
    total: number
    created: number
    created_from_archive: number
    skipped_active_exists: number
    rejected: number
    to_create: number
    assigned: number
    collector_not_matched: number
    /** rows whose amount had more than 2 decimals and was rounded half-up */
    amounts_rounded?: number
    warnings: Record<string, number>
    contacts_new: number
    contacts_existing: number
}

export type ImportProblem = {
    row_number: number
    account_number: string | null
    outcome: ImportOutcome
    code: string
    message: string
    detail: string | null
    warnings: ImportWarning[]
    case_id: string | null
    previous_case_id: string | null
}

export type PortfolioImport = {
    id: string
    upload_id: string
    sheet: string | null
    contract_id: string
    portfolio_name: string
    portfolio_id: string | null
    mapping: ImportMapping
    status: 'queued' | 'running' | 'succeeded' | 'failed'
    counts: Partial<ImportCounts>
    error: { code: string; message: string | null } | null
    attempts: number
    created_by: string
    created_at: string
    updated_at: string
    started_at: string | null
    finished_at: string | null
    report_available: boolean
    original_name?: string
    contract_name?: string | null
    portfolio?: { id: string; name: string } | null
}

export type ImportPreview = {
    counts: ImportCounts
    problems: ImportProblem[]
    problem_limit: number
    blocking: Array<{ code: string; portfolio?: { id: string; name: string }; import_id?: string }>
    can_commit: boolean
    mapping: ImportMapping
    sheet: string
    import: PortfolioImport | null
}

export type ImportBody = {
    upload_id: string
    sheet?: string
    contract_id: string
    portfolio_name: string
    mapping: ImportMapping
}

export type ImportRow = {
    row_number: number
    account_number: string | null
    outcome: ImportOutcome
    code: string
    message: string | null
    case_id: string | null
    previous_case_id: string | null
    warnings: ImportWarning[]
}

/* --------------------------------------------------------------- previous records */
export type PrevPerson = { id: string; name: string | null } | null

export type PrevNote = {
    id: string
    author: PrevPerson
    created_at: string
    text: string
    status: { id: string | null; name: string | null }
    /** 0068: the optional sub-status (null when the note has none) */
    sub_status?: { id: string | null; name: string | null } | null
    contact_method: { id: string | null; name: string | null }
    next_contact_date: string | null
    corrected: boolean
}

export type PrevPayment = {
    id: string
    status: PaymentStatus
    amount: string
    payment_date: string | null
    payment_civil_date: string | null
    created_at: string | null
    recorder: PrevPerson
    collection_type: { id: string; name: string | null } | null
    collection_method: { id: string; name: string | null } | null
    decision: { approved: boolean; by: PrevPerson; at: string | null; comment: string | null } | null
    reversal: { id: string; amount: string; reason: string | null; by: PrevPerson; at: string | null } | null
}

export type PrevPromise = {
    id: string
    state: PromiseState
    due_date: string
    amount: string
    allocated: string
    recorder: PrevPerson
    created_at: string
}

export type PrevFollowUp = {
    id: string
    status: FollowUpStatus
    due_at: string
    due_date: string
    next_action: string | null
    created_at: string
    completed_at: string | null
    close_reason: string | null
}

export type PreviousRecord = {
    depth: number
    case_id: string
    linked_from_case_id: string
    exists: boolean
    name: string | null
    account_number: string | null
    original_account_number: string
    renamed_to: string
    identity_number: string | null
    original_amount: string | null
    is_archived: boolean | null
    created_at: string | null
    linked_at: string
    import_id: string | null
    portfolio: { id: string; name: string } | null
    contract: { id: string; name: string } | null
    archive: { at: string; reason: string | null; by: PrevPerson } | null
    notes: PrevNote[] | null
    payments: PrevPayment[] | null
    payment_counts?: Partial<Record<PaymentStatus, number>>
    promises: PrevPromise[] | null
    follow_ups: PrevFollowUp[] | null
}

export type PreviousRecords = {
    case_id: string
    has_previous: boolean
    permitted: Record<'notes' | 'payments' | 'promises' | 'follow_ups', boolean>
    records: PreviousRecord[]
}

export const portfolioImportApi = {
    upload: (file: File, key: string) => {
        const form = new FormData()
        form.append('file', file)
        return v2Request<ImportUpload>('/v2/portfolio-imports/uploads', { method: 'post', data: form, idempotencyKey: key })
    },

    fields: (params?: { upload_id?: string; sheet?: string }, signal?: AbortSignal) =>
        v2Get<ImportCatalogue>('/v2/portfolio-imports/fields', params, signal),

    preview: (body: ImportBody, key: string) => v2Post<ImportPreview>('/v2/portfolio-imports/preview', body, key),

    commit: (body: ImportBody, key: string) => v2Post<PortfolioImport>('/v2/portfolio-imports/commit', body, key),

    get: (id: string, signal?: AbortSignal) => v2Get<PortfolioImport>(`/v2/portfolio-imports/${enc(id)}`, undefined, signal),

    rows: (id: string, q: { outcome?: string; page?: number; page_size?: number }, signal?: AbortSignal) =>
        v2Get<{ items: ImportRow[]; total: number; page: number; page_size: number }>(
            `/v2/portfolio-imports/${enc(id)}/rows`,
            q,
            signal
        ),

    previousRecords: (caseId: string, signal?: AbortSignal) =>
        v2Get<PreviousRecords>(`/v2/cases/${enc(caseId)}/previous-records`, undefined, signal),
}

/** Downloads the XLSX import report (Arabic labels) with the bearer token. */
export async function downloadImportReport(importId: string): Promise<void> {
    let blob: Blob
    try {
        const res = await BaseService({
            url: `/v2/portfolio-imports/${enc(importId)}/report`,
            method: 'get',
            responseType: 'blob',
            timeout: 0,
        })
        blob = res.data as Blob
    } catch (err) {
        const ax = err as { response?: { status: number; data?: Blob } }
        if (ax?.response) {
            let body: { code?: string; message?: string; error?: unknown } = {}
            try {
                body = JSON.parse(await (ax.response.data as Blob).text())
            } catch {
                /* not JSON */
            }
            throw new V2Error(ax.response.status, body.code || 'unknown', body.message || '', body.error ?? null)
        }
        throw new V2Error(0, 'network_error', (err as Error)?.message || 'network error', null)
    }
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `portfolio-import-${importId.slice(0, 8)}.xlsx`
    a.rel = 'noopener'
    document.body.appendChild(a)
    a.click()
    a.remove()
    setTimeout(() => URL.revokeObjectURL(url), 10000)
}
