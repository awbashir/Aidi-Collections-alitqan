/**
 * Small pieces shared by the /promises and /payment-approvals screens.
 */
import classNames from 'classnames'
import { useTranslation } from 'react-i18next'
import { HiOutlineX } from 'react-icons/hi'
import Input from '@/components/ui/Input'
import { MoneyText } from '@/components/v2/Display'
import type { ReactNode } from 'react'

/** A labelled civil-date input for a filter bar. */
export const DateFilter = ({
    label,
    value,
    onChange,
    min,
    max,
    testId,
}: {
    label: ReactNode
    value: string | undefined
    onChange: (v: string | undefined) => void
    min?: string
    max?: string
    testId?: string
}) => (
    <div className="w-[160px]" data-testid={testId}>
        <div className="text-eyebrow mb-1">{label}</div>
        <Input
            size="sm"
            type="date"
            dir="ltr"
            value={value || ''}
            min={min}
            max={max}
            onChange={(e) => onChange(e.target.value || undefined)}
        />
    </div>
)

/** A KPI tile: eyebrow + exact money, optional lead rail (one per row). */
export const MoneyKpi = ({
    label,
    value,
    sub,
    lead,
    testId,
}: {
    label: ReactNode
    value: string | undefined
    sub?: ReactNode
    lead?: boolean
    testId?: string
}) => (
    <div
        className={classNames(
            'min-w-0 rounded-lg border border-gray-300 bg-white px-5 py-4 dark:border-night-border dark:bg-night-card',
            lead && 'card-lead'
        )}
        data-testid={testId}
        data-value={value ?? ''}
    >
        <div className="text-eyebrow mb-1">{label}</div>
        <MoneyText value={value} className="text-[24px] font-extrabold leading-tight text-navy-600 dark:text-gray-100" />
        {sub && <div className="mt-1 text-[12px] text-gray-600 tabular-nums">{sub}</div>}
    </div>
)

/** The right-hand (inline-end) detail panel next to a list. */
export const SidePanel = ({
    title,
    onClose,
    children,
    testId,
}: {
    title: ReactNode
    onClose: () => void
    children: ReactNode
    testId?: string
}) => {
    const { t } = useTranslation()
    return (
        <aside
            className="self-start rounded-lg border border-gray-300 bg-white dark:border-night-border dark:bg-night-card xl:sticky xl:top-4"
            data-testid={testId}
        >
            <div className="flex items-center justify-between gap-2 border-b border-gray-300 px-5 py-3 dark:border-night-border">
                <div className="min-w-0 truncate text-[15px] font-extrabold text-navy-600 dark:text-gray-100">{title}</div>
                <button
                    type="button"
                    onClick={onClose}
                    aria-label={String(t('p1.action.close'))}
                    className="rounded p-1 text-gray-600 hover:bg-gray-100 dark:hover:bg-night-raised"
                    data-testid="p1-panel-close"
                >
                    <HiOutlineX />
                </button>
            </div>
            <div className="max-h-[calc(100vh-140px)] overflow-y-auto px-5 py-4">{children}</div>
        </aside>
    )
}
