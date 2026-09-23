/**
 * Case corrections review (`/corrections`, route key `case_corrections_review`).
 *
 * GET /v2/corrections with status tabs (pending by default), a kind filter and
 * "my requests". Tab counts are exact: one `page_size=1` request per status
 * with the same filters. The selected correction opens in the side panel,
 * loaded live from GET /v2/corrections/<id> (current values, stale flags).
 * Tab, filters, page and the selected correction live in the URL hash.
 */
import { useEffect, useMemo, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { HiX } from 'react-icons/hi'
import Table from '@/components/ui/Table'
import Checkbox from '@/components/ui/Checkbox'
import useHashState, { hashCodec } from '@/utils/hooks/useHashState'
import type { HashSchema } from '@/utils/hooks/useHashState'
import { CORRECTION_KINDS, CORRECTION_STATUSES, historyApi } from '@/services/v2'
import type { CorrectionKind, CorrectionStatus } from '@/services/v2'
import { useV2Query } from '@/utils/v2/hooks'
import { Ltr, RiyadhTime } from '@/components/v2/Display'
import { CorrectionBadge } from '@/components/v2/Badges'
import { EmptyState, ErrorState, Note, SkeletonRows } from '@/components/v2/States'
import { CountTabs, FilterSelect, PageHead, PagerFooter, TableFrame, rowClass } from '@/components/v2/ListParts'
import CorrectionDetail from '../history/CorrectionDetail'
import { ChangeSummary, useDelinquencyReasons } from '../history/parts'

const { THead, TBody, Tr, Th, Td } = Table

type Tab = CorrectionStatus | 'all'
const TABS: Tab[] = [...CORRECTION_STATUSES, 'all']
const PAGE_SIZE = 25

type HashState = {
    tab: Tab
    kind: CorrectionKind | ''
    mine: '0' | '1'
    page: number
    sel: string | number | undefined
}

const schema: HashSchema<HashState> = {
    tab: { key: 'status', codec: hashCodec.oneOf(TABS), default: 'pending' },
    kind: { key: 'kind', codec: hashCodec.oneOf<CorrectionKind | ''>(['', ...CORRECTION_KINDS]), default: '' },
    mine: { key: 'mine', codec: hashCodec.oneOf<'0' | '1'>(['0', '1']), default: '0' },
    page: { key: 'page', codec: hashCodec.int({ min: 1, max: 100000 }), default: 1 },
    sel: { key: 'sel', codec: hashCodec.id(), default: undefined, history: 'replace' },
}

const CorrectionsReview = () => {
    const { t } = useTranslation()
    const { state, setState } = useHashState(schema)
    const reasons = useDelinquencyReasons().options
    const kind = state.kind || undefined
    const requestedBy = state.mine === '1' ? 'me' : undefined
    const status = state.tab === 'all' ? undefined : state.tab

    const list = useV2Query(
        (signal) =>
            historyApi.corrections(
                { status, kind, requested_by: requestedBy, page: state.page, page_size: PAGE_SIZE },
                signal
            ),
        [status, kind, requestedBy, state.page]
    )

    const counts = useV2Query(
        async (signal) => {
            const res = await Promise.all(
                TABS.map((tab) =>
                    historyApi.corrections(
                        {
                            status: tab === 'all' ? undefined : tab,
                            kind,
                            requested_by: requestedBy,
                            page: 1,
                            page_size: 1,
                        },
                        signal
                    )
                )
            )
            const out = {} as Record<Tab, number>
            TABS.forEach((tab, i) => {
                out[tab] = res[i].total
            })
            return out
        },
        [kind, requestedBy]
    )

    const tabs = useMemo(
        () =>
            TABS.map((tab) => ({
                value: tab,
                label: t(tab === 'all' ? 'p1.corr.all' : `p1.correctionStatus.${tab}`),
                count: counts.data ? counts.data[tab] : undefined,
            })),
        [counts.data, t]
    )

    const kindOptions = CORRECTION_KINDS.map((k) => ({ value: k, label: t(`p1.corrKind.${k}`) }))
    const data = list.data
    const sel = state.sel ? String(state.sel) : undefined
    const reloadAll = () => {
        list.reload()
        counts.reload()
    }
    const cols = 6
    const panelRef = useRef<HTMLElement>(null)
    // Below 2xl the panel sits under the table: bring it into view when a row is picked.
    useEffect(() => {
        if (sel) panelRef.current?.scrollIntoView?.({ block: 'nearest', behavior: 'smooth' })
    }, [sel])

    return (
        <div data-testid="p1-corrections">
            <PageHead
                title={t('p1.corr.title')}
                subtitle={t('p1.corr.subtitle')}
                onRefresh={reloadAll}
                refreshing={list.loading && !!data}
            />

            <div className="mb-4 flex flex-wrap items-end gap-3" data-testid="p1-filters">
                <FilterSelect
                    label={t('p1.filter.kind')}
                    options={kindOptions}
                    value={kind}
                    testId="p1-filter-kind"
                    onChange={(v) => setState({ kind: (v as CorrectionKind) || '', page: 1 })}
                />
                <div className="flex h-9 items-center" data-testid="p1-filter-mine">
                    <Checkbox
                        checked={state.mine === '1'}
                        onChange={(checked) => setState({ mine: checked ? '1' : '0', page: 1 })}
                    >
                        {t('p1.filter.mine')}
                    </Checkbox>
                </div>
            </div>

            <div className="mb-3">
                <CountTabs
                    tabs={tabs}
                    value={state.tab}
                    onChange={(tab) => setState({ tab, page: 1 })}
                    testId="p1-corr-tabs"
                />
            </div>

            <div className={sel ? 'grid grid-cols-1 gap-4 2xl:grid-cols-[minmax(0,1fr)_520px]' : ''}>
                <div className="min-w-0">
                    <TableFrame
                        testId="p1-corr-table"
                        footer={
                            data && data.total > 0 ? (
                                <PagerFooter
                                    page={state.page}
                                    pageSize={PAGE_SIZE}
                                    total={data.total}
                                    onPage={(page) => setState({ page })}
                                />
                            ) : undefined
                        }
                    >
                        {list.error && !data ? (
                            <ErrorState error={list.error} onRetry={list.reload} />
                        ) : (
                            <Table>
                                <THead>
                                    <Tr>
                                        <Th>{t('p1.col.case')}</Th>
                                        <Th>{t('p1.col.kind')}</Th>
                                        <Th>{t('p1.col.change')}</Th>
                                        <Th>{t('p1.col.status')}</Th>
                                        <Th>{t('p1.col.requestedBy')}</Th>
                                        <Th>{t('p1.col.reviewedBy')}</Th>
                                    </Tr>
                                </THead>
                                <TBody>
                                    {list.loading && !data ? (
                                        <SkeletonRows cols={cols} />
                                    ) : data && data.items.length > 0 ? (
                                        data.items.map((c) => (
                                            <Tr
                                                key={c.id}
                                                className={rowClass(sel === c.id)}
                                                aria-current={sel === c.id ? 'true' : undefined}
                                                onClick={() => setState({ sel: c.id }, { replace: true })}
                                                data-testid="p1-corr-row"
                                                data-id={c.id}
                                                data-status={c.status}
                                            >
                                                <Td>
                                                    <div className="font-extrabold text-navy-600 dark:text-gray-100">
                                                        {c.case?.name || '—'}
                                                    </div>
                                                    <Ltr className="text-[12.5px] text-gray-600">
                                                        {c.case?.account_number}
                                                    </Ltr>
                                                </Td>
                                                <Td className="whitespace-nowrap">{t(`p1.corrKind.${c.kind}`)}</Td>
                                                <Td className="min-w-[220px] max-w-[360px]">
                                                    <ChangeSummary correction={c} reasons={reasons} max={2} />
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
                                                            <RiyadhTime
                                                                iso={c.reviewed_at}
                                                                className="text-[12px] text-gray-600"
                                                            />
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
                                                <EmptyState
                                                    title={t('p1.corr.emptyTitle')}
                                                    hint={t('p1.corr.emptyHint')}
                                                />
                                            </td>
                                        </tr>
                                    )}
                                </TBody>
                            </Table>
                        )}
                    </TableFrame>
                    {list.error && data ? <ErrorState error={list.error} onRetry={list.reload} /> : null}
                    {counts.error ? (
                        <Note className="mt-2">{t('p1.corr.countsUnavailable')}</Note>
                    ) : null}
                </div>

                {sel && (
                    <aside
                        ref={panelRef}
                        className="min-w-0 self-start rounded-lg border border-gray-300 bg-white p-5 dark:border-night-border dark:bg-night-card"
                        data-testid="p1-corr-panel"
                        aria-label={String(t('p1.corr.detailTitle'))}
                    >
                        <div className="mb-3 flex items-center justify-between gap-2">
                            <span className="text-eyebrow">{t('p1.corr.detailTitle')}</span>
                            <button
                                type="button"
                                className="rounded p-1 text-gray-500 hover:bg-gray-100 dark:hover:bg-night-raised"
                                onClick={() => setState({ sel: undefined }, { replace: true })}
                                aria-label={String(t('p1.action.close'))}
                                data-testid="p1-corr-panel-close"
                            >
                                <HiX />
                            </button>
                        </div>
                        <CorrectionDetail key={sel} id={sel} onChanged={reloadAll} />
                    </aside>
                )}
            </div>
        </div>
    )
}

export default CorrectionsReview
