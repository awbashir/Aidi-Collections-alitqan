/**
 * Report center builders (docs/report-builder-plan.md), CAWP-API/api/report_builder.py.
 *
 *   GET  /v2/bi/datasets                       datasets, dimensions, measures
 *   GET  /v2/bi/datasets/<ds>/values/<field>   scoped value list (top 50)
 *   POST /v2/bi/query (+ /export)              spec -> pivot (or .xlsx)
 *   GET/POST/PUT/DELETE /v2/bi/reports         saved BI reports
 *   GET  /v2/sql/dictionary                    data dictionary (ar/en)
 *   GET  /v2/sql/status                        runner configured?
 *   POST /v2/sql/build | /v2/sql/run (+ /export)
 *   GET/POST/PUT/DELETE /v2/sql/queries        saved SQL queries
 *   GET  /v2/report-builder/runs               run history
 *
 * Money and numeric values are exact decimal strings. Same transport as ./api.ts; the POST
 * reads send a fresh key (the server does not need one for reads).
 */
import BaseService from '../BaseService'
import { V2Error, newIdempotencyKey, v2Delete, v2Get, v2Post, v2Put } from './http'

export type Labels = { en: string; ar: string }
export type DimType = 'text' | 'date' | 'bool' | 'int'
export type MeasureType = 'money' | 'int' | 'number'
export type Grain = 'day' | 'week' | 'month' | 'quarter' | 'year'

export type BiDimension = { key: string; type: DimType; labels: Labels; description: Labels }
export type BiMeasure = { key: string; type: MeasureType; labels: Labels }
export type BiDataset = {
    key: string
    labels: Labels
    description: Labels
    default_date: string
    dimensions: BiDimension[]
    measures: BiMeasure[]
}

export type ZoneField = { field: string; grain?: Grain | null }
export type BiFilterOp = 'in' | 'not_in' | 'contains' | 'is_empty' | 'not_empty' | 'between' | 'eq'
export type BiFilter = { field: string; op: BiFilterOp; value?: unknown }
export type BiSpec = {
    dataset: string
    rows: ZoneField[]
    columns: ZoneField[]
    values: string[]
    filters: BiFilter[]
}
export type ChartKind = 'bar' | 'line' | 'pie' | 'table'

type Cell = string | number | boolean | null
export type BiResult = {
    dataset: string
    spec: BiSpec
    fields: {
        rows: (ZoneField & { type: DimType })[]
        columns: (ZoneField & { type: DimType })[]
        values: BiMeasure[]
    }
    row_keys: Cell[][]
    column_keys: Cell[][]
    cells: { r: number; c: number; m: (string | number | null)[] }[]
    row_totals: ((string | number | null)[] | null)[] | null
    column_totals: ((string | number | null)[] | null)[] | null
    grand_total: (string | number | null)[] | null
    truncated: boolean
    columns_truncated: boolean
    ms: number
}

export type Saved<P extends string, V> = {
    id: string
    owner_id: string
    owner_name: string | null
    name: string
    shared: boolean
    mine: boolean
    created_at: string
    updated_at: string
    revision: number
} & Record<P, V>
export type SavedBiReport = Saved<'spec', BiSpec & { chart?: ChartKind }>
export type SavedSqlQuery = Saved<'sql_text', string> & { builder: SqlBuilderSpec | null }

export type DictColumn = { name: string; type: string; labels: Labels; description: Labels }
export type DictTable = { name: string; labels: Labels; description: Labels; columns: DictColumn[] }
export type DictJoin = { left: string; left_column: string; right: string; right_column: string }
export type Dictionary = { schema: string; tables: DictTable[]; joins: DictJoin[] }

export type SqlColRef = { table: string; column: string }
export type SqlAgg = 'count' | 'count_distinct' | 'sum' | 'avg' | 'min' | 'max'
export type SqlOp = '=' | '!=' | '>' | '>=' | '<' | '<=' | 'between' | 'in' | 'not_in' | 'contains' | 'starts_with' | 'is_null' | 'not_null'
export type SqlBuilderSpec = {
    table: string
    joins: { table: string; type: 'left' | 'inner'; on: [string, string, string, string] }[]
    columns: (SqlColRef & { agg?: SqlAgg | null })[]
    filters: (SqlColRef & { op: SqlOp; value?: unknown })[]
    order_by: { index: number; dir: 'asc' | 'desc' }[]
    limit: number
}
export type SqlStatus = { configured: boolean; max_rows: number; max_export_rows: number; timeout_ms: number }
export type SqlResult = {
    columns: { name: string; type: string }[]
    rows: Cell[][]
    row_count: number
    truncated: boolean
    ms: number
    limit: number
}
export type BuilderRun = {
    id: string
    user_id: string
    user_name: string | null
    kind: 'bi_query' | 'bi_export' | 'sql_run' | 'sql_export'
    dataset: string | null
    spec: unknown
    sql_text: string | null
    row_count: number | null
    truncated: boolean
    duration_ms: number | null
    outcome: 'ok' | 'refused' | 'error' | 'timeout'
    error_code: string | null
    created_at: string
}

const key = () => newIdempotencyKey()

/** POST that answers a file: saved under the server's file name, or a V2Error. */
async function postDownload(url: string, data: unknown, params: Record<string, string>, fallback: string) {
    let res
    try {
        res = await BaseService({ url, method: 'post', data, params, responseType: 'blob', timeout: 0,
                                  headers: { 'Idempotency-Key': key() } })
    } catch (err) {
        const ax = err as { response?: { status: number; data?: Blob } }
        if (ax?.response) {
            let body: { code?: string; message?: string; error?: unknown } = {}
            try {
                body = JSON.parse(await (ax.response.data as Blob).text())
            } catch {
                /* not JSON */
            }
            throw new V2Error(ax.response.status, body.code || 'unknown', body.message || '', body.error ?? null)
        }
        throw new V2Error(0, 'network_error', (err as Error)?.message || 'network error', null)
    }
    const disposition = String(res.headers?.['content-disposition'] || '')
    const name = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(disposition)?.[1] || fallback
    const href = URL.createObjectURL(res.data as Blob)
    const a = document.createElement('a')
    a.href = href
    a.download = decodeURIComponent(name)
    a.rel = 'noopener'
    document.body.appendChild(a)
    a.click()
    a.remove()
    setTimeout(() => URL.revokeObjectURL(href), 10000)
}

export const biApi = {
    datasets: (signal?: AbortSignal) => v2Get<{ items: BiDataset[]; grains: Grain[] }>('/v2/bi/datasets', undefined, signal),
    values: (dataset: string, field: string, q?: string, signal?: AbortSignal) =>
        v2Get<{ items: { value: string | number; count: number }[] }>(
            `/v2/bi/datasets/${encodeURIComponent(dataset)}/values/${encodeURIComponent(field)}`, { q }, signal),
    query: (spec: BiSpec) => v2Post<BiResult>('/v2/bi/query', { spec }, key()),
    exportXlsx: (spec: BiSpec, lang: string) =>
        postDownload('/v2/bi/query/export', { spec }, { lang }, `bi-${spec.dataset}.xlsx`),
    reports: (signal?: AbortSignal) => v2Get<{ items: SavedBiReport[] }>('/v2/bi/reports', undefined, signal),
    save: (body: { name: string; spec: BiSpec; chart: ChartKind; shared: boolean }, idem: string) =>
        v2Post<SavedBiReport>('/v2/bi/reports', body, idem),
    update: (id: string, body: Partial<{ name: string; spec: BiSpec; chart: ChartKind; shared: boolean }> & { expected_revision: number }) =>
        v2Put<SavedBiReport>(`/v2/bi/reports/${id}`, body, key()),
    remove: (id: string) => v2Delete<{ id: string }>(`/v2/bi/reports/${id}`, key()),
}

export const sqlApi = {
    dictionary: (signal?: AbortSignal) => v2Get<Dictionary>('/v2/sql/dictionary', undefined, signal),
    status: (signal?: AbortSignal) => v2Get<SqlStatus>('/v2/sql/status', undefined, signal),
    build: (spec: SqlBuilderSpec) => v2Post<{ sql: string }>('/v2/sql/build', { spec }, key()),
    run: (sql: string) => v2Post<SqlResult>('/v2/sql/run', { sql }, key()),
    exportXlsx: (sql: string, lang: string) => postDownload('/v2/sql/run/export', { sql }, { lang }, 'sql-result.xlsx'),
    queries: (signal?: AbortSignal) => v2Get<{ items: SavedSqlQuery[] }>('/v2/sql/queries', undefined, signal),
    save: (body: { name: string; sql_text: string; builder: SqlBuilderSpec | null; shared: boolean }, idem: string) =>
        v2Post<SavedSqlQuery>('/v2/sql/queries', body, idem),
    update: (id: string, body: Partial<{ name: string; sql_text: string; builder: SqlBuilderSpec | null; shared: boolean }> & { expected_revision: number }) =>
        v2Put<SavedSqlQuery>(`/v2/sql/queries/${id}`, body, key()),
    remove: (id: string) => v2Delete<{ id: string }>(`/v2/sql/queries/${id}`, key()),
    runs: (q: { kind?: string; all?: boolean; page?: number }, signal?: AbortSignal) =>
        v2Get<{ items: BuilderRun[]; total: number; page: number; page_size: number }>(
            '/v2/report-builder/runs', { kind: q.kind, all: q.all ? 1 : undefined, page: q.page }, signal),
}
