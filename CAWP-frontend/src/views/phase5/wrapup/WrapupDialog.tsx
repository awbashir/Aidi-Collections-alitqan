/**
 * Wrap-up of a PBX call (`POST /v2/calls/<id>/wrapup`, key `calls`): one reviewed
 * transaction of outcome, note, optional callback and an optional EXPLICITLY AGREED
 * promise. Opens by itself when a call ends (the dock) or from "My calls".
 *
 * - The outcomes on offer are the API's (`actions.wrapup_outcomes` of the live view): an
 *   outcome that claims a conversation needs the PBX's bridge evidence. The API enforces it
 *   (409 conversation_not_evidenced) and its refusal is shown as it comes.
 * - "Promise discussed" records a discussion only; only `connected` + "agreed" + the
 *   promises key creates a promise, through the Phase 1 promise rules.
 * - A locked or archived case: the wrap-up is recorded (outcome and note text) and the
 *   reservation released, but the note, callback, promise and blocks wait for a supervisor
 *   (review flag). The dialog says so before and after saving.
 */
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import Select from '@/components/ui/Select'
import ActionDialog, { DialogField } from '@/components/v2/ActionDialog'
import { SkeletonLines } from '@/components/v2/States'
import ToastNotification from '@/components/shared/myComponents/ToastNotification'
import { callsApi, describeP5Error, fetchLiveCall, historyApi, isV2Error } from '@/services/v2'
import type { CallDetail, LiveCall, Option, Outcome, Translate } from '@/services/v2'
import { usePermissions } from '@/utils/v2/hooks'
import { CaseCell, Notice, OutcomeBadge } from '../parts'
import { announceWrapped } from '../live/callDock'
import { loadCaseFacts } from './caseFacts'
import type { CaseFacts } from './caseFacts'
import { BlockingOutcomeFields, CallbackFields, NoteFields, OutcomePicker, PromiseFields } from './fields'
import {
    callbackValid,
    wrapupNoteBody,
    wrapupNoteValid,
    callbackValue,
    emptyCallback,
    emptyNote,
    emptyPromise,
    promiseValid,
    promiseValue,
} from './draft'
import type { CallbackDraft, NoteDraft, PromiseDraft } from './draft'
/* Phase 6: "Draft summary / coaching" with AI (ai_assist); the text is only copied on request. */
import Button from '@/components/ui/Button'
import AiDraftDialog from '@/views/phase6/drafts/AiDraftDialog'

const describe = (e: unknown, t: Translate) => describeP5Error(e, t)

const WrapupDialog = ({
    attemptId,
    isOpen,
    onClose,
    onDone,
}: {
    attemptId: string | null
    isOpen: boolean
    onClose: () => void
    onDone?: (res: CallDetail) => void
}) => {
    const { t } = useTranslation()
    const perms = usePermissions()
    const canPromise = !!(perms.promises || perms.clints_PayemntDates)
    const [view, setView] = useState<LiveCall | null>(null)
    const [facts, setFacts] = useState<CaseFacts | null>(null)
    const [loadError, setLoadError] = useState<unknown>(null)
    const [outcome, setOutcome] = useState<Outcome | undefined>(undefined)
    const [note, setNote] = useState<NoteDraft>(emptyNote())
    const [callback, setCallback] = useState<CallbackDraft>(emptyCallback())
    const [promise, setPromise] = useState<PromiseDraft>(emptyPromise())
    const [wrongReason, setWrongReason] = useState('')
    const [dncReason, setDncReason] = useState('')
    const [replacement, setReplacement] = useState<string | undefined>(undefined)
    const [candidates, setCandidates] = useState<Option[] | null>(null)
    const [aiOpen, setAiOpen] = useState(false)

    const reset = () => {
        setView(null)
        setFacts(null)
        setLoadError(null)
        setOutcome(undefined)
        setNote(emptyNote())
        setCallback(emptyCallback())
        setPromise(emptyPromise())
        setWrongReason('')
        setDncReason('')
        setReplacement(undefined)
        setCandidates(null)
    }

    /* Load the live view (fresh revision + allowed outcomes) and the case facts on open. */
    useEffect(() => {
        if (!isOpen || !attemptId) return undefined
        let alive = true
        setLoadError(null)
        fetchLiveCall(attemptId, null)
            .then(async (v) => {
                if (!alive || !v) return
                setView(v)
                if (v.case && !v.case.missing) {
                    try {
                        const f = await loadCaseFacts(v.case.id)
                        if (alive) setFacts(f)
                    } catch {
                        if (alive) setFacts({ identityNumber: null, contractId: null, isLocked: false, isArchived: false })
                    }
                }
            })
            .catch((e) => alive && setLoadError(e))
        return () => {
            alive = false
        }
    }, [isOpen, attemptId])

    const allowed = view?.actions.wrapup_outcomes || []
    const canSubmit =
        !!view &&
        view.actions.can_wrapup &&
        !!outcome &&
        wrapupNoteValid(note, callback) &&
        callbackValid(callback) &&
        promiseValid(promise, outcome) &&
        (candidates === null || !!replacement)

    const submit = async (key: string) => {
        if (!view || !outcome) return
        try {
            const res = await callsApi.wrapup(
                view.id,
                {
                    expected_revision: view.revision,
                    outcome,
                    ...(wrapupNoteBody(note) ? { note: wrapupNoteBody(note) } : {}),
                    ...(callbackValue(callback) ? { callback_at: callbackValue(callback) } : {}),
                    ...(promiseValue(promise, outcome) ? { promise: promiseValue(promise, outcome) } : {}),
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
            onDone?.(res)
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

    const locked = !!facts && (facts.isLocked || facts.isArchived)
    return (
        <ActionDialog
            isOpen={isOpen && !!attemptId}
            title={t('p5.wrapup.title')}
            confirmLabel={t('p5.wrapup.save')}
            canSubmit={canSubmit}
            width={720}
            testId="p5-wrapup"
            describeError={describe}
            onClose={() => {
                reset()
                onClose()
            }}
            onSubmit={submit}
        >
            {loadError ? (
                <Notice tone="danger">{describeP5Error(loadError, t)}</Notice>
            ) : !view ? (
                <SkeletonLines rows={6} />
            ) : (
                <>
                    <div className="mb-4 flex flex-wrap items-start justify-between gap-2 rounded-lg border border-gray-300 bg-gray-50 px-4 py-3 dark:border-night-border dark:bg-night-raised">
                        <CaseCell kase={view.case} />
                        <div className="text-end text-[12.5px] text-gray-600" data-testid="p5-wrapup-evidence" data-evidenced={view.conversation.evidenced ? '1' : '0'}>
                            {view.conversation.evidenced ? t('p5.wrapup.evidenced') : t('p5.wrapup.notEvidenced')}
                        </div>
                    </div>
                    {!view.actions.can_wrapup && (
                        <Notice tone="warning" className="mb-3" testId="p5-wrapup-not-allowed">
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
                    {locked && (
                        <Notice tone="warning" className="mb-3" title={t(facts?.isLocked ? 'p5.wrapup.lockedTitle' : 'p5.wrapup.archivedTitle')} testId="p5-wrapup-locked">
                            {t('p5.wrapup.lockedBody')}
                        </Notice>
                    )}
                    <div className="text-eyebrow mb-2">{t('p5.wrapup.outcome')}</div>
                    <div className="mb-2">
                        <OutcomePicker value={outcome} onChange={setOutcome} allowed={allowed} />
                    </div>
                    {!view.conversation.evidenced && view.actions.can_wrapup && (
                        <div className="mb-4 text-[12px] text-gray-600" data-testid="p5-wrapup-bridge-rule">
                            {t('p5.wrapup.bridgeRule')}
                        </div>
                    )}
                    <PromiseFields outcome={outcome} value={promise} onChange={setPromise} canPromise={canPromise} />
                    <BlockingOutcomeFields
                        outcome={outcome}
                        wrongReason={wrongReason}
                        dncReason={dncReason}
                        onWrongReason={setWrongReason}
                        onDncReason={setDncReason}
                    />
                    {outcome === 'wrong_number' && candidates !== null && (
                        <DialogField label={t('p5.wrapup.replacementDefault')} required hint={t('p5.wrapup.replacementHint')}>
                            <div data-testid="p5-wrapup-replacement">
                                <Select
                                    size="sm"
                                    menuPosition="fixed"
                                    options={candidates}
                                    value={candidates.find((c) => c.value === replacement) || null}
                                    onChange={(o) => setReplacement((o as Option | null)?.value || undefined)}
                                />
                            </div>
                        </DialogField>
                    )}
                    <div className="mb-2 mt-2 flex flex-wrap items-center justify-between gap-2">
                        <span className="text-eyebrow">{t('p5.wrapup.noteSection')}</span>
                        {perms.ai_assist && view.case && !view.case.missing && (
                            <Button size="xs" onClick={() => setAiOpen(true)} data-testid="p6-wrapup-ai-drafts">
                                {t('p6.drafts.wrapupEntry')}
                            </Button>
                        )}
                    </div>
                    {perms.ai_assist && view.case && !view.case.missing && aiOpen && (
                        <AiDraftDialog
                            isOpen={aiOpen}
                            attemptId={view.id}
                            caseId={view.case.id}
                            onClose={() => setAiOpen(false)}
                            onUseText={(text) => setNote({ ...note, text })}
                        />
                    )}
                    <NoteFields value={note} onChange={setNote} contractId={facts?.contractId || null} active={isOpen} optional />
                    <div className="text-eyebrow mb-2">{t('p5.wrapup.callbackSection')}</div>
                    <CallbackFields value={callback} onChange={setCallback} />
                    {callback.date && !note.status && (
                        <div className="-mt-2 mb-3 text-[12px] text-danger" data-testid="p5-wrapup-callback-needs-status">
                            {t('p5.wrapup.callbackNeedsStatus')}
                        </div>
                    )}
                </>
            )}
        </ActionDialog>
    )
}

export default WrapupDialog
