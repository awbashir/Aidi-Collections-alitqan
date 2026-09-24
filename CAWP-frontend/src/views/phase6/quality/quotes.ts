/**
 * Client-side mirror of the evidence rule's quotation check (services/ai/quality_evidence.py).
 * The server decides; this only tells the reviewer before saving whether a quotation is
 * verbatim. Only Unicode NFC and runs of whitespace are normalised, exactly as on the server, so a
 * paraphrase, a translation, an added diacritic or the wrong segment never passes.
 */
import type { QcSegment } from '@/services/v2'

export const MIN_QUOTE = 2

/** NFC + collapse whitespace runs (Python's `" ".join(text.split())`). */
export const normQuote = (text: string) =>
    (text || '')
        .normalize('NFC')
        .split(/\s+/)
        .filter(Boolean)
        .join(' ')

export type QuoteCheck = { ok: true; segmentIds: string[] } | { ok: false; code: string }

/** Same codes as the server: quote_required, quote_too_short, unknown_segment, quote_not_in_segments, quote_not_found, no_transcript. */
export function checkQuote(quote: string, segmentIds: string[], segments: QcSegment[] | null | undefined, fullText: string | null | undefined): QuoteCheck {
    if (!segments || fullText === null || fullText === undefined) return { ok: false, code: 'no_transcript' }
    if (!quote || !quote.trim()) return { ok: false, code: 'quote_required' }
    const q = normQuote(quote)
    if (q.length < MIN_QUOTE) return { ok: false, code: 'quote_too_short' }
    const index = new Map(segments.map((s, i) => [s.id, i]))
    if (segmentIds.length) {
        if (segmentIds.some((id) => !index.has(id))) return { ok: false, code: 'unknown_segment' }
        const idx = Array.from(new Set(segmentIds.map((id) => index.get(id) as number))).sort((a, b) => a - b)
        const joined = normQuote(idx.map((i) => segments[i].text || '').join(' '))
        return joined.includes(q) ? { ok: true, segmentIds: idx.map((i) => segments[i].id) } : { ok: false, code: 'quote_not_in_segments' }
    }
    const all = normQuote(segments.map((s) => s.text || '').join(' '))
    if (!normQuote(fullText).includes(q) && !all.includes(q)) return { ok: false, code: 'quote_not_found' }
    return { ok: true, segmentIds: [] }
}

/** The timestamps of cited segments (only when every one of them has timings). */
export function segmentSpan(segmentIds: string[], segments: QcSegment[] | null | undefined): { start: number | null; end: number | null } {
    if (!segments || !segmentIds.length) return { start: null, end: null }
    const picked = segments.filter((s) => segmentIds.includes(s.id))
    const starts = picked.map((s) => s.start)
    const ends = picked.map((s) => s.end)
    return {
        start: starts.length && starts.every((x) => x !== null) ? (starts[0] as number) : null,
        end: ends.length && ends.every((x) => x !== null) ? (ends[ends.length - 1] as number) : null,
    }
}
