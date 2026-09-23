/**
 * Wire types of the Phase 1 v2 API (review/phase1-{money,worklist,history}.md,
 * checked against the live API). Money is always a 2-decimal **string**; never
 * convert it to a JS number for arithmetic or display.
 */

export type Money = string
export type Iso = string // ISO-8601 instant (UTC)
export type CivilDate = string // YYYY-MM-DD, Asia/Riyadh

export type Actor = { id: string; name: string } | null

export type Paged<T> = { items: T[]; total: number; page: number; page_size: number }

export type AsOf = { at: Iso; riyadh_date: CivilDate; day_start: Iso; day_end: Iso }

/* ------------------------------------------------------------------ worklist */

export type WorkCase = {
    id: string
    name: string
    accountNumber: string
    identityNumber: string
    original_amount: Money
    is_locked: boolean
    /** Archived cases appear only under pending_approval. */
    is_archived: boolean
    last_comment_at: Iso | null
    owner: { id: string; name: string; user_name?: string } | null
    portfolio: { id: string; name: string } | null
    contract: { id: string; name: string } | null
}

export type AttentionReason =
    | 'late_follow_up'
    | 'missed_promise'
    | 'pending_approval'
    | 'unassigned'
    | 'unscheduled'

export const ATTENTION_REASONS: AttentionReason[] = [
    'late_follow_up',
    'missed_promise',
    'pending_approval',
    'unassigned',
    'unscheduled',
]

export type AttentionItem = {
    case: WorkCase
    reasons: AttentionReason[]
    facts: {
        late_follow_up?: {
            follow_up_id: string
            due_at: Iso
            due_date: CivilDate
            days_late: number
            next_action: string | null
            revision: number
        }
        missed_promise?: { count: number; oldest_due_date: CivilDate; unallocated_amount: Money }
        pending_approval?: { count: number; oldest_created_at: Iso; amount: Money }
        unassigned?: Record<string, never>
        unscheduled?: { last_comment_at: Iso | null }
    }
    open_follow_up: { id: string; due_at: Iso; bucket: FollowUpBucket; revision: number } | null
}

export type WorkFilters = { owner: string | null; contract_id: string | null; portfolio_id: string | null }

export type AttentionResponse = {
    counts: Record<AttentionReason, number>
    total: number
    reason: AttentionReason | null
    page: number
    pageSize: number
    items: AttentionItem[]
    filters: WorkFilters
    as_of: AsOf
    note: string
}

export type FollowUpBucket = 'late' | 'today' | 'upcoming'
export type FollowUpStatus = 'open' | 'completed' | 'cancelled' | 'superseded'

export type FollowUp = {
    id: string
    case_id: string
    status: FollowUpStatus
    source: 'note' | 'manual' | 'imported'
    due_at: Iso
    due_date: CivilDate
    original_due_at: Iso
    next_action: string | null
    owner_user_id: string | null
    source_comment_id: string | null
    created_by: string | null
    created_at: Iso
    reschedule_count: number
    completed_by: string | null
    completed_at: Iso | null
    completed_late: boolean | null
    outcome_comment_id: string | null
    no_further_action_reason: string | null
    next_follow_up_id: string | null
    closed_at: Iso | null
    closed_by: string | null
    superseded_by: string | null
    close_reason: string | null
    revision: number
    bucket: FollowUpBucket | null
    days_late: number | null
    case?: WorkCase
    source_note?: {
        id: string
        missing: boolean
        text: string | null
        is_deleted: boolean | null
        created_at: Iso | null
        status: string | null
    } | null
}

export type FollowUpListResponse = {
    counts: Record<FollowUpBucket, number>
    bucket: FollowUpBucket | null
    total: number
    page: number
    pageSize: number
    items: FollowUp[]
    filters: WorkFilters
    as_of: AsOf
}

export type FollowUpEvent = {
    id: number
    follow_up_id: string
    event: 'created' | 'imported' | 'superseded' | 'rescheduled' | 'completed' | 'cancelled' | 'owner_changed'
    actor_user_id: string | null
    actor_name: string | null
    occurred_at: Iso
    due_at_before: Iso | null
    due_at_after: Iso | null
    was_late: boolean | null
    reason: string | null
    comment_id: string | null
    related_follow_up_id: string | null
    source: string
    details: Record<string, unknown> | null
}

export type CaseFollowUps = {
    case_id: string
    case_is_archived: boolean
    current: FollowUp | null
    follow_ups: FollowUp[]
    events: FollowUpEvent[]
    as_of: AsOf
}

/* --------------------------------------------------------------------- money */

export type Balance = {
    case_id: string
    original: Money
    approved: Money
    reversed: Money
    pending: Money
    rejected: Money
    outstanding: Money
    overpaid: boolean
    counts: { approved: number; pending: number; rejected: number; reversed: number }
}

export type PaymentStatus = 'pending' | 'approved' | 'rejected' | 'reversed' | 'deleted'

export type MoneyCase = {
    id: string
    name: string
    account_number: string
    identity_number: string
    owner: Actor
    portfolio: Actor
    contract: Actor
}

export type PaymentEvent = {
    event: 'submitted' | 'approved' | 'rejected' | 'reversed' | 'edited' | 'deleted'
    at: Iso
    actor: Actor
    amount: Money | null
    comment: string | null
    source: string
}

export type Allocation = {
    id: string
    promise_id: string
    payment_id: string
    amount: Money
    source: 'auto' | 'manual'
    note: string | null
    created_at: Iso
    created_by: string | null
    active: boolean
    voided_at: Iso | null
    voided_by: string | null
    void_reason: string | null
}

export type Payment = {
    id: string
    case_id: string
    status: PaymentStatus
    amount: Money
    allocated: Money
    unallocated: Money
    payment_date: Iso
    payment_civil_date: CivilDate
    created_at: Iso
    revision: number
    source: string
    recorder: Actor
    collection_type: Actor
    collection_method: Actor
    decision: { approved: boolean; by: Actor; at: Iso | null; comment: string | null } | null
    reversal: { id: string; amount: Money; reason: string; by: Actor; at: Iso } | null
    case?: MoneyCase
    events?: PaymentEvent[]
    allocations?: Allocation[]
    changes?: Array<Record<string, unknown>>
}

export type PaymentDesk = {
    items: Payment[]
    page: number
    page_size: number
    total: number
    total_amount: Money
    status: PaymentStatus[]
    sort: string
}

export type PromiseState = 'open' | 'overdue' | 'fulfilled' | 'cancelled'
export const PROMISE_STATES: PromiseState[] = ['open', 'overdue', 'fulfilled', 'cancelled']

export type PromiseEvent = {
    event: string
    at: Iso
    actor: Actor
    amount: Money | null
    reason: string | null
    allocation_id: string | null
    payment_id: string | null
    source: string
}

export type PromiseRow = {
    id: string
    case_id: string
    state: PromiseState
    due_date: CivilDate
    amount: Money
    allocated: Money
    remaining: Money
    recorder: Actor
    created_at: Iso
    revision: number
    fulfilled_at: Iso | null
    cancellation: { at: Iso; by: Actor; reason: string } | null
    case?: MoneyCase
    allocations?: Allocation[]
    events?: PromiseEvent[]
}

export type PromiseList = {
    items: PromiseRow[]
    page: number
    page_size: number
    sort: string
    state: PromiseState[]
    total: number
    totals: { amount: Money; allocated: Money; remaining: Money }
    counts_by_state: Record<PromiseState, { count: number; amount: Money; allocated: Money }>
}

export type CasePromises = {
    case_id: string
    items: PromiseRow[]
    counts_by_state: Record<PromiseState, number>
}

/* ------------------------------------------------------------------- history */

export type Note = {
    id: string
    case_id: string
    author: Actor
    created_at: Iso
    updated_at: Iso | null
    text: string
    original_text: string
    corrected: boolean
    correction_count: number
    retired: boolean
    status: { id: string; name: string } | null
    contact_method: { id: string; name: string } | null
    reachability_id: string | null
    next_contact_date: Iso | null
    contact_number: string | null
    call_id: string | null
    revision: number
}

export type NoteHistory = {
    case_id: string
    hard_deleted: boolean
    note: Note | null
    revisions: Array<{
        revision_no: number
        kind: 'original' | 'correction'
        text: string | null
        reason: string | null
        corrected_by: Actor
        corrected_at: Iso | null
        stored: boolean
    }>
    events: Array<{
        id: number
        occurred_at: Iso
        action: string
        actor: Actor
        source: string
        reason: string | null
        request_id: string | null
        changes?: Record<string, { before: unknown; after: unknown }>
        deleted_row?: Record<string, unknown>
    }>
}

export type Contact = {
    id: string
    row_ids: string[]
    duplicate_rows: number
    contact: string
    contact_type: { id: string; name: string } | null
    is_default: boolean
    is_active: boolean
    is_blocked: boolean
    blocked_at: Iso | null
    blocked_by: string | null
    block_reason: string | null
    eligible: boolean
    created_at: Iso
    updated_at: Iso | null
    revision: number
}

export type DebtorContacts = {
    identity_number: string
    default_contact_id: string | null
    default_count: number
    needs_default: boolean
    contacts: Contact[]
    contact?: Contact
}

export type ContactHistoryItem = {
    occurred_at: Iso
    action: string
    kind: 'insert' | 'update' | 'delete'
    actor: Actor
    source: string
    reason: string | null
    request_id: string | null
    contact: string | null
    row_count: number
    row_ids: string[]
    changes?: Record<string, { before: unknown; after: unknown }>
    values?: Record<string, unknown>
}

export type CorrectionKind = 'identity' | 'profile' | 'attribute' | 'original_debt' | 'delinquency'
export const CORRECTION_KINDS: CorrectionKind[] = [
    'identity',
    'profile',
    'attribute',
    'original_debt',
    'delinquency',
]
export type CorrectionStatus = 'pending' | 'approved' | 'rejected' | 'cancelled'
export const CORRECTION_STATUSES: CorrectionStatus[] = ['pending', 'approved', 'rejected', 'cancelled']

export type Correction = {
    id: string
    case_id: string
    kind: CorrectionKind
    status: CorrectionStatus
    changes: Record<string, { before: unknown; after: unknown }>
    reason: string
    requested_by: Actor
    requested_at: Iso
    reviewed_by: Actor
    reviewed_at: Iso | null
    review_note: string | null
    base_revision: number
    applied_revision: number | null
    revision: number
    case: { id: string; name: string; account_number: string; identity_number: string } | null
    fields?: Array<{ field: string; before: unknown; after: unknown; current: unknown; stale: boolean }>
    stale?: boolean | null
    case_locked?: boolean
    case_revision?: number
    approved_total?: Money
    below_approved_payments?: boolean
    warnings?: Array<{ code: string; contacts_before?: number; contacts_after?: number; message: string }>
}
