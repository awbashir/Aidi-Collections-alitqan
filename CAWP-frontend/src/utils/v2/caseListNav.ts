/**
 * Previous / next client of the list a case was opened from (customer decision 2026-10-17).
 *
 * - Every case list (DataTable with clickable rows, the clients list) registers the case ids of
 *   the page it shows (`registerListRows`).
 * - Opening a row in the same tab (`openCaseInApp`) records that page's ORDERED, de-duplicated
 *   case ids in sessionStorage under the list's URL, which also travels as `state.from`.
 * - The case screen reads them back (`useCaseListNav`) and moves to the previous / next case in
 *   the same tab, replacing the history entry: "Back to list" still returns to the list with
 *   its filters and scroll, and Browser Back does too.
 * Only the current page of the list is known; at its ends the buttons are disabled.
 */
import { useCallback, useMemo } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { backPathFromState, listOrder } from './rowNavigation'

export type CaseListNav = {
    /** False when the case was not opened from a list row in this tab. */
    available: boolean
    /** 1-based position and the page's size ("n من m"). */
    position: number
    total: number
    prevId: string | null
    nextId: string | null
    /** Open another case of the list in this tab (replaces the history entry). */
    go: (caseId: string, tab?: string) => void
}

export function useCaseListNav(caseId: string | null | undefined): CaseListNav {
    const location = useLocation()
    const navigate = useNavigate()
    const from = backPathFromState(location.state)
    const ids = useMemo(() => listOrder(from), [from])
    const index = caseId && ids ? ids.indexOf(String(caseId)) : -1
    const go = useCallback(
        (id: string, tab?: string) => {
            const current = location.pathname.substring(location.pathname.lastIndexOf('/') + 1) || 'details'
            navigate(
                { pathname: `/clients/details/${tab || current}`, search: `?id=${encodeURIComponent(id)}` },
                { replace: true, state: location.state }
            )
        },
        [location.pathname, location.state, navigate]
    )
    if (!ids || index < 0) return { available: false, position: 0, total: 0, prevId: null, nextId: null, go }
    return {
        available: true,
        position: index + 1,
        total: ids.length,
        prevId: index > 0 ? ids[index - 1] : null,
        nextId: index < ids.length - 1 ? ids[index + 1] : null,
        go,
    }
}
