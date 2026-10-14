/**
 * Payment attachments (receipts), db/migrations/0069, CAWP-API/api/payment_attachments.py.
 *
 *   POST   /v2/payments/<id>/attachments                         multipart `file`, optional `name`
 *   GET    /v2/payments/<id>/attachments                         list
 *   GET    /v2/payments/<id>/attachments/<doc>/download          the file (private, inline)
 *   DELETE /v2/payments/<id>/attachments/<doc>                   pending payments only
 *   GET    /v2/cases/<case>/payment-attachments                  {payment_id: [attachments]}
 *
 * Same transport as ./api.ts: the envelope's `data` or a thrown `V2Error`; mutations take the
 * caller's per-action Idempotency-Key.
 */
import BaseService from '../BaseService'
import { V2Error, v2Delete, v2Get, v2Post } from './http'
import type { PaymentAttachment } from './types'

const enc = encodeURIComponent
const base = (paymentId: string) => `/v2/payments/${enc(paymentId)}/attachments`

/** Accepted receipt files: the same set as the case documents. */
export const RECEIPT_EXTENSIONS = ['.pdf', '.jpg', '.jpeg', '.png']
export const RECEIPT_ACCEPT = RECEIPT_EXTENSIONS.join(',')
export const RECEIPT_MAX_BYTES = 10 * 1024 * 1024
export const RECEIPT_MAX_PER_PAYMENT = 5

/** Client-side check before upload: 'type' | 'size' | 'empty', or null when acceptable. */
export function receiptFileProblem(file: File): 'type' | 'size' | 'empty' | null {
    const dot = file.name.lastIndexOf('.')
    const ext = dot >= 0 ? file.name.slice(dot).toLowerCase() : ''
    if (!RECEIPT_EXTENSIONS.includes(ext)) return 'type'
    if (file.size === 0) return 'empty'
    if (file.size > RECEIPT_MAX_BYTES) return 'size'
    return null
}

export type AttachmentList = {
    payment_id: string
    payment_status: string
    items: PaymentAttachment[]
    max_attachments: number
    max_bytes: number
}

export type CaseAttachmentMap = { case_id: string; payments: Record<string, PaymentAttachment[]> }

export const paymentAttachmentsApi = {
    list: (paymentId: string, signal?: AbortSignal) => v2Get<AttachmentList>(base(paymentId), undefined, signal),

    forCase: (caseId: string, signal?: AbortSignal) =>
        v2Get<CaseAttachmentMap>(`/v2/cases/${enc(caseId)}/payment-attachments`, undefined, signal),

    upload: (paymentId: string, file: File, key: string, name?: string) => {
        const form = new FormData()
        form.append('file', file)
        if (name && name.trim()) form.append('name', name.trim())
        return v2Post<{ payment_id: string; attachment: PaymentAttachment; items: PaymentAttachment[] }>(
            base(paymentId),
            form,
            key
        )
    },

    remove: (paymentId: string, documentId: string, key: string) =>
        v2Delete<{ payment_id: string; document_id: string; removed: boolean; items: PaymentAttachment[] }>(
            `${base(paymentId)}/${enc(documentId)}`,
            key
        ),
}

async function blobError(err: unknown): Promise<V2Error> {
    const ax = err as { response?: { status: number; data?: Blob }; code?: string; message?: string }
    if (ax?.response) {
        let body: { code?: string; message?: string; error?: unknown } = {}
        try {
            const data = ax.response.data
            const text = data instanceof Blob ? await data.text() : ''
            body = text ? JSON.parse(text) : {}
        } catch {
            /* not JSON */
        }
        const status = ax.response.status
        const code = body.code || (status === 403 ? 'forbidden' : status === 404 ? 'not_found' : 'unknown')
        return new V2Error(status, code, body.message || '', body.error ?? null)
    }
    if (ax?.code === 'ERR_CANCELED') return new V2Error(0, 'canceled', 'canceled', null)
    return new V2Error(0, 'network_error', ax?.message || 'network error', null)
}

/**
 * Opens a receipt in a new tab. The file needs the bearer token, so it is fetched as a Blob
 * (typed by the API's Content-Type, so PDFs and images render) and shown from a blob URL.
 * The tab is opened before the fetch so a popup blocker treats it as the user's click.
 */
export async function openPaymentAttachment(attachment: PaymentAttachment): Promise<void> {
    const tab = window.open('', '_blank')
    let blob: Blob
    try {
        const res = await BaseService({ url: attachment.download_url, method: 'get', responseType: 'blob' })
        blob = res.data as Blob
    } catch (err) {
        tab?.close()
        throw await blobError(err)
    }
    const url = URL.createObjectURL(blob)
    if (tab) {
        tab.location.href = url
    } else {
        const a = document.createElement('a')
        a.href = url
        a.download = attachment.original_name || 'receipt'
        a.rel = 'noopener'
        document.body.appendChild(a)
        a.click()
        a.remove()
    }
    setTimeout(() => URL.revokeObjectURL(url), 60000)
}
