/**
 * Display parts of the Phase 2 bulk wizard and jobs screen: stepper, status
 * badges, count cards, progress bar, artifact downloads.
 */
import classNames from 'classnames'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { HiCheck, HiOutlineDownload } from 'react-icons/hi'
import Button from '@/components/ui/Button'
import StatusPill from '@/components/shared/StatusPill'
import type { StatusTone } from '@/components/shared/StatusPill'
import { InlineError } from '@/components/v2/States'
import { RiyadhTime } from '@/components/v2/Display'
import { bulkCodeLabel, describeBulkError, downloadArtifact } from '@/services/v2'
import type { ArtifactKind, BulkEligibility, BulkJob, BulkJobDetail, BulkOutcome, BulkStatus } from '@/services/v2'
import { formatCount } from '@/utils/v2/format'
import type { ReactNode } from 'react'

/* ---------------------------------------------------------------- stepper */

export type StepKey =
    | 'source'
    | 'upload'
    | 'mapping'
    | 'params'
    | 'validating'
    | 'preview'
    | 'confirm'
    | 'running'
    | 'results'

export type StepState = 'done' | 'current' | 'todo' | 'skipped'

const ALL_STEPS: StepKey[] = [
    'source',
    'upload',
    'mapping',
    'params',
    'validating',
    'preview',
    'confirm',
    'running',
    'results',
]

/**
 * Nine steps, always in the same order. A step the job does not need (upload
 * and mapping without a spreadsheet; confirm and running for a read-only kind)
 * is shown as "not needed" so the numbering never shifts.
 */
export const Stepper = ({ states }: { states: Record<StepKey, StepState> }) => {
    const { t } = useTranslation()
    return (
        <ol
            className="mb-5 flex flex-wrap items-stretch gap-y-2 overflow-x-auto rounded-lg border border-gray-300 bg-white px-3 py-2.5 dark:border-night-border dark:bg-night-card"
            data-testid="p2-stepper"
            aria-label={String(t('p2.wizard.steps'))}
        >
            {ALL_STEPS.map((key, i) => {
                const st = states[key]
                return (
                    <li
                        key={key}
                        data-step={key}
                        data-state={st}
                        aria-current={st === 'current' ? 'step' : undefined}
                        className="flex min-w-0 items-center"
                    >
                        {i > 0 && <span aria-hidden className="mx-2 h-px w-4 bg-gray-300 dark:bg-night-border" />}
                        <span
                            className={classNames(
                                'flex items-center gap-2 pb-1 text-[12.5px]',
                                st === 'current' && 'font-extrabold text-navy-600 shadow-[inset_0_-2px_0_#FF6600] dark:text-gray-100',
                                st === 'done' && 'text-gray-700 dark:text-night-body',
                                st === 'todo' && 'text-gray-600',
                                st === 'skipped' && 'text-gray-500 line-through decoration-gray-400'
                            )}
                        >
                            <span
                                aria-hidden
                                className={classNames(
                                    'inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[11px] font-extrabold tabular-nums',
                                    st === 'current' && 'bg-navy-600 text-white dark:bg-azure-600',
                                    st === 'done' && 'bg-success-tint text-success',
                                    st === 'todo' && 'border border-gray-400 text-gray-600',
                                    st === 'skipped' && 'border border-dashed border-gray-400 text-gray-500'
                                )}
                            >
                                {st === 'done' ? <HiCheck /> : i + 1}
                            </span>
                            <span className="whitespace-nowrap">{t(`p2.step.${key}`)}</span>
                            {st === 'skipped' && <span className="sr-only">{t('p2.step.skipped')}</span>}
                        </span>
                    </li>
                )
            })}
        </ol>
    )
}

/* ----------------------------------------------------------------- badges */

const STATUS_TONE: Record<BulkStatus, StatusTone> = {
    queued: 'neutral',
    validating: 'info',
    ready: 'warning',
    expired: 'neutral',
    confirmed: 'info',
    running: 'info',
    retrying: 'warning',
    succeeded: 'success',
    partially_succeeded: 'warning',
    failed: 'danger',
    cancelled: 'neutral',
}

export const JobStatusBadge = ({ status, size }: { status: BulkStatus; size?: 'row' | 'md' }) => {
    const { t } = useTranslation()
    return (
        <StatusPill tone={STATUS_TONE[status] || 'neutral'} size={size}>
            <span data-status={status} data-testid="p2-job-status">
                {t(`p2.status.${status}`)}
            </span>
        </StatusPill>
    )
}

const ELIG_TONE: Record<BulkEligibility, StatusTone> = {
    pending: 'neutral',
    eligible: 'success',
    excluded: 'warning',
    error: 'danger',
}

const OUTCOME_TONE: Record<BulkOutcome, StatusTone> = {
    pending: 'neutral',
    succeeded: 'success',
    skipped: 'warning',
    failed: 'danger',
}

export const EligibilityBadge = ({ value }: { value: BulkEligibility }) => {
    const { t } = useTranslation()
    return (
        <StatusPill tone={ELIG_TONE[value] || 'neutral'} size="row">
            <span data-eligibility={value}>{t(`p2.elig.${value}`)}</span>
        </StatusPill>
    )
}

export const OutcomeBadge = ({ value }: { value: BulkOutcome }) => {
    const { t } = useTranslation()
    return (
        <StatusPill tone={OUTCOME_TONE[value] || 'neutral'} size="row">
            <span data-outcome={value}>{t(`p2.outcome.${value}`)}</span>
        </StatusPill>
    )
}

/** A code with its bilingual label; the code itself stays available in the tooltip. */
export const CodeText = ({ code, message, className }: { code: string | null; message?: string | null; className?: string }) => {
    const { t } = useTranslation()
    if (!code) return null
    return (
        <span className={classNames('text-[12.5px] text-gray-700 dark:text-night-body', className)} title={code} data-code={code}>
            {bulkCodeLabel(t, code, message)}
        </span>
    )
}

/* ------------------------------------------------------------ count cards */

export const CountCard = ({
    label,
    value,
    tone,
    lead,
    testId,
    onClick,
    active,
}: {
    label: ReactNode
    value: number | null | undefined
    tone?: 'success' | 'warning' | 'danger' | 'neutral'
    lead?: boolean
    testId?: string
    onClick?: () => void
    active?: boolean
}) => {
    const Tag = onClick ? 'button' : 'div'
    return (
        <Tag
            type={onClick ? 'button' : undefined}
            onClick={onClick}
            data-testid={testId}
            data-value={value ?? ''}
            aria-pressed={onClick ? !!active : undefined}
            className={classNames(
                'min-w-[140px] flex-1 rounded-lg border bg-white px-5 py-4 text-start dark:bg-night-card',
                active ? 'border-azure-600 dark:border-azure-300' : 'border-gray-300 dark:border-night-border',
                lead && 'shadow-[inset_3px_0_0_#FF6600] rtl:shadow-[inset_-3px_0_0_#FF6600]',
                onClick && 'hover:bg-gray-50 dark:hover:bg-night-raised'
            )}
        >
            <div className="text-eyebrow mb-1">{label}</div>
            <div
                dir="ltr"
                className={classNames(
                    'text-start text-[28px] font-extrabold leading-tight tabular-nums rtl:text-end',
                    tone === 'success' && 'text-success',
                    tone === 'warning' && 'text-warning',
                    tone === 'danger' && 'text-danger',
                    (!tone || tone === 'neutral') && 'text-navy-600 dark:text-gray-100'
                )}
            >
                {formatCount(value)}
            </div>
        </Tag>
    )
}

/* --------------------------------------------------------------- progress */

export const ProgressBar = ({
    done,
    total,
    percent,
    label,
}: {
    done: number
    total: number
    percent: number
    label: ReactNode
}) => (
    <div data-testid="p2-progress" data-done={done} data-total={total}>
        <div className="mb-2 flex items-center justify-between gap-3 text-[13px]">
            <span className="font-extrabold text-navy-600 dark:text-gray-100">{label}</span>
            <span dir="ltr" className="tabular-nums text-gray-700 dark:text-night-body">
                {formatCount(done)} / {formatCount(total)} · {percent.toFixed(1)}%
            </span>
        </div>
        <div
            role="progressbar"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={Math.round(percent)}
            className="h-2 w-full overflow-hidden rounded-full bg-gray-200 dark:bg-night-raised"
        >
            <div
                className="h-full rounded-full bg-azure-600 transition-[width] duration-300"
                style={{ width: `${Math.max(0, Math.min(100, percent))}%` }}
            />
        </div>
    </div>
)

/* ------------------------------------------------------------- card frame */

export const Panel = ({
    title,
    actions,
    children,
    testId,
    className,
}: {
    title?: ReactNode
    actions?: ReactNode
    children: ReactNode
    testId?: string
    className?: string
}) => (
    <section
        data-testid={testId}
        className={classNames(
            'mb-4 rounded-lg border border-gray-300 bg-white dark:border-night-border dark:bg-night-card',
            className
        )}
    >
        {(title || actions) && (
            <div className="flex flex-wrap items-center justify-between gap-2 border-b border-gray-300 px-5 py-3 dark:border-night-border">
                {title && <h4 className="mb-0 text-[15px] font-extrabold text-navy-600 dark:text-gray-100">{title}</h4>}
                {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
            </div>
        )}
        <div className="px-5 py-4">{children}</div>
    </section>
)

/* -------------------------------------------------------------- artifacts */

const ARTIFACTS: ArtifactKind[] = ['preview', 'errors', 'results']

/**
 * Download buttons for a job's CSV files. A file that does not exist yet (the
 * results before execution) is not offered; a purged one says so. Only the
 * creator (or SuperAdmin with bulk_jobs_all) may download (`actions.download`).
 */
export const ArtifactButtons = ({ job, only }: { job: BulkJobDetail; only?: ArtifactKind[] }) => {
    const { t } = useTranslation()
    const [busy, setBusy] = useState<ArtifactKind | null>(null)
    const [error, setError] = useState<unknown>(null)
    const kinds = (only || ARTIFACTS).filter((k) => job.artifacts.some((a) => a.kind === k))
    if (!job.actions.download) {
        return <p className="text-[12.5px] text-gray-600">{t('p2.artifacts.noDownload')}</p>
    }
    if (kinds.length === 0) {
        return <p className="text-[12.5px] text-gray-600">{t('p2.artifacts.none')}</p>
    }
    return (
        <div data-testid="p2-artifacts">
            <div className="flex flex-wrap items-center gap-2">
                {kinds.map((k) => {
                    const a = job.artifacts.find((x) => x.kind === k)
                    if (!a) return null
                    const purged = !!a.purged_at || !a.available
                    return (
                        <Button
                            key={k}
                            size="sm"
                            icon={<HiOutlineDownload />}
                            loading={busy === k}
                            disabled={purged}
                            data-testid={`p2-download-${k}`}
                            title={a.file_name}
                            onClick={async () => {
                                setBusy(k)
                                setError(null)
                                try {
                                    await downloadArtifact(job.id, k, a.file_name)
                                } catch (e) {
                                    setError(e)
                                } finally {
                                    setBusy(null)
                                }
                            }}
                        >
                            <bdi>{t(`p2.artifacts.${k}`)}</bdi>
                            <span dir="ltr" className="px-1 font-normal tabular-nums text-gray-600">
                                ({formatCount(a.row_count)})
                            </span>
                        </Button>
                    )
                })}
            </div>
            <div className="mt-2 text-[12px] text-gray-600">
                {job.artifacts.some((a) => a.purged_at) ? (
                    t('p2.artifacts.purged')
                ) : (
                    <>
                        {t('p2.artifacts.expires')}{' '}
                        <RiyadhTime iso={job.artifacts.map((a) => a.expires_at).sort()[0]} />
                        {' · '}
                        {t('p2.artifacts.formulaSafe')}
                    </>
                )}
            </div>
            <InlineError error={error} describe={describeBulkError} />
        </div>
    )
}

/* ----------------------------------------------------------------- source */

/** What a job runs on, in one short phrase. */
export const SourceText = ({ job }: { job: BulkJob }) => {
    const { t } = useTranslation()
    const s = job.source_summary
    if (job.source === 'selection') return <>{t('p2.source.selectionCount', { n: formatCount(s.count ?? 0) })}</>
    if (job.source === 'filter') return <>{`${t('p2.source.filter')} (${t(`p2.list.${s.list || 'clients'}`)})`}</>
    if (job.source === 'spreadsheet') return <bdi>{job.upload?.original_name || t('p2.source.spreadsheet')}</bdi>
    return <>{t(`p2.source.${job.source}`)}</>
}
