/**
 * Case detail → Calls tab (`calls` | `calls_make`): this case's calls
 * (`GET /v2/cases/<id>/calls`), "Log a call" (`calls`), the case's do-not-call block
 * (`GET /v2/cases/<id>/calling-blocks`; lifting it needs `calls_manage` and a reason) and
 * the selected call's detail. The legacy PBX call-records tab stays as it is.
 */
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { HiOutlinePlus } from 'react-icons/hi'
import Button from '@/components/ui/Button'
import Table from '@/components/ui/Table'
import { RiyadhTime } from '@/components/v2/Display'
import { ReasonDialog } from '@/components/v2/ActionDialog'
import { EmptyState, ErrorState, SkeletonRows } from '@/components/v2/States'
import { PagerFooter, TableFrame, rowClass } from '@/components/v2/ListParts'
import { SidePanel } from '@/views/phase1/money/listParts'
import { callsApi, describeP5Error } from '@/services/v2'
import type { Translate } from '@/services/v2'
import type { CasePanelProps } from '@/views/phase1/caseTypes'
import { usePermissions, useV2Query } from '@/utils/v2/hooks'
import { CALL_WRAPPED_EVENT } from '../live/callDock'
import { CallStateBadge, DurationText, ModeLabel, Notice, OutcomeBadge } from '../parts'
import CallDetailView from '../calls/CallDetail'
import LogCallDialog from '../wrapup/LogCallDialog'

const { THead, TBody, Tr, Th, Td } = Table
const PAGE_SIZE = 20
const describe = (e: unknown, t: Translate) => describeP5Error(e, t)

const CaseCallsPanel = ({ caseData, onChanged }: CasePanelProps) => {
    const { t } = useTranslation()
    const perms = usePermissions()
    const [page, setPage] = useState(1)
    const [selected, setSelected] = useState<string | null>(null)
    const [logging, setLogging] = useState(false)
    const [lifting, setLifting] = useState<string | null>(null)
    const list = useV2Query((signal) => callsApi.caseCalls(caseData.id, { page, page_size: PAGE_SIZE }, signal), [caseData.id, page])
    const blocks = useV2Query((signal) => callsApi.caseBlocks(caseData.id, signal), [caseData.id])
    const reloadList = list.reload
    const reloadBlocks = blocks.reload
    useEffect(() => {
        const on = (e: Event) => {
            const d = (e as CustomEvent<{ caseId: string | null }>).detail
            if (!d || d.caseId === caseData.id) {
                reloadList()
                reloadBlocks()
            }
        }
        window.addEventListener(CALL_WRAPPED_EVENT, on)
        return () => window.removeEventListener(CALL_WRAPPED_EVENT, on)
    }, [caseData.id, reloadList, reloadBlocks])
    const data = list.data
    const activeBlock = blocks.data?.items.find((b) => b.active && b.kind === 'do_not_call') || null
    const cols = 7
    return (
        <div data-testid="p5-case-calls">
            <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                <h5 className="mb-0">{t('p5.case.callsTitle')}</h5>
                {perms.calls && (
                    <Button size="sm" icon={<HiOutlinePlus />} onClick={() => setLogging(true)} data-testid="p5-case-log-call">
                        {t('p5.log.open')}
                    </Button>
                )}
            </div>
            {activeBlock && (
                <Notice tone="danger" className="mb-3" title={t('p5.block.activeTitle')} testId="p5-case-dnc">
                    <div dir="auto">{activeBlock.reason || '—'}</div>
                    <div className="mt-1 text-[12px] text-gray-600">
                        {activeBlock.created_by?.name || '—'} · <RiyadhTime iso={activeBlock.created_at} />
                    </div>
                    {perms.calls_manage && (
                        <Button size="xs" className="mt-2" onClick={() => setLifting(activeBlock.id)} data-testid="p5-case-dnc-lift">
                            {t('p5.block.lift')}
                        </Button>
                    )}
                </Notice>
            )}
            {blocks.data && blocks.data.blocked_contacts > 0 && (
                <div className="mb-3 text-[12.5px] text-gray-600" data-testid="p5-case-blocked-contacts">
                    {t('p5.block.blockedContacts', { count: blocks.data.blocked_contacts })}
                </div>
            )}
            <div className={selected ? 'grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1fr)_460px]' : ''}>
                <div className="min-w-0">
                    <TableFrame
                        testId="p5-case-calls-table"
                        footer={data && data.total > PAGE_SIZE ? <PagerFooter page={page} pageSize={PAGE_SIZE} total={data.total} onPage={setPage} /> : undefined}
                    >
                        {list.error && !data ? (
                            <ErrorState error={list.error} onRetry={list.reload} describe={describe} />
                        ) : (
                            <Table>
                                <THead>
                                    <Tr>
                                        <Th>{t('p5.col.time')}</Th>
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
                                        <SkeletonRows cols={cols} rows={3} />
                                    ) : data && data.items.length > 0 ? (
                                        data.items.map((c) => (
                                            <Tr
                                                key={c.id}
                                                className={rowClass(selected === c.id)}
                                                onClick={() => setSelected(c.id)}
                                                data-testid="p5-case-call-row"
                                                data-call={c.id}
                                                data-state={c.state}
                                            >
                                                <Td>
                                                    <RiyadhTime iso={c.started_at || c.created_at} />
                                                </Td>
                                                <Td>{c.collector?.name || '—'}</Td>
                                                <Td>
                                                    <ModeLabel mode={c.mode} />
                                                </Td>
                                                <Td>
                                                    <CallStateBadge state={c.state} size="row" />
                                                </Td>
                                                <Td>{c.wrapup ? <OutcomeBadge outcome={c.wrapup.outcome} size="row" /> : '—'}</Td>
                                                <Td className="text-end">
                                                    <DurationText seconds={c.duration_seconds} />
                                                </Td>
                                                <Td>
                                                    {c.wrapup_pending ? (
                                                        <span className="text-[12.5px] font-extrabold text-warning">{t('p5.calls.wrapupPending')}</span>
                                                    ) : c.wrapup?.needs_review ? (
                                                        <span className="text-[12.5px] text-warning">{t('p5.wrapup.awaitingReview')}</span>
                                                    ) : c.wrapup ? (
                                                        <span className="text-[12.5px] text-gray-600">{t('p5.calls.wrapupDone')}</span>
                                                    ) : (
                                                        '—'
                                                    )}
                                                </Td>
                                            </Tr>
                                        ))
                                    ) : (
                                        <tr>
                                            <td colSpan={cols}>
                                                <EmptyState title={t('p5.case.noCalls')} />
                                            </td>
                                        </tr>
                                    )}
                                </TBody>
                            </Table>
                        )}
                    </TableFrame>
                </div>
                {selected && (
                    <SidePanel title={t('p5.calls.detailTitle')} onClose={() => setSelected(null)} testId="p5-case-call-panel">
                        <CallDetailView key={selected} callId={selected} onChanged={list.reload} />
                    </SidePanel>
                )}
            </div>
            <LogCallDialog
                isOpen={logging}
                caseInfo={{
                    id: caseData.id,
                    name: caseData.name,
                    accountNumber: caseData.accountNumber,
                    identityNumber: caseData.identityNumber,
                    contractId: caseData.contract?.id || null,
                    isLocked: !!caseData.is_locked,
                    isArchived: !!caseData.is_archive,
                }}
                onClose={() => setLogging(false)}
                onDone={() => {
                    list.reload()
                    onChanged?.()
                }}
            />
            <ReasonDialog
                isOpen={!!lifting}
                title={t('p5.block.liftTitle')}
                description={t('p5.block.liftHint')}
                confirmLabel={t('p5.block.lift')}
                testId="p5-dnc-lift"
                describeError={describe}
                onClose={() => setLifting(null)}
                onReload={blocks.reload}
                onSubmit={async (reason, key) => {
                    if (!lifting) return
                    await callsApi.releaseBlock(lifting, reason, key)
                    blocks.reload()
                    onChanged?.()
                }}
            />
        </div>
    )
}

export default CaseCallsPanel
