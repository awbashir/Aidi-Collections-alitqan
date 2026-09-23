/**
 * Status badges for Phase 1 states. Each tone carries its own glyph shape
 * (StatusPill), so states survive greyscale and colour-vision deficiency.
 * Labels come from the API's state value; nothing is computed on the client.
 */
import { useTranslation } from 'react-i18next'
import StatusPill from '@/components/shared/StatusPill'
import type { StatusTone } from '@/components/shared/StatusPill'
import type {
    AttentionReason,
    CorrectionStatus,
    FollowUpBucket,
    FollowUpStatus,
    PaymentStatus,
    PromiseState,
} from '@/services/v2'

const REASON_TONE: Record<AttentionReason, StatusTone> = {
    late_follow_up: 'danger',
    missed_promise: 'danger',
    pending_approval: 'warning',
    unassigned: 'info',
    unscheduled: 'neutral',
}

const PROMISE_TONE: Record<PromiseState, StatusTone> = {
    open: 'info',
    overdue: 'danger',
    fulfilled: 'success',
    cancelled: 'neutral',
}

const PAYMENT_TONE: Record<PaymentStatus, StatusTone> = {
    pending: 'warning',
    approved: 'success',
    rejected: 'danger',
    reversed: 'neutral',
    deleted: 'neutral',
}

const CORRECTION_TONE: Record<CorrectionStatus, StatusTone> = {
    pending: 'info',
    approved: 'success',
    rejected: 'danger',
    cancelled: 'neutral',
}

const BUCKET_TONE: Record<FollowUpBucket, StatusTone> = {
    late: 'danger',
    today: 'warning',
    upcoming: 'info',
}

const FOLLOW_UP_TONE: Record<FollowUpStatus, StatusTone> = {
    open: 'info',
    completed: 'success',
    cancelled: 'neutral',
    superseded: 'neutral',
}

type Size = 'row' | 'md'

export const ReasonBadge = ({ reason, size }: { reason: AttentionReason; size?: Size }) => {
    const { t } = useTranslation()
    return (
        <StatusPill tone={REASON_TONE[reason] || 'neutral'} size={size}>
            <span data-reason={reason}>{t(`p1.reason.${reason}`)}</span>
        </StatusPill>
    )
}

export const PromiseBadge = ({ state, size }: { state: PromiseState; size?: Size }) => {
    const { t } = useTranslation()
    return (
        <StatusPill tone={PROMISE_TONE[state] || 'neutral'} size={size}>
            <span data-state={state}>{t(`p1.promiseState.${state}`)}</span>
        </StatusPill>
    )
}

export const PaymentBadge = ({ status, size }: { status: PaymentStatus; size?: Size }) => {
    const { t } = useTranslation()
    return (
        <StatusPill tone={PAYMENT_TONE[status] || 'neutral'} size={size}>
            <span data-status={status}>{t(`p1.paymentStatus.${status}`)}</span>
        </StatusPill>
    )
}

export const CorrectionBadge = ({ status, size }: { status: CorrectionStatus; size?: Size }) => {
    const { t } = useTranslation()
    return (
        <StatusPill tone={CORRECTION_TONE[status] || 'neutral'} size={size}>
            <span data-status={status}>{t(`p1.correctionStatus.${status}`)}</span>
        </StatusPill>
    )
}

export const BucketBadge = ({
    bucket,
    daysLate,
    size,
}: {
    bucket: FollowUpBucket
    daysLate?: number | null
    size?: Size
}) => {
    const { t } = useTranslation()
    return (
        <StatusPill tone={BUCKET_TONE[bucket] || 'neutral'} size={size}>
            <span data-bucket={bucket}>
                {bucket === 'late' && daysLate
                    ? t('p1.followUp.lateBy', { count: daysLate })
                    : t(`p1.bucket.${bucket}`)}
            </span>
        </StatusPill>
    )
}

export const FollowUpStatusBadge = ({ status, size }: { status: FollowUpStatus; size?: Size }) => {
    const { t } = useTranslation()
    return (
        <StatusPill tone={FOLLOW_UP_TONE[status] || 'neutral'} size={size}>
            <span data-status={status}>{t(`p1.followUpStatus.${status}`)}</span>
        </StatusPill>
    )
}

export const PlainBadge = ({ tone = 'neutral', children, size }: { tone?: StatusTone; children: React.ReactNode; size?: Size }) => (
    <StatusPill tone={tone} size={size}>
        {children}
    </StatusPill>
)
