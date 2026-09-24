/**
 * Why the app signed the user out (Phase 3, server-side sessions).
 *
 * The API rejects a token with 401 once an administrator ends the user's
 * sessions ("sign out everywhere", deactivation, a role or password change),
 * with flask-jwt-extended's body `{"msg": "Token has been revoked"}`; an
 * expired token answers `{"msg": "Token has expired"}`. The global 401
 * interceptor (services/BaseService) records the reason here before it signs
 * the user out, and the sign-in page shows it once.
 *
 * sessionStorage, not the persisted store: the note belongs to this tab and
 * must not survive into an unrelated later visit.
 */
export type SignOutReason = 'revoked' | 'expired'

const KEY = 'cawp.signout.reason'

/** Classifies a 401 body. Anything else (a failed login, a missing token) is not a session end. */
export function signOutReasonFrom(body: unknown): SignOutReason | null {
    const msg = body && typeof body === 'object' ? (body as { msg?: unknown }).msg : undefined
    if (typeof msg !== 'string') return null
    if (/revoked/i.test(msg)) return 'revoked'
    if (/expired/i.test(msg)) return 'expired'
    return null
}

/** Called by the 401 interceptor while the user is still signed in. */
export function recordSignOutReason(reason: SignOutReason | null): void {
    if (!reason) return
    try {
        window.sessionStorage.setItem(KEY, reason)
    } catch {
        /* storage disabled: the user still lands on sign-in, only without the note */
    }
}

/** The recorded reason, if any (the sign-in page reads it on mount). */
export function peekSignOutReason(): SignOutReason | null {
    try {
        const v = window.sessionStorage.getItem(KEY)
        return v === 'revoked' || v === 'expired' ? v : null
    } catch {
        return null
    }
}

/** Clears the note so a reload or the next visit does not repeat it. */
export function clearSignOutReason(): void {
    try {
        window.sessionStorage.removeItem(KEY)
    } catch {
        /* nothing to clear */
    }
}
