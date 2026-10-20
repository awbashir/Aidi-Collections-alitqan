/**
 * GET /v2/dashboards/home/charts: collections this month by day (bars) with the cumulative
 * line against the target pace; promises by due day (7 days back, 14 ahead) kept / broken /
 * open; today's notes by hour and main status; notes by weekday x hour over 28 days.
 * Bars click through to the matching filtered list when the role may open it.
 */
import { useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'
import Chart from '@/components/shared/Chart'
import { useConfig } from '@/components/ui/ConfigProvider'
import { ErrorState, SkeletonLines } from '@/components/v2/States'
import { COLORS, COLOR_1, COLOR_2, COLOR_3, COLOR_8, COLOR_HIGHLIGHT, TARGET_LINE } from '@/constants/chart.constant'
import { homeDashboardApi } from '@/services/v2/homeDashboardApi'
import type { HomeCharts } from '@/services/v2/homeDashboardApi'
import { useV2Query } from '@/utils/v2/hooks'
import { formatCount } from '@/utils/v2/format'
import type { HomeCtx } from './context'
import { CardBody, HomeCard } from './parts'
import { arr, compact, grouped, num, text } from './safe'

const SUCCESS = '#128A5C'
const DANGER = '#B3261E'
const FIRST_HOUR = 7
const LAST_HOUR = 22

const isWorkDay = (d: string) => {
    const day = new Date(`${d}T00:00:00Z`).getUTCDay()
    return Number.isFinite(day) && day !== 5 && day !== 6
}

/** Hours to draw: 07..22, widened to any hour that has data. */
const hourRange = (hours: { hour?: number; total?: number }[]) => {
    const busy = hours.filter((h) => num(h?.total) > 0).map((h) => num(h?.hour))
    const lo = Math.min(FIRST_HOUR, ...busy)
    const hi = Math.max(LAST_HOUR, ...busy)
    return Array.from({ length: hi - lo + 1 }, (_, i) => lo + i)
}

const hh = (h: number) => `${String(h).padStart(2, '0')}:00`

const CollectionsChart = ({ data, ctx }: { data: HomeCharts; ctx: HomeCtx }) => {
    const { t } = useTranslation()
    const { direction } = useConfig()
    const navigate = useNavigate()
    const days = arr(data.collections)
    const target = num(data.target?.target, NaN)
    const workTotal = num(data.target?.work_days)
    const series = useMemo(() => {
        const daily = days.map((d) => num(d?.amount))
        const cum = days.map((d) => num(d?.cumulative))
        const out: { name: string; type: string; data: number[] }[] = [
            { name: t('p10.homedash.collections.daily') as string, type: 'column', data: daily },
            { name: t('p10.homedash.collections.cumulative') as string, type: 'line', data: cum },
        ]
        if (Number.isFinite(target) && target > 0 && workTotal > 0) {
            let passed = 0
            out.push({
                name: t('p10.homedash.collections.pace') as string,
                type: 'line',
                data: days.map((d) => {
                    if (isWorkDay(text(d?.date, ''))) passed += 1
                    return Math.round((target * passed) / workTotal)
                }),
            })
        }
        return out
    }, [days, target, workTotal, t])
    const total = num(days[days.length - 1]?.cumulative)
    return (
        <HomeCard
            title={t('p10.homedash.collections.title')}
            subtitle={
                Number.isFinite(target)
                    ? t('p10.homedash.collections.subtitleTarget', { total: grouped(total), target: grouped(target) })
                    : t('p10.homedash.collections.subtitle', { total: grouped(total) })
            }
            testId="homedash-collections"
            resetKeys={[data]}
        >
            <Chart
                type="line"
                direction={direction}
                height={300}
                series={series}
                xAxis={days.map((d) => text(d?.date, '').slice(8))}
                customOptions={{
                    colors: [COLOR_3, COLOR_1, TARGET_LINE],
                    stroke: { width: [0, 3, 2], dashArray: [0, 0, 6], curve: 'smooth' },
                    plotOptions: { bar: { columnWidth: '55%', borderRadius: 3 } },
                    legend: { position: 'top' },
                    markers: { size: 0 },
                    yaxis: [
                        { seriesName: series[0]?.name, labels: { formatter: compact } },
                        { seriesName: series[1]?.name, opposite: true, labels: { formatter: compact } },
                        ...(series.length > 2 ? [{ seriesName: series[1]?.name, show: false }] : []),
                    ],
                    tooltip: { shared: true, intersect: false, y: { formatter: (v: number) => grouped(v, 2) } },
                    chart: {
                        events: {
                            dataPointSelection: (_e: unknown, _c: unknown, o: { dataPointIndex: number }) => {
                                const d = days[o?.dataPointIndex]?.date
                                const to = d ? ctx.links.collected(d, d) : undefined
                                if (to) navigate(to)
                            },
                        },
                    },
                }}
            />
        </HomeCard>
    )
}

const PromisesChart = ({ data, ctx }: { data: HomeCharts; ctx: HomeCtx }) => {
    const { t } = useTranslation()
    const { direction } = useConfig()
    const navigate = useNavigate()
    const days = arr(data.promises?.days)
    const states = ['fulfilled', 'overdue', 'open'] as const
    const today = text(data.today, '')
    const totals = states.map((s) => days.reduce((n, d) => n + num(d?.[s]?.count), 0))
    const amounts = states.map((s) => days.reduce((n, d) => n + num(d?.[s]?.amount), 0))
    const labels = days.map((d) => text(d?.date, '').slice(5))
    return (
        <HomeCard
            title={t('p10.homedash.promises.title')}
            subtitle={t('p10.homedash.promises.subtitle')}
            testId="homedash-promises"
            resetKeys={[data]}
        >
            <CardBody loading={false} error={null} empty={!totals.some(Boolean)} emptyTitle={t('p10.homedash.noPromises')}>
                {() => (
                    <>
                        <div className="mb-2 flex flex-wrap gap-3 text-[12px]">
                            {states.map((s, i) => (
                                <span key={s} className="inline-flex items-center gap-1">
                                    <span className={`tag ${s === 'fulfilled' ? 'tag-success' : s === 'overdue' ? 'tag-danger' : 'tag-info'} !h-[20px]`}>
                                        {t(`p10.homedash.state.${s}`)}
                                    </span>
                                    <span className="font-extrabold tabular-nums" dir="ltr">
                                        {formatCount(totals[i])} · {grouped(amounts[i])}
                                    </span>
                                </span>
                            ))}
                        </div>
                        <Chart
                            type="bar"
                            direction={direction}
                            height={280}
                            series={states.map((s) => ({ name: t(`p10.homedash.state.${s}`) as string, data: days.map((d) => num(d?.[s]?.count)) }))}
                            xAxis={labels}
                            customOptions={{
                                colors: [SUCCESS, DANGER, COLOR_3],
                                chart: {
                                    stacked: true,
                                    events: {
                                        dataPointSelection: (_e: unknown, _c: unknown, o: { dataPointIndex: number; seriesIndex: number }) => {
                                            const d = days[o?.dataPointIndex]?.date
                                            const to = d ? ctx.links.promises(d, d, states[o?.seriesIndex]) : undefined
                                            if (to) navigate(to)
                                        },
                                    },
                                },
                                plotOptions: { bar: { columnWidth: '60%', borderRadius: 2 } },
                                legend: { position: 'top' },
                                annotations: today
                                    ? { xaxis: [{ x: today.slice(5), borderColor: COLOR_HIGHLIGHT, label: { text: t('p10.homedash.today') as string, style: { background: COLOR_HIGHLIGHT, color: '#fff' } } }] }
                                    : undefined,
                            }}
                        />
                    </>
                )}
            </CardBody>
        </HomeCard>
    )
}

const NotesTodayChart = ({ data, ctx }: { data: HomeCharts; ctx: HomeCtx }) => {
    const { t } = useTranslation()
    const { direction } = useConfig()
    const navigate = useNavigate()
    const nt = data.notes_today
    const statuses = arr(nt?.statuses)
    const hours = arr(nt?.hours)
    const range = hourRange(hours)
    const byHour = new Map(hours.map((h) => [num(h?.hour), h]))
    const series = [
        ...statuses.map((s) => ({
            name: text(s?.name, t('p10.homedash.noStatus') as string),
            data: range.map((h) => num(byHour.get(h)?.by_status?.[s?.key ?? ''])),
        })),
        ...(num(nt?.other) > 0 ? [{ name: t('p10.homedash.other') as string, data: range.map((h) => num(byHour.get(h)?.other)) }] : []),
    ]
    const colors = [...COLORS.slice(0, statuses.length), ...(num(nt?.other) > 0 ? [COLOR_8] : [])]
    const link = ctx.links.notes()
    return (
        <HomeCard
            title={t('p10.homedash.notesToday.title')}
            subtitle={t('p10.homedash.notesToday.subtitle', { n: formatCount(num(nt?.total)) })}
            testId="homedash-notes-today"
            resetKeys={[data]}
        >
            <CardBody loading={false} error={null} empty={!num(nt?.total)} emptyTitle={t('p10.homedash.noNotes')}>
                {() => (
                    <Chart
                        type="bar"
                        direction={direction}
                        height={280}
                        series={series}
                        xAxis={range.map(hh)}
                        customOptions={{
                            colors,
                            chart: {
                                stacked: true,
                                events: { dataPointSelection: () => link && navigate(link) },
                            },
                            plotOptions: { bar: { columnWidth: '60%', borderRadius: 2 } },
                            legend: { position: 'top' },
                        }}
                    />
                )}
            </CardBody>
        </HomeCard>
    )
}

const NotesHeatmap = ({ data }: { data: HomeCharts }) => {
    const { t, i18n } = useTranslation()
    const rows = arr(data.notes_heatmap?.rows)
    const fmt = useMemo(() => {
        try {
            return new Intl.DateTimeFormat(i18n.language || 'ar', { weekday: 'short', timeZone: 'UTC' })
        } catch {
            return null
        }
    }, [i18n.language])
    const dayName = (i: number) => (fmt ? fmt.format(new Date(Date.UTC(2023, 0, 1 + i))) : String(i)) /* 2023-01-01 was a Sunday */
    const hours = Array.from({ length: LAST_HOUR - FIRST_HOUR + 1 }, (_, i) => FIRST_HOUR + i)
    const total = rows.reduce((n, r) => n + arr(r?.hours).reduce((a, b) => a + num(b), 0), 0)
    /* Apex draws the last series on top: reverse so Sunday leads. */
    const series = [...rows]
        .sort((a, b) => num(a?.weekday) - num(b?.weekday))
        .map((r) => ({ name: dayName(num(r?.weekday)), data: hours.map((h) => ({ x: hh(h), y: num(arr(r?.hours)[h]) })) }))
        .reverse()
    return (
        <HomeCard
            title={t('p10.homedash.heatmap.title')}
            subtitle={t('p10.homedash.heatmap.subtitle', { from: text(data.notes_heatmap?.from, ''), to: text(data.notes_heatmap?.to, '') })}
            testId="homedash-heatmap"
            resetKeys={[data]}
        >
            <CardBody loading={false} error={null} empty={!total} emptyTitle={t('p10.homedash.noNotes')}>
                {() => (
                    <Chart
                        type="heatmap"
                        height={280}
                        series={series}
                        customOptions={{
                            colors: [COLOR_2],
                            dataLabels: { enabled: false },
                            legend: { show: false },
                            plotOptions: { heatmap: { radius: 2, shadeIntensity: 0.6 } },
                            xaxis: { type: 'category', categories: hours.map(hh), labels: { rotate: -45 } },
                            tooltip: { y: { formatter: (v: number) => formatCount(num(v)) } },
                        }}
                    />
                )}
            </CardBody>
        </HomeCard>
    )
}

const ChartsSection = ({ ctx }: { ctx: HomeCtx }) => {
    const { t } = useTranslation()
    const q = useV2Query((signal) => homeDashboardApi.charts(ctx.query, signal), [ctx.query.contract_id, ctx.query.user_id])
    const data = q.data
    if (q.error && !data) return <ErrorState error={q.error} onRetry={q.reload} />
    if (!data) return <SkeletonLines rows={8} />
    return (
        <div className="grid gap-4 xl:grid-cols-2" data-testid="homedash-charts">
            <CollectionsChart data={data} ctx={ctx} />
            <PromisesChart data={data} ctx={ctx} />
            <NotesTodayChart data={data} ctx={ctx} />
            <NotesHeatmap data={data} />
            <p className="text-[12px] text-gray-600 xl:col-span-2">
                {t('p10.homedash.chartsNote', { month: text(data.month, '') })}
            </p>
        </div>
    )
}

export default ChartsSection
