/**
 * Softphone endpoints. Everything goes through the v2 transport (bearer token, the global 401
 * sign-out, `V2Error`).
 *
 * The SIP password comes back from `POST /v2/me/sip-credentials` and is handed straight to the
 * engine, which keeps it in memory only: it is never written to localStorage, sessionStorage,
 * IndexedDB, the Redux store or a BroadcastChannel message, and it is fetched again on every
 * reconnect. The POST is not idempotent on the server (the idempotency store would keep the
 * body); the key the transport requires is sent and ignored.
 */
import BaseService from '../BaseService'
import { V2Error, newIdempotencyKey, v2Get, v2Post, v2Put } from '../v2/http'
import type { CallingDevices, LiveAttempt, MyCalling, SipCredential, SipCredentialStatus } from './types'

const enc = encodeURIComponent

export const softphoneApi = {
    issueCredential: () => v2Post<SipCredential>('/v2/me/sip-credentials', {}, newIdempotencyKey()),

    credentialStatus: () => v2Get<SipCredentialStatus>('/v2/me/sip-credentials'),

    revokeCredential: () => v2Post<{ revoked: number }>('/v2/me/sip-credentials/revoke', {}, newIdempotencyKey()),

    myCalling: () => v2Get<MyCalling>('/v2/me/calling'),

    savePreferences: (
        expectedRevision: number,
        body: { auto_answer_attempts?: boolean; devices?: CallingDevices },
        key: string
    ) => v2Put<MyCalling>('/v2/me/calling/preferences', { expected_revision: expectedRevision, ...body }, key),

    /** My attempt that holds a reservation (the live view), or null. */
    myActiveAttempt: (signal?: AbortSignal) =>
        v2Get<{ attempt: LiveAttempt | null }>('/v2/me/calls/active', undefined, signal),
}

/**
 * The P1 live-call view with its ETag: `null` means 304 (nothing changed since `etag`).
 */
export async function fetchLiveAttempt(
    attemptId: string,
    etag: string | null,
    signal?: AbortSignal
): Promise<LiveAttempt | null> {
    try {
        const res = await BaseService({
            url: `/v2/calls/${enc(attemptId)}/live`,
            method: 'get',
            headers: etag ? { 'If-None-Match': etag } : {},
            validateStatus: (s) => s === 200 || s === 304,
            signal,
        })
        if (res.status === 304) return null
        const data = (res.data?.data ?? null) as LiveAttempt | null
        if (data) data.etag = (res.headers?.etag as string | undefined) || undefined
        return data
    } catch (err) {
        const ax = err as { response?: { status: number; data?: { code?: string; message?: string; error?: unknown } } }
        if (ax?.response) {
            const body = ax.response.data || {}
            throw new V2Error(ax.response.status, body.code || 'unknown', body.message || '', body.error ?? null)
        }
        throw new V2Error(0, 'network_error', (err as Error)?.message || 'network error', null)
    }
}
