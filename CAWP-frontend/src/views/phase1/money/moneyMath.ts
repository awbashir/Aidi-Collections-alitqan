/**
 * Exact helpers for the money screens. Amounts stay decimal strings; the only
 * arithmetic here (a tab sum) runs on integer cents with BigInt, never floats.
 */
import type { Money, PromiseRow } from '@/services/v2'

const CENTS = /^([+-]?)(\d+)(?:\.(\d{1,2}))?$/

/** Sum of 2-decimal strings, exact. Returns null if any value is not a plain decimal. */
export function sumMoney(values: Array<Money | null | undefined>): Money | null {
    let total = BigInt(0)
    for (const v of values) {
        if (v === null || v === undefined || v === '') continue
        const m = CENTS.exec(String(v).trim())
        if (!m) return null
        const cents = BigInt(m[2]) * BigInt(100) + BigInt((m[3] || '').padEnd(2, '0') || '0')
        total += m[1] === '-' ? -cents : cents
    }
    const neg = total < BigInt(0)
    const abs = neg ? -total : total
    const int = abs / BigInt(100)
    const frac = (abs % BigInt(100)).toString().padStart(2, '0')
    return `${neg ? '-' : ''}${int.toString()}.${frac}`
}

/** "-24.00" -> "24.00" (display of an overpaid amount; text only). */
export const absMoney = (value: Money) => value.trim().replace(/^[+-]/, '')

/** A promise recorded before Phase 1 with no allocation: its fulfilment is unknown. */
export const isImportedBaseline = (p: PromiseRow) =>
    (p.allocations?.length ?? 0) === 0 &&
    (p.events || []).some((e) => e.event === 'created' && e.source === 'imported')

/** Positive (strictly > 0) 2-decimal string, without float math. */
export const isPositiveMoney = (value: Money | null | undefined) =>
    !!value && !/^\s*-/.test(value) && !/^[+]?0*(\.0*)?$/.test(value.trim())
