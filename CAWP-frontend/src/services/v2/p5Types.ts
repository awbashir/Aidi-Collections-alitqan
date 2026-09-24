/**
 * Phase 5 wire types: human calling (manual log, wrap-up, click-to-call, live view,
 * supervisor review, calling admin), campaigns and PBX evidence.
 *
 * Contracts: review/phase5-{foundation,click-to-call,campaigns,evidence}.md, checked
 * against CAWP-API/api/calling*.py and services/calling/*. Numbers (contact values,
 * destinations) are null unless the caller holds `clints_details`. `duration_seconds:
 * null` means UNKNOWN, never 0. AMI "Success" means submitted, never connected.
 */

import type { Paged } from './types'

type Iso = string

export type UserRef = { id: string; name: string | null } | null

export const CALL_STATES = [
    'prepared',
    'authorized',
    'dispatched',
    'agent_ringing',
    'agent_answered',
    'customer_dialing',
    'customer_answered',
    'bridged',
    'ended',
    'failed',
    'cancelled',
    'uncertain',
] as const
export type CallState = (typeof CALL_STATES)[number]

export const LIVE_CALL_STATES: CallState[] = [
    'prepared',
    'authorized',
    'dispatched',
    'agent_ringing',
    'agent_answered',
    'customer_dialing',
    'customer_answered',
    'bridged',
]

export const CALL_MODES = ['manual', 'click_to_call', 'campaign'] as const
export type CallMode = (typeof CALL_MODES)[number]

export const OUTCOMES = [
    'connected',
    'no_answer',
    'busy',
    'promise_discussed',
    'dispute',
    'wrong_number',
    'do_not_call',
] as const
export type Outcome = (typeof OUTCOMES)[number]

/** Outcomes that claim a conversation: a PBX call needs bridge evidence for them. */
export const CONVERSATION_OUTCOMES: Outcome[] = ['connected', 'promise_discussed', 'dispute', 'wrong_number']

export type CallTimeline = {
    authorized_at: Iso | null
    dispatched_at: Iso | null
    agent_ringing_at: Iso | null
    agent_answered_at: Iso | null
    customer_dialing_at: Iso | null
    customer_answered_at: Iso | null
    bridged_at: Iso | null
    ended_at: Iso | null
}

export type CallWrapup = {
    id: string
    outcome: Outcome
    note_id: string | null
    follow_up_id: string | null
    callback_at: Iso | null
    promise_id: string | null
    contact_blocked: boolean
    calling_block_id: string | null
    reviewed_by: UserRef
    reviewed_at: Iso | null
    needs_review: boolean
    review_reason: 'case_locked' | 'case_archived' | null
    deferred_actions: Record<string, unknown> | null
    note_text?: string | null
}

export type CallReservation = {
    id: string
    reserved_at: Iso | null
    released_at: Iso | null
    release_source: string | null
    active: boolean
}

/** One call attempt (list row / detail). */
export type Call = {
    id: string
    mode: CallMode
    direction: 'outbound' | 'inbound'
    state: CallState
    case: { id: string; name: string | null; account_number: string | null; missing: boolean }
    contact: { id: string; value: string | null } | null
    destination: string | null
    collector: UserRef
    extension: string | null
    campaign_id: string | null
    created_at: Iso
    started_at: Iso | null
    timeline: CallTimeline
    duration_seconds: number | null
    duration_source: 'reported' | 'pbx' | null
    duration_known: boolean
    pbx: { epoch: string | null; channel_id: string | null } | null
    reason_code: string | null
    hangup_cause: number | null
    has_transcript: boolean
    wrapup: CallWrapup | null
    wrapup_pending: boolean
    revision: number
}

export type CallEvidence = {
    note: string
    events: Array<{
        id: number
        received_at: Iso | null
        source: string
        pbx_epoch: string | null
        event: string
        state: string | null
        pbx_ts: string | null
        uniqueid: string | null
        payload: Record<string, unknown>
    }>
    legs: Array<{
        pbx_epoch: string
        uniqueid: string
        role: string
        created_pbx_ts: string | null
        ringing_pbx_ts: string | null
        answered_pbx_ts: string | null
        bridged_pbx_ts: string | null
        bridge_id: string | null
        hungup_pbx_ts: string | null
        hangup_cause: number | null
    }>
    agi_decisions: Array<{ at: Iso | null; decision: string; reason: string | null }>
}

export type CallDetail = Call & {
    transcript: string | null
    reservation: CallReservation | null
    evidence?: CallEvidence
}

export type CallList = {
    items: Call[]
    total: number
    page: number
    page_size: number
    sort: '-time' | 'time'
    counts_by_state: Partial<Record<CallState, number>>
}

export type CallQuery = {
    page?: number
    page_size?: number
    sort?: '-time' | 'time'
    state?: string
    mode?: string
    outcome?: string
    collector_id?: string
    case_id?: string
    campaign_id?: string
    wrapup?: 'pending' | 'done'
    from?: string
    to?: string
}

/* ------------------------------------------------------------------ live view (P1) */

export type LiveEvent = {
    at_pbx: string | null
    at: Iso | null
    source: 'pbx' | 'agi'
    kind: string
    leg: 'agent' | 'customer' | 'other'
    event_id: number | null
    detail: Record<string, unknown>
}

export type ReviewFlag = {
    id: string
    kind: 'wrapup_needs_review' | 'late_evidence' | 'evidence_conflict' | 'reconciliation_unresolved'
    reason: string | null
    detail: Record<string, unknown> | null
    event_id: number | null
    blocks_calling: boolean
    created_at: Iso
    open: boolean
    resolved_at: Iso | null
    resolved_by: UserRef
    resolution_reason: string | null
}

export type Reconciliation = {
    id: number
    at: Iso
    source: 'service' | 'supervisor'
    actor: UserRef
    prior_state: string | null
    prior_reason: string | null
    decision: string
    code: string
    reason: string | null
    reservation_released: boolean
    inventory: Record<string, unknown>
    cdr: Record<string, unknown>
}

export type LiveCall = CallDetail & {
    dispatch: {
        state: string
        result_code: string | null
        queued_at: Iso | null
        expires_at: Iso | null
        claimed_at: Iso | null
        finished_at: Iso | null
        ami_response: string | null
        agent_ring_seconds: number | null
        max_call_seconds: number | null
        note: string
    } | null
    live: boolean
    conversation: { evidenced: boolean; customer_answered: boolean; rule: string }
    uncertain: { reason: string | null; since: Iso | null; note: string } | null
    flags: ReviewFlag[]
    reconciliations: Reconciliation[]
    actions: { can_cancel: boolean; can_wrapup: boolean; wrapup_outcomes: Outcome[]; can_resolve: boolean }
    events: LiveEvent[]
    poll_after_ms: number | null
    etag: string
}

/* ------------------------------------------------------------------ my calling */

export type WindowStatus = {
    riyadh_time: Iso
    riyadh_weekday: string
    configured: boolean
    open: boolean
    reason: string | null
    [k: string]: unknown
}

export type ExtensionRow = {
    id: string
    extension: string
    user: { id: string; name: string | null; is_active: boolean | null } | null
    legacy_extension: string | null
    assigned_at: Iso
    assigned_by: UserRef
    assign_reason: string | null
    source: string
    released_at: Iso | null
    released_by: UserRef
    release_reason: string | null
    active: boolean
    history_count?: number
    in_use?: boolean
    mirror_in_sync?: boolean
}

export type MyCallingInfo = {
    user_id: string
    extension: ExtensionRow | null
    preferences: {
        auto_answer_attempts: boolean
        devices: { microphone: string | null; speaker: string | null; ringer: string | null }
        revision: number
        saved: boolean
        updated_at: Iso | null
    }
    window: WindowStatus
    can_call: boolean
    reason: string | null
    active_attempt_id: string | null
}

export type CaseBlocks = {
    case_id: string
    do_not_call: boolean
    blocked_contacts: number
    items: Array<{
        id: string
        kind: 'do_not_call'
        reason: string | null
        source: string
        attempt_id: string | null
        created_by: UserRef
        created_at: Iso
        active: boolean
        released_at: Iso | null
        released_by: UserRef
        release_reason: string | null
    }>
}

/* ------------------------------------------------------------------ bodies */

export type WrapupNote = {
    text: string
    comment_status_id: string
    contact_method_id: string
    comment_reachability_id?: string
}

export type WrapupBody = {
    expected_revision?: number
    outcome: Outcome
    note: WrapupNote
    callback_at?: string
    promise?: { agreed: true; amount: string; due_date: string }
    replacement_default_id?: string
    wrong_number_reason?: string
    do_not_call_reason?: string
}

export type ManualCallBody = Omit<WrapupBody, 'expected_revision'> & {
    case_id: string
    contact_id?: string
    started_at: string
    duration_seconds?: number
    direction?: 'outbound' | 'inbound'
    transcript?: string
}

/* ------------------------------------------------------------------ review (P1) */

export type UncertainList = {
    items: Array<LiveCall | (CallDetail & { reconciliations: Reconciliation[]; flags: ReviewFlag[] })>
    total: number
    page: number
    page_size: number
}

export type ReviewList = {
    items: Array<ReviewFlag & { call: CallDetail }>
    total: number
    counts_by_kind: Partial<Record<ReviewFlag['kind'], number>>
    page: number
    page_size: number
    state: 'open' | 'resolved' | 'all'
}

/* ------------------------------------------------------------------ admin (P0) */

export type CallingServiceStatus = {
    pbx: {
        host: string | null
        epoch: string | null
        epoch_strength: string | null
        pbx_restarted: boolean | null
        connected_at: Iso | null
        connected: boolean
        disconnected_at: Iso | null
        disconnect_code: string | null
    } | null
    note: string
    in_flight_by_state: Partial<Record<CallState, number>>
    active_reservations: number
    agi_decisions_24h: { allow: number; deny: number }
    window: WindowStatus
}

export type CallingUser = {
    id: string
    name: string | null
    user_name: string
    legacy_extension: string | null
    owned_extension: string | null
}

export type ExtensionHistory = {
    extension: string
    items: ExtensionRow[]
    legacy_holders: Array<{ id: string; name: string | null; is_active: boolean }>
    active?: ExtensionRow | null
    legacy_conflicts?: string[]
}

export type DriftStatus =
    | 'adoptable'
    | 'legacy_invalid'
    | 'legacy_owned_by_other'
    | 'differs_from_owned'
    | 'mirror_missing'
    | 'user_inactive'

export type DriftRow = {
    user: { id: string; name: string | null; is_active: boolean }
    legacy_extension: string | null
    owned_extension: string | null
    legacy_shared_with: number
    status: DriftStatus
}

export type CallingWindow = { days: number[]; start: string; end: string }
export type CallingHoliday = { date: string; label?: string }

export type WindowsView = {
    policy: {
        windows: CallingWindow[]
        holidays: CallingHoliday[]
        revision: number
        updated_at: Iso | null
        updated_by: UserRef
        reason: string | null
    } | null
    status: WindowStatus
    history: Array<{ revision: number; changed_at: Iso; changed_by: UserRef; reason: string | null }>
}

/* ------------------------------------------------------------------ campaigns (P2) */

export const CAMPAIGN_STATUSES = ['draft', 'running', 'paused', 'draining', 'stopped'] as const
export type CampaignStatus = (typeof CAMPAIGN_STATUSES)[number]

export type CampaignSettings = {
    next_mode: 'auto' | 'manual'
    wrapup_delay_seconds: number
    max_attempts_per_day: number
    cooldown_minutes: number
    lease_seconds: number
}

export type CampaignPool = {
    contract_ids: string[]
    portfolio_ids: string[]
    filter: Record<string, unknown>
}

export type Campaign = {
    id: string
    name: string
    status: CampaignStatus
    pool: CampaignPool
    settings: CampaignSettings
    created_by: UserRef
    created_at: Iso
    updated_at: Iso | null
    started_at: Iso | null
    paused_at: Iso | null
    drain_requested_at: Iso | null
    stopped_at: Iso | null
    stop_reason: 'discarded' | 'stopped' | 'drained' | null
    members: { active: number; total: number }
    revision: number
    my_membership?: { active: boolean } | null
    my_readiness?: {
        state: Readiness
        state_since: Iso | null
        state_reason: string | null
        lease_until: Iso | null
        lease_live: boolean
        heartbeat_at: Iso | null
    } | null
    /* transition responses */
    cancelled_queued?: string[]
    drained?: boolean
}

export type Readiness = 'ready' | 'paused' | 'wrap_up' | 'offline'

export type MemberState = 'removed' | 'on_call' | 'wrap_up' | 'offline' | 'paused' | 'waiting_next' | 'ready'

export type DayCounts = {
    attempts: number
    conversations: number
    customer_answered: number
    wrapped_up: number
    failed: number
    cancelled: number
    uncertain: number
    live: number
    outcomes: Partial<Record<Outcome, number>>
}

export type CampaignMemberView = {
    user: UserRef
    active: boolean
    extension: string | null
    state: MemberState
    reason: string | null
    ready_at: Iso | null
    declared: {
        state: Readiness
        since: Iso | null
        reason: string | null
        lease_until: Iso | null
        lease_live: boolean
        heartbeat_at: Iso | null
    } | null
    next_requested_at: Iso | null
    last_dispatch_at: Iso | null
    blocked: { reason: string; detail: unknown; checked_at: Iso | null } | null
    current_attempt: {
        id: string
        state: CallState
        reason_code: string | null
        mode: CallMode
        campaign_id: string | null
        case: { id: string; name: string | null; account_number: string | null }
        authorized_at: Iso | null
        bridged_at: Iso | null
        ended_at: Iso | null
        this_campaign?: boolean
    } | null
    today: DayCounts
}

export type MyCampaignStatus = {
    campaign: Campaign
    member: CampaignMemberView
    state: MemberState
    reason: string | null
    ready_at: Iso | null
    next: { mode: 'auto' | 'manual'; pending: boolean; requested_at: Iso | null; expires_at: Iso | null }
    attempt: LiveCall | null
    heartbeat_after_ms: number
    poll_after_ms: number
    dispatch?: {
        attempt_id?: string
        case_id?: string
        contact_choice?: string
        priority?: string
        blocked?: string
        detail?: Record<string, unknown>
    } | null
}

export type PoolSummary = {
    size: number
    eligible_now: number
    remaining_today: number
    excluded: Record<string, number>
    evaluated_at: Iso
    cached?: boolean
}

export type CampaignLive = {
    campaign: Campaign
    transport: { connected: boolean; epoch: string | null }
    members: CampaignMemberView[]
    members_by_state: Partial<Record<MemberState, number>>
    pool: PoolSummary
    today: DayCounts
    all_time: { attempts: number; uncertain: number; awaiting_wrapup: number }
    outstanding_calls: number
    poll_after_ms: number
}

export type RosterItem = {
    user: UserRef
    active: boolean
    user_active: boolean
    extension: string | null
    added_by: UserRef
    added_at: Iso | null
    changed_by: UserRef
    changed_at: Iso | null
    change_reason: string | null
}

export type PoolItem = {
    case: { id: string; name: string | null; account_number: string | null; owner_id: string | null }
    reason: string
    priority: 'callback_due' | 'normal'
    last_contact_at: Iso | null
    callback_at: Iso | null
    attempts_today: number
    last_tried_at: Iso | null
    last_excluded_reason: string | null
}

export type PoolListing = Paged<PoolItem> & { summary: PoolSummary }

export type CampaignBody = {
    name?: string
    pool?: CampaignPool
    settings?: Partial<CampaignSettings>
    member_ids?: string[]
}

/* ------------------------------------------------------------------ PBX evidence (P4) */

export type PbxRecording = {
    has_recording: boolean
    file: string | null
    url: string | null
    sources?: string[]
}

export type PbxCdrRecord = {
    call_at: Iso | null
    src: string | null
    dst: string | null
    direction: string
    channel: string | null
    dstchannel: string | null
    disposition: string | null
    pbx_status: string | null
    duration_seconds: number | null
    billsec: number | null
    duration_known: boolean
    uniqueid: string | null
    attempt_link: string | null
    recording: PbxRecording | null
}

export type PbxEvidence = {
    attempt_id: string
    state: CallState
    duration_seconds: number | null
    duration_known: boolean
    cdr: { status: 'available' | 'unavailable' | 'disabled'; records: PbxCdrRecord[]; [k: string]: unknown }
    imported: Array<Record<string, unknown>>
    recordings: PbxRecording[]
    recording_access: boolean
    note: string
}
