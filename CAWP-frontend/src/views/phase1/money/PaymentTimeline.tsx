/**
 * The history of one payment: summary, decision, reversal, event timeline,
 * legacy edits (before/after) and promise allocations.
 *
 * Presentational. Pass a payment that carries `events` / `allocations` /
 * `changes` (GET /v2/cases/<case>/payments returns them; GET /v2/payments/<id>
 * does not), or use `CasePaymentHistory`, which loads it.
 */
import { useTranslation } from 'react-i18next'
import { moneyApi } from '@/services/v2'
import type { Allocation, Payment, PaymentEvent } from '@/services/v2'
import { useV2Query } from '@/utils/v2/hooks'
import { CivilDate, Field, Ltr, MoneyText, RiyadhTime } from '@/components/v2/Display'
import { PaymentBadge, PlainBadge } from '@/components/v2/Badges'
import { ErrorState, Note, SkeletonLines } from '@/components/v2/States'
import { BlockTitle, MoneyTimeline } from './Timeline'
import { ReceiptLinks } from '@/views/paymentAttachments'
import type { TimelineEntry, TimelineTone } from './Timeline'

const EVENT_TONE: Record<PaymentEvent['event'], TimelineTone> = {
    submitted: 'info',
    approved: 'success',
    rejected: 'danger',
    reversed: 'warning',
    edited: 'neutral',
    deleted: 'danger',
}

const shortId = (id: string | null | undefined) => (id ? id.slice(0, 8) : '—')

const show = (v: unknown) => {
    if (v === null || v === undefined || v === '') return '—'
    if (typeof v === 'object') return JSON.stringify(v)
    return String(v)
}

/** Allocations of a payment to promises (active and voided). */
export const AllocationList = ({
    allocations,
    side,
    onDeallocate,
}: {
    allocations: Allocation[]
    /** Which id to show as the counterpart: the promise (payment view) or the payment (promise view). */
    side: 'promise' | 'payment'
    onDeallocate?: (a: Allocation) => void
}) => {
    const { t } = useTranslation()
    if (allocations.length === 0) {
        return <div className="text-[13px] text-gray-600">{t('p1.money.noAllocations')}</div>
    }
    return (
        <ul className="flex flex-col gap-2" data-testid="p1-allocations">
            {allocations.map((a) => (
                <li
                    key={a.id}
                    data-testid="p1-allocation"
                    data-active={a.active ? 'true' : 'false'}
                    className="rounded-md border border-gray-200 px-3 py-2 dark:border-night-border"
                >
                    <div className="flex flex-wrap items-center justify-between gap-2">
                        <div className="flex flex-wrap items-center gap-2">
                            <MoneyText value={a.amount} strong />
                            <PlainBadge tone={a.active ? 'success' : 'neutral'} size="row">
                                {a.active ? t('p1.money.allocActive') : t('p1.money.allocVoided')}
                            </PlainBadge>
                            <span className="text-[12px] text-gray-600">
                                {t(`p1.money.allocSource.${a.source}`, { defaultValue: a.source })}
                            </span>
                        </div>
                        {a.active && onDeallocate && (
                            <button
                                type="button"
                                className="text-[12.5px] font-extrabold text-azure-600 hover:underline"
                                onClick={() => onDeallocate(a)}
                                data-testid="p1-deallocate"
                            >
                                {t('p1.money.deallocate')}
                            </button>
                        )}
                    </div>
                    <div className="mt-1 flex flex-wrap gap-x-3 text-[12px] text-gray-600">
                        <span>
                            {side === 'promise' ? t('p1.money.toPromise') : t('p1.money.fromPayment')}{' '}
                            <Ltr title={side === 'promise' ? a.promise_id : a.payment_id}>
                                {shortId(side === 'promise' ? a.promise_id : a.payment_id)}
                            </Ltr>
                        </span>
                        <RiyadhTime iso={a.created_at} />
                    </div>
                    {a.note && <div className="mt-1 text-[12.5px]">{a.note}</div>}
                    {!a.active && (
                        <div className="mt-1 text-[12.5px] text-gray-700 dark:text-night-body">
                            {t('p1.money.voidedAt')} <RiyadhTime iso={a.voided_at} />
                            {a.void_reason ? ` · ${a.void_reason}` : ''}
                        </div>
                    )}
                </li>
            ))}
        </ul>
    )
}

const PaymentTimeline = ({ payment, compact }: { payment: Payment; compact?: boolean }) => {
    const { t } = useTranslation()
    const p = payment
    const events = p.events
    // Legacy edits/deletes (old screens) with before/after. v2 decisions and the legacy
    // "create" row are already in the event timeline, so they are not repeated here.
    const changes = (p.changes || []).filter(
        (c) => String(c.source || '') !== 'api' && !String(c.action || '').endsWith('.create')
    )
    const entries: TimelineEntry[] = (events || []).map((e, i) => ({
        key: `e${i}`,
        title: t(`p1.money.paymentEvent.${e.event}`, { defaultValue: e.event }),
        at: e.at,
        actor: e.actor?.name || null,
        source: e.source,
        tone: EVENT_TONE[e.event] || 'neutral',
        children:
            e.amount || e.comment ? (
                <span className="flex flex-wrap gap-x-2">
                    {e.amount && <MoneyText value={e.amount} />}
                    {e.comment && <span className="break-words">{e.comment}</span>}
                </span>
            ) : undefined,
    }))

    return (
        <div data-testid="p1-payment-timeline" data-payment={p.id}>
            {!compact && (
                <div className="grid grid-cols-2 gap-3">
                    <Field label={t('p1.col.status')}>
                        <PaymentBadge status={p.status} />
                    </Field>
                    <Field label={t('p1.col.amount')}>
                        <MoneyText value={p.amount} strong />
                    </Field>
                    <Field label={t('p1.col.paymentDate')}>
                        <CivilDate value={p.payment_civil_date} />
                    </Field>
                    <Field label={t('p1.col.created')}>
                        <RiyadhTime iso={p.created_at} />
                    </Field>
                    <Field label={t('p1.col.recorder')}>{p.recorder?.name || '—'}</Field>
                    <Field label={t('p1.col.method')}>
                        {[p.collection_type?.name, p.collection_method?.name].filter(Boolean).join(' / ') || '—'}
                    </Field>
                    <Field label={t('p1.col.source')}>
                        {t(`p1.money.source.${p.source}`, { defaultValue: p.source })}
                    </Field>
                    <Field label={t('p1.money.allocatedUnallocated')}>
                        <MoneyText value={p.allocated} /> / <MoneyText value={p.unallocated} />
                    </Field>
                </div>
            )}

            {p.attachments && (
                <>
                    <BlockTitle>{t('p7.payAtt.column')}</BlockTitle>
                    {p.attachments.length > 0 ? (
                        <ReceiptLinks attachments={p.attachments} />
                    ) : (
                        <div className="text-[13px] text-gray-600">{t('p7.payAtt.none')}</div>
                    )}
                </>
            )}

            <BlockTitle>{t('p1.col.decision')}</BlockTitle>
            {p.decision ? (
                <div className="text-[13px]" data-testid="p1-payment-decision">
                    <span className="font-extrabold">
                        {p.decision.approved ? t('p1.money.decisionApproved') : t('p1.money.decisionRejected')}
                    </span>{' '}
                    · {p.decision.by?.name || '—'} · <RiyadhTime iso={p.decision.at} />
                    {p.decision.comment && <div className="mt-0.5 break-words text-gray-700">{p.decision.comment}</div>}
                </div>
            ) : (
                <div className="text-[13px] text-gray-600">{t('p1.money.noDecision')}</div>
            )}

            {p.reversal && (
                <>
                    <BlockTitle>{t('p1.money.reversal')}</BlockTitle>
                    <div
                        className="rounded-md border border-gray-200 px-3 py-2 text-[13px] dark:border-night-border"
                        data-testid="p1-payment-reversal"
                    >
                        <div>
                            <MoneyText value={p.reversal.amount} strong /> · {p.reversal.by?.name || '—'} ·{' '}
                            <RiyadhTime iso={p.reversal.at} />
                        </div>
                        <div className="mt-0.5 break-words">{p.reversal.reason}</div>
                        <Note className="mt-1">{t('p1.money.reversalKept')}</Note>
                    </div>
                </>
            )}

            <BlockTitle>{t('p1.money.history')}</BlockTitle>
            {events ? (
                <MoneyTimeline entries={entries} testId="p1-payment-events" />
            ) : (
                <div className="text-[13px] text-gray-600">{t('p1.money.historyNotLoaded')}</div>
            )}

            {changes.length > 0 && (
                <>
                    <BlockTitle>{t('p1.money.legacyChanges')}</BlockTitle>
                    <ul className="flex flex-col gap-2 text-[12.5px]" data-testid="p1-payment-changes">
                        {changes.map((c, i) => {
                            const before = (c.before || {}) as Record<string, unknown>
                            const after = (c.after || {}) as Record<string, unknown>
                            const fields = Array.from(new Set([...Object.keys(before), ...Object.keys(after)]))
                            return (
                                <li key={i} className="rounded-md border border-gray-200 px-3 py-2 dark:border-night-border">
                                    <div className="flex flex-wrap gap-x-2 text-gray-600">
                                        <span className="font-extrabold text-navy-600 dark:text-gray-100">
                                            {t(`p1.money.audit.${String(c.action)}`, { defaultValue: String(c.action) })}
                                        </span>
                                        <RiyadhTime iso={c.at as string} />
                                    </div>
                                    {fields.length > 0 && (
                                        <div className="mt-1 flex flex-col gap-0.5">
                                            {fields.slice(0, 12).map((f) => (
                                                <div key={f} className="flex flex-wrap gap-x-1.5">
                                                    <Ltr className="text-gray-600">{f}</Ltr>
                                                    <Ltr>{show(before[f])}</Ltr>
                                                    <span aria-hidden className="inline-block text-gray-400 rtl:rotate-180">
                                                        →
                                                    </span>
                                                    <Ltr>{show(after[f])}</Ltr>
                                                </div>
                                            ))}
                                        </div>
                                    )}
                                </li>
                            )
                        })}
                    </ul>
                </>
            )}

            {p.allocations && (
                <>
                    <BlockTitle>{t('p1.money.allocationsToPromises')}</BlockTitle>
                    <AllocationList allocations={p.allocations} side="promise" />
                </>
            )}
        </div>
    )
}

export default PaymentTimeline

/**
 * Loads a case's payment history (GET /v2/cases/<case>/payments, the only read
 * that carries events, allocations and legacy changes) and shows one payment.
 */
export const CasePaymentHistory = ({
    caseId,
    paymentId,
    refreshKey,
    compact,
}: {
    caseId: string
    paymentId: string
    refreshKey?: number
    compact?: boolean
}) => {
    const q = useV2Query((signal) => moneyApi.casePayments(caseId, signal), [caseId, refreshKey])
    if (q.loading && !q.data) return <SkeletonLines rows={5} />
    if (q.error && !q.data) return <ErrorState error={q.error} onRetry={q.reload} />
    const payment = q.data?.items.find((i) => i.id === paymentId)
    if (!payment) return <ErrorState error={new Error('payment not in case history')} onRetry={q.reload} />
    return <PaymentTimeline payment={payment} compact={compact} />
}
