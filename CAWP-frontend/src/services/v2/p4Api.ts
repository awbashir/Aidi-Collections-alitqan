/**
 * Phase 4 endpoints: the report catalogue and definitions, the preview rows,
 * scoped selector options, and the private exports (create, poll, list,
 * download). Everything goes through `v2Request` (bearer token, the global 401
 * sign-out, `V2Error`); the export download is a blob.
 */
import BaseService from '../BaseService'
import { V2Error, v2Get, v2Post } from './http'
import type {
    ReportCatalogue,
    ReportDefinition,
    ReportExport,
    ReportExportsPage,
    ReportExportsQuery,
    ReportFormat,
    ReportOptionsPage,
    ReportRows,
} from './p4Types'

const enc = encodeURIComponent

export type ExportBody = {
    format: ReportFormat
    /** The same flat filter params as the rows request (paging is not part of an export). */
    params: Record<string, string>
    sort: string
}

export const reportsApi = {
    list: (signal?: AbortSignal) => v2Get<ReportCatalogue>('/v2/reports', undefined, signal),

    definition: (id: string, signal?: AbortSignal) =>
        v2Get<ReportDefinition>(`/v2/reports/${enc(id)}`, undefined, signal),

    /** `params` = filter params + `sort`, `page`, `page_size`. Empty values are not sent. */
    rows: (id: string, params: Record<string, string | number>, signal?: AbortSignal) =>
        v2Get<ReportRows>(`/v2/reports/${enc(id)}/rows`, params, signal),

    /** `params` must carry `report`; plus `q`, `page`, `selected` and the depends_on filters' params. */
    options: (field: string, params: Record<string, string | number>, signal?: AbortSignal) =>
        v2Get<ReportOptionsPage>(`/v2/report-options/${enc(field)}`, params, signal),

    /** 202 queued. One Idempotency-Key per user action (a retried click replays). */
    createExport: (id: string, body: ExportBody, key: string) =>
        v2Post<ReportExport>(`/v2/reports/${enc(id)}/exports`, body, key),

    exports: (q: ReportExportsQuery, signal?: AbortSignal) =>
        v2Get<ReportExportsPage>('/v2/report-exports', q, signal),

    exportDetail: (id: string, signal?: AbortSignal) =>
        v2Get<ReportExport>(`/v2/report-exports/${enc(id)}`, undefined, signal),
}

/**
 * Downloads a finished export with the bearer token and saves it under the
 * artifact's file name (the API is cross-origin and does not expose
 * Content-Disposition). Refusals (403 key lost, 404 not the creator, 409 not
 * ready / failed, 410 expired / purged) come back as `V2Error` with the code.
 */
export async function downloadReportExport(exp: Pick<ReportExport, 'id' | 'artifact' | 'report' | 'format'>): Promise<void> {
    let blob: Blob
    try {
        const res = await BaseService({
            url: `/v2/report-exports/${enc(exp.id)}/download`,
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
    const ext = exp.format === 'csv' ? 'csv' : 'xlsx'
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = exp.artifact?.file_name || `${exp.report.id}-${exp.id.slice(0, 8)}.${ext}`
    a.rel = 'noopener'
    document.body.appendChild(a)
    a.click()
    a.remove()
    setTimeout(() => URL.revokeObjectURL(url), 10000)
}
