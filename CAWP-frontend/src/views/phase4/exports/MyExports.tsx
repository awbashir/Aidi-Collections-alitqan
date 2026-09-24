/**
 * "My exports": the caller's own report exports (`GET /v2/report-exports`,
 * newest first; exports are creator-private). Status, progress, rows, size,
 * expiry and the download. Refreshes every 2 s while an export is active.
 * With `report`, only that report's exports (the viewer); without it, all
 * (the hub's "My exports" tab).
 */
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import Table from '@/components/ui/Table'
import { EmptyState, ErrorState, SkeletonRows } from '@/components/v2/States'
import { PagerFooter, TableFrame } from '@/components/v2/ListParts'
import { RiyadhTime } from '@/components/v2/Display'
import { ACTIVE_EXPORT_STATUSES, REPORT_EXPORT_STATUSES, describeReportError, exportFailureText, reportsApi } from '@/services/v2'
import type { ReportExportStatus } from '@/services/v2'
import { useV2Query } from '@/utils/v2/hooks'
import { formatCount } from '@/utils/v2/format'
import { encodeHashParams } from '@/utils/hooks/useHashState'
import { DownloadButton, ExportProgress, ExportStatusPill } from './exportParts'
import { formatBytes, formatLabelKey } from './exportUtils'
import { pick } from '../reportUtils'

const { THead, TBody, Tr, Th, Td } = Table
const PAGE_SIZE = 10

type Props = {
    report?: string
    /** Bump to reload (a new export was queued). */
    refreshKey?: number
    status?: ReportExportStatus | ''
    onStatus?: (s: ReportExportStatus | '') => void
    page?: number
    onPage?: (p: number) => void
}

export default function MyExports({ report, refreshKey = 0, status = '', onStatus, page: pageProp, onPage }: Props) {
    const { t, i18n } = useTranslation()
    const ar = (i18n.language || 'ar').startsWith('ar')
    const [localPage, setLocalPage] = useState(1)
    const page = pageProp ?? localPage
    const setPage = onPage ?? setLocalPage
    const [tick, setTick] = useState(0)
    const q = useV2Query(
        (signal) => reportsApi.exports({ report, status: status || undefined, page, page_size: PAGE_SIZE }, signal),
        [report, status, page, refreshKey, tick]
    )
    const data = q.data
    const anyActive = !!data?.items.some((e) => ACTIVE_EXPORT_STATUSES.includes(e.status))
    useEffect(() => {
        if (!anyActive) return undefined
        const h = setTimeout(() => setTick((n) => n + 1), 2000)
        return () => clearTimeout(h)
    }, [anyActive, data])

    const cols = report ? 7 : 8
    return (
        <TableFrame
            testId="p4-my-exports"
            toolbar={
                <div className="flex w-full flex-wrap items-center justify-between gap-2">
                    <div>
                        <div className="text-[15px] font-extrabold text-navy-600 dark:text-gray-100">{t('p4.exports.title')}</div>
                        <div className="text-[12.5px] text-gray-600">{t('p4.exports.subtitle')}</div>
                    </div>
                    {onStatus && (
                        <label className="flex items-center gap-1 text-[12.5px] text-gray-600">
                            {t('p4.exports.statusFilter')}
                            <select
                                className="h-8 rounded-md border border-[#B8C4CE] bg-white px-2 text-[12.5px] text-gray-800 dark:border-night-border dark:bg-night-card dark:text-gray-100"
                                value={status}
                                onChange={(e) => onStatus(e.target.value as ReportExportStatus | '')}
                                data-testid="p4-exports-status"
                            >
                                <option value="">{t('p4.filter.all')}</option>
                                {REPORT_EXPORT_STATUSES.map((s) => (
                                    <option key={s} value={s}>
                                        {t(`p4.status.${s}`)}
                                    </option>
                                ))}
                            </select>
                        </label>
                    )}
                </div>
            }
            footer={data && data.total > 0 ? <PagerFooter page={page} pageSize={PAGE_SIZE} total={data.total} onPage={setPage} /> : undefined}
        >
            {q.error && !data ? (
                <ErrorState error={q.error} onRetry={q.reload} describe={(e, tt) => describeReportError(e, tt)} />
            ) : (
                <Table>
                    <THead>
                        <Tr>
                            <Th>{t('p4.exports.col.requested')}</Th>
                            {!report && <Th>{t('p4.exports.col.report')}</Th>}
                            <Th>{t('p4.exports.col.format')}</Th>
                            <Th>{t('p4.exports.col.status')}</Th>
                            <Th className="text-end">{t('p4.exports.col.rows')}</Th>
                            <Th>{t('p4.exports.col.file')}</Th>
                            <Th>{t('p4.exports.col.expires')}</Th>
                            <Th>{t('p4.exports.col.actions')}</Th>
                        </Tr>
                    </THead>
                    <TBody>
                        {q.loading && !data ? (
                            <SkeletonRows cols={cols} rows={3} />
                        ) : data && data.items.length ? (
                            data.items.map((e) => {
                                const filtersHash = encodeHashParams(
                                    Object.entries(e.definition?.params || {}).concat(e.definition?.sort ? [['sort', e.definition.sort]] : [])
                                )
                                return (
                                    <Tr key={e.id} data-testid="p4-export-row" data-export={e.id} data-status={e.status} data-format={e.format} data-report={e.report.id}>
                                        <Td>
                                            <RiyadhTime iso={e.requested_at} />
                                        </Td>
                                        {!report && (
                                            <Td>
                                                <Link
                                                    to={`/reports/${encodeURIComponent(e.report.id)}${filtersHash}`}
                                                    className="text-azure-600 underline-offset-2 hover:underline"
                                                    title={String(t('p4.exports.openWithFilters'))}
                                                >
                                                    <span dir="ltr" className="me-1 font-mono text-[12px]">
                                                        {e.report.id}
                                                    </span>
                                                    {pick(e.report.labels, ar)}
                                                </Link>
                                            </Td>
                                        )}
                                        <Td className="whitespace-nowrap">{t(formatLabelKey(e.format))}</Td>
                                        <Td>
                                            <div className="flex flex-col gap-1">
                                                <ExportStatusPill status={e.status} />
                                                {ACTIVE_EXPORT_STATUSES.includes(e.status) && <ExportProgress exp={e} />}
                                                {e.status === 'failed' && (
                                                    <span className="max-w-[260px] text-[12px] text-danger">
                                                        {exportFailureText(e.error?.code, t, e.error?.message)}
                                                    </span>
                                                )}
                                            </div>
                                        </Td>
                                        <Td className="text-end tabular-nums">{formatCount(e.row_count)}</Td>
                                        <Td>
                                            {e.artifact ? (
                                                <span className="text-[12px] text-gray-600">
                                                    <span dir="ltr" className="block max-w-[260px] truncate font-mono" title={e.artifact.file_name}>
                                                        {e.artifact.file_name}
                                                    </span>
                                                    {formatBytes(e.artifact.size_bytes)}
                                                </span>
                                            ) : (
                                                <span className="text-gray-500">—</span>
                                            )}
                                        </Td>
                                        <Td className="whitespace-nowrap" data-testid="p4-export-expiry">
                                            {e.status === 'expired' ? (
                                                <span className="text-gray-600">{t('p4.status.expired')}</span>
                                            ) : e.artifact?.expires_at ? (
                                                <RiyadhTime iso={e.artifact.expires_at} />
                                            ) : (
                                                <span className="text-gray-500">—</span>
                                            )}
                                        </Td>
                                        <Td>
                                            <DownloadButton exp={e} size="xs" />
                                        </Td>
                                    </Tr>
                                )
                            })
                        ) : (
                            <tr>
                                <td colSpan={cols}>
                                    <EmptyState title={t('p4.exports.emptyTitle')} hint={t('p4.exports.emptyHint')} />
                                </td>
                            </tr>
                        )}
                    </TBody>
                </Table>
            )}
            {data && (
                <div className="border-t border-gray-200 px-5 py-2 text-[12px] text-gray-600 dark:border-night-border" data-testid="p4-exports-total" data-total={data.total}>
                    {t('p4.exports.total', { n: formatCount(data.total) })}
                </div>
            )}
        </TableFrame>
    )
}
