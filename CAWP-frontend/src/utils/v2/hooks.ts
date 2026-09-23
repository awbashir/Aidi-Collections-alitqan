import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useAppSelector } from '@/store'
import { newIdempotencyKey } from '@/services/v2/http'
import { withDerivedPermissions } from './derivedPermissions'

/** The signed-in role's permission map (`role.permission`) plus derived keys, never undefined. */
export function usePermissions(): Record<string, boolean> {
    const permissions = useAppSelector((state) => state.auth.user.permissions)
    return useMemo(() => withDerivedPermissions(permissions) as Record<string, boolean>, [permissions])
}

export type QueryState<T> = {
    data: T | undefined
    error: unknown
    loading: boolean
    /** Re-run the loader (keeps the old data on screen while it loads). */
    reload: () => void
}

/**
 * Runs `loader` whenever `deps` change. A response that arrives after a newer
 * request started (case or filter changed) is discarded, and the older request
 * is aborted, so a slow answer can never overwrite the current view.
 */
export function useV2Query<T>(
    loader: ((signal: AbortSignal) => Promise<T>) | null,
    deps: unknown[]
): QueryState<T> {
    const [data, setData] = useState<T | undefined>(undefined)
    const [error, setError] = useState<unknown>(null)
    const [loading, setLoading] = useState<boolean>(!!loader)
    const [tick, setTick] = useState(0)
    const seq = useRef(0)
    const loaderRef = useRef(loader)
    loaderRef.current = loader

    useEffect(() => {
        const run = loaderRef.current
        if (!run) {
            setLoading(false)
            return undefined
        }
        const id = ++seq.current
        const ctrl = new AbortController()
        setLoading(true)
        setError(null)
        run(ctrl.signal)
            .then((res) => {
                if (id !== seq.current) return
                setData(res)
                setLoading(false)
            })
            .catch((err) => {
                if (id !== seq.current) return
                if ((err as { code?: string })?.code === 'canceled') return
                setError(err)
                setLoading(false)
            })
        return () => ctrl.abort()
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [...deps, tick])

    const reload = useCallback(() => setTick((n) => n + 1), [])
    return { data, error, loading, reload }
}

/**
 * One Idempotency-Key per user action. A new key is made each time `active`
 * turns true (a dialog opens); every submit while it stays open (retries after
 * an error) reuses it. Call `renew()` after a success if the same surface can
 * start a new action without closing.
 */
export function useActionKey(active: boolean) {
    const [key, setKey] = useState(() => newIdempotencyKey())
    const prev = useRef(active)
    useEffect(() => {
        if (active && !prev.current) setKey(newIdempotencyKey())
        prev.current = active
    }, [active])
    const renew = useCallback(() => setKey(newIdempotencyKey()), [])
    return { key, renew }
}
