/**
 * Create-a-plan wizard for one case.
 *
 * Step 1 is the form. "Preview schedule" asks the API
 * (POST /v2/cases/<id>/plans/preview, writes nothing) for the exact schedule;
 * no installment, date or amount is computed here. Step 2 shows the preview as
 * returned and "Create plan" posts the same body with one Idempotency-Key per
 * wizard opening. Errors stay inline with the draft kept.
 */
import { useState } from 'react'
import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import Button from '@/components/ui/Button'
import Dialog from '@/components/ui/Dialog'
import Input from '@/components/ui/Input'
import { DialogField } from '@/components/v2/ActionDialog'
import { CivilDate, Field, Ltr, MoneyText } from '@/components/v2/Display'
import { InlineError, Note } from '@/components/v2/States'
import { plansApi } from '@/services/v2'
import type { Plan, PlanDraft, PlanFrequency, PlanPreview } from '@/services/v2'
import { MONEY_INPUT, riyadhToday } from '@/utils/v2/format'
import { useActionKey } from '@/utils/v2/hooks'
import { ChoiceButtons, DateRange } from './parts'
import { describePlanError, isPositiveAmount } from './planUtils'

type Mode = 'count' | 'amount'

const COUNT_INPUT = /^\d{1,3}$/

const GroupLabel = ({ children }: { children: ReactNode }) => (
    <span className="mb-[7px] block text-[12.5px] font-extrabold text-navy-600 dark:text-gray-100">{children}</span>
)

export type PlanWizardProps = {
    caseId: string
    isOpen: boolean
    /** Outstanding balance from GET /v2/cases/<id>/plans, shown as a hint only. */
    outstanding?: string
    onClose: () => void
    onCreated: (plan: Plan) => void
    /** After a conflict (e.g. a plan was created meanwhile): reload the case plans. */
    onReload: () => void
}

const PlanWizard = ({ caseId, isOpen, outstanding, onClose, onCreated, onReload }: PlanWizardProps) => {
    const { t } = useTranslation()
    const { key, renew } = useActionKey(isOpen)
    /* The body the current key was already sent with. A changed schedule after a
     * failed create is a new action: it gets a new key (the server would refuse
     * the old key with another body). */
    const [sentBody, setSentBody] = useState<string | null>(null)
    const today = riyadhToday()

    const [step, setStep] = useState<'form' | 'preview'>('form')
    const [total, setTotal] = useState('')
    const [frequency, setFrequency] = useState<PlanFrequency>('monthly')
    const [start, setStart] = useState('')
    const [mode, setMode] = useState<Mode>('count')
    const [count, setCount] = useState('')
    const [amount, setAmount] = useState('')
    const [note, setNote] = useState('')
    const [preview, setPreview] = useState<{ body: PlanDraft; data: PlanPreview } | null>(null)
    const [error, setError] = useState<unknown>(null)
    const [busy, setBusy] = useState(false)

    const reset = () => {
        setStep('form')
        setTotal('')
        setFrequency('monthly')
        setStart('')
        setMode('count')
        setCount('')
        setAmount('')
        setNote('')
        setPreview(null)
        setError(null)
        setBusy(false)
        setSentBody(null)
    }

    const close = () => {
        if (busy) return
        reset()
        onClose()
    }

    const tt = total.trim()
    const ct = count.trim()
    const at = amount.trim()
    const totalErr = tt !== '' && !(MONEY_INPUT.test(tt) && isPositiveAmount(tt))
    const countNum = COUNT_INPUT.test(ct) ? Number(ct) : NaN
    const countErr = ct !== '' && !(countNum >= 1 && countNum <= 240)
    const amountErr = at !== '' && !(MONEY_INPUT.test(at) && isPositiveAmount(at))
    const formOk =
        tt !== '' &&
        !totalErr &&
        !!start &&
        (mode === 'count' ? ct !== '' && !countErr : at !== '' && !amountErr)

    const body = (): PlanDraft => ({
        total_amount: tt,
        frequency,
        start_date: start,
        ...(mode === 'count' ? { installment_count: countNum } : { installment_amount: at }),
        ...(note.trim() ? { note: note.trim() } : {}),
    })

    const runPreview = async () => {
        if (!formOk || busy) return
        const b = body()
        setBusy(true)
        setError(null)
        try {
            const data = await plansApi.preview(caseId, b)
            if (sentBody !== null && sentBody !== JSON.stringify(b)) {
                renew()
                setSentBody(null)
            }
            setPreview({ body: b, data })
            setStep('preview')
        } catch (err) {
            setError(err)
        } finally {
            setBusy(false)
        }
    }

    const create = async () => {
        if (!preview || busy) return
        setBusy(true)
        setError(null)
        setSentBody(JSON.stringify(preview.body))
        try {
            const plan = await plansApi.create(caseId, preview.body, key)
            setBusy(false)
            reset()
            onClose()
            onCreated(plan)
        } catch (err) {
            setBusy(false)
            setError(err)
        }
    }

    const reloadAfterConflict = () => {
        reset()
        onClose()
        onReload()
    }

    const pv = preview?.data

    return (
        <Dialog isOpen={isOpen} width={680} onClose={close} onRequestClose={close} shouldCloseOnOverlayClick={!busy}>
            <div data-testid="p3-plan-wizard" data-step={step} className="flex max-h-[80vh] flex-col">
                <h5 className="mb-1 pe-8 text-[17px] font-extrabold text-navy-600 dark:text-gray-100">
                    {t('p3.plans.wizard.title')}
                </h5>
                <div className="mb-4 text-[12.5px] text-gray-600">
                    {step === 'form' ? t('p3.plans.wizard.step1') : t('p3.plans.wizard.step2')}
                </div>

                <div className="min-h-0 flex-1 overflow-y-auto pe-1">
                    {step === 'form' ? (
                        <>
                            <DialogField
                                label={t('p3.plans.wizard.total')}
                                required
                                hint={
                                    totalErr ? (
                                        <span className="text-danger">{t('p3.plans.wizard.amountFormat')}</span>
                                    ) : outstanding !== undefined ? (
                                        <span>
                                            {t('p3.plans.wizard.totalHint')} <MoneyText value={outstanding} strong />
                                        </span>
                                    ) : undefined
                                }
                            >
                                <Input
                                    dir="ltr"
                                    inputMode="decimal"
                                    value={total}
                                    invalid={totalErr}
                                    onChange={(e) => setTotal(e.target.value)}
                                    data-testid="p3-plan-total"
                                />
                            </DialogField>

                            <div className="mb-4">
                                <GroupLabel>
                                    {t('p3.plans.wizard.frequency')}
                                    <span className="text-danger"> *</span>
                                </GroupLabel>
                                <ChoiceButtons<PlanFrequency>
                                    value={frequency}
                                    label={String(t('p3.plans.wizard.frequency'))}
                                    onChange={setFrequency}
                                    options={[
                                        { value: 'weekly', label: t('p3.plans.freq.weekly'), testId: 'p3-plan-freq-weekly' },
                                        { value: 'monthly', label: t('p3.plans.freq.monthly'), testId: 'p3-plan-freq-monthly' },
                                    ]}
                                />
                            </div>

                            <DialogField
                                label={t('p3.plans.wizard.start')}
                                required
                                hint={t('p3.plans.wizard.startHint', { today })}
                            >
                                <Input
                                    type="date"
                                    dir="ltr"
                                    min={today}
                                    value={start}
                                    onChange={(e) => setStart(e.target.value)}
                                    data-testid="p3-plan-start"
                                />
                            </DialogField>

                            <div className="mb-4">
                                <GroupLabel>
                                    {t('p3.plans.wizard.splitBy')}
                                    <span className="text-danger"> *</span>
                                </GroupLabel>
                                <ChoiceButtons<Mode>
                                    value={mode}
                                    label={String(t('p3.plans.wizard.splitBy'))}
                                    onChange={setMode}
                                    options={[
                                        { value: 'count', label: t('p3.plans.wizard.modeCount'), testId: 'p3-plan-mode-count' },
                                        { value: 'amount', label: t('p3.plans.wizard.modeAmount'), testId: 'p3-plan-mode-amount' },
                                    ]}
                                />
                            </div>

                            {mode === 'count' ? (
                                <DialogField
                                    label={t('p3.plans.wizard.count')}
                                    required
                                    hint={
                                        countErr ? (
                                            <span className="text-danger">{t('p3.plans.wizard.countFormat')}</span>
                                        ) : (
                                            t('p3.plans.wizard.countHint')
                                        )
                                    }
                                >
                                    <Input
                                        dir="ltr"
                                        inputMode="numeric"
                                        value={count}
                                        invalid={countErr}
                                        onChange={(e) => setCount(e.target.value)}
                                        data-testid="p3-plan-count"
                                    />
                                </DialogField>
                            ) : (
                                <DialogField
                                    label={t('p3.plans.wizard.amount')}
                                    required
                                    hint={
                                        amountErr ? (
                                            <span className="text-danger">{t('p3.plans.wizard.amountFormat')}</span>
                                        ) : (
                                            t('p3.plans.wizard.amountHint')
                                        )
                                    }
                                >
                                    <Input
                                        dir="ltr"
                                        inputMode="decimal"
                                        value={amount}
                                        invalid={amountErr}
                                        onChange={(e) => setAmount(e.target.value)}
                                        data-testid="p3-plan-amount"
                                    />
                                </DialogField>
                            )}

                            <DialogField label={t('p3.plans.wizard.note')}>
                                <Input
                                    textArea
                                    value={note}
                                    maxLength={2000}
                                    onChange={(e) => setNote(e.target.value)}
                                    data-testid="p3-plan-note"
                                />
                            </DialogField>
                            <Note>{t('p3.plans.wizard.formNote')}</Note>
                            <div data-testid="p3-plan-preview-error">
                                <InlineError error={error} describe={describePlanError} onReload={reloadAfterConflict} />
                            </div>
                        </>
                    ) : pv ? (
                        <>
                            <div className="mb-4 grid grid-cols-2 gap-3 rounded-md border border-gray-200 p-3 sm:grid-cols-5 dark:border-night-border">
                                <Field label={t('p3.plans.col.total')}>
                                    <MoneyText value={pv.total_amount} strong />
                                </Field>
                                <Field label={t('p3.plans.col.frequency')}>{t(`p3.plans.freq.${pv.frequency}`)}</Field>
                                <Field label={t('p3.plans.col.startEnd')} className="sm:col-span-2">
                                    <span className="whitespace-nowrap" data-testid="p3-plan-preview-end" data-end={pv.end_date}>
                                        <DateRange start={pv.start_date} end={pv.end_date} />
                                    </span>
                                </Field>
                                <Field label={t('p3.plans.col.installmentCount')}>
                                    <Ltr>{pv.installment_count}</Ltr>
                                </Field>
                            </div>

                            <div className="mb-3 overflow-x-auto rounded-lg border border-gray-300 dark:border-night-border">
                                <table className="w-full text-[13px] tabular-nums" data-testid="p3-plan-preview">
                                    <thead className="bg-gray-50 dark:bg-night-page">
                                        <tr>
                                            <th className="px-3 py-2 text-start text-[12px] font-extrabold text-gray-600">#</th>
                                            <th className="px-3 py-2 text-start text-[12px] font-extrabold text-gray-600">
                                                {t('p3.plans.col.dueDate')}
                                            </th>
                                            <th className="px-3 py-2 text-end text-[12px] font-extrabold text-gray-600">
                                                {t('p3.plans.col.amount')}
                                            </th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {pv.installments.map((i) => (
                                            <tr
                                                key={i.seq}
                                                className="h-11 border-t border-gray-200 dark:border-night-border"
                                                data-testid="p3-plan-preview-row"
                                                data-seq={i.seq}
                                                data-due={i.due_date}
                                                data-amount={i.amount}
                                            >
                                                <td className="px-3 py-2">
                                                    <Ltr className="font-extrabold">{i.seq}</Ltr>
                                                </td>
                                                <td className="px-3 py-2">
                                                    <CivilDate value={i.due_date} />
                                                </td>
                                                <td className="px-3 py-2 text-end">
                                                    <MoneyText value={i.amount} strong />
                                                </td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>

                            <ul className="mb-3 list-disc space-y-1 ps-5 text-[13px] text-gray-700 dark:text-night-body" data-testid="p3-plan-rules">
                                <li data-rule={pv.rules.split}>
                                    {t(`p3.plans.rules.split.${pv.rules.split}`, { defaultValue: pv.rules.split })}
                                </li>
                                <li data-rule={pv.rules.due_dates}>
                                    {t(`p3.plans.rules.dueDates.${pv.rules.due_dates}`, { defaultValue: pv.rules.due_dates })}
                                </li>
                            </ul>

                            <div
                                className="mb-3 flex flex-wrap gap-x-4 gap-y-1 rounded-md border border-gray-200 px-3 py-2 text-[13px] dark:border-night-border"
                                data-testid="p3-plan-preview-balance"
                            >
                                <span>
                                    <span className="text-gray-600">{t('p3.plans.balance.original')}</span>{' '}
                                    <MoneyText value={pv.balance.original} />
                                </span>
                                <span>
                                    <span className="text-gray-600">{t('p3.plans.balance.approved')}</span>{' '}
                                    <MoneyText value={pv.balance.approved} />
                                </span>
                                <span>
                                    <span className="text-gray-600">{t('p3.plans.balance.outstanding')}</span>{' '}
                                    <MoneyText value={pv.balance.outstanding} strong />
                                </span>
                            </div>
                            {preview?.body.note && (
                                <div className="mb-3 text-[13px]">
                                    <span className="text-gray-600">{t('p3.plans.col.note')}:</span>{' '}
                                    <span dir="auto">{preview.body.note}</span>
                                </div>
                            )}
                            <Note>{t('p3.plans.wizard.previewNote')}</Note>
                            <div data-testid="p3-plan-create-error">
                                <InlineError error={error} describe={describePlanError} onReload={reloadAfterConflict} />
                            </div>
                        </>
                    ) : null}
                </div>

                <div className="mt-5 flex items-center justify-between gap-2">
                    <div>
                        {step === 'preview' && (
                            <Button
                                size="sm"
                                type="button"
                                disabled={busy}
                                onClick={() => {
                                    setError(null)
                                    setStep('form')
                                }}
                                data-testid="p3-plan-back"
                            >
                                {t('p3.plans.wizard.back')}
                            </Button>
                        )}
                    </div>
                    <div className="flex items-center gap-2">
                        <Button size="sm" type="button" onClick={close} disabled={busy}>
                            {t('cancel')}
                        </Button>
                        {step === 'form' ? (
                            <Button
                                size="sm"
                                type="button"
                                variant="solid"
                                loading={busy}
                                disabled={!formOk}
                                onClick={runPreview}
                                data-testid="p3-plan-next"
                            >
                                {t('p3.plans.wizard.preview')}
                            </Button>
                        ) : (
                            <Button
                                size="sm"
                                type="button"
                                variant="solid"
                                loading={busy}
                                onClick={create}
                                data-testid="p3-plan-confirm"
                            >
                                {t('p3.plans.wizard.create')}
                            </Button>
                        )}
                    </div>
                </div>
            </div>
        </Dialog>
    )
}

export default PlanWizard
