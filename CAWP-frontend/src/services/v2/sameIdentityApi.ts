/**
 * Other accounts of the same debtor (same identity number), CAWP-API/api/same_identity.py,
 * db/migrations/0072.
 *
 *   GET /v2/cases/<case>/same-identity?include_archived=0|1
 *
 * Every account of the identity is listed read-only; `can_open` is true only for accounts in
 * the caller's own scope. Money is a 2-decimal string; `paid` counts approved payments only
 * (pending, rejected and reversed never count), `remaining` = amount - paid.
 * Same transport as ./api.ts: the envelope's `data` or a thrown `V2Error`.
 */
import { v2Get } from './http'

const enc = encodeURIComponent

type Named = { id: string; name: string | null }

export type SameIdentityAccount = {
    id: string
    account_number: string
    name: string
    contract: Named | null
    portfolio: Named | null
    amount: string
    paid: string
    remaining: string
    is_archive: boolean
    last_note: { status: string | null; sub_status: string | null; color: string | null; at: string | null } | null
    owner: Named | null
    can_open: boolean
}

export type SameIdentity = {
    case_id: string
    identity: string | null
    include_archived: boolean
    truncated: boolean
    summary: { count: number; amount: string; paid: string; remaining: string }
    accounts: SameIdentityAccount[]
}

export const sameIdentityApi = {
    get: (caseId: string, includeArchived: boolean, signal?: AbortSignal) =>
        v2Get<SameIdentity>(
            `/v2/cases/${enc(caseId)}/same-identity`,
            { include_archived: includeArchived ? 1 : 0 },
            signal
        ),
}
