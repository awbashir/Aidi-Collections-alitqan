/**
 * The wrap-up form (`POST /v2/calls/<id>/wrapup`), shared by the wrap-up dialog (calls whose
 * client page is not open: campaigns, other screens) and the client page's note tab in
 * "call result" mode (customer decision 2026-10-17: one entry, the result closes the call).
 *
 * - The outcomes on offer are the API's (`actions.wrapup_outcomes`); "do not call" only for
 *   `calls_manage`, "wrong number" after any call that may be wrapped up (fields.tsx).
 * - The note statuses follow the one shared rule (`noteStatusOptions`) with the case's
 *   contract from the case data the screen already has; an unknown contract offers the
 *   general statuses only.
 * - A deleted case (`case.missing`): only the outcome and the text; the API keeps them for a
 *   supervisor's review.
 * - Note text is never required.
 */
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import Input from '@/components/ui/Input'
import Select from '@/components/ui/Select'
import { DialogField } from '@/components/v2/ActionDialog'
import ToastNotification from '@/components/shared/myComponents/ToastNotification'
import { callsApi, historyApi, isV2Error } from '@/services/v2'
import type { CallDetail, LiveCall, Option, Outcome } from '@/services/v2'
import { usePermissions } from '@/utils/v2/hooks'
import { Notice, OutcomeBadge } from '../parts'
import { announceWrapped } from '../live/callDock'
import type { CaseFacts } from './caseFacts'
import { BlockingOutcomeFields, CallbackFields, NoteFields, OutcomePicker, PromiseFields } from './fields'
import {
    callbackValid,
    callbackValue,
    emptyCallback,
    emptyNote,
    emptyPromise,
    promiseValid,
    promiseValue,
    wrapupNoteBody,
    wrapupNoteValid,
} from './draft'
import type { CallbackDraft, NoteDraft, PromiseDraft } from './draft'
/* Phase 6: "Draft summary / coaching" with AI (ai_assist); the text is only copied on request. */
import Button from '@/components/ui/Button'
import AiDraftDialog from '@/views/phase6/drafts/AiDraftDialog'

/** The case's contract as the call's own case data gives it (when the API sends it). */
export function contractOfCall(view: LiveCall | null): string | null | undefined {
    const kase = view?.case as (LiveCall['case'] & { contract_id?: string | null }) | undefined
    return kase && 'contract_id' in kase ? kase.contract_id ?? null : undefined
}

export type WrapupFormState = ReturnType<typeof useWrapupForm>

export function useWrapupForm(view: LiveCall | null, facts: CaseFacts | null) {
    const { t } = useTranslation()
    const [outcome, setOutcome] = useState<Outcome | undefined>(undefined)
    const [note, setNote] = useState<NoteDraft>(emptyNote())
    const [callback, setCallback] = useState<CallbackDraft>(emptyCallback())
    const [promise, setPromise] = useState<PromiseDraft>(emptyPromise())
    const [wrongReason, setWrongReason] = useState('')
    const [dncReason, setDncReason] = useState('')
    const [replacement, setReplacement] = useState<string | undefined>(undefined)
    const [candidates, setCandidates] = useState<Option[] | null>(null)

    const reset = () => {
        setOutcome(undefined)
        setNote(emptyNote())
        setCallback(emptyCallback())
        setPromise(emptyPromise())
        setWrongReason('')
        setDncReason('')
        setReplacement(undefined)
        setCandidates(null)
    }

    const missing = !!view?.case?.missing
    const canSubmit =
        !!view &&
        view.actions.can_wrapup &&
        !!outcome &&
        (missing || (wrapupNoteValid(note, callback) && callbackValid(callback) && promiseValid(promise, outcome))) &&
        (candidates === null || !!replacement)

    const submit = async (key: string): Promise<CallDetail | null> => {
        if (!view || !outcome) return null
        /* A deleted case: the outcome and the text only (kept for review). */
        const noteBody = missing ? (note.text.trim() ? { text: note.text.trim() } : undefined) : wrapupNoteBody(note)
        try {
            const res = await callsApi.wrapup(
                view.id,
                {
                    expected_revision: view.revision,
                    outcome,
                    ...(noteBody ? { note: noteBody } : {}),
                    ...(!missing && callbackValue(callback) ? { callback_at: callbackValue(callback) } : {}),
                    ...(!missing && promiseValue(promise, outcome) ? { promise: promiseValue(promise, outcome) } : {}),
                    ...(outcome === 'wrong_number' && wrongReason.trim() ? { wrong_number_reason: wrongReason.trim() } : {}),
                    ...(outcome === 'wrong_number' && replacement ? { replacement_default_id: replacement } : {}),
                    ...(outcome === 'do_not_call' && dncReason.trim() ? { do_not_call_reason: dncReason.trim() } : {}),
                },
                key
            )
            const review = !!res.wrapup?.needs_review
            ToastNotification(
                review ? String(t('p5.wrapup.savedForReview')) : String(t('p5.wrapup.saved')),
                String(t('p5.wrapup.title')),
                review ? 'warning' : 'success',
                review ? 8000 : 2500
            )
            announceWrapped({ attemptId: view.id, caseId: view.case?.id || null, needsReview: review })
            reset()
            return res
        } catch (e) {
            /* Wrong number on the default contact: the API names the eligible replacements. */
            if (isV2Error(e) && e.code === 'replacement_default_required' && facts?.identityNumber) {
                const ids = ((e.detail as { candidates?: string[] } | null)?.candidates || []).map(String)
                try {
                    const list = await historyApi.contacts(facts.identityNumber)
                    setCandidates(
                        list.contacts
                            .filter((c) => ids.includes(c.id) || c.row_ids.some((r) => ids.includes(r)))
                            .map((c) => ({ value: c.id, label: c.contact }))
                    )
                } catch {
                    setCandidates([])
                }
            }
            throw e
        }
    }

    return {
        outcome,
        setOutcome,
        note,
        setNote,
        callback,
        setCallback,
        promise,
        setPromise,
        wrongReason,
        setWrongReason,
        dncReason,
        setDncReason,
        replacement,
        setReplacement,
        candidates,
        reset,
        canSubmit,
        submit,
    }
}

/** The form's fields (no frame, no save button): the dialog and the client page add theirs. */
export const WrapupFields = ({
    form,
    view,
    facts,
    active,
    testId = 'p5-wrapup',
}: {
    form: WrapupFormState
    view: LiveCall
    facts: CaseFacts | null
    active: boolean
    testId?: string
}) => {
    const { t } = useTranslation()
    const perms = usePermissions()
    const canPromise = !!(perms.promises || perms.clints_PayemntDates)
    const [aiOpen, setAiOpen] = useState(false)
    const missing = !!view.case?.missing
    const locked = !!facts && (facts.isLocked || facts.isArchived)
    const { outcome, note, setNote, callback } = form
    return (
        <>
            {!view.actions.can_wrapup && (
                <Notice tone="warning" className="mb-3" testId={`${testId}-not-allowed`}>
                    {view.wrapup ? (
                        <>
                            {t('p5.wrapup.already')} <OutcomeBadge outcome={view.wrapup.outcome} size="row" />
                        </>
                    ) : view.state === 'uncertain' ? (
                        t('p5.wrapup.uncertain')
                    ) : (
                        t('p5.wrapup.notYet')
                    )}
                </Notice>
            )}
            {missing && (
                <Notice tone="warning" className="mb-3" testId={`${testId}-case-missing`}>
                    {t('p8.clientpage.caseDeleted')}
                </Notice>
            )}
            {locked && (
                <Notice tone="warning" className="mb-3" title={t(facts?.isLocked ? 'p5.wrapup.lockedTitle' : 'p5.wrapup.archivedTitle')} testId={`${testId}-locked`}>
                    {t('p5.wrapup.lockedBody')}
                </Notice>
            )}
            <div className="text-eyebrow mb-2">{t('p5.wrapup.outcome')}</div>
            <div className="mb-2">
                <OutcomePicker value={outcome} onChange={form.setOutcome} allowed={view.actions.wrapup_outcomes || []} testId={testId === 'p5-wrapup' ? 'p5-outcome' : `${testId}-outcome`} />
            </div>
            {!view.conversation.evidenced && view.actions.can_wrapup && (
                <div className="mb-4 text-[12px] text-gray-600" data-testid={`${testId}-bridge-rule`}>
                    {t('p8.clientpage.bridgeRule')}
                </div>
            )}
            {!missing && <PromiseFields outcome={outcome} value={form.promise} onChange={form.setPromise} canPromise={canPromise} />}
            <BlockingOutcomeFields
                outcome={outcome}
                wrongReason={form.wrongReason}
                dncReason={form.dncReason}
                onWrongReason={form.setWrongReason}
                onDncReason={form.setDncReason}
            />
            {outcome === 'wrong_number' && form.candidates !== null && (
                <DialogField label={t('p5.wrapup.replacementDefault')} required hint={t('p5.wrapup.replacementHint')}>
                    <div data-testid={`${testId}-replacement`}>
                        <Select
                            size="sm"
                            menuPosition="fixed"
                            options={form.candidates}
                            value={form.candidates.find((c) => c.value === form.replacement) || null}
                            onChange={(o) => form.setReplacement((o as Option | null)?.value || undefined)}
                        />
                    </div>
                </DialogField>
            )}
            <div className="mb-2 mt-2 flex flex-wrap items-center justify-between gap-2">
                <span className="text-eyebrow">{t('p5.wrapup.noteSection')}</span>
                {perms.ai_assist && view.case && !missing && (
                    <Button size="xs" onClick={() => setAiOpen(true)} data-testid="p6-wrapup-ai-drafts">
                        {t('p6.drafts.wrapupEntry')}
                    </Button>
                )}
            </div>
            {perms.ai_assist && view.case && !missing && aiOpen && (
                <AiDraftDialog
                    isOpen={aiOpen}
                    attemptId={view.id}
                    caseId={view.case.id}
                    onClose={() => setAiOpen(false)}
                    onUseText={(text) => setNote({ ...note, text })}
                />
            )}
            {missing ? (
                <DialogField label={t('p5.wrapup.noteText')}>
                    <Input textArea value={note.text} maxLength={5000} onChange={(e) => setNote({ ...note, text: e.target.value })} data-testid={`${testId}-text`} />
                </DialogField>
            ) : (
                <>
                    <NoteFields value={note} onChange={setNote} contractId={facts?.contractId ?? null} active={active} optional />
                    <div className="text-eyebrow mb-2">{t('p5.wrapup.callbackSection')}</div>
                    <CallbackFields value={callback} onChange={form.setCallback} />
                    {callback.date && !note.status && (
                        <div className="-mt-2 mb-3 text-[12px] text-danger" data-testid={`${testId}-callback-needs-status`}>
                            {t('p5.wrapup.callbackNeedsStatus')}
                        </div>
                    )}
                </>
            )}
        </>
    )
}
