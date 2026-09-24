/**
 * Phase 4 wire types: v2 reports, scoped selector options and private report
 * exports (`CAWP-API/api/reports_v2.py`, `core/reports/*`; the contract and real
 * examples are in `review/phase4-framework.md`).
 *
 * Money is always an exact decimal string ("1234.50"); instants are ISO UTC
 * ("…Z") and shown in Asia/Riyadh; civil dates are "YYYY-MM-DD". The viewer is
 * driven entirely by the definition (`GET /v2/reports/<id>`), so filter and
 * column types the frontend does not know yet are still typed loosely.
 */

export type Bilingual = { en: string; ar: string }

export type ReportFormat = 'csv' | 'xlsx' | 'xlsx_ar'
export const REPORT_FORMATS: ReportFormat[] = ['csv', 'xlsx', 'xlsx_ar']

/** One entry of `GET /v2/reports` (only the reports whose key the caller holds). */
export type ReportSummary = {
    id: string
    family: string
    labels: Bilingual
    description: Bilingual
    permissions: string[]
    formats: ReportFormat[]
}

export type ReportCatalogue = {
    items: ReportSummary[]
    total: number
    families: { family: string; reports: string[] }[]
}

/* ------------------------------------------------------------------ filters */

type FilterBase = {
    key: string
    labels: Bilingual
    /** The query parameter names of this filter (what the rows endpoint accepts). */
    params: string[]
    required: boolean
    help: Bilingual | null
}

/** `date_range` = inclusive civil dates; `timestamp_range` = Riyadh day bounds over an instant. */
export type DateRangeFilterDef = FilterBase & {
    type: 'date_range' | 'timestamp_range'
    semantics?: string
    timezone?: string
    max_days?: number | null
    /** Server-side default when neither end is sent (RPT-18/20/21/24/25): `today` or `month` (1st → today). */
    default?: 'today' | 'month' | string | null
}

export type EnumChoice = { value: string; labels: Bilingual }

export type EnumFilterDef = FilterBase & {
    type: 'enum'
    choices: EnumChoice[]
    multi: boolean
    /** Applied by the server when the parameter is absent (RPT-23 mode, RPT-24 section). */
    default: string[] | null
}

export type IdsFilterDef = FilterBase & {
    type: 'ids'
    selector: { field: string; entity: string; endpoint: string }
    /** Filters whose current values narrow this selector's candidates. */
    depends_on: string[]
    max_ids: number
}

export type TextFilterDef = FilterBase & { type: 'text'; semantics?: string; max_len: number }

export type AmountRangeFilterDef = FilterBase & { type: 'amount_range'; semantics?: string }

/** RPT-19 `month=YYYY-MM` (default the current Riyadh month). */
export type MonthFilterDef = FilterBase & { type: 'month'; format?: string; default?: string }

/** One Riyadh civil day (`day`, `as_of`), default today. */
export type DayFilterDef = FilterBase & { type: 'date'; semantics?: string; default?: string }

/** RPT-23 `from_hour` / `to_hour`: inclusive Riyadh hours, default 8–17. */
export type HourRangeFilterDef = FilterBase & { type: 'hour_range'; default?: { from: number; to: number } }

/** A filter type this frontend does not know: rendered as plain text inputs on its params. */
export type UnknownFilterDef = FilterBase & { type: string }

export type ReportFilterDef =
    | DateRangeFilterDef
    | EnumFilterDef
    | IdsFilterDef
    | TextFilterDef
    | AmountRangeFilterDef
    | MonthFilterDef
    | DayFilterDef
    | HourRangeFilterDef

/* ------------------------------------------------------------------ columns, totals, sorts */

export type ColumnType = 'text' | 'int' | 'money' | 'date' | 'datetime' | 'enum' | 'bool' | 'uuid' | 'json'

export type ReportColumnDef = {
    key: string
    labels: Bilingual
    type: ColumnType | string
    money: boolean
    currency: string | null
    /** An employee field (core/user_privacy.py): null for a viewer without the right, unless it is their own row. */
    protected: 'identity' | 'target' | null
    role: string | null
    export: boolean
    enum: EnumChoice[] | null
}

export type ReportTotalDef = {
    key: string
    labels: Bilingual
    type: 'int' | 'money'
    currency: string | null
    /** True: a null total means unknown (never shown as 0). */
    nullable?: boolean
}

export type ReportSortDef = { key: string; labels: Bilingual }

export type ReportDefinition = ReportSummary & {
    filters: (ReportFilterDef | UnknownFilterDef)[]
    columns: ReportColumnDef[]
    sorts: ReportSortDef[]
    default_sort: string
    totals: ReportTotalDef[]
    fixed: Bilingual[]
    legacy: { endpoint?: string | null; screen?: string | null; key?: string | null } | null
    paging: { default_page_size: number; max_page_size: number }
}

export type ReportCell = string | number | boolean | null | Record<string, unknown> | unknown[]
export type ReportRow = Record<string, ReportCell>

/** `GET /v2/reports/<id>/rows`: one page plus the exact totals of the full result, in one snapshot. */
export type ReportRows = {
    report: string
    items: ReportRow[]
    total: number
    totals: Record<string, string | number | null>
    page: number
    page_size: number
    pages: number
    sort: string
    /** Normalized filter values (server defaults applied). */
    filters: Record<string, unknown>
    /** The same as flat query parameters (what the export body takes). */
    params: Record<string, string>
    snapshot_at: string
}

/* ------------------------------------------------------------------ selector options */

export type ReportOption = { id: string; label: string | null; secondary: string | null; active: boolean }

export type ReportOptionsPage = {
    report: string
    field: string
    entity: string
    q: string | null
    depends_on: Record<string, unknown>
    items: ReportOption[]
    total: number
    page: number
    page_size: number
    pages: number
    /** The `selected` ids resolved inside the same candidate set (labels beyond page one). */
    selected: ReportOption[]
}

/* ------------------------------------------------------------------ exports */

export type ReportExportStatus = 'queued' | 'running' | 'retrying' | 'succeeded' | 'failed' | 'expired'
export const REPORT_EXPORT_STATUSES: ReportExportStatus[] = [
    'queued',
    'running',
    'retrying',
    'succeeded',
    'failed',
    'expired',
]
export const ACTIVE_EXPORT_STATUSES: ReportExportStatus[] = ['queued', 'running', 'retrying']

export type ReportExportAttempt = {
    attempt: number
    worker_id: string | null
    started_at: string | null
    finished_at: string | null
    outcome: string
    rows_written: number
    error: string | null
}

export type ReportExport = {
    id: string
    report: { id: string; labels: Bilingual | null; family: string | null }
    format: ReportFormat
    status: ReportExportStatus
    stored_status: string
    definition: {
        filters: Record<string, unknown> | null
        params: Record<string, string> | null
        sort: string | null
        columns: string[] | null
        version: number | null
    }
    requested_by: string
    requested_at: string
    started_at: string | null
    cutoff_at: string | null
    finished_at: string | null
    progress: { rows_written: number; rows_expected: number | null; percent: number }
    row_count: number | null
    totals: Record<string, string | number | null> | null
    artifact: {
        file_name: string
        content_type: string | null
        size_bytes: number
        sha256: string
        expires_at: string | null
        purged_at: string | null
        available: boolean
    } | null
    attempt: number
    max_attempts: number
    failed_attempts: number
    error: { code: string; message: string | null } | null
    actions: { download: boolean }
    attempts?: ReportExportAttempt[]
}

export type ReportExportsPage = {
    items: ReportExport[]
    total: number
    page: number
    page_size: number
    filters: { report: string[] | null; status: string[] | null }
}

export type ReportExportsQuery = {
    report?: string
    status?: string
    page?: number
    page_size?: number
}
