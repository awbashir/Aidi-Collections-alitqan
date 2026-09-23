import classNames from 'classnames'
import type { ReactNode } from 'react'

export type StatTileProps = {
    /** 11px / 800 / +0.12em eyebrow above the value. */
    label: ReactNode
    /** 32px / 800 navy, tabular. */
    value: ReactNode
    /** Currency or unit, 12.5px muted, sits on the value's baseline. */
    unit?: ReactNode
    /**
     * The one measure the section exists to report. Carries the 3px `#FF6600`
     * rail on the leading edge (`.card-lead`, RTL aware). **One per row.**
     */
    lead?: boolean
    className?: string
}

/**
 * Aidi Enterprise stat tile — white, radius 8, 1px `#D7E1E8`, no shadow.
 */
const StatTile = ({ label, value, unit, lead, className }: StatTileProps) => {
    return (
        <div
            className={classNames(
                'rounded-lg border border-gray-300 dark:border-night-border bg-white dark:bg-night-card px-4 py-3',
                lead && 'card-lead',
                className
            )}
        >
            <div className="text-eyebrow">{label}</div>
            <div className="mt-1.5 flex items-end gap-1.5">
                <span className="text-[24px] lg:text-[32px] leading-[1.1] font-extrabold tracking-[-0.02em] text-navy-600 dark:text-night-strong tabular-nums">
                    {value}
                </span>
                {unit ? (
                    <span className="pb-1 text-[12.5px] text-gray-600 dark:text-night-muted">
                        {unit}
                    </span>
                ) : null}
            </div>
        </div>
    )
}

export default StatTile
