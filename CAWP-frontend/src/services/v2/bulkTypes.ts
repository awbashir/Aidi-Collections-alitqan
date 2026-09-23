/**
 * Phase 2 wire types: private uploads and reviewed bulk jobs.
 *
 * Contract: review/phase2-framework.md §6 plus the kind reports
 * (phase2-assign / -archive / -notes / -inquiry), checked against the live
 * API (`CAWP-API/api/bulk.py`, `core/bulk/service.py`). Counts are exact
 * integers; money inside row details stays a decimal string.
 */

export type BulkSource = 'selection' | 'filter' | 'portfolio' | 'spreadsheet' | 'query'

export type BulkStatus =
    | 'queued'
    | 'validating'
    | 'ready'
    | 'expired'
    | 'confirmed'
    | 'running'
    | 'retrying'
    | 'succeeded'
    | 'partially_succeeded'
    | 'failed'
    | 'cancelled'

/** Every status, in the order the jobs screen shows them. */
export const BULK_STATUSES: BulkStatus[] = [
    'queued',
    'validating',
    'ready',
    'expired',
    'confirmed',
    'running',
    'retrying',
    'succeeded',
    'partially_succeeded',
    'failed',
    'cancelled',
]

/** A job in one of these states is still moving: poll it. */
export const BULK_ACTIVE: BulkStatus[] = ['queued', 'validating', 'confirmed', 'running', 'retrying']

export const BULK_TERMINAL: BulkStatus[] = ['succeeded', 'partially_succeeded', 'failed', 'cancelled', 'expired']

export type BulkEligibility = 'pending' | 'eligible' | 'excluded' | 'error'
export type BulkOutcome = 'pending' | 'succeeded' | 'skipped' | 'failed'
export type ArtifactKind = 'preview' | 'errors' | 'results'

export type MappingField = {
    key: string
    label_en: string
    label_ar: string
    required: boolean
    match: boolean
    max_length?: number
    description?: string
}

export type BulkKind = {
    key: string
    label_en: string
    label_ar: string
    permissions: string[]
    sources: BulkSource[]
    filter_lists: string[]
    read_only: boolean
    mapping_fields: MappingField[]
    match_by: string | null
    result_columns: string[]
    /** The caller holds one of `permissions`. */
    allowed: boolean
}

export type UploadSheet = {
    name: string
    header_row: number
    headers: string[]
    columns: number
    rows: number
    formula_cells: number
    /** Up to 5 data rows (formula text kept, never evaluated). */
    sample?: Array<Array<string | number | null>>
    complete: boolean
    unnamed_data_columns: number
}

export type UploadWarning = { code: string; message: string; count?: number }

export type BulkUpload = {
    id: string
    purpose: string
    original_name: string
    size_bytes: number
    sha256: string
    detected_type: 'xlsx' | 'csv'
    encoding: string | null
    delimiter: string | null
    sheets: UploadSheet[]
    row_count: number
    header_row: number
    formula_cells: number
    /** Always `not_scanned` today: there is no antivirus. Never shown as clean. */
    scan_status: string
    uploaded_by: string
    uploaded_at: string
    expires_at: string
    purged_at: string | null
    warnings: UploadWarning[]
}

export type BulkPerson = { id: string; name: string | null; user_name: string | null }

export type BulkCounts = {
    total: number
    prepared: number
    eligible: number
    excluded: number
    error: number
    processed: number
    succeeded: number
    skipped: number
    failed: number
}

export type BulkActions = {
    confirm: boolean
    cancel: boolean
    retry: boolean
    reopen: boolean
    download: boolean
}

export type BulkJob = {
    id: string
    kind: string
    kind_label: { en: string; ar: string } | null
    source: BulkSource
    source_summary: {
        count?: number
        list?: string
        query?: string
        portfolio_id?: string
        upload_id?: string
        sheet?: string
    }
    status: BulkStatus
    stored_status: BulkStatus
    phase: 'prepare' | 'execute'
    read_only: boolean
    params: Record<string, unknown>
    mapping: Record<string, string> | null
    sheet: string | null
    upload: {
        id: string
        original_name: string
        detected_type: string
        row_count: number
        purged: boolean
        expires_at: string
    } | null
    counts: BulkCounts
    progress: { phase: 'validating' | 'executing'; done: number; total: number; percent: number }
    revision: number
    created_by: BulkPerson | null
    created_at: string
    updated_at: string
    preview: { ready_at: string | null; expires_at: string | null; expired: boolean }
    needs_accept_partial: boolean
    confirmation: { by: BulkPerson | null; at: string; accept_partial: boolean } | null
    retry: { count: number; last_by: BulkPerson | null; last_at: string | null }
    cancel: {
        requested_by: BulkPerson | null
        requested_at: string | null
        reason: string | null
        cancelled_at: string | null
    } | null
    started_at: string | null
    execution_started_at: string | null
    finished_at: string | null
    attempt: number
    max_attempts: number
    failed_attempts: number
    worker: { id: string; lease_until: string | null; heartbeat_at: string | null } | null
    error: { code: string; message: string | null } | null
    reopened_from: string | null
    actions: BulkActions
}

export type BulkAttempt = {
    attempt: number
    phase: 'prepare' | 'execute'
    worker_id: string | null
    started_at: string | null
    finished_at: string | null
    outcome: string
    rows_processed: number
    error: string | null
}

export type BulkArtifact = {
    id: string
    kind: ArtifactKind
    file_name: string
    size_bytes: number
    sha256: string
    row_count: number
    created_at: string
    expires_at: string
    purged_at: string | null
    available: boolean
}

export type BulkJobDetail = BulkJob & { attempts: BulkAttempt[]; artifacts: BulkArtifact[] }

export type ByCode = { eligibility: 'excluded' | 'error'; code: string; message: string | null; count: number }

export type ByTarget = {
    user_id: string
    user_name: string | null
    name: string | null
    planned: number
    succeeded: number
    skipped: number
    failed: number
}

export type BulkPreview = {
    job_id: string
    status: BulkStatus
    revision: number
    ready: boolean
    expires_at: string | null
    expired: boolean
    counts: { total: number; eligible: number; excluded: number; error: number }
    by_code: ByCode[]
    needs_accept_partial: boolean
    can_confirm: boolean
    rows_limited_to_your_scope: boolean
    kind_summary: { by_target?: ByTarget[] } | null
}

export type BulkRow = {
    id: number
    row_no: number
    case_id: string | null
    case: { id: string; account_number: string; name: string } | null
    match_value: string | null
    raw: Record<string, unknown> | null
    mapped: Record<string, unknown> | null
    eligibility: BulkEligibility
    eligibility_code: string | null
    eligibility_message: string | null
    expected_revision: number | null
    outcome: BulkOutcome
    outcome_code: string | null
    outcome_message: string | null
    processed_at: string | null
    attempt: number | null
    details: Record<string, unknown> | null
}

export type BulkRowsPage = {
    items: BulkRow[]
    total: number
    page: number
    page_size: number
    rows_limited_to_your_scope: boolean
}

export type BulkJobsPage = {
    items: BulkJob[]
    total: number
    page: number
    page_size: number
    counts_by_status: Record<BulkStatus, number>
}

export type BulkCollector = { id: string; user_name: string; name: string | null }

/** Body of `POST /v2/bulk/<kind>`. */
export type BulkCreateBody = {
    source: BulkSource
    case_ids?: string[]
    filter?: { list: string; query: string }
    portfolio_id?: string
    upload_id?: string
    sheet?: string
    mapping?: Record<string, string>
    params: Record<string, unknown>
}
