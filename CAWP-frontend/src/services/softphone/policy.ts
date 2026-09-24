/**
 * Pure softphone rules (no side effects), kept apart so they read like the notes.
 *
 * Auto-answer (PROJECT-NOTES §3.2, spec hard rule 2): only a call that carries our attempt tag
 * (`X-CAWP-Attempt: <uuid>`, set on the extension-first agent leg by the Originate) AND whose
 * value is the caller's own live attempt on this extension (`GET /v2/me/calls/active`) is
 * answered automatically. Everything else rings normally with answer/reject.
 *
 * Dialing: only internal extensions that match the server's `dial_policy.internal_pattern`
 * (an external number never matches; the server refuses patterns that would). Outbound calls
 * to customers go through click-to-call on a case contact, never the dialpad.
 */
import type { AutoAnswerReason, LiveAttempt } from './types'

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/
const LIVE_STATES = ['authorized', 'dispatched', 'agent_ringing', 'agent_answered', 'customer_dialing', 'customer_answered', 'bridged']
const DEFAULT_INTERNAL = /^[0-9]{3,6}$/
const EXTERNAL_PROBES = ['0501234567', '966501234567', '+966501234567', '00966501234567']

export const parseAttemptTag = (value: unknown): string | null => {
    if (typeof value !== 'string') return null
    const v = value.trim().toLowerCase()
    return UUID_RE.test(v) ? v : null
}

export function autoAnswerDecision(input: {
    tag: string | null
    preferenceEnabled: boolean
    myExtension: string | null
    active: LiveAttempt | null
    busyOnAnotherLine: boolean
}): AutoAnswerReason {
    const { tag, preferenceEnabled, myExtension, active, busyOnAnotherLine } = input
    if (!tag) return 'no_tag'
    if (!preferenceEnabled) return 'disabled_by_preference'
    if (!active || active.id !== tag || !myExtension || active.extension !== myExtension) return 'not_my_active_attempt'
    if (!LIVE_STATES.includes(active.state)) return 'attempt_not_live'
    // Another call already in conversation: never auto-answer over it (the agent decides).
    if (busyOnAnotherLine) return 'busy_on_another_line'
    return 'auto_answered'
}

/** The server's internal-dial pattern, refused (default) when it would match an external number. */
export function internalDialRegex(pattern: string | null | undefined): RegExp {
    if (!pattern) return DEFAULT_INTERNAL
    let re: RegExp
    try {
        re = new RegExp(pattern)
    } catch {
        return DEFAULT_INTERNAL
    }
    return EXTERNAL_PROBES.some((n) => re.test(n)) ? DEFAULT_INTERNAL : re
}

export const isInternalTarget = (value: string, pattern: string | null | undefined): boolean =>
    /^[0-9]{2,6}$/.test(value) && internalDialRegex(pattern).test(value)

export const DTMF_KEYS = ['1', '2', '3', '4', '5', '6', '7', '8', '9', '*', '0', '#'] as const
export const isDtmf = (key: string) => (DTMF_KEYS as readonly string[]).includes(key)
