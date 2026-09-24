/**
 * One call line: state, timer, the attempt/case link (with the minimal live-call panel), and
 * the in-call controls. Followers (status-only tabs) see the line without controls.
 */
import classNames from 'classnames'
import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import {
    ArrowRightLeft,
    Grid3x3,
    Mic,
    MicOff,
    Pause,
    Phone,
    PhoneIncoming,
    PhoneOff,
    PhoneOutgoing,
    Play,
} from 'lucide-react'
import StatusPill from '@/components/shared/StatusPill'
import { caseLink } from '@/utils/v2/links'
import { DTMF_KEYS, fetchLiveAttempt, isInternalTarget } from '@/services/softphone'
import type { Line, LiveAttempt, SoftphoneEngine } from '@/services/softphone'
import { ATTEMPT_TONE, LINE_TONE, clock, lineStateLabel } from './labels'

const btn =
    'inline-flex h-9 items-center justify-center gap-1.5 rounded-md border px-3 text-[13px] font-extrabold transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-azure-500 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50'
export const btnSecondary = `${btn} border-slate-300 bg-white text-navy-600 hover:bg-slate-50 dark:border-night-border dark:bg-night-card dark:text-night-strong`
export const btnPrimary = `${btn} border-navy-600 bg-navy-600 text-white hover:bg-ink-800 active:bg-ink-950`
export const btnDanger = `${btn} border-danger bg-danger text-white hover:opacity-90`

function useNow(active: boolean) {
    const [now, setNow] = useState(Date.now())
    useEffect(() => {
        if (!active) return undefined
        const t = setInterval(() => setNow(Date.now()), 1000)
        return () => clearInterval(t)
    }, [active])
    return now
}

/** Minimal live-call panel: the P1 live view of the attempt this line belongs to (ETag polling). */
export const AttemptLive = ({ attemptId }: { attemptId: string }) => {
    const { t } = useTranslation()
    const [view, setView] = useState<LiveAttempt | null>(null)
    const [error, setError] = useState<string | null>(null)
    const etag = useRef<string | null>(null)

    useEffect(() => {
        let stop = false
        let timer: ReturnType<typeof setTimeout> | null = null
        const ctrl = new AbortController()
        const tick = async () => {
            let next = 1500
            try {
                const v = await fetchLiveAttempt(attemptId, etag.current, ctrl.signal)
                if (v) {
                    etag.current = v.etag || null
                    setView(v)
                    setError(null)
                    next = v.poll_after_ms || 0
                } else if (view) {
                    next = view.poll_after_ms || 1500
                }
            } catch (e) {
                setError((e as { code?: string }).code || 'error')
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
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [attemptId])

    return (
        <div
            data-testid="sp-attempt"
            data-state={view?.state || ''}
            className="mt-2 rounded-md border border-slate-200 bg-slate-25 p-2.5 text-[12.5px] dark:border-night-border dark:bg-night-raised"
        >
            <div className="mb-1 flex items-center justify-between gap-2">
                <span className="text-eyebrow">{t('sp.attempt.title')}</span>
                {view && (
                    <StatusPill tone={ATTEMPT_TONE(view.state)}>
                        <span data-attempt-state={view.state}>{t(`sp.attempt.state.${view.state}`, view.state)}</span>
                    </StatusPill>
                )}
            </div>
            {!view && !error && <div className="text-gray-500">{t('sp.attempt.loading')}</div>}
            {error && !view && <div className="text-danger">{t('sp.attempt.error')}</div>}
            {view && (
                <>
                    {view.case && (
                        <div className="flex flex-wrap items-baseline gap-x-2">
                            <Link
                                data-testid="sp-attempt-case"
                                to={caseLink(view.case.id)}
                                className="font-extrabold text-azure-600 hover:underline"
                            >
                                {view.case.name || t('sp.attempt.case')}
                            </Link>
                            {view.case.account_number && (
                                <span dir="ltr" className="tabular-nums text-gray-500">
                                    {view.case.account_number}
                                </span>
                            )}
                        </div>
                    )}
                    <div className="mt-1 text-gray-600 dark:text-night-body">
                        {view.conversation?.evidenced
                            ? t('sp.attempt.bridged')
                            : view.conversation?.customer_answered
                            ? t('sp.attempt.customerAnswered')
                            : t('sp.attempt.noCustomerYet')}
                    </div>
                    <div className="mt-1 text-[11.5px] text-gray-500">{t('sp.attempt.evidenceNote')}</div>
                    {view.uncertain && <div className="mt-1 text-warning">{t('sp.attempt.uncertain')}</div>}
                    {view.wrapup_pending && <div className="mt-1 font-extrabold text-navy-600">{t('sp.attempt.wrapupPending')}</div>}
                    <div className="mt-1 font-mono text-[11px] text-gray-400" dir="ltr">
                        {view.id}
                    </div>
                </>
            )}
        </div>
    )
}

const TransferBox = ({ engine, line, pattern }: { engine: SoftphoneEngine; line: Line; pattern: string | null }) => {
    const { t } = useTranslation()
    const [target, setTarget] = useState('')
    const [error, setError] = useState<string | null>(null)
    const ok = isInternalTarget(target, pattern)
    return (
        <div className="mt-2 flex flex-wrap items-center gap-2">
            <input
                data-testid="sp-transfer-input"
                dir="ltr"
                inputMode="numeric"
                value={target}
                aria-label={t('sp.line.transferTo') as string}
                placeholder={t('sp.line.transferPlaceholder') as string}
                onChange={(e) => {
                    setTarget(e.target.value.replace(/[^0-9]/g, '').slice(0, 6))
                    setError(null)
                }}
                className="h-9 w-32 rounded-md border border-slate-300 px-3 text-[13.5px] tabular-nums focus:border-azure-500 focus:outline-none focus:ring-2 focus:ring-azure-500/20 dark:border-night-border dark:bg-night-card"
            />
            <button
                type="button"
                data-testid="sp-transfer"
                disabled={!ok}
                className={btnSecondary}
                onClick={() => {
                    try {
                        engine.transfer(line.id, target)
                    } catch (e) {
                        setError((e as { code?: string }).code || 'error')
                    }
                }}
            >
                <ArrowRightLeft size={15} aria-hidden />
                {t('sp.line.transfer')}
            </button>
            <div className="w-full text-[11.5px] text-gray-500">{t('sp.line.transferHint')}</div>
            {error && <div className="w-full text-[12px] text-danger">{t(`sp.error.${error}`, error)}</div>}
            {line.transfer && (
                <div data-testid="sp-transfer-state" data-state={line.transfer.state} className="w-full text-[12px]">
                    {t(`sp.line.transferState.${line.transfer.state}`, { target: line.transfer.target })}
                    {line.transfer.detail ? ` (${line.transfer.detail})` : ''}
                </div>
            )}
        </div>
    )
}

const LineCard = ({
    engine,
    line,
    current,
    controls,
    pattern,
}: {
    engine: SoftphoneEngine
    line: Line
    current: boolean
    controls: boolean
    pattern: string | null
}) => {
    const { t } = useTranslation()
    const live = line.state !== 'ended'
    const now = useNow(live)
    const [keypad, setKeypad] = useState(false)
    const [transfer, setTransfer] = useState(false)
    const established = line.state === 'active' || line.state === 'held'
    const Icon = line.direction === 'incoming' ? PhoneIncoming : PhoneOutgoing
    return (
        <li
            data-testid="softphone-line"
            data-line-id={line.id}
            data-state={line.state}
            data-muted={line.muted ? '1' : '0'}
            data-held={line.held ? '1' : '0'}
            data-auto-answered={line.autoAnswered ? '1' : '0'}
            data-attempt={line.attemptId || ''}
            className={classNames(
                'relative rounded-lg border bg-white p-3 dark:bg-night-card',
                current && live ? 'border-azure-500' : 'border-slate-200 dark:border-night-border'
            )}
        >
            {current && live && <span aria-hidden className="absolute inset-y-2 start-0 w-[3px] rounded-e bg-signal-500" />}
            <div className="flex items-center justify-between gap-2">
                <div className="flex min-w-0 items-center gap-2">
                    <Icon size={16} aria-hidden className="shrink-0 text-navy-600 dark:text-night-strong" />
                    <div className="min-w-0">
                        <div className="truncate text-[14px] font-extrabold text-navy-600 dark:text-night-strong" dir="auto">
                            {line.attemptId ? t('sp.line.clickToCall') : line.remoteName || line.remote || t('sp.line.unknown')}
                        </div>
                        {line.remote && line.remote !== 'anonymous' && (
                            <div className="text-[12px] tabular-nums text-gray-500" dir="ltr">
                                {line.remote}
                            </div>
                        )}
                    </div>
                </div>
                <div className="flex flex-col items-end gap-1">
                    <StatusPill tone={LINE_TONE[line.state]}>{lineStateLabel(t, line.state)}</StatusPill>
                    <span className="text-[12px] tabular-nums text-gray-500">
                        {clock((line.endedAt || now) - (line.answeredAt || line.startedAt))}
                    </span>
                </div>
            </div>

            {line.attemptTag && (
                <div className="mt-2 text-[12px] text-gray-600 dark:text-night-body" data-testid="sp-line-decision" data-decision={line.autoAnswerDecision || ''}>
                    {line.autoAnswered ? t('sp.line.autoAnswered') : line.autoAnswerDecision ? t(`sp.decision.${line.autoAnswerDecision}`) : t('sp.line.checkingTag')}
                </div>
            )}
            {!line.attemptTag && line.direction === 'incoming' && line.state === 'ringing' && (
                <div className="mt-2 text-[12px] text-gray-600" data-testid="sp-line-decision" data-decision={line.autoAnswerDecision || 'no_tag'}>
                    {t('sp.decision.no_tag')}
                </div>
            )}
            {line.attemptId && <AttemptLive attemptId={line.attemptId} />}
            {line.muted && established && (
                <div className="mt-2 text-[12px] font-extrabold text-warning">{t('sp.line.mutedNote')}</div>
            )}
            {line.state === 'ended' && (
                <div className="mt-2 text-[12px] text-gray-500">{t('sp.line.endedWith', { cause: line.endCause || '' })}</div>
            )}

            {controls && live && (
                <div className="mt-3 flex flex-wrap gap-2">
                    {line.state === 'ringing' && (
                        <>
                            <button type="button" data-testid="sp-answer" className={btnPrimary} onClick={() => engine.answer(line.id)}>
                                <Phone size={15} aria-hidden />
                                {t('sp.line.answer')}
                            </button>
                            <button type="button" data-testid="sp-reject" className={btnDanger} onClick={() => engine.reject(line.id)}>
                                <PhoneOff size={15} aria-hidden />
                                {t('sp.line.reject')}
                            </button>
                        </>
                    )}
                    {established && (
                        <>
                            <button
                                type="button"
                                data-testid="sp-mute"
                                aria-pressed={line.muted}
                                className={btnSecondary}
                                onClick={() => engine.toggleMute(line.id)}
                            >
                                {line.muted ? <MicOff size={15} aria-hidden /> : <Mic size={15} aria-hidden />}
                                {line.muted ? t('sp.line.unmute') : t('sp.line.mute')}
                            </button>
                            <button
                                type="button"
                                data-testid="sp-hold"
                                aria-pressed={line.held}
                                className={btnSecondary}
                                onClick={() => engine.toggleHold(line.id)}
                            >
                                {line.held ? <Play size={15} aria-hidden /> : <Pause size={15} aria-hidden />}
                                {line.held ? t('sp.line.resume') : t('sp.line.hold')}
                            </button>
                            <button
                                type="button"
                                data-testid="sp-keypad"
                                aria-pressed={keypad}
                                className={btnSecondary}
                                onClick={() => setKeypad((k) => !k)}
                            >
                                <Grid3x3 size={15} aria-hidden />
                                {t('sp.line.keypad')}
                            </button>
                            <button
                                type="button"
                                data-testid="sp-transfer-open"
                                aria-pressed={transfer}
                                className={btnSecondary}
                                onClick={() => setTransfer((x) => !x)}
                            >
                                <ArrowRightLeft size={15} aria-hidden />
                                {t('sp.line.transfer')}
                            </button>
                        </>
                    )}
                    {!current && established && (
                        <button type="button" data-testid="sp-switch" className={btnSecondary} onClick={() => engine.switchTo(line.id)}>
                            {t('sp.line.switch')}
                        </button>
                    )}
                    {line.state !== 'ringing' && (
                        <button type="button" data-testid="sp-hangup" className={btnDanger} onClick={() => engine.hangup(line.id)}>
                            <PhoneOff size={15} aria-hidden />
                            {t('sp.line.hangup')}
                        </button>
                    )}
                </div>
            )}
            {controls && established && keypad && (
                <div className="mt-2 grid w-[180px] grid-cols-3 gap-1.5" role="group" aria-label={t('sp.line.keypad') as string}>
                    {DTMF_KEYS.map((k) => (
                        <button
                            key={k}
                            type="button"
                            data-testid={`sp-dtmf-${k === '*' ? 'star' : k === '#' ? 'hash' : k}`}
                            className={`${btnSecondary} px-0 tabular-nums`}
                            onClick={() => engine.dtmf(line.id, k)}
                        >
                            {k}
                        </button>
                    ))}
                </div>
            )}
            {controls && established && transfer && <TransferBox engine={engine} line={line} pattern={pattern} />}
        </li>
    )
}

export default LineCard
