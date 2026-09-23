/**
 * Correct a note (new text + reason, `clints_comments_correct`) and retire a
 * note (reason, `clints_comments_retire`). Both send the revision they loaded;
 * a 409 offers a reload and saves nothing. The original text, author and time
 * are always kept by the API.
 */
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import Input from '@/components/ui/Input'
import { historyApi } from '@/services/v2'
import type { Note } from '@/services/v2'
import ActionDialog, { DialogField, ReasonDialog } from '@/components/v2/ActionDialog'

export const CorrectNoteDialog = ({
    note,
    onClose,
    onDone,
    onReload,
}: {
    note: Note | null
    onClose: () => void
    onDone: (n: Note) => void
    onReload: () => void
}) => {
    const { t } = useTranslation()
    const [text, setText] = useState('')
    const [reason, setReason] = useState('')
    useEffect(() => {
        if (note) {
            setText(note.text)
            setReason('')
        }
    }, [note])
    const changed = !!note && text.trim() !== note.text.trim()
    return (
        <ActionDialog
            isOpen={!!note}
            width={640}
            title={t('p1.notes.correctTitle')}
            confirmLabel={t('p1.notes.correct')}
            canSubmit={changed && !!text.trim() && !!reason.trim()}
            testId="p1-note-correct-dialog"
            onClose={onClose}
            onReload={onReload}
            onSubmit={async (key) => {
                if (!note) return
                const n = await historyApi.correctNote(
                    note.id,
                    { text: text.trim(), reason: reason.trim(), expected_revision: note.revision },
                    key
                )
                onDone(n)
            }}
        >
            <p className="mb-4 text-[13px] text-gray-600">{t('p1.notes.correctHint')}</p>
            <DialogField label={t('p1.notes.newText')} required>
                <Input
                    textArea
                    dir="auto"
                    value={text}
                    maxLength={5000}
                    onChange={(e) => setText(e.target.value)}
                    data-testid="p1-note-correct-dialog-text"
                />
            </DialogField>
            <DialogField label={t('p1.fields.reason')} required hint={t('p1.hint.reasonRequired')}>
                <Input
                    textArea
                    value={reason}
                    maxLength={2000}
                    onChange={(e) => setReason(e.target.value)}
                    data-testid="p1-note-correct-dialog-reason"
                />
            </DialogField>
        </ActionDialog>
    )
}

export const RetireNoteDialog = ({
    note,
    onClose,
    onDone,
    onReload,
}: {
    note: Note | null
    onClose: () => void
    onDone: (n: Note) => void
    onReload: () => void
}) => {
    const { t } = useTranslation()
    return (
        <ReasonDialog
            isOpen={!!note}
            title={t('p1.notes.retireTitle')}
            description={
                <>
                    <div>{t('p1.notes.retireHint')}</div>
                    {note && (
                        <blockquote
                            dir="auto"
                            className="mt-3 whitespace-pre-wrap rounded-md border border-gray-300 bg-gray-50 p-3 text-[13.5px] dark:border-night-border dark:bg-night-raised"
                        >
                            {note.text}
                        </blockquote>
                    )}
                </>
            }
            confirmLabel={t('p1.notes.retire')}
            tone="danger"
            testId="p1-note-retire-dialog"
            onClose={onClose}
            onReload={onReload}
            onSubmit={async (reason, key) => {
                if (!note) return
                const n = await historyApi.retireNote(note.id, { reason, expected_revision: note.revision }, key)
                onDone(n)
            }}
        />
    )
}
