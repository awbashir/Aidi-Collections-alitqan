/**
 * Params rules of each bulk kind (wizard step 4): validity and the body sent to
 * `POST /v2/bulk/<kind>`. Names follow `services/bulk_*.py validate_params`.
 */
import { riyadhInstant } from '@/utils/v2/format'
import type { BulkSource } from '@/services/v2'

export type Params = Record<string, unknown> & { __ui?: Record<string, string> }


export const str = (v: unknown) => (typeof v === 'string' ? v : '')
export const has = (v: unknown) => typeof v === 'string' && v.trim().length > 0

/** The notes callback as the instant the API stores (always with +03:00). */
export const notesCallback = (p: Params): string | undefined => {
    const date = p.__ui?.cbDate
    if (!date) return undefined
    return riyadhInstant(date, p.__ui?.cbTime || undefined)
}

/** Whether step 4 may continue. */
export function paramsValid(kind: string, source: BulkSource | undefined, p: Params): boolean {
    switch (kind) {
        case 'lock':
        case 'unlock':
        case 'restore':
            return has(p.reason)
        case 'archive':
            return has(p.archive_reason_id)
        case 'assign':
            return source === 'spreadsheet' ? true : has(p.target_user_id)
        case 'round_robin':
            return (
                (has(p.portfolio_id) || has(p.contract_id)) &&
                Array.isArray(p.collector_ids) &&
                (p.collector_ids as string[]).length > 0 &&
                (!has(p.limit) || /^[1-9]\d{0,5}$/.test(str(p.limit)))
            )
        case 'notes':
            return has(p.comment) && has(p.comment_status_id) && has(p.contactMethod_id) && (!p.__ui?.cbTime || !!p.__ui?.cbDate)
        case 'attribute_corrections':
            return has(p.reason)
        default:
            return true
    }
}

/** The `params` body for `POST /v2/bulk/<kind>`: trimmed, empty optional fields left out. */
export function buildParams(kind: string, source: BulkSource | undefined, p: Params, attributes?: Record<string, string>): Record<string, unknown> {
    const out: Record<string, unknown> = {}
    const text = (k: string) => {
        if (has(p[k])) out[k] = str(p[k]).trim()
    }
    switch (kind) {
        case 'lock':
        case 'unlock':
        case 'restore':
            text('reason')
            break
        case 'archive':
            text('archive_reason_id')
            text('note')
            break
        case 'assign':
            if (source !== 'spreadsheet') text('target_user_id')
            text('reason')
            break
        case 'round_robin':
            if (p.__ui?.scope === 'contract') text('contract_id')
            else text('portfolio_id')
            out.collector_ids = (p.collector_ids as string[]) || []
            if (has(p.limit)) out.limit = Number(str(p.limit))
            text('reason')
            break
        case 'notes': {
            text('comment')
            text('comment_status_id')
            text('contactMethod_id')
            text('comment_reachability_id')
            text('contactNumber')
            const cb = notesCallback(p)
            if (cb) out.next_contact_date = cb
            break
        }
        case 'inquiry':
            out.match_mode = p.match_mode === 'any' ? 'any' : 'all'
            break
        case 'followup_closeout':
            text('reason')
            text('owner')
            text('contract_id')
            text('portfolio_id')
            break
        case 'attribute_corrections':
            text('reason')
            out.attributes = attributes || {}
            out.blank_cells = p.blank_cells === 'remove' ? 'remove' : 'skip'
            break
        default:
            Object.entries(p).forEach(([k, v]) => {
                if (k !== '__ui') out[k] = v
            })
    }
    return out
}
