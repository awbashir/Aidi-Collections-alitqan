/**
 * The case's documents for linking to a request (no new file storage), and
 * opening an attached document through the legacy authenticated download.
 */
import { useEffect, useState } from 'react'
import ToastNotification from '@/components/shared/myComponents/ToastNotification'
import { apiDownloadClintDocument, apiGetClintDocuments } from '@/services/ClientsService'

export type CaseDocument = {
    id: string
    original_name: string
    display_name: string | null
    file_type: string
    create_at?: string
}

/**
 * The case's current documents (legacy `GET /clint/<id>/documents`, key
 * `clints_documents`). `available: false` when the caller cannot list them;
 * a request can still be raised without attachments.
 */
export function useCaseDocuments(caseId: string | null | undefined, enabled: boolean) {
    const [state, setState] = useState<{ loading: boolean; available: boolean; docs: CaseDocument[] }>({
        loading: enabled,
        available: false,
        docs: [],
    })
    useEffect(() => {
        if (!enabled || !caseId) {
            setState({ loading: false, available: false, docs: [] })
            return undefined
        }
        let alive = true
        setState((s) => ({ ...s, loading: true }))
        apiGetClintDocuments<{ documents?: CaseDocument[] }, never>(caseId as never)
            .then((res) => {
                if (!alive) return
                const docs = res.data?.documents
                setState({ loading: false, available: Array.isArray(docs), docs: Array.isArray(docs) ? docs : [] })
            })
            .catch(() => {
                if (alive) setState({ loading: false, available: false, docs: [] })
            })
        return () => {
            alive = false
        }
    }, [caseId, enabled])
    return state
}

/** Opens an attached case document through the legacy authenticated download. */
export async function openDocument(documentId: string, failTitle: string, failMessage: string) {
    try {
        const res = await apiDownloadClintDocument<BlobPart, never>(documentId as never)
        const url = window.URL.createObjectURL(new Blob([res.data]))
        window.open(url, '_blank')
        window.setTimeout(() => window.URL.revokeObjectURL(url), 60_000)
    } catch {
        ToastNotification(failMessage, failTitle, 'danger')
    }
}

export const docLabel = (d: { display_name?: string | null; original_name?: string | null; id: string }) =>
    d.display_name || d.original_name || d.id.slice(0, 8)
