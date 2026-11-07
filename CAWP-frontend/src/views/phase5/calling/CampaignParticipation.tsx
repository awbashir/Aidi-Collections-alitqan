/**
 * My participation in one campaign (the collector's side of P2), inside the calling
 * workspace.
 *
 * - Ready / Paused / Offline set my declared state (`POST /v2/campaigns/<id>/heartbeat
 *   {state}`); while this page is open and I am not offline, a lease renewal is sent every
 *   `heartbeat_after_ms` (a third of the lease). Leaving the page stops the heartbeat, the
 *   lease lapses and no NEW call is placed for me. A lapsed lease never cuts a call that is
 *   already in progress (the server cannot hang up, and nothing tries to).
 * - `GET /v2/campaigns/<id>/me` is polled at `poll_after_ms`: my effective state and why,
 *   the wrap-up delay countdown (auto mode), today's counts, and my current call (the P1
 *   live view), which is also handed to the docked live-call panel.
 * - Manual mode: "Next call" (`POST …/next`) places at most one call, now, or says why not.
 * - Message campaigns (2026-10-25, 0088): the PBX dials the clients itself; a ready member only takes
 *   the clients who pressed 1 (their phone rings). No "next"; the transferred client (`message_transfer`)
 *   is shown with a link to its page.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { PhoneForwarded } from 'lucide-react'
import Button from '@/components/ui/Button'
import { RiyadhTime } from '@/components/v2/Display'
import { ErrorState, InlineError, SkeletonLines } from '@/components/v2/States'
import { Link } from 'react-router-dom'
import { Segmented } from '@/views/phase1/followUps/parts'
import { caseLink } from '@/utils/v2/links'
import { campaignsApi, describeP5Error, isV2Error, newIdempotencyKey } from '@/services/v2'
import type { MyCampaignStatus, Readiness, Translate } from '@/services/v2'
import { callDock } from '../live/callDock'
import { CallStateBadge, CampaignStatusBadge, Card, Fact, MemberStateBadge, Notice, OutcomeBadge } from '../parts'
import { memberReason } from '../labels'

const describe = (e: unknown, t: Translate) => describeP5Error(e, t)

/** Seconds left until an instant (never negative); re-renders every second. */
function useCountdown(until: string | null | undefined): number | null {
    /* the state only re-renders every second; the time is read fresh at each render */
    const [, setTick] = useState(0)
    useEffect(() => {
        if (!until) return undefined
        const id = setInterval(() => setTick((n) => n + 1), 1000)
        return () => clearInterval(id)
    }, [until])
    if (!until) return null
    const ms = new Date(until).getTime() - Date.now()
    return Number.isNaN(ms) ? null : Math.max(0, Math.ceil(ms / 1000))
}


const CampaignParticipation = ({ campaignId }: { campaignId: string }) => {
    const { t } = useTranslation()
    const [status, setStatus] = useState<MyCampaignStatus | null>(null)
    const [error, setError] = useState<unknown>(null)
    const [actionError, setActionError] = useState<unknown>(null)
    const [busy, setBusy] = useState(false)
    const [dispatchNote, setDispatchNote] = useState<string | null>(null)
    const [tick, setTick] = useState(0)
    const shownAttempt = useRef<string | null>(null)

    /* my status, at the API's pace */
    useEffect(() => {
        let stop = false
        let timer: ReturnType<typeof setTimeout> | null = null
        const run = async () => {
            let next = 3000
            try {
                const s = await campaignsApi.me(campaignId)
                if (stop) return
                setStatus(s)
                setError(null)
                next = s.poll_after_ms || 3000
            } catch (e) {
                if (stop) return
                setError(e)
                if (isV2Error(e) && (e.status === 404 || e.status === 403)) return
                next = 5000
            }
            if (!stop) timer = setTimeout(run, document.hidden ? Math.max(next, 5000) : next)
        }
        void run()
        return () => {
            stop = true
            if (timer) clearTimeout(timer)
        }
    }, [campaignId, tick])
    const refresh = useCallback(() => setTick((n) => n + 1), [])

    /* the heartbeat: renew my lease while the page is open and I am not offline */
    const declared = status?.member.declared?.state || null
    const beatMs = status?.heartbeat_after_ms || 10000
    useEffect(() => {
        if (!declared || declared === 'offline' || status?.campaign.status === 'stopped') return undefined
        const id = setInterval(() => {
            campaignsApi.heartbeat(campaignId).catch(() => undefined)
        }, beatMs)
        return () => clearInterval(id)
    }, [campaignId, declared, beatMs, status?.campaign.status])

    /* a campaign call placed for me: show it in the docked panel (once per attempt) */
    const attemptId = status?.attempt?.id || null
    useEffect(() => {
        if (attemptId && shownAttempt.current !== attemptId) {
            shownAttempt.current = attemptId
            callDock.show(attemptId, status?.attempt || undefined)
        }
    }, [attemptId, status?.attempt])

    const setReadiness = async (state: Readiness) => {
        setBusy(true)
        setActionError(null)
        try {
            setStatus(await campaignsApi.heartbeat(campaignId, state))
        } catch (e) {
            setActionError(e)
        } finally {
            setBusy(false)
        }
    }
    const next = async () => {
        setBusy(true)
        setActionError(null)
        setDispatchNote(null)
        try {
            const s = await campaignsApi.next(campaignId, newIdempotencyKey())
            setStatus(s)
            const d = s.dispatch
            if (d && d.attempt_id) setDispatchNote(String(t('p5.part.dispatched')))
            else if (d && d.blocked) setDispatchNote(String(t('p5.part.notDispatched', { reason: memberReason(t, d.blocked) })))
            else setDispatchNote(String(t('p5.part.nextPending')))
        } catch (e) {
            setActionError(e)
        } finally {
            setBusy(false)
        }
    }

    const countdown = useCountdown(status?.state === 'wrap_up' && status.reason === 'wrapup_delay' ? status.ready_at : null)
    if (!status && error) return <ErrorState error={error} onRetry={refresh} describe={describe} />
    if (!status) return <SkeletonLines rows={6} />
    const camp = status.campaign
    const member = status.member
    const today = member.today
    const isMessage = camp.kind === 'message'
    const manual = !isMessage && status.next.mode === 'manual'
    const running = camp.status === 'running'
    const lease = member.declared
    const attempt = status.attempt
    const transfer = status.message_transfer
    return (
        <div className="flex flex-col gap-4" data-testid="p5-part" data-state={status.state} data-declared={declared || ''} data-campaign-status={camp.status}>
            <Card
                lead
                title={
                    <span className="flex flex-wrap items-center gap-2">
                        <span dir="auto">{camp.name}</span>
                        <CampaignStatusBadge status={camp.status} size="row" />
                    </span>
                }
                actions={
                    <span className="text-[12.5px] text-gray-600">
                        {isMessage
                            ? t('p12.msg.kind.badge')
                            : `${t(`p5.campaign.mode.${status.next.mode}`)} · ${t('p5.part.delay', { seconds: camp.settings.wrapup_delay_seconds })}`}
                    </span>
                }
            >
                <div className="flex flex-wrap items-center gap-3">
                    <MemberStateBadge state={status.state} />
                    {status.reason && (
                        <span className="text-[13px] text-gray-700 dark:text-night-body" data-testid="p5-part-reason" data-reason={status.reason}>
                            {memberReason(t, status.reason)}
                        </span>
                    )}
                </div>
                {countdown !== null && (
                    <div className="mt-2 text-[14px] font-extrabold text-navy-600 dark:text-gray-100" data-testid="p5-part-countdown" data-seconds={countdown}>
                        {t('p5.part.countdown', { seconds: countdown })}
                    </div>
                )}
                <div className="mt-4 flex flex-wrap items-center gap-3">
                    <Segmented<Readiness>
                        value={(declared === 'wrap_up' ? 'ready' : declared || 'offline') as Readiness}
                        onChange={(v) => void setReadiness(v)}
                        testId="p5-part-readiness"
                        label={String(t('p5.part.readiness'))}
                        options={[
                            { value: 'ready', label: t('p5.part.ready') },
                            { value: 'paused', label: t('p5.part.pause') },
                            { value: 'offline', label: t('p5.part.offline') },
                        ]}
                    />
                    {manual && (
                        <Button
                            variant="solid"
                            size="sm"
                            icon={<PhoneForwarded size={15} />}
                            loading={busy}
                            disabled={!running || !!status.attempt}
                            onClick={next}
                            data-testid="p5-part-next"
                        >
                            {t('p5.part.next')}
                        </Button>
                    )}
                    {!isMessage && !manual && status.state === 'wrap_up' && status.reason === 'wrapup_delay' && (
                        <Button size="sm" loading={busy} onClick={next} data-testid="p5-part-skip">
                            {t('p5.part.skipWait')}
                        </Button>
                    )}
                </div>
                {dispatchNote && (
                    <div className="mt-2 text-[13px] text-gray-700 dark:text-night-body" data-testid="p5-part-dispatch" data-dispatch={status.dispatch?.attempt_id ? 'placed' : status.dispatch?.blocked || 'none'}>
                        {dispatchNote}
                    </div>
                )}
                <InlineError error={actionError} describe={describe} />
                <div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-4">
                    <Fact label={t('p5.part.lease')}>
                        {lease ? (
                            <span data-testid="p5-part-lease" data-live={lease.lease_live ? '1' : '0'}>
                                {lease.lease_live ? t('p5.part.leaseLive') : t('p5.part.leaseLapsed')} · <RiyadhTime iso={lease.lease_until} />
                            </span>
                        ) : (
                            t('p5.part.neverReady')
                        )}
                    </Fact>
                    <Fact label={t('p5.part.heartbeat')}>{t('p5.part.every', { seconds: Math.round(beatMs / 1000) })}</Fact>
                    <Fact label={t('p5.part.extension')}>{member.extension || t('p5.part.noExtension')}</Fact>
                    <Fact label={t('p5.part.nextMode')}>{manual ? (status.next.pending ? t('p5.part.nextRequested') : t('p5.part.nextNone')) : t('p5.part.auto')}</Fact>
                </div>
                <Notice tone="info" className="mt-3" testId="p5-part-rule">
                    {isMessage ? t('p12.msg.part.rule') : t('p5.part.rule', { seconds: camp.settings.lease_seconds })}
                </Notice>
            </Card>

            {isMessage && (
                <Card title={t('p12.msg.part.title')} testId="p12-part-transfer">
                    {transfer ? (
                        <div className="flex flex-wrap items-center justify-between gap-2" data-state={transfer.state}>
                            <div className="flex flex-col">
                                <span dir="auto" className="font-extrabold text-navy-600 dark:text-gray-100">
                                    {transfer.case.name || '—'}
                                </span>
                                <span className="text-[12.5px] text-gray-600" dir="ltr">
                                    {transfer.case.account_number || ''}
                                </span>
                                <span className="text-[12.5px] text-gray-700 dark:text-night-body">
                                    {t(`p12.msg.part.state.${transfer.state === 'transferring' ? 'ringing' : transfer.state === 'talking' ? 'talking' : 'ended'}`)}{' '}
                                    <RiyadhTime iso={transfer.transferred_at} />
                                </span>
                            </div>
                            <Link to={caseLink(transfer.case.id, 'comments')} className="font-extrabold text-azure-600 hover:underline" data-testid="p12-part-open-case">
                                {t('p12.msg.part.open')}
                            </Link>
                        </div>
                    ) : (
                        <div className="text-[13px] text-gray-600">{running ? t('p12.msg.part.waiting') : t('p12.msg.part.notRunning')}</div>
                    )}
                </Card>
            )}

            <Card title={t('p5.part.currentCall')} testId="p5-part-call">
                {attempt ? (
                    <div className="flex flex-wrap items-center justify-between gap-2">
                        <div className="flex flex-wrap items-center gap-2">
                            <CallStateBadge state={attempt.state} />
                            <span dir="auto" className="font-extrabold text-navy-600 dark:text-gray-100">
                                {attempt.case?.name}
                            </span>
                        </div>
                        <span className="flex gap-2">
                            <Button size="sm" onClick={() => callDock.show(attempt.id)} data-testid="p5-part-show-live">
                                {t('p5.detail.showLive')}
                            </Button>
                            {attempt.actions.can_wrapup && (
                                <Button size="sm" variant="solid" onClick={() => callDock.openWrapup(attempt.id)} data-testid="p5-part-wrapup">
                                    {t('p5.live.wrapup')}
                                </Button>
                            )}
                        </span>
                    </div>
                ) : (
                    <div className="text-[13px] text-gray-600">{t('p5.part.noCall')}</div>
                )}
            </Card>

            <Card title={t('p5.part.today')} testId="p5-part-today">
                <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
                    <Fact label={t('p5.count.attempts')}>{today.attempts}</Fact>
                    <Fact label={t('p5.count.conversations')}>{today.conversations}</Fact>
                    <Fact label={t('p5.count.customerAnswered')}>{today.customer_answered}</Fact>
                    <Fact label={t('p5.count.wrappedUp')}>{today.wrapped_up}</Fact>
                    <Fact label={t('p5.count.failed')}>{today.failed}</Fact>
                </div>
                {Object.keys(today.outcomes || {}).length > 0 && (
                    <div className="mt-3 flex flex-wrap gap-2">
                        {Object.entries(today.outcomes).map(([o, n]) => (
                            <span key={o} className="inline-flex items-center gap-1 text-[12.5px]">
                                <OutcomeBadge outcome={o as never} size="row" /> <span className="tabular-nums">{n}</span>
                            </span>
                        ))}
                    </div>
                )}
                <div className="mt-2 text-[11.5px] text-gray-500">{t('p5.count.note')}</div>
            </Card>
        </div>
    )
}

export default CampaignParticipation
