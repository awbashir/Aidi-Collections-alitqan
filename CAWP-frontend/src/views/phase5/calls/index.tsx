/**
 * Calls (`/calls`, key `calls`; Daily operations).
 *
 * - "My calls" (`GET /v2/calls/mine`) and "All calls in my scope" (`GET /v2/calls`: my
 *   calls plus the calls on cases in my scope, so a supervisor sees the team's).
 * - State tabs with exact counts (one extra `page_size=1` read without the state filter, so
 *   every tab's count is exact), filters (mode, outcome, wrap-up pending/done, collector,
 *   case, Riyadh dates), paging, all in the URL hash.
 * - The selected call opens a detail panel: timeline, wrap-up (or "Wrap up"), transcript,
 *   PBX evidence, the CDR and recordings (recording keys).
 * - Entry point for the reviewed PBX CDR import (`pbx_cdr_import`, `calls_manage`) and the
 *   supervisor review of uncertain calls and review flags.
 */
import { useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { HiOutlineX } from 'react-icons/hi'
import { FileUp } from 'lucide-react'
import Table from '@/components/ui/Table'
import useHashState, { hashCodec } from '@/utils/hooks/useHashState'
import type { HashSchema } from '@/utils/hooks/useHashState'
import { CALL_MODES, OUTCOMES, callsApi, describeP5Error } from '@/services/v2'
import type { CallList, CallQuery, CallState, Translate } from '@/services/v2'
import { usePermissions, useV2Query } from '@/utils/v2/hooks'
import { formatCount } from '@/utils/v2/format'
import { Ltr, RiyadhTime } from '@/components/v2/Display'
import { EmptyState, ErrorState, Note, SkeletonRows } from '@/components/v2/States'
import { CountTabs, FilterSelect, PageHead, PagerFooter, TableFrame, rowClass, useScopeOptions } from '@/components/v2/ListParts'
import { DateFilter, SidePanel } from '@/views/phase1/money/listParts'
import { BulkLaunchButton } from '@/views/phase2/bulk/entry'
import { CaseCell, CallStateBadge, DurationText, ModeLabel, OutcomeBadge } from '../parts'
import CallDetailView from './CallDetail'

const { THead, TBody, Tr, Th, Td } = Table

type Scope = 'mine' | 'team'
type StateTab = 'all' | 'live' | 'ended' | 'failed' | 'uncertain' | 'cancelled'
const STATE_TABS: StateTab[] = ['all', 'live', 'ended', 'failed', 'uncertain', 'cancelled']
const LIVE_STATES: CallState[] = ['prepared', 'authorized', 'dispatched', 'agent_ringing', 'agent_answered', 'customer_dialing', 'customer_answered', 'bridged']
const TAB_STATES: Record<StateTab, CallState[] | undefined> = {
    all: undefined,
    live: LIVE_STATES,
    ended: ['ended'],
    failed: ['failed'],
    uncertain: ['uncertain'],
    cancelled: ['cancelled'],
}
const PAGE_SIZE = 25
const WRAPUPS = ['any', 'pending', 'done'] as const
type WrapupFilter = (typeof WRAPUPS)[number]

type HashState = {
    scope: Scope
    tab: StateTab
    mode: string | undefined
    outcome: string | undefined
    wrapup: WrapupFilter
    collector: string | number | undefined
    caseId: string | number | undefined
    from: string | undefined
    to: string | undefined
    page: number
    sel: string | number | undefined
}

const schema: HashSchema<HashState> = {
    scope: { key: 'scope', codec: hashCodec.oneOf(['mine', 'team'] as Scope[]), default: 'mine' },
    tab: { key: 'state', codec: hashCodec.oneOf(STATE_TABS), default: 'all' },
    mode: { key: 'mode', codec: hashCodec.text({ maxLength: 20 }), default: undefined },
    outcome: { key: 'outcome', codec: hashCodec.text({ maxLength: 30 }), default: undefined },
    wrapup: { key: 'wrapup', codec: hashCodec.oneOf(WRAPUPS), default: 'any' },
    collector: { key: 'collector', codec: hashCodec.id(), default: undefined },
    caseId: { key: 'case', codec: hashCodec.id(), default: undefined },
    from: { key: 'from', codec: hashCodec.date(), default: undefined },
    to: { key: 'to', codec: hashCodec.date(), default: undefined },
    page: { key: 'page', codec: hashCodec.int({ min: 1, max: 100000 }), default: 1 },
    sel: { key: 'call', codec: hashCodec.id(), default: undefined, history: 'replace' },
}

const str = (v: string | number | undefined) => (v === undefined ? undefined : String(v))
const describe = (e: unknown, t: Translate) => describeP5Error(e, t)

const Calls = () => {
    const { t } = useTranslation()
    const perms = usePermissions()
    const { state, setState } = useHashState(schema)
    const users = useScopeOptions()
    const scope: Scope = state.scope
    const base: CallQuery = {
        mode: state.mode && (CALL_MODES as readonly string[]).includes(state.mode) ? state.mode : undefined,
        outcome: state.outcome && (OUTCOMES as readonly string[]).includes(state.outcome) ? state.outcome : undefined,
        wrapup: state.wrapup === 'any' ? undefined : state.wrapup,
        collector_id: scope === 'team' ? str(state.collector) : undefined,
        case_id: str(state.caseId),
        from: state.from,
        to: state.to,
    }
    const load = (q: CallQuery, signal: AbortSignal) => (scope === 'mine' ? callsApi.mine(q, signal) : callsApi.log(q, signal))
    const deps = [scope, base.mode, base.outcome, base.wrapup, base.collector_id, base.case_id, base.from, base.to]
    const list = useV2Query(
        (signal) => load({ ...base, state: TAB_STATES[state.tab]?.join(','), page: state.page, page_size: PAGE_SIZE, sort: '-time' }, signal),
        [...deps, state.tab, state.page]
    )
    /* Exact counts per state tab: the same filters without the state filter. */
    const counts = useV2Query((signal) => load({ ...base, page: 1, page_size: 1 }, signal), deps)
    const data: CallList | undefined = list.data
    const tabs = useMemo(
        () =>
            STATE_TABS.map((tab) => {
                const byState = counts.data?.counts_by_state || {}
                const states = TAB_STATES[tab]
                const count = counts.data
                    ? states
                        ? states.reduce((n, s) => n + (byState[s] || 0), 0)
                        : Object.values(byState).reduce((n, v) => n + (v || 0), 0)
                    : undefined
                return { value: tab, label: t(`p5.calls.tab.${tab}`), count }
            }),
        [counts.data, t]
    )
    const modeOptions = CALL_MODES.map((m) => ({ value: m, label: String(t(`p5.mode.${m}`)) }))
    const outcomeOptions = OUTCOMES.map((o) => ({ value: o, label: String(t(`p5.outcome.${o}`)) }))
    const wrapupOptions = [
        { value: 'pending', label: String(t('p5.calls.wrapupPending')) },
        { value: 'done', label: String(t('p5.calls.wrapupDone')) },
    ]
    const selected = str(state.sel)
    const caseFilter = str(state.caseId)
    const caseName = caseFilter ? data?.items.find((r) => r.case.id === caseFilter)?.case : undefined
    const cols = 8

    return (
        <div data-testid="p5-calls">
            <PageHead
                title={t('p5.calls.title')}
                subtitle={data ? t('p5.calls.subtitle', { total: formatCount(data.total) }) : undefined}
                onRefresh={() => {
                    list.reload()
                    counts.reload()
                }}
                refreshing={list.loading && !!data}
                actions={
                    <>
                        {perms.call_review && (
                            <Link to="/call-review" className="text-[13px] font-extrabold text-azure-600 hover:underline" data-testid="p5-calls-review-link">
                                {t('p5.calls.reviewLink')}
                            </Link>
                        )}
                        <BulkLaunchButton
                            kind="pbx_cdr_import"
                            launch={{ from: { path: '/calls', labelKey: 'p5.calls.back' }, source: 'spreadsheet' }}
                            label={t('p5.calls.importCdr')}
                            testId="p5-calls-import"
                            icon={<FileUp size={15} />}
                        />
                    </>
                }
            />

            <div className="mb-3">
                <CountTabs<Scope>
                    tabs={[
                        { value: 'mine', label: t('p5.calls.scope.mine') },
                        { value: 'team', label: t('p5.calls.scope.team') },
                    ]}
                    value={scope}
                    onChange={(v) => setState({ scope: v, page: 1, collector: undefined, sel: undefined })}
                    testId="p5-calls-scope"
                />
            </div>

            <div className="mb-4 flex flex-wrap items-end gap-3" data-testid="p5-calls-filters">
                <FilterSelect label={t('p5.calls.filter.mode')} options={modeOptions} value={base.mode} testId="p5-calls-mode" width={180} onChange={(v) => setState({ mode: v, page: 1 })} />
                <FilterSelect label={t('p5.calls.filter.outcome')} options={outcomeOptions} value={base.outcome} testId="p5-calls-outcome" width={200} onChange={(v) => setState({ outcome: v, page: 1 })} />
                <FilterSelect
                    label={t('p5.calls.filter.wrapup')}
                    options={wrapupOptions}
                    value={state.wrapup === 'any' ? undefined : state.wrapup}
                    testId="p5-calls-wrapup"
                    width={190}
                    onChange={(v) => setState({ wrapup: (v as WrapupFilter) || 'any', page: 1 })}
                />
                {scope === 'team' && (
                    <FilterSelect
                        label={t('p5.calls.filter.collector')}
                        options={users.owners}
                        loading={users.loading}
                        value={str(state.collector)}
                        testId="p5-calls-collector"
                        onChange={(v) => setState({ collector: v, page: 1 })}
                    />
                )}
                <DateFilter label={t('p5.calls.filter.from')} value={state.from} max={state.to} testId="p5-calls-from" onChange={(v) => setState({ from: v, page: 1 })} />
                <DateFilter label={t('p5.calls.filter.to')} value={state.to} min={state.from} testId="p5-calls-to" onChange={(v) => setState({ to: v, page: 1 })} />
                {caseFilter && (
                    <div data-testid="p5-calls-case-chip">
                        <div className="text-eyebrow mb-1">{t('p5.col.case')}</div>
                        <span className="inline-flex h-9 items-center gap-2 rounded-md border border-gray-300 px-3 text-[13px] dark:border-night-border">
                            {caseName && !caseName.missing ? (
                                <>
                                    <span dir="auto">{caseName.name}</span>
                                    <Ltr className="text-gray-600">{caseName.account_number}</Ltr>
                                </>
                            ) : (
                                <Ltr>{caseFilter.slice(0, 8)}…</Ltr>
                            )}
                            <button
                                type="button"
                                aria-label={String(t('p1.filter.clear'))}
                                className="rounded p-0.5 text-gray-600 hover:bg-gray-100 dark:hover:bg-night-raised"
                                onClick={() => setState({ caseId: undefined, page: 1 })}
                            >
                                <HiOutlineX />
                            </button>
                        </span>
                    </div>
                )}
            </div>

            <div className="mb-3">
                <CountTabs<StateTab> secondary tabs={tabs} value={state.tab} onChange={(tab) => setState({ tab, page: 1 })} testId="p5-calls-state-tabs" />
            </div>

            <div className={selected ? 'grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1fr)_480px]' : ''}>
                <div className="min-w-0">
                    <TableFrame
                        testId="p5-calls-table"
                        footer={data && data.total > 0 ? <PagerFooter page={state.page} pageSize={PAGE_SIZE} total={data.total} onPage={(page) => setState({ page })} /> : undefined}
                    >
                        {list.error && !data ? (
                            <ErrorState error={list.error} onRetry={list.reload} describe={describe} />
                        ) : (
                            <Table>
                                <THead>
                                    <Tr>
                                        <Th>{t('p5.col.time')}</Th>
                                        <Th>{t('p5.col.case')}</Th>
                                        <Th>{t('p5.col.collector')}</Th>
                                        <Th>{t('p5.col.mode')}</Th>
                                        <Th>{t('p5.col.state')}</Th>
                                        <Th>{t('p5.col.outcome')}</Th>
                                        <Th className="text-end">{t('p5.col.duration')}</Th>
                                        <Th>{t('p5.col.wrapup')}</Th>
                                    </Tr>
                                </THead>
                                <TBody>
                                    {list.loading && !data ? (
                                        <SkeletonRows cols={cols} />
                                    ) : data && data.items.length > 0 ? (
                                        data.items.map((c) => (
                                            <Tr
                                                key={c.id}
                                                className={rowClass(selected === c.id)}
                                                aria-current={selected === c.id ? 'true' : undefined}
                                                onClick={() => setState({ sel: c.id }, { replace: true })}
                                                data-testid="p5-call-row"
                                                data-call={c.id}
                                                data-state={c.state}
                                                data-outcome={c.wrapup?.outcome || ''}
                                            >
                                                <Td>
                                                    <RiyadhTime iso={c.started_at || c.created_at} />
                                                </Td>
                                                <Td className="min-w-[180px]">
                                                    <CaseCell kase={c.case} stop />
                                                </Td>
                                                <Td>{c.collector?.name || '—'}</Td>
                                                <Td>
                                                    <ModeLabel mode={c.mode} />
                                                </Td>
                                                <Td>
                                                    <CallStateBadge state={c.state} size="row" />
                                                </Td>
                                                <Td>{c.wrapup ? <OutcomeBadge outcome={c.wrapup.outcome} size="row" /> : <span className="text-gray-500">—</span>}</Td>
                                                <Td className="text-end">
                                                    <DurationText seconds={c.duration_seconds} />
                                                </Td>
                                                <Td>
                                                    {c.wrapup_pending ? (
                                                        <span className="text-[12.5px] font-extrabold text-warning" data-testid="p5-call-pending">
                                                            {t('p5.calls.wrapupPending')}
                                                        </span>
                                                    ) : c.wrapup?.needs_review ? (
                                                        <span className="text-[12.5px] text-warning">{t('p5.wrapup.awaitingReview')}</span>
                                                    ) : c.wrapup ? (
                                                        <span className="text-[12.5px] text-gray-600">{t('p5.calls.wrapupDone')}</span>
                                                    ) : (
                                                        <span className="text-gray-500">—</span>
                                                    )}
                                                </Td>
                                            </Tr>
                                        ))
                                    ) : (
                                        <tr>
                                            <td colSpan={cols}>
                                                <EmptyState title={t('p5.calls.emptyTitle')} hint={t('p5.calls.emptyHint')} />
                                            </td>
                                        </tr>
                                    )}
                                </TBody>
                            </Table>
                        )}
                    </TableFrame>
                    {list.error && data ? <ErrorState error={list.error} onRetry={list.reload} describe={describe} /> : null}
                    <Note className="mt-3">{t('p5.calls.footNote')}</Note>
                </div>
                {selected && (
                    <SidePanel title={t('p5.calls.detailTitle')} onClose={() => setState({ sel: undefined }, { replace: true })} testId="p5-call-panel">
                        <CallDetailView
                            key={selected}
                            callId={selected}
                            onChanged={() => {
                                list.reload()
                                counts.reload()
                            }}
                        />
                    </SidePanel>
                )}
            </div>
        </div>
    )
}

export default Calls
