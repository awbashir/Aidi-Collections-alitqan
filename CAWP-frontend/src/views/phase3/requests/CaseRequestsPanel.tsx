/**
 * Case detail: the Requests tab (GET /v2/cases/<id>/requests).
 *
 * The case's requests the caller may see, with exact counts per status, a
 * "New request" dialog (key `requests`, the API's `viewer.can_create`), and
 * the selected request's detail (trail, comments, actions) beside the list.
 * The selected request is kept in the URL hash (`#request=<id>`), so a reload
 * or a shared link reopens it.
 */
import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { HiOutlinePlus } from 'react-icons/hi'
import Button from '@/components/ui/Button'
import Table from '@/components/ui/Table'
import ToastNotification from '@/components/shared/myComponents/ToastNotification'
import useHashState, { hashCodec } from '@/utils/hooks/useHashState'
import type { HashSchema } from '@/utils/hooks/useHashState'
import { REQUEST_STATUSES, describeP3Error, requestsApi } from '@/services/v2'
import type { RequestStatus } from '@/services/v2'
import type { CasePanelProps } from '@/views/phase1/caseTypes'
import { useV2Query } from '@/utils/v2/hooks'
import { CivilDate, MoneyText, RiyadhTime } from '@/components/v2/Display'
import { EmptyState, ErrorState, Note, SkeletonLines, SkeletonRows } from '@/components/v2/States'
import { CountTabs, TableFrame, rowClass } from '@/components/v2/ListParts'
import { SidePanel } from '@/views/phase1/money/listParts'
import NewRequestDialog from './NewRequestDialog'
import RequestDetail from './RequestDetail'
import { DepartmentLabel, RequestStatusBadge } from './parts'

const { THead, TBody, Tr, Th, Td } = Table

type StatusTab = 'all' | RequestStatus
const STATUS_TABS: StatusTab[] = ['all', ...REQUEST_STATUSES]

type HashState = { status: StatusTab; sel: string | number | undefined }
const schema: HashSchema<HashState> = {
    status: { key: 'rstatus', codec: hashCodec.oneOf(STATUS_TABS), default: 'all' },
    sel: { key: 'request', codec: hashCodec.id(), default: undefined, history: 'replace' },
}

const CaseRequestsPanel = ({ caseData }: CasePanelProps) => {
    const { t } = useTranslation()
    const { state, setState } = useHashState(schema)
    const [creating, setCreating] = useState(false)
    const status = state.status === 'all' ? undefined : state.status
    const list = useV2Query(
        (signal) => requestsApi.caseList(caseData.id, { status, pageSize: 50 }, signal),
        [caseData.id, status]
    )
    const selected = state.sel === undefined ? undefined : String(state.sel)
    const detail = useV2Query(
        selected ? (signal) => requestsApi.detail(selected, signal) : null,
        [selected]
    )
    const data = list.data
    const viewer = data?.viewer || null

    const tabs = useMemo(
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
    const cols = 6

    return (
        <div data-testid="p3-case-requests">
            <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                <div>
                    <h6 className="mb-0 text-[15px] font-extrabold text-navy-600 dark:text-gray-100">
                        {t('p3.req.caseTitle')}
                    </h6>
                    <div className="text-[12.5px] text-gray-600">{t('p3.req.caseSubtitle')}</div>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                    <Link
                        to={`/requests#case=${encodeURIComponent(caseData.id)}`}
                        className="text-[13px] font-extrabold text-azure-600 hover:underline"
                        data-testid="p3-req-open-screen"
                    >
                        {t('p3.req.openInScreen')}
                    </Link>
                    {viewer?.can_create && (
                        <Button
                            size="sm"
                            variant="solid"
                            icon={<HiOutlinePlus />}
                            onClick={() => setCreating(true)}
                            data-testid="p3-req-new"
                        >
                            {t('p3.req.new.button')}
                        </Button>
                    )}
                </div>
            </div>

            <div className="mb-3">
                <CountTabs
                    secondary
                    tabs={tabs}
                    value={state.status}
                    onChange={(tab) => setState({ status: tab })}
                    testId="p3-case-req-tabs"
                />
            </div>

            <div className={selected ? 'grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1fr)_460px]' : ''}>
                <div className="min-w-0">
                    <TableFrame testId="p3-case-req-table">
                        {list.error && !data ? (
                            <ErrorState error={list.error} onRetry={list.reload} describe={describeP3Error} />
                        ) : (
                            <Table>
                                <THead>
                                    <Tr>
                                        <Th>{t('p3.req.col.request')}</Th>
                                        <Th>{t('p3.fields.status')}</Th>
                                        <Th>{t('p3.req.col.requester')}</Th>
                                        <Th>{t('p3.req.col.reviewer')}</Th>
                                        <Th>{t('p3.req.col.created')}</Th>
                                        <Th className="text-end">{t('p3.fields.amount')}</Th>
                                    </Tr>
                                </THead>
                                <TBody>
                                    {list.loading && !data ? (
                                        <SkeletonRows cols={cols} rows={3} />
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
                                                <Td className="min-w-[200px]">
                                                    <div dir="auto" className="font-extrabold text-navy-600 dark:text-gray-100">
                                                        {r.subject}
                                                    </div>
                                                    <div className="mt-1">
                                                        <DepartmentLabel department={r.department} />
                                                    </div>
                                                </Td>
                                                <Td>
                                                    <RequestStatusBadge status={r.status} size="row" />
                                                </Td>
                                                <Td>{r.requested_by?.name || '—'}</Td>
                                                <Td>
                                                    {r.reviewer?.name || (
                                                        <span className="text-gray-500">{t('p3.req.notTaken')}</span>
                                                    )}
                                                </Td>
                                                <Td>
                                                    <CivilDate value={r.created_date} />
                                                    <div className="text-[12px] text-gray-600">
                                                        <RiyadhTime iso={r.last_event_at} />
                                                    </div>
                                                </Td>
                                                <Td className="text-end">
                                                    {r.amount ? <MoneyText value={r.amount} /> : <span className="text-gray-500">—</span>}
                                                </Td>
                                            </Tr>
                                        ))
                                    ) : (
                                        <tr>
                                            <td colSpan={cols}>
                                                <EmptyState
                                                    title={t('p3.req.caseEmptyTitle')}
                                                    hint={viewer?.can_create ? t('p3.req.caseEmptyHint') : t('p3.req.caseEmptyHintNoCreate')}
                                                />
                                            </td>
                                        </tr>
                                    )}
                                </TBody>
                            </Table>
                        )}
                    </TableFrame>
                    {data && data.total > data.items.length && (
                        <Note className="mt-2">
                            {t('p3.req.caseMore', { shown: data.items.length, total: data.total })}
                        </Note>
                    )}
                    <Note className="mt-2">{t('p3.req.caseNote')}</Note>
                </div>
                {selected && (
                    <SidePanel
                        title={detail.data?.subject || t('p3.req.detailTitle')}
                        onClose={() => setState({ sel: undefined }, { replace: true })}
                        testId="p3-req-panel"
                    >
                        {detail.loading && !detail.data ? (
                            <SkeletonLines rows={8} />
                        ) : detail.error && !detail.data ? (
                            <ErrorState error={detail.error} onRetry={detail.reload} describe={describeP3Error} />
                        ) : detail.data ? (
                            <RequestDetail
                                key={`${detail.data.id}:${detail.data.revision}:${detail.data.events.length}`}
                                request={detail.data}
                                viewer={viewer}
                                showCaseLink={false}
                                onReload={detail.reload}
                                onChanged={() => {
                                    detail.reload()
                                    list.reload()
                                }}
                            />
                        ) : null}
                    </SidePanel>
                )}
            </div>

            <NewRequestDialog
                caseId={caseData.id}
                isOpen={creating}
                onClose={() => setCreating(false)}
                onCreated={(r) => {
                    ToastNotification(t('p3.req.new.created'), t('p3.req.title'), 'success')
                    list.reload()
                    setState({ sel: r.id }, { replace: true })
                }}
            />
        </div>
    )
}

export default CaseRequestsPanel
