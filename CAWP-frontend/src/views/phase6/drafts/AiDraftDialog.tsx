/**
 * "Draft summary / coaching" for one call (`ai_assist`), opened from the call detail and from the
 * wrap-up: the call kinds (call summary, coaching), this call's previous drafts, save as a note
 * on the call's case. From the wrap-up, the reviewed text can also be copied into the wrap-up's
 * own note ("Use in the wrap-up note"); nothing is saved until the user saves.
 */
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import Button from '@/components/ui/Button'
import Dialog from '@/components/ui/Dialog'
import { CALL_DRAFT_KINDS } from '@/services/v2'
import { loadCaseFacts } from '@/views/phase5/wrapup/caseFacts'
import type { CaseFacts } from '@/views/phase5/wrapup/caseFacts'
import DraftWorkspace from './DraftWorkspace'

const AiDraftDialog = ({
    isOpen,
    attemptId,
    caseId,
    onClose,
    onUseText,
    onNoteSaved,
}: {
    isOpen: boolean
    attemptId: string
    caseId: string
    onClose: () => void
    onUseText?: (text: string) => void
    onNoteSaved?: () => void
}) => {
    const { t } = useTranslation()
    const [selected, setSelected] = useState<string | undefined>(undefined)
    const [facts, setFacts] = useState<CaseFacts | null>(null)
    useEffect(() => {
        if (!isOpen) return undefined
        let alive = true
        setSelected(undefined)
        loadCaseFacts(caseId)
            .then((f) => alive && setFacts(f))
            .catch(() => alive && setFacts({ identityNumber: null, contractId: null, isLocked: false, isArchived: false }))
        return () => {
            alive = false
        }
    }, [isOpen, caseId])
    return (
        <Dialog isOpen={isOpen} width={960} onClose={onClose} onRequestClose={onClose}>
            <div data-testid="p6-draft-dialog" className="flex max-h-[85vh] flex-col">
                <h5 className="mb-4 pe-8 text-[17px] font-extrabold text-navy-600 dark:text-gray-100">{t('p6.drafts.callDialogTitle')}</h5>
                <div className="min-h-0 flex-1 overflow-y-auto pe-1">
                    <DraftWorkspace
                        compact
                        source={{ caseId, attemptId }}
                        kinds={CALL_DRAFT_KINDS}
                        contractId={facts?.contractId || null}
                        caseLocked={facts?.isLocked}
                        caseArchived={facts?.isArchived}
                        selectedId={selected}
                        onSelect={setSelected}
                        onNoteSaved={() => onNoteSaved?.()}
                        onUseText={
                            onUseText
                                ? (text) => {
                                      onUseText(text)
                                      onClose()
                                  }
                                : undefined
                        }
                    />
                </div>
                <div className="mt-4 flex justify-end">
                    <Button size="sm" onClick={onClose} data-testid="p6-draft-dialog-close">
                        {t('p1.action.close')}
                    </Button>
                </div>
            </div>
        </Dialog>
    )
}

export default AiDraftDialog
