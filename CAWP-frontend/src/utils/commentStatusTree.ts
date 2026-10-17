/**
 * Contract note statuses (migration 0068): which statuses a picker offers.
 *
 * - `contract_id` null = a general status, offered on every contract;
 * - `parent_id` set = a sub-status: never offered as the MAIN status, only in the
 *   optional sub-status select under its main status;
 * - `deleted_at` set = soft-deleted: never offered for a new note (GET /comment_status
 *   already leaves them out unless `include_deleted=1`; this is a second guard).
 *
 * Works on the raw `/comment_status` rows (`id`) and on the v2 lookup options (`value`).
 */
export type StatusLike = {
    id?: string
    value?: string
    contract_id?: string | null
    parent_id?: string | null
    deleted_at?: string | null
}

export const statusKey = (s: StatusLike) => String(s.value ?? s.id ?? '')

export const isLiveStatus = (s: StatusLike) => !s.deleted_at

export const isMainStatus = (s: StatusLike) => !s.parent_id && isLiveStatus(s)

/** Live main statuses for a case's contract: the contract's own plus the general ones.
 *  Without a contract (filter screens with no contract chosen): every live main status. */
export function mainStatusesFor<T extends StatusLike>(
    list: T[] | null | undefined,
    contractId?: string | null
): T[] {
    return (list || []).filter(
        (s) =>
            isMainStatus(s) &&
            (!contractId || s.contract_id == null || s.contract_id === contractId)
    )
}

/** Live sub-statuses of one main status (empty when none or no main status). */
export function subStatusesOf<T extends StatusLike>(
    list: T[] | null | undefined,
    parentId?: string | null
): T[] {
    if (!parentId) return []
    return (list || []).filter(
        (s) => isLiveStatus(s) && s.parent_id === parentId
    )
}

/**
 * THE status list of a new case note: the wrap-up dialog, the client page's call-result form
 * and the legacy note form all use this one rule (customer decision 2026-10-17).
 *
 * - Contract: the case's contract statuses + the general ones (live main statuses only). An
 *   UNKNOWN contract (`null`, e.g. a case the screen could not read) offers the general ones
 *   only, never every contract's statuses (the API refuses another contract's status).
 * - Reachability: a status WITHOUT a reachability fits every reachability, so it is offered
 *   always (also while none is chosen); a status WITH one is offered only while that
 *   reachability is chosen. This is exactly what the API accepts (wrap-up and POST /comments).
 */
export function noteStatusOptions<
    T extends StatusLike & { reachability_id?: string | null }
>(
    list: T[] | null | undefined,
    contractId: string | null | undefined,
    reach: string | null | undefined
): T[] {
    return (list || []).filter(
        (s) =>
            isMainStatus(s) &&
            (s.contract_id == null || (!!contractId && s.contract_id === contractId)) &&
            (s.reachability_id == null || (!!reach && s.reachability_id === reach))
    )
}
