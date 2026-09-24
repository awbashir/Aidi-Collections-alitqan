/**
 * Polls the P1 live view (`GET /v2/calls/<id>/live`) with its ETag, at the pace the API
 * asks for: `poll_after_ms` (1 s while live, 5 s while uncertain, null when final = stop).
 * A 304 keeps the last view and the last pace. A hidden tab polls no faster than every 5 s.
 * `seed` shows a view the caller already has (the POST /v2/calls answer) at once.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { fetchLiveCall, isV2Error } from '@/services/v2'
import type { LiveCall } from '@/services/v2'

export type LiveCallState = {
    view: LiveCall | null
    error: unknown
    /** Poll now (after an action, or to refresh a final view). */
    reload: () => void
    /** Replace the view with one returned by a mutation (cancel / resolve). */
    seed: (v: LiveCall) => void
}

const HIDDEN_MIN_MS = 5000
const ERROR_BACKOFF_MS = 5000

export function useLiveCall(attemptId: string | null, initial?: LiveCall | null): LiveCallState {
    const [view, setView] = useState<LiveCall | null>(initial && initial.id === attemptId ? initial : null)
    const [error, setError] = useState<unknown>(null)
    const [tick, setTick] = useState(0)
    const etag = useRef<string | null>(null)
    const pace = useRef<number | null>(1000)
    const viewRef = useRef<LiveCall | null>(view)
    viewRef.current = view

    useEffect(() => {
        etag.current = initial && initial.id === attemptId ? initial.etag || null : null
        pace.current = initial && initial.id === attemptId ? initial.poll_after_ms ?? null : 1000
        setView(initial && initial.id === attemptId ? initial : null)
        setError(null)
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [attemptId])

    useEffect(() => {
        if (!attemptId) return undefined
        let stop = false
        let timer: ReturnType<typeof setTimeout> | null = null
        const ctrl = new AbortController()
        const schedule = (ms: number | null) => {
            if (stop || ms === null) return
            const wait = document.hidden ? Math.max(ms, HIDDEN_MIN_MS) : ms
            timer = setTimeout(run, wait)
        }
        const run = async () => {
            try {
                const v = await fetchLiveCall(attemptId, etag.current, ctrl.signal)
                if (stop) return
                if (v) {
                    etag.current = v.etag || null
                    pace.current = v.poll_after_ms ?? null
                    setView(v)
                }
                setError(null)
                schedule(pace.current)
            } catch (e) {
                if (stop || (isV2Error(e) && e.code === 'canceled')) return
                setError(e)
                /* 404/403: the call is gone or not visible; stop polling it. */
                if (isV2Error(e) && (e.status === 404 || e.status === 403)) return
                schedule(ERROR_BACKOFF_MS)
            }
        }
        void run()
        return () => {
            stop = true
            ctrl.abort()
            if (timer) clearTimeout(timer)
        }
    }, [attemptId, tick])

    const reload = useCallback(() => {
        etag.current = null
        setTick((n) => n + 1)
    }, [])
    const seed = useCallback((v: LiveCall) => {
        etag.current = v.etag || null
        pace.current = v.poll_after_ms ?? null
        setView(v)
        setTick((n) => n + 1)
    }, [])
    return { view, error, reload, seed }
}
