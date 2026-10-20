/**
 * Answer rate (2026-10-20): the team's gauges — outgoing calls answered by the other party and
 * incoming rings the extension answered, each with its denominator — and one bar per user for
 * the chosen direction (users with no such call are left out: no rate without a base). A bar
 * opens that user's calls (`onOpenUser`).
 */
import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import Chart from '@/components/shared/Chart'
import { EmptyState } from '@/components/v2/States'
import { apexLineChartDefaultOption } from '@/configs/chart.config'
import { COLORS } from '@/constants/chart.constant'
import { formatCount } from '@/utils/v2/format'
import { Segmented } from '@/views/phase1/followUps/parts'
import type { ApexOptions } from 'apexcharts'
import type { MonitorStats, MonitorUserStats } from '@/services/v2'
import { Card } from '../../parts'
import { CLICKABLE, agentLabel, inboundRung, outcomeColor, pct, useChartTheme, withDefaults } from './kit'
import { TopNControl, take } from './UsersChart'
import type { TopN } from './UsersChart'

type Way = 'outbound' | 'inbound'

const base = (u: MonitorUserStats, way: Way) => (way === 'outbound' ? u.outbound : inboundRung(u))
const hits = (u: MonitorUserStats, way: Way) => (way === 'outbound' ? u.outbound_answered : u.inbound_answered)

const AnswerRateChart = ({ data, onOpenUser }: { data: MonitorStats; onOpenUser: (u: MonitorUserStats) => void }) => {
    const { t } = useTranslation()
    const { dark, direction } = useChartTheme()
    const [way, setWay] = useState<Way>('outbound')
    const [top, setTop] = useState<TopN>('10')
    const tot = data.totals
    const outRate = pct(tot.outbound_answered, tot.outbound)
    const inRate = pct(tot.inbound_answered, tot.inbound_answered + tot.inbound_missed)
    const rows = useMemo(
        () =>
            take(
                data.users
                    .filter((u) => base(u, way) > 0)
                    .map((u) => ({ u, rate: pct(hits(u, way), base(u, way)) as number }))
                    .sort((a, b) => b.rate - a.rate || base(b.u, way) - base(a.u, way)),
                top
            ),
        [data.users, way, top]
    )
    const gauges = [
        { key: 'out', label: t('p10.moncharts.rate.outbound') as string, value: outRate, part: tot.outbound_answered, whole: tot.outbound },
        { key: 'in', label: t('p10.moncharts.rate.inbound') as string, value: inRate, part: tot.inbound_answered, whole: tot.inbound_answered + tot.inbound_missed },
    ].filter((g) => g.value !== null)
    const radial: ApexOptions = {
        ...apexLineChartDefaultOption,
        colors: gauges.map((g) => (g.key === 'out' ? outcomeColor('answered', dark) : COLORS[2])),
        labels: gauges.map((g) => g.label),
        legend: { show: false },
        plotOptions: {
            radialBar: {
                hollow: { size: '38%' },
                track: { background: dark ? '#1B2049' : '#E7EEF2' },
                dataLabels: {
                    name: { fontSize: '12px' },
                    value: { fontSize: '18px', fontWeight: 800, formatter: (v: number) => `${v}%` },
                    total: {
                        show: gauges.length > 1,
                        label: t('p10.moncharts.rate.outShort') as string,
                        formatter: () => (outRate === null ? '—' : `${outRate}%`),
                    },
                },
            },
        },
        tooltip: { enabled: false },
        stroke: { lineCap: 'round' },
    }
    const options = withDefaults({
        colors: [way === 'outbound' ? outcomeColor('answered', dark) : COLORS[2]],
        chart: {
            events: {
                dataPointSelection: (_e: unknown, _ctx: unknown, cfg: { dataPointIndex: number }) => {
                    const r = rows[cfg.dataPointIndex]
                    if (r) onOpenUser(r.u)
                },
            },
        },
        plotOptions: { bar: { horizontal: true, barHeight: '65%', borderRadius: 2, borderRadiusApplication: 'end' } },
        stroke: { show: false },
        states: { active: { filter: { type: 'none' } } },
        dataLabels: { enabled: true, formatter: (v: number) => `${v}%`, style: { fontSize: '11px', fontWeight: 700 }, offsetX: 0 },
        xaxis: { categories: rows.map((r) => agentLabel(r.u)), min: 0, max: 100, tickAmount: 5, labels: { formatter: (v: string) => `${Math.round(Number(v))}%` } },
        yaxis: { labels: { maxWidth: 200 } },
        grid: { xaxis: { lines: { show: true } }, yaxis: { lines: { show: false } } },
        legend: { show: false },
        tooltip: {
            y: {
                title: { formatter: () => '' },
                formatter: (_v: number, o?: { dataPointIndex: number }) => {
                    const r = o ? rows[o.dataPointIndex] : undefined
                    return r ? t('p10.moncharts.rate.tip', { rate: r.rate, part: formatCount(hits(r.u, way)), whole: formatCount(base(r.u, way)) }) : ''
                },
            },
        },
    })
    return (
        <Card
            title={t('p10.moncharts.rate.title')}
            testId="p10-monchart-rate"
            actions={
                <div className="flex flex-wrap items-center gap-2">
                    <Segmented<Way>
                        value={way}
                        onChange={setWay}
                        label={t('p10.moncharts.rate.title') as string}
                        testId="p10-monchart-rate-way"
                        options={[
                            { value: 'outbound', label: t('p5.monitor.outbound') },
                            { value: 'inbound', label: t('p10.moncharts.rate.incoming') },
                        ]}
                    />
                    <TopNControl value={top} onChange={setTop} />
                </div>
            }
        >
            {!gauges.length ? (
                <EmptyState title={t('p5.monitor.noStats')} />
            ) : (
                <div className="flex flex-col gap-3 lg:flex-row">
                    <div className="lg:w-[230px] lg:shrink-0" data-testid="p10-monchart-rate-team">
                        <Chart key={gauges.length} type="radialBar" height={230} series={gauges.map((g) => g.value as number)} direction={direction} customOptions={radial} />
                        <ul className="mt-1 flex flex-col gap-1 text-[12.5px] text-gray-700 dark:text-night-body">
                            {gauges.map((g) => (
                                <li key={g.key} className="flex justify-between gap-2">
                                    <span>{g.label}</span>
                                    <span className="tabular-nums" dir="ltr">
                                        {`${g.value}% (${formatCount(g.part)} / ${formatCount(g.whole)})`}
                                    </span>
                                </li>
                            ))}
                        </ul>
                    </div>
                    <div className="min-w-0 flex-1">
                        {rows.length ? (
                            <div className={CLICKABLE}>
                                <Chart
                                    key={`${way}-${top}-${rows.length}`}
                                    type="bar"
                                    height={Math.max(230, rows.length * 30 + 60)}
                                    series={[{ name: t('p10.moncharts.rate.title') as string, data: rows.map((r) => r.rate) }]}
                                    direction={direction}
                                    customOptions={options}
                                />
                            </div>
                        ) : (
                            <EmptyState title={t('p10.moncharts.rate.none')} />
                        )}
                    </div>
                </div>
            )}
            <p className="mt-2 text-[12px] text-gray-600 dark:text-night-body">{t('p10.moncharts.rate.note')}</p>
        </Card>
    )
}

export default AnswerRateChart
