/**
 * Calls over time (2026-10-20): stacked columns per hour or day, by outcome (every call),
 * direction (every call) or user (handled calls, the top 8 + "Others", as the former chart).
 * Drag on the plot (or the toolbar) to zoom, the legend toggles a series, and a day's column
 * opens that day by hour (`onDrillDay`).
 */
import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import Chart from '@/components/shared/Chart'
import { EmptyState } from '@/components/v2/States'
import { COLORS } from '@/constants/chart.constant'
import { Segmented } from '@/views/phase1/followUps/parts'
import type { MonitorStats } from '@/services/v2'
import { Card } from '../../parts'
import { CLICKABLE, OUTCOMES, WAYS, WAY_COLORS, agentLabel, bucketTs, outcomeColor, tsLabel, useChartTheme, withDefaults } from './kit'

type View = 'outcome' | 'direction' | 'user'
const TOP_USERS = 8

const TimelineChart = ({ data, onDrillDay }: { data: MonitorStats; onDrillDay: (day: string) => void }) => {
    const { t } = useTranslation()
    const { dark, direction } = useChartTheme()
    const [view, setView] = useState<View>('outcome')
    const hour = data.granularity === 'hour'
    const oneDay = data.from === data.to

    const { series, colors, empty } = useMemo(() => {
        const xs = data.series.map((s) => bucketTs(s.bucket))
        const pts = (ys: number[]) => ys.map((y, i) => ({ x: xs[i], y }))
        if (view === 'outcome') {
            const series = OUTCOMES.map((o) => ({ name: t(`p10.moncharts.outcome.${o}`) as string, data: pts(data.series.map((s) => s.by_outcome?.[o] || 0)) }))
            return { series, colors: OUTCOMES.map((o) => outcomeColor(o, dark)), empty: !data.series.some((s) => OUTCOMES.some((o) => s.by_outcome?.[o])) }
        }
        if (view === 'direction') {
            const series = WAYS.map((w) => ({ name: t(`p10.moncharts.way.${w}`) as string, data: pts(data.series.map((s) => s.by_direction?.[w] || 0)) }))
            return { series, colors: WAY_COLORS, empty: !data.series.some((s) => WAYS.some((w) => s.by_direction?.[w])) }
        }
        const users = data.users.slice(0, TOP_USERS)
        const top = new Set(users.map((u) => u.extension))
        const series = users.map((u) => ({ name: agentLabel(u), data: pts(data.series.map((s) => s.by_extension[u.extension] || 0)) }))
        const others = data.series.map((s) => Object.entries(s.by_extension).reduce((n, [e, v]) => (top.has(e) ? n : n + v), 0))
        if (others.some((v) => v > 0)) series.push({ name: t('p5.monitor.others') as string, data: pts(others) })
        return { series, colors: [...COLORS.slice(0, users.length), COLORS[7]], empty: !data.series.some((s) => s.total) }
    }, [data, view, t, dark])

    const options = withDefaults({
        colors,
        chart: {
            stacked: true,
            zoom: { enabled: true, type: 'x', autoScaleYaxis: true },
            toolbar: { show: true, tools: { download: false, selection: false, zoom: true, zoomin: true, zoomout: true, pan: true, reset: true } },
            events: {
                dataPointSelection: (_e: unknown, _ctx: unknown, cfg: { dataPointIndex: number }) => {
                    const b = data.series[cfg.dataPointIndex]
                    if (b && !hour) onDrillDay(b.bucket)
                },
            },
        },
        plotOptions: { bar: { columnWidth: '70%', borderRadius: 2, borderRadiusApplication: 'end', borderRadiusWhenStacked: 'last' } },
        stroke: { show: true, width: 1, colors: ['transparent'] },
        states: { active: { filter: { type: 'none' } } },
        xaxis: {
            type: 'datetime',
            labels: { datetimeUTC: true, formatter: (_v: string, ts?: number) => (ts === undefined ? '' : tsLabel(ts, hour, !oneDay)) },
            axisBorder: { show: true },
            axisTicks: { show: false },
        },
        yaxis: { min: 0, forceNiceScale: true, labels: { formatter: (v: number) => String(Math.round(v)) } },
        tooltip: {
            shared: true,
            intersect: false,
            x: { formatter: (ts: number) => tsLabel(ts, hour, true) + (hour ? '' : ` — ${t('p10.moncharts.timeline.clickDay')}`) },
            y: { formatter: (v: number) => String(v) },
        },
        legend: { position: 'top', horizontalAlign: 'left', onItemClick: { toggleDataSeries: true } },
    })

    return (
        <Card
            title={t(hour ? 'p10.moncharts.timeline.titleHour' : 'p10.moncharts.timeline.titleDay')}
            testId="p10-monchart-timeline"
            actions={
                <Segmented<View>
                    value={view}
                    onChange={setView}
                    label={t('p10.moncharts.timeline.view') as string}
                    testId="p10-monchart-timeline-view"
                    options={[
                        { value: 'outcome', label: t('p10.moncharts.timeline.byOutcome') },
                        { value: 'direction', label: t('p10.moncharts.timeline.byDirection') },
                        { value: 'user', label: t('p10.moncharts.timeline.byUser') },
                    ]}
                />
            }
        >
            <p className="mb-2 text-[12.5px] text-gray-600 dark:text-night-body">
                {t(view === 'user' ? 'p10.moncharts.timeline.noteHandled' : 'p10.moncharts.timeline.noteEvery')} {t('p10.moncharts.timeline.zoomHint')}
                {!hour && ` ${t('p10.moncharts.timeline.drillHint')}`}
            </p>
            {empty ? (
                <EmptyState title={t('p5.monitor.noStats')} />
            ) : (
                <div className={hour ? undefined : CLICKABLE}>
                    <Chart key={`${view}-${data.granularity}`} type="bar" height={330} series={series} direction={direction} customOptions={options} />
                </div>
            )}
        </Card>
    )
}

export default TimelineChart
