/**
 * Internal requests (`/requests`, derived key `requests_any` = requests |
 * requests_legal | requests_finance | requests_operations).
 *
 * GET /v2/requests: department tabs with exact `department_counts` (every
 * filter except department), status chips with exact `counts` (every filter
 * except status), requester / reviewer ("me" or a user), case and Riyadh
 * created-date filters, newest first, paged. Visibility is the API's: the
 * requester sees their own, a department reviewer that department's requests
 * in their case scope, a supervisor their team's. The selected request opens
 * a side panel (GET /v2/requests/<id>) with its trail, comments and the
 * actions the API allows. All list state lives in the URL hash.
 */
import { useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { HiOutlineX } from 'react-icons/hi'
import Table from '@/components/ui/Table'
import useHashState, { hashCodec } from '@/utils/hooks/useHashState'
import type { HashSchema } from '@/utils/hooks/useHashState'
import { REQUEST_DEPARTMENTS, REQUEST_STATUSES, describeP3Error, requestsApi } from '@/services/v2'
import type { RequestDepartment, RequestStatus, RequestViewer } from '@/services/v2'
import { useV2Query } from '@/utils/v2/hooks'
import { formatCount, formatRiyadhDateTime } from '@/utils/v2/format'
import { caseLink } from '@/utils/v2/links'
import { CivilDate, Ltr, MoneyText, RiyadhTime } from '@/components/v2/Display'
import { EmptyState, ErrorState, Note, SkeletonLines, SkeletonRows } from '@/components/v2/States'
import {
    CountTabs,
    FilterSelect,
    PageHead,
    PagerFooter,
    TableFrame,
    rowClass,
    useScopeOptions,
} from '@/components/v2/ListParts'
import { DateFilter, SidePanel } from '@/views/phase1/money/listParts'
import RequestDetail from './RequestDetail'
import { DepartmentLabel, RequestStatusBadge } from './parts'

const { THead, TBody, Tr, Th, Td } = Table

type DeptTab = 'all' | RequestDepartment
type StatusTab = 'all' | RequestStatus
const DEPT_TABS: DeptTab[] = ['all', ...REQUEST_DEPARTMENTS]
const STATUS_TABS: StatusTab[] = ['all', ...REQUEST_STATUSES]
const PAGE_SIZE = 25

type HashState = {
    dept: DeptTab
    status: StatusTab
    requester: string | number | undefined
    reviewer: string | number | undefined
    caseId: string | number | undefined
    from: string | undefined
    to: string | undefined
    page: number
    sel: string | number | undefined
}

const schema: HashSchema<HashState> = {
    dept: { key: 'dept', codec: hashCodec.oneOf(DEPT_TABS), default: 'all' },
    status: { key: 'status', codec: hashCodec.oneOf(STATUS_TABS), default: 'all' },
    requester: { key: 'requester', codec: hashCodec.id(), default: undefined },
    reviewer: { key: 'reviewer', codec: hashCodec.id(), default: undefined },
    caseId: { key: 'case', codec: hashCodec.id(), default: undefined },
    from: { key: 'from', codec: hashCodec.date(), default: undefined },
    to: { key: 'to', codec: hashCodec.date(), default: undefined },
    page: { key: 'page', codec: hashCodec.int({ min: 1, max: 100000 }), default: 1 },
    sel: { key: 'request', codec: hashCodec.id(), default: undefined, history: 'replace' },
}

const str = (v: string | number | undefined) => (v === undefined ? undefined : String(v))

const RequestPanel = ({
    id,
    viewer,
    onClose,
    onChanged,
}: {
    id: string
    viewer: RequestViewer | null
    onClose: () => void
    onChanged: () => void
}) => {
    const { t } = useTranslation()
    const q = useV2Query((signal) => requestsApi.detail(id, signal), [id])
    return (
        <SidePanel title={q.data?.subject || t('p3.req.detailTitle')} onClose={onClose} testId="p3-req-panel">
            {q.loading && !q.data ? (
                <SkeletonLines rows={8} />
            ) : q.error && !q.data ? (
                <ErrorState error={q.error} onRetry={q.reload} describe={describeP3Error} />
            ) : q.data ? (
                <RequestDetail
                    key={`${q.data.id}:${q.data.revision}:${q.data.events.length}`}
                    request={q.data}
                    viewer={viewer}
                    onReload={q.reload}
                    onChanged={() => {
                        q.reload()
                        onChanged()
                    }}
                />
            ) : null}
        </SidePanel>
    )
}

const Requests = () => {
    const { t } = useTranslation()
    const { state, setState } = useHashState(schema)
    const users = useScopeOptions()
    const dept = state.dept === 'all' ? undefined : state.dept
    const status = state.status === 'all' ? undefined : state.status

    const query = useV2Query(
        (signal) =>
            requestsApi.list(
                {
                    department: dept,
                    status,
                    requester: str(state.requester),
                    reviewer: str(state.reviewer),
                    case_id: str(state.caseId),
                    date_from: state.from,
                    date_to: state.to,
                    page: state.page,
                    pageSize: PAGE_SIZE,
                },
                signal
            ),
        [dept, status, state.requester, state.reviewer, state.caseId, state.from, state.to, state.page]
    )
    const data = query.data
    const viewer = data?.viewer || null

    const deptTabs = useMemo(
        () =>
            DEPT_TABS.map((tab) => ({
                value: tab,
                label: tab === 'all' ? t('p3.req.allDepartments') : t(`p3.req.dept.${tab}`),
                count: data
                    ? tab === 'all'
                        ? REQUEST_DEPARTMENTS.reduce((n, d) => n + data.department_counts[d], 0)
                        : data.department_counts[tab]
                    : undefined,
            })),
        [data, t]
    )
    const statusTabs = useMemo(
        () =>
            STATUS_TABS.map((tab) => ({
                value: tab,
                label: tab === 'all' ? t('p3.req.allStatuses') : t(`p3.req.status.${tab}`),
                count: data
                    ? tab === 'all'
                        ? REQUEST_STATUSES.reduce((n, s) => n + data.counts[s], 0)
                        : data.counts[tab]
                    : undefined,
            })),
        [data, t]
    )

    const personOptions = useMemo(
        () => [{ value: 'me', label: String(t('p3.req.me')) }, ...users.owners],
        [users.owners, t]
    )
    const selected = str(state.sel)
    const caseFilter = str(state.caseId)
    const caseName = caseFilter ? data?.items.find((r) => r.case_id === caseFilter)?.case : undefined
    const cols = 8

    return (
        <div data-testid="p3-requests">
            <PageHead
                title={t('p3.req.title')}
                subtitle={
                    data ? (
                        <span dir="auto">
                            {t('p1.list.asOf', { at: formatRiyadhDateTime(data.as_of.at) })}
                            {viewer && viewer.reviewer_departments.length > 0 && (
                                <>
                                    {' · '}
                                    {t('p3.req.youReview', {
                                        departments: viewer.reviewer_departments
                                            .map((d) => t(`p3.req.dept.${d}`))
                                            .join(String(t('p3.req.listSep'))),
                                    })}
                                </>
                            )}
                        </span>
                    ) : undefined
                }
                onRefresh={query.reload}
                refreshing={query.loading && !!data}
            />

            <div className="mb-3">
                <CountTabs
                    tabs={deptTabs}
                    value={state.dept}
                    onChange={(tab) => setState({ dept: tab, page: 1 })}
                    testId="p3-req-dept-tabs"
                />
            </div>

            <div className="mb-4 flex flex-wrap items-end gap-3" data-testid="p3-req-filters">
                <FilterSelect
                    label={t('p3.fields.requester')}
                    options={personOptions}
                    loading={users.loading}
                    value={str(state.requester)}
                    testId="p3-req-requester"
                    onChange={(v) => setState({ requester: v, page: 1 })}
                />
                <FilterSelect
                    label={t('p3.fields.reviewer')}
                    options={personOptions}
                    loading={users.loading}
                    value={str(state.reviewer)}
                    testId="p3-req-reviewer"
                    onChange={(v) => setState({ reviewer: v, page: 1 })}
                />
                <DateFilter
                    label={t('p3.req.createdFrom')}
                    value={state.from}
                    max={state.to}
                    testId="p3-req-from"
                    onChange={(v) => setState({ from: v, page: 1 })}
                />
                <DateFilter
                    label={t('p3.req.createdTo')}
                    value={state.to}
                    min={state.from}
                    testId="p3-req-to"
                    onChange={(v) => setState({ to: v, page: 1 })}
                />
                {caseFilter && (
                    <div data-testid="p3-req-case-chip">
                        <div className="text-eyebrow mb-1">{t('p3.req.col.case')}</div>
                        <span className="inline-flex h-9 items-center gap-2 rounded-md border border-gray-300 px-3 text-[13px] dark:border-night-border">
                            {caseName && !caseName.missing ? (
                                <>
                                    <span dir="auto">{caseName.name}</span>
                                    <Ltr className="text-gray-600">{caseName.accountNumber}</Ltr>
                                </>
                            ) : (
                                <Ltr>{caseFilter.slice(0, 8)}…</Ltr>
                            )}
                            <button
                                type="button"
                                aria-label={String(t('p1.filter.clear'))}
                                className="rounded p-0.5 text-gray-600 hover:bg-gray-100 dark:hover:bg-night-raised"
                                onClick={() => setState({ caseId: undefined, page: 1 })}
                            >
                                <HiOutlineX />
                            </button>
                        </span>
                    </div>
                )}
            </div>

            <div className="mb-3">
                <CountTabs
                    secondary
                    tabs={statusTabs}
                    value={state.status}
                    onChange={(tab) => setState({ status: tab, page: 1 })}
                    testId="p3-req-status-tabs"
                />
            </div>

            <div className={selected ? 'grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1fr)_460px]' : ''}>
                <div className="min-w-0">
                    <TableFrame
                        testId="p3-req-table"
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
                            <ErrorState error={query.error} onRetry={query.reload} describe={describeP3Error} />
                        ) : (
                            <Table>
                                <THead>
                                    <Tr>
                                        <Th>{t('p3.req.col.request')}</Th>
                                        <Th>{t('p3.fields.status')}</Th>
                                        <Th>{t('p3.req.col.case')}</Th>
                                        <Th>{t('p3.req.col.requester')}</Th>
                                        <Th>{t('p3.req.col.reviewer')}</Th>
                                        <Th>{t('p3.req.col.created')}</Th>
                                        <Th>{t('p3.req.col.lastActivity')}</Th>
                                        <Th className="text-end">{t('p3.fields.amount')}</Th>
                                    </Tr>
                                </THead>
                                <TBody>
                                    {query.loading && !data ? (
                                        <SkeletonRows cols={cols} />
                                    ) : data && data.items.length > 0 ? (
                                        data.items.map((r) => (
                                            <Tr
                                                key={r.id}
                                                className={rowClass(selected === r.id)}
                                                aria-current={selected === r.id ? 'true' : undefined}
                                                onClick={() => setState({ sel: r.id }, { replace: true })}
                                                data-testid="p3-req-row"
                                                data-request={r.id}
                                                data-status={r.status}
                                                data-department={r.department}
                                            >
                                                <Td className="min-w-[220px]">
                                                    <div dir="auto" className="font-extrabold text-navy-600 dark:text-gray-100">
                                                        {r.subject}
                                                    </div>
                                                    <div className="mt-1 flex flex-wrap items-center gap-2 text-[12px] text-gray-600">
                                                        <DepartmentLabel department={r.department} />
                                                        {r.comment_count > 0 && (
                                                            <span>{t('p3.req.commentsN', { count: r.comment_count })}</span>
                                                        )}
                                                        {r.attachment_count > 0 && (
                                                            <span>{t('p3.req.attachedN', { count: r.attachment_count })}</span>
                                                        )}
                                                    </div>
                                                </Td>
                                                <Td>
                                                    <RequestStatusBadge status={r.status} size="row" />
                                                </Td>
                                                <Td>
                                                    {r.case.missing ? (
                                                        <span className="text-gray-600">{t('p3.req.caseMissing')}</span>
                                                    ) : (
                                                        <>
                                                            <Link
                                                                to={caseLink(r.case_id, 'requests')}
                                                                onClick={(e) => e.stopPropagation()}
                                                                className="font-extrabold text-azure-600 hover:underline"
                                                            >
                                                                <span dir="auto">{r.case.name}</span>
                                                            </Link>
                                                            <div>
                                                                <Ltr className="whitespace-nowrap text-[12.5px] text-gray-600">
                                                                    {r.case.accountNumber}
                                                                </Ltr>
                                                            </div>
                                                        </>
                                                    )}
                                                </Td>
                                                <Td>{r.requested_by?.name || '—'}</Td>
                                                <Td>
                                                    {r.reviewer?.name || (
                                                        <span className="text-gray-500">{t('p3.req.notTaken')}</span>
                                                    )}
                                                </Td>
                                                <Td>
                                                    <CivilDate value={r.created_date} />
                                                </Td>
                                                <Td>
                                                    <RiyadhTime iso={r.last_event_at} />
                                                </Td>
                                                <Td className="text-end">
                                                    {r.amount ? <MoneyText value={r.amount} /> : <span className="text-gray-500">—</span>}
                                                </Td>
                                            </Tr>
                                        ))
                                    ) : (
                                        <tr>
                                            <td colSpan={cols}>
                                                <EmptyState title={t('p3.req.emptyTitle')} hint={t('p3.req.emptyHint')} />
                                            </td>
                                        </tr>
                                    )}
                                </TBody>
                            </Table>
                        )}
                    </TableFrame>
                    {query.error && data ? (
                        <ErrorState error={query.error} onRetry={query.reload} describe={describeP3Error} />
                    ) : null}
                    <Note className="mt-3">
                        {t('p3.req.footNote')}
                        {data ? ` · ${t('p3.req.totalN', { total: formatCount(data.total) })}` : ''}
                    </Note>
                    <Note className="mt-1">
                        <span data-testid="p3-req-baseline-note">{t('p3.req.noBaseline')}</span>
                    </Note>
                </div>
                {selected && (
                    <RequestPanel
                        id={selected}
                        viewer={viewer}
                        onClose={() => setState({ sel: undefined }, { replace: true })}
                        onChanged={query.reload}
                    />
                )}
            </div>
        </div>
    )
}

export default Requests
