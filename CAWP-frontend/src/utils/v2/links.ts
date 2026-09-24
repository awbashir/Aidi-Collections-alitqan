/** Links into the existing case detail screen (`/clients/details/:tab?id=`). */
export type CaseTab =
    | 'details'
    | 'comments'
    | 'payments'
    | 'paymentsDate'
    | 'followUps'
    | 'promises'
    | 'corrections'
    /* Phase 3 */
    | 'plan'
    | 'requests'
    /* Phase 5 */
    | 'calls'

export const caseLink = (caseId: string, tab: CaseTab = 'details') =>
    `/clients/details/${tab}?id=${encodeURIComponent(caseId)}`
