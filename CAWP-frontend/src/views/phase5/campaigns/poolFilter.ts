/** Campaign defaults and the clients-list filter hand-over (no components here). */
import type { CampaignPool, CampaignSettings } from '@/services/v2'
import { isMainStatus, mainStatusesFor, subStatusesOf } from '@/utils/commentStatusTree'
import type { StatusLike } from '@/utils/commentStatusTree'

export const DEFAULT_SETTINGS: CampaignSettings = {
    next_mode: 'auto',
    wrapup_delay_seconds: 10,
    max_attempts_per_day: 3,
    cooldown_minutes: 60,
    lease_seconds: 30,
}

/**
 * The clients list's current query parameters (as `listQueryString` serializes them) into
 * a pool filter: repeated `key[]` values become lists; paging, sorting and the list's
 * always-present defaults are left out (the pool refuses keys it does not know, so a typo
 * can never widen it).
 */
export function poolFilterFromQuery(query: string): Record<string, unknown> {
    const out: Record<string, unknown> = {}
    const skip = new Set(['page', 'pageSize', 'field', 'sort', 'sort[key]', 'sort[order]'])
    new URLSearchParams(query).forEach((value, key) => {
        if (skip.has(key) || value === '') return
        if (key === 'customeFilterArchivingStatusesValue' && value === '0') return
        if (key === 'customeFilterStatusesValue' && value === '0') return
        if (key.endsWith('[]')) {
            const prev = out[key]
            out[key] = Array.isArray(prev) ? [...prev, value] : [value]
        } else {
            out[key] = value
        }
    })
    return out
}


/* ------------------------------------------------- note status filters (customer 2026-10-19) */

/** The case's LATEST note: main status (the list's two spellings) and sub-status. The API ANDs
 *  them, exactly as the clients list does. */
export const STATUS_KEY = 'customeFilterCommentStatusValue[]'
export const STATUS_ALT_KEY = 'customeFilterStatusesValue'
export const SUB_STATUS_KEY = 'customeFilterCommentSubStatusValue[]'
export const STATUS_KEYS = [STATUS_KEY, STATUS_ALT_KEY, SUB_STATUS_KEY]

const ids = (v: unknown): string[] =>
    (Array.isArray(v) ? v : v === undefined || v === null ? [] : [v]).map(String).filter((x) => x !== '' && x !== '0')

/** Split the note-status keys off a pool filter (the single-value spelling folds into the
 *  list one; the API reads it only when the list one is absent). */
export function takeStatuses(filter: Record<string, unknown>) {
    const rest = { ...filter }
    const main = ids(rest[STATUS_KEY])
    const statuses = main.length ? main : ids(rest[STATUS_ALT_KEY])
    const subStatuses = ids(rest[SUB_STATUS_KEY])
    STATUS_KEYS.forEach((k) => delete rest[k])
    return { statuses, subStatuses, rest }
}

const intersect = (a: string[] | null, b: string[]) => (a === null ? b : a.filter((x) => b.includes(x)))

/**
 * The contracts whose own note statuses the pool may filter on (null = any contract): the
 * contracts the pool reaches by its own criteria (contracts, portfolios standing for their
 * contract, and the list filter's contracts/portfolios: intersected), within my scope
 * (`scope` null = every contract). Mirrors campaign_pool.status_contracts, the authority.
 */
export function statusContracts(
    pool: CampaignPool,
    portfolios: Array<{ value: string; contract_id?: string | null }>,
    scope: string[] | null
): string[] | null {
    const of = (pids: string[]) =>
        Array.from(new Set(portfolios.filter((p) => pids.includes(p.value) && p.contract_id).map((p) => String(p.contract_id))))
    let own: string[] | null = null
    const f = pool.filter || {}
    ;[pool.contract_ids, ids(f['customeFilterContractValue[]'])].forEach((c) => {
        if (c.length) own = intersect(own, c)
    })
    ;[pool.portfolio_ids, ids(f['customeFilterPortfoliosValue[]'])].forEach((p) => {
        if (p.length) own = intersect(own, of(p))
    })
    if (scope === null) return own
    return own === null ? scope : intersect(own, scope)
}

/** Live main statuses the pool may filter on: the general ones plus those of `contracts`. */
export function poolStatusOptions<T extends StatusLike>(list: T[], contracts: string[] | null): T[] {
    if (contracts === null) return mainStatusesFor(list)
    return list.filter((s) => isMainStatus(s) && (s.contract_id == null || contracts.includes(s.contract_id)))
}

/** Live sub-statuses of the chosen main statuses, in the main statuses' order. */
export function poolSubStatusOptions<T extends StatusLike>(list: T[], mains: string[]): T[] {
    return mains.flatMap((m) => subStatusesOf(list, m))
}

/**
 * Keep only what still fits: main statuses known to be live and outside `contracts` are
 * dropped, then sub-statuses whose main status is no longer chosen (or, with no main status,
 * whose contract no longer fits). Ids the list does not know
 * (a status deleted after the campaign saved it) are kept: the API keeps them on an edit.
 */
export function pruneStatuses<T extends StatusLike>(
    list: T[],
    contracts: string[] | null,
    statuses: string[],
    subStatuses: string[]
): { statuses: string[]; subStatuses: string[] } {
    const byId = new Map(list.map((s) => [String(s.value ?? s.id), s]))
    const allowed = new Set(poolStatusOptions(list, contracts).map((s) => String(s.value ?? s.id)))
    const mains = statuses.filter((id) => !byId.has(id) || allowed.has(id))
    const subs = subStatuses.filter((id) => {
        const s = byId.get(id)
        if (!s) return true
        if (mains.length) return mains.includes(String(s.parent_id))
        return s.contract_id == null || contracts === null || contracts.includes(s.contract_id)
    })
    return { statuses: mains, subStatuses: subs }
}
