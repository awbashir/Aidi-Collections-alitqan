/**
 * Open cases in scope (GET /v2/dashboards/home/portfolio): recovery rate per contract and
 * portfolio (approved, non-reversed money on the open cases / their original debt),
 * outstanding by case age and by amount, reachability of each case's latest note, neglect
 * (no note in 7 / 14 / 30 days) and the first-contact rate this month.
 */
import classNames from 'classnames'
import { Fragment, useState } from 'react'
import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useNavigate } from 'react-router-dom'
import Chart from '@/components/shared/Chart'
import Progress from '@/components/ui/Progress'
import Table from '@/components/ui/Table'
import { useConfig } from '@/components/ui/ConfigProvider'
import { ErrorState, SkeletonLines } from '@/components/v2/States'
import { COLORS, COLOR_2, COLOR_4, COLOR_8 } from '@/constants/chart.constant'
import { homeDashboardApi } from '@/services/v2/homeDashboardApi'
import type { HomeBucket, HomePortfolio } from '@/services/v2/homeDashboardApi'
import { useV2Query } from '@/utils/v2/hooks'
import { formatCount } from '@/utils/v2/format'
import type { HomeCtx } from './context'
import { CardBody, HomeCard } from './parts'
import { arr, compact, grouped, moneyText, num, pct, rate, text } from './safe'

const { THead, TBody, Tr, Th, Td } = Table

const Recovery = ({ data, ctx }: { data: HomePortfolio; ctx: HomeCtx }) => {
    const { t } = useTranslation()
    const [open, setOpen] = useState<string | null>(null)
    const rows = arr(data.contracts)
    return (
        <HomeCard title={t('p10.homedash.recovery.title')} subtitle={t('p10.homedash.recovery.subtitle')} testId="homedash-recovery" resetKeys={[data]}>
            <CardBody loading={false} error={null} empty={!rows.length} emptyTitle={t('p10.homedash.noCases')}>
                {() => (
                    <div className="overflow-x-auto rounded-lg border border-gray-300 dark:border-night-border">
                        <Table compact>
                            <THead>
                                <Tr>
                                    <Th>{t('p10.homedash.recovery.contract')}</Th>
                                    <Th>{t('p10.homedash.recovery.cases')}</Th>
                                    <Th>{t('p10.homedash.recovery.debt')}</Th>
                                    <Th>{t('p10.homedash.recovery.collected')}</Th>
                                    <Th className="min-w-[150px]">{t('p10.homedash.recovery.rate')}</Th>
                                </Tr>
                            </THead>
                            <TBody>
                                {rows.map((c, i) => {
                                    const key = c?.contract_id || `none-${i}`
                                    const expanded = open === key
                                    const selected = !!c?.contract_id && ctx.contract === c.contract_id
                                    return (
                                        <Fragment key={key}>
                                            <Tr
                                                className={classNames('cursor-pointer hover:bg-gray-50 dark:hover:bg-night-raised/60', selected && 'bg-azure-50 dark:bg-night-raised')}
                                                onClick={() => setOpen(expanded ? null : key)}
                                            >
                                                <Td dir="auto">
                                                    <span aria-hidden className="me-1 text-[10px] text-gray-500">{expanded ? '▼' : '▶'}</span>
                                                    <span className="font-extrabold text-navy-600 dark:text-gray-100">{text(c?.name, t('p10.homedash.noContract') as string)}</span>
                                                    {ctx.admin && c?.contract_id && (
                                                        <button
                                                            type="button"
                                                            className="ms-2 text-[12px] font-extrabold text-azure-600 hover:underline"
                                                            onClick={(e) => {
                                                                e.stopPropagation()
                                                                ctx.setContract(selected ? '' : String(c.contract_id))
                                                            }}
                                                        >
                                                            {selected ? t('p10.homedash.clearFilter') : t('p10.homedash.filterHere')}
                                                        </button>
                                                    )}
                                                </Td>
                                                <Td className="tabular-nums" dir="ltr">{formatCount(num(c?.cases))}</Td>
                                                <Td className="tabular-nums" dir="ltr">{moneyText(c?.debt, 0)}</Td>
                                                <Td className="tabular-nums" dir="ltr">{moneyText(c?.collected, 0)}</Td>
                                                <Td>
                                                    <div className="flex items-center gap-2">
                                                        <Progress percent={Math.max(0, Math.min(100, num(c?.rate)))} showInfo={false} className="h-1.5 flex-1" />
                                                        <span className="tabular-nums text-[12.5px] font-extrabold" dir="ltr">{pct(c?.rate ?? null)}</span>
                                                    </div>
                                                </Td>
                                            </Tr>
                                            {expanded &&
                                                arr(c?.portfolios).map((p, j) => {
                                                    const to = p?.portfolio_id ? ctx.links.clients({ portfolio: p.portfolio_id }) : undefined
                                                    return (
                                                        <Tr key={p?.portfolio_id || `${key}-${j}`} className="bg-gray-50/60 dark:bg-night-raised/40">
                                                            <Td dir="auto" className="ps-8 text-[12.5px]">
                                                                {to ? (
                                                                    <Link to={to} className="text-azure-600 hover:underline">{text(p?.name)}</Link>
                                                                ) : (
                                                                    text(p?.name)
                                                                )}
                                                            </Td>
                                                            <Td className="tabular-nums text-[12.5px]" dir="ltr">{formatCount(num(p?.cases))}</Td>
                                                            <Td className="tabular-nums text-[12.5px]" dir="ltr">{moneyText(p?.debt, 0)}</Td>
                                                            <Td className="tabular-nums text-[12.5px]" dir="ltr">{moneyText(p?.collected, 0)}</Td>
                                                            <Td className="tabular-nums text-[12.5px]" dir="ltr">{pct(p?.rate ?? null)}</Td>
                                                        </Tr>
                                                    )
                                                })}
                                        </Fragment>
                                    )
                                })}
                            </TBody>
                        </Table>
                    </div>
                )}
            </CardBody>
        </HomeCard>
    )
}

const BucketChart = ({ title, subtitle, buckets, labels, testId }: { title: ReactNode; subtitle?: ReactNode; buckets: HomeBucket[]; labels: string[]; testId: string }) => {
    const { t } = useTranslation()
    const { direction } = useConfig()
    const total = buckets.reduce((n, b) => n + num(b?.count), 0)
    const names = [t('p10.homedash.buckets.cases') as string, t('p10.homedash.buckets.outstanding') as string]
    return (
        <HomeCard title={title} subtitle={subtitle} testId={testId} resetKeys={[buckets]}>
            <CardBody loading={false} error={null} empty={!total} emptyTitle={t('p10.homedash.noCases')}>
                {() => (
                    <Chart
                        type="bar"
                        direction={direction}
                        height={260}
                        series={[
                            { name: names[0], data: buckets.map((b) => num(b?.count)) },
                            { name: names[1], data: buckets.map((b) => num(b?.outstanding)) },
                        ]}
                        xAxis={labels}
                        customOptions={{
                            colors: [COLOR_2, COLOR_4],
                            plotOptions: { bar: { columnWidth: '55%', borderRadius: 3 } },
                            legend: { position: 'top' },
                            yaxis: [
                                { seriesName: names[0], labels: { formatter: compact } },
                                { seriesName: names[1], opposite: true, labels: { formatter: compact } },
                            ],
                            tooltip: { shared: true, intersect: false, y: { formatter: (v: number) => grouped(v) } },
                        }}
                    />
                )}
            </CardBody>
        </HomeCard>
    )
}

const Reachability = ({ data }: { data: HomePortfolio }) => {
    const { t } = useTranslation()
    const rows = arr(data.reachability).filter((r) => num(r?.count) > 0)
    const top = rows.slice(0, 7)
    const rest = rows.slice(7)
    const labels = [
        ...top.map((r) => (r?.id ? text(r?.name) : (t('p10.homedash.reach.noNote') as string))),
        ...(rest.length ? [t('p10.homedash.other') as string] : []),
    ]
    const values = [...top.map((r) => num(r?.count)), ...(rest.length ? [rest.reduce((n, r) => n + num(r?.count), 0)] : [])]
    const colors = [...top.map((r, i) => (r?.id ? COLORS[i % 7] : COLOR_8)), ...(rest.length ? [COLOR_8] : [])]
    return (
        <HomeCard title={t('p10.homedash.reach.title')} subtitle={t('p10.homedash.reach.subtitle')} testId="homedash-reach" resetKeys={[data]}>
            <CardBody loading={false} error={null} empty={!values.length} emptyTitle={t('p10.homedash.noCases')}>
                {() => (
                    <Chart
                        type="donut"
                        height={260}
                        series={values}
                        donutTitle={t('p10.homedash.recovery.cases') as string}
                        donutText={formatCount(values.reduce((a, b) => a + b, 0))}
                        customOptions={{ labels, colors, legend: { position: 'bottom' } }}
                    />
                )}
            </CardBody>
        </HomeCard>
    )
}

const Neglect = ({ data, ctx }: { data: HomePortfolio; ctx: HomeCtx }) => {
    const { t } = useTranslation()
    const navigate = useNavigate()
    const n = data.neglect
    const open = num(n?.open)
    const link = ctx.links.neglect()
    const tiles: [string, number][] = [
        [t('p10.homedash.neglect.d7'), num(n?.d7)],
        [t('p10.homedash.neglect.d14'), num(n?.d14)],
        [t('p10.homedash.neglect.d30'), num(n?.d30)],
        [t('p10.homedash.neglect.never'), num(n?.never)],
    ]
    return (
        <HomeCard title={t('p10.homedash.neglect.title')} subtitle={t('p10.homedash.neglect.subtitle', { n: formatCount(open) })} testId="homedash-neglect" resetKeys={[data]}>
            <div className="grid grid-cols-2 gap-3">
                {tiles.map(([label, v]) => (
                    <button
                        key={label}
                        type="button"
                        disabled={!link}
                        onClick={() => link && navigate(link)}
                        className={classNames(
                            'rounded-lg border border-gray-300 px-3 py-2 text-start dark:border-night-border',
                            link && 'hover:border-azure-600'
                        )}
                    >
                        <div className="text-eyebrow">{label}</div>
                        <div className="mt-1 flex items-end gap-2">
                            <span className="text-[20px] font-extrabold tabular-nums text-navy-600 dark:text-gray-100" dir="ltr">{formatCount(v)}</span>
                            <span className="pb-0.5 text-[12px] text-gray-600" dir="ltr">{open ? pct((v * 100) / open) : '—'}</span>
                        </div>
                    </button>
                ))}
            </div>
            <div className="mt-4">
                <div className="mb-1 flex items-center justify-between text-[12.5px]">
                    <span className="font-extrabold text-navy-600 dark:text-gray-100">{t('p10.homedash.firstContact.title')}</span>
                    <span className="tabular-nums font-extrabold" dir="ltr">{rate(data.first_contact?.rate ?? null)}</span>
                </div>
                <Progress percent={Math.max(0, Math.min(100, num(data.first_contact?.rate) * 100))} showInfo={false} className="h-2" />
                <div className="mt-1 text-[12px] text-gray-600">
                    {t('p10.homedash.firstContact.hint', { contacted: formatCount(num(data.first_contact?.contacted)), open: formatCount(open) })}
                </div>
            </div>
        </HomeCard>
    )
}

const PortfolioSection = ({ ctx }: { ctx: HomeCtx }) => {
    const { t } = useTranslation()
    const q = useV2Query((signal) => homeDashboardApi.portfolio(ctx.query, signal), [ctx.query.contract_id, ctx.query.user_id])
    const data = q.data
    if (q.error && !data) return <ErrorState error={q.error} onRetry={q.reload} />
    if (!data) return <SkeletonLines rows={8} />
    const age = arr(data.age_buckets)
    const amounts = arr(data.amount_buckets)
    const ageLabel = (b: HomeBucket) =>
        b?.max === null || b?.max === undefined
            ? (t('p10.homedash.buckets.ageOver', { n: num(b?.min) }) as string)
            : (t('p10.homedash.buckets.ageRange', { from: num(b?.min), to: num(b?.max) }) as string)
    const amountLabel = (b: HomeBucket) =>
        b?.key === 'settled'
            ? (t('p10.homedash.buckets.settled') as string)
            : b?.max === null || b?.max === undefined
            ? `≥ ${compact(num(b?.min))}`
            : `${compact(num(b?.min))} – ${compact(num(b?.max))}`
    return (
        <div className="grid gap-4 xl:grid-cols-2" data-testid="homedash-portfolio">
            <div className="xl:col-span-2">
                <Recovery data={data} ctx={ctx} />
            </div>
            <BucketChart title={t('p10.homedash.buckets.ageTitle')} subtitle={t('p10.homedash.buckets.ageSubtitle')} buckets={age} labels={age.map(ageLabel)} testId="homedash-age" />
            <BucketChart title={t('p10.homedash.buckets.amountTitle')} subtitle={t('p10.homedash.buckets.amountSubtitle')} buckets={amounts} labels={amounts.map(amountLabel)} testId="homedash-amounts" />
            <Reachability data={data} />
            <Neglect data={data} ctx={ctx} />
        </div>
    )
}

export default PortfolioSection
