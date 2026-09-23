/**
 * Promises (`/promises`, route key `promises`).
 *
 * GET /v2/promises: state tabs with exact counts and amounts from
 * `counts_by_state` (which ignores the state filter), exact totals for the
 * current filter, owner / contract / portfolio / recorder / due-date filters,
 * sort, paging. The selected promise opens a side panel (GET /v2/promises/<id>)
 * with its allocations, events and the cancel / allocate / deallocate actions.
 * Tab, filters, sort, page and the selected promise live in the URL hash.
 */
import { useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import Table from '@/components/ui/Table'
import ToastNotification from '@/components/shared/myComponents/ToastNotification'
import useHashState, { hashCodec } from '@/utils/hooks/useHashState'
import type { HashSchema } from '@/utils/hooks/useHashState'
import { PROMISE_STATES, moneyApi } from '@/services/v2'
import type { PromiseState } from '@/services/v2'
import { useV2Query } from '@/utils/v2/hooks'
import { formatCount, formatMoney } from '@/utils/v2/format'
import { CivilDate, Ltr, MoneyText } from '@/components/v2/Display'
import { PromiseBadge } from '@/components/v2/Badges'
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
import PromiseDetail from '@/views/phase1/money/PromiseDetail'
import { DateFilter, MoneyKpi, SidePanel } from '@/views/phase1/money/listParts'
import { sumMoney } from '@/views/phase1/money/moneyMath'

const { THead, TBody, Tr, Th, Td } = Table

type Tab = 'all' | PromiseState
const TABS: Tab[] = ['all', ...PROMISE_STATES]
const SORTS = ['due_date', '-due_date', 'created_at', '-created_at', 'amount', '-amount'] as const
type Sort = (typeof SORTS)[number]
const PAGE_SIZE = 25

type HashState = {
    tab: Tab
    owner: string | number | undefined
    contract: string | number | undefined
    portfolio: string | number | undefined
    recorder: string | number | undefined
    from: string | undefined
    to: string | undefined
    sort: Sort
    page: number
    sel: string | number | undefined
}

const schema: HashSchema<HashState> = {
    tab: { key: 'state', codec: hashCodec.oneOf(TABS), default: 'all' },
    owner: { key: 'owner', codec: hashCodec.id(), default: undefined },
    contract: { key: 'contract', codec: hashCodec.id(), default: undefined },
    portfolio: { key: 'portfolio', codec: hashCodec.id(), default: undefined },
    recorder: { key: 'recorder', codec: hashCodec.id(), default: undefined },
    from: { key: 'from', codec: hashCodec.date(), default: undefined },
    to: { key: 'to', codec: hashCodec.date(), default: undefined },
    sort: { key: 'sort', codec: hashCodec.oneOf(SORTS), default: 'due_date' },
    page: { key: 'page', codec: hashCodec.int({ min: 1, max: 100000 }), default: 1 },
    sel: { key: 'promise', codec: hashCodec.id(), default: undefined, history: 'replace' },
}

const str = (v: string | number | undefined) => (v === undefined ? undefined : String(v))

const PromisePanel = ({ id, onClose, onChanged }: { id: string; onClose: () => void; onChanged: () => void }) => {
    const { t } = useTranslation()
    const q = useV2Query((signal) => moneyApi.promise(id, signal), [id])
    return (
        <SidePanel
            title={q.data?.case?.name || t('p1.promises.detailTitle')}
            onClose={onClose}
            testId="p1-promise-panel"
        >
            {q.loading && !q.data ? (
                <SkeletonLines rows={8} />
            ) : q.error && !q.data ? (
                <ErrorState error={q.error} onRetry={q.reload} />
            ) : q.data ? (
                <PromiseDetail
                    promise={q.data}
                    onReload={q.reload}
                    onChanged={() => {
                        ToastNotification(t('p1.promises.saved'), t('p1.promises.title'), 'success')
                        q.reload()
                        onChanged()
                    }}
                />
            ) : null}
        </SidePanel>
    )
}

const Promises = () => {
    const { t } = useTranslation()
    const { state, setState } = useHashState(schema)
    const users = useScopeOptions()
    const stateFilter = state.tab === 'all' ? undefined : state.tab

    const query = useV2Query(
        (signal) =>
            moneyApi.promises(
                {
                    state: stateFilter,
                    owner_id: str(state.owner),
                    contract_id: str(state.contract),
                    portfolio_id: str(state.portfolio),
                    recorder_id: str(state.recorder),
                    due_from: state.from,
                    due_to: state.to,
                    sort: state.sort,
                    page: state.page,
                    page_size: PAGE_SIZE,
                },
                signal
            ),
        [stateFilter, state.owner, state.contract, state.portfolio, state.recorder, state.from, state.to, state.sort, state.page]
    )
    const data = query.data

    const tabs = useMemo(() => {
        const cbs = data?.counts_by_state
        return TABS.map((tab) => {
            if (tab === 'all') {
                const count = cbs ? PROMISE_STATES.reduce((n, s) => n + cbs[s].count, 0) : undefined
                const amount = cbs ? sumMoney(PROMISE_STATES.map((s) => cbs[s].amount)) : null
                return {
                    value: tab,
                    label: t('p1.promises.all'),
                    count,
                    sub: amount ? <span dir="ltr">{formatMoney(amount)}</span> : undefined,
                }
            }
            const c = cbs?.[tab]
            return {
                value: tab,
                label: t(`p1.promiseState.${tab}`),
                count: c ? c.count : undefined,
                sub: c ? (
                    <span dir="ltr" title={String(t('p1.promises.tabSubTitle'))}>
                        {formatMoney(c.amount)}
                        {tab !== 'cancelled' && (
                            <span className="text-gray-500"> · {formatMoney(c.allocated)}</span>
                        )}
                    </span>
                ) : undefined,
            }
        })
    }, [data, t])

    const sortOptions = SORTS.map((s) => ({ value: s, label: t(`p1.promises.sort.${s.replace('-', 'desc_')}`) }))
    const selected = str(state.sel)
    const cols = 8

    return (
        <div data-testid="p1-promises">
            <PageHead
                title={t('p1.promises.title')}
                subtitle={t('p1.promises.subtitle')}
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
                    label={t('p1.filter.recorder')}
                    options={users.owners}
                    loading={users.loading}
                    value={str(state.recorder)}
                    testId="p1-filter-recorder"
                    onChange={(v) => setState({ recorder: v, page: 1 })}
                />
                <DateFilter
                    label={t('p1.promises.dueFrom')}
                    value={state.from}
                    max={state.to}
                    testId="p1-filter-due-from"
                    onChange={(v) => setState({ from: v, page: 1 })}
                />
                <DateFilter
                    label={t('p1.promises.dueTo')}
                    value={state.to}
                    min={state.from}
                    testId="p1-filter-due-to"
                    onChange={(v) => setState({ to: v, page: 1 })}
                />
                <FilterSelect
                    label={t('p1.promises.sortLabel')}
                    options={sortOptions}
                    value={state.sort}
                    width={200}
                    testId="p1-sort"
                    onChange={(v) => setState({ sort: (v as Sort) || 'due_date', page: 1 })}
                />
            </ScopeFilters>

            <div className="mb-4 grid grid-cols-1 gap-4 md:grid-cols-3" data-testid="p1-promise-totals">
                <MoneyKpi
                    label={t('p1.promises.kpiAmount')}
                    value={data?.totals.amount}
                    sub={data ? t('p1.promises.kpiCount', { n: formatCount(data.total) }) : undefined}
                    testId="p1-kpi-amount"
                />
                <MoneyKpi label={t('p1.promises.kpiAllocated')} value={data?.totals.allocated} testId="p1-kpi-allocated" />
                <MoneyKpi
                    label={t('p1.promises.kpiRemaining')}
                    value={data?.totals.remaining}
                    lead
                    testId="p1-kpi-remaining"
                />
            </div>

            <div className="mb-3">
                <CountTabs
                    tabs={tabs}
                    value={state.tab}
                    onChange={(tab) => setState({ tab, page: 1 })}
                    testId="p1-promise-tabs"
                />
            </div>

            <div className={selected ? 'grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1fr)_420px]' : ''}>
                <div className="min-w-0">
                    <TableFrame
                        testId="p1-promise-table"
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
                            <ErrorState error={query.error} onRetry={query.reload} />
                        ) : (
                            <Table>
                                <THead>
                                    <Tr>
                                        <Th>{t('p1.col.case')}</Th>
                                        <Th>{t('p1.col.state')}</Th>
                                        <Th>{t('p1.col.dueDate')}</Th>
                                        <Th className="text-end">{t('p1.col.amount')}</Th>
                                        <Th className="text-end">{t('p1.col.allocated')}</Th>
                                        <Th className="text-end">{t('p1.col.remaining')}</Th>
                                        <Th>{t('p1.col.recorder')}</Th>
                                        <Th>{t('p1.col.owner')}</Th>
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
                                                data-testid="p1-promise-row"
                                                data-promise={p.id}
                                                data-state={p.state}
                                            >
                                                <Td>
                                                    <div className="font-extrabold text-navy-600 dark:text-gray-100">
                                                        {p.case?.name || '—'}
                                                    </div>
                                                    <Ltr className="whitespace-nowrap text-[12.5px] text-gray-600">{p.case?.account_number}</Ltr>
                                                </Td>
                                                <Td>
                                                    <PromiseBadge state={p.state} size="row" />
                                                </Td>
                                                <Td>
                                                    <CivilDate value={p.due_date} />
                                                </Td>
                                                <Td className="text-end">
                                                    <MoneyText value={p.amount} strong />
                                                </Td>
                                                <Td className="text-end">
                                                    <MoneyText value={p.allocated} />
                                                </Td>
                                                <Td className="text-end">
                                                    <MoneyText value={p.remaining} />
                                                </Td>
                                                <Td>{p.recorder?.name || '—'}</Td>
                                                <Td>
                                                    <div>
                                                        {p.case?.owner?.name || (
                                                            <span className="text-gray-500">{t('p1.unassigned')}</span>
                                                        )}
                                                    </div>
                                                    <div dir="auto" className="whitespace-nowrap text-[12px] text-gray-600">{p.case?.portfolio?.name || ''}</div>
                                                </Td>
                                            </Tr>
                                        ))
                                    ) : (
                                        <tr>
                                            <td colSpan={cols}>
                                                <EmptyState
                                                    title={t('p1.promises.emptyTitle')}
                                                    hint={t('p1.promises.emptyHint')}
                                                />
                                            </td>
                                        </tr>
                                    )}
                                </TBody>
                            </Table>
                        )}
                    </TableFrame>
                    {query.error && data ? <ErrorState error={query.error} onRetry={query.reload} /> : null}
                    <Note className="mt-3">{t('p1.promises.footNote')}</Note>
                </div>
                {selected && (
                    <PromisePanel
                        id={selected}
                        onClose={() => setState({ sel: undefined }, { replace: true })}
                        onChanged={query.reload}
                    />
                )}
            </div>
        </div>
    )
}

export default Promises
