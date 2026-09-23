/**
 * The case record the legacy detail screen already loads (GET /clints/<id>),
 * as passed to the Phase 1 case-detail panels.
 */
export type CaseInfo = {
    id: string
    name: string
    accountNumber: string
    identityNumber: string
    amount: number
    is_archive?: boolean
    is_locked?: boolean
    user_id?: string | null
    contract?: { id: string; name: string } | null
    attribute?: Record<string, unknown> | null
    delinquencyReason_id?: string | null
}

export type CasePanelProps = {
    caseData: CaseInfo
    /** Ask the legacy screen to reload what it shows (case, notes, contacts). */
    onChanged?: () => void
}
