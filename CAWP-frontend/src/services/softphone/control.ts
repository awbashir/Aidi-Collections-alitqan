/**
 * Server-controlled call control (2026-10-20, `api/calling_control.py`): transfers (blind and
 * attended), adding a participant (conference) and supervisor listening. The browser never sends
 * a SIP REFER: it asks the API, the calling service performs it on the PBX (AMI), and the result
 * is followed by polling the control (`GET /v2/calling/control/<id>`). A 202 means the command
 * was queued; "done" means the PBX accepted it (submitted), and the legs' state comes from the
 * PBX channel list (`phase`). Targets are app users' internal extensions only.
 */
import { isV2Error, newIdempotencyKey, v2Get, v2Post } from '../v2/http'
import type { Translate } from '../v2/errors'
import { describeP5Error } from '../v2/p5Errors'

const enc = encodeURIComponent

export const CONTROL_HEADER = 'X-CAWP-Control'

export type ControlKind = 'transfer_blind' | 'transfer_attended' | 'conference' | 'spy'
export type ControlState = 'queued' | 'active' | 'completed' | 'cancelled' | 'failed' | 'ended'
export type SpyMode = 'listen' | 'whisper' | 'barge'
export type ControlOp = 'complete' | 'cancel' | 'remove' | 'mode' | 'stop'
export type LegPhase = 'ringing' | 'up' | 'gone'

export type CallControl = {
    id: string
    tag: string
    kind: ControlKind
    state: ControlState
    mode: SpyMode | null
    result_code: string | null
    from: { extension: string; user_id: string; name: string | null }
    target: { extension: string; user_id: string | null; name: string | null }
    attempt_id: string | null
    case: { id: string | null; name?: string | null; account_number?: string | null; in_scope: boolean } | null
    created_at: string
    started_at: string | null
    completed_at: string | null
    ended_at: string | null
    last_op: { op: string; state: string; result_code: string | null; at: string } | null
    phase: { known: boolean; agent?: LegPhase; customer?: 'gone' | 'on_hold' | 'in_room' | 'transferring' | 'up'; target?: LegPhase }
    mine: boolean
    actions: ControlOp[]
    note: string
}

export type IncomingControl = CallControl & {
    role: 'listen' | 'transfer' | 'consult' | 'invite' | 'recall'
    auto_answer: boolean
}

export type ControlTarget = {
    extension: string
    user_id: string
    name: string | null
    role: string | null
    presence: 'free' | 'on_call' | 'ringing' | 'busy' | 'unknown'
}

export type ControlCapabilities = {
    extension: string | null
    transfer: boolean
    conference: boolean
    listen: boolean
    modes: SpyMode[]
}

export const OPEN_STATES: ControlState[] = ['queued', 'active']
export const isOpen = (c: CallControl | null | undefined) =>
    !!c && (OPEN_STATES.includes(c.state) || !!(c.last_op && ['queued', 'sending'].includes(c.last_op.state)))

/** Accepts the header value (32 hex) or a UUID; anything else is not ours. */
export const parseControlTag = (value: unknown): string | null => {
    if (typeof value !== 'string') return null
    const v = value.trim().toLowerCase()
    if (/^[0-9a-f]{32}$/.test(v)) return v
    if (/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(v)) return v.replace(/-/g, '')
    return null
}

export const callControlApi = {
    mine: (signal?: AbortSignal) =>
        v2Get<{ capabilities: ControlCapabilities; items: CallControl[]; presence_known: boolean }>('/v2/me/call-controls', undefined, signal),

    targets: (q?: string, signal?: AbortSignal) =>
        v2Get<{ items: ControlTarget[]; presence_known: boolean }>('/v2/calling/control/targets', q ? { q } : undefined, signal),

    transfer: (body: { mode: 'blind' | 'attended'; target_extension: string; attempt_id?: string | null; remote?: string | null }) =>
        v2Post<CallControl>('/v2/calling/control/transfers', clean(body), newIdempotencyKey()),

    conference: (body: { target_extension: string; attempt_id?: string | null; remote?: string | null }) =>
        v2Post<CallControl>('/v2/calling/control/conferences', clean(body), newIdempotencyKey()),

    spy: (body: { call_id: string; extension: string; mode: SpyMode }) =>
        v2Post<CallControl>('/v2/calling/control/spy', body, newIdempotencyKey()),

    get: (id: string, signal?: AbortSignal) => v2Get<CallControl>(`/v2/calling/control/${enc(id)}`, undefined, signal),

    command: (id: string, op: ControlOp, mode?: SpyMode) =>
        v2Post<CallControl>(`/v2/calling/control/${enc(id)}/${op}`, mode ? { mode } : {}, newIdempotencyKey()),

    incoming: (tag: string, signal?: AbortSignal) =>
        v2Get<IncomingControl>(`/v2/calling/control/incoming/${enc(tag)}`, undefined, signal),
}

function clean<T extends Record<string, unknown>>(body: T): Partial<T> {
    const out: Partial<T> = {}
    Object.entries(body).forEach(([k, v]) => {
        if (v !== null && v !== undefined && v !== '') (out as Record<string, unknown>)[k] = v
    })
    return out
}

const CONTROL_ERRORS = new Set([
    'no_connected_call',
    'ambiguous_call',
    'call_not_two_party',
    'not_your_call',
    'control_in_progress',
    'control_busy',
    'control_not_active',
    'call_not_live',
    'call_not_connected',
    'target_not_app_user',
    'not_in_scope',
    'no_extension',
    'live_calls_unknown',
    'transport_not_connected',
    'op_not_applicable',
])

export function describeControlError(err: unknown, t: Translate): string {
    if (isV2Error(err) && CONTROL_ERRORS.has(err.code)) return t(`p10.callctl.err.${err.code}`)
    return describeP5Error(err, t)
}

/** Poll one control while it is open (1.5 s), then stop. */
export function pollControl(id: string, onData: (c: CallControl) => void, onError: (e: unknown) => void) {
    let stop = false
    let timer: ReturnType<typeof setTimeout> | null = null
    const ctrl = new AbortController()
    const tick = async () => {
        let next = 1500
        try {
            const c = await callControlApi.get(id, ctrl.signal)
            if (stop) return
            onData(c)
            if (c.ended_at) next = 0
            else if (!isOpen(c)) next = 4000
        } catch (e) {
            if (stop) return
            onError(e)
            next = 5000
        }
        if (!stop && next > 0) timer = setTimeout(tick, next)
    }
    void tick()
    return () => {
        stop = true
        ctrl.abort()
        if (timer) clearTimeout(timer)
    }
}
