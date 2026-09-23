/**
 * Paged job rows (`GET /v2/bulk/jobs/<id>/rows`) with eligibility or outcome
 * tabs, a code filter and a row/value search. The filter state lives in the
 * URL hash of the run page. Tab counts are exact: from the preview for
 * eligibility, and from one `page_size=1` request per outcome.
 */
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import Table from '@/components/ui/Table'
import Input from '@/components/ui/Input'
import Select from '@/components/ui/Select'
import Button from '@/components/ui/Button'
import { EmptyState, ErrorState, Note, SkeletonRows } from '@/components/v2/States'
import { CountTabs, PagerFooter, TableFrame } from '@/components/v2/ListParts'
import { Ltr, MoneyText, RiyadhTime } from '@/components/v2/Display'
import { PlainBadge } from '@/components/v2/Badges'
import { bulkApi, bulkCodeLabel, describeBulkError } from '@/services/v2'
import type { BulkJobDetail, BulkPreview, BulkRow, Option } from '@/services/v2'
import { useV2Query } from '@/utils/v2/hooks'
import { caseLink } from '@/utils/v2/links'
import { CodeText, EligibilityBadge, OutcomeBadge } from './parts'
import RowDetails from './RowDetails'

const { THead, TBody, Tr, Th, Td } = Table

const ROWS_PAGE_SIZE = 25

export type RowsFilter = { tab: string; code: string | undefined; q: string | undefined; page: number }

const ELIG_TABS = ['all', 'eligible', 'excluded', 'error'] as const
const OUTCOME_TABS = ['all', 'succeeded', 'skipped', 'failed'] as const

const CaseCell = ({ row }: { row: BulkRow }) => {
    if (row.case) {
        return (
            <>
                <Link
                    to={caseLink(row.case.id)}
                    className="font-extrabold text-navy-600 hover:text-azure-600 hover:underline dark:text-gray-100"
                    data-testid="p2-row-case-link"
                >
                    <bdi>{row.case.name}</bdi>
                </Link>
                <Ltr className="block max-w-[220px] truncate text-[12.5px] text-gray-600" title={row.case.account_number}>
                    {row.case.account_number}
                </Ltr>
            </>
        )
    }
    const raw = row.raw ? Object.values(row.raw).filter((v) => v !== null && v !== '').join(' · ') : ''
    return (
        <Ltr className="block max-w-[240px] truncate text-[12.5px] text-gray-700 dark:text-night-body" title={raw}>
            {row.match_value || raw || '—'}
        </Ltr>
    )
}

export const RowsTable = ({
    job,
    preview,
    mode,
    filter,
    onFilter,
}: {
    job: BulkJobDetail
    preview: BulkPreview | undefined
    mode: 'preview' | 'results'
    filter: RowsFilter
    onFilter: (patch: Partial<RowsFilter>, replace?: boolean) => void
}) => {
    const { t } = useTranslation()
    const results = mode === 'results'
    const tabs = (results ? OUTCOME_TABS : ELIG_TABS) as readonly string[]
    const tab = tabs.includes(filter.tab) ? filter.tab : 'all'
    const [q, setQ] = useState(filter.q || '')
    useEffect(() => setQ(filter.q || ''), [filter.q])

    const query = {
        eligibility: !results && tab !== 'all' ? tab : undefined,
        outcome: results && tab !== 'all' ? tab : undefined,
        code: filter.code,
        q: filter.q,
        page: filter.page,
        page_size: ROWS_PAGE_SIZE,
    }
    const rows = useV2Query(
        (signal) => bulkApi.rows(job.id, query, signal),
        [job.id, job.revision, job.status, tab, filter.code, filter.q, filter.page, mode]
    )
    const outcomeCounts = useV2Query(
        results
            ? async (signal) => {
                  const res = await Promise.all(
                      OUTCOME_TABS.map((o) =>
                          bulkApi.rows(job.id, { outcome: o === 'all' ? undefined : o, page: 1, page_size: 1 }, signal)
                      )
                  )
                  const out: Record<string, number> = {}
                  OUTCOME_TABS.forEach((o, i) => {
                      out[o] = res[i].total
                  })
                  return out
              }
            : null,
        [job.id, job.revision, job.status, results]
    )

    const counts: Record<string, number | undefined> = results
        ? outcomeCounts.data || {}
        : {
              all: preview?.counts.total,
              eligible: preview?.counts.eligible,
              excluded: preview?.counts.excluded,
              error: preview?.counts.error,
          }

    const codeOptions: Option[] = (preview?.by_code || []).map((c) => ({
        value: c.code,
        label: `${bulkCodeLabel(t, c.code, c.message)} (${c.count})`,
    }))
    if (filter.code && !codeOptions.some((o) => o.value === filter.code)) {
        codeOptions.push({ value: filter.code, label: bulkCodeLabel(t, filter.code) })
    }
    const data = rows.data
    const cols = results ? 5 : 4

    return (
        <div data-testid="p2-rows" data-mode={mode}>
            <div className="mb-3">
                <CountTabs
                    secondary
                    testId="p2-rows-tabs"
                    value={tab}
                    onChange={(v) => onFilter({ tab: v, page: 1 })}
                    tabs={tabs.map((v) => ({
                        value: v,
                        label: t(results ? `p2.outcomeTab.${v}` : `p2.eligTab.${v}`),
                        count: counts[v],
                    }))}
                />
            </div>
            <TableFrame
                testId="p2-rows-table"
                toolbar={
                    <>
                        <div className="flex flex-wrap items-end gap-2">
                            <div className="w-[240px]" data-testid="p2-rows-code">
                                <Select
                                    size="sm"
                                    isClearable
                                    placeholder={String(t('p2.rows.anyCode'))}
                                    options={codeOptions}
                                    value={codeOptions.find((o) => o.value === filter.code) || null}
                                    onChange={(o) => onFilter({ code: (o as Option | null)?.value || undefined, page: 1 })}
                                />
                            </div>
                            <form
                                className="flex items-center gap-2"
                                onSubmit={(e) => {
                                    e.preventDefault()
                                    onFilter({ q: q.trim() || undefined, page: 1 })
                                }}
                            >
                                <Input size="sm" className="w-[220px]" dir="auto" placeholder={String(t('p2.rows.search'))} value={q} onChange={(e) => setQ(e.target.value)} data-testid="p2-rows-q" />
                                <Button size="sm" type="submit">
                                    {t('p2.rows.find')}
                                </Button>
                            </form>
                        </div>
                        {data?.rows_limited_to_your_scope && <PlainBadge tone="info">{t('p2.rows.limited')}</PlainBadge>}
                    </>
                }
                footer={
                    data && data.total > 0 ? (
                        <PagerFooter page={filter.page} pageSize={ROWS_PAGE_SIZE} total={data.total} onPage={(page) => onFilter({ page })} />
                    ) : undefined
                }
            >
                {rows.error && !data ? (
                    <ErrorState error={rows.error} onRetry={rows.reload} describe={describeBulkError} />
                ) : (
                    <Table>
                        <THead>
                            <Tr>
                                <Th className="w-[70px]">{t('p2.col.row')}</Th>
                                <Th>{t('p1.col.case')}</Th>
                                <Th>{t('p2.col.eligibility')}</Th>
                                {results && <Th>{t('p2.col.outcome')}</Th>}
                                <Th>{t('p2.col.details')}</Th>
                            </Tr>
                        </THead>
                        <TBody>
                            {rows.loading && !data ? (
                                <SkeletonRows cols={cols} />
                            ) : data && data.items.length > 0 ? (
                                data.items.map((r) => (
                                    <Tr key={r.id} data-testid="p2-row" data-row={r.row_no} data-eligibility={r.eligibility} data-outcome={r.outcome} data-code={r.eligibility_code || r.outcome_code || ''}>
                                        <Td>
                                            <Ltr className="tabular-nums">{r.row_no}</Ltr>
                                        </Td>
                                        <Td className="min-w-[180px]">
                                            <CaseCell row={r} />
                                        </Td>
                                        <Td>
                                            <div className="flex flex-col items-start gap-1">
                                                <EligibilityBadge value={r.eligibility} />
                                                <CodeText code={r.eligibility_code} message={r.eligibility_message} />
                                            </div>
                                        </Td>
                                        {results && (
                                            <Td>
                                                <div className="flex flex-col items-start gap-1">
                                                    <OutcomeBadge value={r.outcome} />
                                                    {r.outcome_code && r.outcome_code !== 'not_eligible' && <CodeText code={r.outcome_code} message={r.outcome_message} />}
                                                    {r.outcome_code === 'not_eligible' && <span className="text-[12px] text-gray-600">{bulkCodeLabel(t, 'not_eligible')}</span>}
                                                    {r.processed_at && <RiyadhTime iso={r.processed_at} className="text-[11.5px] text-gray-600" />}
                                                </div>
                                            </Td>
                                        )}
                                        <Td className="min-w-[200px] max-w-[420px]">
                                            <RowDetails kind={job.kind} row={r} />
                                        </Td>
                                    </Tr>
                                ))
                            ) : (
                                <tr>
                                    <td colSpan={cols}>
                                        <EmptyState title={t('p2.rows.empty')} />
                                    </td>
                                </tr>
                            )}
                        </TBody>
                    </Table>
                )}
            </TableFrame>
            {rows.error && data ? <ErrorState error={rows.error} onRetry={rows.reload} describe={describeBulkError} /> : null}
        </div>
    )
}

/* ---------------------------------------------------------------- inquiry */

const flag = (v: unknown) => v === true || v === 'true'

/**
 * Read-only inquiry results: one row per match. Rows keep the server's order
 * (sheet rows first; extra matches of a row are numbered after all sheet rows),
 * so the "source row" column says where each match came from.
 */
export const InquiryTable = ({
    job,
    preview,
    filter,
    onFilter,
}: {
    job: BulkJobDetail
    preview: BulkPreview | undefined
    filter: RowsFilter
    onFilter: (patch: Partial<RowsFilter>, replace?: boolean) => void
}) => {
    const { t } = useTranslation()
    const tab = (ELIG_TABS as readonly string[]).includes(filter.tab) ? filter.tab : 'all'
    const rows = useV2Query(
        (signal) =>
            bulkApi.rows(
                job.id,
                { eligibility: tab === 'all' ? undefined : tab, code: filter.code, q: filter.q, page: filter.page, page_size: ROWS_PAGE_SIZE },
                signal
            ),
        [job.id, job.status, tab, filter.code, filter.q, filter.page]
    )
    const data = rows.data
    const counts: Record<string, number | undefined> = {
        all: preview?.counts.total,
        eligible: preview?.counts.eligible,
        excluded: preview?.counts.excluded,
        error: preview?.counts.error,
    }
    const cols = 8
    return (
        <div data-testid="p2-inquiry">
            <div className="mb-3">
                <CountTabs
                    secondary
                    testId="p2-rows-tabs"
                    value={tab}
                    onChange={(v) => onFilter({ tab: v, page: 1 })}
                    tabs={ELIG_TABS.map((v) => ({ value: v, label: t(`p2.inquiryTab.${v}`), count: counts[v] }))}
                />
            </div>
            <TableFrame
                testId="p2-inquiry-table"
                footer={data && data.total > 0 ? <PagerFooter page={filter.page} pageSize={ROWS_PAGE_SIZE} total={data.total} onPage={(page) => onFilter({ page })} /> : undefined}
            >
                {rows.error && !data ? (
                    <ErrorState error={rows.error} onRetry={rows.reload} describe={describeBulkError} />
                ) : (
                    <Table>
                        <THead>
                            <Tr>
                                <Th>{t('p2.inq.sourceRow')}</Th>
                                <Th>{t('p2.inq.lookedUp')}</Th>
                                <Th>{t('p1.col.case')}</Th>
                                <Th>{t('p2.inq.matchedBy')}</Th>
                                <Th>{t('p2.inq.owner')}</Th>
                                <Th>{t('p1.col.portfolio')}</Th>
                                <Th className="text-end">{t('p2.inq.outstanding')}</Th>
                                <Th>{t('p2.inq.activity')}</Th>
                            </Tr>
                        </THead>
                        <TBody>
                            {rows.loading && !data ? (
                                <SkeletonRows cols={cols} />
                            ) : data && data.items.length > 0 ? (
                                data.items.map((r) => {
                                    const d = (r.details || {}) as Record<string, unknown>
                                    const matched = r.eligibility === 'eligible'
                                    return (
                                        <Tr key={r.id} data-testid="p2-inq-row" data-eligibility={r.eligibility} data-code={r.eligibility_code || ''}>
                                            <Td>
                                                <Ltr className="tabular-nums">{String(d.source_row_no ?? r.row_no)}</Ltr>
                                                {d.match_count !== undefined && Number(d.match_count) > 1 && (
                                                    <div className="text-[11.5px] tabular-nums text-gray-600">
                                                        {t('p2.inq.matchOf', { k: String(d.match_no), n: String(d.match_count) })}
                                                    </div>
                                                )}
                                            </Td>
                                            <Td>
                                                <Ltr className="block max-w-[180px] truncate text-[12.5px]">{r.match_value || '—'}</Ltr>
                                            </Td>
                                            <Td className="min-w-[180px]">
                                                {matched && r.case ? (
                                                    <CaseCell row={r} />
                                                ) : (
                                                    <div className="flex flex-col items-start gap-1">
                                                        <EligibilityBadge value={r.eligibility} />
                                                        <CodeText code={r.eligibility_code} message={r.eligibility_message} />
                                                    </div>
                                                )}
                                            </Td>
                                            <Td className="text-[12.5px]">
                                                {Array.isArray(d.matched_by) ? (d.matched_by as string[]).map((m) => t(`p2.inq.by.${m}`, { defaultValue: m })).join(' + ') : '—'}
                                            </Td>
                                            <Td className="text-[12.5px]">
                                                {matched ? (d.owner_name ? <bdi>{String(d.owner_name)}</bdi> : <span className="text-gray-500">{t('p1.unassigned')}</span>) : '—'}
                                                {d.owner_user_name ? <Ltr className="block text-gray-600">{String(d.owner_user_name)}</Ltr> : null}
                                            </Td>
                                            <Td className="max-w-[200px] text-[12.5px]">
                                                {matched ? (
                                                    <>
                                                        <bdi className="block truncate">{String(d.portfolio_name || '—')}</bdi>
                                                        <bdi className="block truncate text-gray-600">{String(d.contract_name || '')}</bdi>
                                                        <div className="mt-0.5 flex flex-wrap gap-1">
                                                            {flag(d.is_archived) && <PlainBadge size="row" tone="neutral">{t('p2.inq.archived')}</PlainBadge>}
                                                            {flag(d.is_locked) && <PlainBadge size="row" tone="warning">{t('p2.inq.locked')}</PlainBadge>}
                                                            {d.is_active === false && <PlainBadge size="row" tone="neutral">{t('p2.inq.inactive')}</PlainBadge>}
                                                        </div>
                                                    </>
                                                ) : (
                                                    '—'
                                                )}
                                            </Td>
                                            <Td className="text-end">{matched ? <MoneyText value={d.outstanding_balance as string} strong /> : '—'}</Td>
                                            <Td className="text-[12.5px]">
                                                {matched ? (
                                                    <>
                                                        <div>
                                                            <span className="text-gray-600">{t('p2.inq.lastNote')}: </span>
                                                            {d.last_note_at ? <RiyadhTime iso={String(d.last_note_at)} /> : '—'}
                                                        </div>
                                                        <div>
                                                            <span className="text-gray-600">{t('p2.inq.followUp')}: </span>
                                                            {d.follow_up_due_at ? <RiyadhTime iso={String(d.follow_up_due_at)} /> : '—'}
                                                            {flag(d.follow_up_overdue) && <span className="ms-1 font-extrabold text-danger">{t('p2.inq.overdue')}</span>}
                                                        </div>
                                                    </>
                                                ) : (
                                                    '—'
                                                )}
                                            </Td>
                                        </Tr>
                                    )
                                })
                            ) : (
                                <tr>
                                    <td colSpan={cols}>
                                        <EmptyState title={t('p2.rows.empty')} />
                                    </td>
                                </tr>
                            )}
                        </TBody>
                    </Table>
                )}
            </TableFrame>
            <Note className="mt-2">{t('p2.inq.note')}</Note>
        </div>
    )
}

export default RowsTable
