/**
 * "وعد السداد (اختياري)" — the promise a promise-to-pay note offers (migration 0079): amount and
 * due date (a Riyadh date, not before today), with the case's outstanding balance as a hint.
 * Customer feedback 2026-10-21: OPTIONAL — left empty, the note is saved without a promise;
 * half-filled, the missing field is asked for. No due time (a promise is a civil date).
 * When the case already has an open (or overdue) promise and the user may read promises, the
 * form offers "link the existing promise" instead of a new one (also optional). Shared by the
 * wrap-up / call result, the manual call log, the legacy note form and the follow-up completion.
 */
import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import Input from '@/components/ui/Input'
import Select from '@/components/ui/Select'
import { DialogField } from '@/components/v2/ActionDialog'
import { moneyApi } from '@/services/v2'
import type { Balance, Option, PromiseRow } from '@/services/v2'
import { formatMoney, riyadhToday } from '@/utils/v2/format'
import { usePermissions } from '@/utils/v2/hooks'
import { Segmented } from '@/views/phase1/followUps/parts'
import { promiseDraftEmpty, requiredPromiseErrors } from './requiredPromise'
import type { RequiredPromiseDraft } from './requiredPromise'

/** The case's outstanding balance and open promises (each read may fail: then no hint / no link). */
export function useCasePromiseFacts(caseId: string | null | undefined, active: boolean) {
    const perms = usePermissions()
    const canReadPromises = !!(perms.promises || perms.clints_PayemntDates)
    const [balance, setBalance] = useState<Balance | null>(null)
    const [open, setOpen] = useState<PromiseRow[]>([])
    useEffect(() => {
        if (!caseId || !active) return undefined
        const ctl = new AbortController()
        setBalance(null)
        setOpen([])
        moneyApi
            .balance(caseId, ctl.signal)
            .then((b) => !ctl.signal.aborted && setBalance(b))
            .catch(() => undefined)
        if (canReadPromises) {
            moneyApi
                .casePromises(caseId, ctl.signal)
                .then((d) => !ctl.signal.aborted && setOpen(d.items.filter((p) => p.state === 'open' || p.state === 'overdue')))
                .catch(() => undefined)
        }
        return () => ctl.abort()
    }, [caseId, active, canReadPromises])
    return { outstanding: balance?.outstanding ?? null, open }
}

export const RequiredPromiseFields = ({
    caseId,
    value,
    onChange,
    reason,
    active = true,
    showErrors = false,
    onOutstanding,
    testId = 'p10-promise',
}: {
    caseId: string | null | undefined
    value: RequiredPromiseDraft
    onChange: (v: RequiredPromiseDraft) => void
    /** Why it is offered: the note status, or the outcome "promise discussed". */
    reason: 'status' | 'outcome'
    active?: boolean
    /** Show every missing field (after a save attempt); otherwise only what was typed wrong. */
    showErrors?: boolean
    /** The outstanding balance once read (the parent's validation uses it). */
    onOutstanding?: (outstanding: string | null) => void
    testId?: string
}) => {
    const { t } = useTranslation()
    const { outstanding, open } = useCasePromiseFacts(caseId, active)
    useEffect(() => onOutstanding?.(outstanding), [outstanding, onOutstanding])
    const today = riyadhToday()
    const errors = requiredPromiseErrors(value, today, outstanding)
    /* optional: nothing is asked for until one of the two fields is filled in (an empty draft
       has no errors, so `showErrors` only matters once the promise was started) */
    const started = !promiseDraftEmpty(value)
    const shown = (field: 'amount' | 'due' | 'link', typed: boolean) => (showErrors || typed ? errors[field] : undefined)
    const linkOptions = useMemo<Option[]>(
        () =>
            open.map((p) => ({
                value: p.id,
                label: `${formatMoney(p.amount)} · ${p.due_date} · ${t(`p10.promise.state.${p.state}`)}`,
            })),
        [open, t]
    )
    /* An open promise that disappeared (cancelled meanwhile): back to a new promise. */
    useEffect(() => {
        if (value.mode === 'link' && open.length === 0) onChange({ ...value, mode: 'new', promiseId: undefined })
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [open.length])
    const err = (key?: string) =>
        key ? (
            <span className="mt-1 block text-[12px] text-danger" role="alert">
                {t(`p10.promise.${key}`, { today, outstanding: formatMoney(outstanding) })}
            </span>
        ) : null

    return (
        <div
            className="mb-3 rounded-md border border-azure-100 bg-info-tint p-3 dark:border-night-border dark:bg-night-raised"
            data-testid={testId}
            data-mode={value.mode}
        >
            <div className="text-[13.5px] font-extrabold text-navy-600 dark:text-gray-100">
                {t('p11.notes.promise.title')}
            </div>
            <div className="mb-3 mt-0.5 text-[12px] text-gray-600">
                {t(reason === 'outcome' ? 'p11.notes.promise.whyOutcome' : 'p11.notes.promise.whyStatus')}
            </div>
            {open.length > 0 && (
                <div className="mb-3">
                    <Segmented<'new' | 'link'>
                        value={value.mode}
                        onChange={(mode) => onChange({ ...value, mode, promiseId: mode === 'link' ? value.promiseId ?? open[0]?.id : undefined })}
                        label={String(t('p11.notes.promise.title'))}
                        testId={`${testId}-mode`}
                        options={[
                            { value: 'new', label: t('p10.promise.modeNew') },
                            { value: 'link', label: t('p10.promise.modeLink', { count: open.length }) },
                        ]}
                    />
                </div>
            )}
            {value.mode === 'link' ? (
                <DialogField label={t('p10.promise.linkLabel')} hint={t('p10.promise.linkHint')}>
                    <div data-testid={`${testId}-link`}>
                        <Select
                            size="sm"
                            menuPosition="fixed"
                            isClearable
                            options={linkOptions}
                            value={linkOptions.find((o) => o.value === value.promiseId) || null}
                            onChange={(o) => onChange({ ...value, promiseId: (o as Option | null)?.value || undefined })}
                        />
                    </div>
                    {err(shown('link', false))}
                </DialogField>
            ) : (
                <div className="grid grid-cols-1 gap-x-3 sm:grid-cols-2">
                    <DialogField
                        label={t('p10.promise.amount')}
                        required={started}
                        hint={outstanding !== null ? t('p10.promise.balanceHint', { outstanding: formatMoney(outstanding) }) : undefined}
                    >
                        <Input
                            size="sm"
                            dir="ltr"
                            inputMode="decimal"
                            value={value.amount}
                            invalid={!!shown('amount', started)}
                            onChange={(e) => onChange({ ...value, amount: e.target.value })}
                            data-testid={`${testId}-amount`}
                        />
                        {err(shown('amount', started))}
                    </DialogField>
                    <DialogField label={t('p10.promise.due')} required={started} hint={t('p10.promise.dueHint')}>
                        <Input
                            type="date"
                            size="sm"
                            min={today}
                            value={value.due}
                            invalid={!!shown('due', started)}
                            onChange={(e) => onChange({ ...value, due: e.target.value })}
                            data-testid={`${testId}-due`}
                        />
                        {err(shown('due', started))}
                    </DialogField>
                </div>
            )}
        </div>
    )
}

export default RequiredPromiseFields
