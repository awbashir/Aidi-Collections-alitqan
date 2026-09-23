/** Access rule and small validators shared by the follow-up screen, dialogs and panel. */
import { useAppSelector } from '@/store'
import { usePermissions } from '@/utils/v2/hooks'

/** Signed-in user id (the case owner check mirrors the API's `_check_owner`). */
export function useCurrentUserId(): string {
    return useAppSelector((state) => state.auth.user.id) || ''
}

/**
 * Whether the signed-in user may create / complete / reschedule on a case
 * owned by `ownerId`. The API enforces the same rule: `follow_ups`, plus
 * `follow_ups_manage` when the owner is someone else (or nobody).
 */
export function useFollowUpAccess() {
    const perms = usePermissions()
    const me = useCurrentUserId()
    const canView = !!perms.follow_ups
    const canManage = !!perms.follow_ups_manage
    const canActFor = (ownerId: string | null | undefined) => canView && (canManage || (!!ownerId && ownerId === me))
    return { canView, canManage, canActFor }
}

const TIME_RE = /^\d{2}:\d{2}$/
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/

/** True when a date + time pair is complete enough to send (the API judges the rest). */
export const validDue = (date: string, time: string) => DATE_RE.test(date) && TIME_RE.test(time)
