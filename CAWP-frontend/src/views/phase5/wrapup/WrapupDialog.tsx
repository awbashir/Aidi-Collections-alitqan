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
 * - 2026-10-17: a call whose client page is open is wrapped up IN that page (note tab, "call
 *   result" mode, callDock.openWrapup); this dialog is for the other calls. The form itself is
 *   WrapupForm.tsx, shared by both.
 */
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import ActionDialog from '@/components/v2/ActionDialog'
import { SkeletonLines } from '@/components/v2/States'
import { describeP5Error, fetchLiveCall } from '@/services/v2'
import type { CallDetail, LiveCall, Translate } from '@/services/v2'
import { CaseCell, Notice } from '../parts'
import { loadCaseFacts } from './caseFacts'
import type { CaseFacts } from './caseFacts'
import { WrapupFields, contractOfCall, useWrapupForm } from './WrapupForm'

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
    const [view, setView] = useState<LiveCall | null>(null)
    const [facts, setFacts] = useState<CaseFacts | null>(null)
    const [loadError, setLoadError] = useState<unknown>(null)
    const form = useWrapupForm(view, facts)

    const reset = () => {
        setView(null)
        setFacts(null)
        setLoadError(null)
        form.reset()
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
                    /* The contract from the call's own case data when the API sends it; else the
                       legacy case record, which a non-owner (campaign call) cannot read: then the
                       contract is unknown and only the general statuses are offered. */
                    const fromCall = contractOfCall(v)
                    try {
                        const f = await loadCaseFacts(v.case.id)
                        if (alive) setFacts(fromCall !== undefined ? { ...f, contractId: fromCall ?? f.contractId } : f)
                    } catch {
                        if (alive) setFacts({ identityNumber: null, contractId: fromCall ?? null, isLocked: false, isArchived: false })
                    }
                }
            })
            .catch((e) => alive && setLoadError(e))
        return () => {
            alive = false
        }
    }, [isOpen, attemptId])

    const submit = async (key: string) => {
        const res = await form.submit(key)
        if (!res) return
        reset()
        onDone?.(res)
    }

    return (
        <ActionDialog
            isOpen={isOpen && !!attemptId}
            title={t('p5.wrapup.title')}
            confirmLabel={t('p5.wrapup.save')}
            canSubmit={form.canSubmit}
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
                    <WrapupFields form={form} view={view} facts={facts} active={isOpen} />
                </>
            )}
        </ActionDialog>
    )
}

export default WrapupDialog
