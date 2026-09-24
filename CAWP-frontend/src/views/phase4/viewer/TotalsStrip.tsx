/**
 * The exact totals of the full filtered result (never the page), read in the
 * same snapshot as the page. Money is shown exactly as the API returns it
 * (a decimal string, re-grouped as text only). A nullable total that comes
 * back null is "Unknown", never 0.
 */
import { useTranslation } from 'react-i18next'
import { formatCount, formatMoney } from '@/utils/v2/format'
import type { ReportDefinition, ReportRows } from '@/services/v2'
import { pick } from '../reportUtils'

export default function TotalsStrip({ def, data }: { def: ReportDefinition; data: ReportRows | undefined }) {
    const { t, i18n } = useTranslation()
    const ar = (i18n.language || 'ar').startsWith('ar')
    return (
        <section
            className="mb-4 rounded-lg border border-gray-300 bg-white px-5 py-4 dark:border-night-border dark:bg-night-card"
            aria-label={String(t('p4.totals.title'))}
            data-testid="p4-totals"
        >
            <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
                <div className="text-eyebrow">{t('p4.totals.title')}</div>
                <div className="text-[12px] text-gray-600">{t('p4.totals.hint')}</div>
            </div>
            <div className="grid grid-cols-2 gap-x-6 gap-y-3 sm:grid-cols-3 lg:grid-cols-4 2xl:grid-cols-6">
                {def.totals.map((tot) => {
                    const raw = data?.totals?.[tot.key]
                    const unknown = data !== undefined && (raw === null || raw === undefined) && tot.key in (data?.totals || {})
                    const missing = data !== undefined && !(tot.key in (data?.totals || {}))
                    return (
                        <div
                            key={tot.key}
                            className="min-w-0"
                            data-testid="p4-total"
                            data-key={tot.key}
                            data-value={raw === null ? 'null' : raw === undefined ? '' : String(raw)}
                        >
                            <div className="truncate text-[12px] text-gray-600" title={pick(tot.labels, ar)}>
                                {pick(tot.labels, ar)}
                                {tot.type === 'money' && tot.currency ? ` (${t('p4.sar')})` : ''}
                            </div>
                            <div
                                dir="ltr"
                                className={
                                    'text-[18px] font-extrabold leading-tight tabular-nums text-navy-600 dark:text-gray-100 ' +
                                    (ar ? 'text-right' : 'text-left')
                                }
                            >
                                {data === undefined ? (
                                    <span className="text-gray-400">…</span>
                                ) : unknown || missing ? (
                                    <span className="text-[14px] text-gray-600" title={String(t('p4.totals.unknownTitle'))} data-testid="p4-total-unknown">
                                        {t('p4.totals.unknown')}
                                    </span>
                                ) : tot.type === 'money' ? (
                                    formatMoney(String(raw))
                                ) : (
                                    formatCount(typeof raw === 'number' ? raw : Number(raw))
                                )}
                            </div>
                        </div>
                    )
                })}
            </div>
        </section>
    )
}
