/**
 * Collector activity report (2026-11-05), CAWP-API/api/collector_report.py.
 *
 *   GET /v2/reports/collector-activity/options   the users in the caller's scope
 *   GET /v2/reports/collector-activity/html      ?user_id&from&to -> the print-ready Arabic page (audited)
 *   GET /v2/reports/collector-activity/docx      the same as a Word file (audited)
 */
import BaseService from '../BaseService'
import { V2Error, v2Get } from './http'

export type CollectorReportOptions = {
    users: { id: string; name: string | null; user_name: string | null; is_active: boolean; role: string | null }[]
    max_days: number
}

const fail = async (err: unknown): Promise<never> => {
    const ax = err as { response?: { status: number; data?: unknown } }
    if (ax?.response) {
        let body: { code?: string; message?: string; error?: unknown } = {}
        try {
            const raw = ax.response.data
            body = raw instanceof Blob ? JSON.parse(await raw.text()) : typeof raw === 'string' ? JSON.parse(raw) : (raw as typeof body) || {}
        } catch {
            /* not JSON */
        }
        throw new V2Error(ax.response.status, body.code || 'unknown', body.message || '', body.error ?? null)
    }
    throw new V2Error(0, 'network_error', (err as Error)?.message || 'network error', null)
}

export const collectorReportApi = {
    options: (signal?: AbortSignal) => v2Get<CollectorReportOptions>('/v2/reports/collector-activity/options', undefined, signal),
    /** The report page as HTML text (shown in a frame, printed to PDF by the browser). */
    html: async (q: { user_id: string; from: string; to: string }): Promise<string> => {
        try {
            const res = await BaseService({ url: '/v2/reports/collector-activity/html', method: 'get', params: q,
                                            responseType: 'text', timeout: 0 })
            return String(res.data)
        } catch (err) {
            return fail(err)
        }
    },
    /** The Word file, saved under the server's file name. */
    docx: async (q: { user_id: string; from: string; to: string }) => {
        let res
        try {
            res = await BaseService({ url: '/v2/reports/collector-activity/docx', method: 'get', params: q,
                                      responseType: 'blob', timeout: 0 })
        } catch (err) {
            return fail(err)
        }
        const disposition = String(res.headers?.['content-disposition'] || '')
        const name = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(disposition)?.[1] || 'employee-report.docx'
        const href = URL.createObjectURL(res.data as Blob)
        const a = document.createElement('a')
        a.href = href
        a.download = decodeURIComponent(name)
        a.rel = 'noopener'
        document.body.appendChild(a)
        a.click()
        a.remove()
        setTimeout(() => URL.revokeObjectURL(href), 10000)
    },
}
