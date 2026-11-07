/**
 * AI calling agent test (2026-11-04), CAWP-API/api/ai_test_calls.py.
 *
 *   GET  /v2/ai/test-calls/options    voices, default script, outbound numbers, limits, AI readiness, service
 *   GET  /v2/ai/test-calls            my test calls
 *   POST /v2/ai/test-calls            {destination, caller_id?, voice?, instructions}
 *   GET  /v2/ai/test-calls/<id>       one call (state, result, transcript)
 *   POST /v2/ai/test-calls/<id>/stop  hang up / cancel
 */
import { v2Get, v2Post, v2Request } from './http'
import type { OutboundNumber } from './p5Types'

export type AiTestState = 'queued' | 'preparing' | 'dialing' | 'ringing' | 'answered' | 'ended' | 'failed'
export type AiTestResult =
    | 'completed' | 'no_answer' | 'busy' | 'congestion' | 'unavailable' | 'denied' | 'cancelled'
    | 'ai_refused' | 'ai_failed' | 'sip_failed' | 'service_stopped' | 'time_limit'

export type AiTestCall = {
    id: string
    destination: string
    caller_id: string | null
    voice: string
    instructions: string
    state: AiTestState
    result: AiTestResult | null
    sip_status: number | null
    reason_code: string | null
    model: string | null
    transcript: { role: 'agent' | 'client'; text: string; at: string }[]
    stop_requested: boolean
    created_at: string
    dialing_at: string | null
    answered_at: string | null
    ended_at: string | null
    talk_seconds: number | null
}

export type AiTestOptions = {
    voices: string[]
    default_voice: string
    default_instructions: string
    outbound_numbers: OutboundNumber[]
    limits: { max_seconds: number; per_day: number }
    ready: boolean
    not_ready_reasons: string[]
    model: string | null
    service: { running: boolean; seen_at: string | null }
}

export const OPEN_STATES: AiTestState[] = ['queued', 'preparing', 'dialing', 'ringing', 'answered']

const newKey = () => (crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`)

export const aiTestCallsApi = {
    options: (signal?: AbortSignal) => v2Get<AiTestOptions>('/v2/ai/test-calls/options', undefined, signal),
    list: (signal?: AbortSignal) =>
        v2Get<{ items: AiTestCall[]; total: number }>('/v2/ai/test-calls', { page_size: 20 }, signal),
    get: (id: string, signal?: AbortSignal) => v2Get<AiTestCall>(`/v2/ai/test-calls/${encodeURIComponent(id)}`, undefined, signal),
    create: (body: { destination: string; caller_id?: string | null; voice: string; instructions: string }) =>
        v2Post<AiTestCall>('/v2/ai/test-calls', body, newKey()),
    stop: (id: string) => v2Request<AiTestCall>(`/v2/ai/test-calls/${encodeURIComponent(id)}/stop`, { method: 'post', data: {} }),
}
