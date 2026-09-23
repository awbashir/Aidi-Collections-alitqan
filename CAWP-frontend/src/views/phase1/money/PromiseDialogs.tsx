/**
 * Promise mutations: cancel (reason), allocate an approved payment, deallocate
 * (reason). Each dialog opening is one user action with one Idempotency-Key
 * (ActionDialog / ReasonDialog); cancel sends the revision it loaded.
 */
import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import Input from '@/components/ui/Input'
import Select from '@/components/ui/Select'
import ActionDialog, { DialogField, ReasonDialog } from '@/components/v2/ActionDialog'
import { CivilDate, Field, MoneyText } from '@/components/v2/Display'
import { PromiseBadge } from '@/components/v2/Badges'
import { ErrorState, SkeletonLines } from '@/components/v2/States'
import { moneyApi } from '@/services/v2'
import type { Allocation, Payment, PromiseRow } from '@/services/v2'
import { useV2Query } from '@/utils/v2/hooks'
import { MONEY_INPUT, compareMoney, formatMoney } from '@/utils/v2/format'
import { isPositiveMoney } from './moneyMath'

const PromiseSummary = ({ promise }: { promise: PromiseRow }) => {
    const { t } = useTranslation()
    return (
        <div className="mb-4 grid grid-cols-2 gap-3 rounded-md border border-gray-200 p-3 sm:grid-cols-4 dark:border-night-border">
            <Field label={t('p1.col.state')}>
                <PromiseBadge state={promise.state} />
            </Field>
            <Field label={t('p1.col.dueDate')}>
                <CivilDate value={promise.due_date} />
            </Field>
            <Field label={t('p1.col.amount')}>
                <MoneyText value={promise.amount} strong />
            </Field>
            <Field label={t('p1.col.remaining')}>
                <MoneyText value={promise.remaining} />
            </Field>
        </div>
    )
}

export const CancelPromiseDialog = ({
    promise,
    onClose,
    onDone,
    onReload,
}: {
    promise: PromiseRow | null
    onClose: () => void
    onDone: (p: PromiseRow) => void
    onReload: () => void
}) => {
    const { t } = useTranslation()
    return (
        <ReasonDialog
            isOpen={!!promise}
            title={t('p1.promises.cancelTitle')}
            description={
                promise ? (
                    <>
                        <PromiseSummary promise={promise} />
                        {t('p1.promises.cancelHint')}
                    </>
                ) : undefined
            }
            tone="danger"
            confirmLabel={t('p1.promises.cancelConfirm')}
            testId="p1-promise-cancel-dialog"
            onClose={onClose}
            onReload={onReload}
            onSubmit={async (reason, key) => {
                if (!promise) return
                const res = await moneyApi.cancelPromise(
                    promise.id,
                    { expected_revision: promise.revision, reason },
                    key
                )
                onDone(res)
            }}
        />
    )
}

export const DeallocateDialog = ({
    allocation,
    onClose,
    onDone,
    onReload,
}: {
    allocation: Allocation | null
    onClose: () => void
    onDone: (p: PromiseRow) => void
    onReload: () => void
}) => {
    const { t } = useTranslation()
    return (
        <ReasonDialog
            isOpen={!!allocation}
            title={t('p1.promises.deallocateTitle')}
            description={
                allocation ? (
                    <span>
                        {t('p1.promises.deallocateHint')} <MoneyText value={allocation.amount} strong />
                    </span>
                ) : undefined
            }
            tone="danger"
            confirmLabel={t('p1.money.deallocate')}
            testId="p1-deallocate-dialog"
            onClose={onClose}
            onReload={onReload}
            onSubmit={async (reason, key) => {
                if (!allocation) return
                const res = await moneyApi.deallocate(allocation.id, { reason }, key)
                onDone(res.promise)
            }}
        />
    )
}

/** Approved payments of the case that still have unallocated money. */
const allocatable = (items: Payment[]) =>
    items.filter((p) => p.status === 'approved' && isPositiveMoney(p.unallocated))

const AllocateBody = ({
    promise,
    paymentId,
    setPaymentId,
    amount,
    setAmount,
    note,
    setNote,
}: {
    promise: PromiseRow
    paymentId: string | undefined
    setPaymentId: (v: string | undefined) => void
    amount: string
    setAmount: (v: string) => void
    note: string
    setNote: (v: string) => void
}) => {
    const { t } = useTranslation()
    const q = useV2Query((signal) => moneyApi.casePayments(promise.case_id, signal), [promise.case_id])
    const options = useMemo(
        () =>
            allocatable(q.data?.items || []).map((p) => ({
                value: p.id,
                label: `${p.payment_civil_date} · ${formatMoney(p.amount)} · ${t('p1.promises.unallocatedShort', {
                    amount: formatMoney(p.unallocated),
                })}`,
                payment: p,
            })),
        [q.data, t]
    )
    const selected = options.find((o) => o.value === paymentId) || null
    const amountInvalid = amount.trim() !== '' && (!MONEY_INPUT.test(amount.trim()) || !isPositiveMoney(amount.trim()))

    return (
        <>
            <PromiseSummary promise={promise} />
            {q.loading && !q.data ? (
                <SkeletonLines rows={2} />
            ) : q.error ? (
                <ErrorState error={q.error} onRetry={q.reload} />
            ) : options.length === 0 ? (
                <div className="mb-4 text-[13px] text-gray-700 dark:text-night-body" data-testid="p1-allocate-none">
                    {t('p1.promises.noAllocatablePayments')}
                </div>
            ) : (
                <DialogField label={t('p1.promises.payment')} required>
                    <div data-testid="p1-allocate-payment">
                        <Select
                            size="sm"
                            options={options}
                            value={selected}
                            placeholder={t('p1.promises.choosePayment')}
                            onChange={(o) => setPaymentId((o as { value: string } | null)?.value)}
                        />
                    </div>
                </DialogField>
            )}
            <DialogField
                label={t('p1.promises.allocAmount')}
                hint={
                    amountInvalid ? (
                        <span className="text-danger">{t('p1.promises.amountFormat')}</span>
                    ) : (
                        t('p1.promises.allocAmountHint')
                    )
                }
            >
                <Input
                    dir="ltr"
                    inputMode="decimal"
                    value={amount}
                    invalid={amountInvalid}
                    placeholder={
                        selected
                            ? formatMoney(
                                  compareMoney(selected.payment.unallocated, promise.remaining) <= 0
                                      ? selected.payment.unallocated
                                      : promise.remaining
                              )
                            : ''
                    }
                    onChange={(e) => setAmount(e.target.value)}
                    data-testid="p1-allocate-amount"
                />
            </DialogField>
            <DialogField label={t('p1.promises.allocNote')}>
                <Input
                    value={note}
                    maxLength={500}
                    onChange={(e) => setNote(e.target.value)}
                    data-testid="p1-allocate-note"
                />
            </DialogField>
        </>
    )
}

export const AllocateDialog = ({
    promise,
    onClose,
    onDone,
    onReload,
}: {
    promise: PromiseRow | null
    onClose: () => void
    onDone: (p: PromiseRow) => void
    onReload: () => void
}) => {
    const { t } = useTranslation()
    const [paymentId, setPaymentId] = useState<string | undefined>()
    const [amount, setAmount] = useState('')
    const [note, setNote] = useState('')
    const trimmed = amount.trim()
    const amountOk = trimmed === '' || (MONEY_INPUT.test(trimmed) && isPositiveMoney(trimmed))
    const reset = () => {
        setPaymentId(undefined)
        setAmount('')
        setNote('')
    }
    return (
        <ActionDialog
            isOpen={!!promise}
            title={t('p1.promises.allocateTitle')}
            confirmLabel={t('p1.promises.allocate')}
            canSubmit={!!paymentId && amountOk}
            testId="p1-allocate-dialog"
            onReload={onReload}
            onClose={() => {
                reset()
                onClose()
            }}
            onSubmit={async (key) => {
                if (!promise || !paymentId) return
                const res = await moneyApi.allocate(
                    promise.id,
                    {
                        payment_id: paymentId,
                        amount: trimmed || undefined,
                        note: note.trim() || undefined,
                    },
                    key
                )
                reset()
                onDone(res.promise)
            }}
        >
            {promise && (
                <AllocateBody
                    promise={promise}
                    paymentId={paymentId}
                    setPaymentId={setPaymentId}
                    amount={amount}
                    setAmount={setAmount}
                    note={note}
                    setNote={setNote}
                />
            )}
        </ActionDialog>
    )
}
