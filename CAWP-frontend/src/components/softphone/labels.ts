/** Status tones and labels for the softphone UI (texts in locales `sp.*`). */
import type { Translate } from '@/services/v2'
import type { StatusTone } from '@/components/shared/StatusPill'
import type { LineState, PhoneStatus } from '@/services/softphone'

export const STATUS_TONE: Record<PhoneStatus, StatusTone> = {
    registered: 'success',
    starting: 'info',
    connecting: 'info',
    offline: 'neutral',
    failed: 'danger',
    no_extension: 'warning',
    not_configured: 'warning',
    unsupported: 'danger',
    stopped: 'neutral',
}

/** The four chip states of §3.2, plus the clear non-registrable ones. */
export const statusLabel = (t: Translate, status: PhoneStatus) => t(`sp.status.${status}`)

export const LINE_TONE: Record<LineState, StatusTone> = {
    ringing: 'warning',
    calling: 'info',
    connecting: 'info',
    active: 'success',
    held: 'neutral',
    ended: 'neutral',
}

export const lineStateLabel = (t: Translate, state: LineState) => t(`sp.line.state.${state}`)

export const ATTEMPT_TONE = (state: string): StatusTone => {
    if (state === 'bridged' || state === 'customer_answered') return 'success'
    if (state === 'uncertain' || state === 'failed') return 'danger'
    if (state === 'ended' || state === 'cancelled') return 'neutral'
    return 'info'
}

/** Seconds -> m:ss (tabular). */
export const clock = (ms: number) => {
    const s = Math.max(0, Math.floor(ms / 1000))
    return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
}
