/**
 * Phase 3 v2 endpoints: internal requests, repayment plans, targets, sessions
 * and `/v2/me`. Same transport and conventions as ./api.ts: every function
 * returns the envelope's `data` or throws `V2Error`; mutations take the
 * caller's per-action Idempotency-Key; transitions send the revision they
 * loaded (`expected_revision`).
 *
 * Contracts: review/phase3-{requests,plans,admin}.md, checked against the
 * live API and CAWP-API/api/{requests_v2,plans,admin_v2}.py.
 */
import { newIdempotencyKey, v2Get, v2Post, v2Put, v2Request } from './http'
import type {
    Achievement,
    ActiveResult,
    CasePlans,
    Me,
    NewRequestBody,
    Plan,
    PlanAllocation,
    PlanDraft,
    PlanList,
    PlanPreview,
    PlanQuery,
    RequestDetail,
    RequestList,
    RequestQuery,
    SignOutResult,
    TargetHistory,
    TargetKind,
    TargetSetResult,
    TargetSubject,
} from './p3Types'

const enc = encodeURIComponent

/* ------------------------------------------------------------------ requests */

export type RequestTransition = 'take' | 'resolve' | 'reopen' | 'cancel'

export const requestsApi = {
    list: (q: RequestQuery, signal?: AbortSignal) => v2Get<RequestList>('/v2/requests', q, signal),

    detail: (id: string, signal?: AbortSignal) =>
        v2Get<RequestDetail>(`/v2/requests/${enc(id)}`, undefined, signal),

    caseList: (caseId: string, q: RequestQuery, signal?: AbortSignal) =>
        v2Get<RequestList>(`/v2/cases/${enc(caseId)}/requests`, q, signal),

    create: (caseId: string, body: NewRequestBody, key: string) =>
        v2Post<RequestDetail>(`/v2/cases/${enc(caseId)}/requests`, body, key),

    comment: (id: string, body: { body: string; document_ids?: string[] }, key: string) =>
        v2Post<RequestDetail>(`/v2/requests/${enc(id)}/comments`, body, key),

    /** take (reason optional), resolve / reopen / cancel (reason required). */
    transition: (
        id: string,
        action: RequestTransition,
        body: { expected_revision: number; reason?: string },
        key: string
    ) => v2Post<RequestDetail>(`/v2/requests/${enc(id)}/${action}`, body, key),
}

/* --------------------------------------------------------------------- plans */

export const plansApi = {
    list: (q: PlanQuery, signal?: AbortSignal) => v2Get<PlanList>('/v2/plans', q, signal),

    detail: (id: string, signal?: AbortSignal) => v2Get<Plan>(`/v2/plans/${enc(id)}`, undefined, signal),

    casePlans: (caseId: string, signal?: AbortSignal) =>
        v2Get<CasePlans>(`/v2/cases/${enc(caseId)}/plans`, undefined, signal),

    /**
     * Writes nothing and runs the same checks as create. The route is not
     * idempotent-keyed; the transport still requires a key on a POST, so a
     * throwaway one is sent (the server ignores it). Abortable: the wizard
     * previews on every change and only the newest answer may show.
     */
    preview: (caseId: string, body: PlanDraft, signal?: AbortSignal) =>
        v2Request<PlanPreview>(`/v2/cases/${enc(caseId)}/plans/preview`, {
            method: 'post',
            data: body,
            idempotencyKey: newIdempotencyKey(),
            signal,
        }),

    create: (caseId: string, body: PlanDraft, key: string) =>
        v2Post<Plan>(`/v2/cases/${enc(caseId)}/plans`, body, key),

    cancel: (id: string, body: { expected_revision: number; reason: string }, key: string) =>
        v2Post<Plan>(`/v2/plans/${enc(id)}/cancel`, body, key),

    allocate: (
        id: string,
        body: { payment_id: string; reason: string; installment_id?: string; amount?: string },
        key: string
    ) => v2Post<{ allocations: PlanAllocation[]; plan: Plan }>(`/v2/plans/${enc(id)}/allocations`, body, key),

    deallocate: (allocationId: string, body: { reason: string }, key: string) =>
        v2Post<{ allocation_id: string; plan: Plan }>(
            `/v2/plan-allocations/${enc(allocationId)}/deallocate`,
            body,
            key
        ),
}

/* ----------------------------------------------------- me, sessions, targets */

export const adminApi = {
    me: (signal?: AbortSignal) => v2Get<Me>('/v2/me', undefined, signal),

    signOut: (userId: string, body: { reason?: string }, key: string) =>
        v2Post<SignOutResult>(`/v2/users/${enc(userId)}/sign-out`, body, key),

    deactivate: (userId: string, body: { reason: string }, key: string) =>
        v2Post<ActiveResult>(`/v2/users/${enc(userId)}/deactivate`, body, key),

    activate: (userId: string, body: { reason: string }, key: string) =>
        v2Post<ActiveResult>(`/v2/users/${enc(userId)}/activate`, body, key),

    targets: (subject: TargetSubject, id: string, signal?: AbortSignal) =>
        v2Get<TargetHistory>(`/v2/${subject === 'user' ? 'users' : 'contracts'}/${enc(id)}/targets`, undefined, signal),

    setTarget: (
        subject: TargetSubject,
        id: string,
        month: string,
        body: { kind: TargetKind; value?: string; reason: string; expected_revision: number },
        key: string
    ) =>
        v2Put<TargetSetResult>(
            `/v2/${subject === 'user' ? 'users' : 'contracts'}/${enc(id)}/targets/${enc(month)}`,
            body,
            key
        ),

    achievement: (q: { month?: string; contract_id?: string; user_id?: string }, signal?: AbortSignal) =>
        v2Get<Achievement>('/v2/targets/achievement', q, signal),
}
