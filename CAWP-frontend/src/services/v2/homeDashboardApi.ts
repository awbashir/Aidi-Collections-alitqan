/**
 * Home dashboard v2 (`/home`, 2026-10-20), CAWP-API/api/home_dashboard.py.
 *
 *   GET /v2/dashboards/home/summary     KPI cards (+ previous period, trend %, sparkline), donut by contract
 *   GET /v2/dashboards/home/charts      collections this month by day, promises by due day, notes today, heatmap
 *   GET /v2/dashboards/home/collectors  leaderboard (target / achieved, period activity, kept rate, neglect)
 *   GET /v2/dashboards/home/portfolio   recovery per contract/portfolio, ageing, amounts, reachability, neglect
 *   GET /v2/dashboards/home/calls       today's calls by hour (call monitor statistics, same scope)
 *
 * Common query: period=today|week|month, contract_id, user_id. The API decides the scope
 * (SuperAdmin every case, a supervisor the team, anyone else their own cases and notes) and
 * gates every route with dashbord_user | dashbord_admin. Money is exact decimal strings;
 * pending, rejected and reversed payments never count as collected.
 *
 * Every field is typed optional on purpose: the screen must render whatever comes back
 * (a field missing, a list empty, a lookup that misses) without throwing.
 */
import { v2Get } from './http'

export type HomePeriod = 'today' | 'week' | 'month'

export type HomeQuery = {
    period?: HomePeriod
    contract_id?: string
    user_id?: string
    /** '1' after the user pressed refresh: bypass the API's short response cache */
    nocache?: string
}

type Meta = {
    scope?: 'all' | 'team' | 'own'
    today?: string
    contract_id?: string | null
    user_id?: string | null
}

export type HomeTarget = {
    collectors?: number
    targets_unknown?: number
    target?: string | null
    achieved?: string
    percent?: number | null
    expected_to_date?: string | null
    remaining?: string | null
    work_days?: number
    work_days_passed?: number
    month?: string
}

type StateTotals = { count?: number; amount?: string }

export type HomeSummary = Meta & {
    period?: HomePeriod
    from?: string
    to?: string
    previous?: { from?: string; to?: string }
    options?: { contracts?: { id: string; name?: string | null; is_active?: boolean | null }[] }
    spark_days?: string[]
    kpis?: {
        collected?: { amount?: string; count?: number; previous_amount?: string; trend?: number | null; spark?: number[] }
        pending?: { amount?: string; count?: number }
        promises_due?: StateTotals & {
            by_state?: { open?: StateTotals; overdue?: StateTotals; fulfilled?: StateTotals }
            previous_count?: number
            trend?: number | null
            spark?: number[]
        }
        notes?: { count?: number; clients?: number; previous_count?: number; trend?: number | null; spark?: number[] }
        cases?: { open?: number; debt?: string; not_contacted_7d?: number }
        target?: HomeTarget
    }
    by_contract?: { contract_id?: string | null; name?: string | null; count?: number; amount?: string }[]
}

export type HomeStatus = { key?: string; id?: string | null; name?: string | null; color?: string | null; is_active?: boolean | null; count?: number }

export type HomeCharts = Meta & {
    month?: string
    target?: HomeTarget
    collections?: { date?: string; count?: number; amount?: string; cumulative?: string }[]
    promises?: {
        from?: string
        to?: string
        days?: { date?: string; open?: StateTotals; overdue?: StateTotals; fulfilled?: StateTotals }[]
    }
    notes_today?: {
        statuses?: HomeStatus[]
        other?: number
        total?: number
        hours?: { hour?: number; total?: number; by_status?: Record<string, number>; other?: number }[]
    }
    notes_heatmap?: { from?: string; to?: string; rows?: { weekday?: number; days?: number; hours?: number[] }[] }
}

export type HomeCollector = {
    user_id?: string
    name?: string | null
    contract_id?: string | null
    contract_name?: string | null
    target?: string | null
    achieved?: string
    achievement_percent?: number | null
    collected?: string
    payments?: number
    notes?: number
    note_clients?: number
    promises_due?: number
    promises_due_amount?: string
    kept?: number
    broken?: number
    kept_rate?: number | null
    open_cases?: number
    debt?: string
    neglect?: { never?: number; d7?: number; d14?: number; d30?: number }
    first_contact_rate?: number | null
    rank?: number
}

export type HomeCollectors = Meta & {
    period?: HomePeriod
    from?: string
    to?: string
    kept_rate_days?: number
    neglect_days?: number[]
    collectors?: HomeCollector[]
}

export type HomeBucket = { key?: string; min?: number | string | null; max?: number | string | null; count?: number; outstanding?: string }

export type HomePortfolio = Meta & {
    contracts?: {
        contract_id?: string | null
        name?: string | null
        cases?: number
        debt?: string
        collected?: string
        outstanding?: string
        rate?: number | null
        portfolios?: { portfolio_id?: string; name?: string | null; cases?: number; debt?: string; collected?: string; outstanding?: string; rate?: number | null }[]
    }[]
    age_buckets?: HomeBucket[]
    amount_buckets?: HomeBucket[]
    reachability?: { id?: string | null; name?: string | null; count?: number }[]
    neglect?: { open?: number; never?: number; d7?: number; d14?: number; d30?: number }
    first_contact?: { open?: number; contacted?: number; rate?: number | null }
}

export type HomeCalls = {
    available?: boolean
    error?: string | null
    scope?: 'all' | 'team' | 'own'
    date?: string
    truncated?: boolean
    totals?: {
        calls?: number
        answered?: number
        handled?: number
        outbound?: number
        outbound_answered?: number
        inbound_answered?: number
        inbound_missed?: number
        talk_seconds?: number
        answer_rate?: number | null
    } | null
    hours?: { hour?: number; answered?: number; not_answered?: number; unknown?: number }[] | null
}

const clean = (q: object) => Object.fromEntries(Object.entries(q).filter(([, v]) => v !== undefined && v !== null && v !== ''))

export const homeDashboardApi = {
    summary: (q: HomeQuery, signal?: AbortSignal) => v2Get<HomeSummary>('/v2/dashboards/home/summary', clean(q), signal),
    charts: (q: HomeQuery, signal?: AbortSignal) => v2Get<HomeCharts>('/v2/dashboards/home/charts', clean({ ...q, period: undefined }), signal),
    collectors: (q: HomeQuery, signal?: AbortSignal) => v2Get<HomeCollectors>('/v2/dashboards/home/collectors', clean(q), signal),
    portfolio: (q: HomeQuery, signal?: AbortSignal) =>
        v2Get<HomePortfolio>('/v2/dashboards/home/portfolio', clean({ ...q, period: undefined }), signal),
    calls: (q: Pick<HomeQuery, 'nocache'>, signal?: AbortSignal) => v2Get<HomeCalls>('/v2/dashboards/home/calls', clean(q), signal),
}
