/**
 * An `ids` filter: a scoped, searchable, paged selector backed by
 * `GET /v2/report-options/<field>?report=<id>`.
 *
 * - Candidates are what the API lists for this report and the caller's scope,
 *   narrowed by the current values of the filters in `depends_on` (sent along).
 * - `q` is a literal search (debounced); "Load more" fetches the next page of 50.
 * - Chosen ids beyond the loaded page (e.g. after a reload from the URL hash)
 *   are resolved with `selected=<ids>`; an id the API does not return (outside
 *   the candidates) stays in the filter and is shown as unresolved.
 */
import classNames from 'classnames'
import { useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { HiOutlineChevronDown, HiOutlineX } from 'react-icons/hi'
import Input from '@/components/ui/Input'
import Spinner from '@/components/ui/Spinner'
import { describeReportError, reportsApi } from '@/services/v2'
import type { IdsFilterDef, ReportOption } from '@/services/v2'
import { formatCount } from '@/utils/v2/format'
import { stableKey } from '../reportUtils'

type Props = {
    reportId: string
    filter: IdsFilterDef
    /** Comma-separated ids ('' = any). */
    value: string
    onChange: (csv: string) => void
    /** Params of the depends_on filters and of required filters (the API needs them). */
    context: Record<string, string>
    /** Labels of the depends_on filters, for the "narrowed by" hint. */
    dependsLabels: string[]
    invalid?: boolean
}

const splitIds = (v: string) => (v ? v.split(',').map((s) => s.trim()).filter(Boolean) : [])

export default function IdsFilter({ reportId, filter, value, onChange, context, dependsLabels, invalid }: Props) {
    const { t } = useTranslation()
    const ids = useMemo(() => splitIds(value), [value])
    const ctxKey = stableKey(context)
    const [open, setOpen] = useState(false)
    const [known, setKnown] = useState<Record<string, ReportOption>>({})
    const [unresolved, setUnresolved] = useState<Set<string>>(new Set())
    const rootRef = useRef<HTMLDivElement>(null)
    const searchRef = useRef<HTMLInputElement>(null)

    /* Resolve labels of chosen ids we have not seen (reload, shared link). */
    const checkedRef = useRef<Set<string>>(new Set())
    useEffect(() => {
        const missing = ids.filter((id) => !known[id] && !checkedRef.current.has(`${ctxKey}|${id}`))
        if (!missing.length) return undefined
        missing.forEach((id) => checkedRef.current.add(`${ctxKey}|${id}`))
        const ctrl = new AbortController()
        reportsApi
            .options(filter.key, { report: reportId, selected: missing.join(','), ...context }, ctrl.signal)
            .then((res) => {
                const got: Record<string, ReportOption> = {}
                res.selected.forEach((o) => {
                    got[o.id] = o
                })
                setKnown((k) => ({ ...k, ...got }))
                setUnresolved((u) => {
                    const next = new Set(u)
                    missing.forEach((id) => (got[id] ? next.delete(id) : next.add(id)))
                    return next
                })
            })
            .catch(() => {
                /* labels stay unresolved; the filter still applies */
            })
        return () => ctrl.abort()
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [value, ctxKey, filter.key, reportId])

    /* ------------------------------------------------ dropdown list */
    const [q, setQ] = useState('')
    const [qd, setQd] = useState('')
    useEffect(() => {
        const h = setTimeout(() => setQd(q.trim()), 300)
        return () => clearTimeout(h)
    }, [q])
    const [items, setItems] = useState<ReportOption[]>([])
    /* The page belongs to one list (open + search + context); a new list starts at page 1. */
    const listKey = `${open}|${qd}|${ctxKey}`
    const [pageState, setPageState] = useState({ key: listKey, page: 1 })
    const page = pageState.key === listKey ? pageState.page : 1
    const [total, setTotal] = useState<number | null>(null)
    const [pages, setPages] = useState(0)
    const [loading, setLoading] = useState(false)
    const [error, setError] = useState<unknown>(null)

    useEffect(() => {
        if (!open) return undefined
        const ctrl = new AbortController()
        setLoading(true)
        setError(null)
        if (page === 1) setItems([])
        reportsApi
            .options(filter.key, { report: reportId, page, ...(qd ? { q: qd } : {}), ...context }, ctrl.signal)
            .then((res) => {
                setItems((prev) => (page === 1 ? res.items : [...prev, ...res.items.filter((o) => !prev.some((p) => p.id === o.id))]))
                setTotal(res.total)
                setPages(res.pages)
                setKnown((k) => {
                    const next = { ...k }
                    res.items.forEach((o) => {
                        next[o.id] = o
                    })
                    return next
                })
                setLoading(false)
            })
            .catch((e) => {
                if ((e as { code?: string })?.code === 'canceled') return
                setError(e)
                setLoading(false)
            })
        return () => ctrl.abort()
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [listKey, page, filter.key, reportId])

    /* Close on an outside click or Escape; the search box takes the focus when the list opens. */
    useEffect(() => {
        if (!open) return undefined
        searchRef.current?.focus()
        const onDown = (e: MouseEvent) => {
            if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false)
        }
        const onKey = (e: KeyboardEvent) => {
            if (e.key === 'Escape') setOpen(false)
        }
        document.addEventListener('mousedown', onDown)
        document.addEventListener('keydown', onKey)
        return () => {
            document.removeEventListener('mousedown', onDown)
            document.removeEventListener('keydown', onKey)
        }
    }, [open])

    const toggle = (id: string) => {
        const next = ids.includes(id) ? ids.filter((x) => x !== id) : [...ids, id]
        if (next.length > filter.max_ids) return
        onChange(next.join(','))
    }

    const labelOf = (id: string) => known[id]?.label || (unresolved.has(id) ? t('p4.ids.unresolved', { id: id.slice(0, 8) }) : '…')

    const summary =
        ids.length === 0
            ? t('p4.filter.any')
            : ids.length === 1
            ? labelOf(ids[0])
            : t('p4.ids.summary', { first: labelOf(ids[0]), n: ids.length - 1 })

    return (
        <div ref={rootRef} className="relative" data-testid={`p4-ids-${filter.key}`} data-selected={value}>
            <button
                type="button"
                aria-haspopup="listbox"
                aria-expanded={open}
                onClick={() => setOpen((o) => !o)}
                data-testid={`p4-ids-trigger-${filter.key}`}
                className={classNames(
                    'flex h-9 w-full items-center justify-between gap-2 rounded-md border bg-white px-3 text-start text-[13.5px] dark:bg-night-card',
                    invalid ? 'border-danger' : 'border-[#B8C4CE] hover:border-gray-500 dark:border-night-border',
                    ids.length ? 'text-navy-600 dark:text-gray-100' : 'text-gray-500'
                )}
            >
                <span className="min-w-0 truncate" dir="auto">
                    {summary}
                </span>
                <HiOutlineChevronDown aria-hidden className="shrink-0 text-gray-500" />
            </button>
            {open && (
                <div
                    role="listbox"
                    aria-multiselectable
                    className="absolute z-40 mt-1 w-[340px] max-w-[90vw] rounded-lg border border-gray-300 bg-white p-2 shadow-[0_8px_24px_rgba(7,10,75,.10)] dark:border-night-border dark:bg-night-card"
                    style={{ insetInlineStart: 0 }}
                    data-testid={`p4-ids-panel-${filter.key}`}
                >
                    {ids.length > 0 && (
                        <div className="mb-2 flex flex-wrap gap-1" data-testid="p4-ids-chips">
                            {ids.map((id) => (
                                <span
                                    key={id}
                                    className="inline-flex max-w-full items-center gap-1 rounded-pill border border-azure-600 bg-azure-50 px-2 text-[12px] font-extrabold text-azure-700"
                                >
                                    <span className="truncate" dir="auto">
                                        {labelOf(id)}
                                    </span>
                                    <button
                                        type="button"
                                        aria-label={String(t('p4.ids.remove'))}
                                        onClick={() => toggle(id)}
                                        className="text-azure-600"
                                    >
                                        <HiOutlineX />
                                    </button>
                                </span>
                            ))}
                            <button
                                type="button"
                                className="text-[12px] font-extrabold text-azure-600 underline-offset-2 hover:underline"
                                onClick={() => onChange('')}
                                data-testid="p4-ids-clear"
                            >
                                {t('p4.ids.clear')}
                            </button>
                        </div>
                    )}
                    <Input
                        size="sm"
                        ref={searchRef}
                        value={q}
                        maxLength={100}
                        placeholder={String(t('p4.ids.search'))}
                        onChange={(e) => setQ(e.target.value)}
                        data-testid="p4-ids-search"
                    />
                    {dependsLabels.length > 0 && (
                        <div className="mt-1 text-[11.5px] text-gray-600">{t('p4.ids.narrowedBy', { list: dependsLabels.join('، ') })}</div>
                    )}
                    <div className="mt-2 max-h-[260px] overflow-y-auto">
                        {error ? (
                            <div className="px-2 py-3 text-[12.5px] text-danger" role="alert">
                                {describeReportError(error, t)}
                            </div>
                        ) : items.length === 0 && !loading ? (
                            <div className="px-2 py-3 text-[12.5px] text-gray-600">{t('p4.ids.none')}</div>
                        ) : (
                            items.map((o) => {
                                const checked = ids.includes(o.id)
                                return (
                                    <label
                                        key={o.id}
                                        className={classNames(
                                            'flex cursor-pointer items-start gap-2 rounded-md px-2 py-[6px] hover:bg-gray-50 dark:hover:bg-night-raised',
                                            checked && 'bg-azure-50 dark:bg-night-raised'
                                        )}
                                        data-testid="p4-option"
                                        data-id={o.id}
                                    >
                                        <input
                                            type="checkbox"
                                            className="mt-[3px]"
                                            checked={checked}
                                            onChange={() => toggle(o.id)}
                                        />
                                        <span className="min-w-0 flex-1">
                                            <span className="block truncate text-[13px] text-gray-800 dark:text-gray-100" dir="auto">
                                                {o.label || o.id}
                                            </span>
                                            {(o.secondary || !o.active) && (
                                                <span className="block truncate text-[11.5px] text-gray-600" dir="auto">
                                                    {o.secondary}
                                                    {!o.active && (
                                                        <span className="ms-1 font-extrabold">({t('p4.ids.inactive')})</span>
                                                    )}
                                                </span>
                                            )}
                                        </span>
                                    </label>
                                )
                            })
                        )}
                        {loading && (
                            <div className="flex justify-center py-2">
                                <Spinner size={18} />
                            </div>
                        )}
                    </div>
                    <div className="mt-1 flex items-center justify-between gap-2 border-t border-gray-200 px-1 pt-2 text-[11.5px] text-gray-600 dark:border-night-border">
                        <span className="tabular-nums" data-testid="p4-ids-count">
                            {total === null ? '' : t('p4.ids.count', { shown: formatCount(items.length), total: formatCount(total) })}
                        </span>
                        {page < pages && !loading && (
                            <button
                                type="button"
                                className="font-extrabold text-azure-600 underline-offset-2 hover:underline"
                                onClick={() => setPageState({ key: listKey, page: page + 1 })}
                                data-testid="p4-ids-more"
                            >
                                {t('p4.ids.more')}
                            </button>
                        )}
                    </div>
                </div>
            )}
        </div>
    )
}
