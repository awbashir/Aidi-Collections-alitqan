/**
 * Plan mutations: cancel (reason, sends the loaded revision), allocate an
 * approved payment of the same case by hand (reason), deallocate (reason).
 * Each dialog opening is one user action with one Idempotency-Key
 * (ActionDialog / ReasonDialog); errors stay in the dialog with the draft.
 */
import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import Input from '@/components/ui/Input'
import Select from '@/components/ui/Select'
import ActionDialog, { DialogField, ReasonDialog } from '@/components/v2/ActionDialog'
import { Field, MoneyText } from '@/components/v2/Display'
import { ErrorState, Note, SkeletonLines } from '@/components/v2/States'
import { moneyApi, plansApi } from '@/services/v2'
import type { Plan, PlanAllocation } from '@/services/v2'
import { useV2Query } from '@/utils/v2/hooks'
import { MONEY_INPUT, formatMoney } from '@/utils/v2/format'
import { PlanStateBadge } from './parts'
import { describePlanError, isPositiveAmount, shortId } from './planUtils'

const PlanSummary = ({ plan }: { plan: Plan }) => {
    const { t } = useTranslation()
    return (
        <div className="mb-4 grid grid-cols-2 gap-3 rounded-md border border-gray-200 p-3 sm:grid-cols-4 dark:border-night-border">
            <Field label={t('p3.plans.col.state')}>
                <PlanStateBadge state={plan.state} />
            </Field>
            <Field label={t('p3.plans.col.total')}>
                <MoneyText value={plan.total_amount} strong />
            </Field>
            <Field label={t('p3.plans.col.allocated')}>
                <MoneyText value={plan.allocated} />
            </Field>
            <Field label={t('p3.plans.col.remaining')}>
                <MoneyText value={plan.remaining} />
            </Field>
        </div>
    )
}

export const CancelPlanDialog = ({
    plan,
    onClose,
    onDone,
    onReload,
}: {
    plan: Plan | null
    onClose: () => void
    onDone: (p: Plan) => void
    onReload: () => void
}) => {
    const { t } = useTranslation()
    return (
        <ReasonDialog
            isOpen={!!plan}
            title={t('p3.plans.cancelTitle')}
            description={
                plan ? (
                    <>
                        <PlanSummary plan={plan} />
                        {t('p3.plans.cancelHint')}
                    </>
                ) : undefined
            }
            tone="danger"
            confirmLabel={t('p3.plans.cancelConfirm')}
            testId="p3-plan-cancel-dialog"
            describeError={describePlanError}
            onClose={onClose}
            onReload={onReload}
            onSubmit={async (reason, key) => {
                if (!plan) return
                const res = await plansApi.cancel(plan.id, { expected_revision: plan.revision, reason }, key)
                onDone(res)
            }}
        />
    )
}

export const DeallocatePlanDialog = ({
    allocation,
    onClose,
    onDone,
    onReload,
}: {
    allocation: PlanAllocation | null
    onClose: () => void
    onDone: (p: Plan) => void
    onReload: () => void
}) => {
    const { t } = useTranslation()
    return (
        <ReasonDialog
            isOpen={!!allocation}
            title={t('p3.plans.deallocateTitle')}
            description={
                allocation ? (
                    <span>
                        {t('p3.plans.deallocateHint')} <MoneyText value={allocation.amount} strong />
                    </span>
                ) : undefined
            }
            tone="danger"
            confirmLabel={t('p3.plans.deallocate')}
            testId="p3-alloc-deallocate"
            describeError={describePlanError}
            onClose={onClose}
            onReload={onReload}
            onSubmit={async (reason, key) => {
                if (!allocation) return
                const res = await plansApi.deallocate(allocation.id, { reason }, key)
                onDone(res.plan)
            }}
        />
    )
}

type Opt = { value: string; label: string }

const AllocateBody = ({
    plan,
    paymentId,
    setPaymentId,
    installmentId,
    setInstallmentId,
    amount,
    setAmount,
    reason,
    setReason,
}: {
    plan: Plan
    paymentId: string | undefined
    setPaymentId: (v: string | undefined) => void
    installmentId: string | undefined
    setInstallmentId: (v: string | undefined) => void
    amount: string
    setAmount: (v: string) => void
    reason: string
    setReason: (v: string) => void
}) => {
    const { t } = useTranslation()
    const q = useV2Query((signal) => moneyApi.casePayments(plan.case_id, signal), [plan.case_id])
    const payments: Opt[] = useMemo(
        () =>
            (q.data?.items || [])
                .filter((p) => p.status === 'approved')
                .map((p) => ({
                    value: p.id,
                    label: `${p.payment_civil_date} · ${formatMoney(p.amount)} · ${p.recorder?.name || '—'} · ${shortId(p.id)}`,
                })),
        [q.data]
    )
    const installments: Opt[] = useMemo(
        () =>
            (plan.installments || [])
                .filter((i) => i.state !== 'paid' && i.state !== 'cancelled')
                .map((i) => ({
                    value: i.id,
                    label: t('p3.plans.alloc.installmentOption', {
                        seq: i.seq,
                        due: i.due_date,
                        remaining: formatMoney(i.remaining),
                    }),
                })),
        [plan.installments, t]
    )
    const trimmed = amount.trim()
    const amountInvalid = trimmed !== '' && (!MONEY_INPUT.test(trimmed) || !isPositiveAmount(trimmed))

    return (
        <>
            <PlanSummary plan={plan} />
            <Note className="mb-4">{t('p3.plans.alloc.explain')}</Note>
            {q.loading && !q.data ? (
                <SkeletonLines rows={2} />
            ) : q.error ? (
                <ErrorState error={q.error} onRetry={q.reload} describe={describePlanError} />
            ) : payments.length === 0 ? (
                <div className="mb-4 text-[13px] text-gray-700 dark:text-night-body" data-testid="p3-alloc-none">
                    {t('p3.plans.alloc.noPayments')}
                </div>
            ) : (
                <DialogField label={t('p3.plans.alloc.payment')} required hint={t('p3.plans.alloc.paymentHint')}>
                    <div data-testid="p3-alloc-payment">
                        <Select
                            size="sm"
                            options={payments}
                            value={payments.find((o) => o.value === paymentId) || null}
                            placeholder={t('p3.plans.alloc.choosePayment')}
                            onChange={(o) => setPaymentId((o as Opt | null)?.value)}
                        />
                    </div>
                </DialogField>
            )}
            <DialogField label={t('p3.plans.alloc.installment')} hint={t('p3.plans.alloc.installmentHint')}>
                <div data-testid="p3-alloc-installment">
                    <Select
                        size="sm"
                        isClearable
                        options={installments}
                        value={installments.find((o) => o.value === installmentId) || null}
                        placeholder={t('p3.plans.alloc.anyInstallment')}
                        onChange={(o) => setInstallmentId((o as Opt | null)?.value)}
                    />
                </div>
            </DialogField>
            <DialogField
                label={t('p3.plans.alloc.amount')}
                hint={
                    amountInvalid ? (
                        <span className="text-danger">{t('p3.plans.wizard.amountFormat')}</span>
                    ) : (
                        t('p3.plans.alloc.amountHint')
                    )
                }
            >
                <Input
                    dir="ltr"
                    inputMode="decimal"
                    value={amount}
                    invalid={amountInvalid}
                    onChange={(e) => setAmount(e.target.value)}
                    data-testid="p3-alloc-amount"
                />
            </DialogField>
            <DialogField label={t('p1.fields.reason')} required hint={t('p1.hint.reasonRequired')}>
                <Input
                    textArea
                    value={reason}
                    maxLength={2000}
                    onChange={(e) => setReason(e.target.value)}
                    data-testid="p3-alloc-reason"
                />
            </DialogField>
        </>
    )
}

export const AllocatePlanDialog = ({
    plan,
    onClose,
    onDone,
    onReload,
}: {
    plan: Plan | null
    onClose: () => void
    onDone: (p: Plan) => void
    onReload: () => void
}) => {
    const { t } = useTranslation()
    const [paymentId, setPaymentId] = useState<string | undefined>()
    const [installmentId, setInstallmentId] = useState<string | undefined>()
    const [amount, setAmount] = useState('')
    const [reason, setReason] = useState('')
    const trimmed = amount.trim()
    const amountOk = trimmed === '' || (MONEY_INPUT.test(trimmed) && isPositiveAmount(trimmed))
    const reset = () => {
        setPaymentId(undefined)
        setInstallmentId(undefined)
        setAmount('')
        setReason('')
    }
    return (
        <ActionDialog
            isOpen={!!plan}
            title={t('p3.plans.alloc.title')}
            confirmLabel={t('p3.plans.alloc.confirm')}
            canSubmit={!!paymentId && amountOk && reason.trim().length > 0}
            testId="p3-plan-allocate-dialog"
            describeError={describePlanError}
            onReload={onReload}
            onClose={() => {
                reset()
                onClose()
            }}
            onSubmit={async (key) => {
                if (!plan || !paymentId) return
                const res = await plansApi.allocate(
                    plan.id,
                    {
                        payment_id: paymentId,
                        reason: reason.trim(),
                        installment_id: installmentId || undefined,
                        amount: trimmed || undefined,
                    },
                    key
                )
                reset()
                onDone(res.plan)
            }}
        >
            {plan && (
                <AllocateBody
                    plan={plan}
                    paymentId={paymentId}
                    setPaymentId={setPaymentId}
                    installmentId={installmentId}
                    setInstallmentId={setInstallmentId}
                    amount={amount}
                    setAmount={setAmount}
                    reason={reason}
                    setReason={setReason}
                />
            )}
        </ActionDialog>
    )
}
