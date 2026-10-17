/**
 * The legacy delete / reassign routes refuse cases with an open call (2026-10-17,
 * CAWP-API/services/calling/guards.py): a call that is live, uncertain or awaiting its
 * wrap-up keeps its case and its collector until the wrap-up is saved or a supervisor
 * releases it from the call review screen.
 *
 * - DELETE /clint/<id>, DELETE /clints, DELETE /portfolios/<id>, PUT /clints/<id> (owner
 *   change) answer 409 `{message, status: false, code: 'case_has_open_call', open_call_cases}`
 *   with an Arabic message: `openCallRefusal(err)` returns it (else null).
 * - POST /sepcifyusers/ids and /clint/SepcifyUsersByExcel skip those cases and answer 200
 *   with `skipped_open_calls` and a message saying so: `skippedOpenCalls(body)` returns it.
 */
type LegacyBody = { code?: string; message?: string; skipped_open_calls?: number } | null | undefined

export function openCallRefusal(err: unknown): string | null {
    const data = (err as { response?: { status?: number; data?: LegacyBody } } | null)?.response?.data
    return data && data.code === 'case_has_open_call' && data.message ? data.message : null
}

export function skippedOpenCalls(body: unknown): string | null {
    const b = body as LegacyBody
    return b && typeof b === 'object' && b.skipped_open_calls ? b.message || null : null
}
