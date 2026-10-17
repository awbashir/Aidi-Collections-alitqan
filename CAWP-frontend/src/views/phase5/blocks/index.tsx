/**
 * Blocked clients (`/calling-blocks`, key `calling_blocks` = calls_supervise | calls_manage; Intelligence & control).
 *
 * Customer decision 2026-10-17: only a supervisor blocks a client from calls (do-not-call);
 * collectors mark a wrong or disconnected number with "wrong number", which blocks only that
 * number. This screen lists the do-not-call blocks on cases in the supervisor's scope
 * (`GET /v2/calling-blocks`): client, account, contract, collector, reason, who/when and the
 * source (a call wrap-up or a supervisor). Filters: state, contract, collector, source and a
 * literal search. Lifting a block needs a written reason (`POST /v2/calling-blocks/<id>/release`).
 * A client is blocked from the case's Calls tab.
 */
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import Button from '@/components/ui/Button'
import Input from '@/components/ui/Input'
import Table from '@/components/ui/Table'
import { ReasonDialog } from '@/components/v2/ActionDialog'
import { Ltr, RiyadhTime } from '@/components/v2/Display'
import { EmptyState, ErrorState, Note, SkeletonRows } from '@/components/v2/States'
import { FilterSelect, PageHead, PagerFooter, TableFrame, useScopeOptions } from '@/components/v2/ListParts'
import { Segmented } from '@/views/phase1/followUps/parts'
import useHashState, { hashCodec } from '@/utils/hooks/useHashState'
import type { HashSchema } from '@/utils/hooks/useHashState'
import { describeP5Error } from '@/services/v2'
import type { Translate } from '@/services/v2'
import { supervisionApi } from '@/services/v2/callingSupervisionApi'
import type { BlockedClient } from '@/services/v2/callingSupervisionApi'
import { useV2Query } from '@/utils/v2/hooks'
import { formatCount } from '@/utils/v2/format'
import { CaseCell, Notice } from '../parts'

const { THead, TBody, Tr, Th, Td } = Table
type Active = 'true' | 'false' | 'all'
type Source = 'wrapup' | 'manual'
const SOURCES: Source[] = ['wrapup', 'manual']
type HashState = {
    active: Active
    contract: string | undefined
    owner: string | undefined
    source: string | undefined
    q: string | undefined
    page: number
}
const schema: HashSchema<HashState> = {
    active: { key: 'active', codec: hashCodec.oneOf(['true', 'false', 'all'] as Active[]), default: 'true' },
    contract: { key: 'contract', codec: hashCodec.text({ maxLength: 40 }), default: undefined },
    owner: { key: 'owner', codec: hashCodec.text({ maxLength: 40 }), default: undefined },
    source: { key: 'source', codec: hashCodec.text({ maxLength: 10 }), default: undefined },
    q: { key: 'q', codec: hashCodec.text({ maxLength: 100 }), default: undefined },
    page: { key: 'page', codec: hashCodec.int({ min: 1, max: 100000 }), default: 1 },
}
const PAGE_SIZE = 25
const COLS = 7
const describe = (e: unknown, t: Translate) => describeP5Error(e, t)

/** The search box keeps its own text and updates the hash after a short pause. */
const SearchBox = ({ value, onChange }: { value: string | undefined; onChange: (v: string | undefined) => void }) => {
    const { t } = useTranslation()
    const [text, setText] = useState(value || '')
    useEffect(() => setText(value || ''), [value])
    useEffect(() => {
        const next = text.trim() || undefined
        if (next === (value || undefined)) return
        const id = window.setTimeout(() => onChange(next), 350)
        return () => window.clearTimeout(id)
    }, [text, value, onChange])
    return (
        <div className="w-[240px] min-w-[180px]">
            <div className="text-eyebrow mb-1">{t('p8.calling.blocks.search')}</div>
            <Input
                size="sm"
                value={text}
                placeholder={String(t('p8.calling.blocks.searchHint'))}
                onChange={(e) => setText(e.target.value)}
                data-testid="p8-blocks-search"
            />
        </div>
    )
}

const BlockedClients = () => {
    const { t } = useTranslation()
    const { state, setState } = useHashState(schema)
    const opts = useScopeOptions()
    const source = SOURCES.includes(state.source as Source) ? (state.source as Source) : undefined
    const q = useV2Query(
        (signal) =>
            supervisionApi.blocks(
                {
                    active: state.active,
                    contract_id: state.contract,
                    owner_id: state.owner,
                    source,
                    q: state.q,
                    page: state.page,
                    page_size: PAGE_SIZE,
                },
                signal
            ),
        [state.active, state.contract, state.owner, source, state.q, state.page]
    )
    const [lifting, setLifting] = useState<BlockedClient | null>(null)
    const data = q.data
    const filtered = !!(state.contract || state.owner || source || state.q)
    return (
        <div data-testid="p8-blocks">
            <PageHead
                title={t('p8.calling.blocks.title')}
                subtitle={data ? t('p8.calling.blocks.subtitle', { count: data.total, total: formatCount(data.total) }) : undefined}
                onRefresh={q.reload}
                refreshing={q.loading}
            />
            <Notice tone="info" className="mb-3">
                {t('p8.calling.blocks.intro')}
            </Notice>
            <div className="mb-3">
                <Segmented<Active>
                    value={state.active}
                    onChange={(v) => setState({ active: v, page: 1 })}
                    testId="p8-blocks-state"
                    options={[
                        { value: 'true', label: t('p8.calling.blocks.state.active') },
                        { value: 'false', label: t('p8.calling.blocks.state.lifted') },
                        { value: 'all', label: t('p8.calling.blocks.state.all') },
                    ]}
                />
            </div>
            <div className="mb-4 flex flex-wrap items-end gap-3" data-testid="p8-blocks-filters">
                <FilterSelect
                    label={t('p1.filter.contract')}
                    options={opts.contracts}
                    loading={opts.loading}
                    value={state.contract}
                    testId="p8-blocks-contract"
                    onChange={(v) => setState({ contract: v, page: 1 })}
                />
                <FilterSelect
                    label={t('p8.calling.blocks.owner')}
                    options={opts.owners}
                    loading={opts.loading}
                    value={state.owner}
                    testId="p8-blocks-owner"
                    onChange={(v) => setState({ owner: v, page: 1 })}
                />
                <FilterSelect
                    label={t('p8.calling.blocks.source')}
                    options={[
                        { value: 'wrapup', label: t('p8.calling.blocks.sourceLabel.wrapup') },
                        { value: 'manual', label: t('p8.calling.blocks.sourceLabel.manual') },
                    ]}
                    value={source}
                    width={180}
                    testId="p8-blocks-source"
                    onChange={(v) => setState({ source: v, page: 1 })}
                />
                <SearchBox value={state.q} onChange={(v) => setState({ q: v, page: 1 })} />
                {filtered && (
                    <Button size="sm" variant="plain" onClick={() => setState({ contract: undefined, owner: undefined, source: undefined, q: undefined, page: 1 })}>
                        {t('p1.filter.clear')}
                    </Button>
                )}
            </div>
            <TableFrame
                testId="p8-blocks-table"
                footer={data && data.total > 0 ? <PagerFooter page={state.page} pageSize={PAGE_SIZE} total={data.total} onPage={(page) => setState({ page })} /> : undefined}
            >
                {q.error && !data ? (
                    <ErrorState error={q.error} onRetry={q.reload} describe={describe} />
                ) : (
                    <Table>
                        <THead>
                            <Tr>
                                <Th>{t('p8.calling.blocks.col.client')}</Th>
                                <Th>{t('p8.calling.blocks.col.contract')}</Th>
                                <Th>{t('p8.calling.blocks.owner')}</Th>
                                <Th>{t('p8.calling.blocks.col.reason')}</Th>
                                <Th>{t('p8.calling.blocks.col.blockedBy')}</Th>
                                <Th>{t('p8.calling.blocks.col.state')}</Th>
                                <Th />
                            </Tr>
                        </THead>
                        <TBody>
                            {q.loading && !data ? (
                                <SkeletonRows cols={COLS} rows={4} />
                            ) : data && data.items.length ? (
                                data.items.map((b) => (
                                    <Tr key={b.id} data-testid="p8-blocks-row" data-block={b.id} data-active={b.active ? '1' : '0'} data-source={b.source}>
                                        <Td>
                                            <CaseCell kase={{ id: b.case.id, name: b.case.name, account_number: b.case.accountNumber }} />
                                            {b.case.identityNumber && <Ltr className="block text-[12px] text-gray-500">{b.case.identityNumber}</Ltr>}
                                        </Td>
                                        <Td className="text-[12.5px]">
                                            <div dir="auto">{b.contract?.name || '—'}</div>
                                            {b.portfolio_name && (
                                                <div className="text-gray-500" dir="auto">
                                                    {b.portfolio_name}
                                                </div>
                                            )}
                                        </Td>
                                        <Td className="text-[12.5px]">{b.owner?.name || t('p8.calling.blocks.unassigned')}</Td>
                                        <Td className="max-w-[280px] text-[12.5px]">
                                            <div dir="auto" className="break-words">
                                                {b.reason || '—'}
                                            </div>
                                            <div className="text-gray-500">{t(`p8.calling.blocks.sourceLabel.${b.source}`)}</div>
                                        </Td>
                                        <Td className="text-[12.5px]">
                                            {b.created_by?.name || '—'}
                                            <div className="text-gray-500">
                                                <RiyadhTime iso={b.created_at} />
                                            </div>
                                        </Td>
                                        <Td className="text-[12.5px]">
                                            {b.active ? (
                                                <span className="font-extrabold text-danger">{t('p8.calling.blocks.state.activeOne')}</span>
                                            ) : (
                                                <>
                                                    <span>{t('p8.calling.blocks.state.liftedOne')}</span>
                                                    <div className="text-gray-500">
                                                        {b.released_by?.name || '—'} · <RiyadhTime iso={b.released_at} />
                                                    </div>
                                                    {b.release_reason && (
                                                        <div dir="auto" className="text-gray-600">
                                                            {b.release_reason}
                                                        </div>
                                                    )}
                                                </>
                                            )}
                                        </Td>
                                        <Td>
                                            {b.active && (
                                                <Button size="xs" onClick={() => setLifting(b)} data-testid="p8-blocks-lift">
                                                    {t('p8.calling.blocks.lift')}
                                                </Button>
                                            )}
                                        </Td>
                                    </Tr>
                                ))
                            ) : (
                                <tr>
                                    <td colSpan={COLS}>
                                        <EmptyState title={t(filtered ? 'p8.calling.blocks.emptyFiltered' : 'p8.calling.blocks.empty')} />
                                    </td>
                                </tr>
                            )}
                        </TBody>
                    </Table>
                )}
            </TableFrame>
            <Note className="mt-3">{t('p8.calling.blocks.footNote')}</Note>
            <ReasonDialog
                isOpen={!!lifting}
                title={t('p8.calling.blocks.liftTitle')}
                description={
                    lifting ? (
                        <div className="flex flex-col gap-2">
                            <CaseCell kase={{ id: lifting.case.id, name: lifting.case.name, account_number: lifting.case.accountNumber }} />
                            <div>{t('p8.calling.blocks.liftHint')}</div>
                        </div>
                    ) : undefined
                }
                confirmLabel={t('p8.calling.blocks.lift')}
                testId="p8-blocks-lift-dialog"
                describeError={describe}
                onReload={q.reload}
                onClose={() => setLifting(null)}
                onSubmit={async (reason, key) => {
                    if (!lifting) return
                    await supervisionApi.lift(lifting.id, reason, key)
                    q.reload()
                }}
            />
        </div>
    )
}

export default BlockedClients
