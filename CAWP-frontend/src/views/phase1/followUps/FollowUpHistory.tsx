/**
 * A case's follow-ups and their append-only event history
 * (GET /v2/cases/<id>/follow-ups). A late completion or a late reschedule stays
 * visible: the API keeps `due_at` and marks `was_late` / `completed_late`.
 */
import { useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import Dialog from '@/components/ui/Dialog'
import Table from '@/components/ui/Table'
import { worklistApi } from '@/services/v2'
import type { CaseFollowUps, FollowUp, FollowUpEvent } from '@/services/v2'
import { useV2Query } from '@/utils/v2/hooks'
import { formatRiyadhDateTime } from '@/utils/v2/format'
import { RiyadhTime } from '@/components/v2/Display'
import { BucketBadge, FollowUpStatusBadge, PlainBadge } from '@/components/v2/Badges'
import { EmptyState, ErrorState, SkeletonLines } from '@/components/v2/States'
import { SourceCell } from './parts'

const { THead, TBody, Tr, Th, Td } = Table

/** "#1" = the oldest follow-up of the case, so events can point at a row. */
function useSequence(followUps: FollowUp[]) {
    return useMemo(() => {
        const map = new Map<string, number>()
        const n = followUps.length
        followUps.forEach((fu, i) => map.set(fu.id, n - i)) // API order: newest first
        return map
    }, [followUps])
}

export const MissedMarker = () => {
    const { t } = useTranslation()
    return (
        <PlainBadge tone="danger" size="row">
            <span data-testid="p1-fu-missed">{t('p1.followUps.missedRecorded')}</span>
        </PlainBadge>
    )
}

const Seq = ({ n }: { n: number | undefined }) =>
    n ? (
        <span dir="ltr" className="tabular-nums text-[12px] font-extrabold text-gray-600">
            #{n}
        </span>
    ) : null

const FollowUpsTable = ({ items, seq }: { items: FollowUp[]; seq: Map<string, number> }) => {
    const { t } = useTranslation()
    return (
        <div className="overflow-x-auto rounded-lg border border-gray-300 dark:border-night-border">
            <Table compact data-testid="p1-fu-history-list">
                <THead>
                    <Tr>
                        <Th>#</Th>
                        <Th>{t('p1.col.status')}</Th>
                        <Th>{t('p1.col.due')}</Th>
                        <Th>{t('p1.col.nextAction')}</Th>
                        <Th>{t('p1.col.source')}</Th>
                        <Th>{t('p1.followUps.col.outcome')}</Th>
                        <Th className="text-end">{t('p1.followUps.col.reschedules')}</Th>
                        <Th className="text-end">{t('p1.followUps.col.revision')}</Th>
                    </Tr>
                </THead>
                <TBody>
                    {items.map((fu) => (
                        <Tr key={fu.id} data-testid="p1-fu-history-row" data-status={fu.status}>
                            <Td>
                                <Seq n={seq.get(fu.id)} />
                            </Td>
                            <Td>
                                <div className="flex flex-col items-start gap-1">
                                    <FollowUpStatusBadge status={fu.status} size="row" />
                                    {fu.bucket && <BucketBadge bucket={fu.bucket} daysLate={fu.days_late} size="row" />}
                                </div>
                            </Td>
                            <Td className="whitespace-nowrap">
                                <RiyadhTime iso={fu.due_at} />
                                {fu.original_due_at && fu.original_due_at !== fu.due_at && (
                                    <div className="text-[12px] text-gray-600">
                                        {t('p1.followUps.originally')} <RiyadhTime iso={fu.original_due_at} />
                                    </div>
                                )}
                            </Td>
                            <Td className="min-w-[160px] max-w-[240px]">
                                <span dir="auto" className="break-words">
                                    {fu.next_action || <span className="text-gray-500">—</span>}
                                </span>
                            </Td>
                            <Td>
                                <SourceCell fu={fu} snippet={false} />
                            </Td>
                            <Td className="min-w-[180px]">
                                <Outcome fu={fu} />
                            </Td>
                            <Td className="text-end tabular-nums">{fu.reschedule_count}</Td>
                            <Td className="text-end tabular-nums">{fu.revision}</Td>
                        </Tr>
                    ))}
                </TBody>
            </Table>
        </div>
    )
}

const Outcome = ({ fu }: { fu: FollowUp }) => {
    const { t } = useTranslation()
    if (fu.status === 'open') return <span className="text-gray-500">—</span>
    return (
        <div className="flex flex-col items-start gap-1 text-[12.5px]">
            {fu.closed_at && <RiyadhTime iso={fu.closed_at} />}
            {fu.status === 'completed' && fu.completed_late && <MissedMarker />}
            {fu.status === 'completed' && fu.no_further_action_reason && (
                <span className="break-words">
                    <span className="text-gray-600">{t('p1.followUps.nfa')}: </span>
                    <bdi>{fu.no_further_action_reason}</bdi>
                </span>
            )}
            {fu.status === 'completed' && fu.next_follow_up_id && (
                <span className="text-gray-600">{t('p1.followUps.nextScheduled')}</span>
            )}
            {fu.status === 'superseded' && <span className="text-gray-600">{t('p1.followUps.supersededByNote')}</span>}
        </div>
    )
}

const actorLabel = (ev: FollowUpEvent, t: (k: string) => string) => {
    if (ev.actor_name) return ev.actor_name
    if (ev.source === 'migration') return t('p1.followUps.actor.migration')
    if (ev.source === 'trigger') return t('p1.followUps.actor.trigger')
    return t('p1.followUps.actor.system')
}

const EventItem = ({ ev, seq }: { ev: FollowUpEvent; seq: Map<string, number> }) => {
    const { t } = useTranslation()
    const details = (ev.details || {}) as { next_action_before?: string | null; next_action_after?: string | null }
    const naChanged =
        'next_action_after' in details && (details.next_action_before || null) !== (details.next_action_after || null)
    return (
        <li
            className="relative border-s-2 border-gray-300 pb-4 ps-4 last:pb-0 dark:border-night-border"
            data-testid="p1-fu-event"
            data-event={ev.event}
        >
            <span
                aria-hidden
                className="absolute -start-[5px] top-1.5 h-2 w-2 rounded-full bg-navy-600 dark:bg-gray-100"
            />
            <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                <span className="text-[13.5px] font-extrabold text-navy-600 dark:text-gray-100">
                    {t(`p1.followUps.event.${ev.event}`)}
                </span>
                <Seq n={seq.get(ev.follow_up_id)} />
                {ev.was_late && <MissedMarker />}
                <span className="text-[12.5px] text-gray-600">
                    <RiyadhTime iso={ev.occurred_at} /> · {actorLabel(ev, t)}
                </span>
            </div>
            {(ev.due_at_before || ev.due_at_after) && (
                <div className="mt-1 text-[12.5px] text-gray-700 dark:text-night-body" data-testid="p1-fu-event-due">
                    <span className="text-gray-600">{t('p1.col.due')}: </span>
                    {ev.due_at_before && <RiyadhTime iso={ev.due_at_before} />}
                    {ev.due_at_before && ev.due_at_after && (
                        <span className="mx-1.5 text-gray-500" aria-hidden>
                            <span className="rtl:hidden">→</span>
                            <span className="ltr:hidden">←</span>
                        </span>
                    )}
                    {ev.due_at_after && <RiyadhTime iso={ev.due_at_after} className="font-extrabold" />}
                </div>
            )}
            {ev.reason && (
                <div className="mt-1 text-[12.5px]">
                    <span className="text-gray-600">{t('p1.col.reason')}: </span>
                    <bdi>{ev.reason}</bdi>
                </div>
            )}
            {naChanged && (
                <div className="mt-1 text-[12.5px]">
                    <span className="text-gray-600">{t('p1.col.nextAction')}: </span>
                    <bdi>{details.next_action_before || '—'}</bdi>
                    <span className="mx-1.5 text-gray-500" aria-hidden>
                        <span className="rtl:hidden">→</span>
                        <span className="ltr:hidden">←</span>
                    </span>
                    <bdi>{details.next_action_after || '—'}</bdi>
                </div>
            )}
        </li>
    )
}

/** The follow-ups of a case and every event, newest first. */
export const FollowUpHistory = ({ data }: { data: CaseFollowUps }) => {
    const { t } = useTranslation()
    const seq = useSequence(data.follow_ups)
    if (data.follow_ups.length === 0 && data.events.length === 0) {
        return <EmptyState title={t('p1.followUps.history.emptyTitle')} hint={t('p1.followUps.history.emptyHint')} />
    }
    return (
        <div className="flex flex-col gap-5" data-testid="p1-fu-history">
            <section>
                <div className="text-eyebrow mb-2">
                    {t('p1.followUps.history.followUps')} ({data.follow_ups.length})
                </div>
                <FollowUpsTable items={data.follow_ups} seq={seq} />
            </section>
            <section>
                <div className="text-eyebrow mb-3">{t('p1.followUps.history.events')}</div>
                <ol className="ms-1.5" data-testid="p1-fu-events">
                    {data.events.map((ev) => (
                        <EventItem key={ev.id} ev={ev} seq={seq} />
                    ))}
                </ol>
                <p className="mt-3 text-[12px] text-gray-600">
                    {t('p1.list.asOf', { at: formatRiyadhDateTime(data.as_of.at) })}
                </p>
            </section>
        </div>
    )
}

export type FollowUpHistoryDialogProps = {
    isOpen: boolean
    caseId: string | null
    caseName?: string | null
    accountNumber?: string | null
    onClose: () => void
}

/** The case's follow-up history in a dialog (read-only). */
export const FollowUpHistoryDialog = ({
    isOpen,
    caseId,
    caseName,
    accountNumber,
    onClose,
}: FollowUpHistoryDialogProps) => {
    const { t } = useTranslation()
    const query = useV2Query(isOpen && caseId ? (signal) => worklistApi.caseFollowUps(caseId, signal) : null, [
        isOpen,
        caseId,
    ])
    return (
        <Dialog isOpen={isOpen} width={960} onClose={onClose} onRequestClose={onClose}>
            <div data-testid="p1-fu-history-dialog" className="flex max-h-[80vh] flex-col">
                <h5 className="mb-4 pe-8 text-[17px] font-extrabold text-navy-600 dark:text-gray-100">
                    {t('p1.followUps.history.title')}
                    {caseName && <span className="ms-2 text-[14px] font-normal text-gray-700">{caseName}</span>}
                    {accountNumber && (
                        <span className="ms-2 text-[12.5px] font-normal tabular-nums text-gray-600">
                            <bdi dir="ltr">{accountNumber}</bdi>
                        </span>
                    )}
                </h5>
                <div className="min-h-0 flex-1 overflow-y-auto pe-1">
                    {query.error && !query.data ? (
                        <ErrorState error={query.error} onRetry={query.reload} />
                    ) : query.loading && !query.data ? (
                        <SkeletonLines rows={6} />
                    ) : query.data ? (
                        <FollowUpHistory data={query.data} />
                    ) : null}
                </div>
            </div>
        </Dialog>
    )
}
