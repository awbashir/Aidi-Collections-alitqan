/**
 * Null-safe readers for the home dashboard. Every field from the API is treated as optional:
 * a missing number is 0 (or "—" where unknown must stay unknown), a missing list is [],
 * a lookup that misses falls back to a label. Nothing here throws.
 */
import { useEffect, useRef, useState } from 'react'
import { formatMoney } from '@/utils/v2/format'

/** A finite number from a number or a decimal string, else `fallback`. */
export const num = (v: unknown, fallback = 0): number => {
    const n = typeof v === 'number' ? v : typeof v === 'string' && v.trim() !== '' ? Number(v) : NaN
    return Number.isFinite(n) ? n : fallback
}

export const arr = <T>(v: T[] | null | undefined): T[] => (Array.isArray(v) ? v : [])

export const text = (v: unknown, fallback = '—'): string => (typeof v === 'string' && v.trim() ? v : fallback)

/** "12.5%" from a percent value; "—" when unknown. */
export const pct = (v: number | null | undefined, digits = 1) =>
    typeof v === 'number' && Number.isFinite(v) ? `${v.toFixed(digits)}%` : '—'

/** "50.0%" from a 0..1 rate; "—" when unknown. */
export const rate = (v: number | null | undefined, digits = 1) =>
    typeof v === 'number' && Number.isFinite(v) ? `${(v * 100).toFixed(digits)}%` : '—'

/** Exact money text (never through a float); "—" when absent. */
export const moneyText = (v: string | null | undefined, decimals = 2) => (v === null || v === undefined ? '—' : formatMoney(v, decimals))

/** Compact axis label: 1.2K, 3.4M (axes only; exact values stay in tooltips and cards). */
export const compact = (v: number) => {
    const n = num(v)
    const a = Math.abs(n)
    if (a >= 1e6) return `${(n / 1e6).toFixed(a >= 1e7 ? 0 : 1)}M`
    if (a >= 1e3) return `${(n / 1e3).toFixed(a >= 1e4 ? 0 : 1)}K`
    return String(Math.round(n))
}

/** Grouped number for tooltips ("12,345.50"). */
export const grouped = (v: number, decimals = 0) =>
    num(v).toLocaleString('en-US', { minimumFractionDigits: decimals, maximumFractionDigits: decimals })

/** `path#k=v&...` for the list screens that keep their filters in the URL hash (useHashState). */
export const hashLink = (path: string, params: Record<string, string | number | null | undefined>) => {
    const parts = Object.entries(params)
        .filter(([, v]) => v !== undefined && v !== null && v !== '')
        .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`)
    return parts.length ? `${path}#${parts.join('&')}` : path
}

/** True once the element has come within `margin` of the viewport (stays true). */
export function useInView<T extends Element>(margin = '200px') {
    const ref = useRef<T | null>(null)
    const [seen, setSeen] = useState(false)
    useEffect(() => {
        if (seen) return undefined
        const el = ref.current
        if (!el || typeof IntersectionObserver === 'undefined') {
            setSeen(true)
            return undefined
        }
        const io = new IntersectionObserver(
            (entries) => {
                if (entries.some((e) => e.isIntersecting)) {
                    setSeen(true)
                    io.disconnect()
                }
            },
            { rootMargin: margin }
        )
        io.observe(el)
        return () => io.disconnect()
    }, [seen, margin])
    return { ref, seen }
}
