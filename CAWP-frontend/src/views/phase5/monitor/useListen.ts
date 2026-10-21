/** The call monitor's listening state (LiveControls.tsx): my capabilities and open listening session. */
import { useCallback, useEffect, useState } from 'react'
import { callControlApi, pollControl, softphoneApi } from '@/services/softphone'
import type { CallControl, ControlCapabilities, SpyMode } from '@/services/softphone'
import type { MonitorLive } from '@/services/v2'

export type LiveCall = MonitorLive['calls'][number]

export type ListenState = ReturnType<typeof useListen>

/**
 * My capabilities and my open listening session, kept fresh while the page is open.
 * `softphone`: my in-app softphone is switched on (0082). Off (the default), the PBX rings my
 * own phone on my extension and I pick it up there; null = not known yet.
 */
export function useListen() {
    const [caps, setCaps] = useState<ControlCapabilities | null>(null)
    const [softphone, setSoftphone] = useState<boolean | null>(null)
    const [control, setControl] = useState<CallControl | null>(null)
    const [error, setError] = useState<unknown>(null)
    const [busy, setBusy] = useState(false)

    useEffect(() => {
        const ctrl = new AbortController()
        callControlApi
            .mine(ctrl.signal)
            .then((res) => {
                setCaps(res.capabilities)
                const open = res.items.find((c) => c.kind === 'spy' && c.mine && !c.ended_at && ['queued', 'active'].includes(c.state))
                if (open) setControl(open)
            })
            .catch(() => setCaps(null))
        softphoneApi
            .myCalling()
            .then((me) => !ctrl.signal.aborted && setSoftphone(me.softphone_enabled === true))
            .catch(() => undefined)
        return () => ctrl.abort()
    }, [])

    const id = control && !control.ended_at ? control.id : null
    useEffect(() => {
        if (!id) return undefined
        return pollControl(id, setControl, setError)
    }, [id])

    const start = useCallback(async (call: LiveCall, extension: string, mode: SpyMode = 'listen') => {
        setBusy(true)
        setError(null)
        try {
            setControl(await callControlApi.spy({ call_id: call.id, extension, mode }))
        } catch (e) {
            setError(e)
        } finally {
            setBusy(false)
        }
    }, [])

    const command = useCallback(
        async (op: 'mode' | 'stop', mode?: SpyMode) => {
            if (!control) return
            setBusy(true)
            try {
                setControl(await callControlApi.command(control.id, op, mode))
                setError(null)
            } catch (e) {
                setError(e)
            } finally {
                setBusy(false)
            }
        },
        [control]
    )

    const open = !!control && !control.ended_at && ['queued', 'active'].includes(control.state)
    return { caps, softphone, control, open, error, busy, start, command, dismiss: () => setControl(null) }
}
