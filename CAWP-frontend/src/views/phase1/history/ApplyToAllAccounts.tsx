/**
 * "تطبيق على جميع حسابات العميل (n)" — one note for every account of the same debtor (customer
 * feedback 2026-10-21, migration 0081). Shown in every note / result form (legacy note form,
 * call result and wrap-up, manual call log, follow-up completion, AI save-as-note) only when
 * the case has other ACTIVE accounts (not archived, not locked) with the same identity number
 * (GET /v2/cases/<id>/note-siblings), whatever their owner. Checked by default; the form sends
 * `apply_to_all_accounts` and the API writes the same note to each of them in the same
 * transaction. The promise and the follow-up stay on the current account only. A read that
 * fails hides the option (the note is then saved on this account only).
 */
import { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import Checkbox from '@/components/ui/Checkbox'
import ToastNotification from '@/components/shared/myComponents/ToastNotification'
import { noteSiblingsApi } from '@/services/v2/noteSiblingsApi'
import type { NoteCopy, NoteCopySkipped, NoteSiblings } from '@/services/v2/noteSiblingsApi'
import type { Translate } from '@/services/v2'

const accountsText = (rows: Array<{ account_number: string | null }>) =>
    rows.map((r) => r.account_number || '—').join('، ')

/** The siblings of `caseId` and the checkbox state (default on). `flag()` is what to send. */
export function useApplyToAll(caseId: string | null | undefined, active = true) {
    const [siblings, setSiblings] = useState<NoteSiblings | null>(null)
    const [checked, setChecked] = useState(true)
    const [tick, setTick] = useState(0)
    useEffect(() => {
        setSiblings(null)
        setChecked(true)
        if (!caseId || !active) return undefined
        const ctl = new AbortController()
        noteSiblingsApi
            .get(caseId, ctl.signal)
            .then((d) => !ctl.signal.aborted && setSiblings(d))
            .catch(() => undefined)
        return () => ctl.abort()
    }, [caseId, active, tick])
    const offered = !!siblings && siblings.count > 0 && !siblings.too_many
    /** undefined = nothing to send (no siblings): the request stays as it was before 0081 */
    const flag = (): boolean | undefined => (offered ? checked : undefined)
    const reset = useCallback(() => {
        setChecked(true)
        setTick((n) => n + 1) // an account may have been archived or locked meanwhile
    }, [])
    return { siblings, offered, checked, setChecked, flag, reset }
}

export type ApplyToAllState = ReturnType<typeof useApplyToAll>

export const ApplyToAllAccounts = ({
    state,
    testId = 'p11-apply-all',
    className = 'mb-3',
}: {
    state: ApplyToAllState
    testId?: string
    className?: string
}) => {
    const { t } = useTranslation()
    const s = state.siblings
    if (!s || s.count === 0) return null
    if (s.too_many) {
        return (
            <div className={`${className} text-[12px] text-gray-600`} data-testid={`${testId}-too-many`}>
                {t('p11.notes.applyAll.tooMany', { count: s.count, limit: s.limit })}
            </div>
        )
    }
    return (
        <div
            className={`${className} rounded-md border border-gray-300 px-3 py-2 dark:border-night-border`}
            data-testid={testId}
            data-count={s.count}
            data-checked={state.checked ? '1' : '0'}
        >
            <Checkbox checked={state.checked} onChange={(c: boolean) => state.setChecked(c)} data-testid={`${testId}-check`}>
                <span className="text-[13.5px] font-extrabold text-navy-600 dark:text-gray-100">
                    {t('p11.notes.applyAll.label', { count: s.count })}
                </span>
            </Checkbox>
            <div className="mt-1 text-[12px] text-gray-600">
                {t(state.checked ? 'p11.notes.applyAll.hintOn' : 'p11.notes.applyAll.hintOff', {
                    accounts: accountsText(s.accounts),
                })}
            </div>
        </div>
    )
}

/** The success message's second line: the accounts the note was also written to (and skipped). */
export function appliedToText(
    t: Translate,
    applied?: NoteCopy[] | null,
    skipped?: Array<Pick<NoteCopySkipped, 'account_number'>> | null
): string | null {
    const parts: string[] = []
    if (applied && applied.length > 0) parts.push(String(t('p11.notes.applyAll.applied', { accounts: accountsText(applied) })))
    if (skipped && skipped.length > 0) parts.push(String(t('p11.notes.applyAll.skipped', { accounts: accountsText(skipped) })))
    return parts.length ? parts.join(' ') : null
}

/** A toast with the accounts the note was also applied to (nothing when it was not applied). */
export function announceAppliedTo(
    t: Translate,
    applied?: NoteCopy[] | null,
    skipped?: Array<Pick<NoteCopySkipped, 'account_number'>> | null
) {
    const text = appliedToText(t, applied, skipped)
    if (text) ToastNotification(text, String(t('p11.notes.applyAll.title')), 'success', 6000)
}

export default ApplyToAllAccounts
