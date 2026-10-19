/**
 * Previous statuses import ("استيراد الحالات السابقة") endpoints, checked against
 * CAWP-API/api/previous_status_import.py (migration 0074).
 *
 * Every function returns the envelope's `data` or throws `V2Error` (./http.ts). Mutations take the
 * caller's per-action Idempotency-Key. Preview writes nothing but is a POST (the body carries the
 * mapping and options); it still sends a key because every v2 POST does.
 * Gate: settings_comments_status_add AND the SuperAdmin role (403 `super_admin_required`).
 */
import BaseService from '../BaseService'
import { V2Error, v2Get, v2Post, v2Request } from './http'
import type { BulkUpload } from './bulkTypes'

const enc = encodeURIComponent

export type PrevStatusField = 'account_number' | 'main_status' | 'sub_status'
export type PrevStatusMapping = Partial<Record<PrevStatusField, string>>

export type PrevStatusUpload = BulkUpload & { guess: PrevStatusMapping; guess_sheet: string }

export type PrevStatusOptions = {
    fields: Array<{ key: PrevStatusField; label_en: string; label_ar: string; required: boolean }>
    contact_methods: Array<{ id: string; name: string }>
    default_contact_method_id: string | null
    /** YYYY-MM-DD (Asia/Riyadh); the default is 2026-10-11, the day before go-live */
    default_noted_on: string
    /** yesterday (Asia/Riyadh): the latest day the notes may carry */
    latest_noted_on: string
    marker: string
    note_max: number
    sheet?: string
    headers?: string[]
    guess?: PrevStatusMapping
}

export type PrevStatusSkipCode =
    | 'no_status'
    | 'missing_account'
    | 'formula_not_allowed'
    | 'value_too_long'
    | 'duplicate_in_file'
    | 'account_not_found'
    | 'account_ambiguous'
    | 'case_archived'
    | 'case_locked'
    | 'status_not_found'
    | 'status_ambiguous'
    | 'sub_status_not_found'
    | 'sub_status_ambiguous'
    | 'already_imported'

export type PrevStatusCounts = Partial<Record<PrevStatusSkipCode, number>> & {
    total: number
    /** preview: rows that will be imported; a finished import has `imported` instead */
    ready?: number
    imported?: number
    skipped: number
    ready_with_sub_status?: number
    ready_creating_sub_status?: number
    sub_statuses_to_create?: number
    sub_statuses_created?: number
    missing_main_statuses?: number
}

export type PrevStatusProblem = {
    row_number: number
    account_number: string | null
    main_status: string | null
    sub_status: string | null
    /** a skip code, or "ready" for a ready row that creates its sub-status */
    code: PrevStatusSkipCode | 'ready'
    message: string
    case_id: string | null
    creates_sub_status: boolean
}

export type PrevStatusPreviewOptions = { create_missing_sub_statuses: boolean; include_archived: boolean }

export type PrevStatusPreview = {
    counts: PrevStatusCounts
    problems: PrevStatusProblem[]
    problem_limit: number
    sub_statuses_to_create: Array<{ main_id: string; main_name: string | null; name: string; rows: number }>
    missing_main_statuses: Array<{ name: string; rows: number }>
    missing_sub_statuses: Array<{ main_name: string | null; name: string; rows: number }>
    blocking: Array<{ code: string; import_id?: string }>
    can_apply: boolean
    mapping: PrevStatusMapping
    options: PrevStatusPreviewOptions
    sheet: string
    import: PrevStatusImport | null
}

export type PrevStatusImport = {
    id: string
    upload_id: string
    sheet: string | null
    mapping: PrevStatusMapping
    options: Partial<PrevStatusPreviewOptions> & {
        noted_on?: string
        noted_at?: string
        contact_method_id?: string
        note?: string | null
    }
    status: 'queued' | 'running' | 'succeeded' | 'failed'
    counts: Partial<PrevStatusCounts>
    created_sub_statuses: Array<{ id: string; name: string; parent_id: string; parent_name: string | null; rows: number }>
    error: { code: string; message: string | null } | null
    attempts: number
    created_by: string
    created_at: string
    updated_at: string
    started_at: string | null
    finished_at: string | null
    report_available: boolean
    original_name?: string
}

export type PrevStatusBody = PrevStatusPreviewOptions & {
    upload_id: string
    sheet?: string
    mapping: PrevStatusMapping
}

export type PrevStatusApplyBody = PrevStatusBody & {
    noted_on: string
    contact_method_id?: string
    note?: string
}

const BASE = '/v2/previous-status-imports'

export const previousStatusImportApi = {
    upload: (file: File, key: string) => {
        const form = new FormData()
        form.append('file', file)
        return v2Request<PrevStatusUpload>(`${BASE}/uploads`, { method: 'post', data: form, idempotencyKey: key })
    },

    options: (params?: { upload_id?: string; sheet?: string }, signal?: AbortSignal) =>
        v2Get<PrevStatusOptions>(`${BASE}/options`, params, signal),

    preview: (body: PrevStatusBody, key: string) => v2Post<PrevStatusPreview>(`${BASE}/preview`, body, key),

    apply: (body: PrevStatusApplyBody, key: string) => v2Post<PrevStatusImport>(`${BASE}/apply`, body, key),

    get: (id: string, signal?: AbortSignal) => v2Get<PrevStatusImport>(`${BASE}/${enc(id)}`, undefined, signal),
}

/** Downloads the XLSX result report (skipped rows with reasons, summary, created sub-statuses). */
export async function downloadPrevStatusReport(importId: string): Promise<void> {
    let blob: Blob
    try {
        const res = await BaseService({
            url: `${BASE}/${enc(importId)}/report`,
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
    a.download = `previous-statuses-${importId.slice(0, 8)}.xlsx`
    a.rel = 'noopener'
    document.body.appendChild(a)
    a.click()
    a.remove()
    setTimeout(() => URL.revokeObjectURL(url), 10000)
}
