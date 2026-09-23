/** The exact balance breakdown the API sends with `amount_exceeds_balance` (409). */
import { useTranslation } from 'react-i18next'
import { isV2Error } from '@/services/v2'
import { MoneyText } from '@/components/v2/Display'

type BalanceDetail = { original?: string; approved?: string; outstanding?: string; amount?: string }

const BalanceBreakdown = ({ error }: { error: unknown }) => {
    const { t } = useTranslation()
    if (!isV2Error(error) || error.code !== 'amount_exceeds_balance') return null
    const d = (error.detail || {}) as BalanceDetail
    return (
        <div
            className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 rounded-md border border-gray-200 px-3 py-2 text-[12.5px] sm:grid-cols-4 dark:border-night-border"
            data-testid="p1-balance-breakdown"
        >
            {(['original', 'approved', 'outstanding', 'amount'] as const).map((k) =>
                d[k] !== undefined ? (
                    <div key={k}>
                        <div className="text-eyebrow">{t(`p1.balance.${k === 'amount' ? 'requested' : k}`)}</div>
                        <MoneyText value={d[k]} strong />
                    </div>
                ) : null
            )}
        </div>
    )
}

export default BalanceBreakdown
