/**
 * The required promise of the wrap-up / call result and the manual call log (customer decision
 * 2026-10-20, migration 0079): the outcome "promise discussed", or a note status that records a
 * promise to pay («وعد بالسداد», `requires_promise`), needs the amount and the due date (or an
 * open promise of the case, linked). Such a status needs the outcome connected or promise
 * discussed (the API answers 422 promise_needs_conversation otherwise).
 */
import { useCallback, useState } from 'react'
import type { Outcome, WrapupBody } from '@/services/v2'
import { riyadhToday } from '@/utils/v2/format'
import {
    emptyRequiredPromise,
    requiredPromiseBody,
    requiredPromiseValid,
} from '@/views/phase1/money/requiredPromise'
import type { RequiredPromiseDraft } from '@/views/phase1/money/requiredPromise'

export const PROMISE_OUTCOMES: Outcome[] = ['connected', 'promise_discussed']

export function useRequiredPromise(outcome: Outcome | undefined, disabled = false) {
    const [statusNeedsPromise, setStatusNeedsPromise] = useState(false)
    const [draft, setDraft] = useState<RequiredPromiseDraft>(emptyRequiredPromise())
    const [outstanding, setOutstanding] = useState<string | null>(null)
    const reason: 'outcome' | 'status' | null = disabled
        ? null
        : outcome === 'promise_discussed'
        ? 'outcome'
        : statusNeedsPromise
        ? 'status'
        : null
    /* a promise-to-pay status with an outcome that is not a conversation */
    const mismatch = !disabled && statusNeedsPromise && !!outcome && !PROMISE_OUTCOMES.includes(outcome)
    const valid = !reason || (!mismatch && requiredPromiseValid(draft, riyadhToday(), outstanding))
    const body = (): WrapupBody['promise'] | undefined => (reason && !mismatch ? requiredPromiseBody(draft) : undefined)
    const reset = useCallback(() => setDraft(emptyRequiredPromise()), [])
    return {
        reason,
        required: !!reason,
        mismatch,
        draft,
        setDraft,
        setStatusNeedsPromise,
        setOutstanding,
        valid,
        body,
        reset,
    }
}

export type RequiredPromiseState = ReturnType<typeof useRequiredPromise>
