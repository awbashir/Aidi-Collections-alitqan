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
