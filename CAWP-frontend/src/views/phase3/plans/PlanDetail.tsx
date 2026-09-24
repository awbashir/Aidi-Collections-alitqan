/**
 * One repayment plan: facts, installments (each expandable to its
 * allocations), the event trail, and the actions the signed-in user may take
 * (`plans_manage`: cancel, allocate a payment, deallocate). Used by the
 * /plans side panel and the case plan tab. Every state and amount is the
 * API's value as returned; nothing is computed here.
 */
import { Fragment, useState } from 'react'
import classNames from 'classnames'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { HiOutlineChevronDown } from 'react-icons/hi'
import Button from '@/components/ui/Button'
import { CivilDate, Field, Ltr, MoneyText, RiyadhTime } from '@/components/v2/Display'
import { Note } from '@/components/v2/States'
import type { Plan, PlanAllocation, PlanInstallment } from '@/services/v2'
import { usePermissions } from '@/utils/v2/hooks'
import { BlockTitle, MoneyTimeline } from '@/views/phase1/money/Timeline'
import type { TimelineTone } from '@/views/phase1/money/Timeline'
import { AllocatePlanDialog, CancelPlanDialog, DeallocatePlanDialog } from './PlanDialogs'
import { DateRange, InstallmentBadge, PlanStateBadge } from './parts'
import { casePlanLink, shortId } from './planUtils'

const EVENT_TONE: Record<string, TimelineTone> = {
    created: 'info',
    cancelled: 'neutral',
    allocated: 'success',
    deallocated: 'warning',
    allocation_voided: 'warning',
    completed: 'success',
    reopened_unpaid: 'warning',
}

/** Events whose facts are fully shown by amount / installment / payment / reason. */
const PLAIN_EVENTS = new Set(['created', 'cancelled', 'allocated', 'deallocated', 'allocation_voided'])

/** "reopen_blocked_by_plan" -> "Reopen blocked by plan" (fallback label for an unknown key). */
const humanize = (key: string) => {
    const s = key.replace(/[_-]+/g, ' ').trim()
    return s ? s[0].toUpperCase() + s.slice(1) : key
}

/** A compact "key: value" line of an event's scalar details (unknown / new events). */
const EventDetails = ({ details }: { details: Record<string, unknown> }) => {
    const { t } = useTranslation()
    const pairs = Object.entries(details).filter(
        ([, v]) => v !== null && v !== undefined && (typeof v !== 'object' || Array.isArray(v))
    )
    if (pairs.length === 0) return null
    return (
        <span className="flex flex-wrap gap-x-3 text-[12px] text-gray-600" data-testid="p3-plan-event-details">
            {pairs.map(([k, v]) => (
                <span key={k}>
                    {t(`p3.plans.eventDetail.${k}`, { defaultValue: humanize(k) })}:{' '}
                    <span dir="auto" className="tabular-nums text-gray-700 dark:text-night-body">
                        {Array.isArray(v) ? v.join(', ') : String(v)}
                    </span>
                </span>
            ))}
        </span>
    )
}

const AllocationRow = ({
    a,
    onDeallocate,
}: {
    a: PlanAllocation
    onDeallocate?: (a: PlanAllocation) => void
}) => {
    const { t } = useTranslation()
    return (
        <li
            className={classNames(
                'flex flex-wrap items-start justify-between gap-2 rounded-md border px-3 py-2 text-[12.5px]',
                a.active
                    ? 'border-gray-200 bg-white dark:border-night-border dark:bg-night-card'
                    : 'border-dashed border-gray-300 bg-gray-50 text-gray-600 dark:border-night-border dark:bg-night-page'
            )}
            data-testid="p3-allocation"
            data-alloc={a.id}
            data-active={a.active ? 'true' : 'false'}
            data-amount={a.amount}
        >
            <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
                    <MoneyText value={a.amount} strong className={a.active ? undefined : 'line-through'} />
                    <span className="text-gray-600">
                        {t('p3.plans.alloc.fromPayment')} <Ltr title={a.payment_id}>{shortId(a.payment_id)}</Ltr>
                    </span>
                    <span
                        className={classNames(
                            'rounded px-1.5 text-[11px] font-extrabold',
                            a.active
                                ? 'bg-emerald-50 text-emerald-700 dark:bg-night-raised'
                                : 'bg-gray-200 text-gray-600 dark:bg-night-raised'
                        )}
                    >
                        {a.active ? t('p3.plans.alloc.active') : t('p3.plans.alloc.voided')}
                    </span>
                </div>
                <div className="mt-0.5 text-gray-700 dark:text-night-body">
                    {t(`p3.plans.alloc.source.${a.source}`, { defaultValue: a.source })} ·{' '}
                    <RiyadhTime iso={a.created_at} />
                </div>
                {a.source === 'manual' && a.note && (
                    <div className="mt-0.5 break-words">
                        <span className="text-gray-600">{t('p1.fields.reason')}:</span> {a.note}
                    </div>
                )}
                {!a.active && (
                    <div className="mt-0.5 break-words" data-testid="p3-allocation-void">
                        <span className="text-gray-600">{t('p3.plans.alloc.voidedAt')}</span>{' '}
                        <RiyadhTime iso={a.voided_at} />
                        {a.void_reason && <> · {a.void_reason}</>}
                    </div>
                )}
            </div>
            {a.active && onDeallocate && (
                <Button size="xs" variant="plain" onClick={() => onDeallocate(a)} data-testid="p3-alloc-deallocate-btn">
                    {t('p3.plans.deallocate')}
                </Button>
            )}
        </li>
    )
}

const InstallmentsTable = ({
    installments,
    onDeallocate,
    compact,
}: {
    installments: PlanInstallment[]
    onDeallocate?: (a: PlanAllocation) => void
    /** Narrow panel: "paid at" goes under the state badge instead of its own column. */
    compact?: boolean
}) => {
    const { t } = useTranslation()
    const [open, setOpen] = useState<Set<string>>(new Set())
    const toggle = (id: string) =>
        setOpen((prev) => {
            const next = new Set(prev)
            if (next.has(id)) next.delete(id)
            else next.add(id)
            return next
        })
    const cols = compact ? 6 : 7
    const pad = compact ? 'px-2' : 'px-3'
    const th = `${pad} py-2 text-start text-[12px] font-extrabold text-gray-600 whitespace-nowrap`
    const td = `${pad} py-2 align-middle`
    const paidAt = (i: PlanInstallment) =>
        i.paid_at ? <RiyadhTime iso={i.paid_at} /> : <span className="text-gray-500">—</span>
    return (
        <div className="overflow-x-auto rounded-lg border border-gray-300 dark:border-night-border">
            <table className="w-full text-[13px] tabular-nums" data-testid="p3-installments">
                <thead className="bg-gray-50 dark:bg-night-page">
                    <tr>
                        <th className={th}>#</th>
                        <th className={th}>{t('p3.plans.col.dueDate')}</th>
                        <th className={classNames(th, 'text-end')}>{t('p3.plans.col.amount')}</th>
                        <th className={classNames(th, 'text-end')}>{t('p3.plans.col.allocated')}</th>
                        <th className={classNames(th, 'text-end')}>{t('p3.plans.col.remaining')}</th>
                        <th className={th}>{t('p3.plans.col.state')}</th>
                        {!compact && <th className={th}>{t('p3.plans.col.paidAt')}</th>}
                    </tr>
                </thead>
                <tbody>
                    {installments.map((i) => {
                        const isOpen = open.has(i.id)
                        const n = i.allocations?.length || 0
                        return (
                            <Fragment key={i.id}>
                                <tr
                                    className="h-11 cursor-pointer border-t border-gray-200 hover:bg-gray-50 dark:border-night-border dark:hover:bg-night-raised"
                                    onClick={() => toggle(i.id)}
                                    aria-expanded={isOpen}
                                    data-testid="p3-installment-row"
                                    data-seq={i.seq}
                                    data-due={i.due_date}
                                    data-amount={i.amount}
                                    data-state={i.state}
                                >
                                    <td className={td}>
                                        <span className="flex items-center gap-1.5 whitespace-nowrap">
                                            <HiOutlineChevronDown
                                                aria-hidden
                                                className={classNames('text-gray-500', isOpen && 'rotate-180')}
                                            />
                                            <Ltr className="font-extrabold">{i.seq}</Ltr>
                                            {n > 0 && (
                                                <span
                                                    className="rounded-pill bg-slate-100 px-1.5 text-[11px] text-gray-600 dark:bg-night-raised"
                                                    title={String(t('p3.plans.alloc.countTitle'))}
                                                >
                                                    <Ltr>{n}</Ltr>
                                                </span>
                                            )}
                                        </span>
                                    </td>
                                    <td className={td}>
                                        <CivilDate value={i.due_date} />
                                    </td>
                                    <td className={classNames(td, 'text-end')}>
                                        <MoneyText value={i.amount} strong />
                                    </td>
                                    <td className={classNames(td, 'text-end')}>
                                        <MoneyText value={i.allocated} />
                                    </td>
                                    <td className={classNames(td, 'text-end')}>
                                        <MoneyText value={i.remaining} />
                                    </td>
                                    <td className={td}>
                                        <InstallmentBadge state={i.state} />
                                        {compact && i.paid_at && (
                                            <div className="mt-0.5 text-[11.5px] text-gray-600" title={String(t('p3.plans.col.paidAt'))}>
                                                {paidAt(i)}
                                            </div>
                                        )}
                                    </td>
                                    {!compact && <td className={td}>{paidAt(i)}</td>}
                                </tr>
                                {isOpen && (
                                    <tr data-testid="p3-installment-allocations" data-seq={i.seq}>
                                        <td colSpan={cols} className="bg-gray-50 px-3 py-3 dark:bg-night-page">
                                            {n === 0 ? (
                                                <div className="text-[12.5px] text-gray-600">{t('p3.plans.alloc.none')}</div>
                                            ) : (
                                                <ul className="flex flex-col gap-2">
                                                    {i.allocations.map((a) => (
                                                        <AllocationRow key={a.id} a={a} onDeallocate={onDeallocate} />
                                                    ))}
                                                </ul>
                                            )}
                                        </td>
                                    </tr>
                                )}
                            </Fragment>
                        )
                    })}
                </tbody>
            </table>
        </div>
    )
}

export type PlanDetailProps = {
    plan: Plan
    /** A mutation succeeded (with the plan the API returned). */
    onChanged: (updated?: Plan) => void
    /** After a 409 or a reload request: load the plan again. */
    onReload: () => void
    /** No actions at all (earlier plans on the case tab). */
    readOnly?: boolean
    /** Show the case name and the link to its plan tab (the /plans side panel). */
    showCase?: boolean
    /** Two-column facts (narrow side panel). */
    compact?: boolean
}

const PlanDetail = ({ plan, onChanged, onReload, readOnly, showCase, compact }: PlanDetailProps) => {
    const { t } = useTranslation()
    const perms = usePermissions()
    const [cancelOpen, setCancelOpen] = useState(false)
    const [allocOpen, setAllocOpen] = useState(false)
    const [dealloc, setDealloc] = useState<PlanAllocation | null>(null)

    const p = plan
    /* Only an open plan takes actions: stored `status` must be `active`. A plan
     * closed as completed (stored status since 2026-09-24) or cancelled is
     * read-only. Before that change a fully paid plan stayed `active` with
     * state `completed`; it keeps its actions so it can still be cancelled. */
    const manage = !!perms.plans_manage && !readOnly && p.status === 'active' && p.state !== 'cancelled'
    const installments = p.installments || []
    const seqOf = new Map<string, number>(installments.map((i) => [i.id, i.seq]))

    const events = (p.events || []).map((e, idx) => {
        const seq = e.installment_id ? seqOf.get(e.installment_id) : undefined
        return {
            key: `e${idx}`,
            title: (
                <span className="inline-flex flex-wrap items-center gap-2" data-event={e.event}>
                    {t(`p3.plans.event.${e.event}`, { defaultValue: humanize(e.event) })}
                    {e.source === 'legacy' && (
                        <span
                            className="rounded bg-gray-100 px-1.5 text-[11px] font-extrabold text-gray-600 dark:bg-night-raised"
                            data-testid="p3-plan-event-legacy"
                        >
                            {t('p3.plans.eventLegacy')}
                        </span>
                    )}
                </span>
            ),
            at: e.at,
            actor: e.actor?.name || null,
            source: null,
            tone: EVENT_TONE[e.event] || ('neutral' as TimelineTone),
            children:
                e.amount || e.reason || e.payment_id || seq !== undefined || (!PLAIN_EVENTS.has(e.event) && e.details) ? (
                    <span className="flex flex-wrap gap-x-2">
                        {e.amount && <MoneyText value={e.amount} />}
                        {seq !== undefined && (
                            <span className="text-gray-600">
                                {t('p3.plans.eventInstallment')} <Ltr>#{seq}</Ltr>
                            </span>
                        )}
                        {e.payment_id && (
                            <span className="text-gray-600">
                                {t('p3.plans.alloc.fromPayment')} <Ltr title={e.payment_id}>{shortId(e.payment_id)}</Ltr>
                            </span>
                        )}
                        {e.reason && <span className="break-words">{e.reason}</span>}
                        {!PLAIN_EVENTS.has(e.event) && e.details && <EventDetails details={e.details} />}
                    </span>
                ) : undefined,
        }
    })

    const splitText =
        p.split_mode === 'amount' ? (
            <span>
                {t('p3.plans.split.amount')} · <MoneyText value={p.installment_amount} />
            </span>
        ) : (
            <span>{t('p3.plans.split.count')}</span>
        )

    return (
        <div data-testid="p3-plan-detail" data-plan={p.id} data-state={p.state} data-revision={p.revision}>
            {showCase && p.case && (
                <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
                    <div className="min-w-0">
                        <div className="font-extrabold text-navy-600 dark:text-gray-100">{p.case.name}</div>
                        <Ltr className="text-[12.5px] text-gray-600">{p.case.account_number}</Ltr>
                    </div>
                    <Link
                        to={casePlanLink(p.case_id)}
                        className="text-[13px] font-extrabold text-azure-600 hover:underline"
                        data-testid="p3-plan-case-link"
                    >
                        {t('p3.plans.openCasePlan')}
                    </Link>
                </div>
            )}

            <div className={classNames('grid gap-3', compact ? 'grid-cols-2' : 'grid-cols-2 md:grid-cols-4')}>
                <Field label={t('p3.plans.col.state')}>
                    <PlanStateBadge state={p.state} />
                </Field>
                <Field label={t('p3.plans.col.frequency')}>{t(`p3.plans.freq.${p.frequency}`)}</Field>
                <Field label={t('p3.plans.col.startEnd')}>
                    <span className="whitespace-nowrap">
                        <DateRange start={p.start_date} end={p.end_date} />
                    </span>
                </Field>
                <Field label={t('p3.plans.col.installments')}>
                    <span data-testid="p3-plan-paid-count">
                        {t('p3.plans.paidOfTotal', { paid: p.counts?.paid ?? 0, total: p.installment_count })}
                    </span>
                </Field>
                <Field label={t('p3.plans.col.total')}>
                    <MoneyText value={p.total_amount} strong />
                </Field>
                <Field label={t('p3.plans.col.allocated')}>
                    <MoneyText value={p.allocated} />
                </Field>
                <Field label={t('p3.plans.col.remaining')}>
                    <MoneyText value={p.remaining} />
                </Field>
                <Field label={t('p3.plans.col.overdue')}>
                    <MoneyText value={p.overdue_amount} />
                </Field>
                <Field label={t('p3.plans.col.nextDue')}>
                    <CivilDate value={p.next_due_date} />
                </Field>
                <Field label={t('p3.plans.col.oldestOverdue')}>
                    <CivilDate value={p.oldest_overdue_date} />
                </Field>
                <Field label={t('p3.plans.col.split')}>{splitText}</Field>
                <Field label={t('p3.plans.col.outstandingAtCreation')}>
                    <MoneyText value={p.outstanding_at_creation} />
                </Field>
                <Field label={t('p3.plans.col.createdBy')}>{p.created_by?.name || '—'}</Field>
                <Field label={t('p3.plans.col.createdAt')}>
                    <RiyadhTime iso={p.created_at} />
                </Field>
                {p.completed_at && (
                    <Field label={t('p3.plans.col.completedAt')}>
                        <RiyadhTime iso={p.completed_at} />
                    </Field>
                )}
                <Field label={t('p3.plans.col.brokenThreshold')}>
                    <Ltr>{p.broken_threshold}</Ltr>
                </Field>
                {p.note && (
                    <Field label={t('p3.plans.col.note')} className="col-span-2">
                        <span dir="auto">{p.note}</span>
                    </Field>
                )}
            </div>

            {p.state === 'broken' && (
                <div
                    className="mt-3 rounded-md border border-red-200 bg-danger-tint px-3 py-2 text-[13px] text-danger dark:border-night-border dark:bg-night-raised"
                    data-testid="p3-plan-broken-note"
                >
                    {t('p3.plans.brokenNote', { n: p.broken_threshold })}
                </div>
            )}

            {p.cancellation && (
                <div
                    className="mt-3 rounded-md border border-gray-200 px-3 py-2 text-[13px] dark:border-night-border"
                    data-testid="p3-plan-cancellation"
                >
                    <span className="font-extrabold">{t('p3.plans.cancelledBy')}</span> {p.cancellation.by?.name || '—'} ·{' '}
                    <RiyadhTime iso={p.cancellation.at} />
                    <div className="mt-0.5 break-words">{p.cancellation.reason}</div>
                </div>
            )}

            {manage && (
                <div className="mt-4 flex flex-wrap gap-2">
                    <Button size="sm" onClick={() => setAllocOpen(true)} data-testid="p3-plan-allocate">
                        {t('p3.plans.alloc.open')}
                    </Button>
                    <Button size="sm" variant="plain" onClick={() => setCancelOpen(true)} data-testid="p3-plan-cancel">
                        {t('p3.plans.cancel')}
                    </Button>
                </div>
            )}

            <BlockTitle>{t('p3.plans.installmentsTitle')}</BlockTitle>
            {installments.length > 0 ? (
                <InstallmentsTable
                    installments={installments}
                    compact={compact}
                    onDeallocate={manage ? setDealloc : undefined}
                />
            ) : (
                <div className="text-[13px] text-gray-600">{t('p3.plans.notLoaded')}</div>
            )}

            <BlockTitle>{t('p3.plans.historyTitle')}</BlockTitle>
            {p.events ? (
                <MoneyTimeline entries={events} testId="p3-plan-events" />
            ) : (
                <div className="text-[13px] text-gray-600">{t('p3.plans.notLoaded')}</div>
            )}
            <Note className="mt-3">{t('p3.plans.detailNote')}</Note>

            <CancelPlanDialog
                plan={cancelOpen ? p : null}
                onClose={() => setCancelOpen(false)}
                onReload={onReload}
                onDone={(updated) => onChanged(updated)}
            />
            <AllocatePlanDialog
                plan={allocOpen ? p : null}
                onClose={() => setAllocOpen(false)}
                onReload={onReload}
                onDone={(updated) => onChanged(updated)}
            />
            <DeallocatePlanDialog
                allocation={dealloc}
                onClose={() => setDealloc(null)}
                onReload={onReload}
                onDone={(updated) => onChanged(updated)}
            />
        </div>
    )
}

export default PlanDetail
