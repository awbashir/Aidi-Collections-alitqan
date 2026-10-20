/**
 * Collectors leaderboard (GET /v2/dashboards/home/collectors): achievement vs the month
 * target, the period's collected / notes / promises, promise kept rate (30 days), neglect and
 * first-contact rate. Sortable; a row click filters the whole page to that collector, the
 * link icon opens the collector's cases.
 */
import classNames from 'classnames'
import { useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { HiOutlineExternalLink } from 'react-icons/hi'
import Progress from '@/components/ui/Progress'
import Table from '@/components/ui/Table'
import { homeDashboardApi } from '@/services/v2/homeDashboardApi'
import type { HomeCollector } from '@/services/v2/homeDashboardApi'
import { useV2Query } from '@/utils/v2/hooks'
import { formatCount } from '@/utils/v2/format'
import type { HomeCtx } from './context'
import { CardBody, HomeCard } from './parts'
import { arr, moneyText, num, pct, rate, text } from './safe'

const { THead, TBody, Tr, Th, Td } = Table

type SortKey = 'rank' | 'name' | 'achievement_percent' | 'achieved' | 'collected' | 'notes' | 'promises_due' | 'kept_rate' | 'open_cases' | 'neglect' | 'first_contact_rate'

const value = (r: HomeCollector, k: SortKey): number | string => {
    switch (k) {
        case 'name':
            return text(r?.name, '')
        case 'neglect':
            return num(r?.neglect?.d7)
        case 'achieved':
        case 'collected':
            return num(r?.[k])
        default:
            return num(r?.[k] as number | null | undefined, -1)
    }
}

const SortTh = ({ k, sort, onSort, children }: { k: SortKey; sort: [SortKey, 1 | -1]; onSort: (k: SortKey) => void; children: ReactNode }) => (
    <Th aria-sort={sort[0] === k ? (sort[1] === 1 ? 'ascending' : 'descending') : 'none'} className="whitespace-nowrap">
        <button type="button" className="inline-flex items-center gap-1 font-extrabold hover:underline" onClick={() => onSort(k)} data-testid={`homedash-sort-${k}`}>
            {children}
            <span aria-hidden className="text-[10px]">{sort[0] === k ? (sort[1] === 1 ? '▲' : '▼') : ''}</span>
        </button>
    </Th>
)

const Leaderboard = ({ ctx }: { ctx: HomeCtx }) => {
    const { t } = useTranslation()
    const [sort, setSort] = useState<[SortKey, 1 | -1]>(['rank', 1])
    /* the leaderboard lists every collector of the scope: the collector filter is not sent */
    const q = useV2Query(
        (signal) => homeDashboardApi.collectors({ period: ctx.query.period, contract_id: ctx.query.contract_id, nocache: ctx.query.nocache }, signal),
        [ctx.query.period, ctx.query.contract_id]
    )
    const data = q.data
    const rows = useMemo(() => {
        const list = [...arr(data?.collectors)]
        const [k, dir] = sort
        list.sort((a, b) => {
            const va = value(a, k)
            const vb = value(b, k)
            if (typeof va === 'string' || typeof vb === 'string') return dir * String(va).localeCompare(String(vb), 'ar')
            return dir * (va - vb)
        })
        return list
    }, [data, sort])
    const onSort = (k: SortKey) => setSort(([sk, d]) => (sk === k ? [k, d === 1 ? -1 : 1] : [k, k === 'name' || k === 'rank' ? 1 : -1]))

    return (
        <HomeCard
            title={t('p10.homedash.leaderboard.title')}
            subtitle={t('p10.homedash.leaderboard.subtitle', { days: num(data?.kept_rate_days, 30) })}
            testId="homedash-leaderboard"
            resetKeys={[data]}
        >
            <CardBody loading={q.loading && !data} error={q.error} onRetry={q.reload} empty={!rows.length} emptyTitle={t('p10.homedash.leaderboard.empty')}>
                {() => (
                    <div className="overflow-x-auto rounded-lg border border-gray-300 dark:border-night-border">
                        <Table compact>
                            <THead>
                                <Tr>
                                    <SortTh k="rank" sort={sort} onSort={onSort}>#</SortTh>
                                    <SortTh k="name" sort={sort} onSort={onSort}>{t('p10.homedash.leaderboard.collector')}</SortTh>
                                    <SortTh k="achievement_percent" sort={sort} onSort={onSort}>{t('p10.homedash.leaderboard.achievement')}</SortTh>
                                    <SortTh k="achieved" sort={sort} onSort={onSort}>{t('p10.homedash.leaderboard.achievedTarget')}</SortTh>
                                    <SortTh k="collected" sort={sort} onSort={onSort}>{t('p10.homedash.leaderboard.collected')}</SortTh>
                                    <SortTh k="notes" sort={sort} onSort={onSort}>{t('p10.homedash.leaderboard.notes')}</SortTh>
                                    <SortTh k="promises_due" sort={sort} onSort={onSort}>{t('p10.homedash.leaderboard.promises')}</SortTh>
                                    <SortTh k="kept_rate" sort={sort} onSort={onSort}>{t('p10.homedash.leaderboard.keptRate')}</SortTh>
                                    <SortTh k="open_cases" sort={sort} onSort={onSort}>{t('p10.homedash.leaderboard.openCases')}</SortTh>
                                    <SortTh k="neglect" sort={sort} onSort={onSort}>{t('p10.homedash.leaderboard.neglect')}</SortTh>
                                    <SortTh k="first_contact_rate" sort={sort} onSort={onSort}>{t('p10.homedash.leaderboard.firstContact')}</SortTh>
                                    <Th />
                                </Tr>
                            </THead>
                            <TBody>
                                {rows.map((r, i) => {
                                    const id = r?.user_id
                                    const name = text(r?.name)
                                    const focused = !!id && ctx.focus?.id === id
                                    const ach = typeof r?.achievement_percent === 'number' ? r.achievement_percent : null
                                    const clients = ctx.links.clientsOf(id)
                                    return (
                                        <Tr
                                            key={id || `row-${i}`}
                                            onClick={() => id && ctx.setFocus(focused ? null : { id, name })}
                                            className={classNames('cursor-pointer hover:bg-gray-50 dark:hover:bg-night-raised/60', focused && 'bg-azure-50 dark:bg-night-raised')}
                                            title={t('p10.homedash.leaderboard.focus') as string}
                                            data-testid="homedash-leader-row"
                                        >
                                            <Td className="tabular-nums font-extrabold">{num(r?.rank, i + 1)}</Td>
                                            <Td dir="auto">
                                                <div className="font-extrabold text-navy-600 dark:text-gray-100">{name}</div>
                                                <div className="text-[12px] text-gray-600">{text(r?.contract_name)}</div>
                                            </Td>
                                            <Td className="min-w-[140px]">
                                                <div className="flex items-center gap-2">
                                                    <Progress percent={Math.max(0, Math.min(100, ach ?? 0))} showInfo={false} className="h-1.5 flex-1" />
                                                    <span className="tabular-nums text-[12.5px] font-extrabold" dir="ltr">{pct(ach)}</span>
                                                </div>
                                            </Td>
                                            <Td className="whitespace-nowrap tabular-nums" dir="ltr">
                                                {moneyText(r?.achieved, 0)} / {moneyText(r?.target, 0)}
                                            </Td>
                                            <Td className="tabular-nums" dir="ltr">{moneyText(r?.collected, 0)}</Td>
                                            <Td className="tabular-nums" dir="ltr">{formatCount(num(r?.notes))}</Td>
                                            <Td className="tabular-nums" dir="ltr">{formatCount(num(r?.promises_due))}</Td>
                                            <Td className="tabular-nums" dir="ltr" title={`${num(r?.kept)} / ${num(r?.kept) + num(r?.broken)}`}>
                                                {rate(r?.kept_rate ?? null)}
                                            </Td>
                                            <Td className="tabular-nums" dir="ltr">{formatCount(num(r?.open_cases))}</Td>
                                            <Td
                                                className={classNames('tabular-nums', num(r?.neglect?.d7) > 0 && 'text-danger font-extrabold')}
                                                dir="ltr"
                                                title={t('p10.homedash.leaderboard.neglectTitle', { d14: num(r?.neglect?.d14), d30: num(r?.neglect?.d30), never: num(r?.neglect?.never) }) as string}
                                            >
                                                {formatCount(num(r?.neglect?.d7))}
                                            </Td>
                                            <Td className="tabular-nums" dir="ltr">{rate(r?.first_contact_rate ?? null)}</Td>
                                            <Td onClick={(e) => e.stopPropagation()}>
                                                {clients && (
                                                    <Link to={clients} aria-label={t('p10.homedash.leaderboard.openCasesOf', { name }) as string} className="text-gray-500 hover:text-azure-600">
                                                        <HiOutlineExternalLink />
                                                    </Link>
                                                )}
                                            </Td>
                                        </Tr>
                                    )
                                })}
                            </TBody>
                        </Table>
                    </div>
                )}
            </CardBody>
            <p className="mt-2 text-[12px] text-gray-600">{t('p10.homedash.leaderboard.note')}</p>
        </HomeCard>
    )
}

export default Leaderboard
