/**
 * One note for every account of the same debtor (customer feedback 2026-10-21, migration 0081,
 * CAWP-API/services/note_siblings.py).
 *
 *   GET /v2/cases/<case>/note-siblings
 *
 * The other ACTIVE accounts (not archived, not locked) with the same identity number, whatever
 * their owner: a note saved with `apply_to_all_accounts: true` is written to each of them too
 * (the legacy POST /comments, the wrap-up / manual log, the follow-up completion and the AI
 * save-as-note take the flag). The promise and the follow-up stay on the current account.
 * Same transport as ./api.ts: the envelope's `data` or a thrown `V2Error`.
 */
import { v2Get } from './http'

const enc = encodeURIComponent

type Named = { id: string; name: string | null }

export type NoteSiblingAccount = {
    id: string
    account_number: string | null
    name: string | null
    contract: Named | null
    owner: Named | null
}

export type NoteSiblings = {
    case_id: string
    /** every such account; `accounts` holds at most `limit` */
    count: number
    limit: number
    /** count > limit: the note cannot be applied to all of them (422 too_many_accounts) */
    too_many: boolean
    accounts: NoteSiblingAccount[]
}

/** A copy written on another account (`applied_to` of a save, the call detail, the timeline). */
export type NoteCopy = { case_id: string; account_number: string | null; comment_id: string }

/** An account a note was NOT applied to (a status of another contract). */
export type NoteCopySkipped = { case_id: string; account_number: string | null; reason: 'status_not_for_contract' }

export const noteSiblingsApi = {
    get: (caseId: string, signal?: AbortSignal) =>
        v2Get<NoteSiblings>(`/v2/cases/${enc(caseId)}/note-siblings`, undefined, signal),
}
