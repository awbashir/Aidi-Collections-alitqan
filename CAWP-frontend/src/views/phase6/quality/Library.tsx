/**
 * The QC recording library (`GET /v2/quality/recordings`): status / source / template filters and
 * paging (URL hash), exact total. "Import a recording" (`POST /v2/quality/recordings`, multipart):
 * a private WAV or MP3, inspected by the API (format, size, duration, sample rate); an inspection
 * refusal is said in words; the same audio already in the library is not stored twice. The
 * original file name is never stored. Call recordings enter only through "Add call recording to
 * quality review" on a call (explicit and scoped).
 */
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { FileUp } from 'lucide-react'
import Button from '@/components/ui/Button'
import Input from '@/components/ui/Input'
import Table from '@/components/ui/Table'
import ActionDialog, { DialogField } from '@/components/v2/ActionDialog'
import { RiyadhTime } from '@/components/v2/Display'
import { EmptyState, ErrorState, Note, SkeletonRows } from '@/components/v2/States'
import { FilterSelect, PagerFooter, TableFrame, rowClass } from '@/components/v2/ListParts'
import { QC_STATUSES, describeP6Error, qualityApi } from '@/services/v2'
import type { QcImportResult, QcOverview, QcRecordingRow, Translate } from '@/services/v2'
import { usePermissions, useV2Query } from '@/utils/v2/hooks'
import { formatCount } from '@/utils/v2/format'
import { DurationText, Notice } from '@/views/phase5/parts'
import { QcStatusBadge, RatioText } from '../parts'
import { formatBytes, qcStatusLabel, templateName } from '../labels'

const { THead, TBody, Tr, Th, Td } = Table
export const LIBRARY_PAGE_SIZE = 20
const describe = (e: unknown, t: Translate) => describeP6Error(e, t)
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

const ImportDialog = ({
    isOpen,
    overview,
    onClose,
    onDone,
}: {
    isOpen: boolean
    overview: QcOverview | undefined
    onClose: () => void
    onDone: (r: QcImportResult) => void
}) => {
    const { t } = useTranslation()
    const [file, setFile] = useState<File | null>(null)
    const [title, setTitle] = useState('')
    const [caseId, setCaseId] = useState('')
    const limits = overview?.limits
    const tooBig = !!file && !!limits && file.size > limits.max_bytes
    const caseOk = !caseId.trim() || UUID.test(caseId.trim())
    return (
        <ActionDialog
            isOpen={isOpen}
            title={t('p6.qc.import.title')}
            confirmLabel={t('p6.qc.import.confirm')}
            canSubmit={!!file && !tooBig && caseOk}
            testId="p6-qc-import"
            describeError={describe}
            onClose={() => {
                setFile(null)
                setTitle('')
                setCaseId('')
                onClose()
            }}
            onSubmit={async (key) => {
                if (!file) return
                const r = await qualityApi.importFile(file, { title: title.trim() || undefined, case_id: caseId.trim() || undefined }, key)
                setFile(null)
                setTitle('')
                setCaseId('')
                onDone(r)
            }}
        >
            <Notice tone="info" className="mb-3">
                {t('p6.qc.import.explain')}
            </Notice>
            {limits && (
                <div className="mb-3 text-[12.5px] text-gray-600" data-testid="p6-qc-import-limits">
                    {t('p6.qc.import.limits', {
                        formats: limits.formats.map((f) => f.toUpperCase()).join(', '),
                        mb: (limits.max_bytes / 1_000_000).toFixed(0),
                        minutes: Math.round(limits.max_seconds / 60),
                        min: limits.sample_rate[0] / 1000,
                        max: limits.sample_rate[1] / 1000,
                    })}
                </div>
            )}
            <DialogField label={t('p6.qc.import.file')} required>
                <input
                    type="file"
                    accept=".wav,.mp3,audio/wav,audio/x-wav,audio/mpeg"
                    onChange={(e) => setFile(e.target.files?.[0] || null)}
                    data-testid="p6-qc-import-file"
                    className="block w-full text-[13px]"
                />
                {file && (
                    <span className="mt-1 block text-[12px] text-gray-600">
                        {formatBytes(file.size)}
                        {tooBig && <span className="ms-2 text-danger">{t('p6.qc.import.tooBig')}</span>}
                    </span>
                )}
            </DialogField>
            <DialogField label={t('p6.qc.titleField')} hint={t('p6.qc.import.titleHint')}>
                <Input size="sm" maxLength={200} value={title} onChange={(e) => setTitle(e.target.value)} data-testid="p6-qc-import-title" />
            </DialogField>
            <DialogField label={t('p6.qc.import.caseId')} hint={caseOk ? t('p6.qc.import.caseHint') : t('p6.qc.import.caseInvalid')}>
                <Input size="sm" dir="ltr" value={caseId} onChange={(e) => setCaseId(e.target.value)} data-testid="p6-qc-import-case" />
            </DialogField>
        </ActionDialog>
    )
}

type Filters = { status?: string; source?: string; template?: string }

const Library = ({
    overview,
    filters,
    page,
    tick,
    onFilters,
    onPage,
    onOpen,
    onImported,
}: {
    overview: QcOverview | undefined
    filters: Filters
    page: number
    tick: number
    onFilters: (f: Filters) => void
    onPage: (p: number) => void
    onOpen: (id: string) => void
    onImported: () => void
}) => {
    const { t, i18n } = useTranslation()
    const perms = usePermissions()
    const [importing, setImporting] = useState(false)
    const [last, setLast] = useState<QcImportResult | null>(null)
    const status = filters.status && (QC_STATUSES as readonly string[]).includes(filters.status) ? filters.status : undefined
    const source = filters.source === 'import' || filters.source === 'pbx_call' ? filters.source : undefined
    const template = filters.template || undefined
    const q = useV2Query(
        (signal) => qualityApi.recordings({ status, source, template, page, page_size: LIBRARY_PAGE_SIZE }, signal),
        [status, source, template, page, tick]
    )
    const data = q.data
    const templates = overview?.templates || []
    const cols = 8
    return (
        <div data-testid="p6-qc-library" data-total={data?.total ?? ''}>
            <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
                <div className="flex flex-wrap items-end gap-3">
                    <FilterSelect
                        label={t('p6.qc.col.status')}
                        options={QC_STATUSES.map((s) => ({ value: s, label: String(qcStatusLabel(t, s)) }))}
                        value={status}
                        width={190}
                        testId="p6-qc-filter-status"
                        onChange={(v) => onFilters({ ...filters, status: v })}
                    />
                    <FilterSelect
                        label={t('p6.qc.col.source')}
                        options={[
                            { value: 'import', label: String(t('p6.qc.source.import')) },
                            { value: 'pbx_call', label: String(t('p6.qc.source.pbx_call')) },
                        ]}
                        value={source}
                        width={190}
                        testId="p6-qc-filter-source"
                        onChange={(v) => onFilters({ ...filters, source: v })}
                    />
                    <FilterSelect
                        label={t('p6.qc.template')}
                        options={Array.from(new Set(templates.map((x) => x.key))).map((k) => ({
                            value: k,
                            label: templateName(templates.find((x) => x.key === k), i18n.language),
                        }))}
                        value={template}
                        width={240}
                        testId="p6-qc-filter-template"
                        onChange={(v) => onFilters({ ...filters, template: v })}
                    />
                </div>
                {perms.quality_review && (
                    <Button size="sm" variant="solid" icon={<FileUp size={15} />} onClick={() => setImporting(true)} data-testid="p6-qc-import-open">
                        {t('p6.qc.import.open')}
                    </Button>
                )}
            </div>
            {last && (
                <Notice tone={last.duplicate ? 'info' : 'success'} className="mb-3" testId="p6-qc-imported" >
                    <span data-recording={last.recording.id} data-duplicate={last.duplicate ? '1' : '0'}>
                        {last.duplicate ? t('p6.qc.import.duplicate') : t('p6.qc.import.done')}{' '}
                        <button type="button" className="font-extrabold text-azure-600 underline underline-offset-2" onClick={() => onOpen(last.recording.id)} data-testid="p6-qc-imported-open">
                            {t('p6.qc.import.openIt')}
                        </button>
                    </span>
                </Notice>
            )}
            <TableFrame
                testId="p6-qc-table"
                footer={data && data.total > 0 ? <PagerFooter page={page} pageSize={LIBRARY_PAGE_SIZE} total={data.total} onPage={onPage} /> : undefined}
            >
                {q.error && !data ? (
                    <ErrorState error={q.error} onRetry={q.reload} describe={describe} />
                ) : (
                    <Table>
                        <THead>
                            <Tr>
                                <Th>{t('p6.qc.titleField')}</Th>
                                <Th>{t('p6.qc.col.status')}</Th>
                                <Th>{t('p6.qc.col.source')}</Th>
                                <Th className="text-end">{t('p6.qc.col.duration')}</Th>
                                <Th>{t('p6.qc.template')}</Th>
                                <Th>{t('p6.qc.col.transcript')}</Th>
                                <Th className="text-end">{t('p6.qc.metric.compliance')}</Th>
                                <Th>{t('p6.qc.col.created')}</Th>
                            </Tr>
                        </THead>
                        <TBody>
                            {q.loading && !data ? (
                                <SkeletonRows cols={cols} />
                            ) : data && data.items.length > 0 ? (
                                data.items.map((r: QcRecordingRow) => (
                                    <Tr key={r.id} className={rowClass(false)} onClick={() => onOpen(r.id)} data-testid="p6-qc-row" data-recording={r.id} data-status={r.status}>
                                        <Td className="min-w-[200px]">
                                            <span className="font-extrabold text-azure-600" dir="auto">
                                                {r.title}
                                            </span>
                                            {!r.audio_available && <div className="text-[11.5px] text-gray-500">{t('p6.qc.audio.purgedShort')}</div>}
                                        </Td>
                                        <Td>
                                            <QcStatusBadge status={r.status} size="row" />
                                            {r.processing && <div className="text-[11.5px] text-warning">{t('p6.qc.processingShort')}</div>}
                                        </Td>
                                        <Td className="text-[12.5px]">{t(`p6.qc.source.${r.source}`)}</Td>
                                        <Td className="text-end">
                                            <DurationText seconds={r.duration_seconds} />
                                        </Td>
                                        <Td className="text-[12.5px]">
                                            {r.template ? `${templateName(templates.find((x) => x.key === r.template?.key && x.version === r.template?.version), i18n.language) || r.template.key} · v${r.template.version}` : '—'}
                                        </Td>
                                        <Td className="text-[12.5px]">{r.has_transcript ? t('p5.yes') : t('p5.no')}</Td>
                                        <Td className="text-end">{r.metrics ? <RatioText ratio={r.metrics.compliance} /> : <span className="text-gray-500">—</span>}</Td>
                                        <Td>
                                            <RiyadhTime iso={r.created_at} />
                                        </Td>
                                    </Tr>
                                ))
                            ) : (
                                <tr>
                                    <td colSpan={cols}>
                                        <EmptyState title={t('p6.qc.emptyTitle')} hint={t('p6.qc.emptyHint')} />
                                    </td>
                                </tr>
                            )}
                        </TBody>
                    </Table>
                )}
            </TableFrame>
            <Note className="mt-3">
                {t('p6.qc.libraryNote')} <Link className="text-azure-600 hover:underline" to="/calls">{t('p6.qc.goToCalls')}</Link>
            </Note>
            {data && <span className="hidden" data-testid="p6-qc-total">{formatCount(data.total)}</span>}
            <ImportDialog
                isOpen={importing}
                overview={overview}
                onClose={() => setImporting(false)}
                onDone={(r) => {
                    setLast(r)
                    onImported()
                }}
            />
        </div>
    )
}

export default Library
