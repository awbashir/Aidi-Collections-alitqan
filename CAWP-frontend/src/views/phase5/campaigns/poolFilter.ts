/** Campaign defaults and the clients-list filter hand-over (no components here). */
import type { CampaignSettings } from '@/services/v2'

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

