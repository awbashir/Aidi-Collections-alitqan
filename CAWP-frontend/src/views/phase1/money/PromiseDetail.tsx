/**
 * One promise with its allocations and event history, plus the actions the
 * signed-in user holds a key for (cancel: promises_cancel; allocate and
 * deallocate: promises_allocate). Used by the /promises side panel and the
 * case promises panel. States and amounts come from the API as they are.
 */
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import Button from '@/components/ui/Button'
import { CivilDate, Field, Ltr, MoneyText, RiyadhTime } from '@/components/v2/Display'
import { PromiseBadge } from '@/components/v2/Badges'
import { Note } from '@/components/v2/States'
import type { Allocation, PromiseRow } from '@/services/v2'
import { usePermissions } from '@/utils/v2/hooks'
import { caseLink } from '@/utils/v2/links'
import { AllocationList } from './PaymentTimeline'
import { AllocateDialog, CancelPromiseDialog, DeallocateDialog } from './PromiseDialogs'
import { BlockTitle, MoneyTimeline } from './Timeline'
import type { TimelineTone } from './Timeline'
import { isImportedBaseline, isPositiveMoney } from './moneyMath'

const EVENT_TONE: Record<string, TimelineTone> = {
    created: 'info',
    cancelled: 'neutral',
    allocated: 'success',
    deallocated: 'warning',
    allocation_voided: 'warning',
    edited: 'neutral',
    deleted: 'danger',
}

/** Imported-baseline notice (money report §7): fulfilment before Phase 1 is unknown. */
export const BaselineNote = ({ promise }: { promise: PromiseRow }) => {
    const { t } = useTranslation()
    if (!isImportedBaseline(promise)) return null
    return (
        <div
            className="mb-3 rounded-md border border-gray-300 bg-gray-50 px-3 py-2 text-[12.5px] text-gray-700 dark:border-night-border dark:bg-night-raised dark:text-night-body"
            data-testid="p1-imported-baseline"
        >
            <span className="font-extrabold">{t('p1.promises.baselineTitle')}</span> — {t('p1.promises.baselineHint')}
        </div>
    )
}

export type PromiseDetailProps = {
    promise: PromiseRow
    /** A mutation succeeded; reload the lists that show this promise. */
    onChanged: (updated?: PromiseRow) => void
    /** After a 409: reload the promise itself. */
    onReload: () => void
    showCase?: boolean
    showFacts?: boolean
}

const PromiseDetail = ({ promise, onChanged, onReload, showCase = true, showFacts = true }: PromiseDetailProps) => {
    const { t } = useTranslation()
    const perms = usePermissions()
    const [cancelOpen, setCancelOpen] = useState(false)
    const [allocOpen, setAllocOpen] = useState(false)
    const [dealloc, setDealloc] = useState<Allocation | null>(null)

    const p = promise
    const live = p.state === 'open' || p.state === 'overdue'
    const canCancel = !!perms.promises_cancel && live
    const canAllocate = !!perms.promises_allocate && p.state !== 'cancelled' && isPositiveMoney(p.remaining)
    const canDeallocate = !!perms.promises_allocate

    const events = (p.events || []).map((e, i) => ({
        key: `e${i}`,
        title: t(`p1.money.promiseEvent.${e.event}`, { defaultValue: e.event }),
        at: e.at,
        actor: e.actor?.name || null,
        source: e.source,
        tone: EVENT_TONE[e.event] || ('neutral' as TimelineTone),
        children:
            e.amount || e.reason || e.payment_id ? (
                <span className="flex flex-wrap gap-x-2">
                    {e.amount && <MoneyText value={e.amount} />}
                    {e.payment_id && (
                        <span className="text-gray-600">
                            {t('p1.money.fromPayment')} <Ltr title={e.payment_id}>{e.payment_id.slice(0, 8)}</Ltr>
                        </span>
                    )}
                    {e.reason && <span className="break-words">{e.reason}</span>}
                </span>
            ) : undefined,
    }))

    return (
        <div data-testid="p1-promise-detail" data-promise={p.id} data-state={p.state}>
            <BaselineNote promise={p} />
            {showFacts && (
                <div className="grid grid-cols-2 gap-3">
                    <Field label={t('p1.col.state')}>
                        <PromiseBadge state={p.state} />
                    </Field>
                    <Field label={t('p1.col.dueDate')}>
                        <CivilDate value={p.due_date} />
                    </Field>
                    <Field label={t('p1.col.amount')}>
                        <MoneyText value={p.amount} strong />
                    </Field>
                    <Field label={t('p1.col.allocated')}>
                        <MoneyText value={p.allocated} />
                    </Field>
                    <Field label={t('p1.col.remaining')}>
                        <MoneyText value={p.remaining} />
                    </Field>
                    <Field label={t('p1.col.recorder')}>{p.recorder?.name || '—'}</Field>
                    <Field label={t('p1.col.created')}>
                        <RiyadhTime iso={p.created_at} />
                    </Field>
                    {p.fulfilled_at && (
                        <Field label={t('p1.promises.fulfilledAt')}>
                            <RiyadhTime iso={p.fulfilled_at} />
                        </Field>
                    )}
                    {showCase && p.case && (
                        <Field label={t('p1.col.case')} className="col-span-2">
                            <Link
                                to={caseLink(p.case_id, 'promises')}
                                className="font-extrabold text-azure-600 hover:underline"
                                data-testid="p1-promise-case-link"
                            >
                                {p.case.name}
                            </Link>{' '}
                            <Ltr className="text-[12.5px] text-gray-600">{p.case.account_number}</Ltr>
                        </Field>
                    )}
                </div>
            )}

            {p.cancellation && (
                <div
                    className="mt-3 rounded-md border border-gray-200 px-3 py-2 text-[13px] dark:border-night-border"
                    data-testid="p1-promise-cancellation"
                >
                    <span className="font-extrabold">{t('p1.promises.cancelledBy')}</span> {p.cancellation.by?.name || '—'} ·{' '}
                    <RiyadhTime iso={p.cancellation.at} />
                    <div className="mt-0.5 break-words">{p.cancellation.reason}</div>
                </div>
            )}

            {(canCancel || canAllocate) && (
                <div className="mt-4 flex flex-wrap gap-2">
                    {canAllocate && (
                        <Button size="sm" onClick={() => setAllocOpen(true)} data-testid="p1-promise-allocate">
                            {t('p1.promises.allocate')}
                        </Button>
                    )}
                    {canCancel && (
                        <Button size="sm" variant="plain" onClick={() => setCancelOpen(true)} data-testid="p1-promise-cancel">
                            {t('p1.promises.cancel')}
                        </Button>
                    )}
                </div>
            )}

            <BlockTitle>{t('p1.money.allocationsFromPayments')}</BlockTitle>
            {p.allocations ? (
                <AllocationList
                    allocations={p.allocations}
                    side="payment"
                    onDeallocate={canDeallocate ? setDealloc : undefined}
                />
            ) : (
                <div className="text-[13px] text-gray-600">{t('p1.money.historyNotLoaded')}</div>
            )}

            <BlockTitle>{t('p1.money.history')}</BlockTitle>
            {p.events ? (
                <MoneyTimeline entries={events} testId="p1-promise-events" />
            ) : (
                <div className="text-[13px] text-gray-600">{t('p1.money.historyNotLoaded')}</div>
            )}
            <Note className="mt-3">{t('p1.promises.stateNote')}</Note>

            <CancelPromiseDialog
                promise={cancelOpen ? p : null}
                onClose={() => setCancelOpen(false)}
                onReload={onReload}
                onDone={(updated) => onChanged(updated)}
            />
            <AllocateDialog
                promise={allocOpen ? p : null}
                onClose={() => setAllocOpen(false)}
                onReload={onReload}
                onDone={(updated) => onChanged(updated)}
            />
            <DeallocateDialog
                allocation={dealloc}
                onClose={() => setDealloc(null)}
                onReload={onReload}
                onDone={(updated) => onChanged(updated)}
            />
        </div>
    )
}

export default PromiseDetail
