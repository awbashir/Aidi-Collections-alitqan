/**
 * The BI result: the pivot table (row and column totals from the server's GROUPING SETS, so a
 * distinct count is never summed here) and a chart of the same numbers (bar, line or pie).
 */
import { useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import Chart from '@/components/shared/Chart'
import { useConfig } from '@/components/ui/ConfigProvider'
import { Ltr } from '@/components/v2/Display'
import type { BiDataset, BiResult, ChartKind } from '@/services/v2/reportBuilderApi'
import { keyLabel, measureValue, pick, toNumber } from '../shared'

const MAX_SERIES = 12
const MAX_SLICES = 20

export function usePivotLabels(result: BiResult, dataset: BiDataset | undefined) {
    const { t, i18n } = useTranslation()
    const ar = (i18n.language || 'ar').startsWith('ar')
    return useMemo(() => {
        const dimLabel = (field: string) => pick(dataset?.dimensions.find((d) => d.key === field)?.labels, ar) || field
        const rowLabel = (k: (string | number | boolean | null)[]) =>
            k.map((v, i) => keyLabel(v, t, result.fields.rows[i]?.grain)).join(' / ')
        const colLabel = (k: (string | number | boolean | null)[]) =>
            k.map((v, i) => keyLabel(v, t, result.fields.columns[i]?.grain)).join(' / ')
        return { ar, dimLabel, rowLabel, colLabel }
    }, [result, dataset, ar, t])
}

export const PivotTable = ({ result, dataset }: { result: BiResult; dataset: BiDataset | undefined }) => {
    const { t } = useTranslation()
    const { ar, dimLabel, colLabel } = usePivotLabels(result, dataset)
    const { rows: fRows, columns: fCols, values: fVals } = result.fields
    const grid = useMemo(() => {
        const m = new Map<string, (string | number | null)[]>()
        result.cells.forEach((c) => m.set(`${c.r}:${c.c}`, c.m))
        return m
    }, [result])
    const hasCols = fCols.length > 0
    const hasRows = fRows.length > 0
    const multi = fVals.length > 1
    const showRowTotal = hasCols && (result.row_totals !== null || !hasRows)
    const showColTotal = hasRows && result.grand_total !== null

    if (!hasRows && !hasCols) {
        return (
            <div className="grid grid-cols-2 gap-3 md:grid-cols-4" data-testid="rb-bi-kpis">
                {fVals.map((m, j) => (
                    <div key={m.key} className="rounded-lg border border-gray-300 bg-white p-4 dark:border-night-border dark:bg-night-card">
                        <div className="text-eyebrow mb-1">{pick(m.labels, ar)}</div>
                        <Ltr className="text-[22px] font-extrabold text-navy-600 dark:text-gray-100">
                            {measureValue(result.grand_total?.[j], m.type)}
                        </Ltr>
                    </div>
                ))}
            </div>
        )
    }

    const totalCell = (vals: (string | number | null)[] | null | undefined, j: number, type: (typeof fVals)[number]['type']) =>
        vals ? measureValue(vals[j], type) : '—'

    return (
        <div className="max-h-[560px] overflow-auto rounded-lg border border-gray-300 dark:border-night-border" data-testid="rb-bi-pivot">
            <table className="min-w-full border-collapse text-[12.5px]">
                <thead className="sticky top-0 z-10 bg-gray-50 dark:bg-night-raised">
                    <tr>
                        {fRows.map((f) => (
                            <th key={f.field + (f.grain || '')} rowSpan={multi && hasCols ? 2 : 1}
                                className="whitespace-nowrap border-b border-e border-gray-300 px-3 py-2 text-start font-extrabold text-navy-600 dark:border-night-border dark:text-gray-100">
                                {dimLabel(f.field)}
                            </th>
                        ))}
                        {hasCols
                            ? result.column_keys.map((ck, ci) => (
                                  <th key={ci} colSpan={fVals.length}
                                      className="whitespace-nowrap border-b border-e border-gray-300 px-3 py-2 text-center font-extrabold text-navy-600 dark:border-night-border dark:text-gray-100">
                                      {colLabel(ck)}
                                  </th>
                              ))
                            : fVals.map((m) => (
                                  <th key={m.key}
                                      className="whitespace-nowrap border-b border-e border-gray-300 px-3 py-2 text-end font-extrabold text-navy-600 dark:border-night-border dark:text-gray-100">
                                      {pick(m.labels, ar)}
                                  </th>
                              ))}
                        {showRowTotal && (
                            <th colSpan={fVals.length}
                                className="whitespace-nowrap border-b border-gray-300 bg-gray-100 px-3 py-2 text-center font-extrabold text-navy-600 dark:border-night-border dark:bg-night-card dark:text-gray-100">
                                {t('rb.bi.total')}
                            </th>
                        )}
                    </tr>
                    {multi && hasCols && (
                        <tr>
                            {[...result.column_keys, ...(showRowTotal ? [null] : [])].map((_ck, ci) =>
                                fVals.map((m) => (
                                    <th key={`${ci}-${m.key}`}
                                        className="whitespace-nowrap border-b border-e border-gray-300 px-3 py-1.5 text-center text-[11.5px] font-bold text-gray-600 dark:border-night-border">
                                        {pick(m.labels, ar)}
                                    </th>
                                ))
                            )}
                        </tr>
                    )}
                </thead>
                <tbody>
                    {result.row_keys.map((rk, ri) => (
                        <tr key={ri} className="hover:bg-gray-50 dark:hover:bg-night-raised">
                            {fRows.map((f, i) => (
                                <td key={i} className="whitespace-nowrap border-b border-e border-gray-200 px-3 py-1.5 font-bold text-gray-800 dark:border-night-border dark:text-night-body">
                                    {keyLabel(rk[i], t, f.grain)}
                                </td>
                            ))}
                            {result.column_keys.map((_ck, ci) => {
                                const ms = grid.get(`${ri}:${ci}`)
                                return fVals.map((m, j) => (
                                    <td key={`${ci}-${j}`} className="whitespace-nowrap border-b border-e border-gray-200 px-3 py-1.5 text-end tabular-nums dark:border-night-border" dir="ltr">
                                        {ms ? measureValue(ms[j], m.type) : ''}
                                    </td>
                                ))
                            })}
                            {showRowTotal &&
                                fVals.map((m, j) => (
                                    <td key={`t-${j}`} className="whitespace-nowrap border-b border-gray-200 bg-gray-50 px-3 py-1.5 text-end font-extrabold tabular-nums dark:border-night-border dark:bg-night-card" dir="ltr">
                                        {hasRows ? totalCell(result.row_totals?.[ri], j, m.type) : totalCell(result.grand_total, j, m.type)}
                                    </td>
                                ))}
                        </tr>
                    ))}
                </tbody>
                {showColTotal && (
                    <tfoot className="sticky bottom-0 bg-gray-100 dark:bg-night-card">
                        <tr>
                            <td colSpan={fRows.length} className="border-t border-e border-gray-300 px-3 py-2 font-extrabold text-navy-600 dark:border-night-border dark:text-gray-100">
                                {t('rb.bi.total')}
                            </td>
                            {result.column_keys.map((_ck, ci) =>
                                fVals.map((m, j) => (
                                    <td key={`${ci}-${j}`} className="whitespace-nowrap border-t border-e border-gray-300 px-3 py-2 text-end font-extrabold tabular-nums dark:border-night-border" dir="ltr">
                                        {hasCols ? totalCell(result.column_totals?.[ci], j, m.type) : totalCell(result.grand_total, j, m.type)}
                                    </td>
                                ))
                            )}
                            {showRowTotal &&
                                fVals.map((m, j) => (
                                    <td key={`g-${j}`} className="whitespace-nowrap border-t border-gray-300 px-3 py-2 text-end font-extrabold tabular-nums dark:border-night-border" dir="ltr">
                                        {totalCell(result.grand_total, j, m.type)}
                                    </td>
                                ))}
                        </tr>
                    </tfoot>
                )}
            </table>
        </div>
    )
}

export const PivotChart = ({ result, dataset, kind }: { result: BiResult; dataset: BiDataset | undefined; kind: ChartKind }) => {
    const { t } = useTranslation()
    const { direction } = useConfig()
    const { ar, rowLabel, colLabel } = usePivotLabels(result, dataset)
    const { rows: fRows, columns: fCols, values: fVals } = result.fields
    const data = useMemo(() => {
        const grid = new Map<string, (string | number | null)[]>()
        result.cells.forEach((c) => grid.set(`${c.r}:${c.c}`, c.m))
        const hasRows = fRows.length > 0
        const hasCols = fCols.length > 0
        if (!hasRows && !hasCols) return null
        /* categories along the row keys when there are rows, else along the column keys */
        if (hasRows && hasCols) {
            const categories = result.row_keys.map(rowLabel)
            const series = result.column_keys.slice(0, MAX_SERIES).map((ck, ci) => ({
                name: colLabel(ck),
                data: result.row_keys.map((_rk, ri) => toNumber(grid.get(`${ri}:${ci}`)?.[0])),
            }))
            const pie = result.row_keys.map((_rk, ri) => toNumber(result.row_totals?.[ri]?.[0]))
            return { categories, series, pie, pieLabels: categories }
        }
        if (hasRows) {
            const categories = result.row_keys.map(rowLabel)
            const series = fVals.map((m, j) => ({
                name: pick(m.labels, ar),
                data: result.row_keys.map((_rk, ri) => toNumber(grid.get(`${ri}:0`)?.[j])),
            }))
            return { categories, series, pie: series[0]?.data || [], pieLabels: categories }
        }
        const categories = result.column_keys.map(colLabel)
        const series = fVals.map((m, j) => ({
            name: pick(m.labels, ar),
            data: result.column_keys.map((_ck, ci) => toNumber(grid.get(`0:${ci}`)?.[j])),
        }))
        return { categories, series, pie: series[0]?.data || [], pieLabels: categories }
    }, [result, fRows, fCols, fVals, rowLabel, colLabel, ar])

    if (!data || kind === 'table') return null
    if (data.categories.length === 0) return null
    if (kind === 'pie') {
        /* biggest slices first; the rest is one "other" slice (an exact sum of what it holds) */
        const pairs = data.pie.map((v, i) => ({ v, l: data.pieLabels[i] })).sort((a, b) => b.v - a.v)
        const head = pairs.slice(0, MAX_SLICES)
        const rest = pairs.slice(MAX_SLICES).reduce((s, p) => s + p.v, 0)
        if (rest) head.push({ v: rest, l: String(t('rb.bi.other')) })
        return (
            <div data-testid="rb-bi-chart" data-kind="pie">
                <Chart type="donut" direction={direction} height={340} series={head.map((p) => p.v)}
                       donutTitle={pick(fVals[0].labels, ar)}
                       customOptions={{ labels: head.map((p) => p.l), legend: { position: 'bottom' } }} />
            </div>
        )
    }
    return (
        <div data-testid="rb-bi-chart" data-kind={kind}>
            <Chart type={kind} direction={direction} height={340} series={data.series} xAxis={data.categories}
                   customOptions={{ dataLabels: { enabled: false }, legend: { show: data.series.length > 1, position: 'bottom' },
                                    stroke: kind === 'line' ? { width: 2, curve: 'smooth' } : undefined }} />
        </div>
    )
}
