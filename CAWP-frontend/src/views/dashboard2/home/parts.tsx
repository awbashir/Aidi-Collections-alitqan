/**
 * Building blocks of the home dashboard: a card frame that is its own error boundary, a lazy
 * section (mounts its children, and so starts their requests, only near the viewport), the
 * interactive KPI card and its sparkline.
 */
import classNames from 'classnames'
import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'
import { HiArrowSmDown, HiArrowSmUp, HiOutlineExternalLink } from 'react-icons/hi'
import Chart from '@/components/shared/Chart'
import ErrorBoundary from '@/components/shared/ErrorBoundary'
import { EmptyState, ErrorState, SkeletonLines } from '@/components/v2/States'
import { COLOR_2, COLOR_HIGHLIGHT } from '@/constants/chart.constant'
import { arr, num, useInView } from './safe'

/** Card frame (radius 8, 1px border, no shadow) wrapped in its own error boundary. */
export const HomeCard = ({
    title,
    subtitle,
    actions,
    children,
    testId,
    className,
    lead,
    resetKeys,
}: {
    title?: ReactNode
    subtitle?: ReactNode
    actions?: ReactNode
    children: ReactNode
    testId?: string
    className?: string
    lead?: boolean
    resetKeys?: unknown[]
}) => (
    <ErrorBoundary name={typeof title === 'string' ? title : undefined} resetKeys={resetKeys} className={className}>
        <section
            data-testid={testId}
            className={classNames(
                'min-w-0 rounded-lg border border-gray-300 bg-white p-5 dark:border-night-border dark:bg-night-card',
                lead && 'card-lead',
                className
            )}
        >
            {(title || actions) && (
                <div className="mb-3 flex flex-wrap items-start justify-between gap-2">
                    <div className="min-w-0">
                        {title && <h5 className="mb-0 text-[15px] font-extrabold text-navy-600 dark:text-gray-100">{title}</h5>}
                        {subtitle && <div className="mt-0.5 text-[12px] text-gray-600 dark:text-night-muted">{subtitle}</div>}
                    </div>
                    {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
                </div>
            )}
            {children}
        </section>
    </ErrorBoundary>
)

/** Loading / error / empty around a card body. */
export const CardBody = ({
    loading,
    error,
    empty,
    emptyTitle,
    onRetry,
    rows = 5,
    children,
}: {
    loading: boolean
    error: unknown
    empty?: boolean
    emptyTitle?: ReactNode
    onRetry?: () => void
    rows?: number
    children: () => ReactNode
}) => {
    const { t } = useTranslation()
    if (error) return <ErrorState error={error} onRetry={onRetry} className="py-6" />
    if (loading) return <SkeletonLines rows={rows} className="py-2" />
    if (empty) return <EmptyState title={emptyTitle || t('p10.homedash.noData')} className="py-6" />
    return <>{children()}</>
}

/** Renders `children` only once it is near the viewport; keeps the space meanwhile. */
export const LazySection = ({ children, minHeight = 320, className }: { children: ReactNode; minHeight?: number; className?: string }) => {
    const { ref, seen } = useInView<HTMLDivElement>()
    return (
        <div ref={ref} className={className} style={seen ? undefined : { minHeight }}>
            {seen ? children : null}
        </div>
    )
}

export const Sparkline = ({ data, highlight }: { data?: number[] | null; highlight?: boolean }) => {
    const points = arr(data).map((v) => num(v))
    if (points.length < 2) return null
    return (
        <div className="pointer-events-none -mx-1 mt-2 h-[40px]" aria-hidden>
            <Chart
                type="area"
                height={40}
                series={[{ name: '', data: points }]}
                customOptions={{
                    chart: { sparkline: { enabled: true }, animations: { enabled: false }, toolbar: { show: false } },
                    stroke: { width: 2, curve: 'smooth' },
                    fill: { type: 'gradient', gradient: { opacityFrom: 0.35, opacityTo: 0.02 } },
                    colors: [highlight ? COLOR_HIGHLIGHT : COLOR_2],
                    tooltip: { enabled: false },
                    dataLabels: { enabled: false },
                }}
            />
        </div>
    )
}

/** ▲ 12.5% / ▼ 3.0% vs the previous period; "—" when the previous period was zero (unknown). */
export const Trend = ({ value, upIsGood = true }: { value?: number | null; upIsGood?: boolean }) => {
    const { t } = useTranslation()
    if (typeof value !== 'number' || !Number.isFinite(value)) {
        return (
            <span className="text-[12px] text-gray-500" title={t('p10.homedash.trendUnknown') as string}>
                —
            </span>
        )
    }
    const up = value > 0
    const flat = value === 0
    const good = flat ? null : up === upIsGood
    return (
        <span
            className={classNames(
                'inline-flex items-center gap-0.5 text-[12px] font-extrabold tabular-nums',
                good === null ? 'text-gray-600' : good ? 'text-success' : 'text-danger'
            )}
            title={t('p10.homedash.vsPrevious') as string}
            dir="ltr"
        >
            {flat ? null : up ? <HiArrowSmUp aria-hidden /> : <HiArrowSmDown aria-hidden />}
            {Math.abs(value).toFixed(1)}%
        </span>
    )
}

/**
 * Interactive KPI card: value, trend vs the previous period, sparkline, a details panel on
 * hover / keyboard focus, and a same-tab click-through when `to` is given (only passed when
 * the user may open that screen).
 */
export const KpiCard = ({
    label,
    value,
    unit,
    trend,
    upIsGood,
    spark,
    details,
    to,
    lead,
    testId,
    footer,
}: {
    label: ReactNode
    value: ReactNode
    unit?: ReactNode
    trend?: number | null
    upIsGood?: boolean
    spark?: number[] | null
    details?: ReactNode
    to?: string
    lead?: boolean
    testId?: string
    footer?: ReactNode
}) => {
    const navigate = useNavigate()
    const { t } = useTranslation()
    const open = () => {
        if (to) navigate(to)
    }
    return (
        <ErrorBoundary name={typeof label === 'string' ? label : undefined}>
            <div
                data-testid={testId}
                role={to ? 'link' : undefined}
                tabIndex={to || details ? 0 : undefined}
                onClick={open}
                onKeyDown={(e) => {
                    if (to && (e.key === 'Enter' || e.key === ' ')) {
                        e.preventDefault()
                        open()
                    }
                }}
                className={classNames(
                    'group relative min-w-0 rounded-lg border border-gray-300 bg-white px-4 py-3 transition dark:border-night-border dark:bg-night-card',
                    'focus:outline-none focus-visible:ring-2 focus-visible:ring-azure-600',
                    to && 'cursor-pointer hover:border-azure-600 hover:shadow-sm dark:hover:border-azure-300',
                    lead && 'card-lead'
                )}
            >
                <div className="flex items-start justify-between gap-2">
                    <div className="text-eyebrow">{label}</div>
                    {to && (
                        <HiOutlineExternalLink
                            aria-label={t('p10.homedash.open') as string}
                            className="shrink-0 text-[14px] text-gray-400 group-hover:text-azure-600"
                        />
                    )}
                </div>
                <div className="mt-1.5 flex flex-wrap items-end gap-x-2 gap-y-1">
                    <span className="text-[22px] font-extrabold leading-[1.1] tracking-[-0.02em] text-navy-600 tabular-nums dark:text-night-strong lg:text-[26px]">
                        {value}
                    </span>
                    {unit ? <span className="pb-0.5 text-[12px] text-gray-600 dark:text-night-muted">{unit}</span> : null}
                    {trend !== undefined && (
                        <span className="pb-0.5">
                            <Trend value={trend} upIsGood={upIsGood} />
                        </span>
                    )}
                </div>
                {footer && <div className="mt-1 text-[12px] text-gray-600 dark:text-night-muted">{footer}</div>}
                <Sparkline data={spark} />
                {details && (
                    <div
                        role="tooltip"
                        className="invisible absolute inset-x-0 top-full z-20 mt-1 rounded-lg border border-gray-300 bg-white p-3 text-[12.5px] text-gray-700 opacity-0 shadow-lg transition group-hover:visible group-hover:opacity-100 group-focus-visible:visible group-focus-visible:opacity-100 dark:border-night-border dark:bg-night-raised dark:text-night-body"
                    >
                        {details}
                    </div>
                )}
            </div>
        </ErrorBoundary>
    )
}

/** One "label …… value" line of a details panel. */
export const DetailLine = ({ label, value }: { label: ReactNode; value: ReactNode }) => (
    <div className="flex items-center justify-between gap-3 py-0.5">
        <span className="text-gray-600 dark:text-night-muted">{label}</span>
        <span className="font-extrabold tabular-nums text-navy-600 dark:text-gray-100" dir="ltr">
            {value}
        </span>
    </div>
)
