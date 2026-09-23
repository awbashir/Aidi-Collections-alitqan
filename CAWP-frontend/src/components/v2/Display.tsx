/**
 * Small display atoms for the Phase 1 screens: exact money, Riyadh time,
 * identifiers inside Arabic text.
 */
import classNames from 'classnames'
import type { ReactNode } from 'react'
import { formatMoney, formatRiyadhDateTime, isNegativeMoney } from '@/utils/v2/format'

/** Exact money text. The currency sits in the column header, not in the cell. */
export const MoneyText = ({
    value,
    className,
    strong,
}: {
    value: string | null | undefined
    className?: string
    strong?: boolean
}) => (
    <span
        dir="ltr"
        className={classNames(
            'tabular-nums whitespace-nowrap',
            strong && 'font-extrabold text-navy-600 dark:text-gray-100',
            isNegativeMoney(value || '') && 'text-danger',
            className
        )}
    >
        {formatMoney(value)}
    </span>
)

/** An instant shown as Asia/Riyadh civil time, full ISO in the tooltip. */
export const RiyadhTime = ({ iso, className }: { iso: string | null | undefined; className?: string }) => (
    <span dir="ltr" title={iso || undefined} className={classNames('tabular-nums whitespace-nowrap', className)}>
        {formatRiyadhDateTime(iso)}
    </span>
)

/** A civil date (already a Riyadh date). */
export const CivilDate = ({ value, className }: { value: string | null | undefined; className?: string }) => (
    <span dir="ltr" className={classNames('tabular-nums whitespace-nowrap', className)}>
        {value || '—'}
    </span>
)

/** A Latin identifier (account number, identity, phone) that must not be reordered by bidi. */
export const Ltr = ({ children, className, title }: { children: ReactNode; className?: string; title?: string }) => (
    <span dir="ltr" title={title} className={classNames('tabular-nums', className)}>
        {children}
    </span>
)

/** Eyebrow + value pair used in the detail panels. */
export const Field = ({ label, children, className }: { label: ReactNode; children: ReactNode; className?: string }) => (
    <div className={classNames('min-w-0', className)}>
        <div className="text-eyebrow mb-1">{label}</div>
        <div className="text-[14px] text-gray-700 dark:text-night-body break-words">{children}</div>
    </div>
)
