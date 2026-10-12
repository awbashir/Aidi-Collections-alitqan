/**
 * The live-call view of one attempt (P1 `GET /v2/calls/<id>/live`): an honest state line,
 * the step timeline from the PBX evidence, the uncertainty explanation, open review flags,
 * the evidence events, and the actions the API allows right now (cancel while still
 * queued, wrap up once ended). Used by the docked panel and the calls screen.
 *
 * Hang up: the server cannot end a call (the AMI account has no `call` write), so the button
 * ends the agent's own leg in THIS tab's browser softphone (the line linked to the attempt).
 * Desk-phone calls, or a tab that only mirrors the phone, keep the "hang up on your phone" hint.
 *
 * Wording rules: an accepted Originate is "submitted", never "connected"; the agent's own
 * answer proves only the agent leg; only both legs in one bridge is a conversation;
 * `uncertain` is unknown, not ended.
 */
import classNames from 'classnames'
import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Check, ChevronDown, ChevronUp, CircleDashed, Minus, PhoneOff } from 'lucide-react'
import Button from '@/components/ui/Button'
import Input from '@/components/ui/Input'
import ActionDialog, { DialogField } from '@/components/v2/ActionDialog'
import { Ltr } from '@/components/v2/Display'
import { softphoneEngine, useSoftphoneSnapshot } from '@/services/softphone'
import { btnDanger } from '@/components/softphone/LineCard'
import { callsApi, describeDispatchError } from '@/services/v2'
import type { CallState, LiveCall } from '@/services/v2'
import { CaseCell, CallStateBadge, DurationText, ModeLabel, Notice, OutcomeBadge } from '../parts'
import { reasonLabel, riyadhClock } from '../labels'

const STEPS: Array<{ key: string; field: keyof LiveCall['timeline'] }> = [
    { key: 'authorized', field: 'authorized_at' },
    { key: 'dispatched', field: 'dispatched_at' },
    { key: 'agent_ringing', field: 'agent_ringing_at' },
    { key: 'agent_answered', field: 'agent_answered_at' },
    { key: 'customer_dialing', field: 'customer_dialing_at' },
    { key: 'customer_answered', field: 'customer_answered_at' },
    { key: 'bridged', field: 'bridged_at' },
    { key: 'ended', field: 'ended_at' },
]

const ORDER: CallState[] = [
    'prepared',
    'authorized',
    'dispatched',
    'agent_ringing',
    'agent_answered',
    'customer_dialing',
    'customer_answered',
    'bridged',
    'ended',
]

/** The one-sentence honest status for the current state. */
function statusLine(v: LiveCall, t: (k: string, o?: Record<string, unknown>) => string): string {
    switch (v.state) {
        case 'prepared':
        case 'authorized':
            return v.dispatch && v.dispatch.state !== 'queued'
                ? t('p5.live.line.sending')
                : t('p5.live.line.queued')
        case 'dispatched':
            return t('p5.live.line.submitted')
        case 'agent_ringing':
            return t('p5.live.line.agentRinging')
        case 'agent_answered':
            return t('p5.live.line.agentAnswered')
        case 'customer_dialing':
            return t('p5.live.line.customerDialing')
        case 'customer_answered':
            return t('p5.live.line.customerAnswered')
        case 'bridged':
            return t('p5.live.line.bridged')
        case 'ended':
            return v.conversation.evidenced ? t('p5.live.line.endedAfterConversation') : t('p5.live.line.endedNoConversation')
        case 'failed':
            return t('p5.live.line.failed')
        case 'cancelled':
            return t('p5.live.line.cancelled')
        case 'uncertain':
            return t('p5.live.line.uncertain')
        default:
            return v.state
    }
}

const StepIcon = ({ status }: { status: 'done' | 'current' | 'skipped' | 'pending' }) => {
    if (status === 'done') return <Check aria-hidden size={13} strokeWidth={3} className="text-success" />
    if (status === 'current') return <CircleDashed aria-hidden size={13} strokeWidth={2.5} className="animate-spin text-azure-600 [animation-duration:3s]" />
    if (status === 'skipped') return <Minus aria-hidden size={13} strokeWidth={2.5} className="text-gray-400" />
    return <span aria-hidden className="inline-block h-[9px] w-[9px] rounded-full border-2 border-gray-300" />
}

export const CallTimelineSteps = ({ view }: { view: Pick<LiveCall, 'timeline' | 'state' | 'live'> }) => {
    const { t } = useTranslation()
    const idx = ORDER.indexOf(view.state)
    const lastDone = STEPS.reduce((acc, s, i) => (view.timeline[s.field] ? i : acc), -1)
    return (
        <ol className="flex flex-col gap-1" data-testid="p5-live-steps">
            {STEPS.map((s, i) => {
                const at = view.timeline[s.field]
                let status: 'done' | 'current' | 'skipped' | 'pending' = 'pending'
                if (at) status = 'done'
                else if (i < lastDone) status = 'skipped'
                else if (view.live && ORDER.indexOf(s.key as CallState) === idx + 1) status = 'current'
                return (
                    <li
                        key={s.key}
                        data-step={s.key}
                        data-status={status}
                        className={classNames(
                            'flex items-center justify-between gap-2 rounded-md px-2 py-1 text-[12.5px]',
                            status === 'current' && 'bg-azure-50 dark:bg-night-raised',
                            s.key === 'bridged' && status === 'done' && 'bg-success-tint dark:bg-night-raised'
                        )}
                    >
                        <span className="flex items-center gap-2">
                            <span className="inline-flex w-4 justify-center">
                                <StepIcon status={status} />
                            </span>
                            <span className={classNames(status === 'pending' || status === 'skipped' ? 'text-gray-500' : 'text-gray-800 dark:text-night-body', s.key === 'bridged' && status === 'done' && 'font-extrabold text-success')}>
                                {t(`p5.step.${s.key}`)}
                            </span>
                        </span>
                        <span className="tabular-nums text-gray-500" dir="ltr">
                            {at ? riyadhClock(at) : status === 'skipped' ? t('p5.step.notSeen') : ''}
                        </span>
                    </li>
                )
            })}
        </ol>
    )
}

const eventLabel = (t: (k: string, o?: Record<string, unknown>) => string, kind: string) => {
    if (kind.startsWith('dialplan_')) {
        const st = kind.slice('dialplan_'.length)
        return t('p5.event.dialplan', { state: t(`p5.event.dialplanState.${st}`, { defaultValue: st.replace(/_/g, ' ') }) })
    }
    return t(`p5.event.${kind}`, { defaultValue: kind.replace(/_/g, ' ') })
}

export const LiveEvents = ({ view }: { view: LiveCall }) => {
    const { t } = useTranslation()
    const [open, setOpen] = useState(false)
    if (!view.events.length) return <div className="text-[12px] text-gray-500">{t('p5.live.noEvents')}</div>
    return (
        <div data-testid="p5-live-events">
            <button
                type="button"
                className="flex items-center gap-1 text-[12.5px] font-extrabold text-azure-600"
                onClick={() => setOpen((o) => !o)}
                aria-expanded={open}
            >
                {open ? <ChevronUp size={14} aria-hidden /> : <ChevronDown size={14} aria-hidden />}
                {t('p5.live.events', { count: view.events.length })}
            </button>
            {open && (
                <ol className="mt-2 max-h-56 overflow-y-auto border-s border-gray-200 ps-3 text-[12px] dark:border-night-border">
                    {view.events.map((e, i) => (
                        <li key={`${e.event_id ?? 'agi'}-${i}`} className="mb-1" data-kind={e.kind}>
                            <span className="tabular-nums text-gray-500" dir="ltr">
                                {e.at_pbx ? riyadhClock(new Date(Number(e.at_pbx) * 1000).toISOString()) : riyadhClock(e.at)}
                            </span>{' '}
                            <span className="text-gray-800 dark:text-night-body">{eventLabel(t, e.kind)}</span>
                            {e.leg !== 'other' && e.source === 'pbx' && <span className="text-gray-500"> · {t(`p5.leg.${e.leg}`)}</span>}
                            {e.source === 'agi' && <span className="text-gray-500"> · {t('p5.live.agi')}</span>}
                            {e.detail && (e.detail.reason || e.detail.dial_status || e.detail.cause) ? (
                                <span className="text-gray-500" dir="ltr">
                                    {' '}
                                    ({String(e.detail.reason || e.detail.dial_status || `cause ${e.detail.cause}`)})
                                </span>
                            ) : null}
                        </li>
                    ))}
                </ol>
            )}
        </div>
    )
}

/** Cancel before the calling service claimed the queued call (reason optional). */
export const CancelCallDialog = ({
    view,
    isOpen,
    onClose,
    onDone,
}: {
    view: LiveCall
    isOpen: boolean
    onClose: () => void
    onDone: (v: LiveCall) => void
}) => {
    const { t } = useTranslation()
    const [reason, setReason] = useState('')
    return (
        <ActionDialog
            isOpen={isOpen}
            title={t('p5.live.cancelTitle')}
            confirmLabel={t('p5.live.cancelConfirm')}
            tone="danger"
            testId="p5-cancel-call"
            describeError={describeDispatchError}
            onClose={() => {
                setReason('')
                onClose()
            }}
            onSubmit={async (key) => {
                const res = await callsApi.cancel(
                    view.id,
                    { expected_revision: view.revision, ...(reason.trim() ? { reason: reason.trim() } : {}) },
                    key
                )
                setReason('')
                onDone(res)
            }}
        >
            <div className="mb-3 text-[13.5px] text-gray-700 dark:text-night-body">{t('p5.live.cancelHint')}</div>
            <DialogField label={t('p5.live.cancelReason')}>
                <Input value={reason} maxLength={500} onChange={(e) => setReason(e.target.value)} data-testid="p5-cancel-call-reason" />
            </DialogField>
        </ActionDialog>
    )
}

const LiveCallPanel = ({
    view,
    onChanged,
    onWrapup,
    compact,
}: {
    view: LiveCall
    /** A mutation returned a newer view (cancel). */
    onChanged: (v: LiveCall) => void
    onWrapup?: () => void
    compact?: boolean
}) => {
    const { t } = useTranslation()
    const [cancelling, setCancelling] = useState(false)
    const final = !view.live && view.state !== 'uncertain'
    const engine = useMemo(() => softphoneEngine(), [])
    const phone = useSoftphoneSnapshot(engine)
    const myLine =
        view.live && phone.role !== 'follower'
            ? phone.lines.find((l) => l.state !== 'ended' && (l.attemptId === view.id || l.attemptTag === view.id))
            : undefined
    return (
        <div className="flex flex-col gap-3" data-testid="p5-live" data-state={view.state} data-attempt={view.id} data-evidenced={view.conversation.evidenced ? '1' : '0'}>
            <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                    <CaseCell kase={view.case} />
                    <div className="mt-0.5 flex flex-wrap items-center gap-x-2 text-[12px] text-gray-600">
                        <ModeLabel mode={view.mode} />
                        {view.destination && <Ltr>{view.destination}</Ltr>}
                        {view.extension && (
                            <span>
                                {t('p5.live.extension')} <Ltr>{view.extension}</Ltr>
                            </span>
                        )}
                    </div>
                </div>
                <CallStateBadge state={view.state} />
            </div>

            <div
                className={classNames(
                    'rounded-md px-3 py-2 text-[13px]',
                    view.state === 'bridged' ? 'bg-success-tint text-gray-800' : 'bg-gray-50 text-gray-800 dark:bg-night-raised dark:text-night-body'
                )}
                data-testid="p5-live-line"
            >
                {statusLine(view, t)}
                {view.dispatch?.ami_response && view.state !== 'uncertain' && ['dispatched', 'agent_ringing'].includes(view.state) && (
                    <div className="mt-1 text-[11.5px] text-gray-600" data-testid="p5-live-submitted-note">
                        {t('p5.live.submittedNote', { response: view.dispatch.ami_response })}
                    </div>
                )}
            </div>

            {view.uncertain && (
                <Notice tone="warning" title={t('p5.live.uncertainTitle')} testId="p5-live-uncertain">
                    <div>{t('p5.live.uncertainBody', { reason: reasonLabel(t, view.uncertain.reason) })}</div>
                    <div className="mt-1 text-[12px] text-gray-600">{t('p5.live.uncertainRule')}</div>
                </Notice>
            )}

            {(view.state === 'ended' || view.state === 'failed' || view.state === 'cancelled') && (
                <div className="grid grid-cols-2 gap-2 text-[12.5px]" data-testid="p5-live-final">
                    <div>
                        <div className="text-eyebrow">{t('p5.live.reason')}</div>
                        <div data-reason={view.reason_code || ''}>{reasonLabel(t, view.reason_code) || '—'}</div>
                    </div>
                    <div>
                        <div className="text-eyebrow">{t('p5.live.duration')}</div>
                        <DurationText seconds={view.duration_seconds} source={view.duration_source} />
                    </div>
                </div>
            )}

            {!compact || view.live ? <CallTimelineSteps view={view} /> : null}

            <div className="text-[11.5px] text-gray-500" data-testid="p5-live-rule">
                {t('p5.live.evidenceRule')}
            </div>

            {view.flags.length > 0 && (
                <Notice tone="neutral" title={t('p5.live.flagsTitle')} testId="p5-live-flags">
                    <ul className="list-inside list-disc">
                        {view.flags.map((f) => (
                            <li key={f.id} data-flag-kind={f.kind}>
                                {t(`p5.flag.kind.${f.kind}`)}
                                {f.blocks_calling && <span className="ms-1 font-extrabold text-danger">{t('p5.flag.blocksCalling')}</span>}
                            </li>
                        ))}
                    </ul>
                </Notice>
            )}

            {view.wrapup && (
                <div className="flex flex-wrap items-center gap-2 text-[12.5px]" data-testid="p5-live-wrapped">
                    <span className="text-gray-600">{t('p5.live.wrappedUp')}</span>
                    <OutcomeBadge outcome={view.wrapup.outcome} size="row" />
                    {view.wrapup.needs_review && (
                        <span className="font-extrabold text-warning">{t('p5.wrapup.awaitingReview')}</span>
                    )}
                </div>
            )}

            <LiveEvents view={view} />

            <div className="flex flex-wrap items-center gap-2">
                {view.actions.can_wrapup && onWrapup && (
                    <Button size="sm" variant="solid" onClick={onWrapup} data-testid="p5-live-wrapup">
                        {t('p5.live.wrapup')}
                    </Button>
                )}
                {view.actions.can_cancel && (
                    <Button size="sm" variant="default" onClick={() => setCancelling(true)} data-testid="p5-live-cancel">
                        {t('p5.live.cancel')}
                    </Button>
                )}
                {myLine && (
                    <button type="button" className={btnDanger} onClick={() => engine.hangup(myLine.id)} data-testid="p5-live-hangup">
                        <PhoneOff size={15} aria-hidden />
                        {t('p5.live.hangup')}
                    </button>
                )}
                {!view.actions.can_cancel && view.live && !myLine && (
                    <span className="text-[11.5px] text-gray-500" data-testid="p5-live-no-hangup">
                        {t('p5.live.noServerHangup')}
                    </span>
                )}
                {final && view.wrapup_pending && !view.actions.can_wrapup && (
                    <span className="text-[12px] text-gray-600">{t('p5.live.wrapupByOwner')}</span>
                )}
            </div>
            <CancelCallDialog view={view} isOpen={cancelling} onClose={() => setCancelling(false)} onDone={onChanged} />
        </div>
    )
}

export default LiveCallPanel
