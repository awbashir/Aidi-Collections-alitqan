/**
 * Calls by user (2026-10-20): one stacked horizontal bar per user — outgoing answered / not
 * answered, incoming answered / missed, internal — sorted by a chosen measure, top 10 / 20 / all.
 * The legend toggles a part; a bar opens that user's calls of the period (`onOpenUser`).
 */
import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import Chart from '@/components/shared/Chart'
import Select from '@/components/ui/Select'
import { EmptyState } from '@/components/v2/States'
import { Segmented } from '@/views/phase1/followUps/parts'
import type { MonitorUserStats, Option } from '@/services/v2'
import { Card } from '../../parts'
import { CLICKABLE, agentLabel, clock, pct, useChartTheme, userMixColors, withDefaults } from './kit'

export type SortKey = 'handled' | 'calls' | 'answer_rate' | 'talk' | 'missed'
export type TopN = '10' | '20' | 'all'

const rate = (u: MonitorUserStats) => pct(u.outbound_answered, u.outbound) ?? -1

export const sortUsers = (users: MonitorUserStats[], key: SortKey) =>
    [...users].sort((a, b) => {
        const v = (u: MonitorUserStats) =>
            key === 'handled' ? u.handled : key === 'calls' ? u.calls : key === 'answer_rate' ? rate(u) : key === 'talk' ? u.talk_seconds : u.inbound_missed
        return v(b) - v(a) || a.extension.localeCompare(b.extension)
    })

export const TopNControl = ({ value, onChange, testId }: { value: TopN; onChange: (v: TopN) => void; testId?: string }) => {
    const { t } = useTranslation()
    return (
        <Segmented<TopN>
            value={value}
            onChange={onChange}
            label={t('p10.moncharts.top') as string}
            testId={testId}
            options={[
                { value: '10', label: t('p10.moncharts.topN', { n: 10 }) },
                { value: '20', label: t('p10.moncharts.topN', { n: 20 }) },
                { value: 'all', label: t('p10.moncharts.all') },
            ]}
        />
    )
}

export const take = <T,>(rows: T[], n: TopN) => (n === 'all' ? rows : rows.slice(0, Number(n)))

const UsersChart = ({ users, onOpenUser }: { users: MonitorUserStats[]; onOpenUser: (u: MonitorUserStats) => void }) => {
    const { t } = useTranslation()
    const { dark, direction } = useChartTheme()
    const [sort, setSort] = useState<SortKey>('handled')
    const [top, setTop] = useState<TopN>('10')
    const rows = useMemo(() => take(sortUsers(users, sort), top), [users, sort, top])
    const sortOptions: Option[] = (['handled', 'calls', 'answer_rate', 'talk', 'missed'] as SortKey[]).map((k) => ({
        value: k,
        label: t(`p10.moncharts.sort.${k}`) as string,
    }))
    const series = [
        { name: t('p5.monitor.outAnswered') as string, data: rows.map((u) => u.outbound_answered) },
        { name: t('p5.monitor.outUnanswered') as string, data: rows.map((u) => u.outbound - u.outbound_answered) },
        { name: t('p5.monitor.inAnswered') as string, data: rows.map((u) => u.inbound_answered) },
        { name: t('p5.monitor.inMissed') as string, data: rows.map((u) => u.inbound_missed) },
        { name: t('p5.monitor.internal') as string, data: rows.map((u) => u.internal) },
    ]
    const options = withDefaults({
        colors: userMixColors(dark),
        chart: {
            stacked: true,
            events: {
                dataPointSelection: (_e: unknown, _ctx: unknown, cfg: { dataPointIndex: number }) => {
                    const u = rows[cfg.dataPointIndex]
                    if (u) onOpenUser(u)
                },
            },
        },
        plotOptions: { bar: { horizontal: true, barHeight: '70%', borderRadius: 2, borderRadiusApplication: 'end', borderRadiusWhenStacked: 'last' } },
        stroke: { show: true, width: 1, colors: ['transparent'] },
        states: { active: { filter: { type: 'none' } } },
        xaxis: { categories: rows.map(agentLabel), labels: { formatter: (v: string) => String(Math.round(Number(v))) } },
        yaxis: { labels: { maxWidth: 200 } },
        grid: { xaxis: { lines: { show: true } }, yaxis: { lines: { show: false } } },
        tooltip: {
            shared: true,
            intersect: false,
            y: { formatter: (v: number) => String(v) },
            x: {
                formatter: (_v: number, o?: { dataPointIndex: number }) => {
                    const u = o ? rows[o.dataPointIndex] : undefined
                    if (!u) return ''
                    const r = pct(u.outbound_answered, u.outbound)
                    return `${agentLabel(u)} — ${t('p10.moncharts.users.tip', { handled: u.handled, rate: r === null ? '—' : `${r}%`, talk: clock(u.talk_seconds) })}`
                },
            },
        },
        legend: { position: 'top', horizontalAlign: 'left', onItemClick: { toggleDataSeries: true } },
    })
    return (
        <Card
            title={t('p10.moncharts.users.title')}
            testId="p10-monchart-users"
            actions={
                <div className="flex flex-wrap items-center gap-2">
                    <div className="w-[170px]">
                        <Select
                            size="sm"
                            isSearchable={false}
                            options={sortOptions}
                            value={sortOptions.find((o) => o.value === sort)}
                            onChange={(o) => setSort(((o as Option | null)?.value as SortKey) || 'handled')}
                            aria-label={t('p10.moncharts.sortBy') as string}
                        />
                    </div>
                    <TopNControl value={top} onChange={setTop} testId="p10-monchart-users-top" />
                </div>
            }
        >
            <p className="mb-2 text-[12.5px] text-gray-600 dark:text-night-body">{t('p10.moncharts.clickUser')}</p>
            {rows.length ? (
                <div className={CLICKABLE}>
                    <Chart key={`${sort}-${top}-${rows.length}`} type="bar" height={Math.max(260, rows.length * 32 + 80)} series={series} direction={direction} customOptions={options} />
                </div>
            ) : (
                <EmptyState title={t('p5.monitor.noStats')} />
            )}
        </Card>
    )
}

export default UsersChart
