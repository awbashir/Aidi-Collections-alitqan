/**
 * Display helpers for the Phase 1 screens.
 *
 * Money arrives as an exact decimal string ("1500.50", "-24.00"). It is only ever
 * re-grouped as text here; it is never parsed into a float, so what the user sees
 * is exactly what the database holds.
 *
 * Instants arrive in UTC and are shown as Asia/Riyadh civil time. Civil dates
 * ("YYYY-MM-DD") are already Riyadh dates and are shown as they are. Whether
 * something is late/overdue always comes from the API, never from these helpers.
 */

const RIYADH = 'Asia/Riyadh'

/** "1234567.5" -> "1,234,567.50"; keeps the sign, never goes through a float. */
export function formatMoney(value: string | number | null | undefined, decimals = 2): string {
    if (value === null || value === undefined || value === '') return '—'
    const text = String(value).trim()
    const m = /^([+-]?)(\d*)(?:\.(\d*))?$/.exec(text)
    if (!m) return text
    const sign = m[1] === '-' ? '-' : ''
    const intPart = (m[2] || '0').replace(/^0+(?=\d)/, '')
    let frac = m[3] || ''
    if (frac.length < decimals) frac = frac.padEnd(decimals, '0')
    const grouped = intPart.replace(/\B(?=(\d{3})+(?!\d))/g, ',')
    const isZero = /^0*$/.test(intPart) && /^0*$/.test(frac)
    return `${isZero ? '' : sign}${grouped}${frac ? `.${frac}` : ''}`
}

/** True when a decimal string is negative (for an "overpaid" style), without float math. */
export const isNegativeMoney = (value: string | null | undefined) =>
    !!value && /^-/.test(value.trim()) && !/^-0*(\.0*)?$/.test(value.trim())

/** True when a decimal string is exactly zero. */
export const isZeroMoney = (value: string | null | undefined) =>
    !value || /^[+-]?0*(\.0*)?$/.test(value.trim())

/** Compares two non-negative 2-decimal strings exactly: -1, 0, 1. */
export function compareMoney(a: string, b: string): number {
    const norm = (s: string) => {
        const [i, f = ''] = s.trim().replace(/^\+/, '').split('.')
        return { i: i.replace(/^0+(?=\d)/, ''), f: (f + '00').slice(0, 2) }
    }
    const x = norm(a)
    const y = norm(b)
    if (x.i.length !== y.i.length) return x.i.length < y.i.length ? -1 : 1
    if (x.i !== y.i) return x.i < y.i ? -1 : 1
    if (x.f !== y.f) return x.f < y.f ? -1 : 1
    return 0
}

/** A money amount a user typed: positive, at most 2 decimals. */
export const MONEY_INPUT = /^(?:\d{1,12})(?:\.\d{1,2})?$/

const partsIn = (iso: string, withTime: boolean) => {
    const d = new Date(iso)
    if (Number.isNaN(d.getTime())) return null
    const fmt = new Intl.DateTimeFormat('en-US', {
        timeZone: RIYADH,
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
        ...(withTime ? { hour: '2-digit', minute: '2-digit', hourCycle: 'h23' as const } : {}),
    })
    const p: Record<string, string> = {}
    fmt.formatToParts(d).forEach((x) => {
        p[x.type] = x.value
    })
    return p
}

/** "2026-09-23 16:04" in Asia/Riyadh (Western digits, 24h). */
export function formatRiyadhDateTime(iso: string | null | undefined): string {
    if (!iso) return '—'
    const p = partsIn(iso, true)
    if (!p) return iso
    return `${p.year}-${p.month}-${p.day} ${p.hour === '24' ? '00' : p.hour}:${p.minute}`
}

/** The Asia/Riyadh civil date of an instant: "2026-09-23". */
export function riyadhCivilDate(iso: string | null | undefined): string {
    if (!iso) return '—'
    const p = partsIn(iso, false)
    if (!p) return iso
    return `${p.year}-${p.month}-${p.day}`
}

/** Today in Asia/Riyadh, for `min` on date inputs only (the API decides what is late). */
export function riyadhToday(): string {
    return riyadhCivilDate(new Date().toISOString())
}

/** A Riyadh civil date + "HH:mm" as an ISO instant with the fixed +03:00 offset. */
export function riyadhInstant(date: string, time?: string): string {
    const t = time && /^\d{2}:\d{2}$/.test(time) ? time : '09:00'
    return `${date}T${t}:00+03:00`
}

/** Western digits, grouped: counts in tabs and totals. */
export const formatCount = (n: number | null | undefined) =>
    n === null || n === undefined ? '—' : String(n).replace(/\B(?=(\d{3})+(?!\d))/g, ',')
