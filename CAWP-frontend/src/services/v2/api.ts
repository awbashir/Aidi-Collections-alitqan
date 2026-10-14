/**
 * Phase 1 v2 endpoints. Every function returns the envelope's `data` or throws
 * `V2Error` (see ./http.ts). Mutations take the caller's per-action
 * Idempotency-Key; edits send the `expected_revision` they loaded.
 *
 * Contracts: review/phase1-money.md, review/phase1-worklist.md,
 * review/phase1-history.md, verified against the live API.
 */
import { v2Get, v2Post, v2Put } from './http'
import type {
    AttentionReason,
    AttentionResponse,
    Balance,
    CaseFollowUps,
    CasePromises,
    Contact,
    ContactHistoryItem,
    Correction,
    CorrectionKind,
    DebtorContacts,
    FollowUp,
    FollowUpBucket,
    FollowUpListResponse,
    Note,
    NoteHistory,
    Paged,
    Payment,
    PaymentDesk,
    PromiseList,
    PromiseRow,
} from './types'

const enc = encodeURIComponent

export type WorkQuery = {
    owner?: string
    contract_id?: string
    portfolio_id?: string
    page?: number
    page_size?: number
}

/* ------------------------------------------------------------ worklist (B) */

export const worklistApi = {
    attention: (q: WorkQuery & { reason?: AttentionReason }, signal?: AbortSignal) =>
        v2Get<AttentionResponse>('/v2/attention', q, signal),

    followUps: (q: WorkQuery & { bucket?: FollowUpBucket }, signal?: AbortSignal) =>
        v2Get<FollowUpListResponse>('/v2/follow-ups', q, signal),

    caseFollowUps: (caseId: string, signal?: AbortSignal) =>
        v2Get<CaseFollowUps>(`/v2/cases/${enc(caseId)}/follow-ups`, undefined, signal),

    create: (caseId: string, body: { due_at: string; next_action: string }, key: string) =>
        v2Post<FollowUp>(`/v2/cases/${enc(caseId)}/follow-ups`, body, key),

    complete: (
        id: string,
        body: {
            expected_revision: number
            outcome_note: {
                text: string
                comment_status_id: string
                /** 0068: optional sub-status of comment_status_id */
                comment_sub_status_id?: string
                contact_method_id: string
                comment_reachability_id?: string
                contact_number?: string
            }
            next?: { due_at: string; next_action: string }
            no_further_action_reason?: string
        },
        key: string
    ) =>
        v2Post<{ completed: FollowUp; next: FollowUp | null; outcome_comment_id: string }>(
            `/v2/follow-ups/${enc(id)}/complete`,
            body,
            key
        ),

    reschedule: (
        id: string,
        body: { expected_revision: number; due_at: string; reason: string; next_action?: string },
        key: string
    ) => v2Post<FollowUp>(`/v2/follow-ups/${enc(id)}/reschedule`, body, key),
}

/* --------------------------------------------------------------- money (A) */

export type DeskQuery = {
    status?: string
    case_id?: string
    recorder_id?: string
    owner_id?: string
    portfolio_id?: string
    contract_id?: string
    date_from?: string
    date_to?: string
    sort?: string
    page?: number
    page_size?: number
}

export type PromiseQuery = {
    state?: string
    due_from?: string
    due_to?: string
    recorder_id?: string
    owner_id?: string
    case_id?: string
    portfolio_id?: string
    contract_id?: string
    sort?: string
    page?: number
    page_size?: number
}

export const moneyApi = {
    balance: (caseId: string, signal?: AbortSignal) =>
        v2Get<Balance>(`/v2/cases/${enc(caseId)}/balance`, undefined, signal),

    casePayments: (caseId: string, signal?: AbortSignal) =>
        v2Get<{ case_id: string; balance: Balance; items: Payment[] }>(
            `/v2/cases/${enc(caseId)}/payments`,
            undefined,
            signal
        ),

    desk: (q: DeskQuery, signal?: AbortSignal) => v2Get<PaymentDesk>('/v2/payments', q, signal),

    payment: (id: string, signal?: AbortSignal) =>
        v2Get<Payment>(`/v2/payments/${enc(id)}`, undefined, signal),

    approve: (id: string, body: { expected_revision: number; comment?: string }, key: string) =>
        v2Post<{ payment: Payment; balance: Balance; allocations: unknown[] }>(
            `/v2/payments/${enc(id)}/approve`,
            body,
            key
        ),

    reject: (id: string, body: { expected_revision: number; reason: string }, key: string) =>
        v2Post<{ payment: Payment; balance: Balance }>(`/v2/payments/${enc(id)}/reject`, body, key),

    reverse: (id: string, body: { expected_revision: number; reason: string }, key: string) =>
        v2Post<{ payment: Payment; balance: Balance; voided_allocations: number }>(
            `/v2/payments/${enc(id)}/reverse`,
            body,
            key
        ),

    promises: (q: PromiseQuery, signal?: AbortSignal) => v2Get<PromiseList>('/v2/promises', q, signal),

    promise: (id: string, signal?: AbortSignal) =>
        v2Get<PromiseRow>(`/v2/promises/${enc(id)}`, undefined, signal),

    casePromises: (caseId: string, signal?: AbortSignal) =>
        v2Get<CasePromises>(`/v2/cases/${enc(caseId)}/promises`, undefined, signal),

    createPromise: (caseId: string, body: { amount: string; due_date: string }, key: string) =>
        v2Post<PromiseRow>(`/v2/cases/${enc(caseId)}/promises`, body, key),

    cancelPromise: (id: string, body: { expected_revision: number; reason: string }, key: string) =>
        v2Post<PromiseRow>(`/v2/promises/${enc(id)}/cancel`, body, key),

    allocate: (id: string, body: { payment_id: string; amount?: string; note?: string }, key: string) =>
        v2Post<{ allocation_id: string; promise: PromiseRow }>(`/v2/promises/${enc(id)}/allocations`, body, key),

    deallocate: (allocationId: string, body: { reason: string }, key: string) =>
        v2Post<{ allocation_id: string; promise: PromiseRow }>(
            `/v2/promise-allocations/${enc(allocationId)}/deallocate`,
            body,
            key
        ),
}

/* ------------------------------------------------------------- history (C) */

export const historyApi = {
    caseNotes: (
        caseId: string,
        q: { include_retired?: boolean; page?: number; page_size?: number },
        signal?: AbortSignal
    ) =>
        v2Get<Paged<Note>>(
            `/v2/cases/${enc(caseId)}/notes`,
            { ...q, include_retired: q.include_retired ? 1 : undefined },
            signal
        ),

    noteHistory: (noteId: string, signal?: AbortSignal) =>
        v2Get<NoteHistory>(`/v2/notes/${enc(noteId)}/history`, undefined, signal),

    correctNote: (noteId: string, body: { text: string; reason: string; expected_revision: number }, key: string) =>
        v2Post<Note>(`/v2/notes/${enc(noteId)}/correct`, body, key),

    retireNote: (noteId: string, body: { reason: string; expected_revision: number }, key: string) =>
        v2Post<Note>(`/v2/notes/${enc(noteId)}/retire`, body, key),

    contacts: (identity: string, signal?: AbortSignal) =>
        v2Get<DebtorContacts>(`/v2/debtors/${enc(identity)}/contacts`, undefined, signal),

    contactHistory: (identity: string, q: { page?: number; page_size?: number }, signal?: AbortSignal) =>
        v2Get<Paged<ContactHistoryItem>>(`/v2/debtors/${enc(identity)}/contacts/history`, q, signal),

    addContact: (
        identity: string,
        body: { contact_type_id: string; contact: string; make_default?: boolean; reason?: string },
        key: string
    ) => v2Post<DebtorContacts & { contact: Contact }>(`/v2/debtors/${enc(identity)}/contacts`, body, key),

    editContact: (
        id: string,
        body: { contact?: string; contact_type_id?: string; reason?: string; expected_revision: number },
        key: string
    ) => v2Put<DebtorContacts & { contact: Contact }>(`/v2/contacts/${enc(id)}`, body, key),

    setDefault: (id: string, body: { expected_revision: number; reason?: string }, key: string) =>
        v2Post<DebtorContacts>(`/v2/contacts/${enc(id)}/default`, body, key),

    block: (
        id: string,
        body: { reason: string; expected_revision: number; replacement_default_id?: string },
        key: string
    ) => v2Post<DebtorContacts>(`/v2/contacts/${enc(id)}/block`, body, key),

    unblock: (id: string, body: { reason: string; expected_revision: number }, key: string) =>
        v2Post<DebtorContacts>(`/v2/contacts/${enc(id)}/unblock`, body, key),

    requestCorrection: (
        caseId: string,
        body: { kind: CorrectionKind; changes: Record<string, unknown>; reason: string },
        key: string
    ) => v2Post<Correction>(`/v2/cases/${enc(caseId)}/corrections`, body, key),

    corrections: (
        q: { status?: string; kind?: string; case_id?: string; requested_by?: string; page?: number; page_size?: number },
        signal?: AbortSignal
    ) => v2Get<Paged<Correction>>('/v2/corrections', q, signal),

    correction: (id: string, signal?: AbortSignal) =>
        v2Get<Correction>(`/v2/corrections/${enc(id)}`, undefined, signal),

    approveCorrection: (id: string, body: { review_note?: string }, key: string) =>
        v2Post<Correction>(`/v2/corrections/${enc(id)}/approve`, body, key),

    rejectCorrection: (id: string, body: { review_note: string }, key: string) =>
        v2Post<Correction>(`/v2/corrections/${enc(id)}/reject`, body, key),

    cancelCorrection: (id: string, body: { reason?: string }, key: string) =>
        v2Post<Correction>(`/v2/corrections/${enc(id)}/cancel`, body, key),
}
