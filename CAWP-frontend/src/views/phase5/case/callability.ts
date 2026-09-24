/**
 * What the case page knows about calling before a click: my calling state
 * (`GET /v2/me/calling`) and the case's blocks (`GET /v2/cases/<id>/calling-blocks`), turned
 * into the API's own refusal codes. The API still decides every call.
 */
import { useCallback, useEffect, useState } from 'react'
import { callsApi } from '@/services/v2'
import type { CaseBlocks, Contact, MyCallingInfo } from '@/services/v2'
import { CALL_WRAPPED_EVENT } from '../live/callDock'

export type Refusal = { code: string; detail?: Record<string, unknown> | null }

/** A plausible E.164 phone value, mirroring `e164_from_contact` (only to hide "Call" on e-mails). */
export function looksCallable(value: string | null | undefined): boolean {
    if (!value) return false
    let digits = value.trim().replace(/[\s\-().]/g, '')
    if (digits.startsWith('00')) digits = digits.slice(2)
    digits = digits.replace(/^\++/, '')
    if (/^0?5\d{8}$/.test(digits)) return true
    return /^[1-9]\d{7,14}$/.test(digits)
}

export type Callability = {
    loading: boolean
    my: MyCallingInfo | null
    blocks: CaseBlocks | null
    /** The first reason the screen already knows, in rule order; null = let the API decide. */
    reasonFor: (contact: Contact) => Refusal | null
    reload: () => void
}

/** What the case page knows about calling: my calling state and the case's blocks. */
export function useCallability(caseId: string | null, caseState: { isLocked?: boolean; isArchived?: boolean }, enabled: boolean): Callability {
    const [my, setMy] = useState<MyCallingInfo | null>(null)
    const [blocks, setBlocks] = useState<CaseBlocks | null>(null)
    const [loading, setLoading] = useState(enabled)
    const [tick, setTick] = useState(0)
    useEffect(() => {
        if (!enabled || !caseId) return undefined
        let alive = true
        setLoading(true)
        Promise.allSettled([callsApi.myCalling(), callsApi.caseBlocks(caseId)]).then(([m, b]) => {
            if (!alive) return
            setMy(m.status === 'fulfilled' ? m.value : null)
            setBlocks(b.status === 'fulfilled' ? b.value : null)
            setLoading(false)
        })
        return () => {
            alive = false
        }
    }, [caseId, enabled, tick])
    /* After a wrap-up (a do-not-call block or a released reservation), read again. */
    useEffect(() => {
        const on = () => setTick((n) => n + 1)
        window.addEventListener(CALL_WRAPPED_EVENT, on)
        return () => window.removeEventListener(CALL_WRAPPED_EVENT, on)
    }, [])
    const reload = useCallback(() => setTick((n) => n + 1), [])

    const reasonFor = (c: Contact): Refusal | null => {
        if (my && my.reason === 'no_extension') return { code: 'no_extension' }
        if (caseState.isArchived) return { code: 'case_archived' }
        if (caseState.isLocked) return { code: 'case_locked' }
        if (blocks?.do_not_call) return { code: 'do_not_call' }
        if (c.is_blocked) return { code: 'contact_blocked', detail: { reason: c.block_reason } }
        if (c.contact_type?.name === 'email' || !looksCallable(c.contact)) return { code: 'destination_invalid' }
        /* The API's order: the calling window, then reservations, then the transport. */
        if (my && my.reason && my.reason.startsWith('calling_')) return { code: my.reason, detail: { window: my.window } }
        if (my && my.active_attempt_id) return { code: 'calling_reserved', detail: { reserved: 'collector' } }
        if (my && my.reason && my.reason !== 'forbidden') return { code: my.reason }
        return null
    }
    return { loading, my, blocks, reasonFor, reload }
}

