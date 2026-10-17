/**
 * Call review for supervisors (`/call-review`, key `call_review` = calls_supervise | calls_manage;
 * Intelligence & control).
 *
 * - Uncertain calls (`GET /v2/calling/uncertain`): the PBX state of these calls is unknown
 *   (lost AMI session, a restart, missing or ambiguous evidence). The reservation stays and
 *   the wrap-up waits. Resolve (`POST /v2/calls/<id>/resolve`) needs a written reason, picks
 *   ended or failed, and may release the reservation. A resolution never invents bridge
 *   evidence: without it the conversation outcomes stay unavailable.
 * - Review flags (`GET /v2/calling/review`): wrap-ups saved on a locked/archived case (their
 *   deferred case changes), late evidence after a wrap-up, evidence conflicts, and
 *   reconciliations the service could not settle. Resolving one needs a reason; apply any
 *   deferred change through the normal screens first.
 * - Awaiting wrap-up (2026-10-17, `GET /v2/calling/pending-wrapups`): ended calls that still
 *   hold their reservation without a wrap-up, including ORPHANED calls whose case was
 *   deleted (they block the collector's next call). Release (`POST /v2/calls/<id>/release`)
 *   needs a written reason; the call keeps its state and no wrap-up is invented. Every list
 *   here also shows orphaned calls ("case deleted").
 */
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import Checkbox from '@/components/ui/Checkbox'
import Table from '@/components/ui/Table'
import Button from '@/components/ui/Button'
import { ReasonDialog } from '@/components/v2/ActionDialog'
import { DialogField } from '@/components/v2/ActionDialog'
import { RiyadhTime } from '@/components/v2/Display'
import { EmptyState, ErrorState, Note, SkeletonRows } from '@/components/v2/States'
import { CountTabs, PageHead, PagerFooter, TableFrame } from '@/components/v2/ListParts'
import { Segmented } from '@/views/phase1/followUps/parts'
import useHashState, { hashCodec } from '@/utils/hooks/useHashState'
import type { HashSchema } from '@/utils/hooks/useHashState'
import { callReviewApi, describeP5Error } from '@/services/v2'
import type { CallDetail, ReviewFlag, Translate } from '@/services/v2'
import { supervisionApi } from '@/services/v2/callingSupervisionApi'
import { useV2Query } from '@/utils/v2/hooks'
import { CallStateBadge, CaseCell, Notice, OutcomeBadge } from '../parts'
import { reasonLabel } from '../labels'

const { THead, TBody, Tr, Th, Td } = Table
type Tab = 'uncertain' | 'flags' | 'pending'
type Orphaned = 'any' | 'true'
type FlagState = 'open' | 'resolved' | 'all'
const KINDS: ReviewFlag['kind'][] = ['wrapup_needs_review', 'late_evidence', 'evidence_conflict', 'reconciliation_unresolved']
type HashState = { tab: Tab; fstate: FlagState; kind: string | undefined; page: number; orphaned: Orphaned }
const schema: HashSchema<HashState> = {
    tab: { key: 'tab', codec: hashCodec.oneOf(['uncertain', 'flags', 'pending'] as Tab[]), default: 'uncertain' },
    orphaned: { key: 'orphaned', codec: hashCodec.oneOf(['any', 'true'] as Orphaned[]), default: 'any' },
    fstate: { key: 'flags', codec: hashCodec.oneOf(['open', 'resolved', 'all'] as FlagState[]), default: 'open' },
    kind: { key: 'kind', codec: hashCodec.text({ maxLength: 40 }), default: undefined },
    page: { key: 'page', codec: hashCodec.int({ min: 1, max: 100000 }), default: 1 },
}
const PAGE_SIZE = 25
const describe = (e: unknown, t: Translate) => describeP5Error(e, t)

const ResolveDialog = ({ call, onClose, onDone }: { call: CallDetail | null; onClose: () => void; onDone: () => void }) => {
    const { t } = useTranslation()
    const [resolution, setResolution] = useState<'ended' | 'failed'>('ended')
    const [release, setRelease] = useState(true)
    return (
        <ReasonDialog
            isOpen={!!call}
            title={t('p5.review.resolveTitle')}
            description={
                call ? (
                    <div className="flex flex-col gap-2">
                        <CaseCell kase={call.case} />
                        <div>{t('p5.review.resolveHint', { reason: reasonLabel(t, call.reason_code) })}</div>
                        {!call.timeline.bridged_at && <Notice tone="info">{t('p5.review.noBridge')}</Notice>}
                    </div>
                ) : undefined
            }
            confirmLabel={t('p5.review.resolve')}
            testId="p5-review-resolve"
            describeError={describe}
            onClose={() => {
                setResolution('ended')
                setRelease(true)
                onClose()
            }}
            extra={
                <>
                    <DialogField label={t('p5.review.resolution')} hint={t(`p5.review.resolutionHint.${resolution}`)}>
                        <Segmented<'ended' | 'failed'>
                            value={resolution}
                            onChange={setResolution}
                            testId="p5-review-resolution"
                            options={[
                                { value: 'ended', label: t('p5.state.ended') },
                                { value: 'failed', label: t('p5.state.failed') },
                            ]}
                        />
                    </DialogField>
                    <div className="mb-4">
                        <Checkbox checked={release} onChange={(c: boolean) => setRelease(c)} data-testid="p5-review-release">
                            {t('p5.review.release')}
                        </Checkbox>
                        <div className="mt-1 text-[12px] text-gray-600">{t('p5.review.releaseHint')}</div>
                    </div>
                </>
            }
            onSubmit={async (reason, key) => {
                if (!call) return
                await callReviewApi.resolve(call.id, { expected_revision: call.revision, resolution, reason, release_reservation: release }, key)
                onDone()
            }}
        />
    )
}

const UncertainTab = ({ page, onPage }: { page: number; onPage: (p: number) => void }) => {
    const { t } = useTranslation()
    const q = useV2Query((signal) => callReviewApi.uncertain({ page, page_size: PAGE_SIZE }, signal), [page])
    const [resolving, setResolving] = useState<CallDetail | null>(null)
    const data = q.data
    return (
        <div data-testid="p5-review-uncertain">
            <Notice tone="warning" className="mb-3">
                {t('p5.review.uncertainIntro')}
            </Notice>
            <TableFrame footer={data && data.total > 0 ? <PagerFooter page={page} pageSize={PAGE_SIZE} total={data.total} onPage={onPage} /> : undefined}>
                {q.error && !data ? (
                    <ErrorState error={q.error} onRetry={q.reload} describe={describe} />
                ) : (
                    <Table>
                        <THead>
                            <Tr>
                                <Th>{t('p5.col.case')}</Th>
                                <Th>{t('p5.col.collector')}</Th>
                                <Th>{t('p5.review.col.why')}</Th>
                                <Th>{t('p5.review.col.lastCheck')}</Th>
                                <Th>{t('p5.review.col.reservation')}</Th>
                                <Th />
                            </Tr>
                        </THead>
                        <TBody>
                            {q.loading && !data ? (
                                <SkeletonRows cols={6} rows={3} />
                            ) : data && data.items.length ? (
                                data.items.map((c) => {
                                    const last = c.reconciliations?.[0]
                                    return (
                                        <Tr key={c.id} data-testid="p5-review-uncertain-row" data-call={c.id}>
                                            <Td>
                                                <CaseCell kase={c.case} />
                                            </Td>
                                            <Td>{c.collector?.name || '—'}</Td>
                                            <Td>
                                                <CallStateBadge state={c.state} size="row" />
                                                <div className="text-[12px] text-gray-700 dark:text-night-body" data-reason={c.reason_code || ''}>
                                                    {reasonLabel(t, c.reason_code)}
                                                </div>
                                                {c.timeline.bridged_at && <div className="text-[12px] text-success">{t('p5.review.wasBridged')}</div>}
                                            </Td>
                                            <Td className="text-[12.5px]">
                                                {last ? (
                                                    <>
                                                        {t(`p5.review.decision.${last.decision}`, { defaultValue: last.decision })} ·{' '}
                                                        {t(`p5.review.code.${last.code}`, { defaultValue: last.code.replace(/_/g, ' ') })}
                                                        <div className="text-gray-500">
                                                            <RiyadhTime iso={last.at} />
                                                        </div>
                                                    </>
                                                ) : (
                                                    <span className="text-gray-500">{t('p5.review.noCheck')}</span>
                                                )}
                                            </Td>
                                            <Td>{c.reservation?.active ? t('p5.review.held') : t('p5.review.released')}</Td>
                                            <Td>
                                                <Button size="xs" variant="solid" onClick={() => setResolving(c)} data-testid="p5-review-resolve-open">
                                                    {t('p5.review.resolve')}
                                                </Button>
                                            </Td>
                                        </Tr>
                                    )
                                })
                            ) : (
                                <tr>
                                    <td colSpan={6}>
                                        <EmptyState title={t('p5.review.noUncertain')} />
                                    </td>
                                </tr>
                            )}
                        </TBody>
                    </Table>
                )}
            </TableFrame>
            <ResolveDialog call={resolving} onClose={() => setResolving(null)} onDone={q.reload} />
        </div>
    )
}

const FlagDetail = ({ flag }: { flag: ReviewFlag & { call: CallDetail } }) => {
    const { t } = useTranslation()
    const d = (flag.detail || {}) as { outcome?: string; deferred?: string[] }
    return (
        <div className="text-[12.5px]">
            {d.outcome && (
                <span className="me-2">
                    <OutcomeBadge outcome={d.outcome as never} size="row" />
                </span>
            )}
            {flag.reason && (
                <span data-reason={flag.reason}>
                    {t(`p5.flag.reason.${flag.reason}`, {
                        defaultValue: t(`p8.calling.flagReason.${flag.reason}`, { defaultValue: flag.reason.replace(/_/g, ' ') }),
                    })}
                </span>
            )}
            {d.deferred && d.deferred.length > 0 && (
                <div className="text-gray-600">
                    {t('p5.wrapup.deferred')}: {d.deferred.map((k) => t(`p5.wrapup.deferredKey.${k}`, { defaultValue: k })).join(String(t('p5.listSep')))}
                </div>
            )}
            {flag.call.wrapup?.note_text && (
                <div className="mt-1 max-w-[360px] truncate text-gray-700" dir="auto" title={flag.call.wrapup.note_text}>
                    {flag.call.wrapup.note_text}
                </div>
            )}
        </div>
    )
}

const FlagsTab = ({ state, onState }: { state: HashState; onState: (p: Partial<HashState>) => void }) => {
    const { t } = useTranslation()
    const kind = state.kind && (KINDS as string[]).includes(state.kind) ? state.kind : undefined
    const q = useV2Query((signal) => callReviewApi.flags({ state: state.fstate, kind, page: state.page, page_size: PAGE_SIZE }, signal), [state.fstate, kind, state.page])
    const all = useV2Query((signal) => callReviewApi.flags({ state: state.fstate, page: 1, page_size: 1 }, signal), [state.fstate])
    const [resolving, setResolving] = useState<(ReviewFlag & { call: CallDetail }) | null>(null)
    const data = q.data
    const counts = all.data?.counts_by_kind || {}
    return (
        <div data-testid="p5-review-flags">
            <div className="mb-3 flex flex-wrap items-center gap-3">
                <Segmented<FlagState>
                    value={state.fstate}
                    onChange={(v) => onState({ fstate: v, page: 1 })}
                    testId="p5-review-flag-state"
                    options={[
                        { value: 'open', label: t('p5.review.open') },
                        { value: 'resolved', label: t('p5.review.resolved') },
                        { value: 'all', label: t('p5.review.all') },
                    ]}
                />
            </div>
            <div className="mb-3">
                <CountTabs<string>
                    secondary
                    tabs={[
                        { value: 'any', label: t('p5.review.allKinds'), count: all.data ? all.data.total : undefined },
                        ...KINDS.map((k) => ({ value: k, label: t(`p5.flag.kind.${k}`), count: all.data ? counts[k] || 0 : undefined })),
                    ]}
                    value={kind || 'any'}
                    onChange={(v) => onState({ kind: v === 'any' ? undefined : v, page: 1 })}
                    testId="p5-review-kinds"
                />
            </div>
            <TableFrame footer={data && data.total > 0 ? <PagerFooter page={state.page} pageSize={PAGE_SIZE} total={data.total} onPage={(p) => onState({ page: p })} /> : undefined}>
                {q.error && !data ? (
                    <ErrorState error={q.error} onRetry={q.reload} describe={describe} />
                ) : (
                    <Table>
                        <THead>
                            <Tr>
                                <Th>{t('p5.review.col.flag')}</Th>
                                <Th>{t('p5.col.case')}</Th>
                                <Th>{t('p5.review.col.detail')}</Th>
                                <Th>{t('p5.review.col.raised')}</Th>
                                <Th>{t('p5.review.col.resolution')}</Th>
                                <Th />
                            </Tr>
                        </THead>
                        <TBody>
                            {q.loading && !data ? (
                                <SkeletonRows cols={6} rows={3} />
                            ) : data && data.items.length ? (
                                data.items.map((f) => (
                                    <Tr key={f.id} data-testid="p5-review-flag-row" data-flag={f.id} data-kind={f.kind} data-open={f.open ? '1' : '0'} data-call={f.call.id}>
                                        <Td>
                                            <div className="font-extrabold text-navy-600 dark:text-gray-100">{t(`p5.flag.kind.${f.kind}`)}</div>
                                            {f.blocks_calling && f.open && <div className="text-[12px] font-extrabold text-danger">{t('p5.flag.blocksCalling')}</div>}
                                        </Td>
                                        <Td>
                                            <CaseCell kase={f.call.case} />
                                        </Td>
                                        <Td>
                                            <FlagDetail flag={f} />
                                        </Td>
                                        <Td className="text-[12.5px]">
                                            <RiyadhTime iso={f.created_at} />
                                        </Td>
                                        <Td className="text-[12.5px]">
                                            {f.open ? (
                                                <span className="text-warning">{t('p5.review.open')}</span>
                                            ) : (
                                                <>
                                                    {f.resolved_by?.name || '—'} · <RiyadhTime iso={f.resolved_at} />
                                                    {f.resolution_reason && <div dir="auto">{f.resolution_reason}</div>}
                                                </>
                                            )}
                                        </Td>
                                        <Td>
                                            {f.open && (
                                                <Button size="xs" variant="solid" onClick={() => setResolving(f)} data-testid="p5-review-flag-resolve">
                                                    {t('p5.review.resolve')}
                                                </Button>
                                            )}
                                        </Td>
                                    </Tr>
                                ))
                            ) : (
                                <tr>
                                    <td colSpan={6}>
                                        <EmptyState title={t('p5.review.noFlags')} />
                                    </td>
                                </tr>
                            )}
                        </TBody>
                    </Table>
                )}
            </TableFrame>
            <ReasonDialog
                isOpen={!!resolving}
                title={t('p5.review.flagResolveTitle')}
                description={
                    resolving ? (
                        <div className="flex flex-col gap-2">
                            <div className="font-extrabold">{t(`p5.flag.kind.${resolving.kind}`)}</div>
                            <div>{t(`p5.flag.hint.${resolving.kind}`)}</div>
                        </div>
                    ) : undefined
                }
                confirmLabel={t('p5.review.resolve')}
                testId="p5-review-flag-dialog"
                describeError={describe}
                onReload={q.reload}
                onClose={() => setResolving(null)}
                onSubmit={async (reason, key) => {
                    if (!resolving) return
                    await callReviewApi.resolveFlag(resolving.id, reason, key)
                    q.reload()
                    all.reload()
                }}
            />
        </div>
    )
}

/* ------------------------------------------------------------------ awaiting wrap-up */

const PendingTab = ({ state, onState }: { state: HashState; onState: (p: Partial<HashState>) => void }) => {
    const { t } = useTranslation()
    const orphaned = state.orphaned === 'true' ? 'true' : undefined
    const q = useV2Query(
        (signal) => supervisionApi.pendingWrapups({ orphaned, page: state.page, page_size: PAGE_SIZE }, signal),
        [orphaned, state.page]
    )
    const [releasing, setReleasing] = useState<CallDetail | null>(null)
    const data = q.data
    return (
        <div data-testid="p8-review-pending">
            <Notice tone="info" className="mb-3">
                {t('p8.calling.pending.intro')}
            </Notice>
            <div className="mb-3">
                <Segmented<Orphaned>
                    value={state.orphaned}
                    onChange={(v) => onState({ orphaned: v, page: 1 })}
                    testId="p8-review-pending-filter"
                    options={[
                        { value: 'any', label: t('p8.calling.pending.all') },
                        {
                            value: 'true',
                            label: data ? t('p8.calling.pending.orphanedCount', { count: data.orphaned }) : t('p8.calling.pending.orphaned'),
                        },
                    ]}
                />
            </div>
            <TableFrame footer={data && data.total > 0 ? <PagerFooter page={state.page} pageSize={PAGE_SIZE} total={data.total} onPage={(p) => onState({ page: p })} /> : undefined}>
                {q.error && !data ? (
                    <ErrorState error={q.error} onRetry={q.reload} describe={describe} />
                ) : (
                    <Table>
                        <THead>
                            <Tr>
                                <Th>{t('p5.col.case')}</Th>
                                <Th>{t('p5.col.collector')}</Th>
                                <Th>{t('p5.col.state')}</Th>
                                <Th>{t('p8.calling.pending.col.ended')}</Th>
                                <Th>{t('p5.review.col.reservation')}</Th>
                                <Th />
                            </Tr>
                        </THead>
                        <TBody>
                            {q.loading && !data ? (
                                <SkeletonRows cols={6} rows={3} />
                            ) : data && data.items.length ? (
                                data.items.map((c) => (
                                    <Tr key={c.id} data-testid="p8-review-pending-row" data-call={c.id} data-orphaned={c.case.missing ? '1' : '0'}>
                                        <Td>
                                            <CaseCell kase={c.case} />
                                            {c.case.missing && <div className="text-[12px] font-extrabold text-danger">{t('p8.calling.pending.orphanedHint')}</div>}
                                        </Td>
                                        <Td>{c.collector?.name || '—'}</Td>
                                        <Td>
                                            <CallStateBadge state={c.state} size="row" />
                                            {c.timeline.bridged_at && <div className="text-[12px] text-success">{t('p5.review.wasBridged')}</div>}
                                        </Td>
                                        <Td className="text-[12.5px]">
                                            <RiyadhTime iso={c.timeline.ended_at || c.created_at} />
                                        </Td>
                                        <Td>{c.reservation?.active ? t('p5.review.held') : t('p5.review.released')}</Td>
                                        <Td>
                                            {c.reservation?.active && (
                                                <Button size="xs" variant="solid" onClick={() => setReleasing(c)} data-testid="p8-review-release-open">
                                                    {t('p8.calling.pending.release')}
                                                </Button>
                                            )}
                                        </Td>
                                    </Tr>
                                ))
                            ) : (
                                <tr>
                                    <td colSpan={6}>
                                        <EmptyState title={t('p8.calling.pending.empty')} />
                                    </td>
                                </tr>
                            )}
                        </TBody>
                    </Table>
                )}
            </TableFrame>
            <ReasonDialog
                isOpen={!!releasing}
                title={t('p8.calling.pending.releaseTitle')}
                description={
                    releasing ? (
                        <div className="flex flex-col gap-2">
                            <CaseCell kase={releasing.case} />
                            <div>{t('p8.calling.pending.releaseHint', { collector: releasing.collector?.name || '—' })}</div>
                        </div>
                    ) : undefined
                }
                confirmLabel={t('p8.calling.pending.release')}
                testId="p8-review-release"
                describeError={describe}
                onReload={q.reload}
                onClose={() => setReleasing(null)}
                onSubmit={async (reason, key) => {
                    if (!releasing) return
                    await supervisionApi.release(releasing.id, { expected_revision: releasing.revision, reason }, key)
                    q.reload()
                }}
            />
        </div>
    )
}

const CallReview = () => {
    const { t } = useTranslation()
    const { state, setState } = useHashState(schema)
    return (
        <div data-testid="p5-review">
            <PageHead title={t('p5.review.title')} subtitle={t('p5.review.subtitle')} />
            <div className="mb-4">
                <CountTabs<Tab>
                    tabs={[
                        { value: 'uncertain', label: t('p5.review.tab.uncertain') },
                        { value: 'flags', label: t('p5.review.tab.flags') },
                        { value: 'pending', label: t('p8.calling.pending.tab') },
                    ]}
                    value={state.tab}
                    onChange={(tab) => setState({ tab, page: 1 })}
                    testId="p5-review-tabs"
                />
            </div>
            {state.tab === 'uncertain' ? (
                <UncertainTab page={state.page} onPage={(page) => setState({ page })} />
            ) : state.tab === 'pending' ? (
                <PendingTab state={state} onState={(p) => setState(p)} />
            ) : (
                <FlagsTab state={state} onState={(p) => setState(p)} />
            )}
            <Note className="mt-3">{t('p5.review.footNote')}</Note>
        </div>
    )
}

export default CallReview
