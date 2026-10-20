/**
 * Today's calls by hour (GET /v2/dashboards/home/calls: the call monitor's own statistics and
 * scope, so a collector sees their extension, a supervisor the team, SuperAdmin everyone).
 * When the PBX call records cannot be read the card says the figures are unknown, never 0.
 */
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import Chart from '@/components/shared/Chart'
import { useConfig } from '@/components/ui/ConfigProvider'
import { EmptyState } from '@/components/v2/States'
import { COLOR_2, COLOR_4, COLOR_8 } from '@/constants/chart.constant'
import { homeDashboardApi } from '@/services/v2/homeDashboardApi'
import { useV2Query } from '@/utils/v2/hooks'
import { formatCount } from '@/utils/v2/format'
import type { HomeCtx } from './context'
import { CardBody, HomeCard } from './parts'
import { arr, num, rate } from './safe'

const clock = (seconds: number) => {
    const s = Math.max(0, Math.floor(num(seconds)))
    return `${Math.floor(s / 3600)}:${String(Math.floor((s % 3600) / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`
}

const CallsCard = ({ ctx }: { ctx: HomeCtx }) => {
    const { t } = useTranslation()
    const { direction } = useConfig()
    const q = useV2Query((signal) => homeDashboardApi.calls({ nocache: ctx.query.nocache }, signal), [])
    const d = q.data
    const hours = arr(d?.hours)
    const busy = hours.filter((h) => num(h?.answered) + num(h?.not_answered) + num(h?.unknown) > 0).map((h) => num(h?.hour))
    const lo = Math.min(7, ...busy)
    const hi = Math.max(22, ...busy)
    const range = Array.from({ length: hi - lo + 1 }, (_, i) => lo + i)
    const byHour = new Map(hours.map((h) => [num(h?.hour), h]))
    const tt = d?.totals
    const link = ctx.links.monitor()
    return (
        <HomeCard
            title={t('p10.homedash.calls.title')}
            subtitle={d?.scope ? t(`p10.homedash.scope.${d.scope}`) : undefined}
            testId="homedash-calls"
            actions={
                link ? (
                    <Link to={link} className="text-[12.5px] font-extrabold text-azure-600 hover:underline">
                        {t('p10.homedash.calls.monitor')}
                    </Link>
                ) : undefined
            }
            resetKeys={[d]}
        >
            <CardBody loading={q.loading && !d} error={q.error} onRetry={q.reload}>
                {() =>
                    d?.available === false ? (
                        <EmptyState title={t('p10.homedash.calls.unknownTitle')} hint={t('p10.homedash.calls.unknown')} className="py-6" />
                    ) : (
                        <div className="grid gap-4 lg:grid-cols-4">
                            <div className="grid grid-cols-2 content-start gap-3 lg:grid-cols-1">
                                {[
                                    [t('p10.homedash.calls.handled'), formatCount(num(tt?.handled))],
                                    [t('p10.homedash.calls.outbound'), formatCount(num(tt?.outbound))],
                                    [t('p10.homedash.calls.answerRate'), rate(tt?.answer_rate ?? null)],
                                    [t('p10.homedash.calls.inboundMissed'), formatCount(num(tt?.inbound_missed))],
                                    [t('p10.homedash.calls.talk'), clock(num(tt?.talk_seconds))],
                                ].map(([label, value]) => (
                                    <div key={String(label)}>
                                        <div className="text-eyebrow">{label}</div>
                                        <div className="text-[18px] font-extrabold tabular-nums text-navy-600 dark:text-gray-100" dir="ltr">
                                            {value}
                                        </div>
                                    </div>
                                ))}
                            </div>
                            <div className="lg:col-span-3">
                                {num(tt?.calls) ? (
                                    <Chart
                                        type="bar"
                                        direction={direction}
                                        height={260}
                                        series={[
                                            { name: t('p10.homedash.calls.answered') as string, data: range.map((h) => num(byHour.get(h)?.answered)) },
                                            { name: t('p10.homedash.calls.notAnswered') as string, data: range.map((h) => num(byHour.get(h)?.not_answered)) },
                                            { name: t('p10.homedash.calls.otherResult') as string, data: range.map((h) => num(byHour.get(h)?.unknown)) },
                                        ]}
                                        xAxis={range.map((h) => `${String(h).padStart(2, '0')}:00`)}
                                        customOptions={{
                                            colors: [COLOR_2, COLOR_4, COLOR_8],
                                            chart: { stacked: true },
                                            plotOptions: { bar: { columnWidth: '60%', borderRadius: 2 } },
                                            legend: { position: 'top' },
                                        }}
                                    />
                                ) : (
                                    <EmptyState title={t('p10.homedash.calls.none')} className="py-6" />
                                )}
                                {d?.truncated && <p className="mt-1 text-[12px] text-warning">{t('p10.homedash.calls.truncated')}</p>}
                            </div>
                        </div>
                    )
                }
            </CardBody>
        </HomeCard>
    )
}

export default CallsCard
