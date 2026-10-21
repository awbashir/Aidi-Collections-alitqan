/**
 * "Save as note" (`POST /v2/ai/drafts/<id>/save-as-note`, key `ai_assist`): THE explicit action
 * that turns a reviewed AI draft into one case note. The note form is the legacy one (text,
 * reachability, status filtered by the case's contract, contact method defaulting to mobile).
 * The user confirms they reviewed the AI-generated text. There is no callback, promise, payment,
 * call or message: the API refuses those fields (`422 draft_note_only`).
 * A draft never creates a promise; since 2026-10-21 a promise-to-pay status (0079,
 * `requires_promise`) no longer needs one, so such a note is saved like any other.
 * 0081: "apply to all the client's accounts" (default on, when the case has other active
 * accounts of the same identity) writes the same note to each of them.
 */
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import ActionDialog from '@/components/v2/ActionDialog'
import { draftsApi, describeP6Error, isV2Error } from '@/services/v2'
import type { AiDraft, SavedNote, Translate } from '@/services/v2'
import { Notice } from '@/views/phase5/parts'
import { NoteFields } from '@/views/phase5/wrapup/fields'
import { emptyNote, noteValid } from '@/views/phase5/wrapup/draft'
import type { NoteDraft } from '@/views/phase5/wrapup/draft'
import { ApplyToAllAccounts, announceAppliedTo, useApplyToAll } from '@/views/phase1/history/ApplyToAllAccounts'
import { AiGeneratedNotice, CheckField } from '../parts'

const describe = (e: unknown, t: Translate) =>
    isV2Error(e) && e.code === 'too_many_accounts' ? String(t('p11.notes.err.too_many_accounts')) : describeP6Error(e, t)

const SaveAsNoteDialog = ({
    draft,
    text,
    isOpen,
    contractId,
    caseLocked,
    caseArchived,
    onClose,
    onSaved,
    onReload,
}: {
    draft: AiDraft
    /** The text as the user edited it (may differ from the draft's saved text). */
    text: string
    isOpen: boolean
    contractId: string | null
    caseLocked?: boolean
    caseArchived?: boolean
    onClose: () => void
    onSaved: (res: SavedNote) => void
    onReload?: () => void
}) => {
    const { t } = useTranslation()
    const [note, setNote] = useState<NoteDraft>(emptyNote())
    const [confirmed, setConfirmed] = useState(false)
    const applyAll = useApplyToAll(draft.case_id, isOpen)
    useEffect(() => {
        if (isOpen) {
            setNote({ ...emptyNote(), text })
            setConfirmed(false)
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [isOpen])
    const blocked = !!(caseLocked || caseArchived)
    return (
        <ActionDialog
            isOpen={isOpen}
            title={t('p6.drafts.saveTitle')}
            confirmLabel={t('p6.drafts.saveConfirm')}
            canSubmit={!blocked && confirmed && noteValid(note)}
            width={720}
            testId="p6-save-note"
            describeError={describe}
            onReload={onReload}
            onClose={onClose}
            onSubmit={async (key) => {
                const applyFlag = applyAll.flag()
                const res = await draftsApi.saveAsNote(
                    draft.id,
                    {
                        expected_revision: draft.revision,
                        text: note.text.trim(),
                        comment_status_id: note.status as string,
                        ...(note.subStatus ? { comment_sub_status_id: note.subStatus } : {}),
                        contact_method_id: note.method as string,
                        ...(note.reach ? { comment_reachability_id: note.reach } : {}),
                        confirmed: true,
                        ...(applyFlag !== undefined ? { apply_to_all_accounts: applyFlag } : {}),
                    },
                    key
                )
                announceAppliedTo(t, res.applied_to, res.skipped_accounts)
                onSaved(res)
            }}
        >
            <AiGeneratedNotice text={draft.notice} className="mb-3" />
            {blocked && (
                <Notice tone="warning" className="mb-3" testId="p6-save-note-blocked">
                    {caseLocked ? t('p6.drafts.caseLocked') : t('p6.drafts.caseArchived')}
                </Notice>
            )}
            <p className="mb-3 text-[13px] text-gray-700 dark:text-night-body">{t('p6.drafts.saveHint')}</p>
            <NoteFields
                value={note}
                onChange={setNote}
                contractId={contractId}
                active={isOpen}
                testId="p6-note"
                textHint={t('p6.drafts.noteHint')}
            />
            <ApplyToAllAccounts state={applyAll} testId="p11-ai-apply-all" />
            <div className="rounded-md border border-gray-300 p-3 dark:border-night-border">
                <CheckField checked={confirmed} onChange={setConfirmed} testId="p6-save-note-reviewed">
                    <span className="text-[13.5px] text-gray-800 dark:text-night-body">{t('p6.drafts.saveConfirmCheck')}</span>
                </CheckField>
            </div>
            <div className="mt-2 text-[12px] text-gray-600">{t('p6.drafts.saveNothingElse')}</div>
        </ActionDialog>
    )
}

export default SaveAsNoteDialog
