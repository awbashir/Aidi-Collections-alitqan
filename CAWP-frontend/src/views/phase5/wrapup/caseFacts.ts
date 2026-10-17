/** What the wrap-up needs to know about the case before saving (legacy GET /clints/<id>, or the
 *  record the client page already loaded). */
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


/** The same facts from a case record the screen already holds (the client page's clientData). */
export function factsFromCase(d: {
    identityNumber?: string | null
    contract?: { id?: string | null } | null
    portfolios?: { contract_id?: string | null } | null
    is_locked?: boolean | null
    is_archive?: boolean | null
} | null | undefined): CaseFacts {
    return {
        identityNumber: d?.identityNumber || null,
        contractId: d?.contract?.id || d?.portfolios?.contract_id || null,
        isLocked: !!d?.is_locked,
        isArchived: !!d?.is_archive,
    }
}
