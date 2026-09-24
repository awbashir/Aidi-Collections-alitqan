/**
 * Display pieces shared by the target screens (Team screen, targets panel):
 * the in-force target text ("2.5% of 15,000.10 · from 2026-08 · set in v2").
 *
 * Money and percentages stay decimal strings; nothing here parses them into a
 * float. Which target is in force, and whether a month is locked, is the API's.
 */
import { useTranslation } from 'react-i18next'
import { formatMoney } from '@/utils/v2/format'
import type { TargetInForce, TargetKind, TargetSubject } from '@/services/v2'
import { sourceLabel } from './targetUtils'

/* ---------------------------------------------------------------- target text */

/** The value of a target in its own unit: "2,500.75", "2.5%", or the "none" label. */
export const TargetValueText = ({
    kind,
    value,
    subject,
    percentBase,
}: {
    kind: TargetKind
    value: string
    subject?: TargetSubject
    /** The base a percent target applies to (achievement rows only). */
    percentBase?: string | null
}) => {
    const { t } = useTranslation()
    if (kind === 'none')
        return <span>{subject === 'contract' ? t('p3.targets.kindNoneContract') : t('p3.targets.kindNoneUser')}</span>
    if (kind === 'percent')
        return (
            <span>
                <span dir="ltr" className="tabular-nums">
                    {value}%
                </span>
                {percentBase !== undefined && percentBase !== null && (
                    <>
                        {' '}
                        {t('p3.targets.percentOf')}{' '}
                        <span dir="ltr" className="tabular-nums">
                            {formatMoney(percentBase)}
                        </span>
                    </>
                )}
            </span>
        )
    return (
        <span dir="ltr" className="tabular-nums">
            {formatMoney(value)}
        </span>
    )
}

/**
 * "Amount 2,500.75 · from 2026-10 · set in v2" for a target in force, or
 * "No target history" when the month is before the history starts.
 */
export const InForceText = ({
    target,
    subject,
    percentBase,
    showKind = true,
    className,
}: {
    target: TargetInForce | undefined
    subject?: TargetSubject
    percentBase?: string | null
    showKind?: boolean
    className?: string
}) => {
    const { t } = useTranslation()
    if (!target) return <span className={className}>—</span>
    if (!target.known)
        return (
            <span className={className} data-testid="p3-no-target-history">
                {t('p3.targets.noHistory')}
            </span>
        )
    return (
        <span className={className}>
            {showKind && target.kind !== 'none' && <>{t(`p3.targets.kind.${target.kind}`)} </>}
            <TargetValueText kind={target.kind} value={target.value} subject={subject} percentBase={percentBase} />
            {target.from_month && (
                <>
                    {' · '}
                    {t('p3.targets.fromMonth')}{' '}
                    <span dir="ltr" className="tabular-nums">
                        {target.from_month}
                    </span>
                </>
            )}
            {' · '}
            {sourceLabel(t, target.source)}
        </span>
    )
}
