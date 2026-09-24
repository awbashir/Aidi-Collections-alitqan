/**
 * A time-series bar chart for the reports that are one (RPT-20/21 per day,
 * RPT-23 per hour, RPT-24's daily trend section), following
 * design-reference/dataviz.md:
 * - one measure per axis, bars start at zero, horizontal grid only;
 * - the time axis never mirrors (oldest on the left, also in Arabic);
 * - axis labels may abbreviate (1.2M); exact values are in the tooltip, in the
 *   value list one click away, and in the table;
 * - a missing value is a gap, never a silent zero (RPT-20/21 days without a
 *   row are true zeros: no receipts / promises that day, and say so);
 * - orange is a state (the hovered bar), never a series.
 *
 * The chart reads every row of the current filter (pages of 200, at most
 * 4 pages). Sums are exact (money in whole halalas with BigInt), and each sum
 * must equal the report's own exact total; if the pages were read in different
 * snapshots and do not add up, the chart is withheld and says why.
 */
import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import Button from '@/components/ui/Button'
import { SkeletonLines } from '@/components/v2/States'
import { describeReportError, reportsApi } from '@/services/v2'
import type { ReportDefinition, ReportRow, ReportRows } from '@/services/v2'
import { useV2Query } from '@/utils/v2/hooks'
import { formatCount, formatMoney } from '@/utils/v2/format'
import { daysBetween, fromCents, pick, stableKey, toCents } from '../reportUtils'
import type { ChartSpec } from '../reportUtils'

const MAX_PAGES = 4
const PAGE = 200

type Point = { x: string; label: string; value: bigint | null }

async function loadAll(def: ReportDefinition, params: Record<string, string>, signal: AbortSignal) {
    const first = await reportsApi.rows(def.id, { ...params, page: 1, page_size: PAGE }, signal)
    if (first.total > PAGE * MAX_PAGES) return { first, rows: null as ReportRow[] | null, pages: [first] }
    const pages: ReportRows[] = [first]
    for (let p = 2; p <= first.pages; p += 1) {
        pages.push(await reportsApi.rows(def.id, { ...params, page: p, page_size: PAGE }, signal))
    }
    return { first, rows: pages.flatMap((pg) => pg.items), pages }
}

/** Short axis label: 1,250,000 -> 1.3M (the exact value is always one hover or click away). */
function abbreviate(n: number): string {
    const a = Math.abs(n)
    if (a >= 1e9) return `${(n / 1e9).toFixed(1).replace(/\.0$/, '')}B`
    if (a >= 1e6) return `${(n / 1e6).toFixed(1).replace(/\.0$/, '')}M`
    if (a >= 1e3) return `${(n / 1e3).toFixed(1).replace(/\.0$/, '')}K`
    return Number.isInteger(n) ? String(n) : n.toFixed(1)
}

export default function TrendChart({
    def,
    spec,
    params,
    onShowSection,
}: {
    def: ReportDefinition
    spec: ChartSpec
    params: Record<string, string>
    /** Switch the report to the chart's section (RPT-24 daily trend). */
    onShowSection?: () => void
}) {
    const { t, i18n } = useTranslation()
    const ar = (i18n.language || 'ar').startsWith('ar')
    const [measureKey, setMeasureKey] = useState(spec.measures[0].key)
    const [showValues, setShowValues] = useState(false)
    const [hover, setHover] = useState<number | null>(null)
    const measure = spec.measures.find((m) => m.key === measureKey) || spec.measures[0]
    const inSection = !spec.section || (params.section || '') === spec.section

    const q = useV2Query(inSection ? (signal) => loadAll(def, params, signal) : null, [def.id, stableKey(params), inSection])

    const result = useMemo(() => {
        const d = q.data
        if (!d || !d.rows) return null
        /* Every page must come from an equivalent snapshot: same count and totals. */
        const consistent = d.pages.every((pg) => stableKey(pg.totals) === stableKey(d.first.totals))
        const isMoney = measure.type === 'money'
        const val = (r: ReportRow, key: string): bigint | null => {
            const v = r[key]
            if (v === null || v === undefined) return null
            return isMoney ? toCents(String(v)) : BigInt(Math.trunc(Number(v)))
        }
        let points: Point[] = []
        let sum = 0n
        if (spec.x === 'hours') {
            const from = Number(params.from_hour || 8)
            const to = Number(params.to_hour || 17)
            for (let h = from; h <= to; h += 1) {
                const key = `h${String(h).padStart(2, '0')}`
                let s: bigint | null = null
                d.rows.forEach((r) => {
                    const v = val(r, key)
                    if (v !== null) s = (s ?? 0n) + v
                })
                if (s !== null) sum += s
                points.push({ x: key, label: `${String(h).padStart(2, '0')}:00`, value: s })
            }
            /* The per-row `total` is the sum of the hours in the window. */
            let rowSum = 0n
            d.rows.forEach((r) => {
                const v = val(r, 'total')
                if (v !== null) rowSum += v
            })
            sum = rowSum === sum ? sum : -1n
        } else {
            const rows = spec.section ? d.rows.filter((r) => r.section === spec.section) : d.rows
            const byDay = new Map<string, bigint | null>()
            rows.forEach((r) => {
                const day = typeof r.day === 'string' ? r.day : null
                if (!day) return
                const v = val(r, measure.key)
                const prev = byDay.get(day)
                if (v === null) {
                    if (!byDay.has(day)) byDay.set(day, null)
                    return
                }
                byDay.set(day, (prev ?? 0n) + v)
                sum += v
            })
            const pFrom = params[`${spec.range}_from`]
            const pTo = params[`${spec.range}_to`] || pFrom
            const days = pFrom && pTo ? daysBetween(pFrom, pTo) : Array.from(byDay.keys()).sort()
            points = days.map((day) => ({
                x: day,
                label: day,
                value: byDay.has(day) ? byDay.get(day) ?? null : spec.fillZero ? 0n : null,
            }))
        }
        /* The exact total of the report for this measure must equal the chart's sum. */
        const totalKey = spec.checks?.[measure.key]
        let matches = true
        if (totalKey) {
            const raw = d.first.totals?.[totalKey]
            const expected = raw === null || raw === undefined ? null : isMoney ? toCents(String(raw)) : BigInt(Math.trunc(Number(raw)))
            matches = expected !== null && expected === sum
        }
        return { points, sum, consistent, matches, isMoney, rowsRead: d.rows.length }
    }, [q.data, measure, spec, params])

    const fmt = (v: bigint | null, isMoney: boolean) =>
        v === null ? t('p4.chart.noValue') : isMoney ? formatMoney(fromCents(v)) : formatCount(Number(v))

    const col = def.columns.find((c) => c.key === measure.key)
    const title = pick(col?.labels, ar) + (measure.type === 'money' ? ` (${t('p4.sar')})` : '')

    let body
    if (!inSection) {
        body = (
            <div className="flex flex-wrap items-center gap-3 text-[13px] text-gray-600" data-testid="p4-chart-choose">
                {t('p4.chart.chooseSection')}
                {onShowSection && (
                    <Button size="xs" onClick={onShowSection} data-testid="p4-chart-show-section">
                        {t('p4.chart.showSection')}
                    </Button>
                )}
            </div>
        )
    } else if (q.loading && !q.data) {
        body = <SkeletonLines rows={5} className="py-4" />
    } else if (q.error) {
        body = (
            <div role="alert" className="py-4 text-[13px] text-danger">
                {describeReportError(q.error, t)}
            </div>
        )
    } else if (q.data && !q.data.rows) {
        body = (
            <div className="py-6 text-center text-[13px] text-gray-600" data-testid="p4-chart-too-many">
                {t('p4.chart.tooMany', { n: formatCount(q.data.first.total), max: formatCount(PAGE * MAX_PAGES) })}
            </div>
        )
    } else if (result && (!result.consistent || !result.matches)) {
        body = (
            <div role="status" className="py-6 text-center text-[13px] text-amber-700" data-testid="p4-chart-mismatch">
                {t('p4.chart.mismatch')}{' '}
                <button type="button" className="font-extrabold text-azure-600 underline" onClick={q.reload}>
                    {t('p4.chart.reload')}
                </button>
            </div>
        )
    } else if (result) {
        const pts = result.points
        const nums = pts.map((p) => (p.value === null ? null : Number(p.value) / (result.isMoney ? 100 : 1)))
        const maxV = Math.max(0, ...nums.filter((n): n is number => n !== null))
        /* A round axis top: 1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10 × 10^k (counts: at least 4, so ticks stay whole). */
        const nice = (() => {
            if (maxV <= 0) return result.isMoney ? 1 : 4
            if (!result.isMoney && maxV <= 4) return 4
            const e = 10 ** Math.floor(Math.log10(maxV))
            const m = maxV / e
            const step = [1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10].find((x) => m <= x) || 10
            return step * e
        })()
        const W = 760
        const H = 240
        const L = 56
        const R = 12
        const T = 12
        const B = 34
        const plotW = W - L - R
        const plotH = H - T - B
        const band = plotW / Math.max(pts.length, 1)
        const barW = Math.max(1, band * 0.7)
        const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => f * nice)
        const labelEvery = Math.max(1, Math.ceil(pts.length / 12))
        const peak = pts.reduce<number>((best, p, i) => (p.value !== null && (best < 0 || (pts[best].value ?? 0n) < p.value) ? i : best), -1)
        const gaps = pts.filter((p) => p.value === null).length
        body = (
            <div>
                <div className="mb-2 text-[12.5px] text-gray-600 tabular-nums" data-testid="p4-chart-sub">
                    {t('p4.chart.sub', { total: fmt(result.sum, result.isMoney), rows: formatCount(result.rowsRead) })}
                    {spec.fillZero ? ` · ${t('p4.chart.zeroDays')}` : ''}
                    {gaps > 0 ? ` · ${t('p4.chart.gaps', { n: gaps })}` : ''}
                    {peak >= 0 && pts[peak].value !== null && pts[peak].value !== 0n
                        ? ` · ${t('p4.chart.peak', { x: pts[peak].label, v: fmt(pts[peak].value, result.isMoney) })}`
                        : ''}
                </div>
                <div dir="ltr" className="relative w-full">
                    <svg viewBox={`0 0 ${W} ${H}`} className="h-auto w-full" role="img" aria-label={title} data-testid="p4-chart-svg" data-points={pts.length}>
                        {ticks.map((tv) => {
                            const y = T + plotH - (tv / nice) * plotH
                            return (
                                <g key={tv}>
                                    <line x1={L} x2={W - R} y1={y} y2={y} stroke={tv === 0 ? '#B8C4CE' : '#E7EEF2'} strokeWidth={1} />
                                    <text x={L - 6} y={y + 4} textAnchor="end" fontSize="11.5" fill="#6B7482" style={{ fontVariantNumeric: 'tabular-nums' }}>
                                        {abbreviate(tv)}
                                    </text>
                                </g>
                            )
                        })}
                        {pts.map((p, i) => {
                            const n = nums[i]
                            const x = L + i * band + (band - barW) / 2
                            const h = n === null ? 0 : (n / nice) * plotH
                            return (
                                <g key={p.x} onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}>
                                    <rect x={L + i * band} y={T} width={band} height={plotH} fill="transparent" />
                                    {n !== null && (
                                        <rect
                                            x={x}
                                            y={T + plotH - h}
                                            width={barW}
                                            height={Math.max(h, n > 0 ? 1 : 0)}
                                            rx={Math.min(2, barW / 2)}
                                            fill={hover === i ? '#FF6600' : '#2F5FD6'}
                                            data-x={p.x}
                                            data-value={p.value === null ? '' : result.isMoney ? fromCents(p.value) : String(p.value)}
                                        />
                                    )}
                                    {i % labelEvery === 0 && (
                                        <text x={L + i * band + band / 2} y={H - B + 16} textAnchor="middle" fontSize="11.5" fill="#6B7482" style={{ fontVariantNumeric: 'tabular-nums' }}>
                                            {spec.x === 'hours' ? p.label.slice(0, 2) : p.label.slice(5)}
                                        </text>
                                    )}
                                </g>
                            )
                        })}
                        <line x1={L} x2={W - R} y1={T + plotH} y2={T + plotH} stroke="#D7E1E8" strokeWidth={1} />
                    </svg>
                    {hover !== null && pts[hover] && (
                        <div
                            className="pointer-events-none absolute top-0 rounded-md bg-navy-600 px-[10px] py-2 text-[12.5px] text-white shadow-md tabular-nums"
                            style={{ left: `${((L + hover * band + band / 2) / W) * 100}%`, transform: 'translateX(-50%)' }}
                            data-testid="p4-chart-tooltip"
                        >
                            {pts[hover].label}: {fmt(pts[hover].value, result.isMoney)}
                        </div>
                    )}
                </div>
                <div className="mt-2">
                    <Button size="xs" variant="plain" onClick={() => setShowValues((v) => !v)} data-testid="p4-chart-values-toggle">
                        {showValues ? t('p4.chart.hideValues') : t('p4.chart.showValues')}
                    </Button>
                    {showValues && (
                        <div className="mt-2 max-h-[260px] overflow-y-auto rounded-md border border-gray-200 dark:border-night-border" data-testid="p4-chart-values">
                            <table className="w-full text-[12.5px]">
                                <tbody>
                                    {pts.map((p) => (
                                        <tr key={p.x} className="border-b border-gray-100 last:border-0 dark:border-night-border">
                                            <td className="px-3 py-1 tabular-nums" dir="ltr">
                                                {p.label}
                                            </td>
                                            <td className="px-3 py-1 text-end tabular-nums" dir="ltr" data-x={p.x}>
                                                {fmt(p.value, result.isMoney)}
                                            </td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    )}
                </div>
            </div>
        )
    }

    return (
        <section
            className="mb-4 rounded-lg border border-gray-300 bg-white p-5 dark:border-night-border dark:bg-night-card"
            data-testid="p4-chart"
            aria-label={title}
        >
            <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                <div className="text-[15px] font-extrabold text-navy-600 dark:text-gray-100">
                    {spec.x === 'hours' ? t('p4.chart.titleHours', { m: title }) : t('p4.chart.titleDays', { m: title })}
                </div>
                {spec.measures.length > 1 && (
                    <div className="flex overflow-hidden rounded-md border border-[#B8C4CE] dark:border-night-border" role="radiogroup">
                        {spec.measures.map((m, i) => {
                            const c = def.columns.find((x) => x.key === m.key)
                            const on = m.key === measure.key
                            return (
                                <button
                                    key={m.key}
                                    type="button"
                                    role="radio"
                                    aria-checked={on}
                                    onClick={() => setMeasureKey(m.key)}
                                    data-testid={`p4-chart-measure-${m.key}`}
                                    className={
                                        'h-8 px-3 text-[12.5px] ' +
                                        (i > 0 ? 'border-s border-[#B8C4CE] dark:border-night-border ' : '') +
                                        (on ? 'bg-navy-600 font-extrabold text-white dark:bg-azure-500' : 'text-gray-800 dark:text-gray-200')
                                    }
                                >
                                    {pick(c?.labels, ar)}
                                </button>
                            )
                        })}
                    </div>
                )}
            </div>
            {body}
        </section>
    )
}
