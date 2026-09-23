/**
 * Reviewed bulk wizard, steps 5–9 (`/jobs/run/:id`): validating → preview →
 * confirm → running → results. Reload-safe: everything comes from the job.
 *
 * - The job is polled (1.5 s) while it moves; the step follows its status.
 * - Preview: exact counts, `by_code` with bilingual labels, the per-collector
 *   plan (`kind_summary`), paged rows with filters in the URL hash, CSV
 *   downloads and the expiry time.
 * - Confirm sends the job's `expected_revision` and, when rows are left out,
 *   `accept_partial: true` (a required checkbox). A 409 is never retried
 *   silently: the user reloads and reviews again.
 * - A read-only kind (inquiry) goes straight from validating to its results.
 */
import { useMemo, useState } from 'react'
import { Link, useLocation, useParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import Button from '@/components/ui/Button'
import Checkbox from '@/components/ui/Checkbox'
import Table from '@/components/ui/Table'
import { ErrorState, InlineError, Note, SkeletonLines } from '@/components/v2/States'
import { PageHead } from '@/components/v2/ListParts'
import { Ltr, RiyadhTime } from '@/components/v2/Display'
import { bulkApi, bulkCodeLabel, describeBulkError, isV2Error } from '@/services/v2'
import type { BulkJobDetail, BulkPreview } from '@/services/v2'
import useHashState, { hashCodec } from '@/utils/hooks/useHashState'
import type { HashSchema } from '@/utils/hooks/useHashState'
import { useActionKey, useV2Query } from '@/utils/v2/hooks'
import { formatCount } from '@/utils/v2/format'
import { ArtifactButtons, CountCard, JobStatusBadge, Panel, ProgressBar, Stepper } from './parts'
import type { StepKey, StepState } from './parts'
import { useKindLabel, personLabel, shortId, useJob } from './shared'
import type { BulkLaunch } from './shared'
import { InquiryTable, RowsTable } from './RowsTable'
import type { RowsFilter } from './RowsTable'
import JobActions from './JobActions'
import { ParamsSummary } from './params'

const { THead, TBody, Tr, Th, Td } = Table

type Hash = { view: 'preview' | 'confirm'; tab: string; code: string | undefined; q: string | undefined; page: number }

const TAB_VALUES = ['all', 'eligible', 'excluded', 'error', 'succeeded', 'skipped', 'failed']

const schema: HashSchema<Hash> = {
    view: { key: 'view', codec: hashCodec.oneOf<'preview' | 'confirm'>(['preview', 'confirm']), default: 'preview' },
    tab: { key: 'rows', codec: hashCodec.oneOf(TAB_VALUES), default: 'all' },
    code: { key: 'code', codec: hashCodec.text({ maxLength: 80 }), default: undefined },
    q: { key: 'q', codec: hashCodec.text({ maxLength: 200 }), default: undefined, history: 'replace' },
    page: { key: 'page', codec: hashCodec.int({ min: 1, max: 100000 }), default: 1 },
}

type Stage = 'validating' | 'failed_prepare' | 'preview' | 'confirm' | 'expired' | 'cancelled_prepare' | 'running' | 'results'

function stageOf(job: BulkJobDetail, view: 'preview' | 'confirm'): Stage {
    const s = job.status
    if (s === 'queued' || s === 'validating' || (s === 'retrying' && job.phase === 'prepare')) return 'validating'
    if (s === 'ready') return view === 'confirm' ? 'confirm' : 'preview'
    if (s === 'expired') return 'expired'
    if (s === 'confirmed' || s === 'running' || (s === 'retrying' && job.phase === 'execute')) return 'running'
    if (s === 'failed' && job.phase === 'prepare') return 'failed_prepare'
    if (s === 'cancelled' && job.phase === 'prepare') return 'cancelled_prepare'
    return 'results'
}

const STAGE_STEP: Record<Stage, StepKey> = {
    validating: 'validating',
    failed_prepare: 'validating',
    preview: 'preview',
    expired: 'preview',
    cancelled_prepare: 'preview',
    confirm: 'confirm',
    running: 'running',
    results: 'results',
}

const ORDER: StepKey[] = ['source', 'upload', 'mapping', 'params', 'validating', 'preview', 'confirm', 'running', 'results']

function stepStates(job: BulkJobDetail, stage: Stage): Record<StepKey, StepState> {
    const current = job.read_only && stage === 'results' ? 'results' : STAGE_STEP[stage]
    const ci = ORDER.indexOf(current)
    const st = {} as Record<StepKey, StepState>
    ORDER.forEach((k, i) => {
        st[k] = i < ci ? 'done' : i === ci ? 'current' : 'todo'
    })
    if (job.source !== 'spreadsheet') {
        st.upload = 'skipped'
        st.mapping = 'skipped'
    }
    if (job.read_only) {
        st.preview = 'skipped'
        st.confirm = 'skipped'
        st.running = 'skipped'
    }
    return st
}

const RunJobPage = () => {
    const { t } = useTranslation()
    const { id = '' } = useParams()
    const location = useLocation()
    const launch = ((location.state as { bulkLaunch?: BulkLaunch } | null)?.bulkLaunch || {}) as BulkLaunch
    const { job, error, loading, reload } = useJob(id)
    const label = useKindLabel()
    const { state, setState } = useHashState(schema)

    if (loading && !job) {
        return (
            <div data-testid="p2-run">
                <PageHead title={t('p2.wizard.title')} />
                <SkeletonLines rows={8} />
            </div>
        )
    }
    if (error && !job) {
        return (
            <div data-testid="p2-run">
                <PageHead title={t('p2.wizard.title')} actions={<Link to="/jobs" className="text-[13px] font-extrabold text-azure-600 hover:underline">{t('p2.jobs.title')}</Link>} />
                <ErrorState error={error} onRetry={reload} describe={describeBulkError} />
            </div>
        )
    }
    if (!job) return null
    return <Run job={job} pollError={error} reload={reload} launch={launch} title={label(job.kind_label, job.kind)} hash={state} setHash={setState} />
}

const Run = ({
    job,
    pollError,
    reload,
    launch,
    title,
    hash,
    setHash,
}: {
    job: BulkJobDetail
    pollError: unknown
    reload: () => void
    launch: BulkLaunch
    title: string
    hash: Hash
    setHash: (patch: Partial<Hash>, options?: { replace?: boolean }) => void
}) => {
    const { t } = useTranslation()
    const stage = stageOf(job, hash.view)
    const hasPreview = stage !== 'validating' && stage !== 'running' && !(stage === 'failed_prepare' && !job.preview.ready_at) && !(stage === 'cancelled_prepare' && !job.preview.ready_at)
    const preview = useV2Query(hasPreview ? (signal) => bulkApi.preview(job.id, signal) : null, [job.id, job.revision, job.status, hasPreview])
    const states = useMemo(() => stepStates(job, stage), [job, stage])
    const filter: RowsFilter = { tab: hash.tab, code: hash.code, q: hash.q, page: hash.page }
    const onFilter = (patch: Partial<RowsFilter>) => setHash(patch)

    return (
        <div data-testid="p2-run" data-job={job.id} data-status={job.status} data-stage={stage} data-kind={job.kind} data-revision={job.revision}>
            <PageHead
                title={title}
                subtitle={
                    <span className="flex flex-wrap items-center gap-2">
                        <JobStatusBadge status={job.status} />
                        <span>
                            {t('p2.run.job')} <Ltr>{shortId(job.id)}</Ltr> · {personLabel(job.created_by)} · <RiyadhTime iso={job.created_at} />
                        </span>
                        {job.reopened_from && (
                            <Link className="font-extrabold text-azure-600 hover:underline" to={`/jobs/run/${job.reopened_from}`}>
                                {t('p2.run.reopenedFrom', { id: shortId(job.reopened_from) })}
                            </Link>
                        )}
                    </span>
                }
                onRefresh={reload}
                actions={
                    <>
                        {launch.from && (
                            <Link to={launch.from.path} className="text-[13px] font-extrabold text-azure-600 hover:underline" data-testid="p2-back-to-caller">
                                {t(launch.from.labelKey)}
                            </Link>
                        )}
                        <Link to={`/jobs#sel=${job.id}`} className="text-[13px] font-extrabold text-azure-600 hover:underline" data-testid="p2-to-jobs">
                            {t('p2.jobs.title')}
                        </Link>
                    </>
                }
            />
            <Stepper states={states} />
            {pollError ? <InlineError error={pollError} describe={describeBulkError} /> : null}

            <SetupSummary job={job} />

            {stage === 'validating' && (
                <Panel title={t('p2.step.validating')} testId="p2-stage-validating" actions={<JobActions job={job} onChanged={reload} only={['cancel']} />}>
                    <ProgressBar done={job.progress.done} total={job.progress.total} percent={job.progress.percent} label={t('p2.run.validating')} />
                    <Note className="mt-3">{t('p2.run.validatingNote')}</Note>
                    <CancelNote job={job} />
                </Panel>
            )}

            {stage === 'failed_prepare' && (
                <Panel title={t('p2.run.failedPrepare')} testId="p2-stage-failed" actions={<JobActions job={job} onChanged={reload} />}>
                    <JobError job={job} />
                </Panel>
            )}

            {(stage === 'expired' || stage === 'cancelled_prepare') && (
                <Panel title={t(stage === 'expired' ? 'p2.run.expiredTitle' : 'p2.run.cancelledTitle')} testId={`p2-stage-${stage}`} actions={<JobActions job={job} onChanged={reload} />}>
                    {stage === 'expired' ? (
                        <p className="text-[13.5px]" data-testid="p2-expired">
                            {t('p2.run.expiredBody')} <RiyadhTime iso={job.preview.expires_at} />
                        </p>
                    ) : (
                        <CancelNote job={job} />
                    )}
                </Panel>
            )}

            {(stage === 'preview' || stage === 'expired' || (stage === 'cancelled_prepare' && hasPreview)) && !job.read_only && (
                <PreviewPanel job={job} preview={preview.data} previewError={preview.error} launch={launch} filter={filter} onFilter={onFilter} onConfirm={stage === 'preview' ? () => setHash({ view: 'confirm' }) : undefined} reload={reload} />
            )}

            {stage === 'confirm' && (
                <ConfirmPanel
                    job={job}
                    preview={preview.data}
                    onBack={() => setHash({ view: 'preview' })}
                    onDone={() => {
                        setHash({ view: 'preview' }, { replace: true })
                        reload()
                    }}
                    reload={() => {
                        setHash({ view: 'preview' }, { replace: true })
                        reload()
                        preview.reload()
                    }}
                />
            )}

            {stage === 'running' && (
                <Panel title={t('p2.step.running')} testId="p2-stage-running" actions={<JobActions job={job} onChanged={reload} only={['cancel']} />}>
                    <ProgressBar done={job.progress.done} total={job.progress.total} percent={job.progress.percent} label={t('p2.run.running')} />
                    <Note className="mt-3">{t('p2.run.runningNote')}</Note>
                    <CancelNote job={job} />
                </Panel>
            )}

            {stage === 'results' && (
                <ResultsPanel job={job} preview={preview.data} filter={filter} onFilter={onFilter} reload={reload} />
            )}
        </div>
    )
}

/* --------------------------------------------------------------- summary */

const SetupSummary = ({ job }: { job: BulkJobDetail }) => {
    const { t } = useTranslation()
    const src = job.source_summary
    let source = t(`p2.source.${job.source}`)
    if (job.source === 'selection') source = t('p2.source.selectionCount', { n: formatCount(src.count ?? 0) })
    if (job.source === 'filter') source = `${t('p2.source.filter')} (${t(`p2.list.${src.list || 'clients'}`)})`
    return (
        <Panel title={t('p2.run.setup')} testId="p2-setup">
            <div className="grid grid-cols-1 gap-4 text-[13px] lg:grid-cols-3">
                <div>
                    <div className="text-eyebrow mb-1">{t('p2.step.source')}</div>
                    <div data-testid="p2-setup-source" data-source={job.source}>
                        {source}
                    </div>
                    {job.source === 'filter' && src.query && (
                        <Ltr className="mt-1 block break-all font-mono text-[11px] text-gray-600" title={src.query}>
                            {src.query.length > 180 ? `${src.query.slice(0, 180)}…` : src.query}
                        </Ltr>
                    )}
                </div>
                {job.upload && (
                    <div>
                        <div className="text-eyebrow mb-1">{t('p2.step.upload')}</div>
                        <div>
                            <bdi className="font-extrabold">{job.upload.original_name}</bdi> · <bdi>{job.sheet}</bdi> · {t('p2.upload.rowsN', { n: formatCount(job.upload.row_count) })}
                        </div>
                        {job.upload.purged && <div className="text-danger">{t('p2.run.uploadPurged')}</div>}
                        {job.mapping && (
                            <div className="mt-1 text-gray-600">
                                {Object.entries(job.mapping).map(([f, c]) => (
                                    <div key={f}>
                                        {t(`p2.field.${f}`, { defaultValue: f })} ← <bdi>{c}</bdi>
                                    </div>
                                ))}
                            </div>
                        )}
                    </div>
                )}
                <div>
                    <div className="text-eyebrow mb-1">{t('p2.step.params')}</div>
                    <ParamsSummary kind={job.kind} params={job.params} />
                </div>
            </div>
        </Panel>
    )
}

const CancelNote = ({ job }: { job: BulkJobDetail }) => {
    const { t } = useTranslation()
    if (!job.cancel) return null
    return (
        <Note className="mt-3">
            <span data-testid="p2-cancel-note">
                {job.cancel.cancelled_at ? t('p2.run.cancelledBy', { who: personLabel(job.cancel.requested_by) }) : t('p2.run.cancelRequested', { who: personLabel(job.cancel.requested_by) })}{' '}
                <RiyadhTime iso={job.cancel.cancelled_at || job.cancel.requested_at} />
                {job.cancel.reason ? ` · ${job.cancel.reason}` : ''}
            </span>
        </Note>
    )
}

const JobError = ({ job }: { job: BulkJobDetail }) => {
    const { t } = useTranslation()
    if (!job.error) return null
    return (
        <div role="alert" className="rounded-md border border-red-200 bg-danger-tint px-3 py-2 text-[13px] text-danger" data-testid="p2-job-error" data-code={job.error.code}>
            <div className="font-extrabold">{bulkCodeLabel(t, job.error.code, job.error.message)}</div>
            {job.error.message && <div className="text-[12.5px]">{job.error.message}</div>}
        </div>
    )
}

/* --------------------------------------------------------------- preview */

const ByCodeList = ({ preview, onPick, active }: { preview: BulkPreview; onPick: (code: string, eligibility: string) => void; active?: string }) => {
    const { t } = useTranslation()
    if (preview.by_code.length === 0) return <p className="text-[13px] text-gray-600">{t('p2.preview.noExclusions')}</p>
    return (
        <ul className="flex flex-col gap-1.5" data-testid="p2-by-code">
            {preview.by_code.map((c) => (
                <li key={`${c.eligibility}:${c.code}`} data-code={c.code} data-count={c.count} data-eligibility={c.eligibility}>
                    <button
                        type="button"
                        onClick={() => onPick(c.code, c.eligibility)}
                        aria-pressed={active === c.code}
                        className="flex w-full items-center justify-between gap-3 rounded-md border border-gray-300 px-3 py-1.5 text-start text-[13px] hover:bg-gray-50 dark:border-night-border dark:hover:bg-night-raised"
                    >
                        <span className="flex min-w-0 items-center gap-2">
                            <span className={c.eligibility === 'error' ? 'h-[2px] w-2 shrink-0 bg-danger' : 'h-[7px] w-[7px] shrink-0 rotate-45 bg-warning'} aria-hidden />
                            <span className="min-w-0">
                                <span className="font-extrabold text-navy-600 dark:text-gray-100">{bulkCodeLabel(t, c.code, c.message)}</span>
                                <span className="ms-2 text-[12px] text-gray-600">{t(`p2.elig.${c.eligibility}`)}</span>
                            </span>
                        </span>
                        <span dir="ltr" className="font-extrabold tabular-nums">
                            {formatCount(c.count)}
                        </span>
                    </button>
                </li>
            ))}
        </ul>
    )
}

const TargetSummary = ({ preview, results }: { preview: BulkPreview | undefined; results?: boolean }) => {
    const { t } = useTranslation()
    const rows = preview?.kind_summary?.by_target
    if (!rows || rows.length === 0) return null
    return (
        <Panel title={t('p2.preview.byCollector')} testId="p2-by-target">
            <div className="overflow-x-auto">
                <Table>
                    <THead>
                        <Tr>
                            <Th>#</Th>
                            <Th>{t('p2.preview.collector')}</Th>
                            <Th className="text-end">{t('p2.preview.planned')}</Th>
                            {results && <Th className="text-end">{t('p2.outcome.succeeded')}</Th>}
                            {results && <Th className="text-end">{t('p2.outcome.skipped')}</Th>}
                            {results && <Th className="text-end">{t('p2.outcome.failed')}</Th>}
                        </Tr>
                    </THead>
                    <TBody>
                        {rows.map((r, i) => (
                            <Tr key={r.user_id} data-testid="p2-target-row" data-login={r.user_name || ''} data-planned={r.planned}>
                                <Td className="tabular-nums">{i + 1}</Td>
                                <Td>
                                    <bdi className="font-extrabold">{r.name || r.user_name}</bdi> <Ltr className="text-gray-600">{r.user_name}</Ltr>
                                </Td>
                                <Td className="text-end tabular-nums">{formatCount(r.planned)}</Td>
                                {results && <Td className="text-end tabular-nums">{formatCount(r.succeeded)}</Td>}
                                {results && <Td className="text-end tabular-nums">{formatCount(r.skipped)}</Td>}
                                {results && <Td className="text-end tabular-nums">{formatCount(r.failed)}</Td>}
                            </Tr>
                        ))}
                    </TBody>
                </Table>
            </div>
        </Panel>
    )
}

const PreviewPanel = ({
    job,
    preview,
    previewError,
    launch,
    filter,
    onFilter,
    onConfirm,
    reload,
}: {
    job: BulkJobDetail
    preview: BulkPreview | undefined
    previewError: unknown
    launch: BulkLaunch
    filter: RowsFilter
    onFilter: (p: Partial<RowsFilter>) => void
    onConfirm?: () => void
    reload: () => void
}) => {
    const { t } = useTranslation()
    const c = preview?.counts || { total: job.counts.total, eligible: job.counts.eligible, excluded: job.counts.excluded, error: job.counts.error }
    const listCount = job.source === 'filter' ? launch.filter?.count : null
    return (
        <div data-testid="p2-stage-preview">
            <div className="mb-4 flex flex-wrap gap-3" data-testid="p2-preview-counts">
                <CountCard label={t('p2.count.total')} value={c.total} testId="p2-count-total" onClick={() => onFilter({ tab: 'all', code: undefined, page: 1 })} active={filter.tab === 'all' && !filter.code} />
                <CountCard label={t('p2.count.eligible')} value={c.eligible} tone="success" lead testId="p2-count-eligible" onClick={() => onFilter({ tab: 'eligible', code: undefined, page: 1 })} active={filter.tab === 'eligible'} />
                <CountCard label={t('p2.count.excluded')} value={c.excluded} tone="warning" testId="p2-count-excluded" onClick={() => onFilter({ tab: 'excluded', code: undefined, page: 1 })} active={filter.tab === 'excluded'} />
                <CountCard label={t('p2.count.error')} value={c.error} tone="danger" testId="p2-count-error" onClick={() => onFilter({ tab: 'error', code: undefined, page: 1 })} active={filter.tab === 'error'} />
            </div>

            {listCount !== null && listCount !== undefined && (
                <Note className="mb-3">
                    <span data-testid="p2-filter-compare" data-list={listCount} data-preview={c.total} className={listCount === c.total ? '' : 'font-extrabold text-warning'}>
                        {listCount === c.total ? t('p2.preview.filterSame', { n: formatCount(c.total) }) : t('p2.preview.filterDiffers', { list: formatCount(listCount), preview: formatCount(c.total) })}
                    </span>
                </Note>
            )}

            <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1fr)_380px]">
                <Panel title={t('p2.preview.byCode')}>
                    {previewError && !preview ? <ErrorState error={previewError} describe={describeBulkError} /> : preview ? <ByCodeList preview={preview} active={filter.code} onPick={(code, elig) => onFilter({ code, tab: elig, page: 1 })} /> : <SkeletonLines rows={2} />}
                </Panel>
                <Panel title={t('p2.preview.filesAndExpiry')}>
                    <div className="mb-3 text-[13px]" data-testid="p2-expiry">
                        {job.preview.expires_at ? (
                            <>
                                <span className="text-gray-600">{job.preview.expired || job.status === 'expired' ? t('p2.preview.expiredAt') : t('p2.preview.validUntil')} </span>
                                <RiyadhTime iso={job.preview.expires_at} className="font-extrabold" />
                            </>
                        ) : (
                            '—'
                        )}
                    </div>
                    <ArtifactButtons job={job} only={['preview', 'errors']} />
                </Panel>
            </div>

            <TargetSummary preview={preview} />

            <div className="mb-2 mt-2 text-eyebrow">{t('p2.preview.rows')}</div>
            <RowsTable job={job} preview={preview} mode="preview" filter={filter} onFilter={onFilter} />

            {job.status === 'ready' && (
                <div className="mt-4 flex flex-wrap items-center justify-between gap-2">
                    <JobActions job={job} onChanged={reload} only={['cancel']} />
                    {onConfirm && job.actions.confirm ? (
                        <Button size="sm" variant="solid" onClick={onConfirm} data-testid="p2-to-confirm">
                            {t('p2.preview.continue')}
                        </Button>
                    ) : (
                        <Note>{c.eligible === 0 ? t('p2.preview.nothingEligible') : t('p2.preview.cannotConfirm')}</Note>
                    )}
                </div>
            )}
        </div>
    )
}

/* --------------------------------------------------------------- confirm */

const ConfirmPanel = ({
    job,
    preview,
    onBack,
    onDone,
    reload,
}: {
    job: BulkJobDetail
    preview: BulkPreview | undefined
    onBack: () => void
    onDone: () => void
    reload: () => void
}) => {
    const { t } = useTranslation()
    const { key, renew } = useActionKey(true)
    const [accept, setAccept] = useState(false)
    const [busy, setBusy] = useState(false)
    const [error, setError] = useState<unknown>(null)
    const c = preview?.counts || job.counts
    const left = c.excluded + c.error
    const needs = job.needs_accept_partial
    const can = job.actions.confirm && (!needs || accept) && !busy

    const confirm = async () => {
        if (!can) return
        setBusy(true)
        setError(null)
        try {
            await bulkApi.confirm(job.id, { expected_revision: job.revision, ...(needs || accept ? { accept_partial: accept } : {}) }, key)
            renew()
            onDone()
        } catch (e) {
            setError(e)
            setBusy(false)
        }
    }

    return (
        <Panel title={t('p2.step.confirm')} testId="p2-stage-confirm">
            <div className="max-w-[760px]">
                <p className="mb-2 text-[15px] text-navy-600 dark:text-gray-100" data-testid="p2-confirm-summary">
                    {t('p2.confirm.willRun', { n: formatCount(c.eligible) })}
                </p>
                {left > 0 && (
                    <p className="mb-3 text-[13.5px] text-gray-700 dark:text-night-body">
                        {t('p2.confirm.leftOut', { excluded: formatCount(c.excluded), error: formatCount(c.error) })}
                    </p>
                )}
                <ul className="mb-4 list-disc ps-5 text-[13px] text-gray-700 dark:text-night-body">
                    <li>{t('p2.confirm.rechecks')}</li>
                    <li>
                        {t('p2.confirm.expires')} <RiyadhTime iso={job.preview.expires_at} />
                    </li>
                    <li>
                        {t('p2.confirm.revision')} <Ltr data-testid="p2-confirm-revision">{job.revision}</Ltr>
                    </li>
                </ul>
                {needs && (
                    <div className="mb-4 rounded-md border border-warning-tint bg-warning-tint px-3 py-2" data-testid="p2-accept-partial">
                        <Checkbox checked={accept} onChange={(v) => setAccept(!!v)}>
                            <span className="text-[13.5px] font-extrabold text-warning">
                                {t('p2.confirm.acceptPartial', { eligible: formatCount(c.eligible), left: formatCount(left) })}
                            </span>
                        </Checkbox>
                    </div>
                )}
                <InlineError
                    error={error}
                    describe={describeBulkError}
                    onReload={() => {
                        setError(null)
                        reload()
                    }}
                />
                {isV2Error(error) && error.code === 'preview_expired' && (
                    <Button size="sm" className="mt-2" onClick={reload}>
                        {t('p1.action.reloadLatest')}
                    </Button>
                )}
                <div className="mt-4 flex flex-wrap items-center gap-2">
                    <Button size="sm" onClick={onBack} disabled={busy} data-testid="p2-confirm-back">
                        {t('p2.confirm.back')}
                    </Button>
                    <Button size="sm" variant="solid" loading={busy} disabled={!can} aria-disabled={!can} onClick={confirm} data-testid="p2-confirm-btn">
                        {t('p2.confirm.run', { n: formatCount(c.eligible) })}
                    </Button>
                </div>
            </div>
        </Panel>
    )
}

/* --------------------------------------------------------------- results */

const ResultsPanel = ({
    job,
    preview,
    filter,
    onFilter,
    reload,
}: {
    job: BulkJobDetail
    preview: BulkPreview | undefined
    filter: RowsFilter
    onFilter: (p: Partial<RowsFilter>) => void
    reload: () => void
}) => {
    const { t } = useTranslation()
    const c = job.counts
    if (job.read_only) {
        return (
            <div data-testid="p2-stage-results" data-read-only="1">
                <div className="mb-4 flex flex-wrap gap-3">
                    <CountCard label={t('p2.inq.rows')} value={c.total} testId="p2-count-total" />
                    <CountCard label={t('p2.inq.matched')} value={c.eligible} tone="success" lead testId="p2-count-eligible" />
                    <CountCard label={t('p2.inq.duplicates')} value={c.excluded} tone="warning" testId="p2-count-excluded" />
                    <CountCard label={t('p2.inq.notFound')} value={c.error} tone="danger" testId="p2-count-error" />
                </div>
                <Panel title={t('p2.results.files')} actions={<JobActions job={job} onChanged={reload} only={['reopen']} />}>
                    <ArtifactButtons job={job} />
                </Panel>
                <InquiryTable job={job} preview={preview} filter={filter} onFilter={onFilter} />
            </div>
        )
    }
    const notRun = c.excluded + c.error
    return (
        <div data-testid="p2-stage-results">
            <div className="mb-4 flex flex-wrap gap-3" data-testid="p2-result-counts">
                <CountCard label={t('p2.outcome.succeeded')} value={c.succeeded} tone="success" lead testId="p2-count-succeeded" />
                <CountCard label={t('p2.results.skipped')} value={c.skipped} tone="warning" testId="p2-count-skipped" />
                <CountCard label={t('p2.outcome.failed')} value={c.failed} tone="danger" testId="p2-count-failed" />
                <CountCard label={t('p2.results.notRun')} value={notRun} testId="p2-count-not-run" />
            </div>
            <Panel title={t('p2.results.summary')} actions={<JobActions job={job} onChanged={reload} only={['retry', 'reopen']} />}>
                <div className="grid grid-cols-1 gap-3 text-[13px] md:grid-cols-2">
                    <div>
                        <span className="text-gray-600">{t('p2.results.status')}: </span>
                        <JobStatusBadge status={job.status} size="row" />
                        {job.finished_at && (
                            <span className="ms-2">
                                <RiyadhTime iso={job.finished_at} />
                            </span>
                        )}
                    </div>
                    {job.confirmation && (
                        <div data-testid="p2-confirmed-by">
                            <span className="text-gray-600">{t('p2.results.confirmedBy')}: </span>
                            {personLabel(job.confirmation.by)} · <RiyadhTime iso={job.confirmation.at} />
                            {job.confirmation.accept_partial && <span className="ms-1 text-warning">({t('p2.results.partialAccepted')})</span>}
                        </div>
                    )}
                </div>
                <JobError job={job} />
                <CancelNote job={job} />
                {job.kind === 'attribute_corrections' && c.succeeded > 0 && (
                    <p className="mt-3 text-[13px]">
                        <Link className="font-extrabold text-azure-600 hover:underline" to="/corrections#status=pending" data-testid="p2-to-corrections">
                            {t('p2.results.reviewCorrections')}
                        </Link>
                    </p>
                )}
                <div className="mt-4">
                    <div className="text-eyebrow mb-2">{t('p2.results.files')}</div>
                    <ArtifactButtons job={job} />
                </div>
            </Panel>
            <TargetSummary preview={preview} results />
            <div className="mb-2 text-eyebrow">{t('p2.results.rows')}</div>
            <RowsTable job={job} preview={preview} mode="results" filter={filter} onFilter={onFilter} />
        </div>
    )
}

export default RunJobPage
