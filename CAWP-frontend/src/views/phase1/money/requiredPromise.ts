/**
 * The promise of a promise-to-pay note (migration 0079): a note status flagged
 * `requires_promise` («وعد بالسداد», a sub-status carries its main status's flag) and the call
 * outcome `promise_discussed` OFFER the promise AMOUNT and DUE DATE, or a link to an open
 * promise of the case. Customer feedback 2026-10-21: OPTIONAL (2026-10-20 had made it
 * required): an empty draft sends no promise and the note is saved without one; a half-filled
 * one must be completed. No due time: a promise is a civil date. The API creates the promise
 * with the note / wrap-up in one transaction and stays the judge (amount above the balance, a
 * past date...); these checks only mirror it. No components here (RequiredPromiseFields.tsx).
 */
import { describeV2Error, isV2Error } from '@/services/v2'
import { PROMISE_RULE_CODES } from '@/services/v2/p5Errors'
import type { Translate } from '@/services/v2'
import { MONEY_INPUT } from '@/utils/v2/format'
import { isPositiveMoney } from './moneyMath'

export type RequiredPromiseDraft = { mode: 'new' | 'link'; amount: string; due: string; promiseId?: string }

export const emptyRequiredPromise = (): RequiredPromiseDraft => ({ mode: 'new', amount: '', due: '' })

type FlaggedStatus = { value?: string; id?: string; requires_promise?: boolean | null }

/** Does the chosen main status (or its sub-status) ask for a promise? */
export function statusRequiresPromise(
    statuses: FlaggedStatus[] | null | undefined,
    statusId?: string | null,
    subStatusId?: string | null
): boolean {
    const ids = [statusId, subStatusId].filter(Boolean).map(String)
    return (statuses || []).some((s) => ids.includes(String(s.value ?? s.id ?? '')) && s.requires_promise === true)
}

const CENTS = /^(\d+)(?:\.(\d{1,2}))?$/
const cents = (v: string): bigint | null => {
    const m = CENTS.exec(v.trim().replace(/^\+/, ''))
    return m ? BigInt(m[1]) * BigInt(100) + BigInt((m[2] || '').padEnd(2, '0') || '0') : null
}

/** Nothing entered (no amount, no date; or "link" with no promise chosen): no promise is sent. */
export const promiseDraftEmpty = (d: RequiredPromiseDraft) =>
    d.mode === 'link' ? !d.promiseId : !d.amount.trim() && !d.due

/** i18n keys (under `p10.promise.`) of what is still wrong; empty = ready to send (an empty
 *  draft is ready: the promise is optional). */
export function requiredPromiseErrors(
    d: RequiredPromiseDraft,
    today: string,
    outstanding?: string | null
): { amount?: string; due?: string; link?: string } {
    if (promiseDraftEmpty(d)) return {}
    if (d.mode === 'link') return {}
    const out: { amount?: string; due?: string } = {}
    const a = d.amount.trim()
    if (!a) out.amount = 'amountRequired'
    else if (!MONEY_INPUT.test(a)) out.amount = 'amountFormat'
    else if (!isPositiveMoney(a)) out.amount = 'amountPositive'
    else if (outstanding && cents(outstanding) !== null && (cents(a) as bigint) > (cents(outstanding) as bigint))
        out.amount = 'amountAboveBalance'
    if (!d.due) out.due = 'dueRequired'
    else if (d.due < today) out.due = 'dueInPast'
    return out
}

export const requiredPromiseValid = (d: RequiredPromiseDraft, today: string, outstanding?: string | null) =>
    Object.keys(requiredPromiseErrors(d, today, outstanding)).length === 0

/** The `promise` object of a wrap-up / follow-up completion; undefined = no promise. */
export const requiredPromiseBody = (
    d: RequiredPromiseDraft
): { amount: string; due_date: string } | { promise_id: string } | undefined =>
    promiseDraftEmpty(d)
        ? undefined
        : d.mode === 'link' && d.promiseId
        ? { promise_id: d.promiseId }
        : { amount: d.amount.trim(), due_date: d.due }

/** The legacy note form's fields (POST /comments); {} = no promise. */
export const requiredPromiseLegacyFields = (d: RequiredPromiseDraft) =>
    promiseDraftEmpty(d)
        ? {}
        : d.mode === 'link' && d.promiseId
        ? { promise_id: d.promiseId }
        : { promise_amount: d.amount.trim(), promise_date: d.due }

/** Screens that show a case's promises listen for this to reload after a note created one. */
export const PROMISE_CHANGED_EVENT = 'cawp:promise-changed'

export function announcePromiseChanged(caseId: string | null | undefined) {
    if (caseId) window.dispatchEvent(new CustomEvent<{ caseId: string }>(PROMISE_CHANGED_EVENT, { detail: { caseId } }))
}

/** The promise rule's codes have their own `p10.promise.err.<code>` message (services/v2/p5Errors.ts). */
export function describePromiseCode(err: unknown, t: Translate): string | null {
    if (isV2Error(err) && err.code === 'too_many_accounts') return String(t('p11.notes.err.too_many_accounts')) // 0081
    return isV2Error(err) && PROMISE_RULE_CODES.has(err.code) ? String(t(`p10.promise.err.${err.code}`)) : null
}

/** For dialogs whose default describer is the v2 one (follow-up completion, AI save-as-note). */
export const describePromiseError = (err: unknown, t: Translate) => describePromiseCode(err, t) ?? describeV2Error(err, t)
