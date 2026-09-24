/**
 * Phase 6 v2 endpoints: AI status / data policy / provider-call audit (`ai_admin`), AI drafts
 * (`ai_assist`) and the quality workspace (`quality_review`, `quality_approve`).
 *
 * Same transport and conventions as ./api.ts and ./p5Api.ts: every function returns the
 * envelope's `data` or throws `V2Error`; mutations take the caller's per-action
 * Idempotency-Key; row edits send the revision they loaded (`expected_revision`).
 *
 * Nothing here talks to a provider: the API's gateway does, and without a key it answers
 * `503 ai_not_configured` (or `model_not_set`, `ai_policy_required`, ...), which the screens
 * show as it is. Generated text is only ever a draft; the one follow-up is an explicit
 * "save as note" / a human-saved quality review.
 */
import BaseService from '../BaseService'
import { V2Error, v2Get, v2Patch, v2Post, v2Put } from './http'
import type {
    AiCheckResult,
    AiDraft,
    AiPolicy,
    AiPolicyBody,
    AiPolicyView,
    AiProviderCall,
    AiProviderCallQuery,
    AiStatus,
    DraftCreateBody,
    DraftQuery,
    QcApproval,
    QcDashboard,
    QcDashboardQuery,
    QcEvidenceBody,
    QcImportResult,
    QcOverview,
    QcRecording,
    QcRecordingList,
    QcRecordingQuery,
    QcReview,
    QcReviewBody,
    QcTemplate,
    QcTranscript,
    QcTranscriptBody,
    SaveAsNoteBody,
    SavedNote,
} from './p6Types'
import type { Paged } from './types'

const enc = encodeURIComponent

/* ---------------------------------------------------------------- AI (Q0) */

export const aiApi = {
    /** Readiness per capability + reasons (any AI key; details for ai_admin). No key is ever returned. */
    status: (signal?: AbortSignal) => v2Get<AiStatus>('/v2/ai/status', undefined, signal),

    policy: (signal?: AbortSignal) => v2Get<AiPolicyView>('/v2/ai/policy', undefined, signal),

    /** A new policy revision: full replacement, `confirmed: true`, the revision you loaded. */
    savePolicy: (body: AiPolicyBody, key: string) =>
        v2Put<{ policy: AiPolicy; revision: number }>('/v2/ai/policy', body, key),

    policyHistory: (q: { page?: number; page_size?: number }, signal?: AbortSignal) =>
        v2Get<Paged<AiPolicy>>('/v2/ai/policy/history', q, signal),

    providerCalls: (q: AiProviderCallQuery, signal?: AbortSignal) =>
        v2Get<Paged<AiProviderCall>>('/v2/ai/provider-calls', q, signal),

    /** One fixed-text provider call (no data category). Without a key: the honest refusal. */
    check: (key: string) => v2Post<AiCheckResult>('/v2/ai/check', {}, key),
}

/* ---------------------------------------------------------------- AI drafts (Q1) */

export const draftsApi = {
    /** Generate a draft (or an honest refusal). The key is kept on the draft row: a replay returns it. */
    create: (body: DraftCreateBody, key: string) => v2Post<AiDraft>('/v2/ai/drafts', body, key),

    list: (q: DraftQuery, signal?: AbortSignal) => v2Get<Paged<AiDraft>>('/v2/ai/drafts', q, signal),

    get: (id: string, signal?: AbortSignal) => v2Get<AiDraft>(`/v2/ai/drafts/${enc(id)}`, undefined, signal),

    edit: (id: string, body: { text: string; expected_revision: number }, key: string) =>
        v2Patch<AiDraft>(`/v2/ai/drafts/${enc(id)}`, body, key),

    /** THE explicit action: the reviewed text becomes one case note (no callback, no promise, nothing sent). */
    saveAsNote: (id: string, body: SaveAsNoteBody, key: string) =>
        v2Post<SavedNote>(`/v2/ai/drafts/${enc(id)}/save-as-note`, body, key),

    discard: (id: string, expectedRevision: number, key: string) =>
        v2Post<AiDraft>(`/v2/ai/drafts/${enc(id)}/discard`, { expected_revision: expectedRevision }, key),
}

/* ---------------------------------------------------------------- quality (Q2) */

const rec = (id: string) => `/v2/quality/recordings/${enc(id)}`

export const qualityApi = {
    overview: (signal?: AbortSignal) => v2Get<QcOverview>('/v2/quality', undefined, signal),

    templates: (signal?: AbortSignal) => v2Get<QcTemplate[]>('/v2/quality/templates', undefined, signal),

    template: (key: string, version: number, signal?: AbortSignal) =>
        v2Get<QcTemplate>(`/v2/quality/templates/${enc(key)}/${version}`, undefined, signal),

    recordings: (q: QcRecordingQuery, signal?: AbortSignal) => v2Get<QcRecordingList>('/v2/quality/recordings', q, signal),

    /** Private import (multipart `file`, optional `title`, `case_id`). 201 new, 200 a visible duplicate. */
    importFile: (file: File, fields: { title?: string; case_id?: string }, key: string) => {
        const form = new FormData()
        form.append('file', file)
        if (fields.title) form.append('title', fields.title)
        if (fields.case_id) form.append('case_id', fields.case_id)
        return v2Post<QcImportResult>('/v2/quality/recordings', form, key)
    },

    /** "Add call recording to QC": only on this explicit, confirmed request (recording key + case scope). */
    fromCall: (body: { attempt_id: string; recording_path?: string; title?: string; confirmed: true }, key: string) =>
        v2Post<QcImportResult>('/v2/quality/recordings/from-call', body, key),

    detail: (id: string, signal?: AbortSignal) => v2Get<QcRecording>(rec(id), undefined, signal),

    updateEvidence: (id: string, body: QcEvidenceBody, key: string) => v2Patch<QcRecording>(rec(id), body, key),

    saveTranscript: (id: string, body: QcTranscriptBody, key: string) =>
        v2Put<QcRecording>(`${rec(id)}/transcript`, body, key),

    transcripts: (id: string, signal?: AbortSignal) => v2Get<QcTranscript[]>(`${rec(id)}/transcripts`, undefined, signal),

    transcribe: (id: string, body: { expected_revision: number; language?: 'ar' | 'en' }, key: string) =>
        v2Post<QcRecording>(`${rec(id)}/transcribe`, body, key),

    aiDraft: (id: string, body: { expected_revision: number; language: 'ar' | 'en' }, key: string) =>
        v2Post<QcRecording>(`${rec(id)}/draft`, body, key),

    saveReview: (id: string, body: QcReviewBody, key: string) => v2Put<QcRecording>(`${rec(id)}/review`, body, key),

    reviews: (id: string, signal?: AbortSignal) => v2Get<QcReview[]>(`${rec(id)}/reviews`, undefined, signal),

    approve: (id: string, body: { expected_revision: number; confirmed: true }, key: string) =>
        v2Post<QcRecording>(`${rec(id)}/approve`, body, key),

    approvals: (id: string, signal?: AbortSignal) => v2Get<QcApproval[]>(`${rec(id)}/approvals`, undefined, signal),

    approval: (approvalId: string, signal?: AbortSignal) =>
        v2Get<QcApproval>(`/v2/quality/approvals/${enc(approvalId)}`, undefined, signal),

    dashboard: (q: QcDashboardQuery, signal?: AbortSignal) => v2Get<QcDashboard>('/v2/quality/dashboard', q, signal),
}

/** A failed binary request: read the JSON envelope out of the blob / array buffer. */
async function binaryError(err: unknown): Promise<V2Error> {
    const ax = err as { response?: { status: number; data?: Blob | ArrayBuffer }; code?: string; message?: string }
    if (ax?.response) {
        let body: { code?: string; message?: string; error?: unknown } = {}
        try {
            const data = ax.response.data
            const textBody =
                data instanceof Blob ? await data.text() : data ? new TextDecoder().decode(data as ArrayBuffer) : ''
            body = textBody ? JSON.parse(textBody) : {}
        } catch {
            /* not JSON */
        }
        const status = ax.response.status
        const code =
            body.code ||
            (status === 403 ? 'forbidden' : status === 404 ? 'not_found' : status === 410 ? 'audio_purged' : status === 416 ? 'range_not_satisfiable' : 'unknown')
        return new V2Error(status, code, body.message || '', body.error ?? null)
    }
    if (ax?.code === 'ERR_CANCELED') return new V2Error(0, 'canceled', 'canceled', null)
    return new V2Error(0, 'network_error', ax?.message || 'network error', null)
}

/**
 * The HTML report (ar/en) of a recording, or of one approval. The API sends it as an attachment
 * (`default-src 'none'`); it is saved as a file, never rendered inside the app.
 */
export async function downloadQualityReport(id: string, language: 'ar' | 'en', approvalId?: string): Promise<string> {
    let blob: Blob
    let name = `qc-report-${id}-${language}.html`
    try {
        const res = await BaseService({
            url: `${rec(id)}/report`,
            method: 'get',
            params: { language, ...(approvalId ? { approval_id: approvalId } : {}) },
            responseType: 'blob',
            timeout: 0,
        })
        blob = res.data as Blob
        const cd = String(res.headers?.['content-disposition'] || '')
        const m = /filename="?([^";]+)"?/i.exec(cd)
        if (m) name = m[1]
    } catch (err) {
        throw await binaryError(err)
    }
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = name
    a.rel = 'noopener'
    document.body.appendChild(a)
    a.click()
    a.remove()
    setTimeout(() => URL.revokeObjectURL(url), 10000)
    return name
}

export type AudioLoadProgress = { loaded: number; total: number; requests: number; partial: number }

/** Bytes per range request: small enough to show progress, large enough to stay few. */
export const AUDIO_CHUNK_BYTES = 512 * 1024

/**
 * Loads a QC recording's private audio with byte-range requests (`Range: bytes=a-b`, 206
 * answers), because the bearer token cannot ride on an `<audio src>`. The size comes from a
 * HEAD (`Content-Length` is readable cross-origin; `Content-Range` is not exposed by the API's
 * CORS setup, so each chunk is checked by its own length). The result is a Blob the page plays
 * from memory, fully seekable. `410 audio_purged` / `404 audio_missing` come back as V2Error.
 */
export async function loadQualityAudio(
    id: string,
    contentType: string,
    onProgress?: (p: AudioLoadProgress) => void,
    signal?: AbortSignal
): Promise<{ blob: Blob; progress: AudioLoadProgress }> {
    const url = `${rec(id)}/audio`
    let total: number
    try {
        const head = await BaseService({ url, method: 'head', signal })
        total = Number(head.headers?.['content-length'])
    } catch (err) {
        /* a HEAD error has no body: ask again with a 1-byte GET to get the API's code */
        try {
            await BaseService({ url, method: 'get', headers: { Range: 'bytes=0-0' }, responseType: 'arraybuffer', signal })
        } catch (inner) {
            throw await binaryError(inner)
        }
        throw await binaryError(err)
    }
    if (!Number.isFinite(total) || total <= 0) throw new V2Error(0, 'audio_size_unknown', 'audio size unknown', null)
    const parts: ArrayBuffer[] = []
    const progress: AudioLoadProgress = { loaded: 0, total, requests: 0, partial: 0 }
    while (progress.loaded < total) {
        const start = progress.loaded
        const end = Math.min(total, start + AUDIO_CHUNK_BYTES) - 1
        let res
        try {
            res = await BaseService({
                url,
                method: 'get',
                headers: { Range: `bytes=${start}-${end}` },
                responseType: 'arraybuffer',
                timeout: 0,
                signal,
            })
        } catch (err) {
            throw await binaryError(err)
        }
        const buf = res.data as ArrayBuffer
        progress.requests += 1
        if (res.status === 206) progress.partial += 1
        if (res.status === 200) {
            /* the server ignored the range: this is the whole file */
            parts.length = 0
            parts.push(buf)
            progress.loaded = buf.byteLength
            onProgress?.({ ...progress })
            break
        }
        if (buf.byteLength !== end - start + 1) {
            throw new V2Error(0, 'audio_range_mismatch', `expected ${end - start + 1} bytes, got ${buf.byteLength}`, null)
        }
        parts.push(buf)
        progress.loaded += buf.byteLength
        onProgress?.({ ...progress })
    }
    return { blob: new Blob(parts, { type: contentType || 'audio/wav' }), progress }
}
