/**
 * Average ring and talk time per user (2026-10-20): ring = the user's ring seconds over every
 * call (missed rings included), talk = talk seconds over the user's answered calls (users with no
 * answered call show no talk bar: no average without a base). Sorted by the chosen measure, top
 * 10 / 20 / all; a bar opens that user's calls (`onOpenUser`).
 */
import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import Chart from '@/components/shared/Chart'
import { EmptyState } from '@/components/v2/States'
import { COLORS } from '@/constants/chart.constant'
import { formatCount } from '@/utils/v2/format'
import { Segmented } from '@/views/phase1/followUps/parts'
import type { MonitorStats, MonitorUserStats } from '@/services/v2'
import { Card } from '../../parts'
import { CLICKABLE, agentLabel, clock, useChartTheme, withDefaults } from './kit'
import { TopNControl, take } from './UsersChart'
import type { TopN } from './UsersChart'

type By = 'talk' | 'ring'

const avgTalk = (u: MonitorUserStats) => (u.answered ? Math.round(u.talk_seconds / u.answered) : null)
const avgRing = (u: MonitorUserStats) => (u.calls ? Math.round(u.ring_seconds / u.calls) : null)

const AvgTimesChart = ({ data, onOpenUser }: { data: MonitorStats; onOpenUser: (u: MonitorUserStats) => void }) => {
    const { t } = useTranslation()
    const { direction } = useChartTheme()
    const [by, setBy] = useState<By>('talk')
    const [top, setTop] = useState<TopN>('10')
    const rows = useMemo(() => {
        const key = by === 'talk' ? avgTalk : avgRing
        return take(
            data.users.filter((u) => u.calls > 0).sort((a, b) => (key(b) ?? -1) - (key(a) ?? -1) || b.handled - a.handled),
            top
        )
    }, [data.users, by, top])
    const tot = data.totals
    const teamTalk = tot.answered ? tot.talk_seconds / tot.answered : null
    const teamRing = tot.calls ? tot.ring_seconds / tot.calls : null
    const options = withDefaults({
        colors: [COLORS[1], COLORS[4]],
        chart: {
            events: {
                dataPointSelection: (_e: unknown, _ctx: unknown, cfg: { dataPointIndex: number }) => {
                    const u = rows[cfg.dataPointIndex]
                    if (u) onOpenUser(u)
                },
            },
        },
        plotOptions: { bar: { horizontal: true, barHeight: '75%', borderRadius: 2, borderRadiusApplication: 'end' } },
        stroke: { show: true, width: 2, colors: ['transparent'] },
        states: { active: { filter: { type: 'none' } } },
        xaxis: {
            categories: rows.map(agentLabel),
            labels: { formatter: (v: string) => clock(Number(v)) },
            title: { text: t('p10.moncharts.avg.axis') as string },
        },
        yaxis: { labels: { maxWidth: 200 } },
        grid: { xaxis: { lines: { show: true } }, yaxis: { lines: { show: false } } },
        tooltip: {
            shared: true,
            intersect: false,
            y: { formatter: (v: number | null) => (v === null || v === undefined ? '—' : clock(v)) },
            x: {
                formatter: (_v: number, o?: { dataPointIndex: number }) => {
                    const u = o ? rows[o.dataPointIndex] : undefined
                    return u ? `${agentLabel(u)} — ${t('p10.moncharts.avg.tip', { calls: formatCount(u.calls), answered: formatCount(u.answered) })}` : ''
                },
            },
        },
        legend: { position: 'top', horizontalAlign: 'left', onItemClick: { toggleDataSeries: true } },
    })
    const series = [
        { name: t('p10.moncharts.avg.talk') as string, data: rows.map(avgTalk) },
        { name: t('p10.moncharts.avg.ring') as string, data: rows.map(avgRing) },
    ]
    return (
        <Card
            title={t('p10.moncharts.avg.title')}
            testId="p10-monchart-avg"
            actions={
                <div className="flex flex-wrap items-center gap-2">
                    <Segmented<By>
                        value={by}
                        onChange={setBy}
                        label={t('p10.moncharts.sortBy') as string}
                        options={[
                            { value: 'talk', label: t('p10.moncharts.avg.byTalk') },
                            { value: 'ring', label: t('p10.moncharts.avg.byRing') },
                        ]}
                    />
                    <TopNControl value={top} onChange={setTop} />
                </div>
            }
        >
            <p className="mb-2 text-[12.5px] text-gray-600 dark:text-night-body">
                {t('p10.moncharts.avg.sub', { talk: teamTalk === null ? '—' : clock(teamTalk), ring: teamRing === null ? '—' : clock(teamRing) })}
            </p>
            {rows.length ? (
                <div className={CLICKABLE}>
                    <Chart key={`${by}-${top}-${rows.length}`} type="bar" height={Math.max(260, rows.length * 44 + 80)} series={series} direction={direction} customOptions={options} />
                </div>
            ) : (
                <EmptyState title={t('p5.monitor.noStats')} />
            )}
        </Card>
    )
}

export default AvgTimesChart
