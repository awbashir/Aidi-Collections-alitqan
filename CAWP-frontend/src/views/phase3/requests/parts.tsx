/**
 * Badges shared by the requests screen, the case Requests tab and the
 * request detail. Document helpers are in ./documents.ts.
 */
import { useTranslation } from 'react-i18next'
import StatusPill from '@/components/shared/StatusPill'
import type { StatusTone } from '@/components/shared/StatusPill'
import type { RequestDepartment, RequestStatus } from '@/services/v2'

const STATUS_TONE: Record<RequestStatus, StatusTone> = {
    open: 'info',
    in_review: 'warning',
    resolved: 'success',
    cancelled: 'neutral',
}

export const RequestStatusBadge = ({ status, size }: { status: RequestStatus; size?: 'row' | 'md' }) => {
    const { t } = useTranslation()
    return (
        <StatusPill tone={STATUS_TONE[status] || 'neutral'} size={size}>
            <span data-status={status}>{t(`p3.req.status.${status}`)}</span>
        </StatusPill>
    )
}

/** Departments are categories, not states: a quiet outlined label. */
export const DepartmentLabel = ({ department }: { department: RequestDepartment }) => {
    const { t } = useTranslation()
    return (
        <span
            data-department={department}
            className="inline-flex h-[22px] items-center rounded-pill border border-gray-300 px-2 text-[12px] font-extrabold text-navy-600 dark:border-night-border dark:text-gray-100"
        >
            {t(`p3.req.dept.${department}`)}
        </span>
    )
}
