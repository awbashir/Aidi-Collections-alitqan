/**
 * Busy hours (2026-10-20): handled calls by weekday (Sunday first, only the weekdays the period
 * contains) x hour of day (Asia/Riyadh), as a total or as an average per such day. Useful for a
 * period of several days; a single day is the hourly column chart already.
 */
import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import Chart from '@/components/shared/Chart'
import { EmptyState } from '@/components/v2/States'
import { RAMP_SEQUENTIAL } from '@/constants/chart.constant'
import { formatCount } from '@/utils/v2/format'
import { Segmented } from '@/views/phase1/followUps/parts'
import type { MonitorStats } from '@/services/v2'
import { Card } from '../../parts'
import { useChartTheme, weekdayNames, withDefaults } from './kit'

type Mode = 'total' | 'average'
const HOURS = Array.from({ length: 24 }, (_, h) => `${String(h).padStart(2, '0')}:00`)

const HeatmapChart = ({ data }: { data: MonitorStats }) => {
    const { t, i18n } = useTranslation()
    const { dark, direction } = useChartTheme()
    const [mode, setMode] = useState<Mode>('total')
    const names = useMemo(() => weekdayNames(i18n.language), [i18n.language])
    const rows = (data.heatmap || []).filter((r) => r.days > 0)
    const value = (n: number, days: number) => (mode === 'total' ? n : Math.round((n / days) * 10) / 10)
    /* Apex draws the first series at the bottom: reversed so Sunday is the top row. */
    const series = rows
        .map((r) => ({ name: names[r.weekday], data: r.hours.map((n, h) => ({ x: HOURS[h], y: value(n, r.days) })) }))
        .reverse()
    const max = Math.max(0, ...series.flatMap((s) => s.data.map((p) => p.y)))
    const ramp = RAMP_SEQUENTIAL.slice(1)
    const ranges = [
        { from: 0, to: 0, color: dark ? '#1B2049' : '#F2F6F8', name: t('p10.moncharts.heat.none') as string },
        ...ramp.map((color, i) => ({
            from: i === 0 ? 0.01 : (max * i) / ramp.length + 0.001,
            to: (max * (i + 1)) / ramp.length,
            color,
        })),
    ]
    const options = withDefaults({
        chart: { toolbar: { show: false } },
        dataLabels: { enabled: false },
        stroke: { width: 2, colors: [dark ? '#0C1033' : '#FFFFFF'] },
        plotOptions: { heatmap: { radius: 2, enableShades: false, useFillColorAsStroke: false, colorScale: { ranges: max > 0 ? ranges : ranges.slice(0, 1) } } },
        xaxis: { categories: HOURS, labels: { rotate: -45, hideOverlappingLabels: true } },
        legend: { show: false },
        tooltip: {
            y: {
                formatter: (v: number, o?: { seriesIndex: number; dataPointIndex: number }) => {
                    const row = o ? rows[rows.length - 1 - o.seriesIndex] : undefined
                    const total = row && o ? row.hours[o.dataPointIndex] : v
                    return mode === 'total' || !row
                        ? (t('p10.moncharts.heat.tipTotal', { n: formatCount(total) }) as string)
                        : (t('p10.moncharts.heat.tipAvg', { avg: v, n: formatCount(total), days: row.days }) as string)
                },
            },
        },
    })
    return (
        <Card
            title={t('p10.moncharts.heat.title')}
            testId="p10-monchart-heatmap"
            actions={
                <Segmented<Mode>
                    value={mode}
                    onChange={setMode}
                    label={t('p10.moncharts.heat.title') as string}
                    options={[
                        { value: 'total', label: t('p10.moncharts.heat.total') },
                        { value: 'average', label: t('p10.moncharts.heat.average') },
                    ]}
                />
            }
        >
            <p className="mb-2 text-[12.5px] text-gray-600 dark:text-night-body">{t('p10.moncharts.heat.sub')}</p>
            {data.from === data.to ? (
                <EmptyState title={t('p10.moncharts.heat.multiDay')} />
            ) : max > 0 ? (
                <Chart key={`${mode}-${rows.length}`} type="heatmap" height={Math.max(220, rows.length * 34 + 70)} series={series} direction={direction} customOptions={options} />
            ) : (
                <EmptyState title={t('p5.monitor.noStats')} />
            )}
        </Card>
    )
}

export default HeatmapChart
