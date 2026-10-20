/**
 * Talk-time distribution (2026-10-20): the period's ANSWERED calls by talk time — up to 1 min,
 * 1–2, 2–3, 3–5, 5–10 and over 10 minutes (the API's `talk_distribution`; each bucket is
 * "longer than its lower bound, up to its upper bound", so the buckets after the first add up to
 * the "longer than 1 minute" tile). Each column shows its count and its share of answered calls.
 */
import { useTranslation } from 'react-i18next'
import Chart from '@/components/shared/Chart'
import { EmptyState } from '@/components/v2/States'
import { COLORS } from '@/constants/chart.constant'
import { formatCount } from '@/utils/v2/format'
import type { MonitorStats } from '@/services/v2'
import { Card } from '../../parts'
import { clock, pct, useChartTheme, withDefaults } from './kit'

const TalkTimeChart = ({ data }: { data: MonitorStats }) => {
    const { t } = useTranslation()
    const { direction } = useChartTheme()
    const dist = data.talk_distribution || []
    const answered = dist.reduce((n, b) => n + b.count, 0)
    const label = (b: { min: number; max: number | null }) =>
        b.max === null
            ? (t('p10.moncharts.talk.over', { m: b.min / 60 }) as string)
            : b.min === 0
              ? (t('p10.moncharts.talk.upTo', { m: b.max / 60 }) as string)
              : (t('p10.moncharts.talk.between', { a: b.min / 60, b: b.max / 60 }) as string)
    const avg = data.totals.answered ? data.totals.talk_seconds / data.totals.answered : null
    const options = withDefaults({
        colors: [COLORS[1]],
        plotOptions: { bar: { columnWidth: '60%', borderRadius: 3, borderRadiusApplication: 'end', dataLabels: { position: 'top' } } },
        dataLabels: { enabled: true, offsetY: -18, style: { fontSize: '11px', fontWeight: 700, colors: ['#6B7482'] }, formatter: (v: number) => formatCount(v) },
        xaxis: { categories: dist.map(label) },
        yaxis: { min: 0, forceNiceScale: true, labels: { formatter: (v: number) => String(Math.round(v)) }, title: { text: t('p10.moncharts.talk.axis') as string } },
        legend: { show: false },
        tooltip: {
            y: {
                title: { formatter: () => '' },
                formatter: (v: number) => t('p10.moncharts.talk.tip', { n: formatCount(v), share: pct(v, answered) ?? 0 }) as string,
            },
        },
    })
    return (
        <Card title={t('p10.moncharts.talk.title')} testId="p10-monchart-talk">
            <p className="mb-2 text-[12.5px] text-gray-600 dark:text-night-body">
                {t('p10.moncharts.talk.sub', { n: formatCount(answered), avg: avg === null ? '—' : clock(avg) })}
            </p>
            {answered ? (
                <Chart type="bar" height={300} series={[{ name: t('p10.moncharts.talk.title') as string, data: dist.map((b) => b.count) }]} direction={direction} customOptions={options} />
            ) : (
                <EmptyState title={t('p10.moncharts.talk.none')} />
            )}
        </Card>
    )
}

export default TalkTimeChart
