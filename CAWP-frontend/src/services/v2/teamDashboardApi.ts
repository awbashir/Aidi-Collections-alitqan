/**
 * Team performance dashboard ("لوحة أداء الفريق", 2026-10-20), CAWP-API/api/team_dashboard.py.
 *
 *   GET /v2/dashboards/team         ?from&to&granularity&contract_id&user_id (comma-separated)&group
 *   GET /v2/dashboards/team/export  XLSX of the same query (audited)
 *
 * The API decides the scope (SuperAdmin every user, a supervisor their team and only notes on
 * cases in their scope). Call figures are null when the PBX call records are unavailable
 * (`calls.available` false): unknown stays unknown. Same transport as ./api.ts.
 */
import BaseService from '../BaseService'
import { V2Error, v2Get } from './http'

export type TeamGroup = 'status' | 'sub_status'

export type TeamDashboardQuery = {
    from?: string
    to?: string
    granularity?: 'hour' | 'day'
    contract_id?: string
    /** comma-separated user ids */
    user_id?: string
    group?: TeamGroup
}

export type TeamCallFields = {
    calls_made: number | null
    answered: number | null
    not_answered: number | null
    inbound_answered: number | null
    inbound_missed: number | null
    internal: number | null
    talk_seconds: number | null
    handled: number | null
    answer_rate: number | null
}

export type TeamUserRow = TeamCallFields & {
    user_id: string | null
    name: string | null
    is_active: boolean | null
    extensions: string[]
    notes: number
    clients: number
    by_status: Record<string, number>
    top_status: { id: string; name: string | null; color: string | null; count: number } | null
    promises: number
    promises_amount: string
}

export type TeamStatus = {
    key: string
    id: string | null
    name: string | null
    color: string | null
    parent_id: string | null
    parent_name: string | null
    no_sub: boolean
    count: number
}

export type TeamDashboard = {
    scope: 'all' | 'team' | 'own'
    from: string
    to: string
    granularity: 'hour' | 'day'
    group: TeamGroup
    contract_id: string | null
    user_ids: string[]
    max_days: number
    options: {
        users: { id: string; name: string | null; is_active: boolean; extensions: string[] }[]
        contracts: { id: string; name: string; is_active: boolean }[]
    }
    calls: { available: boolean; error: string | null; truncated: boolean; contract_filtered: boolean }
    totals: TeamCallFields & { notes: number; clients: number; promises: number; promises_amount: string }
    users: TeamUserRow[]
    statuses: TeamStatus[]
    series: { bucket: string; total: number; by_status: Record<string, number> }[]
}

const clean = (q: object) => Object.fromEntries(Object.entries(q).filter(([, v]) => v !== undefined && v !== null && v !== ''))

export const teamDashboardApi = {
    get: (q: TeamDashboardQuery, signal?: AbortSignal) => v2Get<TeamDashboard>('/v2/dashboards/team', clean(q), signal),
    /** XLSX download with the bearer token, saved under the server's file name. */
    download: async (q: TeamDashboardQuery) => {
        let res
        try {
            res = await BaseService({ url: '/v2/dashboards/team/export', method: 'get', params: clean(q), responseType: 'blob', timeout: 0 })
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
        const disposition = String(res.headers?.['content-disposition'] || '')
        const name = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(disposition)?.[1] || 'team-dashboard.xlsx'
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
