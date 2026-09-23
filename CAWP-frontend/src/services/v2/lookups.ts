/**
 * Option lists for the Phase 1 screens, read from the existing (legacy) lookup
 * routes. Cached for the session so a screen and its dialogs share one request.
 * A failed request is not cached, so the next screen retries it.
 */
import ApiService from '../ApiService'

export type Option = { value: string; label: string }

export type CommentStatusOption = Option & {
    contract_id: string | null
    reachability_id: string | null
    color?: string | null
}

const cache = new Map<string, Promise<unknown>>()

function cached<T>(key: string, load: () => Promise<T>): Promise<T> {
    const hit = cache.get(key) as Promise<T> | undefined
    if (hit) return hit
    const p = load().catch((e) => {
        cache.delete(key)
        throw e
    })
    cache.set(key, p)
    return p
}

const get = async <T>(url: string) => (await ApiService.fetchData<T>({ url, method: 'get' })).data

type Named = { id: string; name: string }

export const lookups = {
    users: () =>
        cached('users', async () => {
            const d = await get<{
                users?: Array<{ id: string; frist_name?: string; last_name?: string; user_name?: string; is_active?: boolean }>
            }>('/uesrs')
            return (d.users || [])
                .map((u) => ({
                    value: u.id,
                    label:
                        `${(u.frist_name || '').trim()} ${(u.last_name || '').trim()}`.trim() || u.user_name || u.id,
                }))
                .sort((a, b) => a.label.localeCompare(b.label, 'ar'))
        }),
    contracts: () =>
        cached('contracts', async () => {
            const d = await get<{ contracts?: Named[] }>('/contracts')
            return (d.contracts || []).map((c) => ({ value: c.id, label: c.name }))
        }),
    portfolios: () =>
        cached('portfolios', async () => {
            const d = await get<{ portfolios?: Array<Named & { contracts?: Named | null }> }>('/portfolios')
            return (d.portfolios || []).map((p) => ({
                value: p.id,
                label: p.name,
                contract_id: p.contracts?.id ?? null,
            }))
        }),
    contactMethods: () =>
        cached('contactMethods', async () => {
            const d = await get<{ contact_methods?: Named[] }>('/contact_methods')
            return (d.contact_methods || []).map((c) => ({ value: c.id, label: c.name }))
        }),
    reachability: () =>
        cached('reachability', async () => {
            const d = await get<{ comment_reachabilitys?: Named[] }>('/comment_reachability')
            return (d.comment_reachabilitys || []).map((c) => ({ value: c.id, label: c.name }))
        }),
    commentStatuses: () =>
        cached('commentStatuses', async () => {
            const d = await get<{
                comment_statuses?: Array<
                    Named & { contract_id?: string | null; CommentReachability_id?: string | null; color?: string | null }
                >
            }>('/comment_status')
            return (d.comment_statuses || []).map(
                (c): CommentStatusOption => ({
                    value: c.id,
                    label: c.name,
                    contract_id: c.contract_id ?? null,
                    reachability_id: c.CommentReachability_id ?? null,
                    color: c.color ?? null,
                })
            )
        }),
    contactTypes: () =>
        cached('contactTypes', async () => {
            const d = await get<{ clint_contact_types?: Array<Named & { is_deleted?: boolean }> }>(
                '/clint_contact_types'
            )
            return (d.clint_contact_types || [])
                .filter((c) => !c.is_deleted)
                .map((c) => ({ value: c.id, label: c.name }))
        }),
    delinquencyReasons: () =>
        cached('delinquencyReasons', async () => {
            const d = await get<{ delinquency_reasons?: Named[] }>('/clints/delinquency_reasons')
            return (d.delinquency_reasons || []).map((c) => ({ value: c.id, label: c.name }))
        }),
}
