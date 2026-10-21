/**
 * One campaign for its supervisor (`/campaigns/:id`, `campaigns_manage`).
 *
 * - Lifecycle: start / resume, pause, drain, stop, each behind a confirmation that says
 *   what happens: calls still QUEUED are cancelled (never sent); a call already handed to
 *   the PBX always continues to its end and still needs its wrap-up. Nothing cuts a call.
 * - Live view (`GET …/live`, polled at `poll_after_ms`): the calling service's connection,
 *   members with their effective state and why, current attempts, today's counts
 *   (conversations = bridge evidence; a customer answer alone is not one), the pool summary
 *   (size, eligible now, remaining today, excluded reasons) and outstanding calls.
 * - Pool (`GET …/pool`): the cases in dispatch order with each one's reason, filterable.
 * - Roster (`GET/POST …/members`, deactivate with a reason; a live call is never touched).
 * - Settings and pool definition, with "Edit" (`PATCH`, expected_revision).
 * - 2026-10-21: the number each campaign call dialled (the rotation over the client's callable
 *   numbers: "2 of 3"; the number itself needs clints_details) on the member's current call and
 *   on each pool row, and the pool rows a promise assigned to the agent who made the call.
 */
import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useParams } from 'react-router-dom'
import { HiOutlineArrowLeft, HiOutlinePencil, HiOutlinePlus } from 'react-icons/hi'
import Button from '@/components/ui/Button'
import Input from '@/components/ui/Input'
import Select from '@/components/ui/Select'
import Table from '@/components/ui/Table'
import ActionDialog, { DialogField, ReasonDialog } from '@/components/v2/ActionDialog'
import { Ltr, RiyadhTime } from '@/components/v2/Display'
import { EmptyState, ErrorState, SkeletonLines, SkeletonRows } from '@/components/v2/States'
import { CountTabs, PagerFooter, TableFrame } from '@/components/v2/ListParts'
import ToastNotification from '@/components/shared/myComponents/ToastNotification'
import useHashState, { hashCodec } from '@/utils/hooks/useHashState'
import type { HashSchema } from '@/utils/hooks/useHashState'
import { campaignsApi, describeP5Error, isV2Error, lookups } from '@/services/v2'
import type { Campaign, CampaignDialed, CampaignLive, Option, Translate } from '@/services/v2'
import { usePermissions, useV2Query } from '@/utils/v2/hooks'
import { formatCount } from '@/utils/v2/format'
import { CallStateBadge, CampaignStatusBadge, Card, CaseCell, Fact, MemberStateBadge, Notice, OutcomeBadge } from '../parts'
import { memberReason, poolReason } from '../labels'
import CampaignForm from './CampaignForm'
import CampaignReport from './CampaignReport'
import PoolFilterChips, { usePoolNames } from './PoolFilterChips'

const { THead, TBody, Tr, Th, Td } = Table
type Tab = 'live' | 'pool' | 'roster' | 'settings' | 'report'
const TABS: Tab[] = ['live', 'pool', 'roster', 'settings', 'report']
type HashState = { tab: Tab; reason: string | undefined; page: number }
const schema: HashSchema<HashState> = {
    tab: { key: 'tab', codec: hashCodec.oneOf(TABS), default: 'live' },
    reason: { key: 'reason', codec: hashCodec.text({ maxLength: 40 }), default: undefined },
    page: { key: 'page', codec: hashCodec.int({ min: 1, max: 100000 }), default: 1 },
}
const describe = (e: unknown, t: Translate) => describeP5Error(e, t)

/** Pool reasons added after p5 (0080) have their own label. */
const P11_REASONS = new Set(['status_stops_campaigns'])
const poolReasonText = (t: Translate, r: string) => (P11_REASONS.has(r) ? t(`p11.camprules.reason.${r}`) : poolReason(t, r))

/** "0551… · 2 of 3": the dialled number (when visible to me) and its rotation place. */
const DialedLine = ({ dialed, testId }: { dialed: CampaignDialed | null | undefined; testId?: string }) => {
    const { t } = useTranslation()
    if (!dialed) return null
    const place = dialed.position && dialed.of ? String(t('p11.camprules.dialed.place', { position: dialed.position, of: dialed.of })) : ''
    return (
        <span className="text-[12px] text-gray-600" data-testid={testId} data-position={dialed.position ?? ''}>
            {t('p11.camprules.dialed.label')}: {dialed.number ? <Ltr>{dialed.number}</Ltr> : t('p11.camprules.dialed.hidden')}
            {place ? ` · ${place}` : ''}
            {dialed.choice === 'default' ? ` · ${t('p11.camprules.dialed.default')}` : ''}
        </span>
    )
}

type Action = 'start' | 'pause' | 'drain' | 'stop'

/* ------------------------------------------------------------------ lifecycle */

const TransitionDialog = ({
    campaign,
    action,
    onClose,
    onDone,
    onReload,
}: {
    campaign: Campaign
    action: Action | null
    onClose: () => void
    onDone: (c: Campaign) => void
    onReload: () => void
}) => {
    const { t } = useTranslation()
    const [reason, setReason] = useState('')
    const key = action === 'start' && campaign.status === 'paused' ? 'resume' : action || 'start'
    return (
        <ActionDialog
            isOpen={!!action}
            title={t(`p5.cd.confirm.${key}.title`)}
            confirmLabel={t(`p5.cd.confirm.${key}.confirm`)}
            tone={action === 'stop' ? 'danger' : 'primary'}
            testId={`p5-cd-confirm-${action || ''}`}
            describeError={describe}
            onReload={onReload}
            onClose={() => {
                setReason('')
                onClose()
            }}
            onSubmit={async (k) => {
                if (!action) return
                const res = await campaignsApi.transition(
                    campaign.id,
                    action,
                    { expected_revision: campaign.revision, ...(reason.trim() ? { reason: reason.trim() } : {}) },
                    k
                )
                setReason('')
                const n = res.cancelled_queued?.length || 0
                ToastNotification(
                    [String(t(`p5.cd.done.${key}`)), n ? String(t('p5.cd.cancelledQueued', { count: n })) : '', res.drained ? String(t('p5.cd.drainedNow')) : '']
                        .filter(Boolean)
                        .join(' '),
                    campaign.name,
                    'success',
                    4000
                )
                onDone(res)
            }}
        >
            <div className="mb-3 text-[13.5px] text-gray-700 dark:text-night-body" data-testid="p5-cd-confirm-body">
                {t(`p5.cd.confirm.${key}.body`)}
            </div>
            <Notice tone="info" className="mb-3">
                {t('p5.cd.neverCut')}
            </Notice>
            <DialogField label={t('p5.cd.reason')}>
                <Input value={reason} maxLength={500} onChange={(e) => setReason(e.target.value)} data-testid="p5-cd-reason" />
            </DialogField>
        </ActionDialog>
    )
}

/* ------------------------------------------------------------------ live */

const Kpi = ({ label, value, lead, testId }: { label: React.ReactNode; value: React.ReactNode; lead?: boolean; testId?: string }) => (
    <div
        className={'min-w-0 rounded-lg border border-gray-300 bg-white px-4 py-3 dark:border-night-border dark:bg-night-card ' + (lead ? 'card-lead' : '')}
        data-testid={testId}
    >
        <div className="text-eyebrow mb-1">{label}</div>
        <div className="text-[22px] font-extrabold tabular-nums text-navy-600 dark:text-gray-100">{value}</div>
    </div>
)

const LiveTab = ({ campaignId, onCampaign }: { campaignId: string; onCampaign: (c: Campaign) => void }) => {
    const { t } = useTranslation()
    const [live, setLive] = useState<CampaignLive | null>(null)
    const [error, setError] = useState<unknown>(null)
    const [tick, setTick] = useState(0)
    useEffect(() => {
        let stop = false
        let timer: ReturnType<typeof setTimeout> | null = null
        const run = async () => {
            let next = 5000
            try {
                const v = await campaignsApi.live(campaignId)
                if (stop) return
                setLive(v)
                setError(null)
                onCampaign(v.campaign)
                next = v.poll_after_ms || 5000
            } catch (e) {
                if (stop) return
                setError(e)
                if (isV2Error(e) && (e.status === 404 || e.status === 403)) return
            }
            if (!stop) timer = setTimeout(run, document.hidden ? Math.max(next, 5000) : next)
        }
        void run()
        return () => {
            stop = true
            if (timer) clearTimeout(timer)
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [campaignId, tick])
    if (!live && error) return <ErrorState error={error} onRetry={() => setTick((n) => n + 1)} describe={describe} />
    if (!live) return <SkeletonLines rows={8} />
    const today = live.today
    return (
        <div className="flex flex-col gap-4" data-testid="p5-cd-live" data-outstanding={live.outstanding_calls}>
            <div className="flex flex-wrap items-center gap-3 text-[13px]" data-testid="p5-cd-transport" data-connected={live.transport.connected ? '1' : '0'}>
                <span className={live.transport.connected ? 'font-extrabold text-success' : 'font-extrabold text-danger'}>
                    {live.transport.connected ? t('p5.cd.transportUp') : t('p5.cd.transportDown')}
                </span>
                {live.transport.epoch && (
                    <span className="text-gray-600">
                        {t('p5.admin.epoch')}: <Ltr>{live.transport.epoch}</Ltr>
                    </span>
                )}
            </div>
            <div className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-6">
                <Kpi lead label={t('p5.count.attempts')} value={today.attempts} testId="p5-cd-kpi-attempts" />
                <Kpi label={t('p5.count.conversations')} value={today.conversations} testId="p5-cd-kpi-conversations" />
                <Kpi label={t('p5.count.customerAnswered')} value={today.customer_answered} />
                <Kpi label={t('p5.count.wrappedUp')} value={today.wrapped_up} testId="p5-cd-kpi-wrapped" />
                <Kpi label={t('p5.count.live')} value={today.live} testId="p5-cd-kpi-live" />
                <Kpi label={t('p5.cd.outstanding')} value={live.outstanding_calls} testId="p5-cd-kpi-outstanding" />
            </div>
            {Object.keys(today.outcomes || {}).length > 0 && (
                <div className="flex flex-wrap gap-3" data-testid="p5-cd-outcomes">
                    {Object.entries(today.outcomes).map(([o, n]) => (
                        <span key={o} className="inline-flex items-center gap-1 text-[12.5px]" data-outcome={o} data-count={n}>
                            <OutcomeBadge outcome={o as never} size="row" /> <span className="tabular-nums">{n}</span>
                        </span>
                    ))}
                </div>
            )}
            <div className="text-[11.5px] text-gray-500">{t('p5.count.note')}</div>

            <Card title={t('p5.cd.members')} testId="p5-cd-members">
                <div className="overflow-x-auto">
                    <Table>
                        <THead>
                            <Tr>
                                <Th>{t('p5.cd.col.member')}</Th>
                                <Th>{t('p5.cd.col.state')}</Th>
                                <Th>{t('p5.cd.col.lease')}</Th>
                                <Th>{t('p5.cd.col.call')}</Th>
                                <Th className="text-end">{t('p5.cd.col.today')}</Th>
                            </Tr>
                        </THead>
                        <TBody>
                            {live.members.map((m) => (
                                <Tr key={m.user?.id || ''} data-testid="p5-cd-member" data-user={m.user?.id || ''} data-state={m.state}>
                                    <Td>
                                        <div className="font-extrabold text-navy-600 dark:text-gray-100">{m.user?.name || '—'}</div>
                                        <div className="text-[12px] text-gray-600">{m.extension ? <Ltr>{m.extension}</Ltr> : t('p5.part.noExtension')}</div>
                                    </Td>
                                    <Td>
                                        <MemberStateBadge state={m.state} size="row" />
                                        {m.reason && <div className="text-[12px] text-gray-600" data-reason={m.reason}>{memberReason(t, m.reason)}</div>}
                                    </Td>
                                    <Td>
                                        {m.declared ? (
                                            <span className="text-[12.5px]">
                                                {t(`p5.part.declared.${m.declared.state}`)} · {m.declared.lease_live ? t('p5.part.leaseLive') : t('p5.part.leaseLapsed')}{' '}
                                                <RiyadhTime iso={m.declared.lease_until} />
                                            </span>
                                        ) : (
                                            <span className="text-gray-500">{t('p5.part.neverReady')}</span>
                                        )}
                                    </Td>
                                    <Td>
                                        {m.current_attempt ? (
                                            <div className="flex flex-col gap-1">
                                                <CallStateBadge state={m.current_attempt.state} size="row" />
                                                <CaseCell kase={{ ...m.current_attempt.case, missing: false }} />
                                                <DialedLine dialed={m.current_attempt.dialed} testId="p11-cd-member-dialed" />
                                                {!m.current_attempt.this_campaign && <span className="text-[11.5px] text-gray-500">{t('p5.cd.otherCall')}</span>}
                                            </div>
                                        ) : (
                                            '—'
                                        )}
                                    </Td>
                                    <Td className="text-end tabular-nums text-[12.5px]">
                                        {t('p5.cd.todayCell', { attempts: m.today.attempts, conversations: m.today.conversations })}
                                        {(m.today.assigned_by_promise || 0) > 0 && (
                                            <div className="text-[12px] font-bold text-success-600 dark:text-success-400" data-testid="p11-cd-member-assigned">
                                                {t('p11.camprules.assignedToday', { count: m.today.assigned_by_promise })}
                                            </div>
                                        )}
                                    </Td>
                                </Tr>
                            ))}
                        </TBody>
                    </Table>
                </div>
            </Card>

            <Card title={t('p5.cd.pool')} testId="p5-cd-pool-summary">
                <div className="grid grid-cols-3 gap-3">
                    <Fact label={t('p5.cd.poolSize')} testId="p5-cd-pool-size">{formatCount(live.pool.size)}</Fact>
                    <Fact label={t('p5.cd.eligibleNow')} testId="p5-cd-pool-eligible">{formatCount(live.pool.eligible_now)}</Fact>
                    <Fact label={t('p5.cd.remainingToday')} testId="p5-cd-pool-remaining">{formatCount(live.pool.remaining_today)}</Fact>
                </div>
                {Object.keys(live.pool.excluded).length > 0 && (
                    <div className="mt-3" data-testid="p5-cd-excluded">
                        <div className="text-eyebrow mb-1">{t('p5.cd.excluded')}</div>
                        <ul className="grid grid-cols-1 gap-1 text-[12.5px] sm:grid-cols-2">
                            {Object.entries(live.pool.excluded).map(([r, n]) => (
                                <li key={r} className="flex justify-between gap-2" data-reason={r}>
                                    <span>{poolReasonText(t, r)}</span>
                                    <span className="tabular-nums">{formatCount(n)}</span>
                                </li>
                            ))}
                        </ul>
                    </div>
                )}
                <div className="mt-2 text-[11.5px] text-gray-500">
                    {t('p5.cd.poolAsOf')} <RiyadhTime iso={live.pool.evaluated_at} />
                    {live.pool.cached ? ` · ${t('p5.cd.cached')}` : ''}
                </div>
            </Card>
            <div className="text-[12px] text-gray-600">
                {t('p5.cd.allTime', { attempts: live.all_time.attempts, uncertain: live.all_time.uncertain, awaiting: live.all_time.awaiting_wrapup })}
            </div>
        </div>
    )
}

/* ------------------------------------------------------------------ pool */

const POOL_REASONS = [
    'eligible',
    'case_archived',
    'case_locked',
    'do_not_call',
    'calling_review_pending',
    'in_call',
    'no_member_in_scope',
    'case_paid',
    'status_stops_campaigns',
    'no_callable_contact',
    'callback_scheduled',
    'max_attempts_today',
    'cooldown',
    'recheck_later',
]

const PoolTab = ({ campaignId, reason, page, onState }: { campaignId: string; reason: string | undefined; page: number; onState: (p: Partial<HashState>) => void }) => {
    const { t } = useTranslation()
    const q = useV2Query((signal) => campaignsApi.pool(campaignId, { reason, page, page_size: 25 }, signal), [campaignId, reason, page])
    const data = q.data
    const options = POOL_REASONS.map((r) => ({ value: r, label: String(poolReasonText(t, r)) }))
    return (
        <div data-testid="p5-cd-pool">
            <div className="mb-3 flex flex-wrap items-end gap-3">
                <div className="w-[260px]">
                    <div className="text-eyebrow mb-1">{t('p5.cd.poolReason')}</div>
                    <Select
                        size="sm"
                        isClearable
                        options={options}
                        value={options.find((o) => o.value === reason) || null}
                        onChange={(o) => onState({ reason: (o as Option | null)?.value || undefined, page: 1 })}
                    />
                </div>
                {data && (
                    <span className="text-[12.5px] text-gray-600">
                        {t('p5.cd.poolSummaryLine', { size: formatCount(data.summary.size), eligible: formatCount(data.summary.eligible_now) })}
                    </span>
                )}
            </div>
            <TableFrame footer={data && data.total > 0 ? <PagerFooter page={page} pageSize={25} total={data.total} onPage={(p) => onState({ page: p })} /> : undefined}>
                {q.error && !data ? (
                    <ErrorState error={q.error} onRetry={q.reload} describe={describe} />
                ) : (
                    <Table>
                        <THead>
                            <Tr>
                                <Th>{t('p5.col.case')}</Th>
                                <Th>{t('p5.cd.col.reason')}</Th>
                                <Th>{t('p5.cd.col.priority')}</Th>
                                <Th>{t('p5.cd.col.lastContact')}</Th>
                                <Th>{t('p5.cd.col.callback')}</Th>
                                <Th className="text-end">{t('p5.cd.col.attemptsToday')}</Th>
                                <Th>{t('p11.camprules.col.lastDialed')}</Th>
                            </Tr>
                        </THead>
                        <TBody>
                            {q.loading && !data ? (
                                <SkeletonRows cols={7} />
                            ) : data && data.items.length ? (
                                data.items.map((it) => (
                                    <Tr key={it.case.id} data-testid="p5-cd-pool-row" data-reason={it.reason}>
                                        <Td>
                                            <CaseCell kase={{ ...it.case, missing: false }} />
                                            {it.assigned_by_campaign && (
                                                <div className="mt-1 text-[12px] font-bold text-success-600 dark:text-success-400" data-testid="p11-cd-pool-assigned">
                                                    {t('p11.camprules.assigned', { name: it.assigned_by_campaign.user?.name || '—' })}{' '}
                                                    <RiyadhTime iso={it.assigned_by_campaign.at} />
                                                </div>
                                            )}
                                        </Td>
                                        <Td>{poolReasonText(t, it.reason)}</Td>
                                        <Td>{t(`p5.cd.priority.${it.priority}`)}</Td>
                                        <Td>
                                            <RiyadhTime iso={it.last_contact_at} />
                                        </Td>
                                        <Td>
                                            <RiyadhTime iso={it.callback_at} />
                                        </Td>
                                        <Td className="text-end tabular-nums">{it.attempts_today}</Td>
                                        <Td>
                                            {it.last_dialed ? (
                                                <div className="flex flex-col">
                                                    <DialedLine dialed={it.last_dialed} testId="p11-cd-pool-dialed" />
                                                    <RiyadhTime iso={it.last_dialed.at} />
                                                </div>
                                            ) : (
                                                '—'
                                            )}
                                        </Td>
                                    </Tr>
                                ))
                            ) : (
                                <tr>
                                    <td colSpan={7}>
                                        <EmptyState title={t('p5.cd.poolEmpty')} />
                                    </td>
                                </tr>
                            )}
                        </TBody>
                    </Table>
                )}
            </TableFrame>
            <div className="mt-2 text-[12px] text-gray-600">{t('p5.cd.poolRule')}</div>
        </div>
    )
}

/* ------------------------------------------------------------------ roster */

const RosterTab = ({ campaign, onChanged }: { campaign: Campaign; onChanged: () => void }) => {
    const { t } = useTranslation()
    const q = useV2Query((signal) => campaignsApi.members(campaign.id, signal), [campaign.id, campaign.revision])
    const [adding, setAdding] = useState(false)
    const [picked, setPicked] = useState<string[]>([])
    const [users, setUsers] = useState<Option[]>([])
    const [removing, setRemoving] = useState<string | null>(null)
    useEffect(() => {
        if (!adding) return
        lookups.users().then(setUsers).catch(() => setUsers([]))
    }, [adding])
    const stopped = campaign.status === 'stopped'
    return (
        <div data-testid="p5-cd-roster">
            <div className="mb-3 flex justify-end">
                {!stopped && (
                    <Button size="sm" icon={<HiOutlinePlus />} onClick={() => setAdding(true)} data-testid="p5-cd-add-members">
                        {t('p5.cd.addMembers')}
                    </Button>
                )}
            </div>
            <TableFrame>
                {q.error && !q.data ? (
                    <ErrorState error={q.error} onRetry={q.reload} describe={describe} />
                ) : (
                    <Table>
                        <THead>
                            <Tr>
                                <Th>{t('p5.cd.col.member')}</Th>
                                <Th>{t('p5.cd.col.active')}</Th>
                                <Th>{t('p5.cd.col.added')}</Th>
                                <Th>{t('p5.cd.col.changed')}</Th>
                                <Th />
                            </Tr>
                        </THead>
                        <TBody>
                            {q.loading && !q.data ? (
                                <SkeletonRows cols={5} rows={3} />
                            ) : (
                                (q.data?.items || []).map((m) => (
                                    <Tr key={m.user?.id || ''} data-testid="p5-cd-roster-row" data-user={m.user?.id || ''} data-active={m.active ? '1' : '0'}>
                                        <Td>
                                            <div className="font-extrabold text-navy-600 dark:text-gray-100">{m.user?.name || '—'}</div>
                                            <div className="text-[12px] text-gray-600">{m.extension ? <Ltr>{m.extension}</Ltr> : t('p5.part.noExtension')}</div>
                                        </Td>
                                        <Td>{m.active ? t('p5.yes') : t('p5.no')}</Td>
                                        <Td className="text-[12.5px]">
                                            {m.added_by?.name || '—'} · <RiyadhTime iso={m.added_at} />
                                        </Td>
                                        <Td className="text-[12.5px]">
                                            {m.changed_at ? (
                                                <>
                                                    {m.changed_by?.name || '—'} · <RiyadhTime iso={m.changed_at} />
                                                    {m.change_reason && <div dir="auto">{m.change_reason}</div>}
                                                </>
                                            ) : (
                                                '—'
                                            )}
                                        </Td>
                                        <Td>
                                            {m.active && !stopped && (
                                                <Button size="xs" onClick={() => setRemoving(m.user?.id || null)} data-testid="p5-cd-deactivate">
                                                    {t('p5.cd.deactivate')}
                                                </Button>
                                            )}
                                        </Td>
                                    </Tr>
                                ))
                            )}
                        </TBody>
                    </Table>
                )}
            </TableFrame>
            <ActionDialog
                isOpen={adding}
                title={t('p5.cd.addMembers')}
                confirmLabel={t('p5.cd.add')}
                canSubmit={picked.length > 0}
                describeError={describe}
                testId="p5-cd-add"
                onReload={onChanged}
                onClose={() => {
                    setAdding(false)
                    setPicked([])
                }}
                onSubmit={async (key) => {
                    await campaignsApi.addMembers(campaign.id, { expected_revision: campaign.revision, user_ids: picked }, key)
                    setPicked([])
                    onChanged()
                    q.reload()
                }}
            >
                <DialogField label={t('p5.cf.membersLabel')} hint={t('p5.cf.membersHint')}>
                    <Select
                        size="sm"
                        menuPosition="fixed"
                        isMulti
                        options={users}
                        value={users.filter((o) => picked.includes(o.value))}
                        onChange={(v) => setPicked(((v as Option[]) || []).map((o) => o.value))}
                    />
                </DialogField>
            </ActionDialog>
            <ReasonDialog
                isOpen={!!removing}
                title={t('p5.cd.deactivateTitle')}
                description={t('p5.cd.deactivateHint')}
                confirmLabel={t('p5.cd.deactivate')}
                testId="p5-cd-deactivate-dialog"
                describeError={describe}
                onReload={onChanged}
                onClose={() => setRemoving(null)}
                onSubmit={async (reason, key) => {
                    if (!removing) return
                    await campaignsApi.deactivateMember(campaign.id, removing, { expected_revision: campaign.revision, reason }, key)
                    onChanged()
                    q.reload()
                }}
            />
        </div>
    )
}

/* ------------------------------------------------------------------ settings */

const SettingsTab = ({ campaign }: { campaign: Campaign }) => {
    const { t } = useTranslation()
    /* contracts, portfolios, collectors and note statuses by name (customer 2026-10-19) */
    const names = usePoolNames()
    const s = campaign.settings
    return (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2" data-testid="p5-cd-settings">
            <Card title={t('p5.cf.settings')}>
                <div className="grid grid-cols-2 gap-3">
                    <Fact label={t('p5.cf.nextMode')}>{t(`p5.campaign.mode.${s.next_mode}`)}</Fact>
                    <Fact label={t('p5.cf.delay')}>{t('p5.unit.seconds', { n: s.wrapup_delay_seconds })}</Fact>
                    <Fact label={t('p5.cf.maxAttempts')}>{s.max_attempts_per_day}</Fact>
                    <Fact label={t('p5.cf.cooldown')}>{t('p5.unit.minutes', { n: s.cooldown_minutes })}</Fact>
                    <Fact label={t('p5.cf.lease')}>{t('p5.unit.seconds', { n: s.lease_seconds })}</Fact>
                    <Fact label={t('p5.cd.revision')}>{campaign.revision}</Fact>
                </div>
            </Card>
            <Card title={t('p5.cf.pool')}>
                <div className="flex flex-col gap-2 text-[13px]">
                    <Fact label={t('p5.cf.contracts')}>{campaign.pool.contract_ids.map((id) => names[id] || id.slice(0, 8)).join(String(t('p5.listSep'))) || '—'}</Fact>
                    <Fact label={t('p5.cf.portfolios')}>{campaign.pool.portfolio_ids.map((id) => names[id] || id.slice(0, 8)).join(String(t('p5.listSep'))) || '—'}</Fact>
                    {campaign.pool.unassigned_only && (
                        <Fact label={t('p11.camprules.unassigned.label')} testId="p11-cd-unassigned">
                            {t('p11.camprules.unassigned.on')}
                        </Fact>
                    )}
                    <Fact label={t('p5.cf.listFilter')}>
                        {Object.keys(campaign.pool.filter || {}).length ? (
                            <PoolFilterChips filter={campaign.pool.filter} names={names} testId="p9-campaign-filter-chips" />
                        ) : (
                            '—'
                        )}
                    </Fact>
                    <div className="text-[12px] text-gray-600">{t('p5.cf.scopeRule')}</div>
                </div>
            </Card>
            <Card title={t('p5.cd.history')}>
                <div className="grid grid-cols-2 gap-3 text-[12.5px]">
                    <Fact label={t('p5.cd.created')}>
                        {campaign.created_by?.name || '—'} · <RiyadhTime iso={campaign.created_at} />
                    </Fact>
                    <Fact label={t('p5.cd.started')}>
                        <RiyadhTime iso={campaign.started_at} />
                    </Fact>
                    <Fact label={t('p5.cd.paused')}>
                        <RiyadhTime iso={campaign.paused_at} />
                    </Fact>
                    <Fact label={t('p5.cd.drainRequested')}>
                        <RiyadhTime iso={campaign.drain_requested_at} />
                    </Fact>
                    <Fact label={t('p5.cd.stopped')}>
                        <RiyadhTime iso={campaign.stopped_at} />
                        {campaign.stop_reason && <span> · {t(`p5.cd.stopReason.${campaign.stop_reason}`)}</span>}
                    </Fact>
                </div>
            </Card>
        </div>
    )
}

/* ------------------------------------------------------------------ page */

const CampaignDetail = () => {
    const { t } = useTranslation()
    const { id = '' } = useParams()
    const perms = usePermissions()
    const { state, setState } = useHashState(schema)
    const q = useV2Query((signal) => campaignsApi.detail(id, signal), [id])
    const [latest, setLatest] = useState<Campaign | null>(null)
    useEffect(() => setLatest(null), [id])
    const campaign = useMemo(() => {
        if (latest && q.data && latest.revision >= q.data.revision) return latest
        return q.data || latest
    }, [latest, q.data])
    const [action, setAction] = useState<Action | null>(null)
    const [editing, setEditing] = useState(false)

    if (!perms.campaigns_manage) {
        /* A member (`campaigns` only) takes part from the calling workspace. */
        return (
            <Notice tone="info" title={t('p5.cd.memberOnlyTitle')} testId="p5-cd-member-only">
                {t('p5.cd.memberOnly')}{' '}
                <Link className="font-extrabold text-azure-600 hover:underline" to={`/calling#campaign=${id}`}>
                    {t('p5.cd.openWorkspace')}
                </Link>
            </Notice>
        )
    }
    if (!campaign && q.error) return <ErrorState error={q.error} onRetry={q.reload} describe={describe} />
    if (!campaign) return <SkeletonLines rows={8} />
    const status = campaign.status
    const manage = !!perms.campaigns_manage
    const can = {
        start: status === 'draft' || status === 'paused',
        pause: status === 'running',
        drain: status === 'running' || status === 'paused',
        stop: status !== 'stopped',
    }
    return (
        <div data-testid="p5-campaign" data-campaign={campaign.id} data-status={status} data-revision={campaign.revision}>
            <Link to="/campaigns" className="mb-2 inline-flex items-center gap-1 text-[13px] font-extrabold text-azure-600 hover:underline">
                <HiOutlineArrowLeft className="rtl:rotate-180" /> {t('p5.cd.back')}
            </Link>
            <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
                <div className="min-w-0">
                    <h3 className="mb-0 flex flex-wrap items-center gap-2 text-[20px] font-extrabold text-navy-600 dark:text-gray-100">
                        <span dir="auto">{campaign.name}</span>
                        <CampaignStatusBadge status={status} />
                    </h3>
                    <div className="mt-1 text-[12.5px] text-gray-600">
                        {t(`p5.campaign.mode.${campaign.settings.next_mode}`)} · {t('p5.cd.membersN', { active: campaign.members.active, total: campaign.members.total })}
                    </div>
                </div>
                {manage && (
                    <div className="flex flex-wrap items-center gap-2" data-testid="p5-cd-actions">
                        {(status === 'stopped' || status === 'draining') && state.tab !== 'report' && (
                            <Button size="sm" variant="solid" onClick={() => setState({ tab: 'report', page: 1 })} data-testid="p11-cd-open-report">
                                {t('p11.campreport.open')}
                            </Button>
                        )}
                        {status !== 'stopped' && (
                            <Button size="sm" icon={<HiOutlinePencil />} onClick={() => setEditing(true)} data-testid="p5-cd-edit">
                                {t('p5.cd.edit')}
                            </Button>
                        )}
                        {can.start && (
                            <Button size="sm" variant="solid" onClick={() => setAction('start')} data-testid="p5-cd-start">
                                {status === 'paused' ? t('p5.cd.resume') : t('p5.cd.start')}
                            </Button>
                        )}
                        {can.pause && (
                            <Button size="sm" onClick={() => setAction('pause')} data-testid="p5-cd-pause">
                                {t('p5.cd.pause')}
                            </Button>
                        )}
                        {can.drain && (
                            <Button size="sm" onClick={() => setAction('drain')} data-testid="p5-cd-drain">
                                {t('p5.cd.drain')}
                            </Button>
                        )}
                        {can.stop && (
                            <Button size="sm" variant="destructive" onClick={() => setAction('stop')} data-testid="p5-cd-stop">
                                {t('p5.cd.stop')}
                            </Button>
                        )}
                    </div>
                )}
            </div>
            <div className="mb-4">
                <CountTabs<Tab>
                    tabs={TABS.map((tab) => ({ value: tab, label: tab === 'report' ? t('p11.campreport.tab') : t(`p5.cd.tab.${tab}`) }))}
                    value={state.tab}
                    onChange={(tab) => setState({ tab, page: 1 })}
                    testId="p5-cd-tabs"
                />
            </div>
            {state.tab === 'live' && <LiveTab campaignId={campaign.id} onCampaign={(c) => setLatest(c)} />}
            {state.tab === 'pool' && <PoolTab campaignId={campaign.id} reason={state.reason} page={state.page} onState={(p) => setState(p)} />}
            {state.tab === 'roster' && <RosterTab campaign={campaign} onChanged={q.reload} />}
            {state.tab === 'settings' && <SettingsTab campaign={campaign} />}
            {state.tab === 'report' && <CampaignReport campaign={campaign} />}
            <TransitionDialog
                campaign={campaign}
                action={action}
                onClose={() => setAction(null)}
                onReload={q.reload}
                onDone={(c) => {
                    setLatest(c)
                    q.reload()
                }}
            />
            {manage && (
                <CampaignForm
                    isOpen={editing}
                    campaign={campaign}
                    onClose={() => setEditing(false)}
                    onSaved={(c) => {
                        setLatest(c)
                        q.reload()
                    }}
                />
            )}
        </div>
    )
}

export default CampaignDetail
