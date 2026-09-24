/** Small formatting helpers for the voice lab page. */

/** The ar or en text of a bilingual value. */
export const pick = (b: { ar: string; en: string } | null | undefined, ar: boolean) => (b ? (ar ? b.ar : b.en) : '')

/** mm:ss (for tabular display). */
export const clock = (seconds: number) => {
    const s = Math.max(0, Math.floor(seconds))
    return `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`
}
