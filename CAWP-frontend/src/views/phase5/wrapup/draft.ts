/**
 * Drafts and rules of the wrap-up / manual-log forms (no components here): the note
 * (text, status, contact method: the API's required fields), the optional callback (a
 * Riyadh date alone means 09:00) and the optional explicitly agreed promise (outcome
 * `connected` only).
 */
import { useEffect, useState } from 'react'
import { lookups } from '@/services/v2'
import type { CommentStatusOption, Option, Outcome, WrapupBody } from '@/services/v2'
import { MONEY_INPUT, riyadhInstant } from '@/utils/v2/format'

/* The legacy note form defaults the contact method to "mobile" (Comments.tsx MOBILE_METHOD_ID). */
export const MOBILE_METHOD_ID = 'ff081c7e-4500-4a75-ae36-580f2ae90834'

export type NoteDraft = { text: string; reach?: string; status?: string; method?: string }
export type CallbackDraft = { date: string; time: string }
export type PromiseDraft = { agreed: boolean; amount: string; due: string }

export const emptyNote = (): NoteDraft => ({ text: '' })
export const emptyCallback = (): CallbackDraft => ({ date: '', time: '' })
export const emptyPromise = (): PromiseDraft => ({ agreed: false, amount: '', due: '' })

type NoteLookups = { methods: Option[]; reachability: Option[]; statuses: CommentStatusOption[]; loading: boolean; failed: boolean }

export function useNoteLookups(active: boolean): NoteLookups {
    const [state, setState] = useState<NoteLookups>({ methods: [], reachability: [], statuses: [], loading: true, failed: false })
    useEffect(() => {
        if (!active) return undefined
        let alive = true
        Promise.allSettled([lookups.contactMethods(), lookups.reachability(), lookups.commentStatuses()]).then(([m, r, s]) => {
            if (!alive) return
            setState({
                methods: m.status === 'fulfilled' ? m.value : [],
                reachability: r.status === 'fulfilled' ? r.value : [],
                statuses: s.status === 'fulfilled' ? s.value : [],
                loading: false,
                failed: [m, r, s].some((x) => x.status === 'rejected'),
            })
        })
        return () => {
            alive = false
        }
    }, [active])
    return state
}

/** Whether the note part is complete (text, status and contact method are required by the API). */
export const noteValid = (n: NoteDraft) => n.text.trim() !== '' && !!n.status && !!n.method

export const noteBody = (n: NoteDraft): WrapupBody['note'] => ({
    text: n.text.trim(),
    comment_status_id: n.status as string,
    contact_method_id: n.method as string,
    ...(n.reach ? { comment_reachability_id: n.reach } : {}),
})

/** A time without a date is incomplete; a date alone means 09:00 Riyadh (the API's rule). */
export const callbackValid = (c: CallbackDraft) => (c.time ? !!c.date : true)

export const callbackValue = (c: CallbackDraft): string | undefined =>
    c.date ? (c.time ? riyadhInstant(c.date, c.time) : c.date) : undefined

export const promiseValid = (p: PromiseDraft, outcome: Outcome | undefined) =>
    !p.agreed || (outcome === 'connected' && MONEY_INPUT.test(p.amount.trim()) && !!p.due)

export const promiseValue = (p: PromiseDraft, outcome: Outcome | undefined): WrapupBody['promise'] | undefined =>
    p.agreed && outcome === 'connected' ? { agreed: true, amount: p.amount.trim(), due_date: p.due } : undefined

