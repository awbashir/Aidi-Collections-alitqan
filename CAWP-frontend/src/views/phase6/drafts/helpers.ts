/**
 * Helpers of the AI drafts screens (no components here, so the component files keep fast refresh).
 */
import type { AiDraft, DraftChecks } from '@/services/v2'

export type DraftSource = {
    /** The case the draft is for (the note is saved on it). */
    caseId: string
    /** For the call kinds (call summary, coaching). */
    attemptId?: string | null
}

/** `actions[1]` -> the item's own words (action / point / event / concern / strength). */
export function flaggedItemText(draft: AiDraft, path: string): string {
    const m = /^([a-z_]+)\[(\d+)\]$/.exec(path)
    const content = (draft.content || {}) as Record<string, unknown>
    if (!m) return path
    const list = content[m[1]]
    const item = Array.isArray(list) ? list[Number(m[2])] : undefined
    if (!item || typeof item !== 'object') return path
    const o = item as Record<string, unknown>
    for (const k of ['action', 'point', 'event', 'concern', 'objection', 'suggestion', 'quote']) {
        if (typeof o[k] === 'string' && (o[k] as string).trim()) return o[k] as string
    }
    return path
}

/** The server's checks, as lists of paths the model did not support. */
export function flagged(checks: DraftChecks | null | undefined): { unsupported: string[]; unverified: string[]; unknownFacts: string[] } {
    const asPaths = (v: unknown) => (Array.isArray(v) ? v.map((x) => (typeof x === 'string' ? x : JSON.stringify(x))) : [])
    return {
        unsupported: asPaths(checks?.unsupported),
        unverified: asPaths(checks?.unverified_quotes),
        unknownFacts: asPaths(checks?.unknown_fact_ids),
    }
}

/** A draft that can still change (generated / edited) and is the caller's own. */
export const isLive = (d: AiDraft | null | undefined) => !!d && (d.status === 'generated' || d.status === 'edited') && d.content_available

export const DRAFT_HASH_KEY = 'draft'
