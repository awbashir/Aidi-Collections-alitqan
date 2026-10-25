/**
 * Phase 5 v2 endpoints: calls (manual log, wrap-up, click-to-call, the live view),
 * supervisor review, calling administration, campaigns and PBX evidence.
 *
 * Same transport and conventions as ./api.ts: every function returns the envelope's
 * `data` or throws `V2Error`; mutations take the caller's per-action Idempotency-Key;
 * row edits send the revision they loaded (`expected_revision`).
 *
 * Contracts: review/phase5-{foundation,click-to-call,campaigns,evidence}.md, checked
 * against CAWP-API/api/calling*.py. No call here talks to a PBX: `POST /v2/calls` only
 * authorizes and queues; the `calling` service sends it (AMI "Success" = submitted).
 */
import BaseService from '../BaseService'
import { V2Error, v2Get, v2Patch, v2Post, v2Put, v2Request } from './http'
import type {
    CampaignSound,
    MessageCall,
    SoundKind,
    CalledListing,
    CaseImportResult,
    CallDetail,
    CallList,
    CallQuery,
    CallingServiceStatus,
    CallingUser,
    CallingHoliday,
    CallingWindow,
    Campaign,
    CampaignBody,
    CampaignLive,
    CaseBlocks,
    DriftRow,
    ExtensionHistory,
    ExtensionRow,
    LiveCall,
    ManualCallBody,
    MyCallingInfo,
    MyCampaignStatus,
    PbxEvidence,
    PoolListing,
    Readiness,
    ReviewList,
    RosterItem,
    UncertainList,
    WindowsView,
    SurveySetting,
    SurveyResults,
    MonitorCallsPage,
    MonitorLive,
    MonitorStats,
    WrapupBody,
} from './p5Types'
import type { Paged } from './types'

const enc = encodeURIComponent

/* ---------------------------------------------------------------- calls */

export const callsApi = {
    /** Scoped call log (key `calls`): my calls plus the calls on cases in my scope. */
    log: (q: CallQuery, signal?: AbortSignal) => v2Get<CallList>('/v2/calls', q, signal),

    mine: (q: CallQuery, signal?: AbortSignal) => v2Get<CallList>('/v2/calls/mine', q, signal),

    /** XLSX of the list with its filters (2026-10-19): `team` = the call log, `mine` = my calls. */
    download: (kind: 'team' | 'mine', q: CallQuery) =>
        downloadXlsx(kind === 'team' ? '/v2/calls/export' : '/v2/calls/mine/export', q, `calls-${kind}.xlsx`),

    caseCalls: (caseId: string, q: CallQuery, signal?: AbortSignal) =>
        v2Get<CallList>(`/v2/cases/${enc(caseId)}/calls`, q, signal),

    detail: (id: string, signal?: AbortSignal) => v2Get<CallDetail>(`/v2/calls/${enc(id)}`, undefined, signal),

    pbxEvidence: (id: string, signal?: AbortSignal) =>
        v2Get<PbxEvidence>(`/v2/calls/${enc(id)}/pbx-evidence`, undefined, signal),

    /** Click-to-call (key `calls_make`). The browser never sends a number, only the contact. */
    place: (body: { case_id: string; contact_id: string }, key: string) => v2Post<LiveCall>('/v2/calls', body, key),

    cancel: (id: string, body: { expected_revision: number; reason?: string }, key: string) =>
        v2Post<LiveCall>(`/v2/calls/${enc(id)}/cancel`, body, key),

    wrapup: (id: string, body: WrapupBody, key: string) => v2Post<CallDetail>(`/v2/calls/${enc(id)}/wrapup`, body, key),

    logManual: (body: ManualCallBody, key: string) => v2Post<CallDetail>('/v2/calls/manual', body, key),

    /** My attempt that still holds a reservation (live, uncertain or awaiting wrap-up). */
    myActive: (signal?: AbortSignal) => v2Get<{ attempt: LiveCall | null }>('/v2/me/calls/active', undefined, signal),

    myCalling: (signal?: AbortSignal) => v2Get<MyCallingInfo>('/v2/me/calling', undefined, signal),

    caseBlocks: (caseId: string, signal?: AbortSignal) =>
        v2Get<CaseBlocks>(`/v2/cases/${enc(caseId)}/calling-blocks`, undefined, signal),

    releaseBlock: (blockId: string, reason: string, key: string) =>
        v2Post<CaseBlocks>(`/v2/calling-blocks/${enc(blockId)}/release`, { reason }, key),
}

/**
 * The P1 live view with ETag polling. Returns `null` on 304 (nothing changed).
 *
 * The API does not expose the `ETag` header cross-origin (no Access-Control-Expose-Headers),
 * so the body's own `etag` field (the same weak tag) is used when the header is hidden.
 * `If-None-Match` is allowed by the CORS preflight.
 */
export async function fetchLiveCall(id: string, etag: string | null, signal?: AbortSignal): Promise<LiveCall | null> {
    try {
        const res = await BaseService({
            url: `/v2/calls/${enc(id)}/live`,
            method: 'get',
            headers: etag ? { 'If-None-Match': etag } : {},
            validateStatus: (s) => s === 200 || s === 304,
            signal,
        })
        if (res.status === 304) return null
        const data = (res.data?.data ?? null) as LiveCall | null
        if (data) {
            const header = (res.headers?.etag as string | undefined) || undefined
            data.etag = header || data.etag
        }
        return data
    } catch (err) {
        const ax = err as {
            code?: string
            response?: { status: number; data?: { code?: string; message?: string; error?: unknown } }
        }
        if (ax?.response) {
            const body = ax.response.data || {}
            throw new V2Error(ax.response.status, body.code || 'unknown', body.message || '', body.error ?? null)
        }
        if (ax?.code === 'ERR_CANCELED') throw new V2Error(0, 'canceled', 'canceled', null)
        throw new V2Error(0, 'network_error', (err as Error)?.message || 'network error', null)
    }
}

/* ---------------------------------------------------------------- supervisor review */

export const callReviewApi = {
    uncertain: (q: { page?: number; page_size?: number }, signal?: AbortSignal) =>
        v2Get<UncertainList>('/v2/calling/uncertain', q, signal),

    resolve: (
        id: string,
        body: { expected_revision: number; resolution: 'ended' | 'failed'; reason: string; release_reservation: boolean },
        key: string
    ) => v2Post<LiveCall>(`/v2/calls/${enc(id)}/resolve`, body, key),

    flags: (
        q: { state?: 'open' | 'resolved' | 'all'; kind?: string; page?: number; page_size?: number },
        signal?: AbortSignal
    ) => v2Get<ReviewList>('/v2/calling/review', q, signal),

    resolveFlag: (flagId: string, reason: string, key: string) =>
        v2Post<{ flag_id: string; call: LiveCall }>(`/v2/calling/review-flags/${enc(flagId)}/resolve`, { reason }, key),
}

/* ---------------------------------------------------------------- calling admin */

export const callingAdminApi = {
    status: (signal?: AbortSignal) => v2Get<CallingServiceStatus>('/v2/calling/status', undefined, signal),

    users: (q: { q?: string; page?: number; page_size?: number }, signal?: AbortSignal) =>
        v2Get<Paged<CallingUser>>('/v2/calling/users', q, signal),

    extensions: (
        q: { state?: 'active' | 'released' | 'all'; q?: string; page?: number; page_size?: number },
        signal?: AbortSignal
    ) => v2Get<Paged<ExtensionRow> & { state: string }>('/v2/calling/extensions', q, signal),

    history: (extension: string, signal?: AbortSignal) =>
        v2Get<ExtensionHistory>(`/v2/calling/extensions/${enc(extension)}/history`, undefined, signal),

    assign: (
        extension: string,
        body: { user_id: string; reason: string; expected_owner_id: string | null; replace?: boolean },
        key: string
    ) => v2Post<ExtensionHistory>(`/v2/calling/extensions/${enc(extension)}/assign`, body, key),

    release: (extension: string, body: { reason: string; expected_owner_id: string | null }, key: string) =>
        v2Post<ExtensionHistory>(`/v2/calling/extensions/${enc(extension)}/release`, body, key),

    drift: (q: { page?: number; page_size?: number }, signal?: AbortSignal) =>
        v2Get<Paged<DriftRow>>('/v2/calling/extensions/legacy-drift', q, signal),

    adopt: (body: { user_id: string; extension: string; reason: string }, key: string) =>
        v2Post<ExtensionHistory>('/v2/calling/extensions/legacy-drift/adopt', body, key),

    windows: (signal?: AbortSignal) => v2Get<WindowsView>('/v2/calling/windows', undefined, signal),

    saveWindows: (
        body: { expected_revision: number; reason: string; windows: CallingWindow[]; holidays: CallingHoliday[] },
        key: string
    ) => v2Put<WindowsView>('/v2/calling/windows', body, key),

    surveySettings: (signal?: AbortSignal) => v2Get<{ items: SurveySetting[] }>('/v2/calling/surveys/settings', undefined, signal),

    saveSurveySetting: (contractId: string, body: { post_call_survey: boolean; expected_revision: number; reason?: string }, key: string) =>
        v2Put<SurveySetting>(`/v2/calling/surveys/settings/${enc(contractId)}`, body, key),

    surveyResults: (q: { from?: string; to?: string; contract_id?: string }, signal?: AbortSignal) =>
        v2Get<SurveyResults>('/v2/calling/surveys', q, signal),
}

/** Call monitor: live calls and calls by user over time (scope decided by the API). */
export const callMonitorApi = {
    live: (q: { extension?: string; contract?: string }, signal?: AbortSignal) => v2Get<MonitorLive>('/v2/calling/monitor/live', q, signal),
    stats: (q: { from?: string; to?: string; granularity?: 'hour' | 'day'; extension?: string; contract?: string; number?: string }, signal?: AbortSignal) =>
        v2Get<MonitorStats>('/v2/calling/monitor/stats', q, signal),
    /** One user's calls of the period, newest first (page_size max 200). */
    calls: (q: { from?: string; to?: string; extension?: string; contract?: string; number?: string; page?: number; page_size?: number }, signal?: AbortSignal) =>
        v2Get<MonitorCallsPage>('/v2/calling/monitor/calls', q, signal),
    /** XLSX download with the bearer token: `summary` = the per-user table, `calls` = one row per call. */
    download: async (kind: 'summary' | 'calls', q: { from?: string; to?: string; extension?: string; contract?: string; number?: string }) =>
        downloadXlsx(kind === 'summary' ? '/v2/calling/monitor/stats/export' : '/v2/calling/monitor/calls/export', q, `call-monitor-${kind}.xlsx`),
}

/** GET an XLSX with the bearer token and save it under the server's file name. */
async function downloadXlsx(url: string, q: object, fallback: string) {
    const params = Object.fromEntries(Object.entries(q).filter(([, v]) => v !== undefined && v !== null && v !== ''))
    let res
    try {
        res = await BaseService({
            url,
            method: 'get',
            params,
            responseType: 'blob',
            timeout: 0,
        })
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
    const name = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(disposition)?.[1] || fallback
    const href = URL.createObjectURL(res.data as Blob)
    const a = document.createElement('a')
    a.href = href
    a.download = decodeURIComponent(name)
    a.rel = 'noopener'
    document.body.appendChild(a)
    a.click()
    a.remove()
    setTimeout(() => URL.revokeObjectURL(href), 10000)
}

/* ---------------------------------------------------------------- campaigns */

/** A POST the API explicitly accepts without an Idempotency-Key (the campaign heartbeat). */
async function postWithoutKey<T>(url: string, data: unknown): Promise<T> {
    try {
        const res = await BaseService({ url, method: 'post', data })
        const body = res.data as { data?: T }
        return (body && typeof body === 'object' && 'data' in body ? body.data : body) as T
    } catch (err) {
        const ax = err as { response?: { status: number; data?: { code?: string; message?: string; error?: unknown } } }
        if (ax?.response) {
            const b = ax.response.data || {}
            const code = b.code || (ax.response.status === 404 ? 'not_found' : ax.response.status === 403 ? 'forbidden' : 'unknown')
            throw new V2Error(ax.response.status, code, b.message || '', b.error ?? null)
        }
        throw new V2Error(0, 'network_error', (err as Error)?.message || 'network error', null)
    }
}

export const campaignsApi = {
    list: (q: { status?: string; mine?: 1; page?: number; page_size?: number }, signal?: AbortSignal) =>
        v2Get<Paged<Campaign>>('/v2/campaigns', q, signal),

    detail: (id: string, signal?: AbortSignal) => v2Get<Campaign>(`/v2/campaigns/${enc(id)}`, undefined, signal),

    /** 2026-10-19: the contracts my scope reaches (null = every contract), for the form's note-status pickers. */
    poolScope: (signal?: AbortSignal) => v2Get<{ contract_ids: string[] | null }>('/v2/campaigns/pool-scope', undefined, signal),

    create: (body: CampaignBody & { name: string }, key: string) => v2Post<Campaign>('/v2/campaigns', body, key),

    update: (id: string, body: CampaignBody & { expected_revision: number }, key: string) =>
        v2Patch<Campaign>(`/v2/campaigns/${enc(id)}`, body, key),

    transition: (
        id: string,
        action: 'start' | 'pause' | 'drain' | 'stop',
        body: { expected_revision: number; reason?: string },
        key: string
    ) => v2Post<Campaign>(`/v2/campaigns/${enc(id)}/${action}`, body, key),

    members: (id: string, signal?: AbortSignal) =>
        v2Get<{ items: RosterItem[] }>(`/v2/campaigns/${enc(id)}/members`, undefined, signal),

    addMembers: (id: string, body: { expected_revision: number; user_ids: string[]; reason?: string }, key: string) =>
        v2Post<{ items: RosterItem[]; added: string[]; reactivated: string[]; revision: number }>(
            `/v2/campaigns/${enc(id)}/members`,
            body,
            key
        ),

    deactivateMember: (id: string, userId: string, body: { expected_revision: number; reason: string }, key: string) =>
        v2Post<{ items: RosterItem[]; deactivated: string; revision: number }>(
            `/v2/campaigns/${enc(id)}/members/${enc(userId)}/deactivate`,
            body,
            key
        ),

    me: (id: string, signal?: AbortSignal) => v2Get<MyCampaignStatus>(`/v2/campaigns/${enc(id)}/me`, undefined, signal),

    /**
     * Lease renewal (+ optional state). The one v2 mutation sent WITHOUT an Idempotency-Key:
     * the route makes the key optional (a renewal is naturally idempotent, runs every few
     * seconds, and a key would store a response row per beat).
     */
    heartbeat: (id: string, state?: Readiness) => postWithoutKey<MyCampaignStatus>(`/v2/campaigns/${enc(id)}/heartbeat`, state ? { state } : {}),

    next: (id: string, key: string) => v2Post<MyCampaignStatus>(`/v2/campaigns/${enc(id)}/next`, {}, key),

    live: (id: string, signal?: AbortSignal) => v2Get<CampaignLive>(`/v2/campaigns/${enc(id)}/live`, undefined, signal),

    pool: (id: string, q: { reason?: string; page?: number; page_size?: number }, signal?: AbortSignal) =>
        v2Get<PoolListing>(`/v2/campaigns/${enc(id)}/pool`, q, signal),

    /** 2026-10-24: add customers to a customer-list campaign, by national id OR account number. */
    importCases: (
        id: string,
        body: { match_by: 'identity' | 'account'; expected_revision: number } & ({ values: string[] } | { file: File; column?: number }),
        key: string
    ) => {
        if ('file' in body) {
            const form = new FormData()
            form.append('match_by', body.match_by)
            form.append('expected_revision', String(body.expected_revision))
            if (body.column) form.append('column', String(body.column))
            form.append('file', body.file)
            return v2Request<CaseImportResult>(`/v2/campaigns/${enc(id)}/cases/import`, { method: 'post', data: form, idempotencyKey: key })
        }
        return v2Post<CaseImportResult>(`/v2/campaigns/${enc(id)}/cases/import`, body, key)
    },

    removeCases: (id: string, body: { expected_revision: number; case_ids: string[]; reason?: string }, key: string) =>
        v2Post<{ removed: string[]; revision: number }>(`/v2/campaigns/${enc(id)}/cases/remove`, body, key),

    restoreCases: (id: string, body: { expected_revision: number; case_ids: string[]; reason?: string }, key: string) =>
        v2Post<{ restored: string[]; revision: number }>(`/v2/campaigns/${enc(id)}/cases/restore`, body, key),

    called: (id: string, q: { answered?: '0' | '1'; page?: number; page_size?: number }, signal?: AbortSignal) =>
        v2Get<CalledListing>(`/v2/campaigns/${enc(id)}/called`, q, signal),

    /* 2026-10-25 (0088): message campaigns */
    sounds: (id: string, signal?: AbortSignal) =>
        v2Get<{ items: CampaignSound[] }>(`/v2/campaigns/${enc(id)}/sounds`, undefined, signal),

    uploadSound: (id: string, kind: SoundKind, file: File, key: string) => {
        const form = new FormData()
        form.append('file', file)
        return v2Request<CampaignSound>(`/v2/campaigns/${enc(id)}/sounds/${enc(kind)}`, { method: 'post', data: form, idempotencyKey: key })
    },

    deleteSound: (id: string, kind: SoundKind, key: string) =>
        v2Request<{ kind: SoundKind; deleted: boolean }>(`/v2/campaigns/${enc(id)}/sounds/${enc(kind)}`, { method: 'delete', idempotencyKey: key }),

    /** The converted WAV as an object URL for an <audio> preview (the caller revokes it). */
    soundUrl: async (id: string, kind: SoundKind) => {
        const res = await BaseService({ url: `/v2/campaigns/${enc(id)}/sounds/${enc(kind)}/audio`, method: 'get', responseType: 'blob' })
        return URL.createObjectURL(res.data as Blob)
    },

    messageCalls: (id: string, q: { result?: string; page?: number; page_size?: number }, signal?: AbortSignal) =>
        v2Get<Paged<MessageCall>>(`/v2/campaigns/${enc(id)}/message-calls`, q, signal),
}
