/**
 * Export the current report result: CSV, XLSX and (where the definition
 * offers it) the Arabic RTL XLSX. `POST /v2/reports/<id>/exports` with the
 * same flat params and sort as the preview (202 queued); the worker then
 * writes the file in a fresh snapshot. The job is polled every 1.5 s while it
 * is queued/running/retrying, with its progress; a finished file downloads
 * under its own name. The row count is compared with the preview total.
 */
import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { FileSpreadsheet, FileText } from 'lucide-react'
import Button from '@/components/ui/Button'
import { InlineError } from '@/components/v2/States'
import { RiyadhTime } from '@/components/v2/Display'
import { ACTIVE_EXPORT_STATUSES, describeReportError, exportFailureText, newIdempotencyKey, reportsApi } from '@/services/v2'
import type { ReportDefinition, ReportExport, ReportFormat } from '@/services/v2'
import { formatCount } from '@/utils/v2/format'
import { DownloadButton, ExportProgress, ExportStatusPill } from './exportParts'
import { formatBytes, formatLabelKey } from './exportUtils'
import { stableKey } from '../reportUtils'

type Props = {
    def: ReportDefinition
    /** Non-empty filter params, exactly as the preview sends them. */
    params: Record<string, string>
    sort: string
    /** The preview's row count, to compare with the file's. */
    previewTotal: number | undefined
    onChanged: () => void
}

const POLL_MS = 1500

export default function ExportPanel({ def, params, sort, previewTotal, onChanged }: Props) {
    const { t } = useTranslation()
    const [current, setCurrent] = useState<ReportExport | null>(null)
    const [creating, setCreating] = useState<ReportFormat | null>(null)
    const [error, setError] = useState<unknown>(null)
    /* One Idempotency-Key per user action: a retry of the same request (same
     * format, params and sort) after a failure reuses it, so a lost response
     * never queues a second export. */
    const attempt = useRef<{ sig: string; key: string } | null>(null)
    const previewAtRequest = useRef<number | undefined>(undefined)

    const create = async (format: ReportFormat) => {
        const sig = `${format}|${sort}|${stableKey(params)}`
        if (!attempt.current || attempt.current.sig !== sig) attempt.current = { sig, key: newIdempotencyKey() }
        setCreating(format)
        setError(null)
        try {
            const exp = await reportsApi.createExport(def.id, { format, params, sort }, attempt.current.key)
            attempt.current = null
            previewAtRequest.current = previewTotal
            setCurrent(exp)
            onChanged()
        } catch (e) {
            setError(e)
        } finally {
            setCreating(null)
        }
    }

    /* The card describes one request: a new filter or sort clears it (the job itself goes on,
     * and "My exports" below keeps every export). */
    const requestKey = `${sort}|${stableKey(params)}`
    useEffect(() => {
        setCurrent(null)
        setError(null)
    }, [requestKey])

    /* Poll while the export is active. */
    const active = !!current && ACTIVE_EXPORT_STATUSES.includes(current.status)
    useEffect(() => {
        if (!current || !active) return undefined
        const ctrl = new AbortController()
        const h = setTimeout(() => {
            reportsApi
                .exportDetail(current.id, ctrl.signal)
                .then((exp) => {
                    setCurrent(exp)
                    if (!ACTIVE_EXPORT_STATUSES.includes(exp.status)) onChanged()
                })
                .catch((e) => {
                    if ((e as { code?: string })?.code !== 'canceled') setError(e)
                })
        }, POLL_MS)
        return () => {
            clearTimeout(h)
            ctrl.abort()
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [current, active])

    const icon = (f: ReportFormat) => (f === 'csv' ? <FileText size={15} /> : <FileSpreadsheet size={15} />)
    const expected = previewAtRequest.current
    const differs =
        current?.status === 'succeeded' && current.row_count !== null && expected !== undefined && current.row_count !== expected

    return (
        <section
            className="mb-4 rounded-lg border border-gray-300 bg-white px-5 py-4 dark:border-night-border dark:bg-night-card"
            data-testid="p4-export"
            aria-label={String(t('p4.export.title'))}
        >
            <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="min-w-0">
                    <div className="text-[15px] font-extrabold text-navy-600 dark:text-gray-100">{t('p4.export.title')}</div>
                    <div className="mt-0.5 text-[12.5px] text-gray-600" data-testid="p4-export-note">
                        {t('p4.export.snapshotNote')}
                    </div>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                    {def.formats.map((f) => (
                        <Button
                            key={f}
                            size="sm"
                            icon={icon(f)}
                            loading={creating === f}
                            disabled={!!creating || active}
                            onClick={() => create(f)}
                            data-testid={`p4-export-${f}`}
                        >
                            {t(formatLabelKey(f))}
                        </Button>
                    ))}
                </div>
            </div>
            {!def.formats.includes('xlsx_ar') && (
                <div className="mt-1 text-[12px] text-gray-600" data-testid="p4-export-no-ar">
                    {t('p4.export.noArabic')}
                </div>
            )}
            <InlineError error={error} describe={(e, tt) => describeReportError(e, tt)} />
            {current && (
                <div
                    className="mt-3 flex flex-wrap items-center gap-x-5 gap-y-2 rounded-md border border-gray-200 bg-gray-50 px-4 py-3 dark:border-night-border dark:bg-night-page"
                    data-testid="p4-export-current"
                    data-export={current.id}
                    data-status={current.status}
                >
                    <ExportStatusPill status={current.status} />
                    <span className="text-[12.5px] font-extrabold text-navy-600 dark:text-gray-100">{t(formatLabelKey(current.format))}</span>
                    <span className="text-[12px] text-gray-600">
                        {t('p4.export.requestedAt')} <RiyadhTime iso={current.requested_at} />
                    </span>
                    {active && <ExportProgress exp={current} />}
                    {current.status === 'succeeded' && (
                        <>
                            <span className="text-[12.5px] tabular-nums" data-testid="p4-export-rows" data-rows={current.row_count ?? ''}>
                                {t('p4.export.rows', { n: formatCount(current.row_count) })}
                                {expected !== undefined && !differs && (
                                    <span className="ms-1 text-gray-600">{t('p4.export.matchesPreview')}</span>
                                )}
                            </span>
                            {current.artifact && (
                                <span className="text-[12px] text-gray-600">
                                    {formatBytes(current.artifact.size_bytes)} · {t('p4.export.expires')}{' '}
                                    <RiyadhTime iso={current.artifact.expires_at} />
                                </span>
                            )}
                            {current.cutoff_at && (
                                <span className="text-[12px] text-gray-600">
                                    {t('p4.export.cutoff')} <RiyadhTime iso={current.cutoff_at} />
                                </span>
                            )}
                            <DownloadButton exp={current} />
                        </>
                    )}
                    {differs && (
                        <div className="w-full text-[12.5px] text-amber-700" role="status" data-testid="p4-export-differs">
                            {t('p4.export.differs', { file: formatCount(current.row_count), preview: formatCount(expected ?? null) })}
                        </div>
                    )}
                    {current.status === 'failed' && (
                        <div className="w-full text-[12.5px] text-danger" role="alert" data-testid="p4-export-failed">
                            {exportFailureText(current.error?.code, t, current.error?.message) || t('p4.status.failed')}
                        </div>
                    )}
                    {current.status === 'retrying' && (
                        <div className="w-full text-[12px] text-gray-600">{t('p4.export.retryingNote', { n: current.attempt, max: current.max_attempts })}</div>
                    )}
                </div>
            )}
        </section>
    )
}
