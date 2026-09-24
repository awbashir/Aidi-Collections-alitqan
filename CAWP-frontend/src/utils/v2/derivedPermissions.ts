/**
 * Permission keys the frontend derives from real keys.
 *
 * A route and its nav item are gated by one key (`role.permission[key]`). The
 * Phase 2 jobs screen has no key of its own: it mirrors `GET /v2/jobs`, which
 * answers anyone holding a bulk kind key or `bulk_jobs_all`
 * (`core/bulk/service.any_bulk_permission`). The kind keys below mirror the
 * live `GET /v2/bulk/kinds` registry (2026-09-23); the API still decides.
 *
 * Phase 3 adds `requests_any`, `plans_any` and `team_any` the same way: each
 * screen's route and nav item open for any key its list endpoint accepts.
 */

/** Any one of these opens the jobs screen (`bulk_jobs`). */
export const BULK_JOB_KEYS = [
    'bulk_jobs_all',
    // lock / unlock
    'clints_bulk_lock',
    'clints_lock',
    // assign / round_robin
    'clints_sepcifyUser',
    // archive
    'clints_move_archive',
    // restore
    'clints_archive_move',
    'clints_archive',
    // notes, attribute_corrections
    'clints_UpdateByExcel',
    // attribute_corrections
    'case_corrections_request',
    // followup_closeout
    'follow_ups_manage',
    // inquiry
    'client_query',
] as const

/** Phase 3: any of these opens the requests screen (mirrors the gate of GET /v2/requests). */
export const REQUEST_KEYS = ['requests', 'requests_legal', 'requests_finance', 'requests_operations'] as const

/** Phase 3: any of these opens the plans screen (mirrors GET /v2/plans). */
export const PLAN_KEYS = ['plans', 'plans_manage'] as const

/** Phase 3: any of these opens the team screen (mirrors GET /v2/targets/achievement). */
export const TEAM_KEYS = ['dashbord_admin', 'dashbord_user'] as const

export const DERIVED_KEYS = {
    bulk_jobs: (p: Record<string, unknown>) => BULK_JOB_KEYS.some((k) => p[k] === true),
    requests_any: (p: Record<string, unknown>) => REQUEST_KEYS.some((k) => p[k] === true),
    plans_any: (p: Record<string, unknown>) => PLAN_KEYS.some((k) => p[k] === true),
    team_any: (p: Record<string, unknown>) => TEAM_KEYS.some((k) => p[k] === true),
}

/** The permission map plus the derived keys. Never removes or changes a real key. */
export function withDerivedPermissions(permissions: object | null | undefined): Record<string, boolean> {
    const p = (permissions ?? {}) as Record<string, boolean>
    const out: Record<string, boolean> = { ...p }
    ;(Object.keys(DERIVED_KEYS) as Array<keyof typeof DERIVED_KEYS>).forEach((k) => {
        if (out[k] === undefined) out[k] = DERIVED_KEYS[k](p)
    })
    return out
}
