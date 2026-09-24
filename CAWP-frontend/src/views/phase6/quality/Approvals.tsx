/**
 * Approval history and review revisions of one recording, and the immutable snapshot of an
 * approval (`GET …/approvals`, `GET /v2/quality/approvals/<id>`, `GET …/reviews`).
 *
 * An approval is never edited: a later evidence, transcript or review change clears the current
 * approval, and the old one stays here, marked "superseded". The snapshot is exactly what was
 * approved (recording facts, template, transcript version, evidence, every finding, metrics,
 * approver) with its sha256. The report of any approval can be downloaded in Arabic or English.
 */
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import Button from '@/components/ui/Button'
import { Ltr, RiyadhTime } from '@/components/v2/Display'
import { ErrorState, Note, SkeletonLines } from '@/components/v2/States'
import { describeP6Error, qualityApi } from '@/services/v2'
import type { QcApproval, QcRecording, Translate } from '@/services/v2'
import { useV2Query } from '@/utils/v2/hooks'
import { Card, Fact, Notice } from '@/views/phase5/parts'
import { RatioText, ResultBadge } from '../parts'
import { criterionName, formatStamp, templateName } from '../labels'
import ReportButtons from './ReportButtons'
import { TranscriptView } from './TranscriptEditor'

const describe = (e: unknown, t: Translate) => describeP6Error(e, t)

const Snapshot = ({ approvalId, recordingId }: { approvalId: string; recordingId: string }) => {
    const { t, i18n } = useTranslation()
    const q = useV2Query((signal) => qualityApi.approval(approvalId, signal), [approvalId])
    if (q.loading && !q.data) return <SkeletonLines rows={8} />
    if (!q.data) return <ErrorState error={q.error} onRetry={q.reload} describe={describe} />
    const a = q.data
    const s = a.snapshot
    if (!s) return <Note>{t('p6.qc.approvals.noSnapshot')}</Note>
    const byId = new Map(s.template.criteria.map((c) => [c.id, c]))
    return (
        <div className="flex flex-col gap-3" data-testid="p6-qc-snapshot" data-approval={a.id} data-sha={a.snapshot_sha256}>
            <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
                <Fact label={t('p6.qc.approvals.approvedBy')}>{s.approval.approved_by?.name || '—'}</Fact>
                <Fact label={t('p6.qc.approvals.approvedAt')}>
                    <RiyadhTime iso={s.approval.approved_at} />
                </Fact>
                <Fact label={t('p6.qc.approvals.revision')}>
                    <Ltr>{s.approval.approved_revision}</Ltr>
                </Fact>
                <Fact label={t('p6.qc.template')}>
                    {templateName(s.template, i18n.language)} · v{s.template.version}
                </Fact>
                <Fact label={t('p6.qc.metric.compliance')}>
                    <RatioText ratio={s.metrics.compliance} testId="p6-qc-snapshot-compliance" />
                </Fact>
                <Fact label={t('p6.qc.metric.coverage')}>
                    <RatioText ratio={s.metrics.coverage} />
                </Fact>
                <Fact label={t('p6.qc.approvals.sha')} className="col-span-2">
                    <Ltr className="break-all font-mono text-[11.5px]">{a.snapshot_sha256}</Ltr>
                </Fact>
            </div>
            <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
                <Fact label={t('p6.qc.evidence.employee')}>{s.evidence.employee_label || '—'}</Fact>
                <Fact label={t('p6.qc.evidence.action')}>{s.evidence.recorded_action || '—'}</Fact>
                <Fact label={t('p6.qc.evidence.notes')}>
                    <span dir="auto" className="whitespace-pre-wrap">
                        {s.evidence.notes || '—'}
                    </span>
                </Fact>
                <Fact label={t('p6.qc.evidence.context')}>
                    <span dir="auto" className="whitespace-pre-wrap">
                        {s.evidence.context || '—'}
                    </span>
                </Fact>
            </div>
            <div>
                <div className="text-eyebrow mb-1">{t('p6.qc.approvals.findings')}</div>
                <div className="overflow-x-auto">
                    <table className="min-w-full text-[12.5px]" data-testid="p6-qc-snapshot-results">
                        <thead>
                            <tr className="text-gray-600">
                                <th className="pe-3 text-start font-extrabold">{t('p6.qc.criterion')}</th>
                                <th className="pe-3 text-start font-extrabold">{t('p6.qc.review.result')}</th>
                                <th className="pe-3 text-start font-extrabold">{t('p6.qc.review.reason')}</th>
                                <th className="text-start font-extrabold">{t('p6.qc.review.evidence')}</th>
                            </tr>
                        </thead>
                        <tbody>
                            {s.review.results.map((r) => (
                                <tr key={r.criterion_id} className="border-t border-gray-200 align-top dark:border-night-border" data-criterion={r.criterion_id} data-result={r.result}>
                                    <td className="py-1.5 pe-3">{byId.get(r.criterion_id) ? criterionName(byId.get(r.criterion_id) as never, i18n.language) : r.criterion_id}</td>
                                    <td className="pe-3">
                                        <ResultBadge result={r.result} size="row" />
                                    </td>
                                    <td className="pe-3" dir="auto">
                                        {r.reason}
                                    </td>
                                    <td>
                                        {r.evidence.length ? (
                                            <ul className="flex flex-col gap-1">
                                                {r.evidence.map((e, i) =>
                                                    e.type === 'quote' ? (
                                                        <li key={i} dir="auto">
                                                            «{e.text}» {e.segment_ids?.length ? <Ltr className="text-gray-500">({e.segment_ids.join(', ')})</Ltr> : null}
                                                        </li>
                                                    ) : (
                                                        <li key={i}>
                                                            {t('p6.qc.approvals.audioAt')} <Ltr>{formatStamp(e.start)}{e.end !== null && e.end !== undefined ? `–${formatStamp(e.end)}` : ''}</Ltr>
                                                        </li>
                                                    )
                                                )}
                                            </ul>
                                        ) : (
                                            '—'
                                        )}
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            </div>
            {s.transcript && (
                <div>
                    <div className="text-eyebrow mb-1">{t('p6.qc.approvals.transcript', { version: s.transcript.version })}</div>
                    <TranscriptView transcript={s.transcript} />
                </div>
            )}
            <ReportButtons recordingId={recordingId} approvalId={a.id} />
        </div>
    )
}

const Approvals = ({ recording }: { recording: QcRecording }) => {
    const { t } = useTranslation()
    const approvals = useV2Query((signal) => qualityApi.approvals(recording.id, signal), [recording.id, recording.revision])
    const reviews = useV2Query((signal) => qualityApi.reviews(recording.id, signal), [recording.id, recording.revision])
    const [open, setOpen] = useState<string | null>(recording.approval?.id || null)
    return (
        <div className="flex flex-col gap-4" data-testid="p6-qc-approvals">
            <Card title={t('p6.qc.approvals.title')} testId="p6-qc-approval-history">
                {approvals.loading && !approvals.data ? (
                    <SkeletonLines rows={3} />
                ) : approvals.error && !approvals.data ? (
                    <ErrorState error={approvals.error} onRetry={approvals.reload} describe={describe} />
                ) : approvals.data && approvals.data.length ? (
                    <ul className="flex flex-col divide-y divide-gray-200 dark:divide-night-border">
                        {approvals.data.map((a: QcApproval) => (
                            <li key={a.id} className="flex flex-wrap items-center justify-between gap-2 py-2" data-testid="p6-qc-approval-row" data-approval={a.id} data-current={a.current ? '1' : '0'}>
                                <span className="flex flex-wrap items-center gap-x-3 text-[13px]">
                                    <span className={a.current ? 'font-extrabold text-success' : 'font-extrabold text-gray-600'}>
                                        {a.current ? t('p6.qc.approvals.current') : t('p6.qc.approvals.superseded')}
                                    </span>
                                    <span>
                                        {a.approved_by?.name || '—'} · <RiyadhTime iso={a.approved_at} />
                                    </span>
                                    <span>{t('p6.qc.approvals.revisionShort', { revision: a.approved_revision })}</span>
                                    <span>
                                        {t('p6.qc.metric.compliance')}: <RatioText ratio={a.metrics.compliance} />
                                    </span>
                                </span>
                                <Button size="xs" onClick={() => setOpen(open === a.id ? null : a.id)} data-testid="p6-qc-approval-open">
                                    {open === a.id ? t('p1.action.close') : t('p6.qc.approvals.snapshot')}
                                </Button>
                            </li>
                        ))}
                    </ul>
                ) : (
                    <Note>{t('p6.qc.approvals.none')}</Note>
                )}
            </Card>
            {open && (
                <Card title={t('p6.qc.approvals.snapshotTitle')} testId="p6-qc-snapshot-card">
                    <Snapshot approvalId={open} recordingId={recording.id} />
                </Card>
            )}
            <Card title={t('p6.qc.approvals.reviews')} testId="p6-qc-review-history">
                {reviews.loading && !reviews.data ? (
                    <SkeletonLines rows={3} />
                ) : reviews.data && reviews.data.length ? (
                    <ul className="flex flex-col gap-1 text-[12.5px]">
                        {reviews.data.map((r) => (
                            <li key={r.id} className="flex flex-wrap items-center gap-x-3" data-review={r.id} data-origin={r.origin}>
                                <span className="font-extrabold text-navy-600 dark:text-gray-100">{t('p6.qc.review.revision', { version: r.version })}</span>
                                <span>{r.origin === 'ai' ? t('p6.qc.review.originAi') : t('p6.qc.review.originHuman')}</span>
                                <span className="text-gray-600">
                                    {r.created_by?.name || '—'} · <RiyadhTime iso={r.created_at} />
                                </span>
                                <span>
                                    {t('p6.qc.metric.compliance')}: <RatioText ratio={r.metrics.compliance} />
                                </span>
                                {r.id === recording.review?.id && <span className="text-[11.5px] font-extrabold text-success">{t('p6.qc.current')}</span>}
                            </li>
                        ))}
                    </ul>
                ) : (
                    <Note>{t('p6.qc.review.none')}</Note>
                )}
            </Card>
            {recording.approval === null && recording.history.approvals > 0 && (
                <Notice tone="info">{t('p6.qc.approvals.clearedNote')}</Notice>
            )}
        </div>
    )
}

export default Approvals
