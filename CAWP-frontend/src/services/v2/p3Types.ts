/**
 * Wire types of the Phase 3 v2 API: internal requests, repayment plans,
 * targets, sessions and `/v2/me` (review/phase3-{requests,plans,admin}.md,
 * checked against the live API on 2026-09-24).
 *
 * Money is always an exact decimal **string** ("1000.00"); civil dates are
 * Asia/Riyadh `YYYY-MM-DD`; instants are ISO-8601 UTC. Nothing here is ever
 * converted to a float.
 */
import type { CivilDate, Iso, Money } from './types'

/* ------------------------------------------------------------------ requests */

export type RequestDepartment = 'legal' | 'finance' | 'operations'
export const REQUEST_DEPARTMENTS: RequestDepartment[] = ['legal', 'finance', 'operations']

export type RequestStatus = 'open' | 'in_review' | 'resolved' | 'cancelled'
export const REQUEST_STATUSES: RequestStatus[] = ['open', 'in_review', 'resolved', 'cancelled']

export type RequestActor = { id: string; name: string; user_name?: string } | null

export type RequestCase = {
    id: string
    /** The case was hard-deleted; only `id` is known. */
    missing: boolean
    name?: string
    accountNumber?: string
    identityNumber?: string
    original_amount?: Money
    is_locked?: boolean
    is_archived?: boolean
    owner?: RequestActor
    portfolio?: { id: string; name: string } | null
    contract?: { id: string; name: string } | null
}

export type RequestEventKind = 'created' | 'commented' | 'taken' | 'resolved' | 'reopened' | 'cancelled'

export type RequestEvent = {
    id: number
    event: RequestEventKind
    actor: RequestActor
    occurred_at: Iso
    status_before: RequestStatus | null
    status_after: RequestStatus
    reason: string | null
    body: string | null
    details: { document_ids?: string[] } | null
}

export type RequestAttachment = {
    document_id: string
    event_id: number
    added_by: RequestActor
    added_at: Iso
    document: {
        id: string
        missing: boolean
        display_name?: string | null
        original_name?: string | null
        file_type?: string | null
        is_deleted?: boolean
        created_at?: Iso | null
        download_url?: string | null
    }
}

export type RequestActions = {
    comment: boolean
    take: boolean
    resolve: boolean
    reopen: boolean
    cancel: boolean
}

export type RequestRow = {
    id: string
    case_id: string
    department: RequestDepartment
    subject: string
    body: string
    amount: Money | null
    related_date: CivilDate | null
    status: RequestStatus
    requested_by: RequestActor
    created_at: Iso
    created_date: CivilDate
    reviewer: RequestActor
    review_started_at: Iso | null
    resolved_by: RequestActor
    resolved_at: Iso | null
    resolution: string | null
    cancelled_by: RequestActor
    cancelled_at: Iso | null
    cancel_reason: string | null
    reopen_count: number
    last_event_at: Iso
    comment_count: number
    attachment_count: number
    revision: number
    case: RequestCase
}

export type RequestDetail = RequestRow & {
    events: RequestEvent[]
    attachments: RequestAttachment[]
    actions: RequestActions
    as_of?: { at: Iso; riyadh_date: CivilDate }
}

export type RequestViewer = {
    user_id: string
    scope: string
    can_create: boolean
    reviewer_departments: RequestDepartment[]
}

export type RequestList = {
    counts: Record<RequestStatus, number>
    department_counts: Record<RequestDepartment, number>
    total: number
    page: number
    pageSize: number
    items: RequestRow[]
    filters: {
        department: RequestDepartment[]
        status: RequestStatus[]
        requester: string | null
        reviewer: string | null
        case_id: string | null
        date_from: CivilDate | null
        date_to: CivilDate | null
    }
    viewer: RequestViewer
    as_of: { at: Iso; riyadh_date: CivilDate }
    /** Only on GET /v2/cases/<id>/requests. */
    case_id?: string
}

export type RequestQuery = {
    department?: string
    status?: string
    requester?: string
    reviewer?: string
    case_id?: string
    date_from?: string
    date_to?: string
    page?: number
    pageSize?: number
}

export type NewRequestBody = {
    department: RequestDepartment
    subject: string
    body: string
    amount?: string
    related_date?: string
    document_ids?: string[]
}

/* --------------------------------------------------------------------- plans */

export type PlanFrequency = 'weekly' | 'monthly'
export type PlanState = 'active' | 'completed' | 'broken' | 'cancelled'
export const PLAN_STATES: PlanState[] = ['active', 'completed', 'broken', 'cancelled']

export type InstallmentState = 'upcoming' | 'due' | 'overdue' | 'partially_paid' | 'paid' | 'cancelled'
export const INSTALLMENT_STATES: InstallmentState[] = [
    'upcoming',
    'due',
    'overdue',
    'partially_paid',
    'paid',
    'cancelled',
]

export type PlanActor = { id: string; name: string } | null

export type PlanAllocation = {
    id: string
    plan_id: string
    installment_id: string
    payment_id: string
    amount: Money
    source: 'auto' | 'manual'
    /** A manual allocation's reason. */
    note: string | null
    created_at: Iso
    created_by: string | null
    active: boolean
    voided_at: Iso | null
    voided_by: string | null
    void_reason: string | null
}

export type PlanInstallment = {
    id: string
    seq: number
    due_date: CivilDate
    amount: Money
    allocated: Money
    remaining: Money
    state: InstallmentState
    paid_at: Iso | null
    allocations: PlanAllocation[]
}

export type PlanEvent = {
    /* `completed` / `reopened_unpaid`: the plan closes itself when fully paid and a
     * reversal can reopen it (decision 2026-09-24). Unknown future events keep their name. */
    event:
        | 'created'
        | 'cancelled'
        | 'allocated'
        | 'deallocated'
        | 'allocation_voided'
        | 'completed'
        | 'reopened_unpaid'
        | (string & Record<never, never>)
    at: Iso
    actor: PlanActor
    amount: Money | null
    reason: string | null
    installment_id: string | null
    allocation_id: string | null
    payment_id: string | null
    source: 'api' | 'legacy'
    details: Record<string, unknown> | null
}

export type PlanCase = {
    id: string
    name: string
    account_number: string
    identity_number: string
    owner: PlanActor
    portfolio: PlanActor
    contract: PlanActor
}

export type Plan = {
    id: string
    case_id: string
    state: PlanState
    /** Stored status. `completed` is stored (auto-closed when fully paid) since the 2026-09-24 decision. */
    status: 'active' | 'cancelled' | 'completed'
    frequency: PlanFrequency
    start_date: CivilDate
    end_date: CivilDate
    total_amount: Money
    installment_count: number
    split_mode: 'count' | 'amount'
    installment_amount: Money | null
    allocated: Money
    remaining: Money
    overdue_amount: Money
    counts: Record<InstallmentState, number>
    oldest_overdue_date: CivilDate | null
    next_due_date: CivilDate | null
    broken_threshold: number
    outstanding_at_creation: Money
    note: string | null
    created_by: PlanActor
    created_at: Iso
    revision: number
    completed_at: Iso | null
    cancellation: { at: Iso; by: PlanActor; reason: string } | null
    case?: PlanCase
    installments?: PlanInstallment[]
    events?: PlanEvent[]
}

export type PlanStateCount = { count: number; total_amount: Money; allocated: Money; overdue_amount: Money }

export type PlanList = {
    items: Plan[]
    page: number
    page_size: number
    sort: string
    state: PlanState[]
    total: number
    totals: { total_amount: Money; allocated: Money; remaining: Money; overdue_amount: Money }
    counts_by_state: Record<PlanState, PlanStateCount>
    broken_threshold: number
}

export type PlanQuery = {
    state?: string
    case_id?: string
    owner_id?: string
    created_by?: string
    portfolio_id?: string
    contract_id?: string
    frequency?: string
    created_from?: string
    created_to?: string
    next_due_from?: string
    next_due_to?: string
    sort?: string
    page?: number
    page_size?: number
}

export type CasePlans = {
    case_id: string
    balance: {
        case_id: string
        original: Money
        approved: Money
        reversed: Money
        pending: Money
        rejected: Money
        outstanding: Money
        overpaid: boolean
    }
    active_plan_id: string | null
    broken_threshold: number
    items: Plan[]
}

export type PlanDraft = {
    total_amount: string
    frequency: PlanFrequency
    start_date: string
    installment_count?: number
    installment_amount?: string
    note?: string
}

export type PlanPreview = {
    case_id: string
    frequency: PlanFrequency
    start_date: CivilDate
    end_date: CivilDate
    total_amount: Money
    installment_count: number
    split_mode: 'count' | 'amount'
    installment_amount: Money | null
    installments: Array<{ seq: number; due_date: CivilDate; amount: Money }>
    balance: { original: Money; approved: Money; outstanding: Money }
    rules: { split: string; due_dates: string }
}

/* ----------------------------------------------------------- me / sessions */

export type Me = {
    user: {
        id: string
        user_name: string
        frist_name?: string
        last_name?: string
        name?: string
        role_id?: string
        is_active?: boolean
        contract_id?: string | null
        [k: string]: unknown
    }
    role: { id: string; name: string } | null
    branch: { id: string; name: string } | null
    permissions: Record<string, boolean>
    scope: { kind: 'collector' | 'supervisor' | 'super'; supervisor_id: string | null }
    session: { sessions_valid_after: Iso | null; token_issued_at: Iso | null; token_expires_at: Iso | null }
}

export type SignOutResult = { user_id: string; sessions_valid_after: Iso | null }
export type ActiveResult = { user_id: string; is_active: boolean; sessions_valid_after: Iso | null }

/* ------------------------------------------------------------------- targets */

export type TargetKind = 'amount' | 'percent' | 'none'
export type TargetSubject = 'user' | 'contract'
export type TargetSource = 'legacy' | 'v2' | 'imported_baseline' | 'legacy_columns'

export type TargetInForce =
    | { known: false }
    | {
          known: true
          kind: TargetKind
          value: string
          revision: number | null
          from_month: string | null
          source: TargetSource
      }

export type TargetRow = {
    id: number
    subject_type: TargetSubject
    subject_id: string
    month: string
    revision: number
    kind: TargetKind
    value: string
    legacy: { amount: number; percent: number; is_percentage: boolean } | null
    source: TargetSource
    imported_baseline: boolean
    set_by: string | null
    set_at: Iso
    reason: string | null
}

export type TargetHistory = {
    subject_type: TargetSubject
    subject_id: string
    current_month: string
    legacy_current: { amount: number; percent: number; is_percentage: boolean; kind: TargetKind; value: string }
    months: Array<{ month: string; locked: boolean; revision: number; in_force: TargetInForce }>
    history: TargetRow[]
}

export type TargetSetResult = { target: TargetRow; month: string; revision: number; mirrored_to_legacy: boolean }

export type AchievementTarget = {
    known: boolean
    amount: Money | null
    source?: 'user' | 'contract' | null
    user_target?: TargetInForce
    contract_target?: TargetInForce
    percent_base: Money | null
}

export type AchievementCollector = {
    user_id: string
    user_name: string
    name: string
    contract_id: string | null
    contract_name: string | null
    target: AchievementTarget
    actual: Money
    payments_count: number
    actual_recorded: Money
    recorded_count: number
    achievement_percent: string | null
    remaining: Money | null
    rank: number
}

export type AchievementContract = {
    contract_id: string
    name: string
    is_active: boolean
    target: AchievementTarget
    collectors_target_sum: Money
    actual: Money
    payments_count: number
    achievement_percent: string | null
    remaining: Money | null
}

export type Achievement = {
    month: string
    view: 'all' | 'own'
    period: { first_day: CivilDate; last_day: CivilDate; timezone: string; locked: boolean }
    collectors: AchievementCollector[]
    contracts: AchievementContract[]
    totals: {
        collectors: number
        target: Money
        targets_unknown: number
        actual: Money
        actual_recorded: Money
        all_approved_recovery: Money | null
        unassigned_cases_recovery: Money | null
    }
}
