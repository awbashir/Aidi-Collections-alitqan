/**
 * Building blocks shared by the Phase 1 list screens: page head, count tabs,
 * scope filters (owner / contract / portfolio), table frame and pager.
 */
import classNames from 'classnames'
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { HiOutlineRefresh } from 'react-icons/hi'
import Button from '@/components/ui/Button'
import Select from '@/components/ui/Select'
import Pagination from '@/components/ui/Pagination'
import Tabs from '@/components/ui/Tabs'
import { lookups } from '@/services/v2'
import type { Option } from '@/services/v2'
import { formatCount } from '@/utils/v2/format'
import type { ReactNode } from 'react'

export const PageHead = ({
    title,
    subtitle,
    actions,
    onRefresh,
    refreshing,
}: {
    title: ReactNode
    subtitle?: ReactNode
    actions?: ReactNode
    onRefresh?: () => void
    refreshing?: boolean
}) => {
    const { t } = useTranslation()
    return (
        <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
            <div className="min-w-0">
                <h3 className="mb-0 text-[20px] font-extrabold text-navy-600 dark:text-gray-100">{title}</h3>
                {subtitle && <div className="mt-1 text-[12.5px] text-gray-600 tabular-nums">{subtitle}</div>}
            </div>
            <div className="flex flex-wrap items-center gap-2">
                {actions}
                {onRefresh && (
                    <Button
                        size="sm"
                        icon={<HiOutlineRefresh />}
                        loading={refreshing}
                        onClick={onRefresh}
                        data-testid="p1-refresh"
                    >
                        {t('p1.action.refresh')}
                    </Button>
                )}
            </div>
        </div>
    )
}

export type CountTab<V extends string> = {
    value: V
    label: ReactNode
    count?: number | null
    sub?: ReactNode
}

/** Underline tabs with exact counts (the page's one orange underline). */
export function CountTabs<V extends string>({
    tabs,
    value,
    onChange,
    secondary,
    testId,
}: {
    tabs: CountTab<V>[]
    value: V
    onChange: (v: V) => void
    secondary?: boolean
    testId?: string
}) {
    return (
        <Tabs value={value} onChange={(v) => onChange(v as V)}>
            <Tabs.TabList className={classNames(secondary && 'tab-list-secondary')} data-testid={testId}>
                {tabs.map((tab) => (
                    <Tabs.TabNav key={tab.value} value={tab.value} data-tab={tab.value}>
                        <span className="flex flex-col items-center gap-0.5 py-1">
                            <span className="flex items-center gap-2">
                                <span>{tab.label}</span>
                                {tab.count !== undefined && (
                                    <span
                                        dir="ltr"
                                        className="rounded-pill bg-slate-100 px-2 text-[11.5px] font-extrabold tabular-nums text-navy-600 dark:bg-night-raised dark:text-gray-100"
                                        data-count={tab.count ?? ''}
                                    >
                                        {formatCount(tab.count)}
                                    </span>
                                )}
                            </span>
                            {tab.sub && <span className="text-[11.5px] font-normal text-gray-600 tabular-nums">{tab.sub}</span>}
                        </span>
                    </Tabs.TabNav>
                ))}
            </Tabs.TabList>
        </Tabs>
    )
}

type ScopeOptions = { owners: Option[]; contracts: Option[]; portfolios: (Option & { contract_id: string | null })[] }

/** Loads the owner / contract / portfolio option lists once per screen. */
export function useScopeOptions(): ScopeOptions & { loading: boolean } {
    const [state, setState] = useState<ScopeOptions & { loading: boolean }>({
        owners: [],
        contracts: [],
        portfolios: [],
        loading: true,
    })
    useEffect(() => {
        let alive = true
        Promise.allSettled([lookups.users(), lookups.contracts(), lookups.portfolios()]).then(([u, c, p]) => {
            if (!alive) return
            setState({
                owners: u.status === 'fulfilled' ? u.value : [],
                contracts: c.status === 'fulfilled' ? c.value : [],
                portfolios: p.status === 'fulfilled' ? p.value : [],
                loading: false,
            })
        })
        return () => {
            alive = false
        }
    }, [])
    return state
}

/** A compact labelled single select for filter bars (`undefined` = any). */
export const FilterSelect = ({
    label,
    options,
    value,
    onChange,
    loading,
    testId,
    width = 220,
}: {
    label: ReactNode
    options: Option[]
    value: string | undefined
    onChange: (v: string | undefined) => void
    loading?: boolean
    testId?: string
    width?: number
}) => {
    const { t } = useTranslation()
    const selected = options.find((o) => o.value === value) || null
    return (
        <div style={{ width }} className="min-w-[160px]" data-testid={testId}>
            <div className="text-eyebrow mb-1">{label}</div>
            <Select
                size="sm"
                isClearable
                isLoading={loading}
                placeholder={t('p1.filter.any')}
                options={options}
                value={selected}
                onChange={(o) => onChange((o as Option | null)?.value || undefined)}
            />
        </div>
    )
}

/** Owner / contract / portfolio filters. They narrow the caller's scope, never widen it. */
export const ScopeFilters = ({
    owner,
    contract,
    portfolio,
    onChange,
    children,
}: {
    owner?: string
    contract?: string
    portfolio?: string
    onChange: (patch: { owner?: string; contract?: string; portfolio?: string }) => void
    children?: ReactNode
}) => {
    const { t } = useTranslation()
    const opts = useScopeOptions()
    const portfolios = contract ? opts.portfolios.filter((p) => p.contract_id === contract) : opts.portfolios
    const active = !!(owner || contract || portfolio)
    return (
        <div className="mb-4 flex flex-wrap items-end gap-3" data-testid="p1-filters">
            <FilterSelect
                label={t('p1.filter.owner')}
                options={opts.owners}
                loading={opts.loading}
                value={owner}
                testId="p1-filter-owner"
                onChange={(v) => onChange({ owner: v })}
            />
            <FilterSelect
                label={t('p1.filter.contract')}
                options={opts.contracts}
                loading={opts.loading}
                value={contract}
                testId="p1-filter-contract"
                onChange={(v) => onChange({ contract: v, portfolio: undefined })}
            />
            <FilterSelect
                label={t('p1.filter.portfolio')}
                options={portfolios}
                loading={opts.loading}
                value={portfolio}
                testId="p1-filter-portfolio"
                onChange={(v) => onChange({ portfolio: v })}
            />
            {children}
            {active && (
                <Button
                    size="sm"
                    variant="plain"
                    onClick={() => onChange({ owner: undefined, contract: undefined, portfolio: undefined })}
                >
                    {t('p1.filter.clear')}
                </Button>
            )}
        </div>
    )
}

/** Card frame for a data table: toolbar, scrolling body, footer. No shadow. */
export const TableFrame = ({
    toolbar,
    children,
    footer,
    testId,
}: {
    toolbar?: ReactNode
    children: ReactNode
    footer?: ReactNode
    testId?: string
}) => (
    <div
        data-testid={testId}
        className="overflow-hidden rounded-lg border border-gray-300 bg-white dark:border-night-border dark:bg-night-card"
    >
        {toolbar && (
            <div className="flex flex-wrap items-center justify-between gap-2 border-b border-gray-300 px-5 py-3 dark:border-night-border">
                {toolbar}
            </div>
        )}
        <div className="overflow-x-auto">{children}</div>
        {footer}
    </div>
)

/** "Rows 1–25 of 12,480" on the leading edge, pager on the trailing edge. */
export const PagerFooter = ({
    page,
    pageSize,
    total,
    onPage,
}: {
    page: number
    pageSize: number
    total: number
    onPage: (p: number) => void
}) => {
    const { t } = useTranslation()
    const from = total === 0 ? 0 : (page - 1) * pageSize + 1
    const to = Math.min(total, page * pageSize)
    return (
        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-gray-300 bg-gray-50 px-5 py-3 text-[12.5px] text-gray-600 dark:border-night-border dark:bg-night-page">
            <span className="flex-none whitespace-nowrap tabular-nums" data-testid="p1-range">
                {t('p1.list.range', { from: formatCount(from), to: formatCount(to), total: formatCount(total) })}
            </span>
            {total > pageSize && (
                <Pagination currentPage={page} pageSize={pageSize} total={total} onChange={onPage} />
            )}
        </div>
    )
}

/** A clickable table row with the selected-row rail (leading edge, flips in RTL). */
export const rowClass = (selected: boolean) =>
    classNames(
        'cursor-pointer hover:bg-gray-50 dark:hover:bg-night-raised',
        selected && 'table-row-current bg-azure-50 dark:bg-night-raised'
    )
