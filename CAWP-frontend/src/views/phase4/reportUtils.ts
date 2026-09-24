/**
 * Phase 4 helpers shared by the reports hub and the generic viewer: labels in
 * the current language, the client-side filter defaults the report notes ask
 * for, the URL-hash schema built from a definition, request parameters, exact
 * money sums for charts, and the "classic" legacy screen of each report.
 *
 * Nothing here decides a business rule: the API validates every parameter,
 * applies scope and computes every total. These helpers only choose what to
 * send and how to show what comes back.
 */
import { hashCodec } from '@/utils/hooks/useHashState'
import type { HashCodec, HashSchema } from '@/utils/hooks/useHashState'
import { riyadhToday } from '@/utils/v2/format'
import type {
    Bilingual,
    DateRangeFilterDef,
    EnumFilterDef,
    HourRangeFilterDef,
    ReportColumnDef,
    ReportDefinition,
    ReportFilterDef,
    UnknownFilterDef,
} from '@/services/v2'

export type AnyFilterDef = ReportFilterDef | UnknownFilterDef

/** A bilingual pair in the current language (falls back to the other one). */
export const pick = (l: Bilingual | null | undefined, ar: boolean): string =>
    !l ? '' : ar ? l.ar || l.en || '' : l.en || l.ar || ''

/* ------------------------------------------------------------------ Riyadh calendar */

/** First and last civil day of the current Asia/Riyadh month. */
export function riyadhMonthBounds(today = riyadhToday()): { from: string; to: string } {
    const [y, m] = today.split('-').map(Number)
    const last = new Date(Date.UTC(y, m, 0)).getUTCDate()
    const mm = String(m).padStart(2, '0')
    return { from: `${y}-${mm}-01`, to: `${y}-${mm}-${String(last).padStart(2, '0')}` }
}

/* ------------------------------------------------------------------ defaults */

/**
 * Per-report defaults the review notes ask for, where the API has none:
 * - RPT-16: the current Riyadh month on the callback date (the legacy screen
 *   always sent it; the API lists every callback without it).
 * - RPT-04/05: `created` is required; the legacy screens defaulted to a date
 *   pair, so the current Riyadh month (a narrow window keeps RPT-04 fast).
 */
const REPORT_DEFAULTS: Record<string, (month: { from: string; to: string }) => Record<string, string>> = {
    'RPT-16': (m) => ({ callback_from: m.from, callback_to: m.to }),
    'RPT-04': (m) => ({ created_from: m.from, created_to: m.to }),
    'RPT-05': (m) => ({ created_from: m.from, created_to: m.to }),
}

/**
 * The value every filter parameter starts with (flat params, '' = not set).
 *
 * Server defaults (RPT-18…25: `today`, `month`, the current month, hours 8–17,
 * mode `notes`, section `summary`) are mirrored here so the filter shows the
 * value the API will apply and the request states it explicitly. A required
 * date range without a server default gets the current Riyadh month.
 */
export function clientDefaults(def: ReportDefinition): Record<string, string> {
    const today = riyadhToday()
    const month = riyadhMonthBounds(today)
    const out: Record<string, string> = {}
    def.filters.forEach((f) => {
        f.params.forEach((p) => {
            out[p] = ''
        })
        if (f.type === 'date_range' || f.type === 'timestamp_range') {
            const d = f as DateRangeFilterDef
            const [pFrom, pTo] = d.params
            if (d.default === 'today') {
                out[pFrom] = today
                out[pTo] = today
            } else if (d.default === 'month') {
                out[pFrom] = month.from
                out[pTo] = today
            } else if (d.required) {
                out[pFrom] = month.from
                out[pTo] = month.to
            }
        } else if (f.type === 'month') {
            out[f.params[0]] = today.slice(0, 7)
        } else if (f.type === 'date') {
            out[f.params[0]] = today
        } else if (f.type === 'hour_range') {
            const h = (f as HourRangeFilterDef).default || { from: 8, to: 17 }
            out[f.params[0]] = String(h.from)
            out[f.params[1]] = String(h.to)
        } else if (f.type === 'enum') {
            const e = f as EnumFilterDef
            if (e.default && e.default.length) out[e.params[0]] = e.default.join(',')
        }
    })
    const extra = REPORT_DEFAULTS[def.id]
    if (extra) Object.assign(out, extra(month))
    return out
}

/** True when the API itself applies a default for this filter (clearing it is not meaningful). */
export const hasServerDefault = (f: AnyFilterDef) =>
    ((f.type === 'date_range' || f.type === 'timestamp_range') && !!(f as DateRangeFilterDef).default) ||
    f.type === 'month' ||
    f.type === 'date' ||
    f.type === 'hour_range' ||
    (f.type === 'enum' && !!(f as EnumFilterDef).default?.length)

/* ------------------------------------------------------------------ URL hash */

/** Hash marker for "cleared on purpose" when the parameter has a non-empty default. */
export const CLEARED = '~'

/**
 * One filter parameter in the hash. Values are kept verbatim (the API
 * validates them; an invalid one comes back as a field error). A parameter
 * whose default is non-empty writes `~` when the user clears it, so "no
 * callback window" survives a reload instead of snapping back to the month.
 */
function paramCodec(hasDefault: boolean): HashCodec<string> {
    const parse = (raw: string) => {
        if (raw === CLEARED) return ''
        // eslint-disable-next-line no-control-regex
        return raw.length <= 7600 && !/[\u0000-\u001f\u007f]/.test(raw) ? raw : undefined
    }
    return {
        parse,
        serialize: (value) => {
            if (value === '' || value === undefined || value === null) return hasDefault ? CLEARED : ''
            return parse(value) === value ? value : ''
        },
    }
}

const SORT_RE = /^-?[A-Za-z0-9_]{1,80}$/
const sortCodec: HashCodec<string> = {
    parse: (raw) => (SORT_RE.test(raw) ? raw : undefined),
    serialize: (v) => (SORT_RE.test(v) ? v : ''),
}

export const PAGE_SIZES = [25, 50, 100, 200] as const

export type ViewerHashState = Record<string, string | number>

/** Report-specific hash schema: every filter param, plus `sort`, `page`, `size`. */
export function buildHashSchema(def: ReportDefinition, defaults: Record<string, string>): HashSchema<ViewerHashState> {
    const schema: HashSchema<ViewerHashState> = {}
    def.filters.forEach((f) =>
        f.params.forEach((p) => {
            const d = defaults[p] ?? ''
            schema[p] = { key: p, codec: paramCodec(d !== '') as HashCodec<string | number>, default: d }
        })
    )
    const defSize = (PAGE_SIZES as readonly number[]).includes(def.paging?.default_page_size) ? def.paging.default_page_size : 50
    schema.__sort = { key: 'sort', codec: sortCodec as HashCodec<string | number>, default: def.default_sort }
    schema.__page = { key: 'page', codec: hashCodec.int({ min: 1, max: 1000000 }) as HashCodec<string | number>, default: 1 }
    schema.__size = {
        key: 'size',
        codec: hashCodec.oneOf(PAGE_SIZES) as unknown as HashCodec<string | number>,
        default: defSize,
    }
    return schema
}

/** The filter params of a hash state (strings; '' = not set). */
export function filterParams(def: ReportDefinition, state: ViewerHashState): Record<string, string> {
    const out: Record<string, string> = {}
    def.filters.forEach((f) =>
        f.params.forEach((p) => {
            out[p] = String(state[p] ?? '')
        })
    )
    return out
}

/** Only the non-empty params (what is sent; the export body takes exactly these). */
export const nonEmpty = (params: Record<string, string>) => {
    const out: Record<string, string> = {}
    Object.entries(params).forEach(([k, v]) => {
        if (v !== '' && v !== undefined && v !== null) out[k] = v
    })
    return out
}

/** A stable string for an effect dependency. */
export const stableKey = (o: Record<string, unknown>) =>
    Object.keys(o)
        .sort()
        .map((k) => `${k}=${String(o[k])}`)
        .join('&')

/* ------------------------------------------------------------------ columns */

/** A sort key's column: the same key, or the documented aliases. */
export function sortForColumn(def: ReportDefinition, col: ReportColumnDef): string | null {
    const keys = new Set(def.sorts.map((s) => s.key))
    if (keys.has(col.key)) return col.key
    if (col.key === 'achievement_percent' && keys.has('achievement')) return 'achievement'
    if (col.key === 'case_created_at' && keys.has('created_at') && !def.columns.some((c) => c.key === 'created_at'))
        return 'created_at'
    return null
}

/** Numbers are trailing-aligned. */
export const isNumericColumn = (col: ReportColumnDef) => col.type === 'money' || col.type === 'int'

/** Reports whose rows fall into sections (a `section` column): rendered as grouped rows. */
export const isSectioned = (def: ReportDefinition) => def.columns.some((c) => c.key === 'section' && c.type === 'enum')

/**
 * Reports whose columns depend on the section, mode or hour window (RPT-18,
 * RPT-23, RPT-24): columns that are empty on every row of the page are
 * collapsed by default, with a toggle to show them.
 */
export const COLLAPSE_EMPTY_DEFAULT = new Set(['RPT-18', 'RPT-23', 'RPT-24'])

/** The viewer may see a protected employee field (mirrors core/user_privacy.py). */
export function mayViewProtected(col: ReportColumnDef, perms: Record<string, boolean>): boolean {
    const identity = !!(perms.users_details || perms.users_edit)
    if (col.protected === 'identity') return identity
    if (col.protected === 'target') return identity || !!perms.users_edit_target
    return true
}

/** RPT-25 recording links need a recording key, separate from the call metadata. */
export const RECORDING_KEYS = ['clints_callsRecords_download', 'clints_callsRecords_play'] as const

/* ------------------------------------------------------------------ exact money */

/** "1234.5" -> 123450n (halalas). Never a float. */
export function toCents(value: string | number | null | undefined): bigint | null {
    if (value === null || value === undefined || value === '') return null
    const m = /^([+-]?)(\d*)(?:\.(\d{0,2}))?$/.exec(String(value).trim())
    if (!m) return null
    const cents = BigInt(m[2] || '0') * 100n + BigInt((m[3] || '').padEnd(2, '0') || '0')
    return m[1] === '-' ? -cents : cents
}

/** 123450n -> "1234.50". */
export function fromCents(c: bigint): string {
    const neg = c < 0n
    const a = neg ? -c : c
    const s = `${a / 100n}.${String(a % 100n).padStart(2, '0')}`
    return neg ? `-${s}` : s
}

/* ------------------------------------------------------------------ classic screens */

export type ClassicLink = { path: string; key: string; labelKey: string }

/**
 * The legacy screen(s) behind each report (from the definitions' `legacy`
 * field and routes.config). The legacy screens stay; the hub links to them
 * as "classic" entries when the user holds the route's key. RPT-11, RPT-24
 * and RPT-25 have no standalone legacy screen.
 */
export const CLASSIC_LINKS: Record<string, ClassicLink[]> = {
    'RPT-01': [{ path: '/users', key: 'users', labelKey: 'p4.classic.users' }],
    'RPT-02': [{ path: '/clients', key: 'clints', labelKey: 'p4.classic.clients' }],
    'RPT-03': [{ path: '/archive', key: 'clints_archive', labelKey: 'p4.classic.archive' }],
    'RPT-04': [{ path: '/report/users/comments', key: 'report_users_comments', labelKey: 'p4.classic.usersComments' }],
    'RPT-05': [
        {
            path: '/report/collectors/comments',
            key: 'report_collectors_currentUser_comments',
            labelKey: 'p4.classic.myComments',
        },
    ],
    'RPT-06': [{ path: '/user_collection', key: 'collections_users', labelKey: 'p4.classic.usersCollections' }],
    'RPT-07': [{ path: '/current_user_collection', key: 'collections_currentUser', labelKey: 'p4.classic.myCollections' }],
    'RPT-08': [{ path: '/payment', key: 'accounts_payments', labelKey: 'p4.classic.accountingPayments' }],
    'RPT-09': [
        { path: '/report/users/paymentDates', key: 'report_users_paymentDates', labelKey: 'p4.classic.usersPromises' },
    ],
    'RPT-10': [
        {
            path: '/report/collectors/paymentDates',
            key: 'report_collectors_currentUser_paymentDates',
            labelKey: 'p4.classic.myPromises',
        },
    ],
    'RPT-12': [
        { path: '/report/portfolios/portfolios', key: 'report_portfolios', labelKey: 'p4.classic.portfolioReport' },
        { path: '/portfolios', key: 'portfolios', labelKey: 'p4.classic.portfolios' },
    ],
    'RPT-13': [{ path: '/report/clients/active', key: 'report_clintes_active', labelKey: 'p4.classic.activeClients' }],
    'RPT-14': [
        { path: '/report/clients/inactive', key: 'report_clintes_inactive', labelKey: 'p4.classic.inactiveClients' },
    ],
    'RPT-15': [{ path: '/report/quality/neglect', key: 'report_quality_neglect', labelKey: 'p4.classic.neglect' }],
    'RPT-16': [
        {
            path: '/report/collectors/nextCalls',
            key: 'report_collectors_currentUser_nextCalls',
            labelKey: 'p4.classic.nextCalls',
        },
    ],
    'RPT-17': [{ path: '/home', key: 'dashbord', labelKey: 'p4.classic.dashboard' }],
    'RPT-18': [{ path: '/home', key: 'dashbord', labelKey: 'p4.classic.dashboard' }],
    'RPT-19': [
        { path: '/home', key: 'dashbord', labelKey: 'p4.classic.dashboard' },
        { path: '/team', key: 'team_any', labelKey: 'p4.classic.team' },
    ],
    'RPT-20': [{ path: '/home', key: 'dashbord', labelKey: 'p4.classic.dashboard' }],
    'RPT-21': [{ path: '/home', key: 'dashbord', labelKey: 'p4.classic.dashboard' }],
    'RPT-22': [{ path: '/home', key: 'dashbord', labelKey: 'p4.classic.dashboard' }],
    'RPT-23': [{ path: '/home', key: 'dashbord', labelKey: 'p4.classic.dashboard' }],
}

/** The classic links of a report the user may open. */
export const classicLinksFor = (id: string, perms: Record<string, boolean>) =>
    (CLASSIC_LINKS[id] || []).filter((l) => perms[l.key] === true)

/* ------------------------------------------------------------------ charts */

export type ChartSpec = {
    /** Row field holding the x value (a civil day) — or `hours` for RPT-23's h00…h23 columns. */
    x: 'day' | 'hours'
    /** Date filter whose window is the x axis (x = day). */
    range?: string
    /** Measures the user can switch between (column keys), with their type. */
    measures: { key: string; type: 'money' | 'int' }[]
    /** Only for this section (RPT-24 trend). */
    section?: string
    /** A day with no row is a true zero (no receipts / no promises that day), not unknown. */
    fillZero?: boolean
    /** Total that must equal the sum of the chart's values for its measure (integrity check). */
    checks?: Record<string, string>
}

/**
 * Time-series reports get a chart (design-reference/dataviz.md): RPT-20 and
 * RPT-21 (per day, summed exactly over the owner/contract groups), RPT-23 (per
 * hour, summed over collectors) and RPT-24's daily trend section. Exact values
 * always stay in the table and in the chart's value list.
 */
export const CHARTS: Record<string, ChartSpec> = {
    'RPT-20': {
        x: 'day',
        range: 'payment_date',
        fillZero: true,
        measures: [
            { key: 'approved_amount', type: 'money' },
            { key: 'receipts', type: 'int' },
        ],
        checks: { approved_amount: 'approved_amount', receipts: 'receipts' },
    },
    'RPT-21': {
        x: 'day',
        range: 'due_date',
        fillZero: true,
        measures: [
            { key: 'promised_amount', type: 'money' },
            { key: 'promises', type: 'int' },
        ],
        checks: { promised_amount: 'promised_amount', promises: 'promises' },
    },
    'RPT-23': { x: 'hours', measures: [{ key: 'total', type: 'int' }], checks: { total: 'total' } },
    'RPT-24': {
        x: 'day',
        range: 'activity_date',
        section: 'trend',
        measures: [
            { key: 'approved_amount', type: 'money' },
            { key: 'notes_total', type: 'int' },
            { key: 'approved_count', type: 'int' },
        ],
        checks: { approved_amount: 'approved_amount', notes_total: 'notes_total', approved_count: 'approved_count' },
    },
}

/** Every civil day from `from` to `to` inclusive ("YYYY-MM-DD"). */
export function daysBetween(from: string, to: string, max = 400): string[] {
    const out: string[] = []
    const d = new Date(`${from}T00:00:00Z`)
    const end = new Date(`${to}T00:00:00Z`)
    if (Number.isNaN(d.getTime()) || Number.isNaN(end.getTime())) return out
    while (d <= end && out.length < max) {
        out.push(d.toISOString().slice(0, 10))
        d.setUTCDate(d.getUTCDate() + 1)
    }
    return out
}
