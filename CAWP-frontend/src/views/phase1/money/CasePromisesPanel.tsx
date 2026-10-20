/**
 * Case detail: promises and payments (the coordinator wires it into the case
 * screen's "promises" tab).
 *
 * - GET /v2/cases/<id>/promises: state counts, each promise with its
 *   allocations and events; create (key `promises`), cancel, allocate,
 *   deallocate (PromiseDetail).
 * - GET /v2/cases/<id>/payments: a compact list with status badges and each
 *   payment's history (PaymentTimeline).
 * Nothing about state (overdue, fulfilled) is computed here; it is the API's.
 */
import { Fragment, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { HiOutlineChevronDown, HiOutlinePlus } from 'react-icons/hi'
import Button from '@/components/ui/Button'
import Input from '@/components/ui/Input'
import Table from '@/components/ui/Table'
import ToastNotification from '@/components/shared/myComponents/ToastNotification'
import ActionDialog, { DialogField } from '@/components/v2/ActionDialog'
import { CivilDate, MoneyText, RiyadhTime } from '@/components/v2/Display'
import { PaymentBadge, PromiseBadge } from '@/components/v2/Badges'
import { EmptyState, ErrorState, Note, SkeletonRows } from '@/components/v2/States'
import { PROMISE_STATES, moneyApi } from '@/services/v2'
import type { PromiseRow } from '@/services/v2'
import type { CasePanelProps } from '@/views/phase1/caseTypes'
import { MONEY_INPUT, formatCount, formatMoney, riyadhToday } from '@/utils/v2/format'
import { usePermissions, useV2Query } from '@/utils/v2/hooks'
import BalanceBreakdown from './BalanceBreakdown'
import PaymentTimeline from './PaymentTimeline'
import PromiseDetail from './PromiseDetail'
import { isImportedBaseline, isPositiveMoney } from './moneyMath'
import { PROMISE_CHANGED_EVENT } from './requiredPromise'

const { THead, TBody, Tr, Th, Td } = Table

const CreatePromiseDialog = ({
    caseId,
    isOpen,
    onClose,
    onDone,
}: {
    caseId: string
    isOpen: boolean
    onClose: () => void
    onDone: (p: PromiseRow) => void
}) => {
    const { t } = useTranslation()
    const [amount, setAmount] = useState('')
    const [due, setDue] = useState('')
    const [touched, setTouched] = useState(false)
    const [lastError, setLastError] = useState<unknown>(null)
    const today = riyadhToday()
    const a = amount.trim()
    // Mirrors the API: amount required, positive, at most 2 decimals; due date not before Riyadh today.
    const amountErr = !a
        ? t('p1.promises.amountRequired')
        : !MONEY_INPUT.test(a)
        ? t('p1.promises.amountFormat')
        : !isPositiveMoney(a)
        ? t('p1.promises.amountPositive')
        : ''
    const dueErr = !due ? t('p1.promises.dueRequired') : due < today ? t('p1.promises.dueInPast', { today }) : ''
    const reset = () => {
        setAmount('')
        setDue('')
        setTouched(false)
        setLastError(null)
    }
    return (
        <ActionDialog
            isOpen={isOpen}
            title={t('p1.promises.createTitle')}
            confirmLabel={t('p1.promises.create')}
            canSubmit={!amountErr && !dueErr}
            testId="p1-promise-create-dialog"
            onClose={() => {
                reset()
                onClose()
            }}
            onSubmit={async (key) => {
                setTouched(true)
                setLastError(null)
                try {
                    const p = await moneyApi.createPromise(caseId, { amount: a, due_date: due }, key)
                    reset()
                    onDone(p)
                } catch (err) {
                    setLastError(err)
                    throw err
                }
            }}
        >
            <DialogField
                label={t('p1.promises.amount')}
                required
                hint={touched || a ? <span className={amountErr ? 'text-danger' : ''}>{amountErr || t('p1.promises.amountHint')}</span> : t('p1.promises.amountHint')}
            >
                <Input
                    dir="ltr"
                    inputMode="decimal"
                    value={amount}
                    invalid={!!a && !!amountErr}
                    onChange={(e) => setAmount(e.target.value)}
                    onBlur={() => setTouched(true)}
                    data-testid="p1-promise-amount"
                />
            </DialogField>
            <DialogField
                label={t('p1.promises.dueDate')}
                required
                hint={
                    due && dueErr ? <span className="text-danger">{dueErr}</span> : t('p1.promises.dueHint', { today })
                }
            >
                <Input
                    type="date"
                    dir="ltr"
                    min={today}
                    value={due}
                    invalid={!!due && !!dueErr}
                    onChange={(e) => setDue(e.target.value)}
                    data-testid="p1-promise-due"
                />
            </DialogField>
            <Note>{t('p1.promises.createNote')}</Note>
            <BalanceBreakdown error={lastError} />
        </ActionDialog>
    )
}

const CasePayments = ({ caseId, refreshKey }: { caseId: string; refreshKey: number }) => {
    const { t } = useTranslation()
    const q = useV2Query((signal) => moneyApi.casePayments(caseId, signal), [caseId, refreshKey])
    const [open, setOpen] = useState<string | null>(null)
    const items = q.data?.items || []
    const cols = 6
    return (
        <section className="mt-8" data-testid="p1-case-payments">
            <div className="mb-3 flex items-center justify-between gap-2">
                <h6 className="mb-0 text-[15px] font-extrabold text-navy-600 dark:text-gray-100">
                    {t('p1.casePromises.paymentsTitle')}
                </h6>
                {q.data && (
                    <span className="text-[12.5px] text-gray-600 tabular-nums">
                        {t('p1.casePromises.paymentsCount', { count: items.length, formatted: formatCount(items.length) })}
                    </span>
                )}
            </div>
            <div className="overflow-hidden rounded-lg border border-gray-300 bg-white dark:border-night-border dark:bg-night-card">
                {q.error && !q.data ? (
                    <ErrorState error={q.error} onRetry={q.reload} />
                ) : (
                    <Table>
                        <THead>
                            <Tr>
                                <Th>{t('p1.col.status')}</Th>
                                <Th className="text-end">{t('p1.col.amount')}</Th>
                                <Th>{t('p1.col.paymentDate')}</Th>
                                <Th className="text-end">{t('p1.casePromises.unallocated')}</Th>
                                <Th>{t('p1.col.recorder')}</Th>
                                <Th>{t('p1.col.decision')}</Th>
                            </Tr>
                        </THead>
                        <TBody>
                            {q.loading && !q.data ? (
                                <SkeletonRows cols={cols} rows={3} />
                            ) : items.length === 0 ? (
                                <tr>
                                    <td colSpan={cols}>
                                        <EmptyState title={t('p1.casePromises.noPayments')} />
                                    </td>
                                </tr>
                            ) : (
                                items.map((p) => (
                                    <Fragment key={p.id}>
                                        <Tr
                                            className="cursor-pointer"
                                            onClick={() => setOpen(open === p.id ? null : p.id)}
                                            aria-expanded={open === p.id}
                                            data-testid="p1-case-payment-row"
                                            data-payment={p.id}
                                            data-status={p.status}
                                        >
                                            <Td>
                                                <span className="flex items-center gap-2">
                                                    <HiOutlineChevronDown
                                                        aria-hidden
                                                        className={open === p.id ? 'rotate-180' : ''}
                                                    />
                                                    <PaymentBadge status={p.status} size="row" />
                                                </span>
                                            </Td>
                                            <Td className="text-end">
                                                <MoneyText value={p.amount} strong />
                                            </Td>
                                            <Td>
                                                <CivilDate value={p.payment_civil_date} />
                                            </Td>
                                            <Td className="text-end">
                                                <MoneyText value={p.unallocated} />
                                            </Td>
                                            <Td>{p.recorder?.name || '—'}</Td>
                                            <Td>
                                                {p.decision ? (
                                                    <span className="text-[12.5px]">
                                                        {p.decision.by?.name || '—'} · <RiyadhTime iso={p.decision.at} />
                                                    </span>
                                                ) : (
                                                    <span className="text-gray-500">—</span>
                                                )}
                                            </Td>
                                        </Tr>
                                        {open === p.id && (
                                            <tr>
                                                <td colSpan={cols} className="bg-gray-50 px-5 py-4 dark:bg-night-page">
                                                    <PaymentTimeline payment={p} />
                                                </td>
                                            </tr>
                                        )}
                                    </Fragment>
                                ))
                            )}
                        </TBody>
                    </Table>
                )}
            </div>
        </section>
    )
}

const CasePromisesPanel = ({ caseData, onChanged }: CasePanelProps) => {
    const { t } = useTranslation()
    const perms = usePermissions()
    const caseId = caseData.id
    const [refreshKey, setRefreshKey] = useState(0)
    const [createOpen, setCreateOpen] = useState(false)
    const [open, setOpen] = useState<string | null>(null)
    const q = useV2Query((signal) => moneyApi.casePromises(caseId, signal), [caseId, refreshKey])
    /* 0079: a note / call result with a promise-to-pay status created (or linked) a promise */
    useEffect(() => {
        const onPromise = (e: Event) => {
            if ((e as CustomEvent<{ caseId: string }>).detail?.caseId === caseId) setRefreshKey((n) => n + 1)
        }
        window.addEventListener(PROMISE_CHANGED_EVENT, onPromise)
        return () => window.removeEventListener(PROMISE_CHANGED_EVENT, onPromise)
    }, [caseId])
    const data = q.data
    const items = data?.items || []

    const changed = (message?: string) => {
        setRefreshKey((n) => n + 1)
        if (message) ToastNotification(message, t('p1.promises.title'), 'success')
        onChanged?.()
    }

    const cols = 6
    return (
        <div data-testid="p1-case-promises">
            <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
                <div className="flex flex-wrap items-center gap-2" data-testid="p1-case-promise-counts">
                    <h6 className="mb-0 me-2 text-[15px] font-extrabold text-navy-600 dark:text-gray-100">
                        {t('p1.promises.title')}
                    </h6>
                    {data &&
                        PROMISE_STATES.map((s) => (
                            <span key={s} className="flex items-center gap-1" data-state={s} data-count={data.counts_by_state[s]}>
                                <PromiseBadge state={s} size="row" />
                                <span className="text-[12.5px] font-extrabold tabular-nums">
                                    {formatCount(data.counts_by_state[s])}
                                </span>
                            </span>
                        ))}
                </div>
                <div className="flex items-center gap-2">
                    {perms.promises && !caseData.is_locked && (
                        <Button
                            size="sm"
                            variant="solid"
                            icon={<HiOutlinePlus />}
                            onClick={() => setCreateOpen(true)}
                            data-testid="p1-promise-create"
                        >
                            {t('p1.promises.create')}
                        </Button>
                    )}
                </div>
            </div>
            {caseData.is_locked && perms.promises && <Note className="mb-3">{t('p1.casePromises.locked')}</Note>}

            <div className="overflow-hidden rounded-lg border border-gray-300 bg-white dark:border-night-border dark:bg-night-card">
                {q.error && !data ? (
                    <ErrorState error={q.error} onRetry={q.reload} />
                ) : (
                    <Table>
                        <THead>
                            <Tr>
                                <Th>{t('p1.col.state')}</Th>
                                <Th>{t('p1.col.dueDate')}</Th>
                                <Th className="text-end">{t('p1.col.amount')}</Th>
                                <Th className="text-end">{t('p1.col.allocated')}</Th>
                                <Th className="text-end">{t('p1.col.remaining')}</Th>
                                <Th>{t('p1.col.recorder')}</Th>
                            </Tr>
                        </THead>
                        <TBody>
                            {q.loading && !data ? (
                                <SkeletonRows cols={cols} rows={3} />
                            ) : items.length === 0 ? (
                                <tr>
                                    <td colSpan={cols}>
                                        <EmptyState
                                            title={t('p1.casePromises.emptyTitle')}
                                            hint={t('p1.casePromises.emptyHint')}
                                        />
                                    </td>
                                </tr>
                            ) : (
                                items.map((p) => (
                                    <Fragment key={p.id}>
                                        <Tr
                                            className="cursor-pointer"
                                            onClick={() => setOpen(open === p.id ? null : p.id)}
                                            aria-expanded={open === p.id}
                                            data-testid="p1-case-promise-row"
                                            data-promise={p.id}
                                            data-state={p.state}
                                        >
                                            <Td>
                                                <span className="flex flex-wrap items-center gap-2">
                                                    <HiOutlineChevronDown
                                                        aria-hidden
                                                        className={open === p.id ? 'rotate-180' : ''}
                                                    />
                                                    <PromiseBadge state={p.state} size="row" />
                                                    {isImportedBaseline(p) && (
                                                        <span className="text-[11.5px] text-gray-600">
                                                            {t('p1.promises.baselineShort')}
                                                        </span>
                                                    )}
                                                </span>
                                            </Td>
                                            <Td>
                                                <CivilDate value={p.due_date} />
                                            </Td>
                                            <Td className="text-end">
                                                <MoneyText value={p.amount} strong />
                                            </Td>
                                            <Td className="text-end">
                                                <MoneyText value={p.allocated} />
                                            </Td>
                                            <Td className="text-end">
                                                <MoneyText value={p.remaining} />
                                            </Td>
                                            <Td>{p.recorder?.name || '—'}</Td>
                                        </Tr>
                                        {open === p.id && (
                                            <tr>
                                                <td colSpan={cols} className="bg-gray-50 px-5 py-4 dark:bg-night-page">
                                                    <PromiseDetail
                                                        promise={p}
                                                        showCase={false}
                                                        showFacts={false}
                                                        onReload={() => setRefreshKey((n) => n + 1)}
                                                        onChanged={() => changed(String(t('p1.promises.saved')))}
                                                    />
                                                </td>
                                            </tr>
                                        )}
                                    </Fragment>
                                ))
                            )}
                        </TBody>
                    </Table>
                )}
            </div>

            <CasePayments caseId={caseId} refreshKey={refreshKey} />

            <CreatePromiseDialog
                caseId={caseId}
                isOpen={createOpen}
                onClose={() => setCreateOpen(false)}
                onDone={(p) =>
                    changed(String(t('p1.promises.created', { amount: formatMoney(p.amount), due: p.due_date })))
                }
            />
        </div>
    )
}

export default CasePromisesPanel
