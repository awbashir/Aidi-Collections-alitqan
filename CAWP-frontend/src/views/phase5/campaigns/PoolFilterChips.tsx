/**
 * A campaign pool's clients-list filter as chips with NAMES, not ids (customer 2026-10-19):
 * contracts, portfolios, collectors and note statuses / sub-statuses are looked up; anything
 * else (branches, dates, text) shows its value. Used by the campaign form and the details page.
 */
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { HiOutlineX } from 'react-icons/hi'
import ApiService from '@/services/ApiService'
import { lookups } from '@/services/v2'

type Names = Record<string, string>

/** id -> name for every id a pool filter can hold. Note statuses include deleted ones (a saved
 *  campaign keeps a status deleted later; it still has a name). */
export function usePoolNames(active = true): Names {
    const [names, setNames] = useState<Names>({})
    useEffect(() => {
        if (!active) return undefined
        let alive = true
        const statuses = ApiService.fetchData<{ comment_statuses?: Array<{ id: string; name: string }> }>({
            url: '/comment_status',
            method: 'get',
            params: { include_deleted: 1 },
        }).then((r) => (r.data.comment_statuses || []).map((s) => ({ value: s.id, label: s.name })))
        Promise.allSettled([lookups.contracts(), lookups.portfolios(), lookups.users(), statuses]).then((all) => {
            if (!alive) return
            const m: Names = {}
            all.forEach((r) => {
                if (r.status === 'fulfilled') r.value.forEach((o) => (m[o.value] = o.label))
            })
            setNames(m)
        })
        return () => {
            alive = false
        }
    }, [active])
    return names
}

const NAMED = new Set([
    'customeFilterContractValue[]',
    'customeFilterPortfoliosValue[]',
    'customeFilterUserValue[]',
    'customeFilterCommentStatusValue[]',
    'customeFilterStatusesValue',
    'customeFilterCommentSubStatusValue[]',
])

export const filterKeyLabel = (k: string, t: (k: string, o?: Record<string, unknown>) => string) =>
    String(t(`p5.cf.filterKey.${k.replace(/\[\]$/, '')}`, { defaultValue: k }))

export function filterValueText(k: string, v: unknown, names: Names, sep: string): string {
    const list = Array.isArray(v) ? v.map(String) : [String(v)]
    if (!NAMED.has(k)) return list.join(sep)
    return list.map((id) => names[id] || id.slice(0, 8)).join(sep)
}

const PoolFilterChips = ({
    filter,
    names,
    onRemove,
    testId,
}: {
    filter: Record<string, unknown>
    names: Names
    /** Shown as an × on each chip (the form); none on the details page. */
    onRemove?: (key: string) => void
    testId?: string
}) => {
    const { t } = useTranslation()
    const sep = String(t('p5.listSep'))
    return (
        <div className="flex flex-wrap gap-2" data-testid={testId}>
            {Object.entries(filter).map(([k, v]) => (
                <span
                    key={k}
                    className="inline-flex min-h-7 items-center gap-1 rounded-pill border border-azure-500 bg-azure-50 px-3 py-0.5 text-[12px] text-azure-600"
                    data-key={k}
                >
                    <span dir="auto">
                        {filterKeyLabel(k, t)}: {filterValueText(k, v, names, sep)}
                    </span>
                    {onRemove && (
                        <button type="button" aria-label={String(t('p1.filter.clear'))} onClick={() => onRemove(k)}>
                            <HiOutlineX />
                        </button>
                    )}
                </span>
            ))}
        </div>
    )
}

export default PoolFilterChips
