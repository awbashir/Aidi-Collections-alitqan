/**
 * A vertical event timeline for the money history (payments and promises).
 * The rail sits on the leading edge (border-inline-start), so it flips in RTL.
 */
import classNames from 'classnames'
import { useTranslation } from 'react-i18next'
import { RiyadhTime } from '@/components/v2/Display'
import type { ReactNode } from 'react'

export type TimelineTone = 'success' | 'warning' | 'danger' | 'info' | 'neutral'

export type TimelineEntry = {
    key: string
    title: ReactNode
    at: string | null
    actor?: string | null
    source?: string | null
    tone?: TimelineTone
    children?: ReactNode
}

/* Each tone has its own glyph shape (dot, diamond, bar, ring), like StatusPill. */
const DOT: Record<TimelineTone, string> = {
    success: 'mt-[6px] h-2 w-2 rounded-full bg-emerald-600',
    warning: 'mt-[6px] h-2 w-2 rotate-45 bg-amber-600',
    danger: 'mt-[9px] h-[3px] w-2 bg-danger',
    info: 'mt-[6px] h-2 w-2 rounded-full border-2 border-azure-600 bg-white dark:bg-night-card',
    neutral: 'mt-[6px] h-2 w-2 rounded-full bg-gray-400',
}

export const MoneyTimeline = ({
    entries,
    empty,
    testId,
}: {
    entries: TimelineEntry[]
    empty?: ReactNode
    testId?: string
}) => {
    const { t } = useTranslation()
    if (entries.length === 0) {
        return <div className="text-[13px] text-gray-600">{empty || t('p1.money.noEvents')}</div>
    }
    return (
        <ol className="relative ms-1.5 border-s border-gray-300 dark:border-night-border" data-testid={testId}>
            {entries.map((e) => (
                <li key={e.key} className="relative mb-3 ps-4 last:mb-0" data-testid="p1-timeline-entry">
                    <span
                        aria-hidden
                        className={classNames('absolute -start-[4.5px]', DOT[e.tone || 'neutral'])}
                    />
                    <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
                        <span className="text-[13px] font-extrabold text-navy-600 dark:text-gray-100">{e.title}</span>
                        <RiyadhTime iso={e.at} className="text-[12px] text-gray-600" />
                        {e.actor && <span className="text-[12px] text-gray-600">· {e.actor}</span>}
                        {e.source && (
                            <span className="rounded bg-gray-100 px-1.5 text-[11px] font-extrabold text-gray-600 dark:bg-night-raised">
                                {t(`p1.money.source.${e.source}`, { defaultValue: e.source })}
                            </span>
                        )}
                    </div>
                    {e.children && <div className="mt-0.5 text-[12.5px] text-gray-700 dark:text-night-body">{e.children}</div>}
                </li>
            ))}
        </ol>
    )
}

/** Eyebrow heading for a block inside a detail panel. */
export const BlockTitle = ({ children, action }: { children: ReactNode; action?: ReactNode }) => (
    <div className="mb-2 mt-4 flex items-center justify-between gap-2 first:mt-0">
        <div className="text-eyebrow">{children}</div>
        {action}
    </div>
)
