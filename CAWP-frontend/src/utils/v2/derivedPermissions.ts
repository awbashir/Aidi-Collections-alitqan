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
 * Phase 4 adds `reports_any` (the reports hub and viewer).
 * Phase 5 adds `calling_any` (the calling workspace: calls_make | campaigns) and
 * `campaigns_any` (the campaigns screen: campaigns | campaigns_manage), mirroring the
 * keys their endpoints accept (`GET /v2/me/calling`, `GET /v2/campaigns`), plus
 * `calling_admin` (= calls_manage), `call_review` and `calling_blocks` (= calls_manage | calls_supervise),
 * and `call_monitor` (the call monitor:
 * reports_calls | calls_manage, as `GET /v2/calling/monitor/*` accepts).
 * Phase 6 adds `quality_any` (the quality workspace: quality_review | quality_approve).
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
    // Phase 5: pbx_cdr_import (the reviewed PBX CDR import)
    'calls_manage',
] as const

/** Phase 3: any of these opens the requests screen (mirrors the gate of GET /v2/requests). */
export const REQUEST_KEYS = ['requests', 'requests_legal', 'requests_finance', 'requests_operations'] as const

/** Phase 3: any of these opens the plans screen (mirrors GET /v2/plans). */
export const PLAN_KEYS = ['plans', 'plans_manage'] as const

/** Phase 3: any of these opens the team screen (mirrors GET /v2/targets/achievement). */
export const TEAM_KEYS = ['dashbord_admin', 'dashbord_user'] as const

/**
 * Phase 4: any report's key opens the reports hub (`reports_any`), mirroring
 * `GET /v2/reports`, which lists every report whose any-of keys the caller
 * holds (screen-key rule). Keys of RPT-01…25 on the live registry (2026-09-24);
 * the hub itself shows exactly what the API returns, and every report page
 * re-checks its own key through `GET /v2/reports/<id>`.
 */
export const REPORT_KEYS = [
    'users', // RPT-01
    'clints', // RPT-02
    'clints_archive', // RPT-03
    'report_users_comments', // RPT-04
    'report_collectors_currentUser_comments', // RPT-05
    'collections_users', // RPT-06
    'collections_currentUser', // RPT-07
    'accounts_payments', // RPT-08
    'report_users_paymentDates', // RPT-09
    'report_collectors_currentUser_paymentDates', // RPT-10, RPT-11
    'reports_performance', // RPT-11, RPT-24
    'report_portfolios', // RPT-12
    'portfolios', // RPT-12
    'report_clintes_active', // RPT-13
    'report_clintes_inactive', // RPT-14
    'report_quality_neglect', // RPT-15
    'report_collectors_currentUser_nextCalls', // RPT-16
    'dashbord_user', // RPT-17, 18, 19
    'dashbord_admin', // RPT-19 … 23
    'clints_callsRecords', // RPT-25
    'reports_calls', // RPT-25
] as const

/** Phase 5: the calling workspace (`/calling`): click-to-call or campaign participation. */
export const CALLING_KEYS = ['calls_make', 'campaigns'] as const

/** Phase 5: the campaigns screen (`/campaigns`), mirroring GET /v2/campaigns. */
export const CAMPAIGN_KEYS = ['campaigns', 'campaigns_manage'] as const

/**
 * Phase 6: the quality workspace (`/quality`) opens for `quality_review` (library, reviews,
 * reports) and for `quality_approve` (an approver must see what they approve), mirroring the
 * read gate of GET /v2/quality/*; mutations and approval re-check their own key in the API.
 */
export const QUALITY_KEYS = ['quality_review', 'quality_approve'] as const

export const DERIVED_KEYS = {
    bulk_jobs: (p: Record<string, unknown>) => BULK_JOB_KEYS.some((k) => p[k] === true),
    requests_any: (p: Record<string, unknown>) => REQUEST_KEYS.some((k) => p[k] === true),
    plans_any: (p: Record<string, unknown>) => PLAN_KEYS.some((k) => p[k] === true),
    team_any: (p: Record<string, unknown>) => TEAM_KEYS.some((k) => p[k] === true),
    reports_any: (p: Record<string, unknown>) => REPORT_KEYS.some((k) => p[k] === true),
    calling_any: (p: Record<string, unknown>) => CALLING_KEYS.some((k) => p[k] === true),
    campaigns_any: (p: Record<string, unknown>) => CAMPAIGN_KEYS.some((k) => p[k] === true),
    /* Phase 5: two screens of `calls_manage` (a nav item needs a key of its own). */
    /* call review: calls_manage, or calls_supervise (2026-10-17: supervisors without calling admin) */
    call_review: (p: Record<string, unknown>) => p.calls_manage === true || p.calls_supervise === true,
    calling_admin: (p: Record<string, unknown>) => p.calls_manage === true,
    /* blocked clients (2026-10-17): only supervisors block or lift a client */
    calling_blocks: (p: Record<string, unknown>) => p.calls_manage === true || p.calls_supervise === true,
    call_monitor: (p: Record<string, unknown>) => p.reports_calls === true || p.calls_manage === true,
    /* Phase 6: the quality workspace (quality_review | quality_approve). */
    quality_any: (p: Record<string, unknown>) => QUALITY_KEYS.some((k) => p[k] === true),
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
