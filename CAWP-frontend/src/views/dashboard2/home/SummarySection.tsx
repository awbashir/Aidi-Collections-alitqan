/**
 * KPI cards (GET /v2/dashboards/home/summary, loaded by the page) + the month-target radial gauge + a donut:
 * collected by contract for admins (a slice click filters every card to that contract),
 * the promises of the period by state for a collector.
 */
import { useTranslation } from 'react-i18next'
import { Link, useNavigate } from 'react-router-dom'
import { SaudiRiyal } from 'lucide-react'
import Chart from '@/components/shared/Chart'
import { EmptyState, ErrorState, SkeletonLines } from '@/components/v2/States'
import { COLORS, COLOR_8, COLOR_HIGHLIGHT, COLOR_3 } from '@/constants/chart.constant'
import type { HomeSummary, HomeTarget } from '@/services/v2/homeDashboardApi'
import type { QueryState } from '@/utils/v2/hooks'
import { formatCount } from '@/utils/v2/format'
import type { HomeCtx } from './context'
import { DetailLine, HomeCard, KpiCard } from './parts'
import { arr, grouped, moneyText, num, pct, text } from './safe'

const SUCCESS = '#128A5C'
const DANGER = '#B3261E'
const Riyal = () => <SaudiRiyal size={13} className="inline" aria-label="SAR" />

export const TargetGauge = ({ target, link }: { target?: HomeTarget; link?: string }) => {
    const { t } = useTranslation()
    const percent = typeof target?.percent === 'number' ? target.percent : null
    const known = target?.target !== null && target?.target !== undefined
    const expected = num(target?.expected_to_date, NaN)
    const achieved = num(target?.achieved)
    const onPace = Number.isFinite(expected) ? achieved >= expected : null
    return (
        <HomeCard
            title={t('p10.homedash.target.title')}
            subtitle={target?.month ? t('p10.homedash.target.month', { month: target.month }) : undefined}
            testId="homedash-target"
            actions={
                link ? (
                    <Link to={link} className="text-[12.5px] font-extrabold text-azure-600 hover:underline">
                        {t('p10.homedash.details')}
                    </Link>
                ) : undefined
            }
        >
            {!known ? (
                <EmptyState title={t('p10.homedash.target.unknown')} hint={t('p10.homedash.target.unknownHint')} className="py-6" />
            ) : (
                <div className="grid items-center gap-2 sm:grid-cols-2">
                    <Chart
                        type="radialBar"
                        height={230}
                        series={[Math.max(0, Math.min(100, percent ?? 0))]}
                        customOptions={{
                            labels: [t('p10.homedash.target.achieved') as string],
                            colors: [onPace === false ? DANGER : percent !== null && percent >= 100 ? SUCCESS : COLORS[1]],
                            plotOptions: {
                                radialBar: {
                                    hollow: { size: '62%' },
                                    track: { background: '#E7EEF2' },
                                    dataLabels: {
                                        name: { show: true, fontSize: '12px', offsetY: 18 },
                                        value: { show: true, fontSize: '24px', fontWeight: 800, offsetY: -12, formatter: () => pct(percent) },
                                    },
                                },
                            },
                            stroke: { lineCap: 'round' },
                        }}
                    />
                    <div className="text-[12.5px]">
                        <DetailLine label={t('p10.homedash.target.target')} value={moneyText(target?.target, 0)} />
                        <DetailLine label={t('p10.homedash.target.achieved')} value={moneyText(target?.achieved, 0)} />
                        <DetailLine label={t('p10.homedash.target.expected')} value={moneyText(target?.expected_to_date, 0)} />
                        <DetailLine label={t('p10.homedash.target.remaining')} value={moneyText(target?.remaining, 0)} />
                        <DetailLine
                            label={t('p10.homedash.target.workDays')}
                            value={`${num(target?.work_days_passed)} / ${num(target?.work_days)}`}
                        />
                        {onPace !== null && (
                            <div className={`mt-2 tag ${onPace ? 'tag-success' : 'tag-danger'}`}>
                                {onPace ? t('p10.homedash.target.onPace') : t('p10.homedash.target.behind')}
                            </div>
                        )}
                        {num(target?.targets_unknown) > 0 && (
                            <div className="mt-2 text-[12px] text-gray-600">
                                {t('p10.homedash.target.someUnknown', { n: num(target?.targets_unknown) })}
                            </div>
                        )}
                    </div>
                </div>
            )}
        </HomeCard>
    )
}

const ContractDonut = ({ data, ctx }: { data: HomeSummary; ctx: HomeCtx }) => {
    const { t } = useTranslation()
    const rows = arr(data.by_contract).filter((r) => num(r?.amount) > 0)
    const top = rows.slice(0, 7)
    const rest = rows.slice(7)
    const labels = [...top.map((r) => text(r?.name, t('p10.homedash.noContract') as string)), ...(rest.length ? [t('p10.homedash.other') as string] : [])]
    const values = [...top.map((r) => num(r?.amount)), ...(rest.length ? [rest.reduce((n, r) => n + num(r?.amount), 0)] : [])]
    const colors = top.map((r, i) => (ctx.contract && r?.contract_id === ctx.contract ? COLOR_HIGHLIGHT : COLORS[i % 7]))
    if (rest.length) colors.push(COLOR_8)
    const total = values.reduce((a, b) => a + b, 0)
    return (
        <HomeCard
            title={t('p10.homedash.byContract.title')}
            subtitle={ctx.admin ? t('p10.homedash.byContract.hint') : undefined}
            testId="homedash-by-contract"
            resetKeys={[data]}
        >
            {values.length ? (
                <Chart
                    type="donut"
                    height={260}
                    series={values}
                    donutTitle={t('p10.homedash.kpi.collected') as string}
                    donutText={grouped(total)}
                    customOptions={{
                        labels,
                        colors,
                        legend: { position: 'bottom' },
                        tooltip: { y: { formatter: (v: number) => grouped(v, 2) } },
                        chart: {
                            events: {
                                dataPointSelection: (_e: unknown, _c: unknown, o: { dataPointIndex: number }) => {
                                    const id = top[o?.dataPointIndex]?.contract_id
                                    if (ctx.admin && id) ctx.setContract(ctx.contract === id ? '' : id)
                                },
                            },
                        },
                    }}
                />
            ) : (
                <EmptyState title={t('p10.homedash.noCollections')} className="py-6" />
            )}
        </HomeCard>
    )
}

const PromiseStateDonut = ({ data, ctx }: { data: HomeSummary; ctx: HomeCtx }) => {
    const { t } = useTranslation()
    const navigate = useNavigate()
    const st = data.kpis?.promises_due?.by_state
    const keys = ['fulfilled', 'overdue', 'open'] as const
    const values = keys.map((k) => num(st?.[k]?.count))
    const total = values.reduce((a, b) => a + b, 0)
    return (
        <HomeCard title={t('p10.homedash.promiseStates.title')} subtitle={t('p10.homedash.promiseStates.hint')} testId="homedash-promise-states">
            {total ? (
                <Chart
                    type="donut"
                    height={260}
                    series={values}
                    donutTitle={t('p10.homedash.kpi.promises') as string}
                    donutText={formatCount(total)}
                    customOptions={{
                        labels: keys.map((k) => t(`p10.homedash.state.${k}`) as string),
                        colors: [SUCCESS, DANGER, COLOR_3],
                        legend: { position: 'bottom' },
                        chart: {
                            events: {
                                dataPointSelection: (_e: unknown, _c: unknown, o: { dataPointIndex: number }) => {
                                    const to = ctx.links.promises(data.from, data.to, keys[o?.dataPointIndex])
                                    if (to) navigate(to)
                                },
                            },
                        },
                    }}
                />
            ) : (
                <EmptyState title={t('p10.homedash.noPromises')} className="py-6" />
            )}
        </HomeCard>
    )
}

/** The summary query lives in the page (its contract list feeds the page filter). */
const SummarySection = ({ ctx, q }: { ctx: HomeCtx; q: QueryState<HomeSummary> }) => {
    const { t } = useTranslation()
    const data = q.data
    if (q.error && !data) return <ErrorState error={q.error} onRetry={q.reload} />
    if (!data) return <SkeletonLines rows={6} />
    const k = data.kpis || {}
    const from = data.from
    const to = data.to
    const prevLabel = `${text(data.previous?.from, '')} → ${text(data.previous?.to, '')}`
    const tg = k.target
    return (
        <div className="flex flex-col gap-4" data-testid="homedash-summary">
            <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
                <KpiCard
                    lead
                    testId="homedash-kpi-collected"
                    label={t('p10.homedash.kpi.collected')}
                    value={moneyText(k.collected?.amount, 0)}
                    unit={<Riyal />}
                    trend={k.collected?.trend ?? null}
                    spark={k.collected?.spark}
                    footer={t('p10.homedash.kpi.payments', { n: formatCount(num(k.collected?.count)) })}
                    to={ctx.links.collected(from, to)}
                    details={
                        <>
                            <DetailLine label={t('p10.homedash.kpi.previous', { range: prevLabel })} value={moneyText(k.collected?.previous_amount)} />
                            <DetailLine label={t('p10.homedash.kpi.pending')} value={moneyText(k.pending?.amount)} />
                            <div className="mt-1 text-[11.5px] text-gray-500">{t('p10.homedash.kpi.collectedRule')}</div>
                        </>
                    }
                />
                <KpiCard
                    testId="homedash-kpi-promises"
                    label={t('p10.homedash.kpi.promises')}
                    value={formatCount(num(k.promises_due?.count))}
                    trend={k.promises_due?.trend ?? null}
                    spark={k.promises_due?.spark}
                    footer={
                        <span>
                            {moneyText(k.promises_due?.amount, 0)} <Riyal />
                        </span>
                    }
                    to={ctx.links.promises(from, to)}
                    details={
                        <>
                            {(['fulfilled', 'overdue', 'open'] as const).map((s) => (
                                <DetailLine
                                    key={s}
                                    label={t(`p10.homedash.state.${s}`)}
                                    value={`${formatCount(num(k.promises_due?.by_state?.[s]?.count))} · ${moneyText(k.promises_due?.by_state?.[s]?.amount, 0)}`}
                                />
                            ))}
                            <DetailLine label={t('p10.homedash.kpi.previous', { range: prevLabel })} value={formatCount(num(k.promises_due?.previous_count))} />
                        </>
                    }
                />
                <KpiCard
                    testId="homedash-kpi-notes"
                    label={t('p10.homedash.kpi.notes')}
                    value={formatCount(num(k.notes?.count))}
                    trend={k.notes?.trend ?? null}
                    spark={k.notes?.spark}
                    footer={t('p10.homedash.kpi.noteClients', { n: formatCount(num(k.notes?.clients)) })}
                    to={ctx.links.notes()}
                    details={
                        <>
                            <DetailLine label={t('p10.homedash.kpi.previous', { range: prevLabel })} value={formatCount(num(k.notes?.previous_count))} />
                            <div className="mt-1 text-[11.5px] text-gray-500">{t('p10.homedash.kpi.notesRule')}</div>
                        </>
                    }
                />
                <KpiCard
                    testId="homedash-kpi-cases"
                    label={ctx.admin ? t('p10.homedash.kpi.openCases') : t('p10.homedash.kpi.myCases')}
                    value={formatCount(num(k.cases?.open))}
                    footer={
                        <span>
                            {moneyText(k.cases?.debt, 0)} <Riyal />
                        </span>
                    }
                    to={ctx.links.clients()}
                    details={
                        <>
                            <DetailLine label={t('p10.homedash.kpi.debt')} value={moneyText(k.cases?.debt)} />
                            <DetailLine label={t('p10.homedash.kpi.notContacted7')} value={formatCount(num(k.cases?.not_contacted_7d))} />
                        </>
                    }
                />
                <KpiCard
                    testId="homedash-kpi-target"
                    label={t('p10.homedash.kpi.target')}
                    value={pct(tg?.percent)}
                    footer={
                        <span>
                            {moneyText(tg?.achieved, 0)} / {moneyText(tg?.target, 0)}
                        </span>
                    }
                    to={ctx.links.team()}
                    details={
                        <>
                            <DetailLine label={t('p10.homedash.target.expected')} value={moneyText(tg?.expected_to_date, 0)} />
                            <DetailLine label={t('p10.homedash.target.remaining')} value={moneyText(tg?.remaining, 0)} />
                            <DetailLine label={t('p10.homedash.target.collectors')} value={formatCount(num(tg?.collectors))} />
                        </>
                    }
                />
                <KpiCard
                    testId="homedash-kpi-pending"
                    label={t('p10.homedash.kpi.pending')}
                    value={formatCount(num(k.pending?.count))}
                    footer={
                        <span>
                            {moneyText(k.pending?.amount, 0)} <Riyal />
                        </span>
                    }
                    to={ctx.links.pending(from, to)}
                    details={<div className="text-[11.5px] text-gray-500">{t('p10.homedash.kpi.pendingRule')}</div>}
                />
            </div>
            <div className="grid gap-4 lg:grid-cols-2">
                <TargetGauge target={tg} link={ctx.links.team()} />
                {ctx.admin ? <ContractDonut data={data} ctx={ctx} /> : <PromiseStateDonut data={data} ctx={ctx} />}
            </div>
        </div>
    )
}

export default SummarySection
