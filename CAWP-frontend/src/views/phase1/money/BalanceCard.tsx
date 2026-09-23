/**
 * Case balance (GET /v2/cases/<id>/balance), every figure exact as the API
 * returns it. Outstanding = original − approved (non-reversed); pending,
 * rejected and reversed money is shown separately and never counts.
 *
 * If the caller lacks every money read key (403), `fallback` (the legacy card)
 * renders instead. `refreshKey` reloads after a mutation elsewhere on the page.
 */
import classNames from 'classnames'
import { useTranslation } from 'react-i18next'
import type { ReactNode } from 'react'
import { moneyApi, isV2Error } from '@/services/v2'
import { useV2Query } from '@/utils/v2/hooks'
import { formatCount } from '@/utils/v2/format'
import { MoneyText } from '@/components/v2/Display'
import { PlainBadge } from '@/components/v2/Badges'
import { ErrorState, SkeletonLines } from '@/components/v2/States'
import { absMoney } from './moneyMath'

export type BalanceCardProps = {
    caseId: string
    fallback?: ReactNode
    refreshKey?: number
}

const Kpi = ({
    label,
    value,
    count,
    testId,
}: {
    label: ReactNode
    value: string
    count?: number
    testId: string
}) => {
    const { t } = useTranslation()
    return (
        <div className="min-w-0" data-testid={testId} data-value={value}>
            <div className="text-eyebrow mb-1">{label}</div>
            <MoneyText value={value} className="text-[17px] font-extrabold text-navy-600 dark:text-gray-100" />
            {count !== undefined && (
                <div className="mt-0.5 text-[12px] text-gray-600 tabular-nums">
                    {t('p1.money.paymentsCount', { count, formatted: formatCount(count) })}
                </div>
            )}
        </div>
    )
}

const BalanceCard = ({ caseId, fallback, refreshKey }: BalanceCardProps) => {
    const { t } = useTranslation()
    const q = useV2Query((signal) => moneyApi.balance(caseId, signal), [caseId, refreshKey])
    const b = q.data

    if (q.error && isV2Error(q.error) && q.error.status === 403 && fallback !== undefined) {
        return <>{fallback}</>
    }

    return (
        <div
            className="rounded-lg border border-gray-300 bg-white dark:border-night-border dark:bg-night-card"
            data-testid="p1-balance-card"
        >
            <div className="flex flex-wrap items-center justify-between gap-2 border-b border-gray-300 px-5 py-3 dark:border-night-border">
                <div className="text-[15px] font-extrabold text-navy-600 dark:text-gray-100">
                    {t('p1.balance.title')}{' '}
                    <span className="text-[12.5px] font-normal text-gray-600">({t('p1.currencyCode')})</span>
                </div>
                {b?.overpaid && (
                    <span data-testid="p1-balance-overpaid">
                        <PlainBadge tone="danger">{t('p1.balance.overpaid')}</PlainBadge>
                    </span>
                )}
            </div>
            {q.loading && !b ? (
                <div className="p-5">
                    <SkeletonLines rows={3} />
                </div>
            ) : q.error && !b ? (
                <ErrorState error={q.error} onRetry={q.reload} />
            ) : b ? (
                <div className="flex flex-col gap-4 p-5 md:flex-row">
                    <div
                        className={classNames(
                            'card-lead min-w-[200px] rounded-md border border-gray-200 px-4 py-3 dark:border-night-border'
                        )}
                        data-testid="p1-balance-outstanding"
                        data-value={b.outstanding}
                        data-overpaid={b.overpaid ? 'true' : 'false'}
                    >
                        <div className="text-eyebrow mb-1">
                            {b.overpaid ? t('p1.balance.overpaidBy') : t('p1.balance.outstanding')}
                        </div>
                        <div
                            dir="ltr"
                            className={classNames(
                                'text-[28px] font-extrabold leading-tight tabular-nums text-start',
                                b.overpaid ? 'text-danger' : 'text-navy-600 dark:text-gray-100'
                            )}
                        >
                            <MoneyText value={b.overpaid ? absMoney(b.outstanding) : b.outstanding} className="text-inherit" />
                        </div>
                        {b.overpaid && (
                            <div className="mt-1 text-[12px] text-gray-600">
                                {t('p1.balance.overpaidHint')} <MoneyText value={b.outstanding} />
                            </div>
                        )}
                    </div>
                    <div className="grid flex-1 grid-cols-2 gap-4 sm:grid-cols-3">
                        <Kpi label={t('p1.balance.original')} value={b.original} testId="p1-balance-original" />
                        <Kpi
                            label={t('p1.balance.approved')}
                            value={b.approved}
                            count={b.counts.approved}
                            testId="p1-balance-approved"
                        />
                        <Kpi
                            label={t('p1.balance.pending')}
                            value={b.pending}
                            count={b.counts.pending}
                            testId="p1-balance-pending"
                        />
                        <Kpi
                            label={t('p1.balance.reversed')}
                            value={b.reversed}
                            count={b.counts.reversed}
                            testId="p1-balance-reversed"
                        />
                        <Kpi
                            label={t('p1.balance.rejected')}
                            value={b.rejected}
                            count={b.counts.rejected}
                            testId="p1-balance-rejected"
                        />
                    </div>
                </div>
            ) : null}
            {b && (
                <div className="border-t border-gray-300 px-5 py-2.5 text-[12px] text-gray-600 dark:border-night-border">
                    {t('p1.balance.rule')}
                </div>
            )}
        </div>
    )
}

export default BalanceCard
