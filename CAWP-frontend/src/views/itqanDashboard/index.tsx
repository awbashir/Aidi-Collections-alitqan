/**
 * Alitqan performance board ("لوحة أداء فنية الإتقان", `/itqan-dashboard`, derived key
 * `itqan_dashboard` = calls_supervise | calls_manage; customer request 2026-10-29), beside the team
 * dashboard. The customer's design (the claude.ai artifact of the same name) fed with the system's
 * real data: one query (`GET /v2/dashboards/itqan`) computes every score on the server.
 *
 * Sections: where the monthly target stands, rating distribution, collected per month, each
 * collector against the expected-to-date, the leaderboard (a row opens the collector's card), the
 * supervisor cards, cash and today's promises by portfolio, the strong-collector indicators and how
 * each score is computed. Criteria with no data in the system are dropped and the weights rescaled
 * (shown in the method card); a criterion unknown for one collector shows "—" and is left out of
 * that collector's score. Collectors with no activity this month are listed apart.
 */
import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import classNames from 'classnames'
import Input from '@/components/ui/Input'
import Select from '@/components/ui/Select'
import Table from '@/components/ui/Table'
import Chart from '@/components/shared/Chart'
import { useConfig } from '@/components/ui/ConfigProvider'
import { COLORS } from '@/constants/chart.constant'
import { Ltr } from '@/components/v2/Display'
import { EmptyState, ErrorState, SkeletonLines } from '@/components/v2/States'
import { PageHead, TableFrame, rowClass } from '@/components/v2/ListParts'
import { Card, Fact, Notice } from '@/views/phase5/parts'
import { describeP5Error } from '@/services/v2'
import type { Option, Translate } from '@/services/v2'
import { itqanApi } from '@/services/v2/itqanApi'
import type { CollectorCriterion, ItqanBoard, ItqanCollector, ItqanRating, SupervisorCriterion } from '@/services/v2/itqanApi'
import { useV2Query } from '@/utils/v2/hooks'
import { formatCount, formatMoney, riyadhToday } from '@/utils/v2/format'

const { THead, TBody, Tr, Th, Td } = Table
const describe = (e: unknown, t: Translate) => describeP5Error(e, t)
const CRITERIA: CollectorCriterion[] = ['payments', 'calls', 'reach', 'ptp', 'intensity', 'attendance', 'system']
const SUP_CRITERIA: SupervisorCriterion[] = ['target', 'quality', 'improvement']
const RATINGS: ItqanRating[] = ['excellent', 'very_good', 'good', 'needs_improvement']
const RATING_COLORS: Record<ItqanRating, string> = { excellent: '#16a34a', very_good: '#0d9488', good: '#d97706', needs_improvement: '#dc2626' }
const RATING_CLASS: Record<ItqanRating, string> = {
    excellent: 'bg-emerald-50 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300',
    very_good: 'bg-teal-50 text-teal-700 dark:bg-teal-500/15 dark:text-teal-300',
    good: 'bg-amber-50 text-amber-700 dark:bg-amber-500/15 dark:text-amber-300',
    needs_improvement: 'bg-red-50 text-red-700 dark:bg-red-500/15 dark:text-red-300',
}
const TOP_PACE = 25

const pct = (v: number | null | undefined) => (v === null || v === undefined ? '—' : `${v.toFixed(1)}%`)
const num = (v: number | null | undefined, d = 1) => (v === null || v === undefined ? '—' : v.toFixed(d))
const money = (v: string | null | undefined) => (v === null || v === undefined ? '—' : formatMoney(v, 0))

const RatingBadge = ({ rating, t }: { rating: ItqanRating | null; t: Translate }) =>
    rating ? (
        <span className={classNames('inline-block whitespace-nowrap rounded-full px-2 py-0.5 text-[12px] font-extrabold', RATING_CLASS[rating])}>
            {t(`p13.itqan.rating.${rating}`)}
        </span>
    ) : (
        <span className="text-gray-500">—</span>
    )

const ScoreBar = ({ value }: { value: number | null }) => (
    <div className="flex min-w-[110px] items-center gap-2">
        <div className="h-2 flex-1 overflow-hidden rounded-full bg-gray-100 dark:bg-night-border">
            <div
                className="h-full rounded-full"
                style={{
                    width: `${Math.max(0, Math.min(100, value ?? 0))}%`,
                    background: value === null ? 'transparent' : value >= 90 ? RATING_COLORS.excellent : value >= 80 ? RATING_COLORS.very_good : value >= 70 ? RATING_COLORS.good : RATING_COLORS.needs_improvement,
                }}
            />
        </div>
        <Ltr className="w-10 text-end text-[13px] font-extrabold tabular-nums">{num(value)}</Ltr>
    </div>
)

const Big = ({ children }: { children: React.ReactNode }) => (
    <Ltr className="text-[20px] font-extrabold tabular-nums text-navy-600 dark:text-gray-100">{children}</Ltr>
)

/** The collector's card: every criterion with its indicator, score and weight, and the 6-month trend. */
const CollectorCard = ({ c, data, onClose, t }: { c: ItqanCollector; data: ItqanBoard; onClose: () => void; t: Translate }) => {
    const { direction } = useConfig()
    const w = data.weights.collector.effective
    const p = data.params
    const indicator: Record<CollectorCriterion, string> = {
        payments: t('p13.itqan.ind.payments', { got: money(c.collected), exp: money(c.expected_to_date) }) as string,
        calls: t('p13.itqan.ind.calls', { n: num(c.calls_per_day), target: num(p.calls_target_per_day) }) as string,
        reach: t('p13.itqan.ind.reach', { rate: pct(c.reach_rate), target: p.reach_target }) as string,
        ptp: t('p13.itqan.ind.ptp', { kept: pct(c.kept_rate), paid: pct(c.paid_rate), n: c.promises_due }) as string,
        intensity: t('p13.itqan.ind.intensity', { attempts: num(c.attempts_per_client), notes: num(c.notes_per_day) }) as string,
        attendance: t('p13.itqan.ind.attendance', { n: c.login_days, of: data.work_days_passed }) as string,
        system: t('p13.itqan.ind.system', { rate: pct(c.update_rate), days: p.system_days }) as string,
    }
    const trend = data.trend.collectors[c.id] || []
    return (
        <Card
            lead
            testId="p13-itqan-collector"
            title={
                <span className="flex flex-wrap items-center gap-2">
                    {c.name || '—'} <RatingBadge rating={c.rating} t={t} />
                </span>
            }
            actions={
                <button type="button" className="text-[12.5px] font-extrabold text-navy-600 underline dark:text-gray-100" onClick={onClose}>
                    {t('p13.itqan.close')}
                </button>
            }
        >
            <div className="mb-4 grid grid-cols-2 gap-4 sm:grid-cols-4 xl:grid-cols-6">
                <Fact label={t('p13.itqan.score')}><Big>{num(c.score)}</Big></Fact>
                <Fact label={t('p13.itqan.rank')}><Big>{c.rank ?? '—'}</Big></Fact>
                <Fact label={t('p13.itqan.target')}><Big>{money(c.target)}</Big></Fact>
                <Fact label={t('p13.itqan.collected')}>
                    <Big>{money(c.collected)}</Big>
                    <div className="text-[12px] text-gray-600">{t('p13.itqan.ofTarget', { p: pct(c.achievement) })}</div>
                </Fact>
                <Fact label={t('p13.itqan.supervisor')}>{c.supervisor?.name || '—'}</Fact>
                <Fact label={t('p13.itqan.portfolio')}>{c.contract_name || '—'}</Fact>
            </div>
            <TableFrame>
                <Table compact>
                    <THead>
                        <Tr>
                            <Th>{t('p13.itqan.criterion')}</Th>
                            <Th>{t('p13.itqan.weight')}</Th>
                            <Th>{t('p13.itqan.indicator')}</Th>
                            <Th>{t('p13.itqan.score')}</Th>
                        </Tr>
                    </THead>
                    <TBody>
                        {CRITERIA.map((k) => (
                            <Tr key={k}>
                                <Td className="font-bold">{t(`p13.itqan.crit.${k}`)}</Td>
                                <Td><Ltr>{w[k]}%</Ltr></Td>
                                <Td className="text-[12.5px] text-gray-700 dark:text-night-body">{indicator[k]}</Td>
                                <Td><ScoreBar value={c.scores[k]} /></Td>
                            </Tr>
                        ))}
                    </TBody>
                </Table>
            </TableFrame>
            {trend.some((v) => v !== null) && (
                <div className="mt-4">
                    <div className="text-eyebrow mb-1">{t('p13.itqan.trendCollector')}</div>
                    <Chart type="area" direction={direction} height={160} series={[{ name: t('p13.itqan.achievement') as string, data: trend.map((v) => v ?? 0) }]} xAxis={data.trend.months} customOptions={{ colors: [COLORS[0]], dataLabels: { enabled: false }, yaxis: { labels: { formatter: (v: number) => `${Math.round(v)}%` } } }} />
                </div>
            )}
        </Card>
    )
}

const ItqanDashboardPage = () => {
    const { t } = useTranslation()
    const { direction } = useConfig()
    const thisMonth = riyadhToday().slice(0, 7)
    const [month, setMonth] = useState(thisMonth)
    const [contract, setContract] = useState('')
    const [open, setOpen] = useState<string | null>(null)
    const q = useV2Query((signal) => itqanApi.get({ month, contract_id: contract || undefined }, signal), [month, contract])
    const data = q.data
    const totals = data?.totals

    const contractOptions: Option[] = [
        { value: '', label: t('p13.itqan.allPortfolios') as string },
        ...(data?.options.contracts || []).map((c) => ({ value: c.id, label: c.name })),
    ]
    const selected = data?.collectors.find((c) => c.id === open) || null
    const paced = useMemo(
        () => [...(data?.collectors || [])].filter((c) => c.expected_to_date && Number(c.expected_to_date) > 0).sort((a, b) => (b.pace ?? 0) - (a.pace ?? 0)).slice(0, TOP_PACE),
        [data]
    )
    const best = useMemo(() => {
        const rows = data?.collectors || []
        const max = (f: (c: ItqanCollector) => number | null, low = false) => {
            const vals = rows.map(f).filter((v): v is number => v !== null)
            return vals.length ? (low ? Math.min(...vals) : Math.max(...vals)) : null
        }
        return {
            call_to_ptp: max((c) => c.call_to_ptp),
            kept_rate: max((c) => c.kept_rate),
            paid_rate: max((c) => c.paid_rate),
            avg_payment: max((c) => (c.avg_payment ? Number(c.avg_payment) : null)),
            calls_per_contact: max((c) => c.calls_per_contact, true),
            new_files_24h_rate: max((c) => c.new_files_24h_rate),
            attempts_per_client: max((c) => c.attempts_per_client),
        }
    }, [data])
    const hi = (v: number | null, b: number | null) => (v !== null && b !== null && v === b ? 'bg-emerald-50 font-extrabold text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300' : '')

    const subtitle = data ? t(`p13.itqan.scope.${data.scope}`) : t('p13.itqan.subtitle')
    const target = totals?.target ? Number(totals.target) : 0
    const collected = totals ? Number(totals.collected) : 0
    const promised = totals ? Math.min(Number(totals.promises_open_amount), Math.max(0, target - collected)) : 0

    return (
        <div data-testid="p13-itqan" className="flex flex-col gap-4">
            <PageHead title={t('p13.itqan.title')} subtitle={subtitle} onRefresh={q.reload} refreshing={q.loading} />

            <div className="flex flex-wrap items-end gap-3" data-testid="p13-itqan-filters">
                <label className="flex flex-col gap-1 text-[12.5px]">
                    <span className="text-gray-600">{t('p13.itqan.month')}</span>
                    <Input size="sm" type="month" value={month} max={thisMonth} onChange={(e) => e.target.value && setMonth(e.target.value)} className="w-[170px]" />
                </label>
                <label className="flex w-full max-w-[280px] flex-col gap-1 text-[12.5px]">
                    <span className="text-gray-600">{t('p13.itqan.portfolio')}</span>
                    <Select size="sm" isSearchable options={contractOptions} value={contractOptions.find((o) => o.value === contract) || contractOptions[0]} onChange={(o) => setContract((o as Option | null)?.value || '')} />
                </label>
                {data && (
                    <div className="mb-1 text-[12.5px] text-gray-600">
                        {t(data.closed ? 'p13.itqan.workDaysClosed' : 'p13.itqan.workDays', { passed: data.work_days_passed, total: data.work_days })}
                    </div>
                )}
            </div>

            {q.error && !data ? (
                <ErrorState error={q.error} onRetry={q.reload} describe={describe} />
            ) : !data || !totals ? (
                <SkeletonLines rows={10} />
            ) : (
                <>
                    {data.idle.length > 0 && (
                        <Notice tone="neutral" testId="p13-itqan-idle">
                            {t('p13.itqan.idle', { n: data.idle.length, names: data.idle.slice(0, 8).map((u) => u.name || '—').join('، ') })}
                        </Notice>
                    )}

                    {/* where the monthly target stands */}
                    <Card lead title={t('p13.itqan.overview')} testId="p13-itqan-overview">
                        <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 xl:grid-cols-6">
                            <Fact label={t('p13.itqan.target')}><Big>{money(totals.target)}</Big></Fact>
                            <Fact label={t('p13.itqan.collected')}>
                                <Big>{money(totals.collected)}</Big>
                                <div className="text-[12px] text-gray-600">{t('p13.itqan.ofTarget', { p: pct(totals.achievement) })}</div>
                            </Fact>
                            <Fact label={t('p13.itqan.expected')}>
                                <Big>{money(totals.expected_to_date)}</Big>
                                <div className="text-[12px] text-gray-600">{t('p13.itqan.pace', { p: pct(totals.pace) })}</div>
                            </Fact>
                            <Fact label={t('p13.itqan.openPromises')}><Big>{money(totals.promises_open_amount)}</Big></Fact>
                            <Fact label={t('p13.itqan.gap')}><Big>{money(totals.gap_after_promises)}</Big></Fact>
                            <Fact label={t('p13.itqan.collectors')}><Big>{formatCount(totals.collectors)}</Big></Fact>
                        </div>
                        {target > 0 && (
                            <div className="mt-4">
                                <div className="flex h-3 overflow-hidden rounded-full bg-gray-100 dark:bg-night-border" aria-hidden>
                                    <div style={{ width: `${Math.min(100, (collected * 100) / target)}%`, background: COLORS[0] }} />
                                    <div style={{ width: `${Math.min(100, (promised * 100) / target)}%`, background: COLORS[2] }} />
                                </div>
                                <div className="mt-1 flex flex-wrap gap-4 text-[12px] text-gray-600">
                                    <span><span className="me-1 inline-block h-2 w-2 rounded-full" style={{ background: COLORS[0] }} />{t('p13.itqan.collected')}</span>
                                    <span><span className="me-1 inline-block h-2 w-2 rounded-full" style={{ background: COLORS[2] }} />{t('p13.itqan.openPromises')}</span>
                                    <span><span className="me-1 inline-block h-2 w-2 rounded-full bg-gray-200" />{t('p13.itqan.gap')}</span>
                                </div>
                            </div>
                        )}
                    </Card>

                    {selected && <CollectorCard c={selected} data={data} onClose={() => setOpen(null)} t={t} />}

                    <div className="grid gap-4 xl:grid-cols-3">
                        <Card title={t('p13.itqan.ratings')} testId="p13-itqan-ratings">
                            {totals.collectors ? (
                                <Chart
                                    type="donut"
                                    direction={direction}
                                    height={260}
                                    series={RATINGS.map((r) => totals.ratings[r] || 0)}
                                    donutTitle={t('p13.itqan.collectors') as string}
                                    donutText={formatCount(totals.collectors)}
                                    customOptions={{ labels: RATINGS.map((r) => t(`p13.itqan.rating.${r}`) as string), colors: RATINGS.map((r) => RATING_COLORS[r]), legend: { position: 'bottom' } }}
                                />
                            ) : (
                                <EmptyState title={t('p13.itqan.none')} />
                            )}
                        </Card>
                        <Card title={t('p13.itqan.trend')} testId="p13-itqan-trend" className="xl:col-span-2">
                            <Chart
                                type="bar"
                                direction={direction}
                                height={260}
                                series={[{ name: t('p13.itqan.collected') as string, data: [...data.trend.total.map((m) => Number(m.collected)), collected] }]}
                                xAxis={[...data.trend.months, data.month]}
                                customOptions={{ colors: [COLORS[0]], dataLabels: { enabled: false }, plotOptions: { bar: { borderRadius: 4 } }, yaxis: { labels: { formatter: (v: number) => formatMoney(v, 0) } } }}
                            />
                            {data.trend.total.every((m) => m.achievement === null) && <div className="mt-1 text-[12px] text-gray-600">{t('p13.itqan.trendNoTargets')}</div>}
                        </Card>
                    </div>

                    <Card title={t('p13.itqan.paceTitle')} testId="p13-itqan-pace">
                        {paced.length ? (
                            <Chart
                                type="bar"
                                direction={direction}
                                height={Math.max(260, paced.length * 30)}
                                series={[
                                    { name: t('p13.itqan.collected') as string, data: paced.map((c) => Number(c.collected)) },
                                    { name: t('p13.itqan.expected') as string, data: paced.map((c) => Number(c.expected_to_date)) },
                                ]}
                                xAxis={paced.map((c) => c.name || '—')}
                                customOptions={{
                                    colors: [COLORS[0], COLORS[7] || '#cbd5e1'],
                                    chart: { events: { dataPointSelection: (_e: unknown, _c: unknown, o: { dataPointIndex: number }) => setOpen(paced[o.dataPointIndex]?.id ?? null) } },
                                    plotOptions: { bar: { horizontal: true, borderRadius: 3 } },
                                    dataLabels: { enabled: false },
                                    legend: { position: 'top' },
                                    xaxis: { labels: { formatter: (v: string) => formatMoney(Number(v), 0) } },
                                }}
                            />
                        ) : (
                            <EmptyState title={t('p13.itqan.noTargets')} />
                        )}
                    </Card>

                    {/* leaderboard */}
                    <Card title={t('p13.itqan.leaderboard')} testId="p13-itqan-leaderboard" actions={<span className="text-[12px] text-gray-600">{t('p13.itqan.rowHint')}</span>}>
                        {data.collectors.length ? (
                            <TableFrame>
                                <Table compact>
                                    <THead>
                                        <Tr>
                                            <Th>#</Th>
                                            <Th>{t('p13.itqan.collector')}</Th>
                                            <Th>{t('p13.itqan.portfolio')}</Th>
                                            <Th>{t('p13.itqan.score')}</Th>
                                            <Th>{t('p13.itqan.ratingCol')}</Th>
                                            {CRITERIA.map((k) => (
                                                <Th key={k} className="whitespace-nowrap">{t(`p13.itqan.critShort.${k}`)}</Th>
                                            ))}
                                            <Th>{t('p13.itqan.collected')}</Th>
                                            <Th>{t('p13.itqan.paceCol')}</Th>
                                        </Tr>
                                    </THead>
                                    <TBody>
                                        {data.collectors.map((c) => (
                                            <Tr key={c.id} className={rowClass(open === c.id)} onClick={() => setOpen(open === c.id ? null : c.id)} data-testid="p13-itqan-row">
                                                <Td><Ltr className="font-extrabold">{c.rank ?? '—'}</Ltr></Td>
                                                <Td className="whitespace-nowrap font-bold">{c.name || '—'}</Td>
                                                <Td className="whitespace-nowrap text-[12.5px]">{c.contract_name || '—'}</Td>
                                                <Td><ScoreBar value={c.score} /></Td>
                                                <Td><RatingBadge rating={c.rating} t={t} /></Td>
                                                {CRITERIA.map((k) => (
                                                    <Td key={k}><Ltr className="tabular-nums">{num(c.scores[k], 0)}</Ltr></Td>
                                                ))}
                                                <Td><Ltr className="tabular-nums">{money(c.collected)}</Ltr></Td>
                                                <Td><Ltr className="tabular-nums">{pct(c.pace)}</Ltr></Td>
                                            </Tr>
                                        ))}
                                    </TBody>
                                </Table>
                            </TableFrame>
                        ) : (
                            <EmptyState title={t('p13.itqan.none')} />
                        )}
                    </Card>

                    {/* supervisors */}
                    {data.supervisors.length > 0 && (
                        <Card title={t('p13.itqan.supervisors')} testId="p13-itqan-supervisors">
                            <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
                                {data.supervisors.map((s) => (
                                    <div key={s.id} className="rounded-lg border border-gray-200 p-4 dark:border-night-border">
                                        <div className="mb-2 flex items-center justify-between gap-2">
                                            <div className="font-extrabold text-navy-600 dark:text-gray-100">{s.name || '—'}</div>
                                            <RatingBadge rating={s.rating} t={t} />
                                        </div>
                                        <div className="mb-3 flex items-baseline gap-2">
                                            <Big>{num(s.score)}</Big>
                                            <span className="text-[12px] text-gray-600">{t('p13.itqan.teamOf', { n: s.team_size })}</span>
                                        </div>
                                        {SUP_CRITERIA.map((k) => (
                                            <div key={k} className="mb-1.5">
                                                <div className="flex justify-between text-[12px] text-gray-600">
                                                    <span>{t(`p13.itqan.supCrit.${k}`)}</span>
                                                    <Ltr>{data.weights.supervisor.effective[k]}%</Ltr>
                                                </div>
                                                <ScoreBar value={s.scores[k]} />
                                            </div>
                                        ))}
                                        <div className="mt-2 text-[12px] text-gray-600">
                                            {t('p13.itqan.supLine', { got: money(s.collected), target: money(s.target), prev: pct(s.prev_achievement) })}
                                        </div>
                                    </div>
                                ))}
                            </div>
                        </Card>
                    )}

                    {/* portfolios */}
                    <Card title={t('p13.itqan.portfolios')} testId="p13-itqan-portfolios">
                        <TableFrame>
                            <Table compact>
                                <THead>
                                    <Tr>
                                        <Th>{t('p13.itqan.portfolio')}</Th>
                                        <Th>{t('p13.itqan.collectors')}</Th>
                                        <Th>{t('p13.itqan.target')}</Th>
                                        <Th>{t('p13.itqan.collected')}</Th>
                                        <Th>{t('p13.itqan.achievement')}</Th>
                                        <Th>{t('p13.itqan.cash')}</Th>
                                        <Th>{t('p13.itqan.scheduled')}</Th>
                                        <Th>{t('p13.itqan.promisesToday')}</Th>
                                        <Th>{t('p13.itqan.openPromises')}</Th>
                                    </Tr>
                                </THead>
                                <TBody>
                                    {data.portfolios.map((g, i) => (
                                        <Tr key={g.contract_id || i}>
                                            <Td className="whitespace-nowrap font-bold">{g.name || '—'}</Td>
                                            <Td><Ltr>{formatCount(g.collectors)}</Ltr></Td>
                                            <Td><Ltr className="tabular-nums">{money(g.target)}</Ltr></Td>
                                            <Td><Ltr className="tabular-nums">{money(g.collected)}</Ltr></Td>
                                            <Td><Ltr className="tabular-nums">{pct(g.achievement)}</Ltr></Td>
                                            <Td><Ltr className="tabular-nums">{money(g.cash)}</Ltr></Td>
                                            <Td><Ltr className="tabular-nums">{money(g.scheduled)}</Ltr></Td>
                                            <Td><Ltr className="tabular-nums">{formatCount(g.promises_today)} · {money(g.promises_today_amount)}</Ltr></Td>
                                            <Td><Ltr className="tabular-nums">{money(g.promises_open_amount)}</Ltr></Td>
                                        </Tr>
                                    ))}
                                </TBody>
                            </Table>
                        </TableFrame>
                    </Card>

                    {/* strong-collector indicators */}
                    <Card title={t('p13.itqan.advanced')} testId="p13-itqan-advanced" actions={<span className="text-[12px] text-gray-600">{t('p13.itqan.advancedHint')}</span>}>
                        <TableFrame>
                            <Table compact>
                                <THead>
                                    <Tr>
                                        <Th>{t('p13.itqan.collector')}</Th>
                                        <Th>{t('p13.itqan.adv.call_to_ptp')}</Th>
                                        <Th>{t('p13.itqan.adv.kept_rate')}</Th>
                                        <Th>{t('p13.itqan.adv.paid_rate')}</Th>
                                        <Th>{t('p13.itqan.adv.avg_payment')}</Th>
                                        <Th>{t('p13.itqan.adv.calls_per_contact')}</Th>
                                        <Th>{t('p13.itqan.adv.attempts_per_client')}</Th>
                                        <Th>{t('p13.itqan.adv.new_files_24h')}</Th>
                                    </Tr>
                                </THead>
                                <TBody>
                                    {data.collectors.map((c) => (
                                        <Tr key={c.id} className={rowClass(open === c.id)} onClick={() => setOpen(c.id)}>
                                            <Td className="whitespace-nowrap font-bold">{c.name || '—'}</Td>
                                            <Td className={hi(c.call_to_ptp, best.call_to_ptp)}><Ltr>{pct(c.call_to_ptp)}</Ltr></Td>
                                            <Td className={hi(c.kept_rate, best.kept_rate)}><Ltr>{pct(c.kept_rate)}</Ltr></Td>
                                            <Td className={hi(c.paid_rate, best.paid_rate)}><Ltr>{pct(c.paid_rate)}</Ltr></Td>
                                            <Td className={hi(c.avg_payment ? Number(c.avg_payment) : null, best.avg_payment)}><Ltr>{money(c.avg_payment)}</Ltr></Td>
                                            <Td className={hi(c.calls_per_contact, best.calls_per_contact)}><Ltr>{num(c.calls_per_contact)}</Ltr></Td>
                                            <Td className={hi(c.attempts_per_client, best.attempts_per_client)}><Ltr>{num(c.attempts_per_client)}</Ltr></Td>
                                            <Td className={hi(c.new_files_24h_rate, best.new_files_24h_rate)}>
                                                <Ltr>{pct(c.new_files_24h_rate)}</Ltr>
                                                {c.new_files > 0 && <span className="ms-1 text-[11px] text-gray-500">({formatCount(c.new_files_24h)}/{formatCount(c.new_files)})</span>}
                                            </Td>
                                        </Tr>
                                    ))}
                                </TBody>
                            </Table>
                        </TableFrame>
                    </Card>

                    {/* how the scores are computed */}
                    <div className="grid gap-4 xl:grid-cols-2">
                        <Card title={t('p13.itqan.methodCollector')} testId="p13-itqan-method">
                            <div className="mb-2 text-[12.5px] text-gray-600">{t('p13.itqan.methodIntro')}</div>
                            <TableFrame>
                                <Table compact>
                                    <THead>
                                        <Tr>
                                            <Th>{t('p13.itqan.criterion')}</Th>
                                            <Th>{t('p13.itqan.weightOriginal')}</Th>
                                            <Th>{t('p13.itqan.weight')}</Th>
                                            <Th>{t('p13.itqan.how')}</Th>
                                        </Tr>
                                    </THead>
                                    <TBody>
                                        {CRITERIA.map((k) => (
                                            <Tr key={k}>
                                                <Td className="font-bold">{t(`p13.itqan.crit.${k}`)}</Td>
                                                <Td><Ltr>{data.weights.collector.original[k]}</Ltr></Td>
                                                <Td><Ltr>{data.weights.collector.effective[k]}%</Ltr></Td>
                                                <Td className="text-[12.5px]">{t(`p13.itqan.how.${k}`, { ...data.params, calls: num(data.params.calls_target_per_day) })}</Td>
                                            </Tr>
                                        ))}
                                    </TBody>
                                </Table>
                            </TableFrame>
                            <div className="mt-2 text-[12.5px] text-gray-600">
                                {t('p13.itqan.dropped', { list: Object.entries(data.weights.collector.dropped).map(([k, v]) => `${t(`p13.itqan.drop.${k}`)} (${v})`).join('، ') })}
                            </div>
                        </Card>
                        <Card title={t('p13.itqan.methodSupervisor')}>
                            <TableFrame>
                                <Table compact>
                                    <THead>
                                        <Tr>
                                            <Th>{t('p13.itqan.criterion')}</Th>
                                            <Th>{t('p13.itqan.weightOriginal')}</Th>
                                            <Th>{t('p13.itqan.weight')}</Th>
                                            <Th>{t('p13.itqan.how')}</Th>
                                        </Tr>
                                    </THead>
                                    <TBody>
                                        {SUP_CRITERIA.map((k) => (
                                            <Tr key={k}>
                                                <Td className="font-bold">{t(`p13.itqan.supCrit.${k}`)}</Td>
                                                <Td><Ltr>{data.weights.supervisor.original[k]}</Ltr></Td>
                                                <Td><Ltr>{data.weights.supervisor.effective[k]}%</Ltr></Td>
                                                <Td className="text-[12.5px]">{t(`p13.itqan.supHow.${k}`)}</Td>
                                            </Tr>
                                        ))}
                                    </TBody>
                                </Table>
                            </TableFrame>
                            <div className="mt-2 text-[12.5px] text-gray-600">
                                {t('p13.itqan.dropped', { list: Object.entries(data.weights.supervisor.dropped).map(([k, v]) => `${t(`p13.itqan.drop.${k}`)} (${v})`).join('، ') })}
                            </div>
                            <div className="mt-2 text-[12.5px] text-gray-600">{t('p13.itqan.ratingRule')}</div>
                        </Card>
                    </div>
                </>
            )}
        </div>
    )
}

export default ItqanDashboardPage
