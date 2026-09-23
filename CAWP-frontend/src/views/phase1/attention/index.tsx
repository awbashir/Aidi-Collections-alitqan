/**
 * Daily attention queue (`/attention`, permission `attention_queue`).
 *
 * Reads GET /v2/attention: exact counts per reason, one row per case with every
 * reason that applies. Being in the queue authorizes nothing: there are no call,
 * approve or assign buttons here, only links to the screen where that work is
 * done (which checks its own permission). Filters, tab, page and the last opened
 * case live in the URL hash.
 */
import { useMemo } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import Table from '@/components/ui/Table'
import useHashState, { hashCodec } from '@/utils/hooks/useHashState'
import type { HashSchema } from '@/utils/hooks/useHashState'
import { ATTENTION_REASONS, worklistApi } from '@/services/v2'
import type { AttentionItem, AttentionReason } from '@/services/v2'
import { useV2Query } from '@/utils/v2/hooks'
import { formatCount, formatRiyadhDateTime } from '@/utils/v2/format'
import { caseLink } from '@/utils/v2/links'
import { CivilDate, Ltr, MoneyText, RiyadhTime } from '@/components/v2/Display'
import { BucketBadge, ReasonBadge } from '@/components/v2/Badges'
import { EmptyState, ErrorState, Note, SkeletonRows } from '@/components/v2/States'
import { CountTabs, PageHead, PagerFooter, ScopeFilters, TableFrame, rowClass } from '@/components/v2/ListParts'

const { THead, TBody, Tr, Th, Td } = Table

type Tab = 'all' | AttentionReason
const TABS: Tab[] = ['all', ...ATTENTION_REASONS]
const PAGE_SIZE = 25

type HashState = {
    tab: Tab
    owner: string | number | undefined
    contract: string | number | undefined
    portfolio: string | number | undefined
    page: number
    sel: string | number | undefined
}

const schema: HashSchema<HashState> = {
    tab: { key: 'reason', codec: hashCodec.oneOf(TABS), default: 'all' },
    owner: { key: 'owner', codec: hashCodec.id(), default: undefined },
    contract: { key: 'contract', codec: hashCodec.id(), default: undefined },
    portfolio: { key: 'portfolio', codec: hashCodec.id(), default: undefined },
    page: { key: 'page', codec: hashCodec.int({ min: 1, max: 100000 }), default: 1 },
    sel: { key: 'case', codec: hashCodec.id(), default: undefined, history: 'replace' },
}

/** The screen where each reason's work is done. The link carries no authority. */
const workLink = (reason: AttentionReason, caseId: string) => {
    switch (reason) {
        case 'late_follow_up':
        case 'unscheduled':
            return caseLink(caseId, 'followUps')
        case 'missed_promise':
            return caseLink(caseId, 'promises')
        case 'pending_approval':
            return `/payment-approvals#status=pending&case=${encodeURIComponent(caseId)}`
        case 'unassigned':
        default:
            return caseLink(caseId, 'details')
    }
}

const FactLine = ({ item, reason }: { item: AttentionItem; reason: AttentionReason }) => {
    const { t } = useTranslation()
    const f = item.facts
    let parts: React.ReactNode[] = []
    if (reason === 'late_follow_up' && f.late_follow_up) {
        parts = [
            t('p1.attention.fact.late', { days: f.late_follow_up.days_late }),
            <CivilDate key="d" value={f.late_follow_up.due_date} />,
        ]
    } else if (reason === 'missed_promise' && f.missed_promise) {
        parts = [
            t('p1.attention.fact.missed', { count: f.missed_promise.count }),
            <CivilDate key="d" value={f.missed_promise.oldest_due_date} />,
            <span key="a">
                {t('p1.attention.fact.unallocated')} <MoneyText value={f.missed_promise.unallocated_amount} />
            </span>,
        ]
    } else if (reason === 'pending_approval' && f.pending_approval) {
        parts = [
            t('p1.attention.fact.pending', { count: f.pending_approval.count }),
            <MoneyText key="a" value={f.pending_approval.amount} />,
        ]
    } else if (reason === 'unassigned') {
        parts = [t('p1.attention.fact.unassigned')]
    } else if (reason === 'unscheduled') {
        parts = f.unscheduled?.last_comment_at
            ? [t('p1.attention.fact.lastNote'), <RiyadhTime key="d" iso={f.unscheduled.last_comment_at} />]
            : [t('p1.attention.fact.neverContacted')]
    }
    return (
        <Link
            to={workLink(reason, item.case.id)}
            onClick={(e) => e.stopPropagation()}
            className="flex flex-wrap items-center gap-x-1.5 py-0.5 text-[12.5px] text-azure-600 hover:underline"
            data-testid={`p1-work-link-${reason}`}
        >
            {parts.map((p, i) => (
                <span key={i} className="whitespace-nowrap">
                    {i > 0 && <span className="me-1.5 text-gray-400">·</span>}
                    {p}
                </span>
            ))}
        </Link>
    )
}

const AttentionQueue = () => {
    const { t } = useTranslation()
    const navigate = useNavigate()
    const { state, setState } = useHashState(schema)
    const reason = state.tab === 'all' ? undefined : state.tab

    const query = useV2Query(
        (signal) =>
            worklistApi.attention(
                {
                    reason,
                    owner: state.owner ? String(state.owner) : undefined,
                    contract_id: state.contract ? String(state.contract) : undefined,
                    portfolio_id: state.portfolio ? String(state.portfolio) : undefined,
                    page: state.page,
                    page_size: PAGE_SIZE,
                },
                signal
            ),
        [reason, state.owner, state.contract, state.portfolio, state.page]
    )
    const data = query.data

    const tabs = useMemo(
        () =>
            TABS.map((tab) => ({
                value: tab,
                label: t(tab === 'all' ? 'p1.attention.all' : `p1.reason.${tab}`),
                // "All" is the distinct number of cases, known only for the unfiltered list.
                count:
                    tab === 'all'
                        ? state.tab === 'all' && data
                            ? data.total
                            : undefined
                        : data
                        ? data.counts[tab]
                        : undefined,
            })),
        [data, state.tab, t]
    )

    const openCase = (item: AttentionItem) => {
        setState({ sel: item.case.id }, { replace: true })
        navigate(caseLink(item.case.id, 'details'))
    }

    const cols = 7
    return (
        <div data-testid="p1-attention">
            <PageHead
                title={t('p1.attention.title')}
                subtitle={
                    data ? (
                        <span dir="auto">
                            {t('p1.list.asOf', { at: formatRiyadhDateTime(data.as_of.at) })}
                        </span>
                    ) : undefined
                }
                onRefresh={query.reload}
                refreshing={query.loading && !!data}
            />

            <ScopeFilters
                owner={state.owner ? String(state.owner) : undefined}
                contract={state.contract ? String(state.contract) : undefined}
                portfolio={state.portfolio ? String(state.portfolio) : undefined}
                onChange={(patch) => setState({ ...patch, page: 1 })}
            />

            <div className="mb-3">
                <CountTabs
                    tabs={tabs}
                    value={state.tab}
                    onChange={(tab) => setState({ tab, page: 1 })}
                    testId="p1-attention-tabs"
                />
            </div>

            <TableFrame
                testId="p1-attention-table"
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
                                <Th>{t('p1.col.reasons')}</Th>
                                <Th>{t('p1.col.facts')}</Th>
                                <Th>{t('p1.col.owner')}</Th>
                                <Th>{t('p1.col.portfolio')}</Th>
                                <Th>{t('p1.col.openFollowUp')}</Th>
                                <Th className="text-end">{t('p1.col.originalDebt')}</Th>
                            </Tr>
                        </THead>
                        <TBody>
                            {query.loading && !data ? (
                                <SkeletonRows cols={cols} />
                            ) : data && data.items.length > 0 ? (
                                data.items.map((item) => (
                                    <Tr
                                        key={item.case.id}
                                        className={rowClass(String(state.sel) === item.case.id)}
                                        aria-current={String(state.sel) === item.case.id ? 'true' : undefined}
                                        onClick={() => openCase(item)}
                                        data-testid="p1-attention-row"
                                        data-case={item.case.id}
                                    >
                                        <Td>
                                            <div className="font-extrabold text-navy-600 dark:text-gray-100">
                                                {item.case.name}
                                            </div>
                                            <Ltr className="text-[12.5px] text-gray-600">{item.case.accountNumber}</Ltr>
                                        </Td>
                                        <Td>
                                            <div className="flex flex-wrap gap-1.5" data-testid="p1-reasons">
                                                {item.reasons.map((r) => (
                                                    <ReasonBadge key={r} reason={r} />
                                                ))}
                                            </div>
                                        </Td>
                                        <Td className="min-w-[220px]">
                                            {item.reasons.map((r) => (
                                                <FactLine key={r} item={item} reason={r} />
                                            ))}
                                        </Td>
                                        <Td>{item.case.owner?.name || <span className="text-gray-500">{t('p1.unassigned')}</span>}</Td>
                                        <Td>
                                            <div>{item.case.portfolio?.name || '—'}</div>
                                            <div className="text-[12px] text-gray-600">{item.case.contract?.name || ''}</div>
                                        </Td>
                                        <Td>
                                            {item.open_follow_up ? (
                                                <div className="flex flex-col items-start gap-1">
                                                    <RiyadhTime iso={item.open_follow_up.due_at} />
                                                    <BucketBadge bucket={item.open_follow_up.bucket} />
                                                </div>
                                            ) : (
                                                <span className="text-gray-500">{t('p1.none')}</span>
                                            )}
                                        </Td>
                                        <Td className="text-end">
                                            <MoneyText value={item.case.original_amount} />
                                        </Td>
                                    </Tr>
                                ))
                            ) : (
                                <tr>
                                    <td colSpan={cols}>
                                        <EmptyState
                                            title={t('p1.attention.emptyTitle')}
                                            hint={t('p1.attention.emptyHint')}
                                        />
                                    </td>
                                </tr>
                            )}
                        </TBody>
                    </Table>
                )}
            </TableFrame>
            {query.error && data ? <ErrorState error={query.error} onRetry={query.reload} /> : null}
            <Note className="mt-3">
                {t('p1.attention.noAuthority')}
                {data ? ` · ${t('p1.list.totalCases', { total: formatCount(data.total) })}` : ''}
            </Note>
        </div>
    )
}

export default AttentionQueue
