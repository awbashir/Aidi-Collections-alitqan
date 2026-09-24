/**
 * Pure helpers of the target screens: Riyadh months, an exact signed decimal
 * comparator for sorting, and the source label. Money and percentages stay
 * decimal strings; nothing here parses them into a float.
 */
import { riyadhToday } from '@/utils/v2/format'
import type { TargetSource } from '@/services/v2'

/* --------------------------------------------------------------------- months */

export const MONTH_RE = /^\d{4}-(0[1-9]|1[0-2])$/

/** The current Asia/Riyadh month "YYYY-MM" (the API's `period`/`current_month` confirms it). */
export const riyadhMonth = () => riyadhToday().slice(0, 7)

/** "2026-01" + -1 -> "2025-12". Calendar arithmetic on the text only. */
export function shiftMonth(month: string, delta: number): string {
    const [y, m] = month.split('-').map(Number)
    const idx = y * 12 + (m - 1) + delta
    const ny = Math.floor(idx / 12)
    const nm = (idx % 12) + 1
    return `${String(ny).padStart(4, '0')}-${String(nm).padStart(2, '0')}`
}

/* ------------------------------------------------------------------- decimals */

const DEC = /^\s*([+-]?)(\d*)(?:\.(\d*))?\s*$/

/**
 * Exact comparison of two signed decimal strings ("-43317.87", "121.66",
 * "2100000.00", "2.5"): -1, 0, 1. No float. A string that is not a plain
 * decimal compares as text after every number (it never happens on the wire).
 */
export function compareDecimal(a: string, b: string): number {
    const x = DEC.exec(a)
    const y = DEC.exec(b)
    if (!x || !y) return x ? -1 : y ? 1 : a < b ? -1 : a > b ? 1 : 0
    const parts = (m: RegExpExecArray) => {
        const int = (m[2] || '0').replace(/^0+(?=\d)/, '')
        const frac = (m[3] || '').replace(/0+$/, '')
        const zero = /^0*$/.test(int) && frac === ''
        return { neg: m[1] === '-' && !zero, int, frac }
    }
    const p = parts(x)
    const q = parts(y)
    if (p.neg !== q.neg) return p.neg ? -1 : 1
    const sign = p.neg ? -1 : 1
    if (p.int.length !== q.int.length) return (p.int.length < q.int.length ? -1 : 1) * sign
    if (p.int !== q.int) return (p.int < q.int ? -1 : 1) * sign
    const len = Math.max(p.frac.length, q.frac.length)
    const fp = p.frac.padEnd(len, '0')
    const fq = q.frac.padEnd(len, '0')
    if (fp !== fq) return (fp < fq ? -1 : 1) * sign
    return 0
}

/** True when a decimal string is > 0 (no float). */
export const isPositiveDecimal = (v: string) => compareDecimal(v, '0') > 0

export const sourceLabel = (t: (k: string) => string, source: TargetSource | null | undefined) =>
    source ? t(`p3.targets.source.${source}`) : '—'
