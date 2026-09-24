/**
 * Voice lab endpoints (api/voice_lab.py) through the v2 transport (bearer token, global 401
 * sign-out, `V2Error`), plus `GET /v2/ai/status` for the honest readiness state.
 *
 * `POST /v2/ai/voice-lab/sessions` is not idempotent on the server (the idempotency store would
 * keep the client secret); the key the transport requires is sent and ignored. The one-open-
 * session rule stops duplicates instead.
 */
import appConfig from '@/configs/app.config'
import { REQUEST_HEADER_AUTH_KEY, TOKEN_TYPE } from '@/constants/api.constant'
import store from '@/store'
import { newIdempotencyKey, v2Get, v2Post } from '../v2/http'
import type { AiStatus, MintedSession, MySessions, VoiceEndOutcome, VoiceLabCatalogue, VoiceLang, VoiceSessionRecord } from './types'

const enc = encodeURIComponent

export const voiceLabApi = {
    status: (signal?: AbortSignal) => v2Get<AiStatus>('/v2/ai/status', undefined, signal),

    scenarios: (signal?: AbortSignal) => v2Get<VoiceLabCatalogue>('/v2/ai/voice-lab/scenarios', undefined, signal),

    mySessions: (signal?: AbortSignal) => v2Get<MySessions>('/v2/ai/voice-lab/sessions', { page_size: 10 }, signal),

    start: (body: { scenario_id: string; language: VoiceLang }) =>
        v2Post<MintedSession>('/v2/ai/voice-lab/sessions', body, newIdempotencyKey()),

    end: (sessionId: string, body: { outcome: VoiceEndOutcome; connected_seconds?: number | null }) =>
        v2Post<{ session: VoiceSessionRecord; changed: boolean }>(
            `/v2/ai/voice-lab/sessions/${enc(sessionId)}/end`,
            body.connected_seconds === null || body.connected_seconds === undefined
                ? { outcome: body.outcome }
                : body,
            newIdempotencyKey()
        ),
}

/**
 * The end report when the page is being unloaded (tab closed, reload): a `keepalive` fetch so the
 * browser still sends it after the page is gone. Best effort: if it is lost, the server expires the
 * session on its own and records the duration as unknown.
 */
export function endOnUnload(sessionId: string, outcome: VoiceEndOutcome, connectedSeconds: number | null) {
    try {
        const token = store.getState().auth.session.token
        const headers: Record<string, string> = {
            'Content-Type': 'application/json',
            'Idempotency-Key': newIdempotencyKey(),
        }
        if (token) headers[REQUEST_HEADER_AUTH_KEY] = `${TOKEN_TYPE}${token}`
        const body: Record<string, unknown> = { outcome }
        if (connectedSeconds !== null) body.connected_seconds = connectedSeconds
        void fetch(`${appConfig.apiPrefix}/v2/ai/voice-lab/sessions/${enc(sessionId)}/end`, {
            method: 'POST',
            headers,
            body: JSON.stringify(body),
            keepalive: true,
        }).catch(() => undefined)
    } catch {
        /* the server expires the session by itself */
    }
}
