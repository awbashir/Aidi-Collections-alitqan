/**
 * Small pieces shared by the Phase 5 calling screens: state / outcome / mode badges,
 * honest labels, durations (unknown stays unknown) and the case cell.
 *
 * Honesty rules (PROJECT-NOTES §2.9, docs/phase5-spec.md): "submitted" is never
 * "connected"; a conversation is only the PBX's bridge evidence; an unknown duration is
 * "Unknown", never 0; an uncertain call is shown as unknown until it is reconciled.
 */
import classNames from 'classnames'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import StatusPill from '@/components/shared/StatusPill'
import { Ltr } from '@/components/v2/Display'
import { caseLink } from '@/utils/v2/links'
import type { CaseTab } from '@/utils/v2/links'
import type { CallMode, CallState, CampaignStatus, MemberState, Outcome } from '@/services/v2'
import { CALL_STATE_TONE, CAMPAIGN_TONE, MEMBER_TONE, OUTCOME_TONE, callStateLabel, formatDuration, outcomeLabel } from './labels'

type Size = 'row' | 'md'

export const CallStateBadge = ({ state, size }: { state: CallState; size?: Size }) => {
    const { t } = useTranslation()
    return (
        <StatusPill tone={CALL_STATE_TONE[state] || 'neutral'} size={size}>
            <span data-call-state={state}>{callStateLabel(t, state)}</span>
        </StatusPill>
    )
}

export const OutcomeBadge = ({ outcome, size }: { outcome: Outcome; size?: Size }) => {
    const { t } = useTranslation()
    return (
        <StatusPill tone={OUTCOME_TONE[outcome] || 'neutral'} size={size}>
            <span data-outcome={outcome}>{outcomeLabel(t, outcome)}</span>
        </StatusPill>
    )
}

export const CampaignStatusBadge = ({ status, size }: { status: CampaignStatus; size?: Size }) => {
    const { t } = useTranslation()
    return (
        <StatusPill tone={CAMPAIGN_TONE[status] || 'neutral'} size={size}>
            <span data-campaign-status={status}>{t(`p5.campaign.status.${status}`)}</span>
        </StatusPill>
    )
}

export const MemberStateBadge = ({ state, size }: { state: MemberState; size?: Size }) => {
    const { t } = useTranslation()
    return (
        <StatusPill tone={MEMBER_TONE[state] || 'neutral'} size={size}>
            <span data-member-state={state}>{t(`p5.member.state.${state}`)}</span>
        </StatusPill>
    )
}

export const ModeLabel = ({ mode }: { mode: CallMode }) => {
    const { t } = useTranslation()
    return (
        <span className="whitespace-nowrap text-[12.5px] text-gray-600" data-mode={mode}>
            {t(`p5.mode.${mode}`)}
        </span>
    )
}

export const DurationText = ({
    seconds,
    source,
    className,
}: {
    seconds: number | null | undefined
    source?: 'reported' | 'pbx' | null
    className?: string
}) => {
    const { t } = useTranslation()
    const text = formatDuration(seconds)
    if (text === null) {
        return (
            <span className={classNames('text-gray-500', className)} data-duration="unknown" title={String(t('p5.duration.unknownHint'))}>
                {t('p5.duration.unknown')}
            </span>
        )
    }
    return (
        <span className={classNames('tabular-nums', className)} dir="ltr" data-duration={seconds ?? ''}>
            {text}
            {source && <span className="ms-1 text-[11px] text-gray-500">({t(`p5.duration.source.${source}`)})</span>}
        </span>
    )
}

export const CaseCell = ({
    kase,
    tab = 'calls',
    stop,
}: {
    kase: { id: string; name: string | null; account_number: string | null; missing?: boolean } | null
    tab?: CaseTab
    stop?: boolean
}) => {
    const { t } = useTranslation()
    if (!kase || kase.missing) return <span className="text-gray-500">{t('p5.case.missing')}</span>
    return (
        <span className="flex min-w-0 flex-col">
            <Link
                to={caseLink(kase.id, tab)}
                onClick={stop ? (e) => e.stopPropagation() : undefined}
                className="truncate font-extrabold text-azure-600 hover:underline"
                data-testid="p5-case-link"
            >
                <span dir="auto">{kase.name || t('p5.case.unnamed')}</span>
            </Link>
            {kase.account_number && <Ltr className="whitespace-nowrap text-[12px] text-gray-600">{kase.account_number}</Ltr>}
        </span>
    )
}

/** A bordered notice (info / warning / danger), with an optional title. */
export const Notice = ({
    tone = 'info',
    title,
    children,
    testId,
    className,
}: {
    tone?: 'info' | 'warning' | 'danger' | 'success' | 'neutral'
    title?: React.ReactNode
    children?: React.ReactNode
    testId?: string
    className?: string
}) => (
    <div
        data-testid={testId}
        role={tone === 'danger' ? 'alert' : undefined}
        className={classNames(
            'rounded-lg border px-3 py-2 text-[13px]',
            tone === 'info' && 'border-azure-100 bg-info-tint text-gray-800 dark:border-night-border dark:bg-night-raised dark:text-night-body',
            tone === 'warning' && 'border-warning/30 bg-warning-tint text-gray-800 dark:border-night-border dark:bg-night-raised dark:text-night-body',
            tone === 'danger' && 'border-red-200 bg-danger-tint text-danger dark:border-night-border dark:bg-night-raised',
            tone === 'success' && 'border-success/30 bg-success-tint text-gray-800 dark:border-night-border dark:bg-night-raised dark:text-night-body',
            tone === 'neutral' && 'border-gray-300 bg-gray-50 text-gray-700 dark:border-night-border dark:bg-night-raised dark:text-night-body',
            className
        )}
    >
        {title && <div className="mb-0.5 font-extrabold text-navy-600 dark:text-gray-100">{title}</div>}
        {children}
    </div>
)

/** Eyebrow + value in a compact grid. */
export const Fact = ({ label, children, testId, className }: { label: React.ReactNode; children: React.ReactNode; testId?: string; className?: string }) => (
    <div className={classNames('min-w-0', className)} data-testid={testId}>
        <div className="text-eyebrow mb-0.5">{label}</div>
        <div className="break-words text-[13.5px] text-gray-800 dark:text-night-body">{children}</div>
    </div>
)

/** A card frame (8px radius, 1px border, no shadow). */
export const Card = ({
    title,
    actions,
    children,
    testId,
    className,
    lead,
}: {
    title?: React.ReactNode
    actions?: React.ReactNode
    children: React.ReactNode
    testId?: string
    className?: string
    lead?: boolean
}) => (
    <section
        data-testid={testId}
        className={classNames(
            'min-w-0 rounded-lg border border-gray-300 bg-white p-5 dark:border-night-border dark:bg-night-card',
            lead && 'card-lead',
            className
        )}
    >
        {(title || actions) && (
            <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                {title && <h5 className="mb-0 text-[15px] font-extrabold text-navy-600 dark:text-gray-100">{title}</h5>}
                {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
            </div>
        )}
        {children}
    </section>
)
