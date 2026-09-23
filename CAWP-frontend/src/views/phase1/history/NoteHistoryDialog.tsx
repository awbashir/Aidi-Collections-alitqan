/**
 * One note's history (GET /v2/notes/<id>/history): the original text and each
 * correction with its reason, author and time, plus the audit events
 * (v2 corrections / retirements and legacy edits or deletes recorded by the
 * database trigger, whose actor is unknown).
 */
import { useTranslation } from 'react-i18next'
import Dialog from '@/components/ui/Dialog'
import Table from '@/components/ui/Table'
import { historyApi } from '@/services/v2'
import type { NoteHistory } from '@/services/v2'
import { useV2Query } from '@/utils/v2/hooks'
import { RiyadhTime } from '@/components/v2/Display'
import { PlainBadge } from '@/components/v2/Badges'
import { EmptyState, ErrorState, SkeletonLines } from '@/components/v2/States'
import { ActorName, Banner } from './parts'

const { THead, TBody, Tr, Th, Td } = Table

type HistoryEvent = NoteHistory['events'][number]

const NOISE = new Set(['update_at', 'updated_at', 'revision'])

const show = (v: unknown, yesNo?: [string, string]) =>
    v === null || v === undefined
        ? '—'
        : typeof v === 'boolean' && yesNo
        ? yesNo[v ? 0 : 1]
        : typeof v === 'object'
        ? JSON.stringify(v)
        : String(v)

const EventChanges = ({ event }: { event: HistoryEvent }) => {
    const { t } = useTranslation()
    if (event.deleted_row) {
        const text = event.deleted_row.comment
        return (
            <div className="text-[12.5px]">
                <span className="text-gray-600">{t('p1.notes.deletedText')} </span>
                <span dir="auto" className="whitespace-pre-wrap">
                    {show(text)}
                </span>
            </div>
        )
    }
    const yesNo: [string, string] = [String(t('p1.corr.true')), String(t('p1.corr.false'))]
    const entries = Object.entries(event.changes || {}).filter(([f]) => !NOISE.has(f))
    if (entries.length === 0) return <span className="text-gray-500">—</span>
    return (
        <div className="flex flex-col gap-1 text-[12.5px]">
            {entries.map(([f, c]) => (
                <div key={f}>
                    <span className="font-extrabold">{t(`p1.notes.field.${f}`, { defaultValue: f })}</span>
                    <span className="text-gray-400">: </span>
                    <span dir="auto" className="whitespace-pre-wrap text-gray-600 line-through decoration-gray-400">
                        {show(c.before, yesNo)}
                    </span>
                    <span aria-hidden className="mx-1 inline-block text-gray-400 rtl:rotate-180">
                        →
                    </span>
                    <span dir="auto" className="whitespace-pre-wrap text-navy-600 dark:text-gray-100">
                        {show(c.after, yesNo)}
                    </span>
                </div>
            ))}
        </div>
    )
}

const NoteHistoryDialog = ({
    noteId,
    onClose,
}: {
    /** Open for this note; closed when null. */
    noteId: string | null
    onClose: () => void
}) => {
    const { t } = useTranslation()
    const query = useV2Query(noteId ? (signal) => historyApi.noteHistory(noteId, signal) : null, [noteId])
    const data = noteId ? query.data : undefined

    return (
        <Dialog isOpen={!!noteId} width={880} onClose={onClose} onRequestClose={onClose}>
            <div data-testid="p1-note-history" className="flex max-h-[80vh] flex-col">
                <h5 className="mb-4 pe-8 text-[17px] font-extrabold text-navy-600 dark:text-gray-100">
                    {t('p1.notes.historyTitle')}
                </h5>
                <div className="min-h-0 flex-1 overflow-y-auto pe-1">
                    {query.error && !data ? (
                        <ErrorState error={query.error} onRetry={query.reload} />
                    ) : !data || query.loading ? (
                        <SkeletonLines rows={5} />
                    ) : (
                        <div className="flex flex-col gap-5">
                            {data.hard_deleted && (
                                <Banner tone="danger" title={t('p1.notes.hardDeletedTitle')}>
                                    {t('p1.notes.hardDeletedHint')}
                                </Banner>
                            )}
                            {data.note?.retired && (
                                <Banner tone="warning">{t('p1.notes.retiredHint')}</Banner>
                            )}

                            <section>
                                <div className="text-eyebrow mb-2">{t('p1.notes.revisions')}</div>
                                <ol className="flex flex-col gap-2" data-testid="p1-note-revisions">
                                    {data.revisions.map((r) => (
                                        <li
                                            key={r.revision_no}
                                            className="rounded-md border border-gray-300 p-3 dark:border-night-border"
                                            data-testid="p1-note-revision"
                                            data-kind={r.kind}
                                        >
                                            <div className="mb-1.5 flex flex-wrap items-center gap-2 text-[12.5px] text-gray-600">
                                                <PlainBadge tone={r.kind === 'original' ? 'neutral' : 'info'} size="row">
                                                    {r.kind === 'original'
                                                        ? t('p1.notes.original')
                                                        : t('p1.notes.correctionNo', { n: r.revision_no })}
                                                </PlainBadge>
                                                {r.corrected_by && <span>{r.corrected_by.name}</span>}
                                                {r.corrected_at && <RiyadhTime iso={r.corrected_at} />}
                                            </div>
                                            {r.stored || r.text ? (
                                                <p dir="auto" className="whitespace-pre-wrap text-[14px] text-gray-700 dark:text-night-body">
                                                    {r.text ?? data.note?.text ?? '—'}
                                                </p>
                                            ) : (
                                                <p dir="auto" className="whitespace-pre-wrap text-[14px] text-gray-700 dark:text-night-body">
                                                    {data.note?.original_text ?? data.note?.text ?? '—'}
                                                </p>
                                            )}
                                            {r.reason && (
                                                <div className="mt-1.5 text-[12.5px] text-gray-600">
                                                    {t('p1.col.reason')}: <span dir="auto">{r.reason}</span>
                                                </div>
                                            )}
                                        </li>
                                    ))}
                                </ol>
                            </section>

                            <section>
                                <div className="text-eyebrow mb-2">{t('p1.notes.events')}</div>
                                {data.events.length === 0 ? (
                                    <EmptyState title={t('p1.notes.noEvents')} className="py-4" />
                                ) : (
                                    <div className="overflow-x-auto rounded-md border border-gray-300 dark:border-night-border">
                                        <Table compact data-testid="p1-note-events">
                                            <THead>
                                                <Tr>
                                                    <Th>{t('p1.col.when')}</Th>
                                                    <Th>{t('p1.col.event')}</Th>
                                                    <Th>{t('p1.col.actor')}</Th>
                                                    <Th>{t('p1.col.reason')}</Th>
                                                    <Th>{t('p1.notes.changes')}</Th>
                                                </Tr>
                                            </THead>
                                            <TBody>
                                                {data.events.map((e) => (
                                                    <Tr key={e.id} data-testid="p1-note-event" data-action={e.action}>
                                                        <Td className="whitespace-nowrap">
                                                            <RiyadhTime iso={e.occurred_at} />
                                                        </Td>
                                                        <Td>
                                                            <div className="font-extrabold text-navy-600 dark:text-gray-100">
                                                                {t(`p1.notes.action.${e.action.replace('.', '_')}`, {
                                                                    defaultValue: e.action,
                                                                })}
                                                            </div>
                                                            <div className="text-[11.5px] text-gray-500">
                                                                {t(`p1.hist.source.${e.source}`, { defaultValue: e.source })}
                                                            </div>
                                                        </Td>
                                                        <Td>
                                                            <ActorName actor={e.actor} source={e.source} />
                                                        </Td>
                                                        <Td className="max-w-[180px]">
                                                            <span dir="auto" className="break-words">
                                                                {e.reason || '—'}
                                                            </span>
                                                        </Td>
                                                        <Td className="min-w-[240px]">
                                                            <EventChanges event={e} />
                                                        </Td>
                                                    </Tr>
                                                ))}
                                            </TBody>
                                        </Table>
                                    </div>
                                )}
                            </section>
                        </div>
                    )}
                </div>
            </div>
        </Dialog>
    )
}

export default NoteHistoryDialog
