/**
 * Small pieces shared by the Phase 6 screens: the honest AI readiness / refusal notices (every
 * reason the API gives, and for administrators a link to AI admin), the "AI-generated, review
 * before saving" notice, percentages that stay "—" when nothing was assessed, and status badges.
 */
import classNames from 'classnames'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { Sparkles } from 'lucide-react'
import StatusPill from '@/components/shared/StatusPill'
import { describeP6Error, isAiRefusal, isV2Error, refusalReasons, aiReasonText } from '@/services/v2'
import type { AiCapability, AiCallOutcome, AiQuota, DraftStatus, QcRatio, QcResult, QcStatus } from '@/services/v2'
import { usePermissions } from '@/utils/v2/hooks'
import { formatRiyadhDateTime } from '@/utils/v2/format'
import { Notice } from '@/views/phase5/parts'
import {
    CALL_OUTCOME_TONE,
    DRAFT_STATUS_TONE,
    QC_RESULT_TONE,
    QC_STATUS_TONE,
    formatFraction,
    formatPercent,
    qcStatusLabel,
    resultLabel,
} from './labels'

type Size = 'row' | 'md'

/** For administrators: where the configuration and the data policy are managed. */
export const AiAdminLink = ({ className }: { className?: string }) => {
    const { t } = useTranslation()
    const perms = usePermissions()
    if (!perms.ai_admin) return <div className={classNames('mt-1 text-[12px] text-gray-600', className)}>{t('p6.ai.askAdmin')}</div>
    return (
        <Link to="/ai-admin" className={classNames('mt-1 inline-block text-[12.5px] font-extrabold text-azure-600 hover:underline', className)} data-testid="p6-ai-admin-link">
            {t('p6.ai.openAdmin')}
        </Link>
    )
}

/** Why an AI capability cannot run now: every reason, in the API's order, and the quota. */
export const AiReadinessNotice = ({
    capability,
    quota,
    testId = 'p6-ai-not-ready',
    className,
    extraReasons = [],
}: {
    capability: AiCapability | undefined
    quota?: AiQuota | null
    testId?: string
    className?: string
    extraReasons?: string[]
}) => {
    const { t } = useTranslation()
    const reasons = [...(capability?.reasons || []), ...extraReasons]
    if (quota?.exceeded && !reasons.includes('ai_quota_exceeded')) reasons.push('ai_quota_exceeded')
    if (!capability || (capability.ready && reasons.length === 0)) return null
    const quotaDetail = quota?.exceeded
        ? {
              scope: quota.exceeded,
              limit: quota.exceeded === 'user' ? quota.user?.limit : quota.total.limit,
              used: quota.exceeded === 'user' ? quota.user?.used : quota.total.used,
              resets_at: quota.resets_at,
          }
        : null
    return (
        <Notice tone="warning" title={t('p6.ai.notReadyTitle')} testId={testId} className={className}>
            <ul className="list-disc ps-5" data-reasons={reasons.join(',')}>
                {reasons.map((r) => (
                    <li key={r} data-reason={r}>
                        {aiReasonText(r, r === 'ai_quota_exceeded' ? quotaDetail : null, t)}
                    </li>
                ))}
            </ul>
            <div className="mt-1 text-[12px] text-gray-600">{t('p6.ai.nothingFabricated')}</div>
            <AiAdminLink />
        </Notice>
    )
}

/**
 * The answer to a provider action: an honest refusal (every reason it carries) or another
 * failure in one sentence. Nothing is simulated in its place.
 */
export const AiErrorNotice = ({ error, testId = 'p6-ai-error', className }: { error: unknown; testId?: string; className?: string }) => {
    const { t } = useTranslation()
    if (!error) return null
    if (isAiRefusal(error)) {
        const reasons = refusalReasons(error)
        const detail = isV2Error(error) ? error.detail : null
        return (
            <Notice tone="warning" title={t('p6.ai.refusedTitle')} testId={testId} className={className}>
                <ul className="list-disc ps-5" data-code={isV2Error(error) ? error.code : ''} data-reasons={reasons.join(',')}>
                    {reasons.map((r) => (
                        <li key={r} data-reason={r}>
                            {aiReasonText(r, detail, t)}
                        </li>
                    ))}
                </ul>
                <div className="mt-1 text-[12px] text-gray-600">{t('p6.ai.nothingFabricated')}</div>
                <AiAdminLink />
            </Notice>
        )
    }
    return (
        <Notice tone="danger" testId={testId} className={className}>
            <span data-code={isV2Error(error) ? error.code : ''}>{describeP6Error(error, t)}</span>
        </Notice>
    )
}

/** "AI-generated. Review and edit it before saving" (the API's own notice when it sends one). */
export const AiGeneratedNotice = ({ text, className }: { text?: string | null; className?: string }) => {
    const { t } = useTranslation()
    return (
        <div
            role="note"
            data-testid="p6-ai-generated"
            className={classNames(
                'flex items-start gap-2 rounded-lg border border-azure-100 bg-info-tint px-3 py-2 text-[13px] text-gray-800 dark:border-night-border dark:bg-night-raised dark:text-night-body',
                className
            )}
        >
            <Sparkles aria-hidden size={16} className="mt-0.5 shrink-0 text-azure-600" />
            <span>{text || t('p6.ai.generatedNotice')}</span>
        </div>
    )
}

/** A percentage with its fraction; null shows "—" and says why. */
export const RatioText = ({ ratio, testId, className }: { ratio: QcRatio | undefined; testId?: string; className?: string }) => {
    const { t } = useTranslation()
    if (!ratio) {
        return (
            <span className={classNames('text-gray-500', className)} data-testid={testId} data-percent="" title={String(t('p6.qc.noPercentHint'))}>
                —
            </span>
        )
    }
    return (
        <span className={classNames('tabular-nums', className)} dir="ltr" data-testid={testId} data-percent={ratio.percent} title={formatFraction(ratio)}>
            {formatPercent(ratio)}
        </span>
    )
}

export const QcStatusBadge = ({ status, size }: { status: QcStatus; size?: Size }) => {
    const { t } = useTranslation()
    return (
        <StatusPill tone={QC_STATUS_TONE[status] || 'neutral'} size={size}>
            <span data-qc-status={status}>{qcStatusLabel(t, status)}</span>
        </StatusPill>
    )
}

export const ResultBadge = ({ result, size }: { result: QcResult | string | null | undefined; size?: Size }) => {
    const { t } = useTranslation()
    if (!result) return <span className="text-gray-500">—</span>
    return (
        <StatusPill tone={QC_RESULT_TONE[result as QcResult] || 'neutral'} size={size}>
            <span data-result={result}>{resultLabel(t, result)}</span>
        </StatusPill>
    )
}

export const CallOutcomeBadge = ({ outcome, size }: { outcome: AiCallOutcome; size?: Size }) => {
    const { t } = useTranslation()
    return (
        <StatusPill tone={CALL_OUTCOME_TONE[outcome] || 'neutral'} size={size}>
            <span data-outcome={outcome}>{t(`p6.callOutcome.${outcome}`, { defaultValue: outcome })}</span>
        </StatusPill>
    )
}

export const DraftStatusBadge = ({ status, size }: { status: DraftStatus; size?: Size }) => {
    const { t } = useTranslation()
    return (
        <StatusPill tone={DRAFT_STATUS_TONE[status] || 'neutral'} size={size}>
            <span data-draft-status={status}>{t(`p6.draftStatus.${status}`, { defaultValue: status })}</span>
        </StatusPill>
    )
}

/** Quota line: "12 of 50 used today (resets 2026-09-25 00:00)". */
export const QuotaLine = ({ quota }: { quota: AiQuota | null | undefined }) => {
    const { t } = useTranslation()
    if (!quota) return null
    return (
        <div className="text-[12.5px] text-gray-600" data-testid="p6-quota-line" data-exceeded={quota.exceeded || ''}>
            {quota.user ? t('p6.quota.userLine', { used: quota.user.used, limit: quota.user.limit }) : null}
            {quota.user ? ' · ' : null}
            {t('p6.quota.totalLine', { used: quota.total.used, limit: quota.total.limit })} ·{' '}
            {t('p6.quota.resets', { at: formatRiyadhDateTime(quota.resets_at) })}
        </div>
    )
}

/**
 * A fully controlled checkbox with its label (the shared `Checkbox` keeps its own state, which
 * drifts when a form is reset from outside). Same look: the design system's `checkbox` class.
 */
export const CheckField = ({
    checked,
    onChange,
    children,
    testId,
    disabled,
    className,
}: {
    checked: boolean
    onChange: (checked: boolean) => void
    children?: React.ReactNode
    testId?: string
    disabled?: boolean
    className?: string
}) => (
    <label className={classNames('inline-flex cursor-pointer items-start gap-2', disabled && 'cursor-not-allowed opacity-60', className)}>
        <input
            type="checkbox"
            className="checkbox mt-0.5 shrink-0 text-navy-600"
            checked={checked}
            disabled={disabled}
            onChange={(e) => onChange(e.target.checked)}
            data-testid={testId}
        />
        {children !== undefined && <span className="min-w-0">{children}</span>}
    </label>
)
