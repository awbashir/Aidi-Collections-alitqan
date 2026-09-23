/**
 * Bulk jobs (`/jobs`, derived key `bulk_jobs`: any bulk kind key or
 * bulk_jobs_all, mirroring `GET /v2/jobs`).
 *
 * Lists the runs the caller may see (own jobs; with bulk_jobs_all also the
 * jobs of users in scope), newest first, with kind / status / creator / date
 * filters and exact `counts_by_status` chips. The selected job opens in the
 * side panel: attempts, row counts, artifacts, cancel / retry / reopen, and a
 * link that resumes the wizard at the job's current step. Filters, page and
 * the selected job live in the URL hash.
 */
import classNames from 'classnames'
import { useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { HiX } from 'react-icons/hi'
import Table from '@/components/ui/Table'
import Input from '@/components/ui/Input'
import useHashState, { hashCodec } from '@/utils/hooks/useHashState'
import type { HashSchema } from '@/utils/hooks/useHashState'
import { BULK_STATUSES, bulkApi, describeBulkError, lookups } from '@/services/v2'
import type { BulkStatus, Option } from '@/services/v2'
import { useV2Query } from '@/utils/v2/hooks'
import { formatCount } from '@/utils/v2/format'
import { Ltr, RiyadhTime } from '@/components/v2/Display'
import { EmptyState, ErrorState, Note, SkeletonRows } from '@/components/v2/States'
import { FilterSelect, PageHead, PagerFooter, TableFrame, rowClass } from '@/components/v2/ListParts'
import { JobStatusBadge, SourceText } from '../bulk/parts'
import { personLabel, shortId, useBulkKinds, useKindLabel } from '../bulk/shared'
import JobDetailPanel from './JobDetailPanel'

const { THead, TBody, Tr, Th, Td } = Table

const PAGE_SIZE = 25

type HashState = {
    status: BulkStatus | 'all'
    kind: string | undefined
    creator: string | number | undefined
    from: string | undefined
    to: string | undefined
    page: number
    sel: string | number | undefined
}

const schema: HashSchema<HashState> = {
    status: { key: 'status', codec: hashCodec.oneOf<BulkStatus | 'all'>(['all', ...BULK_STATUSES]), default: 'all' },
    kind: { key: 'kind', codec: hashCodec.text({ maxLength: 40 }), default: undefined },
    creator: { key: 'by', codec: hashCodec.id(), default: undefined },
    from: { key: 'from', codec: hashCodec.date(), default: undefined },
    to: { key: 'to', codec: hashCodec.date(), default: undefined },
    page: { key: 'page', codec: hashCodec.int({ min: 1, max: 100000 }), default: 1 },
    sel: { key: 'sel', codec: hashCodec.id(), default: undefined, history: 'replace' },
}

const JobsScreen = () => {
    const { t } = useTranslation()
    const { state, setState } = useHashState(schema)
    const kinds = useBulkKinds()
    const label = useKindLabel()
    const [users, setUsers] = useState<Option[]>([])
    useEffect(() => {
        let alive = true
        lookups
            .users()
            .then((u) => alive && setUsers(u))
            .catch(() => undefined)
        return () => {
            alive = false
        }
    }, [])

    const q = {
        kind: state.kind,
        status: state.status === 'all' ? undefined : state.status,
        created_by: state.creator ? String(state.creator) : undefined,
        date_from: state.from,
        date_to: state.to,
        page: state.page,
        page_size: PAGE_SIZE,
    }
    const list = useV2Query((signal) => bulkApi.jobs(q, signal), [state.kind, state.status, state.creator, state.from, state.to, state.page])
    const data = list.data
    const sel = state.sel ? String(state.sel) : undefined

    const counts = data?.counts_by_status
    const all = counts ? Object.values(counts).reduce((a, b) => a + b, 0) : undefined
    const kindOptions = useMemo(() => kinds.kinds.map((k) => ({ value: k.key, label: label(k) })), [kinds.kinds, label])
    const creatorOptions = useMemo(() => [{ value: 'me', label: String(t('p2.jobs.me')) }, ...users], [users, t])
    const panelRef = useRef<HTMLElement>(null)
    useEffect(() => {
        if (sel) panelRef.current?.scrollIntoView?.({ block: 'nearest', behavior: 'smooth' })
    }, [sel])

    const active = !!(state.kind || state.creator || state.from || state.to)
    const cols = 6

    return (
        <div data-testid="p2-jobs">
            <PageHead title={t('p2.jobs.title')} subtitle={t('p2.jobs.subtitle')} onRefresh={list.reload} refreshing={list.loading && !!data} />

            <div className="mb-4 flex flex-wrap items-end gap-3" data-testid="p2-jobs-filters">
                <FilterSelect label={t('p2.jobs.kind')} options={kindOptions} value={state.kind} loading={kinds.loading} testId="p2-jobs-kind" onChange={(v) => setState({ kind: v, page: 1 })} />
                <FilterSelect label={t('p2.jobs.creator')} options={creatorOptions} value={state.creator ? String(state.creator) : undefined} testId="p2-jobs-creator" onChange={(v) => setState({ creator: v, page: 1 })} />
                <div className="w-[160px]">
                    <div className="text-eyebrow mb-1">{t('p1.filter.dateFrom')}</div>
                    <Input type="date" size="sm" value={state.from || ''} onChange={(e) => setState({ from: e.target.value || undefined, page: 1 })} data-testid="p2-jobs-from" />
                </div>
                <div className="w-[160px]">
                    <div className="text-eyebrow mb-1">{t('p1.filter.dateTo')}</div>
                    <Input type="date" size="sm" value={state.to || ''} onChange={(e) => setState({ to: e.target.value || undefined, page: 1 })} data-testid="p2-jobs-to" />
                </div>
                {active && (
                    <button type="button" className="h-9 text-[13px] font-extrabold text-azure-600 hover:underline" onClick={() => setState({ kind: undefined, creator: undefined, from: undefined, to: undefined, page: 1 })}>
                        {t('p1.filter.clear')}
                    </button>
                )}
            </div>

            <div className="mb-3 flex flex-wrap gap-2" role="group" aria-label={String(t('p2.jobs.byStatus'))} data-testid="p2-jobs-chips">
                {(['all', ...BULK_STATUSES] as Array<BulkStatus | 'all'>)
                    .filter((s) => s === 'all' || s === state.status || (counts && counts[s as BulkStatus] > 0))
                    .map((s) => {
                        const n = s === 'all' ? all : counts?.[s as BulkStatus]
                        const on = state.status === s
                        return (
                            <button
                                key={s}
                                type="button"
                                aria-pressed={on}
                                data-status={s}
                                data-count={n ?? ''}
                                onClick={() => setState({ status: s, page: 1 })}
                                className={classNames(
                                    'inline-flex h-7 items-center gap-2 rounded-pill border px-3 text-[12.5px] font-extrabold',
                                    on
                                        ? 'border-azure-500 bg-azure-50 text-azure-600 dark:border-azure-300 dark:bg-night-raised dark:text-azure-300'
                                        : 'border-gray-400 bg-white text-gray-700 hover:bg-gray-50 dark:border-night-border dark:bg-night-card dark:text-night-body'
                                )}
                            >
                                <span>{s === 'all' ? t('p2.jobs.all') : t(`p2.status.${s}`)}</span>
                                <span dir="ltr" className="tabular-nums">
                                    {formatCount(n)}
                                </span>
                            </button>
                        )
                    })}
            </div>

            <div className={sel ? 'grid grid-cols-1 gap-4 2xl:grid-cols-[minmax(0,1fr)_560px]' : ''}>
                <div className="min-w-0">
                    <TableFrame
                        testId="p2-jobs-table"
                        footer={data && data.total > 0 ? <PagerFooter page={state.page} pageSize={PAGE_SIZE} total={data.total} onPage={(page) => setState({ page })} /> : undefined}
                    >
                        {list.error && !data ? (
                            <ErrorState error={list.error} onRetry={list.reload} describe={describeBulkError} />
                        ) : (
                            <Table>
                                <THead>
                                    <Tr>
                                        <Th>{t('p2.jobs.col.job')}</Th>
                                        <Th>{t('p1.col.status')}</Th>
                                        <Th className="text-end">{t('p2.jobs.col.rows')}</Th>
                                        <Th className="text-end">{t('p2.jobs.col.outcome')}</Th>
                                        <Th>{t('p2.jobs.col.created')}</Th>
                                        <Th className="text-end">{t('p1.col.actions')}</Th>
                                    </Tr>
                                </THead>
                                <TBody>
                                    {list.loading && !data ? (
                                        <SkeletonRows cols={cols} />
                                    ) : data && data.items.length > 0 ? (
                                        data.items.map((j) => (
                                            <Tr
                                                key={j.id}
                                                className={rowClass(sel === j.id)}
                                                aria-current={sel === j.id ? 'true' : undefined}
                                                onClick={() => setState({ sel: j.id }, { replace: true })}
                                                data-testid="p2-job-row"
                                                data-id={j.id}
                                                data-kind={j.kind}
                                                data-status={j.status}
                                            >
                                                <Td className="min-w-[220px]">
                                                    <div className="font-extrabold text-navy-600 dark:text-gray-100">{label(j.kind_label, j.kind)}</div>
                                                    <div className="text-[12.5px] text-gray-600">
                                                        <Ltr>{shortId(j.id)}</Ltr> · <SourceText job={j} />
                                                    </div>
                                                </Td>
                                                <Td>
                                                    <div className="flex flex-col items-start gap-1">
                                                        <JobStatusBadge status={j.status} size="row" />
                                                        {['validating', 'running'].includes(j.status) && (
                                                            <span dir="ltr" className="text-[11.5px] tabular-nums text-gray-600">
                                                                {j.progress.percent.toFixed(1)}%
                                                            </span>
                                                        )}
                                                    </div>
                                                </Td>
                                                <Td className="text-end text-[12.5px] tabular-nums">
                                                    <div className="font-extrabold">{formatCount(j.counts.total)}</div>
                                                    <div className="text-gray-600" dir="ltr">
                                                        <span className="text-success">{formatCount(j.counts.eligible)}</span> / <span className="text-warning">{formatCount(j.counts.excluded)}</span> / <span className="text-danger">{formatCount(j.counts.error)}</span>
                                                    </div>
                                                </Td>
                                                <Td className="text-end text-[12.5px] tabular-nums">
                                                    {j.phase === 'execute' || j.read_only ? (
                                                        <span dir="ltr">
                                                            <span className="text-success">{formatCount(j.counts.succeeded)}</span> / <span className="text-warning">{formatCount(j.counts.skipped)}</span> / <span className="text-danger">{formatCount(j.counts.failed)}</span>
                                                        </span>
                                                    ) : (
                                                        <span className="text-gray-500">—</span>
                                                    )}
                                                </Td>
                                                <Td className="text-[12.5px]">
                                                    <div>{personLabel(j.created_by)}</div>
                                                    <RiyadhTime iso={j.created_at} className="text-gray-600" />
                                                </Td>
                                                <Td className="text-end">
                                                    <Link
                                                        to={`/jobs/run/${j.id}`}
                                                        onClick={(e) => e.stopPropagation()}
                                                        className="text-[13px] font-extrabold text-azure-600 hover:underline"
                                                        data-testid="p2-job-open"
                                                    >
                                                        {t(j.status === 'ready' ? 'p2.jobs.review' : 'p2.jobs.open')}
                                                    </Link>
                                                </Td>
                                            </Tr>
                                        ))
                                    ) : (
                                        <tr>
                                            <td colSpan={cols}>
                                                <EmptyState title={t('p2.jobs.empty')} hint={t('p2.jobs.emptyHint')} />
                                            </td>
                                        </tr>
                                    )}
                                </TBody>
                            </Table>
                        )}
                    </TableFrame>
                    {list.error && data ? <ErrorState error={list.error} onRetry={list.reload} describe={describeBulkError} /> : null}
                    <Note className="mt-2">{t('p2.jobs.note')}</Note>
                </div>

                {sel && (
                    <aside
                        ref={panelRef}
                        className="min-w-0 self-start rounded-lg border border-gray-300 bg-white p-5 dark:border-night-border dark:bg-night-card"
                        data-testid="p2-job-panel"
                        aria-label={String(t('p2.jobs.detail'))}
                    >
                        <div className="mb-3 flex items-center justify-between gap-2">
                            <span className="text-eyebrow">{t('p2.jobs.detail')}</span>
                            <button
                                type="button"
                                className="rounded p-1 text-gray-500 hover:bg-gray-100 dark:hover:bg-night-raised"
                                onClick={() => setState({ sel: undefined }, { replace: true })}
                                aria-label={String(t('p1.action.close'))}
                                data-testid="p2-job-panel-close"
                            >
                                <HiX />
                            </button>
                        </div>
                        <JobDetailPanel key={sel} id={sel} onChanged={list.reload} />
                    </aside>
                )}
            </div>
        </div>
    )
}

export default JobsScreen
