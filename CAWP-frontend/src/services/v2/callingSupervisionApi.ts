/**
 * Calling supervision (customer feedback 2026-10-17; key `calls_manage`):
 *
 * - Calls that ended without a wrap-up and still hold their reservation, including ORPHANED
 *   ones whose case was deleted (`GET /v2/calling/pending-wrapups`), and the supervisor
 *   release of that reservation with a reason (`POST /v2/calls/<id>/release`).
 * - Blocked clients (`GET /v2/calling-blocks`), blocking a client (`POST /v2/calling-blocks`)
 *   and lifting a block (`POST /v2/calling-blocks/<id>/release`). Only supervisors block a
 *   client; collectors mark a wrong or disconnected number with the `wrong_number` outcome.
 *
 * Contracts: CAWP-API/api/calling.py, services/calling/review.py, queries.list_blocks.
 */
import { v2Get, v2Post } from './http'
import type { CallDetail, CaseBlocks, LiveCall, ReviewFlag, UserRef } from './p5Types'

const enc = encodeURIComponent

export type PendingWrapupList = {
    items: Array<CallDetail & { flags: ReviewFlag[] }>
    total: number
    /** pending calls whose case was deleted (whatever the `orphaned` filter) */
    orphaned: number
    page: number
    page_size: number
}

export type BlockedClient = {
    id: string
    case: { id: string; name: string | null; accountNumber: string | null; identityNumber: string | null }
    owner: UserRef
    contract: { id: string; name: string | null } | null
    portfolio_name: string | null
    kind: 'do_not_call'
    reason: string | null
    source: 'wrapup' | 'manual'
    attempt_id: string | null
    created_by: UserRef
    created_at: string
    active: boolean
    released_at: string | null
    released_by: UserRef
    release_reason: string | null
}

export type BlockedClientList = {
    items: BlockedClient[]
    total: number
    page: number
    page_size: number
    active: 'true' | 'false' | 'all'
}

export type BlockQuery = {
    active?: 'true' | 'false' | 'all'
    contract_id?: string
    owner_id?: string
    source?: 'wrapup' | 'manual'
    q?: string
    page?: number
    page_size?: number
}

export const supervisionApi = {
    pendingWrapups: (q: { orphaned?: 'true' | 'false'; page?: number; page_size?: number }, signal?: AbortSignal) =>
        v2Get<PendingWrapupList>('/v2/calling/pending-wrapups', q, signal),

    /** Releases the reservation (source `reconciled`); the call keeps its state, no wrap-up is invented. */
    release: (id: string, body: { expected_revision: number; reason: string }, key: string) =>
        v2Post<LiveCall>(`/v2/calls/${enc(id)}/release`, body, key),

    blocks: (q: BlockQuery, signal?: AbortSignal) => v2Get<BlockedClientList>('/v2/calling-blocks', q, signal),

    block: (body: { case_id: string; reason: string }, key: string) => v2Post<CaseBlocks>('/v2/calling-blocks', body, key),

    lift: (blockId: string, reason: string, key: string) =>
        v2Post<CaseBlocks>(`/v2/calling-blocks/${enc(blockId)}/release`, { reason }, key),
}
