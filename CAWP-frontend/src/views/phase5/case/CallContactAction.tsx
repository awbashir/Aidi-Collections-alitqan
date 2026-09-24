/**
 * Click-to-call on a case contact (key `calls_make`), for the Phase 1 contacts panel.
 *
 * - Before any click it says why a contact cannot be called when the screen already knows
 *   it, in the API's own rule codes (`p5.refusal.<code>`): a blocked contact, a non-phone
 *   value, the case's do-not-call block, a locked/archived case, no extension, the calling
 *   window, the calling service, or a call that already holds my reservation.
 * - The API still decides every call (`POST /v2/calls` rechecks all the rules: closed/paid,
 *   a later callback, a pending supervisor review...). A refusal is shown under the row,
 *   mapped to the same bilingual messages.
 * - A call placed shows in the docked live-call panel. The browser never sends a number,
 *   only the contact id; the PBX rings MY extension first.
 */
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { PhoneOutgoing } from 'lucide-react'
import Tooltip from '@/components/ui/Tooltip'
import ToastNotification from '@/components/shared/myComponents/ToastNotification'
import { callsApi, describeP5Error, isV2Error, newIdempotencyKey, refusalText } from '@/services/v2'
import type { Contact } from '@/services/v2'
import { callDock } from '../live/callDock'
import type { Refusal } from './callability'

/** The "Call" icon button plus, under the row, the reason it cannot be called. */
export const CallContactButton = ({
    caseId,
    contact,
    known,
    onRefused,
    onPlaced,
    pending,
}: {
    caseId: string
    contact: Contact
    /** A reason the screen already knows (disables the button). */
    known: Refusal | null
    /** Still reading my calling state and the case's blocks. */
    pending?: boolean
    onRefused: (contactId: string, r: Refusal | null, message?: string) => void
    onPlaced?: () => void
}) => {
    const { t } = useTranslation()
    const [busy, setBusy] = useState(false)
    const title = pending ? String(t('p5.call.checking')) : known ? refusalText(known.code, known.detail, t) : String(t('p5.call.action'))
    const place = async () => {
        if (busy || known || pending) return
        setBusy(true)
        onRefused(contact.id, null)
        try {
            const view = await callsApi.place({ case_id: caseId, contact_id: contact.id }, newIdempotencyKey())
            callDock.show(view.id, view)
            ToastNotification(String(t('p5.call.placed')), String(t('p5.call.action')), 'info')
            onPlaced?.()
        } catch (e) {
            const rule = isV2Error(e) ? ((e.detail as { rule?: string } | null)?.rule || e.code) : 'unknown'
            onRefused(contact.id, { code: rule, detail: isV2Error(e) ? (e.detail as Record<string, unknown>) : null }, describeP5Error(e, t, 'dispatch'))
        } finally {
            setBusy(false)
        }
    }
    return (
        <Tooltip title={title}>
            <button
                type="button"
                aria-label={title}
                aria-disabled={!!known || busy || !!pending}
                disabled={busy}
                onClick={place}
                data-testid="p5-call-contact"
                data-known={known?.code || ''}
                className={
                    'inline-flex h-9 w-9 items-center justify-center rounded-md transition-colors ' +
                    (known || pending ? 'cursor-not-allowed text-gray-400' : 'text-azure-600 hover:bg-azure-50 dark:hover:bg-night-raised')
                }
            >
                <PhoneOutgoing size={17} strokeWidth={2} aria-hidden className={busy ? 'animate-pulse' : undefined} />
            </button>
        </Tooltip>
    )
}

/** The reason line under a contact row (known before the click, or the API's refusal). */
export const CallRefusalLine = ({ refusal, message }: { refusal: Refusal; message?: string }) => {
    const { t } = useTranslation()
    return (
        <div className="text-[12px] text-warning" data-testid="p5-call-refusal" data-code={refusal.code} role="status">
            {t('p5.call.cannotCall')} {message || refusalText(refusal.code, refusal.detail, t)}
        </div>
    )
}
