/**
 * Keeps the signed-in user's permissions current (Phase 3, `GET /v2/me`).
 *
 * The permission map used to come only from the login response, so a grant
 * change on the role reached the UI only after a new sign-in. This hook reads
 * `GET /v2/me` right after sign-in, whenever the window regains focus (at most
 * once per 15 s) and every 5 minutes, and replaces `auth.user.permissions`
 * when it differs. The nav and every route guard read that map, so they follow
 * without a reload.
 *
 * A revoked session answers 401; the global interceptor (services/BaseService)
 * records why and signs out, and the sign-in page explains it. Other failures
 * (network, 5xx) keep the current map: the server still checks every call.
 */
import { useCallback, useEffect, useRef } from 'react'
import { setPermissions, useAppDispatch, useAppSelector } from '@/store'
import { adminApi } from '@/services/v2/p3Api'

export const PERMISSION_REFRESH_MS = 5 * 60 * 1000
const FOCUS_THROTTLE_MS = 15 * 1000

/** The granted keys, sorted: two maps grant the same when these match. */
const grantedKeys = (p: object | null | undefined) =>
    Object.entries((p ?? {}) as Record<string, unknown>)
        .filter(([, v]) => v === true)
        .map(([k]) => k)
        .sort()
        .join(',')

export function useSessionRefresh(enabled: boolean) {
    const dispatch = useAppDispatch()
    const userId = useAppSelector((s) => s.auth.user.id)
    const permissions = useAppSelector((s) => s.auth.user.permissions)
    const permRef = useRef(permissions)
    permRef.current = permissions
    const last = useRef(0)
    const inFlight = useRef(false)

    const refresh = useCallback(
        async (force = false) => {
            if (!enabled || inFlight.current) return
            const now = Date.now()
            if (!force && now - last.current < FOCUS_THROTTLE_MS) return
            last.current = now
            inFlight.current = true
            try {
                const me = await adminApi.me()
                // A different user's token (another tab signed in as someone else): leave it.
                if (userId && me.user?.id && String(me.user.id) !== String(userId)) return
                if (grantedKeys(me.permissions) !== grantedKeys(permRef.current)) {
                    dispatch(setPermissions({ permissions: me.permissions, roleName: me.role?.name }))
                }
            } catch {
                /* 401 is handled by the interceptor; anything else keeps the current map */
            } finally {
                inFlight.current = false
            }
        },
        [dispatch, enabled, userId]
    )

    useEffect(() => {
        if (!enabled) return undefined
        refresh(true)
        const onFocus = () => refresh(false)
        const onVisible = () => {
            if (document.visibilityState === 'visible') refresh(false)
        }
        /* Lets a screen that just changed grants (the role form) ask for a refresh. */
        const onRequest = () => refresh(true)
        window.addEventListener('focus', onFocus)
        document.addEventListener('visibilitychange', onVisible)
        window.addEventListener('cawp:refresh-permissions', onRequest)
        const timer = window.setInterval(() => refresh(true), PERMISSION_REFRESH_MS)
        return () => {
            window.removeEventListener('focus', onFocus)
            document.removeEventListener('visibilitychange', onVisible)
            window.removeEventListener('cawp:refresh-permissions', onRequest)
            window.clearInterval(timer)
        }
    }, [enabled, refresh])
}

/** Ask the running app to re-read `GET /v2/me` now (e.g. after saving the caller's own role). */
export const requestPermissionRefresh = () => window.dispatchEvent(new Event('cawp:refresh-permissions'))
