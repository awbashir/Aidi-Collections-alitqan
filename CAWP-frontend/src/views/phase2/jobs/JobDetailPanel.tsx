/**
 * The selected job on the jobs screen: facts, row counts, attempts, artifacts,
 * cancel / retry / reopen, and the link that resumes the wizard at the job's
 * current step (`/jobs/run/:id`). Polled while the job is moving.
 */
import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import Table from '@/components/ui/Table'
import { ErrorState, SkeletonLines } from '@/components/v2/States'
import { Field, Ltr, RiyadhTime } from '@/components/v2/Display'
import { bulkCodeLabel, describeBulkError } from '@/services/v2'
import type { BulkJobDetail } from '@/services/v2'
import { formatCount } from '@/utils/v2/format'
import { ArtifactButtons, JobStatusBadge, SourceText } from '../bulk/parts'
import { isActive, personLabel, shortId, useJob, useKindLabel } from '../bulk/shared'
import JobActions from '../bulk/JobActions'
import { ParamsSummary } from '../bulk/params'

const { THead, TBody, Tr, Th, Td } = Table

const resumeLabel = (job: BulkJobDetail) => {
    if (job.status === 'ready') return 'p2.jobs.resumeConfirm'
    if (isActive(job.status)) return 'p2.jobs.resumeProgress'
    return 'p2.jobs.resumeResults'
}

const JobDetailPanel = ({ id, onChanged }: { id: string; onChanged: () => void }) => {
    const { t } = useTranslation()
    const { job, error, loading, reload } = useJob(id)
    const label = useKindLabel()
    if (loading && !job) return <SkeletonLines rows={8} />
    if (error && !job) return <ErrorState error={error} onRetry={reload} describe={describeBulkError} />
    if (!job) return null
    const c = job.counts
    const changed = () => {
        reload()
        onChanged()
    }
    return (
        <div data-testid="p2-job-detail" data-id={job.id} data-status={job.status}>
            <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                <div className="min-w-0">
                    <h4 className="mb-1 text-[17px] font-extrabold text-navy-600 dark:text-gray-100">{label(job.kind_label, job.kind)}</h4>
                    <div className="flex flex-wrap items-center gap-2 text-[12.5px] text-gray-600">
                        <JobStatusBadge status={job.status} size="row" />
                        <Ltr>{shortId(job.id)}</Ltr>
                    </div>
                </div>
                <Link
                    to={`/jobs/run/${job.id}`}
                    className="inline-flex h-8 items-center rounded-md bg-navy-600 px-3.5 text-[12.5px] font-extrabold text-white hover:bg-navy-700 dark:bg-azure-600"
                    data-testid="p2-job-resume"
                >
                    {t(resumeLabel(job))}
                </Link>
            </div>

            <div className="mb-4 grid grid-cols-2 gap-3">
                <Field label={t('p2.step.source')}>
                    <SourceText job={job} />
                    {job.upload?.purged && <div className="text-danger">{t('p2.run.uploadPurged')}</div>}
                </Field>
                <Field label={t('p2.jobs.createdBy')}>
                    {personLabel(job.created_by)}
                    <div>
                        <RiyadhTime iso={job.created_at} className="text-gray-600" />
                    </div>
                </Field>
                <Field label={t('p2.jobs.preview')}>
                    {job.preview.ready_at ? <RiyadhTime iso={job.preview.ready_at} /> : '—'}
                    {job.preview.expires_at && (
                        <div className="text-[12px] text-gray-600">
                            {t('p2.preview.validUntil')} <RiyadhTime iso={job.preview.expires_at} />
                        </div>
                    )}
                </Field>
                <Field label={t('p2.results.confirmedBy')}>
                    {job.confirmation ? (
                        <>
                            {personLabel(job.confirmation.by)}
                            <div>
                                <RiyadhTime iso={job.confirmation.at} className="text-gray-600" />
                                {job.confirmation.accept_partial && <span className="ms-1 text-warning">({t('p2.results.partialAccepted')})</span>}
                            </div>
                        </>
                    ) : (
                        '—'
                    )}
                </Field>
                <Field label={t('p2.jobs.finished')}>{job.finished_at ? <RiyadhTime iso={job.finished_at} /> : '—'}</Field>
                <Field label={t('p2.jobs.attempts')}>
                    <span className="tabular-nums" data-testid="p2-job-attempt">
                        {job.attempt} / {job.max_attempts}
                    </span>
                    {job.failed_attempts > 0 && <span className="ms-1 text-danger">({t('p2.jobs.failedAttempts', { n: job.failed_attempts })})</span>}
                    {job.retry.count > 0 && <div className="text-[12px] text-gray-600">{t('p2.jobs.retries', { n: job.retry.count })}</div>}
                </Field>
                {job.reopened_from && (
                    <Field label={t('p2.jobs.reopenedFrom')}>
                        <Link className="font-extrabold text-azure-600 hover:underline" to={`/jobs#sel=${job.reopened_from}`}>
                            <Ltr>{shortId(job.reopened_from)}</Ltr>
                        </Link>
                    </Field>
                )}
                {job.cancel && (
                    <Field label={t('p2.jobs.cancel')}>
                        {personLabel(job.cancel.requested_by)} · <RiyadhTime iso={job.cancel.cancelled_at || job.cancel.requested_at} />
                        {job.cancel.reason && <div className="text-[12px] text-gray-600">{job.cancel.reason}</div>}
                    </Field>
                )}
            </div>

            {job.error && (
                <div role="alert" className="mb-4 rounded-md border border-red-200 bg-danger-tint px-3 py-2 text-[13px] text-danger">
                    <span className="font-extrabold">{bulkCodeLabel(t, job.error.code, job.error.message)}</span>
                </div>
            )}

            <div className="text-eyebrow mb-2">{t('p2.jobs.rowCounts')}</div>
            <dl className="mb-4 grid grid-cols-4 gap-2 text-[12.5px]" data-testid="p2-job-counts">
                {(['total', 'eligible', 'excluded', 'error', 'processed', 'succeeded', 'skipped', 'failed'] as const).map((k) => (
                    <div key={k} className="rounded-md border border-gray-300 px-2 py-1.5 dark:border-night-border" data-count={k} data-value={c[k]}>
                        <dt className="text-gray-600">{t(`p2.count.${k}`)}</dt>
                        <dd dir="ltr" className="text-start text-[15px] font-extrabold tabular-nums text-navy-600 rtl:text-end dark:text-gray-100">
                            {formatCount(c[k])}
                        </dd>
                    </div>
                ))}
            </dl>

            <div className="text-eyebrow mb-2">{t('p2.step.params')}</div>
            <div className="mb-4">
                <ParamsSummary kind={job.kind} params={job.params} />
            </div>

            <div className="text-eyebrow mb-2">{t('p2.jobs.attemptsTitle')}</div>
            <div className="mb-4 overflow-x-auto rounded-lg border border-gray-300 dark:border-night-border" data-testid="p2-job-attempts">
                <Table>
                    <THead>
                        <Tr>
                            <Th>#</Th>
                            <Th>{t('p2.jobs.phase')}</Th>
                            <Th>{t('p2.jobs.started')}</Th>
                            <Th>{t('p2.jobs.outcome')}</Th>
                            <Th className="text-end">{t('p2.jobs.rowsDone')}</Th>
                        </Tr>
                    </THead>
                    <TBody>
                        {job.attempts.length === 0 ? (
                            <Tr>
                                <Td colSpan={5} className="text-gray-600">
                                    {t('p2.jobs.noAttempts')}
                                </Td>
                            </Tr>
                        ) : (
                            job.attempts.map((a) => (
                                <Tr key={a.attempt} data-testid="p2-attempt-row" data-outcome={a.outcome}>
                                    <Td className="tabular-nums">{a.attempt}</Td>
                                    <Td>{t(`p2.phase.${a.phase}`)}</Td>
                                    <Td className="text-[12.5px]">
                                        <RiyadhTime iso={a.started_at} />
                                        {a.finished_at && (
                                            <div className="text-gray-600">
                                                → <RiyadhTime iso={a.finished_at} />
                                            </div>
                                        )}
                                    </Td>
                                    <Td className="text-[12.5px]">
                                        {t(`p2.attempt.${a.outcome}`, { defaultValue: a.outcome })}
                                        {a.error && <div className="text-danger">{a.error}</div>}
                                        {a.worker_id && <Ltr className="block text-[11px] text-gray-500">{a.worker_id}</Ltr>}
                                    </Td>
                                    <Td className="text-end tabular-nums">{formatCount(a.rows_processed)}</Td>
                                </Tr>
                            ))
                        )}
                    </TBody>
                </Table>
            </div>

            <div className="text-eyebrow mb-2">{t('p2.results.files')}</div>
            <div className="mb-4">
                <ArtifactButtons job={job} />
            </div>

            <JobActions job={job} onChanged={changed} />
        </div>
    )
}

export default JobDetailPanel
