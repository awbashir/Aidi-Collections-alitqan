/**
 * Form pieces shared by the wrap-up dialog and the manual call log: the outcome picker,
 * the note (text, reachability, status, contact method: the legacy note form's rules),
 * the optional callback, and the optional EXPLICITLY AGREED promise (outcome `connected`
 * only, with the promises key). "Promise discussed" never creates a promise.
 */
import classNames from 'classnames'
import { useEffect, useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import Checkbox from '@/components/ui/Checkbox'
import Input from '@/components/ui/Input'
import Select from '@/components/ui/Select'
import { DialogField } from '@/components/v2/ActionDialog'
import { Note } from '@/components/v2/States'
import type { Option, Outcome } from '@/services/v2'
import { OUTCOMES } from '@/services/v2'
import { riyadhToday } from '@/utils/v2/format'
import { Notice } from '../parts'
import { MOBILE_METHOD_ID, useNoteLookups } from './draft'
import type { CallbackDraft, NoteDraft, PromiseDraft } from './draft'

const selectProps = { size: 'sm' as const, menuPosition: 'fixed' as const, isClearable: true }

/* ------------------------------------------------------------------ outcome */

export const OutcomePicker = ({
    value,
    onChange,
    allowed,
    testId = 'p5-outcome',
}: {
    value: Outcome | undefined
    onChange: (o: Outcome) => void
    /** Outcomes the API allows now (`actions.wrapup_outcomes`); undefined = all (manual log). */
    allowed?: Outcome[]
    testId?: string
}) => {
    const { t } = useTranslation()
    return (
        <div role="radiogroup" aria-label={String(t('p5.wrapup.outcome'))} className="grid grid-cols-1 gap-2 sm:grid-cols-2" data-testid={testId}>
            {OUTCOMES.map((o) => {
                const enabled = !allowed || allowed.includes(o)
                const active = value === o
                return (
                    <button
                        key={o}
                        type="button"
                        role="radio"
                        aria-checked={active}
                        aria-disabled={!enabled}
                        disabled={!enabled}
                        data-outcome={o}
                        data-enabled={enabled ? '1' : '0'}
                        onClick={() => enabled && onChange(o)}
                        className={classNames(
                            'flex min-h-[44px] flex-col items-start rounded-md border px-3 py-2 text-start focus:outline-none focus-visible:ring-2 focus-visible:ring-azure-500',
                            active
                                ? 'border-azure-500 bg-azure-50 dark:bg-night-raised'
                                : enabled
                                ? 'border-gray-300 bg-white hover:bg-gray-50 dark:border-night-border dark:bg-night-card'
                                : 'cursor-not-allowed border-gray-200 bg-gray-50 text-gray-500 dark:border-night-border dark:bg-night-page'
                        )}
                    >
                        <span className={classNames('text-[13.5px]', active ? 'font-extrabold text-navy-600 dark:text-gray-100' : 'text-gray-800 dark:text-night-body', !enabled && 'text-gray-500')}>
                            {t(`p5.outcome.${o}`)}
                        </span>
                        <span className="text-[11.5px] text-gray-600">
                            {enabled ? t(`p5.outcomeHint.${o}`) : t('p5.wrapup.needsBridge')}
                        </span>
                    </button>
                )
            })}
        </div>
    )
}

/* ------------------------------------------------------------------ note */

export const NoteFields = ({
    value,
    onChange,
    contractId,
    active,
    testId = 'p5-note',
    textHint,
    activeOnly,
    optional,
}: {
    value: NoteDraft
    onChange: (v: NoteDraft) => void
    /** The case's contract: note statuses are filtered like the legacy note form. */
    contractId: string | null
    active: boolean
    testId?: string
    /** Phase 6: the hint under the text (default: the wrap-up's "linked to this call"). */
    textHint?: React.ReactNode
    /** Phase 6: offer only statuses whose legacy `is_active` is true (AI save-as-note refuses the others). */
    activeOnly?: boolean
    /** Wrap-up / manual log: text, reachability and status are optional (a status writes the case note). */
    optional?: boolean
}) => {
    const { t } = useTranslation()
    const lk = useNoteLookups(active)
    useEffect(() => {
        if (active && !value.method && lk.methods.some((m) => m.value === MOBILE_METHOD_ID)) onChange({ ...value, method: MOBILE_METHOD_ID })
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [lk.methods, active])
    const statusOptions = useMemo(
        () =>
            lk.statuses.filter(
                (s) =>
                    (contractId ? s.contract_id === contractId || s.contract_id === null : true) &&
                    s.reachability_id === (value.reach ?? null) &&
                    (!activeOnly || s.is_active === true)
            ),
        [lk.statuses, contractId, value.reach, activeOnly]
    )
    useEffect(() => {
        if (value.status && !lk.loading && !statusOptions.some((s) => s.value === value.status)) onChange({ ...value, status: undefined })
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [statusOptions])
    return (
        <div data-testid={testId}>
            <DialogField label={t('p5.wrapup.noteText')} required={!optional} hint={textHint ?? t('p5.wrapup.noteHint')}>
                <Input
                    textArea
                    value={value.text}
                    maxLength={5000}
                    onChange={(e) => onChange({ ...value, text: e.target.value })}
                    data-testid={`${testId}-text`}
                />
            </DialogField>
            <div className="grid grid-cols-1 gap-x-3 sm:grid-cols-3">
                <DialogField label={t('p1.followUps.complete.reachability')}>
                    <div data-testid={`${testId}-reach`}>
                        <Select
                            {...selectProps}
                            placeholder={t('p1.followUps.select')}
                            isLoading={lk.loading}
                            options={lk.reachability}
                            value={lk.reachability.find((o) => o.value === value.reach) || null}
                            onChange={(o) => onChange({ ...value, reach: (o as Option | null)?.value || undefined })}
                        />
                    </div>
                </DialogField>
                <DialogField
                    label={t('p1.followUps.complete.status')}
                    required={!optional}
                    hint={optional ? t('p5.wrapup.statusOptionalHint') : !value.reach ? t('p1.followUps.complete.statusHint') : undefined}
                >
                    <div data-testid={`${testId}-status`}>
                        <Select
                            {...selectProps}
                            placeholder={t('p1.followUps.select')}
                            isLoading={lk.loading}
                            options={statusOptions}
                            noOptionsMessage={() => t('p1.followUps.complete.noStatuses')}
                            value={statusOptions.find((o) => o.value === value.status) || null}
                            onChange={(o) => onChange({ ...value, status: (o as Option | null)?.value || undefined })}
                        />
                    </div>
                </DialogField>
                <DialogField label={t('p1.followUps.complete.method')} required={!optional || !!value.status}>
                    <div data-testid={`${testId}-method`}>
                        <Select
                            {...selectProps}
                            placeholder={t('p1.followUps.select')}
                            isLoading={lk.loading}
                            options={lk.methods}
                            value={lk.methods.find((o) => o.value === value.method) || null}
                            onChange={(o) => onChange({ ...value, method: (o as Option | null)?.value || undefined })}
                        />
                    </div>
                </DialogField>
            </div>
            {lk.failed && <Note className="-mt-2 mb-3 text-danger">{t('p1.followUps.lookupsFailed')}</Note>}
        </div>
    )
}

/* ------------------------------------------------------------------ callback */

export const CallbackFields = ({ value, onChange, testId = 'p5-callback' }: { value: CallbackDraft; onChange: (v: CallbackDraft) => void; testId?: string }) => {
    const { t } = useTranslation()
    return (
        <div className="grid grid-cols-1 gap-x-3 sm:grid-cols-2" data-testid={testId}>
            <DialogField label={t('p5.wrapup.callbackDate')} hint={t('p5.wrapup.callbackHint')}>
                <Input
                    type="date"
                    size="sm"
                    min={riyadhToday()}
                    value={value.date}
                    onChange={(e) => onChange({ ...value, date: e.target.value })}
                    data-testid={`${testId}-date`}
                />
            </DialogField>
            <DialogField label={t('p5.wrapup.callbackTime')}>
                <Input type="time" size="sm" value={value.time} onChange={(e) => onChange({ ...value, time: e.target.value })} data-testid={`${testId}-time`} />
            </DialogField>
        </div>
    )
}

/* ------------------------------------------------------------------ promise */

export const PromiseFields = ({
    outcome,
    value,
    onChange,
    canPromise,
    testId = 'p5-promise',
}: {
    outcome: Outcome | undefined
    value: PromiseDraft
    onChange: (v: PromiseDraft) => void
    /** The caller holds `promises` or `clints_PayemntDates`. */
    canPromise: boolean
    testId?: string
}) => {
    const { t } = useTranslation()
    if (outcome === 'promise_discussed') {
        return (
            <Notice tone="info" testId={`${testId}-discussed`}>
                {t('p5.wrapup.discussedNotPromise')}
            </Notice>
        )
    }
    if (outcome !== 'connected') return null
    if (!canPromise) return <Note className="mb-3">{t('p5.wrapup.promiseNoKey')}</Note>
    return (
        <div className="mb-3 rounded-md border border-gray-300 p-3 dark:border-night-border" data-testid={testId}>
            <Checkbox checked={value.agreed} onChange={(c: boolean) => onChange({ ...value, agreed: c })} data-testid={`${testId}-agreed`}>
                <span className="text-[13.5px] font-extrabold text-navy-600 dark:text-gray-100">{t('p5.wrapup.promiseAgreed')}</span>
            </Checkbox>
            <div className="mt-1 text-[12px] text-gray-600">{t('p5.wrapup.promiseAgreedHint')}</div>
            {value.agreed && (
                <div className="mt-3 grid grid-cols-1 gap-x-3 sm:grid-cols-2">
                    <DialogField label={t('p5.wrapup.promiseAmount')} required hint={t('p5.wrapup.promiseAmountHint')}>
                        <Input
                            size="sm"
                            dir="ltr"
                            inputMode="decimal"
                            value={value.amount}
                            onChange={(e) => onChange({ ...value, amount: e.target.value })}
                            data-testid={`${testId}-amount`}
                        />
                    </DialogField>
                    <DialogField label={t('p5.wrapup.promiseDue')} required>
                        <Input
                            type="date"
                            size="sm"
                            min={riyadhToday()}
                            value={value.due}
                            onChange={(e) => onChange({ ...value, due: e.target.value })}
                            data-testid={`${testId}-due`}
                        />
                    </DialogField>
                </div>
            )}
        </div>
    )
}

/** The extra fields of the blocking outcomes: wrong number (the contact) and do-not-call (the case). */
export const BlockingOutcomeFields = ({
    outcome,
    wrongReason,
    dncReason,
    onWrongReason,
    onDncReason,
}: {
    outcome: Outcome | undefined
    wrongReason: string
    dncReason: string
    onWrongReason: (v: string) => void
    onDncReason: (v: string) => void
}) => {
    const { t } = useTranslation()
    if (outcome === 'wrong_number') {
        return (
            <>
                <Notice tone="warning" className="mb-3" testId="p5-wrong-number-note">
                    {t('p5.wrapup.wrongNumberNote')}
                </Notice>
                <DialogField label={t('p5.wrapup.wrongNumberReason')}>
                    <Input value={wrongReason} maxLength={500} onChange={(e) => onWrongReason(e.target.value)} data-testid="p5-wrong-number-reason" />
                </DialogField>
            </>
        )
    }
    if (outcome === 'do_not_call') {
        return (
            <>
                <Notice tone="warning" className="mb-3" testId="p5-dnc-note">
                    {t('p5.wrapup.dncNote')}
                </Notice>
                <DialogField label={t('p5.wrapup.dncReason')}>
                    <Input value={dncReason} maxLength={500} onChange={(e) => onDncReason(e.target.value)} data-testid="p5-dnc-reason" />
                </DialogField>
            </>
        )
    }
    return null
}
