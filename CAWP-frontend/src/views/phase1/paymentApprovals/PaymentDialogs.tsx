/**
 * Approval-desk decisions. Approve (optional comment) and reject (reason) need
 * `payments_approve`; reverse (reason, approved payments only) needs
 * `payments_reverse`. Each sends the revision the row was loaded with; a 409
 * offers a reload and never retries with a fresh revision.
 */
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import Input from '@/components/ui/Input'
import ActionDialog, { DialogField, ReasonDialog } from '@/components/v2/ActionDialog'
import { CivilDate, Field, Ltr, MoneyText } from '@/components/v2/Display'
import { PaymentBadge } from '@/components/v2/Badges'
import { Note } from '@/components/v2/States'
import { moneyApi } from '@/services/v2'
import type { Balance, Payment } from '@/services/v2'
import BalanceBreakdown from '@/views/phase1/money/BalanceBreakdown'
import { ReceiptLinks } from '@/views/paymentAttachments'

export type DecisionKind = 'approve' | 'reject' | 'reverse'

export type DecisionResult = {
    kind: DecisionKind
    payment: Payment
    balance: Balance
    /** approve: auto-allocations made; reverse: allocations voided. */
    allocations?: number
    voided?: number
}

const PaymentSummary = ({ payment }: { payment: Payment }) => {
    const { t } = useTranslation()
    return (
        <div className="mb-4 grid grid-cols-2 gap-3 rounded-md border border-gray-200 p-3 sm:grid-cols-3 dark:border-night-border">
            <Field label={t('p1.col.case')} className="col-span-2 sm:col-span-3">
                <span className="font-extrabold">{payment.case?.name || '—'}</span>{' '}
                <Ltr className="text-[12.5px] text-gray-600">{payment.case?.account_number}</Ltr>
            </Field>
            <Field label={t('p1.col.amount')}>
                <MoneyText value={payment.amount} strong />
            </Field>
            <Field label={t('p1.col.paymentDate')}>
                <CivilDate value={payment.payment_civil_date} />
            </Field>
            <Field label={t('p1.col.status')}>
                <PaymentBadge status={payment.status} />
            </Field>
            <Field label={t('p1.col.recorder')}>{payment.recorder?.name || '—'}</Field>
            <Field label={t('p1.col.method')} className="sm:col-span-2">
                {[payment.collection_type?.name, payment.collection_method?.name].filter(Boolean).join(' / ') || '—'}
            </Field>
            <Field label={t('p7.payAtt.column')} className="col-span-2 sm:col-span-3">
                {payment.attachments && payment.attachments.length > 0 ? (
                    <ReceiptLinks attachments={payment.attachments} />
                ) : (
                    <span className="text-gray-600">{t('p7.payAtt.none')}</span>
                )}
            </Field>
        </div>
    )
}

export const DecisionDialogs = ({
    payment,
    kind,
    onClose,
    onDone,
    onReload,
}: {
    payment: Payment | null
    kind: DecisionKind | null
    onClose: () => void
    onDone: (r: DecisionResult) => void
    onReload: () => void
}) => {
    const { t } = useTranslation()
    const [comment, setComment] = useState('')
    const [lastError, setLastError] = useState<unknown>(null)
    const p = payment

    return (
        <>
            <ActionDialog
                isOpen={!!p && kind === 'approve'}
                title={t('p1.desk.approveTitle')}
                confirmLabel={t('p1.desk.approve')}
                testId="p1-approve-dialog"
                onReload={onReload}
                onClose={() => {
                    setComment('')
                    setLastError(null)
                    onClose()
                }}
                onSubmit={async (key) => {
                    if (!p) return
                    setLastError(null)
                    try {
                        const res = await moneyApi.approve(
                            p.id,
                            { expected_revision: p.revision, comment: comment.trim() || undefined },
                            key
                        )
                        setComment('')
                        onDone({
                            kind: 'approve',
                            payment: res.payment,
                            balance: res.balance,
                            allocations: res.allocations?.length ?? 0,
                        })
                    } catch (err) {
                        setLastError(err)
                        throw err
                    }
                }}
            >
                {p && <PaymentSummary payment={p} />}
                <DialogField label={t('p1.desk.comment')} hint={t('p1.desk.commentHint')}>
                    <Input
                        textArea
                        value={comment}
                        maxLength={2000}
                        onChange={(e) => setComment(e.target.value)}
                        data-testid="p1-approve-dialog-comment"
                    />
                </DialogField>
                <Note>{t('p1.desk.approveNote')}</Note>
                <BalanceBreakdown error={lastError} />
            </ActionDialog>

            <ReasonDialog
                isOpen={!!p && kind === 'reject'}
                title={t('p1.desk.rejectTitle')}
                description={p ? <PaymentSummary payment={p} /> : undefined}
                confirmLabel={t('p1.desk.reject')}
                tone="danger"
                testId="p1-reject-dialog"
                onClose={onClose}
                onReload={onReload}
                onSubmit={async (reason, key) => {
                    if (!p) return
                    const res = await moneyApi.reject(p.id, { expected_revision: p.revision, reason }, key)
                    onDone({ kind: 'reject', payment: res.payment, balance: res.balance })
                }}
            />

            <ReasonDialog
                isOpen={!!p && kind === 'reverse'}
                title={t('p1.desk.reverseTitle')}
                description={
                    p ? (
                        <>
                            <PaymentSummary payment={p} />
                            <div className="rounded-md border border-gray-300 bg-gray-50 px-3 py-2 dark:border-night-border dark:bg-night-raised">
                                {t('p1.desk.reverseExplain')}
                            </div>
                        </>
                    ) : undefined
                }
                confirmLabel={t('p1.desk.reverse')}
                tone="danger"
                testId="p1-reverse-dialog"
                onClose={onClose}
                onReload={onReload}
                onSubmit={async (reason, key) => {
                    if (!p) return
                    const res = await moneyApi.reverse(p.id, { expected_revision: p.revision, reason }, key)
                    onDone({
                        kind: 'reverse',
                        payment: res.payment,
                        balance: res.balance,
                        voided: res.voided_allocations,
                    })
                }}
            />
        </>
    )
}
