/**
 * Calling workspace (`/calling`, derived `calling_any` = calls_make | campaigns; Daily
 * operations): my extension and softphone status, my active call, and my campaigns.
 *
 * - "My calling" is `GET /v2/me/calling` (extension, calling window in Riyadh time, whether
 *   click-to-call is possible now and the first reason why not) plus the in-app softphone's
 *   registration state (the header chip; this page only reads it).
 * - "My active call" is the attempt that still holds my reservation
 *   (`GET /v2/me/calls/active`), handed to the docked live-call panel.
 * - "My campaigns" (`GET /v2/campaigns?mine=1`, key `campaigns`): pick one to take part
 *   (ready / paused / offline with the lease heartbeat, next call, wrap-up delay).
 */
import { useEffect, useMemo, useState, useSyncExternalStore } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import Button from '@/components/ui/Button'
import StatusPill from '@/components/shared/StatusPill'
import { Ltr, RiyadhTime } from '@/components/v2/Display'
import { EmptyState, ErrorState, SkeletonLines } from '@/components/v2/States'
import { PageHead } from '@/components/v2/ListParts'
import useHashState, { hashCodec } from '@/utils/hooks/useHashState'
import type { HashSchema } from '@/utils/hooks/useHashState'
import { callsApi, campaignsApi, describeP5Error, refusalText } from '@/services/v2'
import type { LiveCall, Translate } from '@/services/v2'
import { softphoneEngine } from '@/services/softphone'
import type { Snapshot } from '@/services/softphone'
import { STATUS_TONE, statusLabel } from '@/components/softphone/labels'
import { usePermissions, useV2Query } from '@/utils/v2/hooks'
import { callDock } from '../live/callDock'
import { CallStateBadge, CampaignStatusBadge, Card, CaseCell, Fact, MemberStateBadge, Notice } from '../parts'
import CampaignParticipation from './CampaignParticipation'

type HashState = { campaign: string | number | undefined }
const schema: HashSchema<HashState> = {
    campaign: { key: 'campaign', codec: hashCodec.id(), default: undefined },
}
const describe = (e: unknown, t: Translate) => describeP5Error(e, t)

/** The softphone engine's snapshot (read only; the header chip owns the engine). */
function useSoftphoneStatus(enabled: boolean): Snapshot | null {
    const engine = useMemo(() => (enabled ? softphoneEngine() : null), [enabled])
    const subscribe = useMemo(() => (engine ? engine.subscribe : () => () => undefined), [engine])
    const get = useMemo(() => (engine ? engine.getSnapshot : () => null), [engine])
    return useSyncExternalStore(subscribe, get, get)
}

const MyCallingCard = () => {
    const { t } = useTranslation()
    const perms = usePermissions()
    const q = useV2Query((signal) => callsApi.myCalling(signal), [])
    const phone = useSoftphoneStatus(!!perms.calls_make)
    const my = q.data
    return (
        <Card title={t('p5.ws.myCalling')} testId="p5-ws-calling" actions={<Button size="xs" onClick={q.reload}>{t('p1.action.refresh')}</Button>}>
            {q.loading && !my ? (
                <SkeletonLines rows={3} />
            ) : q.error && !my ? (
                <ErrorState error={q.error} onRetry={q.reload} describe={describe} />
            ) : my ? (
                <>
                    <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                        <Fact label={t('p5.ws.extension')} testId="p5-ws-extension">
                            {my.extension ? <Ltr className="font-extrabold">{my.extension.extension}</Ltr> : <span className="text-warning">{t('p5.ws.noExtension')}</span>}
                        </Fact>
                        <Fact label={t('p5.ws.window')}>
                            <span data-open={my.window.open ? '1' : '0'}>
                                {my.window.configured ? (my.window.open ? t('p5.ws.windowOpen') : t('p5.ws.windowClosed')) : t('p5.ws.windowMissing')}
                            </span>
                            <div className="text-[12px] text-gray-600">
                                <RiyadhTime iso={my.window.riyadh_time} />
                            </div>
                        </Fact>
                        <Fact label={t('p5.ws.softphone')} testId="p5-ws-softphone">
                            {phone ? (
                                <StatusPill tone={STATUS_TONE[phone.status]} size="row">
                                    <span data-status={phone.status}>{statusLabel(t, phone.status)}</span>
                                </StatusPill>
                            ) : (
                                <span className="text-gray-500">{t('p5.ws.noSoftphone')}</span>
                            )}
                        </Fact>
                        <Fact label={t('p5.ws.autoAnswer')}>{my.preferences.auto_answer_attempts ? t('p5.yes') : t('p5.no')}</Fact>
                    </div>
                    <div className="mt-3" data-testid="p5-ws-can-call" data-can-call={my.can_call ? '1' : '0'} data-reason={my.reason || ''}>
                        {my.can_call ? (
                            <Notice tone="success">{t('p5.ws.canCall')}</Notice>
                        ) : (
                            <Notice tone="warning">
                                {t('p5.call.cannotCall')} {refusalText(my.reason || 'unknown', { window: my.window }, t)}
                            </Notice>
                        )}
                    </div>
                    <div className="mt-2 text-[12px] text-gray-600">{t('p5.ws.howToCall')}</div>
                </>
            ) : null}
        </Card>
    )
}

const ActiveCallCard = () => {
    const { t } = useTranslation()
    const [active, setActive] = useState<LiveCall | null>(null)
    const [loaded, setLoaded] = useState(false)
    useEffect(() => {
        let stop = false
        let timer: ReturnType<typeof setTimeout> | null = null
        const run = async () => {
            try {
                const res = await callsApi.myActive()
                if (stop) return
                setActive(res.attempt)
                setLoaded(true)
            } catch {
                /* try again */
            }
            if (!stop) timer = setTimeout(run, 3000)
        }
        void run()
        return () => {
            stop = true
            if (timer) clearTimeout(timer)
        }
    }, [])
    return (
        <Card title={t('p5.ws.activeCall')} testId="p5-ws-active">
            {!loaded ? (
                <SkeletonLines rows={2} />
            ) : active ? (
                <div className="flex flex-wrap items-center justify-between gap-3" data-attempt={active.id} data-state={active.state}>
                    <div className="flex min-w-0 items-center gap-3">
                        <CallStateBadge state={active.state} />
                        <CaseCell kase={active.case} />
                    </div>
                    <span className="flex gap-2">
                        <Button size="sm" onClick={() => callDock.show(active.id, active)} data-testid="p5-ws-show-live">
                            {t('p5.detail.showLive')}
                        </Button>
                        {active.actions.can_wrapup && (
                            <Button size="sm" variant="solid" onClick={() => callDock.openWrapup(active.id)} data-testid="p5-ws-wrapup">
                                {t('p5.live.wrapup')}
                            </Button>
                        )}
                    </span>
                </div>
            ) : (
                <div className="text-[13px] text-gray-600">{t('p5.ws.noActiveCall')}</div>
            )}
        </Card>
    )
}

const MyCampaigns = ({ selected, onSelect }: { selected: string | undefined; onSelect: (id: string | undefined) => void }) => {
    const { t } = useTranslation()
    const q = useV2Query((signal) => campaignsApi.list({ mine: 1, status: 'draft,running,paused,draining', page_size: 50 }, signal), [])
    const items = q.data?.items || []
    /* One campaign: open it directly. */
    useEffect(() => {
        if (!selected && items.length === 1) onSelect(items[0].id)
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [items.length])
    return (
        <Card title={t('p5.ws.myCampaigns')} testId="p5-ws-campaigns" actions={<Button size="xs" onClick={q.reload}>{t('p1.action.refresh')}</Button>}>
            {q.loading && !q.data ? (
                <SkeletonLines rows={3} />
            ) : q.error && !q.data ? (
                <ErrorState error={q.error} onRetry={q.reload} describe={describe} />
            ) : items.length === 0 ? (
                <EmptyState title={t('p5.ws.noCampaigns')} hint={t('p5.ws.noCampaignsHint')} className="py-4" />
            ) : (
                <ul className="flex flex-col gap-1">
                    {items.map((c) => (
                        <li key={c.id}>
                            <button
                                type="button"
                                onClick={() => onSelect(c.id)}
                                data-testid="p5-ws-campaign"
                                data-campaign={c.id}
                                aria-current={selected === c.id ? 'true' : undefined}
                                className={
                                    'flex w-full flex-wrap items-center justify-between gap-2 rounded-md border px-3 py-2 text-start ' +
                                    (selected === c.id ? 'border-azure-500 bg-azure-50 dark:bg-night-raised' : 'border-gray-300 hover:bg-gray-50 dark:border-night-border dark:hover:bg-night-raised')
                                }
                            >
                                <span className="flex min-w-0 items-center gap-2">
                                    <span className="truncate font-extrabold text-navy-600 dark:text-gray-100" dir="auto">
                                        {c.name}
                                    </span>
                                    <CampaignStatusBadge status={c.status} size="row" />
                                </span>
                                <span className="flex items-center gap-2 text-[12px] text-gray-600">
                                    {t(`p5.campaign.mode.${c.settings.next_mode}`)}
                                    {c.my_readiness && c.my_readiness.lease_live && (
                                        <MemberStateBadge state={c.my_readiness.state === 'wrap_up' ? 'wrap_up' : (c.my_readiness.state as never)} size="row" />
                                    )}
                                </span>
                            </button>
                        </li>
                    ))}
                </ul>
            )}
        </Card>
    )
}

const CallingWorkspace = () => {
    const { t } = useTranslation()
    const perms = usePermissions()
    const { state, setState } = useHashState(schema)
    const selected = state.campaign === undefined ? undefined : String(state.campaign)
    const inCampaigns = !!(perms.campaigns || perms.campaigns_manage)
    return (
        <div data-testid="p5-workspace">
            <PageHead
                title={t('p5.ws.title')}
                subtitle={t('p5.ws.subtitle')}
                actions={
                    perms.campaigns_manage ? (
                        <Link to="/campaigns" className="text-[13px] font-extrabold text-azure-600 hover:underline">
                            {t('p5.ws.manageCampaigns')}
                        </Link>
                    ) : undefined
                }
            />
            <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(0,1.3fr)]">
                <div className="flex min-w-0 flex-col gap-4">
                    {(perms.calls_make || perms.calls) && <MyCallingCard />}
                    {(perms.calls_make || perms.calls) && <ActiveCallCard />}
                    {inCampaigns && <MyCampaigns selected={selected} onSelect={(id) => setState({ campaign: id })} />}
                </div>
                <div className="min-w-0">
                    {inCampaigns && selected ? (
                        <CampaignParticipation key={selected} campaignId={selected} />
                    ) : inCampaigns ? (
                        <Card>
                            <EmptyState title={t('p5.ws.pickCampaign')} hint={t('p5.ws.pickCampaignHint')} />
                        </Card>
                    ) : (
                        <Card>
                            <EmptyState title={t('p5.ws.noCampaignKey')} hint={t('p5.ws.howToCall')} />
                        </Card>
                    )}
                </div>
            </div>
        </div>
    )
}

export default CallingWorkspace
