/**
 * Shared state of the home dashboard sections: the page filters and the click-through links.
 * A link is only built when the signed-in role may open that screen (the route key is the
 * permission key, AuthorityGuard); otherwise the card is not clickable.
 */
import { useMemo } from 'react'
import { useAppSelector } from '@/store'
import { usePermissions } from '@/utils/v2/hooks'
import type { HomePeriod, HomeQuery } from '@/services/v2/homeDashboardApi'
import { hashLink } from './safe'

export type HomeCtx = {
    admin: boolean
    period: HomePeriod
    contract: string
    focus: { id: string; name: string } | null
    query: HomeQuery
    setContract: (id: string) => void
    setFocus: (f: { id: string; name: string } | null) => void
    links: HomeLinks
}

export type HomeLinks = ReturnType<typeof useHomeLinks>

export function useHomeLinks(admin: boolean, contract: string, focusId: string | undefined) {
    const perms = usePermissions()
    const myId = useAppSelector((s) => (s.auth?.user as { id?: string } | undefined)?.id)
    return useMemo(() => {
        const can = (k: string) => perms?.[k] === true
        const owner = focusId || undefined
        const c = contract || undefined
        return {
            /** approved payments of [from, to] */
            collected: (from?: string, to?: string) =>
                can('payments_approve')
                    ? hashLink('/payment-approvals', { status: 'approved', from, to, contract: c, owner })
                    : !admin && can('collections_currentUser')
                    ? '/current_user_collection'
                    : can('collections_users')
                    ? '/user_collection'
                    : undefined,
            pending: (from?: string, to?: string) =>
                can('payments_approve') ? hashLink('/payment-approvals', { status: 'pending', from, to, contract: c, owner }) : undefined,
            /** promises due in [from, to], optionally one state (open | overdue | fulfilled) */
            promises: (from?: string, to?: string, state?: string) =>
                can('promises')
                    ? hashLink('/promises', { state, from, to, contract: c, owner })
                    : !admin && can('report_collectors_currentUser_paymentDates')
                    ? '/report/collectors/paymentDates'
                    : can('report_users_paymentDates')
                    ? '/report/users/paymentDates'
                    : undefined,
            notes: () =>
                !admin && can('report_collectors_currentUser_comments')
                    ? '/report/collectors/comments'
                    : can('report_users_comments')
                    ? '/report/users/comments'
                    : can('team_dashboard')
                    ? '/team-dashboard'
                    : undefined,
            /** the cases list, filtered to the focused collector (or "mine" for a collector) */
            clients: (extra: Record<string, string | undefined> = {}) =>
                can('clints')
                    ? hashLink('/clients', { user: owner || (!admin ? myId : undefined), contract: c, ...extra })
                    : undefined,
            clientsOf: (userId?: string | null) => (can('clints') && userId ? hashLink('/clients', { user: userId, contract: c }) : undefined),
            neglect: () => (can('report_quality_neglect') ? '/report/quality/neglect' : undefined),
            team: () => (can('team_any') ? '/team' : undefined),
            monitor: () => (can('call_monitor') ? '/call-monitor' : undefined),
        }
    }, [perms, myId, admin, contract, focusId])
}
