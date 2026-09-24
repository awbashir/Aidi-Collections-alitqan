/** What the wrap-up needs to know about the case before saving (legacy GET /clints/<id>). */
import { apiGetClientsDetails } from '@/services/ClientsService'

export type CaseFacts = {
    identityNumber: string | null
    contractId: string | null
    isLocked: boolean
    isArchived: boolean
}

/** The legacy case record (GET /clints/<id>): contract (note statuses), lock and archive state. */
export async function loadCaseFacts(caseId: string): Promise<CaseFacts> {
    const res = await apiGetClientsDetails<
        { identityNumber?: string; contract?: { id?: string } | null; portfolios?: { contract_id?: string } | null; is_locked?: boolean; is_archive?: boolean },
        never
    >(caseId as never)
    const d = res.data || {}
    return {
        identityNumber: d.identityNumber || null,
        contractId: d.contract?.id || d.portfolios?.contract_id || null,
        isLocked: !!d.is_locked,
        isArchived: !!d.is_archive,
    }
}

