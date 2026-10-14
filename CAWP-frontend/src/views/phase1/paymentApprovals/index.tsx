/**
 * Payment approval desk (`/payment-approvals`, route key `payments_approve`).
 *
 * GET /v2/payments with a status filter (pending by default). The desk returns
 * `total` / `total_amount` for the active filter only, so each tab's exact
 * count comes from its own page_size=1 call with the same filters. Approve /
 * reject (payments_approve) and reverse (payments_reverse) send the loaded
 * revision; the resulting case balance is shown inline and in a toast.
 * The attention queue links here with `#status=pending&case=<id>`.
 */
import { useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { HiOutlineX } from 'react-icons/hi'
import Button from '@/components/ui/Button'
import Table from '@/components/ui/Table'
import ToastNotification from '@/components/shared/myComponents/ToastNotification'
import useHashState, { hashCodec } from '@/utils/hooks/useHashState'
import type { HashSchema } from '@/utils/hooks/useHashState'
import { moneyApi } from '@/services/v2'
import type { DeskQuery, Payment } from '@/services/v2'
import { usePermissions, useV2Query } from '@/utils/v2/hooks'
import { formatCount, formatMoney } from '@/utils/v2/format'
import { caseLink } from '@/utils/v2/links'
import { CivilDate, Ltr, MoneyText, RiyadhTime } from '@/components/v2/Display'
import { PaymentBadge } from '@/components/v2/Badges'
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
import { CasePaymentHistory } from '@/views/phase1/money/PaymentTimeline'
import { DateFilter, SidePanel } from '@/views/phase1/money/listParts'
import { absMoney } from '@/views/phase1/money/moneyMath'
import { DecisionDialogs } from './PaymentDialogs'
import { ReceiptLinks } from '@/views/paymentAttachments'
import type { DecisionKind, DecisionResult } from './PaymentDialogs'

const { THead, TBody, Tr, Th, Td } = Table

const STATUS_TABS = ['pending', 'approved', 'rejected', 'reversed', 'all'] as const
type Tab = (typeof STATUS_TABS)[number]
const SORTS = ['-created_at', 'created_at', '-payment_date', 'payment_date', '-amount', 'amount'] as const
type Sort = (typeof SORTS)[number]
const PAGE_SIZE = 25

type HashState = {
    tab: Tab
    caseId: string | number | undefined
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
    tab: { key: 'status', codec: hashCodec.oneOf(STATUS_TABS), default: 'pending' },
    caseId: { key: 'case', codec: hashCodec.id(), default: undefined },
    owner: { key: 'owner', codec: hashCodec.id(), default: undefined },
    contract: { key: 'contract', codec: hashCodec.id(), default: undefined },
    portfolio: { key: 'portfolio', codec: hashCodec.id(), default: undefined },
    recorder: { key: 'recorder', codec: hashCodec.id(), default: undefined },
    from: { key: 'from', codec: hashCodec.date(), default: undefined },
    to: { key: 'to', codec: hashCodec.date(), default: undefined },
    sort: { key: 'sort', codec: hashCodec.oneOf(SORTS), default: '-created_at' },
    page: { key: 'page', codec: hashCodec.int({ min: 1, max: 100000 }), default: 1 },
    sel: { key: 'payment', codec: hashCodec.id(), default: undefined, history: 'replace' },
}

const str = (v: string | number | undefined) => (v === undefined ? undefined : String(v))

type Action = { kind: DecisionKind; payment: Payment } | null

const RowActions = ({
    p,
    perms,
    onAction,
}: {
    p: Payment
    perms: Record<string, boolean>
    onAction: (a: Action) => void
}) => {
    const { t } = useTranslation()
    const stop = (fn: () => void) => (e: React.MouseEvent) => {
        e.stopPropagation()
        fn()
    }
    const canDecide = p.status === 'pending' && !!perms.payments_approve
    const canReverse = p.status === 'approved' && !!perms.payments_reverse
    if (!canDecide && !canReverse) return <span className="text-gray-400">—</span>
    return (
        <div className="flex flex-nowrap items-center justify-end gap-1.5">
            {canDecide && (
                <>
                    <Button
                        size="xs"
                        variant="solid"
                        onClick={stop(() => onAction({ kind: 'approve', payment: p }))}
                        data-testid="p1-approve"
                    >
                        {t('p1.desk.approve')}
                    </Button>
                    <Button size="xs" onClick={stop(() => onAction({ kind: 'reject', payment: p }))} data-testid="p1-reject">
                        {t('p1.desk.reject')}
                    </Button>
                </>
            )}
            {canReverse && (
                <Button size="xs" onClick={stop(() => onAction({ kind: 'reverse', payment: p }))} data-testid="p1-reverse">
                    {t('p1.desk.reverse')}
                </Button>
            )}
        </div>
    )
}

const ResultBanner = ({ result, onClose }: { result: DecisionResult; onClose: () => void }) => {
    const { t } = useTranslation()
    const b = result.balance
    return (
        <div
            role="status"
            className="mb-4 flex items-start justify-between gap-3 rounded-lg border border-gray-300 bg-white px-5 py-3 dark:border-night-border dark:bg-night-card"
            data-testid="p1-decision-result"
            data-kind={result.kind}
        >
            <div className="min-w-0 text-[13px]">
                <div className="font-extrabold text-navy-600 dark:text-gray-100">
                    {t(`p1.desk.done.${result.kind}`, {
                        amount: formatMoney(result.payment.amount),
                        case: result.payment.case?.name || '',
                    })}
                </div>
                <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-gray-700 dark:text-night-body">
                    <span data-testid="p1-result-outstanding" data-value={b.outstanding}>
                        {b.overpaid ? t('p1.balance.overpaidBy') : t('p1.balance.outstanding')}:{' '}
                        <MoneyText value={b.overpaid ? absMoney(b.outstanding) : b.outstanding} strong />
                    </span>
                    <span>
                        {t('p1.balance.original')}: <MoneyText value={b.original} />
                    </span>
                    <span>
                        {t('p1.balance.approved')}: <MoneyText value={b.approved} />
                    </span>
                    <span>
                        {t('p1.balance.pending')}: <MoneyText value={b.pending} />
                    </span>
                    <span>
                        {t('p1.balance.reversed')}: <MoneyText value={b.reversed} />
                    </span>
                    {result.kind === 'approve' && (
                        <span>{t('p1.desk.autoAllocated', { count: result.allocations ?? 0 })}</span>
                    )}
                    {result.kind === 'reverse' && <span>{t('p1.desk.voided', { count: result.voided ?? 0 })}</span>}
                </div>
            </div>
            <button
                type="button"
                onClick={onClose}
                aria-label={String(t('p1.action.close'))}
                className="rounded p-1 text-gray-600 hover:bg-gray-100 dark:hover:bg-night-raised"
            >
                <HiOutlineX />
            </button>
        </div>
    )
}

const PaymentPanel = ({
    id,
    refreshKey,
    perms,
    onAction,
    onClose,
}: {
    id: string
    refreshKey: number
    perms: Record<string, boolean>
    onAction: (a: Action) => void
    onClose: () => void
}) => {
    const { t } = useTranslation()
    const q = useV2Query((signal) => moneyApi.payment(id, signal), [id, refreshKey])
    const p = q.data
    return (
        <SidePanel title={p?.case?.name || t('p1.desk.detailTitle')} onClose={onClose} testId="p1-payment-panel">
            {q.loading && !p ? (
                <SkeletonLines rows={8} />
            ) : q.error && !p ? (
                <ErrorState error={q.error} onRetry={q.reload} />
            ) : p ? (
                <>
                    <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                        {p.case && (
                            <Link
                                to={caseLink(p.case_id, 'payments')}
                                className="text-[13px] font-extrabold text-azure-600 hover:underline"
                                data-testid="p1-payment-case-link"
                            >
                                {t('p1.action.openCase')} · <Ltr>{p.case.account_number}</Ltr>
                            </Link>
                        )}
                        <RowActions p={p} perms={perms} onAction={onAction} />
                    </div>
                    <CasePaymentHistory caseId={p.case_id} paymentId={p.id} refreshKey={refreshKey} />
                </>
            ) : null}
        </SidePanel>
    )
}

const PaymentApprovals = () => {
    const { t } = useTranslation()
    const perms = usePermissions()
    const { state, setState } = useHashState(schema)
    const users = useScopeOptions()
    const [action, setAction] = useState<Action>(null)
    const [result, setResult] = useState<DecisionResult | null>(null)
    const [refreshKey, setRefreshKey] = useState(0)

    const filters: DeskQuery = {
        case_id: str(state.caseId),
        owner_id: str(state.owner),
        contract_id: str(state.contract),
        portfolio_id: str(state.portfolio),
        recorder_id: str(state.recorder),
        date_from: state.from,
        date_to: state.to,
    }
    const filterDeps = [state.caseId, state.owner, state.contract, state.portfolio, state.recorder, state.from, state.to]

    const query = useV2Query(
        (signal) =>
            moneyApi.desk({ ...filters, status: state.tab, sort: state.sort, page: state.page, page_size: PAGE_SIZE }, signal),
        [...filterDeps, state.tab, state.sort, state.page, refreshKey]
    )
    const data = query.data

    // Exact per-tab totals: one page_size=1 call per status with the same filters.
    const counts = useV2Query(
        async (signal) => {
            const res = await Promise.allSettled(
                STATUS_TABS.map((s) => moneyApi.desk({ ...filters, status: s, page: 1, page_size: 1 }, signal))
            )
            const out: Partial<Record<Tab, { total: number; amount: string }>> = {}
            res.forEach((r, i) => {
                if (r.status === 'fulfilled') out[STATUS_TABS[i]] = { total: r.value.total, amount: r.value.total_amount }
            })
            return out
        },
        [...filterDeps, refreshKey]
    )

    const tabs = useMemo(
        () =>
            STATUS_TABS.map((s) => {
                const c = s === state.tab && data ? { total: data.total, amount: data.total_amount } : counts.data?.[s]
                return {
                    value: s,
                    label: t(s === 'all' ? 'p1.desk.all' : `p1.paymentStatus.${s}`),
                    count: c ? c.total : undefined,
                    sub: c ? <span dir="ltr">{formatMoney(c.amount)}</span> : undefined,
                }
            }),
        [counts.data, data, state.tab, t]
    )

    const sortOptions = SORTS.map((s) => ({ value: s, label: t(`p1.desk.sort.${s.replace('-', 'desc_')}`) }))
    const selected = str(state.sel)
    // Remember the filtered case's name once seen, so the chip keeps it when the tab is empty.
    const seenNames = useRef<Record<string, string>>({})
    data?.items.forEach((i) => {
        if (i.case?.name) seenNames.current[i.case_id] = i.case.name
    })
    const caseName = state.caseId !== undefined ? seenNames.current[String(state.caseId)] : undefined

    const done = (r: DecisionResult) => {
        setResult(r)
        setRefreshKey((n) => n + 1)
        ToastNotification(
            t('p1.desk.toastBalance', {
                outstanding: formatMoney(r.balance.outstanding),
                case: r.payment.case?.name || '',
            }),
            t(`p1.desk.toastTitle.${r.kind}`),
            'success',
            5000
        )
    }

    const cols = 10
    return (
        <div data-testid="p1-payment-approvals">
            <PageHead
                title={t('p1.desk.title')}
                subtitle={t('p1.desk.subtitle')}
                onRefresh={() => setRefreshKey((n) => n + 1)}
                refreshing={query.loading && !!data}
            />

            {state.caseId !== undefined && (
                <div className="mb-3 flex items-center gap-2" data-testid="p1-case-filter">
                    <span className="inline-flex h-7 items-center gap-2 rounded-pill border border-azure-600 bg-azure-50 pe-2 ps-3 text-[12.5px] font-extrabold text-azure-700 dark:bg-night-raised">
                        {t('p1.desk.caseFilter')}: {caseName || <Ltr>{String(state.caseId).slice(0, 8)}</Ltr>}
                        <button
                            type="button"
                            aria-label={String(t('p1.filter.clear'))}
                            onClick={() => setState({ caseId: undefined, page: 1 })}
                            data-testid="p1-case-filter-clear"
                        >
                            <HiOutlineX />
                        </button>
                    </span>
                    <Link to={caseLink(String(state.caseId), 'payments')} className="text-[12.5px] text-azure-600 hover:underline">
                        {t('p1.action.openCase')}
                    </Link>
                </div>
            )}

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
                    label={t('p1.desk.dateFrom')}
                    value={state.from}
                    max={state.to}
                    testId="p1-filter-date-from"
                    onChange={(v) => setState({ from: v, page: 1 })}
                />
                <DateFilter
                    label={t('p1.desk.dateTo')}
                    value={state.to}
                    min={state.from}
                    testId="p1-filter-date-to"
                    onChange={(v) => setState({ to: v, page: 1 })}
                />
                <FilterSelect
                    label={t('p1.desk.sortLabel')}
                    options={sortOptions}
                    value={state.sort}
                    width={200}
                    testId="p1-sort"
                    onChange={(v) => setState({ sort: (v as Sort) || '-created_at', page: 1 })}
                />
            </ScopeFilters>

            <div className="mb-3">
                <CountTabs tabs={tabs} value={state.tab} onChange={(tab) => setState({ tab, page: 1 })} testId="p1-desk-tabs" />
            </div>

            {result && <ResultBanner result={result} onClose={() => setResult(null)} />}

            <div className={selected ? 'grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1fr)_420px]' : ''}>
                <div className="min-w-0">
                    <TableFrame
                        testId="p1-desk-table"
                        toolbar={
                            <>
                                <div className="text-[15px] font-extrabold text-navy-600 dark:text-gray-100">
                                    {t(state.tab === 'all' ? 'p1.desk.all' : `p1.paymentStatus.${state.tab}`)}
                                </div>
                                <div
                                    className="text-[12.5px] text-gray-600 tabular-nums"
                                    data-testid="p1-desk-totals"
                                    data-total={data?.total ?? ''}
                                    data-amount={data?.total_amount ?? ''}
                                >
                                    {data
                                        ? t('p1.desk.totals', {
                                              n: formatCount(data.total),
                                              amount: formatMoney(data.total_amount),
                                          })
                                        : '—'}
                                </div>
                            </>
                        }
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
                                        <Th>{t('p1.col.status')}</Th>
                                        <Th className="text-end">{t('p1.col.amount')}</Th>
                                        <Th>{t('p1.col.paymentDate')}</Th>
                                        <Th>{t('p1.col.method')}</Th>
                                        <Th>{t('p7.payAtt.column')}</Th>
                                        <Th>{t('p1.col.recorder')}</Th>
                                        <Th>{t('p1.col.created')}</Th>
                                        <Th>{t('p1.col.decision')}</Th>
                                        <Th className="text-end">{t('p1.col.actions')}</Th>
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
                                                data-testid="p1-desk-row"
                                                data-payment={p.id}
                                                data-status={p.status}
                                                data-case={p.case_id}
                                            >
                                                <Td>
                                                    <div className="font-extrabold text-navy-600 dark:text-gray-100">
                                                        {p.case?.name || '—'}
                                                    </div>
                                                    <Ltr className="whitespace-nowrap text-[12.5px] text-gray-600">{p.case?.account_number}</Ltr>
                                                </Td>
                                                <Td>
                                                    <PaymentBadge status={p.status} size="row" />
                                                </Td>
                                                <Td className="text-end">
                                                    <MoneyText value={p.amount} strong />
                                                </Td>
                                                <Td>
                                                    <CivilDate value={p.payment_civil_date} />
                                                </Td>
                                                <Td>
                                                    <div>{p.collection_type?.name || '—'}</div>
                                                    <div className="text-[12px] text-gray-600">{p.collection_method?.name || ''}</div>
                                                </Td>
                                                <Td data-testid="payatt-desk-cell">
                                                    {p.attachments && p.attachments.length > 0 ? (
                                                        <ReceiptLinks attachments={p.attachments} compact />
                                                    ) : (
                                                        <span className="text-gray-500">—</span>
                                                    )}
                                                </Td>
                                                <Td>{p.recorder?.name || '—'}</Td>
                                                <Td>
                                                    <RiyadhTime iso={p.created_at} />
                                                </Td>
                                                <Td>
                                                    {p.decision ? (
                                                        <div className="text-[12.5px]">
                                                            <div>{p.decision.by?.name || '—'}</div>
                                                            <RiyadhTime iso={p.decision.at} className="text-gray-600" />
                                                        </div>
                                                    ) : (
                                                        <span className="text-gray-500">—</span>
                                                    )}
                                                </Td>
                                                <Td className="text-end">
                                                    <RowActions p={p} perms={perms} onAction={setAction} />
                                                </Td>
                                            </Tr>
                                        ))
                                    ) : (
                                        <tr>
                                            <td colSpan={cols}>
                                                <EmptyState
                                                    title={t('p1.desk.emptyTitle')}
                                                    hint={t('p1.desk.emptyHint')}
                                                />
                                            </td>
                                        </tr>
                                    )}
                                </TBody>
                            </Table>
                        )}
                    </TableFrame>
                    {query.error && data ? <ErrorState error={query.error} onRetry={query.reload} /> : null}
                    <Note className="mt-3">{t('p1.desk.footNote')}</Note>
                </div>
                {selected && (
                    <PaymentPanel
                        id={selected}
                        refreshKey={refreshKey}
                        perms={perms}
                        onAction={setAction}
                        onClose={() => setState({ sel: undefined }, { replace: true })}
                    />
                )}
            </div>

            <DecisionDialogs
                payment={action?.payment || null}
                kind={action?.kind || null}
                onClose={() => setAction(null)}
                onReload={() => setRefreshKey((n) => n + 1)}
                onDone={done}
            />
        </div>
    )
}

export default PaymentApprovals
