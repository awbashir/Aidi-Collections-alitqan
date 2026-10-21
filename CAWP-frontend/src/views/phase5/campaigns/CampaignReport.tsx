/**
 * Campaign report ("تقرير الحملة", customer 2026-10-21), the `report` tab of `/campaigns/:id`
 * (`campaigns_manage`). Final once the campaign is stopped; "so far" while it runs.
 *
 * - KPIs: clients (total, called, reached, not reached, pending, excluded), attempts, answer
 *   rate (reached = PBX bridge evidence / sent), talk time, promises, collected after contact.
 * - Summary: pool (contracts, filters), creator, start / stop / duration, agents, exclusions,
 *   payments awaiting approval (never counted), auto-assignments when the campaign rules made any.
 * - Charts: outcomes donut, attempts / reached by Riyadh hour, per-agent bars.
 * - Tables: note statuses recorded (main › sub), per agent, promises by due date.
 * - Download: the same as XLSX (Arabic, with a sheet per client), `GET …/report/export`.
 * "Collected after contact" counts approved payments only and is not proof the call caused them.
 */
import { useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import Button from '@/components/ui/Button'
import Table from '@/components/ui/Table'
import Chart from '@/components/shared/Chart'
import { useConfig } from '@/components/ui/ConfigProvider'
import { COLORS, COLOR_8 } from '@/constants/chart.constant'
import { CivilDate, Ltr, RiyadhTime } from '@/components/v2/Display'
import { EmptyState, ErrorState, SkeletonLines } from '@/components/v2/States'
import { TableFrame } from '@/components/v2/ListParts'
import { Segmented } from '@/views/phase1/followUps/parts'
import { describeP5Error } from '@/services/v2'
import type { Campaign, Translate } from '@/services/v2'
import { campaignReportApi } from '@/services/v2/campaignReportApi'
import type { CampaignReport as Report, CampaignReportStatus, ReportOutcome } from '@/services/v2/campaignReportApi'
import { useV2Query } from '@/utils/v2/hooks'
import { formatCount, formatMoney } from '@/utils/v2/format'
import { Card, Fact, Notice } from '../parts'
import { formatDuration, outcomeLabel, poolReason } from '../labels'
import { filterKeyLabel } from './PoolFilterChips'

const { THead, TBody, Tr, Th, Td } = Table
const describe = (e: unknown, t: Translate) => describeP5Error(e, t)
const OUTCOMES: ReportOutcome[] = ['connected', 'promise_discussed', 'dispute', 'no_answer', 'busy', 'wrong_number', 'do_not_call']
const OUTCOME_COLORS = [COLORS[1], COLORS[0], COLORS[4], COLORS[6], COLORS[3], COLORS[5], COLOR_8]
type Days = '0' | '7' | '14' | '30'
const DAYS: Days[] = ['0', '7', '14', '30']
const TOP_AGENTS = 20

const percent = (r: number | null | undefined) => (r === null || r === undefined ? '—' : `${(r * 100).toFixed(1)}%`)
const clock = (s: number | null | undefined) => formatDuration(s) ?? '—'
const statusLabel = (s: Pick<CampaignReportStatus, 'status_name' | 'sub_status_name' | 'sub_status_id'>) =>
    s.sub_status_id ? `${s.status_name || '—'} › ${s.sub_status_name || '—'}` : s.status_name || '—'

const Big = ({ children, testId }: { children: ReactNode; testId?: string }) => (
    <Ltr className="text-[20px] font-extrabold text-navy-600 dark:text-gray-100">
        <span data-testid={testId}>{children}</span>
    </Ltr>
)

const Sub = ({ children }: { children: ReactNode }) => <div className="text-[12px] text-gray-600">{children}</div>

const Kpis = ({ d }: { d: Report }) => {
    const { t } = useTranslation()
    const c = d.clients
    const a = d.attempts
    return (
        <Card lead testId="p11-campreport-kpis">
            <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 xl:grid-cols-6">
                <Fact label={t('p11.campreport.kpi.clients')}>
                    <Big testId="p11-campreport-kpi-total">{formatCount(c.total)}</Big>
                    <Sub>{t('p11.campreport.kpi.poolNow', { n: formatCount(c.pool_now) })}</Sub>
                </Fact>
                <Fact label={t('p11.campreport.kpi.called')}>
                    <Big testId="p11-campreport-kpi-called">{formatCount(c.called)}</Big>
                    <Sub>{t('p11.campreport.kpi.pending', { n: formatCount(c.pending) })}</Sub>
                </Fact>
                <Fact label={t('p11.campreport.kpi.reached')}>
                    <Big testId="p11-campreport-kpi-reached">{formatCount(c.reached)}</Big>
                    <Sub>{t('p11.campreport.kpi.notReached', { n: formatCount(c.not_reached) })}</Sub>
                </Fact>
                <Fact label={t('p11.campreport.kpi.excluded')}>
                    <Big>{formatCount(c.excluded)}</Big>
                </Fact>
                <Fact label={t('p11.campreport.kpi.attempts')}>
                    <Big testId="p11-campreport-kpi-attempts">{formatCount(a.sent)}</Big>
                    <Sub>{t('p11.campreport.kpi.notSent', { n: formatCount(a.not_sent) })}</Sub>
                </Fact>
                <Fact label={t('p11.campreport.kpi.answerRate')}>
                    <Big testId="p11-campreport-kpi-rate">{percent(a.answer_rate)}</Big>
                    <Sub>{t('p11.campreport.kpi.conversations', { n: formatCount(a.reached) })}</Sub>
                </Fact>
                <Fact label={t('p11.campreport.kpi.talk')}>
                    <Big>{clock(a.talk_seconds)}</Big>
                    {a.talk_unknown > 0 && <Sub>{t('p11.campreport.kpi.talkUnknown', { n: formatCount(a.talk_unknown) })}</Sub>}
                </Fact>
                <Fact label={t('p11.campreport.kpi.avgTalk')}>
                    <Big>{clock(a.avg_talk_seconds)}</Big>
                </Fact>
                <Fact label={t('p11.campreport.kpi.promises')}>
                    <Big testId="p11-campreport-kpi-promises">{formatCount(d.promises.count)}</Big>
                    <Sub>
                        <Ltr>{formatMoney(d.promises.amount)}</Ltr>
                    </Sub>
                </Fact>
                <Fact label={t('p11.campreport.kpi.collected')}>
                    <Big testId="p11-campreport-kpi-collected">{formatMoney(d.payments.amount)}</Big>
                    <Sub>{t('p11.campreport.kpi.collectedCases', { n: formatCount(d.payments.cases) })}</Sub>
                </Fact>
                <Fact label={t('p11.campreport.kpi.pendingWrapup')}>
                    <Big>{formatCount(a.pending_wrapup)}</Big>
                </Fact>
                <Fact label={t('p11.campreport.kpi.agents')}>
                    <Big>{formatCount(d.agents_took_part)}</Big>
                </Fact>
            </div>
        </Card>
    )
}

const Summary = ({ d }: { d: Report }) => {
    const { t } = useTranslation()
    const sep = String(t('p5.listSep'))
    const w = d.window
    const agents = d.agents.filter((a) => a.sent > 0)
    const reasons = Object.entries(d.clients.excluded_by_reason)
    return (
        <Card title={t('p11.campreport.summary')} testId="p11-campreport-summary">
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
                <Fact label={t('p11.campreport.contracts')}>
                    <span dir="auto">{d.pool.contracts.map((c) => c.name || c.id.slice(0, 8)).join(sep) || '—'}</span>
                </Fact>
                {d.pool.portfolios.length > 0 && (
                    <Fact label={t('p11.campreport.portfolios')}>
                        <span dir="auto">{d.pool.portfolios.map((p) => p.name || p.id.slice(0, 8)).join(sep)}</span>
                    </Fact>
                )}
                <Fact label={t('p11.campreport.filters')} className="sm:col-span-2">
                    {d.pool.filters.length ? (
                        <div className="flex flex-col gap-0.5">
                            {d.pool.filters.map((f) => (
                                <span key={f.key} dir="auto">
                                    {filterKeyLabel(f.key, t)}: {f.values.join(sep)}
                                </span>
                            ))}
                        </div>
                    ) : (
                        '—'
                    )}
                </Fact>
                <Fact label={t('p11.campreport.createdBy')}>
                    <span dir="auto">{d.campaign.created_by?.name || '—'}</span>
                    <Sub>
                        <RiyadhTime iso={w.created_at} />
                    </Sub>
                </Fact>
                <Fact label={t('p11.campreport.started')}>
                    <RiyadhTime iso={w.started_at} />
                </Fact>
                <Fact label={d.final ? t('p11.campreport.stopped') : t('p11.campreport.asOf')}>
                    <RiyadhTime iso={d.final ? w.stopped_at : d.generated_at} />
                </Fact>
                <Fact label={t('p11.campreport.duration')}>
                    <Ltr>{clock(w.duration_seconds)}</Ltr>
                </Fact>
                <Fact label={t('p11.campreport.firstCall')}>
                    <RiyadhTime iso={w.first_call_at} />
                </Fact>
                <Fact label={t('p11.campreport.lastCall')}>
                    <RiyadhTime iso={w.last_call_at} />
                </Fact>
                <Fact label={t('p11.campreport.agentsList')} className="sm:col-span-2">
                    <span dir="auto">{agents.map((a) => a.user?.name || '—').join(sep) || '—'}</span>
                </Fact>
                <Fact label={t('p11.campreport.excludedWhy')} className="sm:col-span-2" testId="p11-campreport-excluded">
                    {reasons.length ? (
                        <div className="flex flex-wrap gap-2">
                            {reasons.map(([r, n]) => (
                                <span key={r} className="rounded-pill border border-gray-300 px-2.5 py-0.5 text-[12px] dark:border-night-border">
                                    {poolReason(t, r)}: <Ltr>{formatCount(n)}</Ltr>
                                </span>
                            ))}
                        </div>
                    ) : (
                        t('p11.campreport.noExclusions')
                    )}
                </Fact>
                <Fact label={t('p11.campreport.promiseDue')}>
                    {d.promises.first_due ? (
                        <span>
                            <CivilDate value={d.promises.first_due} /> – <CivilDate value={d.promises.last_due} />
                        </span>
                    ) : (
                        '—'
                    )}
                    {d.promises.cancelled > 0 && <Sub>{t('p11.campreport.promisesCancelled', { n: formatCount(d.promises.cancelled) })}</Sub>}
                </Fact>
                <Fact label={t('p11.campreport.pendingPayments')} testId="p11-campreport-pending-payments">
                    <Ltr>{formatMoney(d.payments.pending_not_counted.amount)}</Ltr>
                    <Sub>{t('p11.campreport.pendingPaymentsHint', { n: formatCount(d.payments.pending_not_counted.count) })}</Sub>
                </Fact>
                {d.assignments.available && (
                    <Fact label={t('p11.campreport.assigned')} className="sm:col-span-2" testId="p11-campreport-assigned">
                        <Ltr>{formatCount(d.assignments.count)}</Ltr>
                        {d.assignments.by_user.length > 0 && (
                            <Sub>
                                <span dir="auto">{d.assignments.by_user.map((x) => `${x.user?.name || '—'} (${formatCount(x.count)})`).join(sep)}</span>
                            </Sub>
                        )}
                    </Fact>
                )}
            </div>
            <Notice tone="info" className="mt-4">
                {t('p11.campreport.collectedNote', { days: d.payments.payment_days, until: d.payments.until })}
            </Notice>
        </Card>
    )
}

const Charts = ({ d }: { d: Report }) => {
    const { t } = useTranslation()
    const { direction } = useConfig()
    const outcomes = OUTCOMES.map((o) => d.outcomes[o] || 0)
    const wrapped = outcomes.reduce((n, v) => n + v, 0)
    const busyHours = d.hours.filter((h) => h.attempts > 0)
    const lo = busyHours.length ? busyHours[0].hour : 0
    const hi = busyHours.length ? busyHours[busyHours.length - 1].hour : 23
    const hours = d.hours.filter((h) => h.hour >= lo && h.hour <= hi)
    const agents = d.agents.filter((a) => a.attempts > 0).slice(0, TOP_AGENTS)
    return (
        <div className="grid gap-4 xl:grid-cols-2">
            <Card title={t('p11.campreport.chart.outcomes')} testId="p11-campreport-chart-outcomes">
                {wrapped ? (
                    <Chart
                        type="donut"
                        height={300}
                        series={outcomes}
                        donutTitle={t('p11.campreport.chart.wrapped')}
                        donutText={formatCount(wrapped)}
                        customOptions={{ labels: OUTCOMES.map((o) => String(outcomeLabel(t, o))), colors: OUTCOME_COLORS, legend: { position: 'bottom' } }}
                    />
                ) : (
                    <EmptyState title={t('p11.campreport.noWrapups')} />
                )}
            </Card>
            <Card title={t('p11.campreport.chart.hours')} testId="p11-campreport-chart-hours">
                {busyHours.length ? (
                    <Chart
                        type="bar"
                        direction={direction}
                        height={300}
                        series={[
                            { name: String(t('p11.campreport.attempts')), data: hours.map((h) => h.attempts) },
                            { name: String(t('p11.campreport.reached')), data: hours.map((h) => h.reached) },
                        ]}
                        xAxis={hours.map((h) => `${String(h.hour).padStart(2, '0')}:00`)}
                        customOptions={{ colors: [COLORS[3], COLORS[1]], plotOptions: { bar: { borderRadius: 3, columnWidth: '60%' } }, legend: { position: 'top' } }}
                    />
                ) : (
                    <EmptyState title={t('p11.campreport.noCalls')} />
                )}
            </Card>
            <Card title={t('p11.campreport.chart.agents')} testId="p11-campreport-chart-agents" className="xl:col-span-2">
                {agents.length ? (
                    <Chart
                        type="bar"
                        direction={direction}
                        height={Math.max(220, agents.length * 38)}
                        series={[
                            { name: String(t('p11.campreport.sent')), data: agents.map((a) => a.sent) },
                            { name: String(t('p11.campreport.reached')), data: agents.map((a) => a.reached) },
                            { name: String(t('p11.campreport.promisesN')), data: agents.map((a) => a.promises) },
                        ]}
                        xAxis={agents.map((a) => a.user?.name || '—')}
                        customOptions={{ colors: [COLORS[3], COLORS[1], COLORS[4]], plotOptions: { bar: { horizontal: true, borderRadius: 4 } }, legend: { position: 'top' } }}
                    />
                ) : (
                    <EmptyState title={t('p11.campreport.noCalls')} />
                )}
            </Card>
        </div>
    )
}

const StatusesTable = ({ d }: { d: Report }) => {
    const { t } = useTranslation()
    const total = d.statuses.reduce((n, s) => n + s.count, 0)
    return (
        <TableFrame testId="p11-campreport-statuses" toolbar={<span className="text-[13px] font-extrabold text-navy-600 dark:text-gray-100">{t('p11.campreport.statuses')}</span>}>
            <Table>
                <THead>
                    <Tr>
                        <Th>{t('p11.campreport.col.status')}</Th>
                        <Th className="text-end">{t('p11.campreport.col.count')}</Th>
                        <Th className="text-end">{t('p11.campreport.col.share')}</Th>
                    </Tr>
                </THead>
                <TBody>
                    {d.statuses.length ? (
                        d.statuses.map((s) => (
                            <Tr key={s.key} data-testid="p11-campreport-status-row">
                                <Td dir="auto">
                                    {s.color && <span className="me-2 inline-block h-2.5 w-2.5 rounded-full align-middle" style={{ background: s.color }} aria-hidden />}
                                    {statusLabel(s)}
                                </Td>
                                <Td className="text-end tabular-nums">{formatCount(s.count)}</Td>
                                <Td className="text-end tabular-nums">{percent(total ? s.count / total : null)}</Td>
                            </Tr>
                        ))
                    ) : (
                        <tr>
                            <td colSpan={3}>
                                <EmptyState title={t('p11.campreport.noStatuses')} />
                            </td>
                        </tr>
                    )}
                </TBody>
            </Table>
        </TableFrame>
    )
}

const AgentsTable = ({ d }: { d: Report }) => {
    const { t } = useTranslation()
    const names = useMemo(() => Object.fromEntries(d.statuses.map((s) => [s.key, statusLabel(s)])), [d.statuses])
    return (
        <TableFrame testId="p11-campreport-agents" toolbar={<span className="text-[13px] font-extrabold text-navy-600 dark:text-gray-100">{t('p11.campreport.agents')}</span>}>
            <Table>
                <THead>
                    <Tr>
                        <Th>{t('p11.campreport.col.agent')}</Th>
                        <Th className="text-end">{t('p11.campreport.col.attempts')}</Th>
                        <Th className="text-end">{t('p11.campreport.col.reached')}</Th>
                        <Th className="text-end">{t('p11.campreport.col.rate')}</Th>
                        <Th className="text-end">{t('p11.campreport.col.talk')}</Th>
                        <Th className="text-end">{t('p11.campreport.col.avgTalk')}</Th>
                        <Th className="text-end">{t('p11.campreport.col.promises')}</Th>
                        <Th>{t('p11.campreport.col.statuses')}</Th>
                    </Tr>
                </THead>
                <TBody>
                    {d.agents.length ? (
                        d.agents.map((a) => (
                            <Tr key={a.user?.id || 'none'} data-testid="p11-campreport-agent-row" data-user={a.user?.id}>
                                <Td dir="auto">
                                    <span className="font-extrabold text-navy-600 dark:text-gray-100">{a.user?.name || '—'}</span>
                                    {a.extension && (
                                        <span className="ms-1 text-[12px] text-gray-600">
                                            <Ltr>{a.extension}</Ltr>
                                        </span>
                                    )}
                                    {a.member && !a.active && <Sub>{t('p11.campreport.removed')}</Sub>}
                                </Td>
                                <Td className="text-end tabular-nums">
                                    {formatCount(a.sent)}
                                    {a.attempts !== a.sent && <span className="text-gray-500"> / {formatCount(a.attempts)}</span>}
                                </Td>
                                <Td className="text-end tabular-nums">{formatCount(a.reached)}</Td>
                                <Td className="text-end tabular-nums">{percent(a.answer_rate)}</Td>
                                <Td className="text-end tabular-nums">
                                    <Ltr>{clock(a.talk_seconds)}</Ltr>
                                </Td>
                                <Td className="text-end tabular-nums">
                                    <Ltr>{clock(a.avg_talk_seconds)}</Ltr>
                                </Td>
                                <Td className="text-end tabular-nums">
                                    {formatCount(a.promises)}
                                    {a.promises > 0 && (
                                        <div className="text-[12px] text-gray-600">
                                            <Ltr>{formatMoney(a.promises_amount)}</Ltr>
                                        </div>
                                    )}
                                </Td>
                                <Td dir="auto" className="text-[12.5px]">
                                    {Object.entries(a.statuses)
                                        .sort((x, y) => y[1] - x[1])
                                        .map(([k, n]) => `${names[k] || '—'} (${formatCount(n)})`)
                                        .join(String(t('p5.listSep'))) || '—'}
                                </Td>
                            </Tr>
                        ))
                    ) : (
                        <tr>
                            <td colSpan={8}>
                                <EmptyState title={t('p11.campreport.noCalls')} />
                            </td>
                        </tr>
                    )}
                </TBody>
            </Table>
        </TableFrame>
    )
}

const PromisesTable = ({ d }: { d: Report }) => {
    const { t } = useTranslation()
    if (!d.promises.by_due_date.length) return null
    return (
        <TableFrame testId="p11-campreport-promises" toolbar={<span className="text-[13px] font-extrabold text-navy-600 dark:text-gray-100">{t('p11.campreport.promisesByDue')}</span>}>
            <Table>
                <THead>
                    <Tr>
                        <Th>{t('p11.campreport.col.dueDate')}</Th>
                        <Th className="text-end">{t('p11.campreport.col.count')}</Th>
                        <Th className="text-end">{t('p11.campreport.col.amount')}</Th>
                    </Tr>
                </THead>
                <TBody>
                    {d.promises.by_due_date.map((p) => (
                        <Tr key={p.date}>
                            <Td>
                                <CivilDate value={p.date} />
                            </Td>
                            <Td className="text-end tabular-nums">{formatCount(p.count)}</Td>
                            <Td className="text-end tabular-nums">
                                <Ltr>{formatMoney(p.amount)}</Ltr>
                            </Td>
                        </Tr>
                    ))}
                </TBody>
            </Table>
        </TableFrame>
    )
}

const CampaignReport = ({ campaign }: { campaign: Campaign }) => {
    const { t } = useTranslation()
    const [days, setDays] = useState<Days>('7')
    const [downloading, setDownloading] = useState(false)
    const [downloadError, setDownloadError] = useState<unknown>(null)
    const q = useV2Query((signal) => campaignReportApi.get(campaign.id, { payment_days: Number(days) }, signal), [campaign.id, campaign.status, days])
    const d = q.data
    const download = async () => {
        setDownloading(true)
        setDownloadError(null)
        try {
            await campaignReportApi.download(campaign.id, { payment_days: Number(days) })
        } catch (e) {
            setDownloadError(e)
        } finally {
            setDownloading(false)
        }
    }
    return (
        <div className="flex flex-col gap-4" data-testid="p11-campreport" data-final={d ? String(d.final) : undefined}>
            <div className="flex flex-wrap items-end justify-between gap-3">
                <div>
                    <h4 className="mb-0 text-[16px] font-extrabold text-navy-600 dark:text-gray-100">
                        {(d ? d.final : campaign.status === 'stopped') ? t('p11.campreport.titleFinal') : t('p11.campreport.titleSoFar')}
                    </h4>
                    {d && (
                        <div className="text-[12.5px] text-gray-600">
                            {t('p11.campreport.generated')} <RiyadhTime iso={d.generated_at} />
                        </div>
                    )}
                </div>
                <div className="flex flex-wrap items-center gap-2">
                    <span className="text-[12.5px] text-gray-600">{t('p11.campreport.paymentDays')}</span>
                    <Segmented<Days>
                        value={days}
                        onChange={setDays}
                        label={String(t('p11.campreport.paymentDays'))}
                        testId="p11-campreport-days"
                        options={DAYS.map((v) => ({ value: v, label: t('p11.campreport.daysN', { n: Number(v) }) }))}
                    />
                    <Button size="sm" onClick={q.reload} loading={q.loading && !!d} data-testid="p11-campreport-refresh">
                        {t('p11.campreport.refresh')}
                    </Button>
                    <Button size="sm" variant="solid" loading={downloading} disabled={downloading} onClick={download} data-testid="p11-campreport-download">
                        {t('p11.campreport.download')}
                    </Button>
                </div>
            </div>
            {d && !d.final && (
                <Notice tone="warning" testId="p11-campreport-so-far">
                    {t('p11.campreport.soFarNote')}
                </Notice>
            )}
            {downloadError ? <Notice tone="danger">{describe(downloadError, t)}</Notice> : null}
            {q.error && !d ? (
                <ErrorState error={q.error} onRetry={q.reload} describe={describe} />
            ) : !d ? (
                <SkeletonLines rows={8} />
            ) : (
                <>
                    <Kpis d={d} />
                    <Summary d={d} />
                    <Charts d={d} />
                    <StatusesTable d={d} />
                    <AgentsTable d={d} />
                    <PromisesTable d={d} />
                    <div className="text-[12px] text-gray-600">{t('p11.campreport.definitions')}</div>
                </>
            )}
        </div>
    )
}

export default CampaignReport
