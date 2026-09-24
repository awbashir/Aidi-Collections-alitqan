/**
 * Repayment plans (`/plans`, keys `plans` | `plans_manage`).
 *
 * GET /v2/plans: state tabs with the exact counts and plan totals from
 * `counts_by_state` (which ignores the state filter), exact totals for the
 * current filter, owner / contract / portfolio / creator / frequency /
 * created / next-due filters, sort and paging. The selected plan opens a side
 * panel (GET /v2/plans/<id>) with its installments, allocations, events and
 * the actions. Tab, filters, sort, page and the selected plan live in the URL
 * hash. Plans never change the debt and never count as recovered money.
 */
import { useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import Table from '@/components/ui/Table'
import ToastNotification from '@/components/shared/myComponents/ToastNotification'
import useHashState, { hashCodec } from '@/utils/hooks/useHashState'
import type { HashSchema } from '@/utils/hooks/useHashState'
import { PLAN_STATES, plansApi } from '@/services/v2'
import type { PlanState } from '@/services/v2'
import { useV2Query } from '@/utils/v2/hooks'
import { formatCount, formatMoney } from '@/utils/v2/format'
import { CivilDate, Ltr, MoneyText } from '@/components/v2/Display'
import { EmptyState, ErrorState, Note, SkeletonLines, SkeletonRows } from '@/components/v2/States'
import {
    CountTabs,
    FilterSelect,
    PageHead,
    PagerFooter,
    ScopeFilters,
    TableFrame,
    rowClass,
    useScopeOptions,
} from '@/components/v2/ListParts'
import { DateFilter, MoneyKpi, SidePanel } from '@/views/phase1/money/listParts'
import PlanDetail from './PlanDetail'
import { DateRange, PlanStateBadge } from './parts'
import { describePlanError } from './planUtils'

const { THead, TBody, Tr, Th, Td } = Table

type Tab = 'all' | PlanState
const TABS: Tab[] = ['all', ...PLAN_STATES]
const SORTS = ['-created_at', 'created_at', 'next_due_date', 'oldest_overdue_date', 'total_amount', '-total_amount'] as const
type Sort = (typeof SORTS)[number]
const FREQS = ['weekly', 'monthly'] as const
type Freq = (typeof FREQS)[number]
const FREQ_FILTER = ['any', ...FREQS] as const
type FreqFilter = (typeof FREQ_FILTER)[number]
const PAGE_SIZE = 25

type HashState = {
    tab: Tab
    owner: string | number | undefined
    contract: string | number | undefined
    portfolio: string | number | undefined
    creator: string | number | undefined
    freq: FreqFilter
    from: string | undefined
    to: string | undefined
    nfrom: string | undefined
    nto: string | undefined
    sort: Sort
    page: number
    sel: string | number | undefined
}

const schema: HashSchema<HashState> = {
    tab: { key: 'state', codec: hashCodec.oneOf(TABS), default: 'all' },
    owner: { key: 'owner', codec: hashCodec.id(), default: undefined },
    contract: { key: 'contract', codec: hashCodec.id(), default: undefined },
    portfolio: { key: 'portfolio', codec: hashCodec.id(), default: undefined },
    creator: { key: 'creator', codec: hashCodec.id(), default: undefined },
    freq: { key: 'freq', codec: hashCodec.oneOf(FREQ_FILTER), default: 'any' },
    from: { key: 'from', codec: hashCodec.date(), default: undefined },
    to: { key: 'to', codec: hashCodec.date(), default: undefined },
    nfrom: { key: 'nfrom', codec: hashCodec.date(), default: undefined },
    nto: { key: 'nto', codec: hashCodec.date(), default: undefined },
    sort: { key: 'sort', codec: hashCodec.oneOf(SORTS), default: '-created_at' },
    page: { key: 'page', codec: hashCodec.int({ min: 1, max: 100000 }), default: 1 },
    sel: { key: 'plan', codec: hashCodec.id(), default: undefined, history: 'replace' },
}

const str = (v: string | number | undefined) => (v === undefined ? undefined : String(v))

const PlanPanel = ({ id, onClose, onChanged }: { id: string; onClose: () => void; onChanged: () => void }) => {
    const { t } = useTranslation()
    const q = useV2Query((signal) => plansApi.detail(id, signal), [id])
    return (
        <SidePanel title={q.data?.case?.name || t('p3.plans.detailTitle')} onClose={onClose} testId="p3-plan-panel">
            {q.loading && !q.data ? (
                <SkeletonLines rows={8} />
            ) : q.error && !q.data ? (
                <ErrorState error={q.error} onRetry={q.reload} describe={describePlanError} />
            ) : q.data ? (
                <PlanDetail
                    plan={q.data}
                    showCase
                    compact
                    onReload={q.reload}
                    onChanged={() => {
                        ToastNotification(t('p3.plans.saved'), t('p3.plans.title'), 'success')
                        q.reload()
                        onChanged()
                    }}
                />
            ) : null}
        </SidePanel>
    )
}

const Plans = () => {
    const { t } = useTranslation()
    const { state, setState } = useHashState(schema)
    const users = useScopeOptions()
    const stateFilter = state.tab === 'all' ? undefined : state.tab

    const query = useV2Query(
        (signal) =>
            plansApi.list(
                {
                    state: stateFilter,
                    owner_id: str(state.owner),
                    contract_id: str(state.contract),
                    portfolio_id: str(state.portfolio),
                    created_by: str(state.creator),
                    frequency: state.freq === 'any' ? undefined : state.freq,
                    created_from: state.from,
                    created_to: state.to,
                    next_due_from: state.nfrom,
                    next_due_to: state.nto,
                    sort: state.sort,
                    page: state.page,
                    page_size: PAGE_SIZE,
                },
                signal
            ),
        [
            stateFilter,
            state.owner,
            state.contract,
            state.portfolio,
            state.creator,
            state.freq,
            state.from,
            state.to,
            state.nfrom,
            state.nto,
            state.sort,
            state.page,
        ]
    )
    const data = query.data

    const tabs = useMemo(() => {
        const cbs = data?.counts_by_state
        return TABS.map((tab) => {
            if (tab === 'all') {
                // A count sum is an integer sum; the money sub-line is only shown
                // when the API itself returned it (the unfiltered "all" totals).
                const count = cbs ? PLAN_STATES.reduce((n, s) => n + (cbs[s]?.count || 0), 0) : undefined
                return {
                    value: tab,
                    label: t('p3.plans.all'),
                    count,
                    sub:
                        data && state.tab === 'all' && data.state.length === PLAN_STATES.length ? (
                            <span dir="ltr" data-amount={data.totals.total_amount}>
                                {formatMoney(data.totals.total_amount)}
                            </span>
                        ) : undefined,
                }
            }
            const c = cbs?.[tab]
            return {
                value: tab,
                label: t(`p3.plan.state.${tab}`),
                count: c ? c.count : undefined,
                sub: c ? (
                    <span dir="ltr" data-amount={c.total_amount} title={String(t('p3.plans.tabSubTitle'))}>
                        {formatMoney(c.total_amount)}
                    </span>
                ) : undefined,
            }
        })
    }, [data, state.tab, t])

    const sortOptions = SORTS.map((s) => ({ value: s, label: t(`p3.plans.sort.${s.replace('-', 'desc_')}`) }))
    const freqOptions = FREQS.map((f) => ({ value: f, label: t(`p3.plans.freq.${f}`) }))
    const selected = str(state.sel)
    const cols = 11

    return (
        <div data-testid="p3-plans">
            <PageHead
                title={t('p3.plans.title')}
                subtitle={t('p3.plans.subtitle')}
                onRefresh={query.reload}
                refreshing={query.loading && !!data}
            />

            <ScopeFilters
                owner={str(state.owner)}
                contract={str(state.contract)}
                portfolio={str(state.portfolio)}
                onChange={(patch) => setState({ ...patch, page: 1 })}
            >
                <FilterSelect
                    label={t('p3.plans.filter.creator')}
                    options={users.owners}
                    loading={users.loading}
                    value={str(state.creator)}
                    testId="p3-filter-creator"
                    onChange={(v) => setState({ creator: v, page: 1 })}
                />
                <FilterSelect
                    label={t('p3.plans.filter.frequency')}
                    options={freqOptions}
                    value={state.freq === 'any' ? undefined : state.freq}
                    width={160}
                    testId="p3-filter-frequency"
                    onChange={(v) => setState({ freq: (v as Freq | undefined) || 'any', page: 1 })}
                />
                <DateFilter
                    label={t('p3.plans.filter.createdFrom')}
                    value={state.from}
                    max={state.to}
                    testId="p3-filter-created-from"
                    onChange={(v) => setState({ from: v, page: 1 })}
                />
                <DateFilter
                    label={t('p3.plans.filter.createdTo')}
                    value={state.to}
                    min={state.from}
                    testId="p3-filter-created-to"
                    onChange={(v) => setState({ to: v, page: 1 })}
                />
                <DateFilter
                    label={t('p3.plans.filter.nextDueFrom')}
                    value={state.nfrom}
                    max={state.nto}
                    testId="p3-filter-next-from"
                    onChange={(v) => setState({ nfrom: v, page: 1 })}
                />
                <DateFilter
                    label={t('p3.plans.filter.nextDueTo')}
                    value={state.nto}
                    min={state.nfrom}
                    testId="p3-filter-next-to"
                    onChange={(v) => setState({ nto: v, page: 1 })}
                />
                <FilterSelect
                    label={t('p3.plans.sortLabel')}
                    options={sortOptions}
                    value={state.sort}
                    width={210}
                    testId="p3-sort"
                    onChange={(v) => setState({ sort: (v as Sort) || '-created_at', page: 1 })}
                />
            </ScopeFilters>

            <div className="mb-4 grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4" data-testid="p3-plan-totals">
                <MoneyKpi
                    label={t('p3.plans.kpi.total')}
                    value={data?.totals.total_amount}
                    sub={data ? t('p3.plans.kpi.count', { n: formatCount(data.total) }) : undefined}
                    testId="p3-kpi-total"
                />
                <MoneyKpi label={t('p3.plans.kpi.allocated')} value={data?.totals.allocated} testId="p3-kpi-allocated" />
                <MoneyKpi label={t('p3.plans.kpi.remaining')} value={data?.totals.remaining} testId="p3-kpi-remaining" />
                <MoneyKpi
                    label={t('p3.plans.kpi.overdue')}
                    value={data?.totals.overdue_amount}
                    lead
                    testId="p3-kpi-overdue"
                />
            </div>

            <div className="mb-3">
                <CountTabs
                    tabs={tabs}
                    value={state.tab}
                    onChange={(tab) => setState({ tab, page: 1 })}
                    testId="p3-plan-tabs"
                />
            </div>
            <Note className="mb-3">
                <span data-testid="p3-plan-broken-explain">
                    {t('p3.plans.brokenExplain', { n: data?.broken_threshold ?? '—' })}
                </span>
            </Note>

            <div className={selected ? 'grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1fr)_540px]' : ''}>
                <div className="min-w-0">
                    <TableFrame
                        testId="p3-plan-table"
                        footer={
                            data && data.total > 0 ? (
                                <PagerFooter
                                    page={state.page}
                                    pageSize={PAGE_SIZE}
                                    total={data.total}
                                    onPage={(page) => setState({ page })}
                                />
                            ) : undefined
                        }
                    >
                        {query.error && !data ? (
                            <ErrorState error={query.error} onRetry={query.reload} describe={describePlanError} />
                        ) : (
                            <Table>
                                <THead>
                                    <Tr>
                                        <Th>{t('p3.plans.col.case')}</Th>
                                        <Th>{t('p3.plans.col.state')}</Th>
                                        <Th>{t('p3.plans.col.frequency')}</Th>
                                        <Th>{t('p3.plans.col.startEnd')}</Th>
                                        <Th className="text-end">{t('p3.plans.col.total')}</Th>
                                        <Th className="text-end">{t('p3.plans.col.allocated')}</Th>
                                        <Th className="text-end">{t('p3.plans.col.remaining')}</Th>
                                        <Th className="text-end">{t('p3.plans.col.overdue')}</Th>
                                        <Th>{t('p3.plans.col.nextOrOverdue')}</Th>
                                        <Th>{t('p3.plans.col.installments')}</Th>
                                        <Th>{t('p3.plans.col.owner')}</Th>
                                    </Tr>
                                </THead>
                                <TBody>
                                    {query.loading && !data ? (
                                        <SkeletonRows cols={cols} />
                                    ) : data && data.items.length > 0 ? (
                                        data.items.map((p) => (
                                            <Tr
                                                key={p.id}
                                                className={rowClass(selected === p.id)}
                                                aria-current={selected === p.id ? 'true' : undefined}
                                                onClick={() => setState({ sel: p.id }, { replace: true })}
                                                data-testid="p3-plan-row"
                                                data-plan={p.id}
                                                data-state={p.state}
                                            >
                                                <Td>
                                                    <div className="font-extrabold text-navy-600 dark:text-gray-100">
                                                        {p.case?.name || '—'}
                                                    </div>
                                                    <Ltr className="whitespace-nowrap text-[12.5px] text-gray-600">
                                                        {p.case?.account_number}
                                                    </Ltr>
                                                </Td>
                                                <Td>
                                                    <PlanStateBadge state={p.state} size="row" />
                                                </Td>
                                                <Td className="whitespace-nowrap">{t(`p3.plans.freq.${p.frequency}`)}</Td>
                                                <Td>
                                                    <span className="whitespace-nowrap">
                                                        <DateRange start={p.start_date} end={p.end_date} />
                                                    </span>
                                                </Td>
                                                <Td className="text-end">
                                                    <MoneyText value={p.total_amount} strong />
                                                </Td>
                                                <Td className="text-end">
                                                    <MoneyText value={p.allocated} />
                                                </Td>
                                                <Td className="text-end">
                                                    <MoneyText value={p.remaining} />
                                                </Td>
                                                <Td className="text-end">
                                                    <MoneyText value={p.overdue_amount} />
                                                </Td>
                                                <Td>
                                                    <div className="whitespace-nowrap text-[12.5px]">
                                                        <span className="text-gray-600">{t('p3.plans.nextShort')}</span>{' '}
                                                        <CivilDate value={p.next_due_date} />
                                                    </div>
                                                    {p.oldest_overdue_date && (
                                                        <div className="whitespace-nowrap text-[12.5px] text-danger">
                                                            {t('p3.plans.overdueShort')} <CivilDate value={p.oldest_overdue_date} />
                                                        </div>
                                                    )}
                                                </Td>
                                                <Td className="whitespace-nowrap">
                                                    <Ltr>
                                                        {formatCount(p.counts?.paid ?? 0)} / {formatCount(p.installment_count)}
                                                    </Ltr>
                                                </Td>
                                                <Td>
                                                    <div>
                                                        {p.case?.owner?.name || (
                                                            <span className="text-gray-500">{t('p1.unassigned')}</span>
                                                        )}
                                                    </div>
                                                    <div dir="auto" className="whitespace-nowrap text-[12px] text-gray-600">
                                                        {p.case?.portfolio?.name || ''}
                                                    </div>
                                                </Td>
                                            </Tr>
                                        ))
                                    ) : (
                                        <tr>
                                            <td colSpan={cols}>
                                                <EmptyState title={t('p3.plans.emptyTitle')} hint={t('p3.plans.emptyHint')} />
                                            </td>
                                        </tr>
                                    )}
                                </TBody>
                            </Table>
                        )}
                    </TableFrame>
                    {query.error && data ? (
                        <ErrorState error={query.error} onRetry={query.reload} describe={describePlanError} />
                    ) : null}
                    <Note className="mt-3">{t('p3.plans.footNote')}</Note>
                </div>
                {selected && (
                    <PlanPanel
                        id={selected}
                        onClose={() => setState({ sel: undefined }, { replace: true })}
                        onChanged={query.reload}
                    />
                )}
            </div>
        </div>
    )
}

export default Plans
