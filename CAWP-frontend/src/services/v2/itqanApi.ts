/**
 * Alitqan performance board ("لوحة أداء فنية الإتقان", 2026-10-29), CAWP-API/api/itqan_performance.py.
 *
 *   GET /v2/dashboards/itqan   ?month=YYYY-MM&contract_id
 *
 * The API decides the scope and computes every score (criteria with no data in the system are
 * dropped and the weights rescaled; a criterion unknown for one collector is null). Money is exact
 * decimal strings. Same transport as ./api.ts.
 */
import { v2Get } from './http'

export type ItqanRating = 'excellent' | 'very_good' | 'good' | 'needs_improvement'
export type CollectorCriterion = 'payments' | 'calls' | 'reach' | 'ptp' | 'intensity' | 'attendance' | 'system'
export type SupervisorCriterion = 'target' | 'quality' | 'improvement'

export type ItqanTotals = {
    collectors: number
    targets_unknown: number
    target: string | null
    collected: string
    expected_to_date: string | null
    achievement: number | null
    pace: number | null
    promises_open_amount: string
    gap_after_promises: string | null
    cash: string
    scheduled: string
    promises_today: number
    promises_today_amount: string
    calls: number
    answered: number
    ratings: Record<ItqanRating, number>
}

export type ItqanCollector = {
    id: string
    name: string | null
    contract_id: string | null
    contract_name: string | null
    supervisor: { id: string; name: string | null } | null
    target: string | null
    collected: string
    expected_to_date: string | null
    achievement: number | null
    pace: number | null
    remaining: string | null
    payments: number
    paid_clients: number
    cash: string
    scheduled: string
    avg_payment: string | null
    calls: number
    answered: number
    clients_called: number
    calls_per_day: number | null
    reach_rate: number | null
    attempts_per_client: number | null
    calls_per_contact: number | null
    notes: number
    notes_per_day: number | null
    promises_due: number
    promises_kept: number
    promises_paid: number
    kept_rate: number | null
    paid_rate: number | null
    promises_open_amount: string
    promises_today: number
    promises_today_amount: string
    promises_made: number
    call_to_ptp: number | null
    login_days: number
    active_cases: number
    updated_cases: number
    update_rate: number | null
    new_files: number
    new_files_24h: number
    new_files_24h_rate: number | null
    scores: Record<CollectorCriterion, number | null>
    score: number | null
    rating: ItqanRating | null
    rank: number | null
}

export type ItqanSupervisor = {
    id: string
    name: string | null
    team_size: number
    collectors: string[]
    target: string | null
    collected: string
    expected_to_date: string | null
    achievement: number | null
    pace: number | null
    prev_achievement: number | null
    avg_collector_score: number | null
    scores: Record<SupervisorCriterion, number | null>
    score: number | null
    rating: ItqanRating | null
    rank: number | null
}

type Weights<K extends string> = { original: Record<K, number>; effective: Record<K, number>; dropped: Record<string, number> }

export type ItqanBoard = {
    scope: 'all' | 'team' | 'own'
    today: string
    contract_id: string | null
    month: string
    closed: boolean
    work_days: number
    work_days_passed: number
    weights: { collector: Weights<CollectorCriterion>; supervisor: Weights<SupervisorCriterion> }
    params: {
        reach_target: number
        ptp_target: number
        attempts_full: number
        notes_full: number
        calls_target_per_day: number | null
        system_days: number
    }
    totals: ItqanTotals
    idle: { id: string; name: string | null }[]
    collectors: ItqanCollector[]
    supervisors: ItqanSupervisor[]
    portfolios: (ItqanTotals & { contract_id: string | null; name: string | null })[]
    trend: {
        months: string[]
        total: { month: string; target: string | null; collected: string; achievement: number | null }[]
        collectors: Record<string, (number | null)[]>
    }
    options: { contracts: { id: string; name: string }[] }
}

const clean = (q: object) => Object.fromEntries(Object.entries(q).filter(([, v]) => v !== undefined && v !== null && v !== ''))

export const itqanApi = {
    get: (q: { month?: string; contract_id?: string }, signal?: AbortSignal) =>
        v2Get<ItqanBoard>('/v2/dashboards/itqan', clean(q), signal),
}
