/**
 * Labels, tones and formatting shared by the Phase 5 calling screens (no components here,
 * so the component files keep fast refresh). Honesty rules: "submitted" is never
 * "connected", a conversation is only the PBX's bridge evidence, and an unknown duration
 * stays "Unknown" (never 0).
 */
import type { StatusTone } from '@/components/shared/StatusPill'
import type { Translate } from '@/services/v2'
import type { CallState, CampaignStatus, MemberState, Outcome } from '@/services/v2'

export const CALL_STATE_TONE: Record<CallState, StatusTone> = {
    prepared: 'info',
    authorized: 'info',
    dispatched: 'info',
    agent_ringing: 'info',
    agent_answered: 'info',
    customer_dialing: 'info',
    customer_answered: 'info',
    bridged: 'success',
    ended: 'neutral',
    failed: 'danger',
    cancelled: 'neutral',
    uncertain: 'warning',
}

export const OUTCOME_TONE: Record<Outcome, StatusTone> = {
    connected: 'success',
    no_answer: 'neutral',
    busy: 'neutral',
    promise_discussed: 'info',
    dispute: 'warning',
    wrong_number: 'danger',
    do_not_call: 'danger',
}

export const CAMPAIGN_TONE: Record<CampaignStatus, StatusTone> = {
    draft: 'neutral',
    running: 'success',
    paused: 'warning',
    draining: 'info',
    stopped: 'neutral',
}

export const MEMBER_TONE: Record<MemberState, StatusTone> = {
    ready: 'success',
    waiting_next: 'info',
    on_call: 'info',
    wrap_up: 'warning',
    paused: 'warning',
    offline: 'neutral',
    removed: 'neutral',
}

export const callStateLabel = (t: Translate, state: string) => t(`p5.state.${state}`, { defaultValue: state })
export const outcomeLabel = (t: Translate, outcome: string) => t(`p5.outcome.${outcome}`, { defaultValue: outcome })

/** Why a call ended / failed / is uncertain, in words (`p5.reason.<code>`; the rule codes fall back to their refusal). */
export const reasonLabel = (t: Translate, code: string | null | undefined) =>
    code
        ? t(`p5.reason.${code}`, {
              // p10.callctl.reason: call control (2026-10-20), e.g. `transferred`
              defaultValue: t(`p5.refusal.${code}`, { defaultValue: t(`p10.callctl.reason.${code}`, { defaultValue: code.replace(/_/g, ' ') }) }),
          })
        : ''

/** Seconds as m:ss / h:mm:ss. `null` is UNKNOWN (never 0). */
export const formatDuration = (seconds: number | null | undefined): string | null => {
    if (seconds === null || seconds === undefined) return null
    const s = Math.max(0, Math.floor(seconds))
    const h = Math.floor(s / 3600)
    const m = Math.floor((s % 3600) / 60)
    const r = s % 60
    return h > 0 ? `${h}:${String(m).padStart(2, '0')}:${String(r).padStart(2, '0')}` : `${m}:${String(r).padStart(2, '0')}`
}

/** "Riyadh HH:MM:SS" of an instant (the timeline steps are seconds apart). */
export function riyadhClock(iso: string | null | undefined): string {
    if (!iso) return '—'
    const d = new Date(iso)
    if (Number.isNaN(d.getTime())) return iso
    return new Intl.DateTimeFormat('en-GB', {
        timeZone: 'Asia/Riyadh',
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
        hourCycle: 'h23',
    }).format(d)
}

/** A campaign pool exclusion reason in words. */
export const poolReason = (t: Translate, reason: string) => t(`p5.pool.reason.${reason}`, { defaultValue: reason.replace(/_/g, ' ') })

/** Why a campaign member is in their state, in words. */
export const memberReason = (t: Translate, reason: string | null) =>
    reason ? t(`p5.member.reason.${reason}`, { defaultValue: reasonLabel(t, reason) }) : ''
