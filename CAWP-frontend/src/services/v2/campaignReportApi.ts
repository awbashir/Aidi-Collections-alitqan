/**
 * Campaign report ("تقرير الحملة", 2026-10-21), CAWP-API/api/calling_campaign_report.py.
 *
 *   GET /v2/campaigns/:id/report          ?payment_days=0..90 (default 7)
 *   GET /v2/campaigns/:id/report/export   the same as an Arabic XLSX with a sheet per client (audited)
 *
 * `final` is true once the campaign is stopped; otherwise the figures are "so far".
 * Definitions (services/calling/campaign_report.py): reached = PBX bridge evidence; talk time of
 * a reached call with no end yet is unknown (`talk_unknown`), never 0; "collected after contact"
 * counts APPROVED payments only (pending ones are shown apart in `pending_not_counted`) and is not
 * proof that the call caused them. Same transport as ./api.ts.
 */
import BaseService from '../BaseService'
import { V2Error, v2Get } from './http'
import type { Campaign } from './p5Types'

export type ReportOutcome = 'connected' | 'promise_discussed' | 'dispute' | 'no_answer' | 'busy' | 'wrong_number' | 'do_not_call'

export type CampaignReportAgent = {
    user: { id: string; name: string | null } | null
    member: boolean
    active: boolean
    extension: string | null
    attempts: number
    sent: number
    reached: number
    clients: number
    answer_rate: number | null
    talk_seconds: number
    talk_unknown: number
    avg_talk_seconds: number | null
    promises: number
    promises_amount: string
    outcomes: Partial<Record<ReportOutcome, number>>
    /** key = main status id, or `<main id>/<sub id>` */
    statuses: Record<string, number>
}

export type CampaignReportStatus = {
    key: string
    status_id: string
    status_name: string | null
    sub_status_id: string | null
    sub_status_name: string | null
    color: string | null
    count: number
}

export type CampaignReport = {
    campaign: Campaign
    final: boolean
    generated_at: string
    window: {
        created_at: string | null
        started_at: string | null
        stopped_at: string | null
        stop_reason: string | null
        drain_requested_at: string | null
        end_at: string | null
        duration_seconds: number | null
        first_call_at: string | null
        last_call_at: string | null
    }
    pool: {
        contracts: { id: string; name: string | null }[]
        portfolios: { id: string; name: string | null }[]
        filters: { key: string; label_ar: string; values: string[] }[]
    }
    clients: {
        total: number
        pool_now: number
        called: number
        reached: number
        not_reached: number
        excluded: number
        pending: number
        excluded_by_reason: Record<string, number>
    }
    attempts: {
        total: number
        sent: number
        not_sent: number
        reached: number
        customer_answered: number
        failed: number
        uncertain: number
        live: number
        pending_wrapup: number
        talk_seconds: number
        talk_known: number
        talk_unknown: number
        answer_rate: number | null
        avg_talk_seconds: number | null
    }
    outcomes: Record<ReportOutcome, number>
    statuses: CampaignReportStatus[]
    promises: {
        count: number
        amount: string
        cancelled: number
        by_state: Record<string, number>
        linked_existing: number
        first_due: string | null
        last_due: string | null
        by_due_date: { date: string; count: number; amount: string }[]
    }
    payments: {
        label: 'collected_after_contact'
        count: number
        amount: string
        cases: number
        reached_cases_amount: string
        payment_days: number
        until: string
        pending_not_counted: { count: number; amount: string }
    }
    assignments: { available: boolean; count: number; by_user: { user: { id: string; name: string | null }; count: number }[] }
    agents: CampaignReportAgent[]
    hours: { hour: number; attempts: number; reached: number }[]
    agents_took_part: number
}

const enc = encodeURIComponent

export const campaignReportApi = {
    get: (id: string, q: { payment_days?: number }, signal?: AbortSignal) =>
        v2Get<CampaignReport>(`/v2/campaigns/${enc(id)}/report`, q, signal),
    /** XLSX download with the bearer token, saved under the server's file name. */
    download: async (id: string, q: { payment_days?: number }) => {
        let res
        try {
            res = await BaseService({ url: `/v2/campaigns/${enc(id)}/report/export`, method: 'get', params: q, responseType: 'blob', timeout: 0 })
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
        const name = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(disposition)?.[1] || 'campaign-report.xlsx'
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
