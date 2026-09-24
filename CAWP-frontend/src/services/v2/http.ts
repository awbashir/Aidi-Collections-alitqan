/**
 * Phase 1 v2 service layer: transport.
 *
 * The v2 routes (`/v2/...`) use real HTTP codes and one envelope:
 *
 *   success  2xx  {"message", "status": true,  "data": ...}
 *   failure  4xx  {"message", "status": false, "error": ..., "code": "snake_case"}
 *
 * Requests go through the legacy `BaseService` axios instance on purpose: it adds
 * the bearer token and keeps its global 401 interceptor (sign-out). Nothing here
 * adds interceptors, so legacy screens behave exactly as before.
 *
 * Every mutation sends an `Idempotency-Key`. The caller generates it **once per
 * user action** (see `useActionKey`) and passes the same key when the user retries
 * that action, so a retry after a lost response is a replay, not a second write.
 */
import type { AxiosError, AxiosRequestConfig } from 'axios'
import BaseService from '../BaseService'

export type V2Envelope<T> = {
    message?: string
    status?: boolean
    data?: T
    error?: unknown
    code?: string
}

/** A failed v2 call. `status` 0 means no response (network, CORS, timeout). */
export class V2Error extends Error {
    status: number
    code: string
    detail: unknown
    serverMessage: string

    constructor(status: number, code: string, serverMessage: string, detail: unknown) {
        super(serverMessage || code)
        this.name = 'V2Error'
        this.status = status
        this.code = code
        this.detail = detail
        this.serverMessage = serverMessage
    }

    /** Revision / stale conflicts: someone else changed the row since it was loaded. */
    get isConflict(): boolean {
        return (
            this.status === 409 &&
            [
                'revision_conflict',
                'stale_correction',
                'follow_up_not_open',
                'conflict',
                'payment_already_decided',
                'promise_not_cancellable',
                'allocation_already_voided',
                'correction_not_pending',
                // Phase 2: the bulk job moved on (confirmed/cancelled elsewhere) since it was loaded
                'job_not_ready',
                // Phase 3: the request / plan moved on since it was loaded
                'invalid_transition',
                'request_cancelled',
                'plan_not_cancellable',
                'plan_not_allocatable',
                'plan_already_active',
                // Phase 5: a call / extension / campaign / review flag moved on since it was loaded
                'already_dispatching',
                'wrapup_exists',
                'owner_changed',
                'not_uncertain',
                'flag_already_resolved',
                'block_already_released',
                'legacy_extension_changed',
                // Phase 6: a draft / quality recording moved on since it was loaded
                'draft_saved',
                'draft_discarded',
                'already_approved',
                'processing_superseded',
            ].includes(this.code)
        )
    }

    get isForbidden(): boolean {
        return this.status === 403
    }

    get isNotFound(): boolean {
        return this.status === 404
    }
}

export const isV2Error = (e: unknown): e is V2Error => e instanceof V2Error

/** A UUID v4 for `Idempotency-Key`. */
export function newIdempotencyKey(): string {
    const c = (globalThis as { crypto?: Crypto }).crypto
    if (c && typeof c.randomUUID === 'function') return c.randomUUID()
    const bytes = new Uint8Array(16)
    if (c && typeof c.getRandomValues === 'function') {
        c.getRandomValues(bytes)
    } else {
        for (let i = 0; i < 16; i += 1) bytes[i] = Math.floor(Math.random() * 256)
    }
    bytes[6] = (bytes[6] & 0x0f) | 0x40
    bytes[8] = (bytes[8] & 0x3f) | 0x80
    const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('')
    return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`
}

type Query = Record<string, string | number | boolean | undefined | null | Array<string | number>>

export type V2RequestOptions = {
    method?: 'get' | 'post' | 'put' | 'patch'
    params?: Query
    data?: unknown
    /** Required on every mutation. One key per user action; reuse it for retries. */
    idempotencyKey?: string
    signal?: AbortSignal
}

const cleanParams = (params?: Query) => {
    if (!params) return undefined
    const out: Record<string, string | number | boolean> = {}
    Object.entries(params).forEach(([k, v]) => {
        if (v === undefined || v === null || v === '') return
        if (Array.isArray(v)) {
            if (v.length) out[k] = v.join(',')
            return
        }
        out[k] = v
    })
    return out
}

function toV2Error(err: unknown): V2Error {
    const ax = err as AxiosError<V2Envelope<unknown>>
    if (ax?.response) {
        const { status, data } = ax.response
        const body = (data && typeof data === 'object' ? data : {}) as V2Envelope<unknown>
        /* Some v2 routes (the worklist ones) answer a missing permission with the
         * legacy `required_roles` body `{"message"}` and no code. */
        const code =
            body.code ||
            (status === 403
                ? 'forbidden'
                : status === 404
                ? 'not_found'
                : status === 409
                ? 'conflict'
                : status === 422
                ? 'validation_failed'
                : status === 423
                ? 'case_locked'
                : status >= 500
                ? 'internal_error'
                : 'unknown')
        return new V2Error(status, code, body.message || '', body.error ?? null)
    }
    if ((ax as { code?: string })?.code === 'ERR_CANCELED') {
        return new V2Error(0, 'canceled', 'canceled', null)
    }
    return new V2Error(0, 'network_error', (ax as Error)?.message || 'network error', null)
}

/** Calls a v2 route and returns `data` from the envelope, or throws `V2Error`. */
export async function v2Request<T>(url: string, options: V2RequestOptions = {}): Promise<T> {
    const { method = 'get', params, data, idempotencyKey, signal } = options
    const headers: Record<string, string> = {}
    if (method !== 'get') {
        if (!idempotencyKey) {
            // A programming error: never send a v2 mutation without a key.
            throw new V2Error(0, 'idempotency_key_required', 'missing Idempotency-Key', null)
        }
        headers['Idempotency-Key'] = idempotencyKey
    }
    const config: AxiosRequestConfig = {
        url,
        method,
        params: cleanParams(params),
        data: method === 'get' ? undefined : data ?? {},
        headers,
        signal,
    }
    try {
        const res = await BaseService(config)
        const body = res.data as V2Envelope<T>
        if (body && typeof body === 'object' && 'data' in body) return body.data as T
        return body as unknown as T
    } catch (err) {
        throw toV2Error(err)
    }
}

export const v2Get = <T>(url: string, params?: Query, signal?: AbortSignal) =>
    v2Request<T>(url, { method: 'get', params, signal })

export const v2Post = <T>(url: string, data: unknown, idempotencyKey: string) =>
    v2Request<T>(url, { method: 'post', data, idempotencyKey })

export const v2Put = <T>(url: string, data: unknown, idempotencyKey: string) =>
    v2Request<T>(url, { method: 'put', data, idempotencyKey })

/** Phase 5: `PATCH` (campaign edits). Same rules as the other mutations. */
export const v2Patch = <T>(url: string, data: unknown, idempotencyKey: string) =>
    v2Request<T>(url, { method: 'patch', data, idempotencyKey })
