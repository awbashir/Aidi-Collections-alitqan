/**
 * Phase 2 endpoints: private uploads and reviewed bulk jobs.
 *
 * Every function returns the envelope's `data` or throws `V2Error` (./http.ts).
 * Mutations take the caller's per-action Idempotency-Key (`useActionKey`).
 * Contract: review/phase2-framework.md §6, checked against CAWP-API/api/bulk.py.
 */
import BaseService from '../BaseService'
import { V2Error, v2Get, v2Post, v2Request } from './http'
import type {
    ArtifactKind,
    BulkCollector,
    BulkCreateBody,
    BulkJob,
    BulkJobDetail,
    BulkJobsPage,
    BulkKind,
    BulkPreview,
    BulkRowsPage,
    BulkUpload,
} from './bulkTypes'

const enc = encodeURIComponent

export type JobsQuery = {
    kind?: string
    status?: string
    created_by?: string
    date_from?: string
    date_to?: string
    page?: number
    page_size?: number
}

export type RowsQuery = {
    eligibility?: string
    outcome?: string
    code?: string
    q?: string
    page?: number
    page_size?: number
}

/* The kind registry answers `allowed` for the caller. It changes only with the
 * role, so it is cached per signed-in user for the session. A failed load is
 * not cached. */
let kindsCache: { user: string; promise: Promise<BulkKind[]> } | null = null

export const bulkApi = {
    kinds: (user: string): Promise<BulkKind[]> => {
        if (kindsCache && kindsCache.user === user) return kindsCache.promise
        const promise = v2Get<BulkKind[]>('/v2/bulk/kinds').catch((e) => {
            if (kindsCache?.promise === promise) kindsCache = null
            throw e
        })
        kindsCache = { user, promise }
        return promise
    },

    collectors: (signal?: AbortSignal) =>
        v2Get<{ items: BulkCollector[]; total: number }>('/v2/bulk/assign/collectors', undefined, signal),

    /** Multipart upload; `purpose` is the kind key (or `bulk`). */
    upload: (file: File, purpose: string, key: string) => {
        const form = new FormData()
        form.append('purpose', purpose)
        form.append('file', file)
        return v2Request<BulkUpload>('/v2/uploads', { method: 'post', data: form, idempotencyKey: key })
    },

    create: (kind: string, body: BulkCreateBody, key: string) => v2Post<BulkJob>(`/v2/bulk/${enc(kind)}`, body, key),

    job: (id: string, signal?: AbortSignal) => v2Get<BulkJobDetail>(`/v2/bulk/jobs/${enc(id)}`, undefined, signal),

    preview: (id: string, signal?: AbortSignal) =>
        v2Get<BulkPreview>(`/v2/bulk/jobs/${enc(id)}/preview`, undefined, signal),

    rows: (id: string, q: RowsQuery, signal?: AbortSignal) =>
        v2Get<BulkRowsPage>(`/v2/bulk/jobs/${enc(id)}/rows`, q, signal),

    confirm: (id: string, body: { expected_revision: number; accept_partial?: boolean }, key: string) =>
        v2Post<BulkJob>(`/v2/bulk/jobs/${enc(id)}/confirm`, body, key),

    cancel: (id: string, body: { reason?: string }, key: string) =>
        v2Post<BulkJob>(`/v2/bulk/jobs/${enc(id)}/cancel`, body, key),

    retry: (id: string, body: { expected_revision?: number }, key: string) =>
        v2Post<BulkJob>(`/v2/bulk/jobs/${enc(id)}/retry`, body, key),

    reopen: (id: string, key: string) => v2Post<BulkJob>(`/v2/bulk/jobs/${enc(id)}/reopen`, {}, key),

    jobs: (q: JobsQuery, signal?: AbortSignal) => v2Get<BulkJobsPage>('/v2/jobs', q, signal),
}

/**
 * Downloads a job artifact (CSV) with the bearer token and saves it under the
 * server's file name. The API checks the permission; a refusal (403, 409 not
 * ready, 410 purged) comes back as a `V2Error` with the server's code.
 */
export async function downloadArtifact(jobId: string, kind: ArtifactKind, fileName?: string): Promise<void> {
    let blob: Blob
    try {
        const res = await BaseService({
            url: `/v2/bulk/jobs/${enc(jobId)}/artifacts/${kind}`,
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
    a.download = fileName || `bulk-${jobId.slice(0, 8)}-${kind}.csv`
    a.rel = 'noopener'
    document.body.appendChild(a)
    a.click()
    a.remove()
    setTimeout(() => URL.revokeObjectURL(url), 10000)
}

/**
 * The query string a list screen sends for its current filter, built by the same
 * axios instance (same params serializer) the list uses. The bulk framework
 * re-runs that list's query builder on it; paging is ignored server-side.
 */
export function listQueryString(url: string, params: Record<string, unknown>): string {
    const uri = BaseService.getUri({ url, params })
    const i = uri.indexOf('?')
    return i === -1 ? '' : uri.slice(i + 1)
}
