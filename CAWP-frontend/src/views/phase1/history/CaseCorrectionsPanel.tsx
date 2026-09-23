/**
 * Case-detail "Corrections" tab: this case's correction requests (pending and
 * past, newest first) from GET /v2/corrections?case_id=<id>, with a "Request
 * correction" button (`case_corrections_request`). Selecting a row opens the
 * live detail with approve / reject for reviewers (never on their own request)
 * and cancel for the requester.
 */
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { HiOutlinePencilAlt } from 'react-icons/hi'
import Button from '@/components/ui/Button'
import Table from '@/components/ui/Table'
import { historyApi } from '@/services/v2'
import { usePermissions, useV2Query } from '@/utils/v2/hooks'
import { RiyadhTime } from '@/components/v2/Display'
import { CorrectionBadge } from '@/components/v2/Badges'
import { EmptyState, ErrorState, ForbiddenState, SkeletonRows } from '@/components/v2/States'
import { PagerFooter, TableFrame, rowClass } from '@/components/v2/ListParts'
import type { CasePanelProps } from '../caseTypes'
import CorrectionDetail from './CorrectionDetail'
import RequestCorrectionDialog from './RequestCorrectionDialog'
import { ChangeSummary, useDelinquencyReasons } from './parts'

const { THead, TBody, Tr, Th, Td } = Table
const PAGE_SIZE = 20

const CaseCorrectionsPanel = ({ caseData, onChanged }: CasePanelProps) => {
    const { t } = useTranslation()
    const perms = usePermissions()
    const reasons = useDelinquencyReasons().options
    const canRead = !!(perms.case_corrections_request || perms.case_corrections_review)
    const canRequest = !!perms.case_corrections_request
    const [page, setPage] = useState(1)
    const [selected, setSelected] = useState<string | null>(null)
    const [requestOpen, setRequestOpen] = useState(false)

    const list = useV2Query(
        canRead
            ? (signal) => historyApi.corrections({ case_id: caseData.id, page, page_size: PAGE_SIZE }, signal)
            : null,
        [caseData.id, page, canRead]
    )
    const data = list.data
    const cols = 5

    if (!canRead) {
        return (
            <div data-testid="p1-case-corrections">
                <ForbiddenState keys={['case_corrections_request', 'case_corrections_review']} />
            </div>
        )
    }

    return (
        <div data-testid="p1-case-corrections" className="flex flex-col gap-4">
            <TableFrame
                testId="p1-case-corr-table"
                toolbar={
                    <>
                        <div className="min-w-0">
                            <div className="text-[15px] font-extrabold text-navy-600 dark:text-gray-100">
                                {t('p1.corr.caseTitle')}
                            </div>
                            <div className="text-[12.5px] text-gray-600">{t('p1.corr.caseSubtitle')}</div>
                        </div>
                        {canRequest && (
                            <Button
                                size="sm"
                                variant="solid"
                                icon={<HiOutlinePencilAlt />}
                                onClick={() => setRequestOpen(true)}
                                data-testid="p1-corr-request"
                            >
                                {t('p1.corr.requestButton')}
                            </Button>
                        )}
                    </>
                }
                footer={
                    data && data.total > PAGE_SIZE ? (
                        <PagerFooter page={page} pageSize={PAGE_SIZE} total={data.total} onPage={setPage} />
                    ) : undefined
                }
            >
                {list.error && !data ? (
                    <ErrorState error={list.error} onRetry={list.reload} />
                ) : (
                    <Table>
                        <THead>
                            <Tr>
                                <Th>{t('p1.col.kind')}</Th>
                                <Th>{t('p1.col.change')}</Th>
                                <Th>{t('p1.col.status')}</Th>
                                <Th>{t('p1.col.requestedBy')}</Th>
                                <Th>{t('p1.col.reviewedBy')}</Th>
                            </Tr>
                        </THead>
                        <TBody>
                            {list.loading && !data ? (
                                <SkeletonRows cols={cols} rows={3} />
                            ) : data && data.items.length > 0 ? (
                                data.items.map((c) => (
                                    <Tr
                                        key={c.id}
                                        className={rowClass(selected === c.id)}
                                        aria-current={selected === c.id ? 'true' : undefined}
                                        onClick={() => setSelected(selected === c.id ? null : c.id)}
                                        data-testid="p1-case-corr-row"
                                        data-id={c.id}
                                        data-status={c.status}
                                    >
                                        <Td className="whitespace-nowrap">{t(`p1.corrKind.${c.kind}`)}</Td>
                                        <Td className="min-w-[220px]">
                                            <ChangeSummary correction={c} reasons={reasons} />
                                        </Td>
                                        <Td>
                                            <CorrectionBadge status={c.status} size="row" />
                                        </Td>
                                        <Td>
                                            <div>{c.requested_by?.name || '—'}</div>
                                            <RiyadhTime iso={c.requested_at} className="text-[12px] text-gray-600" />
                                        </Td>
                                        <Td>
                                            {c.reviewed_by ? (
                                                <>
                                                    <div>{c.reviewed_by.name}</div>
                                                    <RiyadhTime iso={c.reviewed_at} className="text-[12px] text-gray-600" />
                                                </>
                                            ) : (
                                                <span className="text-gray-500">—</span>
                                            )}
                                        </Td>
                                    </Tr>
                                ))
                            ) : (
                                <tr>
                                    <td colSpan={cols}>
                                        <EmptyState title={t('p1.corr.caseEmptyTitle')} hint={t('p1.corr.caseEmptyHint')} />
                                    </td>
                                </tr>
                            )}
                        </TBody>
                    </Table>
                )}
            </TableFrame>

            {selected && (
                <div
                    className="rounded-lg border border-gray-300 bg-white p-5 dark:border-night-border dark:bg-night-card"
                    data-testid="p1-case-corr-detail"
                >
                    <CorrectionDetail
                        key={selected}
                        id={selected}
                        showCaseLink={false}
                        onChanged={(c) => {
                            list.reload()
                            // An approval changed the case itself: the legacy screen reloads it.
                            if (c.status === 'approved') onChanged?.()
                        }}
                    />
                </div>
            )}

            <RequestCorrectionDialog
                isOpen={requestOpen}
                caseData={caseData}
                onClose={() => setRequestOpen(false)}
                onDone={() => {
                    setPage(1)
                    list.reload()
                }}
            />
        </div>
    )
}

export default CaseCorrectionsPanel
