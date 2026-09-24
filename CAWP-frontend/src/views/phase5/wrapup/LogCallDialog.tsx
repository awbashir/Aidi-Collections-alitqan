/**
 * "Log a call" (`POST /v2/calls/manual`, key `calls`): a call made outside the PBX flow
 * (desk phone, mobile) with its wrap-up in one transaction: start time, duration (blank =
 * unknown, never 0), direction, outcome, note, optional callback, optional explicitly
 * agreed promise, optional transcript text. The manual log records what the collector
 * reports; PBX evidence rules apply only to PBX calls.
 *
 * A locked or archived case refuses a manual log (the legacy note rule, 409).
 */
import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import Input from '@/components/ui/Input'
import Select from '@/components/ui/Select'
import ActionDialog, { DialogField } from '@/components/v2/ActionDialog'
import ToastNotification from '@/components/shared/myComponents/ToastNotification'
import { Segmented } from '@/views/phase1/followUps/parts'
import { callsApi, describeP5Error, historyApi } from '@/services/v2'
import type { CallDetail, Contact, Option, Outcome, Translate } from '@/services/v2'
import { usePermissions } from '@/utils/v2/hooks'
import { riyadhInstant, riyadhToday } from '@/utils/v2/format'
import { Notice } from '../parts'
import { announceWrapped } from '../live/callDock'
import { BlockingOutcomeFields, CallbackFields, NoteFields, OutcomePicker, PromiseFields } from './fields'
import {
    callbackValid,
    callbackValue,
    emptyCallback,
    emptyNote,
    emptyPromise,
    noteBody,
    noteValid,
    promiseValid,
    promiseValue,
} from './draft'
import type { CallbackDraft, NoteDraft, PromiseDraft } from './draft'

export type LogCallCase = {
    id: string
    name?: string | null
    accountNumber?: string | null
    identityNumber?: string | null
    contractId?: string | null
    isLocked?: boolean
    isArchived?: boolean
}

const nowRiyadhTime = () =>
    new Intl.DateTimeFormat('en-GB', { timeZone: 'Asia/Riyadh', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).format(new Date())

const describe = (e: unknown, t: Translate) => describeP5Error(e, t)

const LogCallDialog = ({
    caseInfo,
    isOpen,
    onClose,
    onDone,
}: {
    caseInfo: LogCallCase
    isOpen: boolean
    onClose: () => void
    onDone?: (res: CallDetail) => void
}) => {
    const { t } = useTranslation()
    const perms = usePermissions()
    const canPromise = !!(perms.promises || perms.clints_PayemntDates)
    const [contacts, setContacts] = useState<Contact[]>([])
    const [contactId, setContactId] = useState<string | undefined>(undefined)
    const [date, setDate] = useState(riyadhToday())
    const [time, setTime] = useState(nowRiyadhTime())
    const [minutes, setMinutes] = useState('')
    const [seconds, setSeconds] = useState('')
    const [direction, setDirection] = useState<'outbound' | 'inbound'>('outbound')
    const [outcome, setOutcome] = useState<Outcome | undefined>(undefined)
    const [note, setNote] = useState<NoteDraft>(emptyNote())
    const [callback, setCallback] = useState<CallbackDraft>(emptyCallback())
    const [promise, setPromise] = useState<PromiseDraft>(emptyPromise())
    const [wrongReason, setWrongReason] = useState('')
    const [dncReason, setDncReason] = useState('')
    const [transcript, setTranscript] = useState('')

    useEffect(() => {
        if (!isOpen || !caseInfo.identityNumber) return undefined
        let alive = true
        historyApi
            .contacts(caseInfo.identityNumber)
            .then((d) => alive && setContacts(d.contacts))
            .catch(() => alive && setContacts([]))
        return () => {
            alive = false
        }
    }, [isOpen, caseInfo.identityNumber])

    const contactOptions: Option[] = useMemo(
        () =>
            contacts
                .filter((c) => c.contact_type?.name !== 'email')
                .map((c) => ({
                    value: c.id,
                    label: `${c.contact}${c.is_default ? ` · ${t('p1.contacts.default')}` : ''}${c.is_blocked ? ` · ${t('p1.contacts.blocked')}` : ''}`,
                })),
        [contacts, t]
    )

    const reset = () => {
        setContactId(undefined)
        setDate(riyadhToday())
        setTime(nowRiyadhTime())
        setMinutes('')
        setSeconds('')
        setDirection('outbound')
        setOutcome(undefined)
        setNote(emptyNote())
        setCallback(emptyCallback())
        setPromise(emptyPromise())
        setWrongReason('')
        setDncReason('')
        setTranscript('')
    }

    const durationKnown = minutes.trim() !== '' || seconds.trim() !== ''
    const durationOk =
        !durationKnown || (/^\d{0,4}$/.test(minutes.trim()) && /^\d{0,2}$/.test(seconds.trim()) && Number(seconds || 0) < 60)
    const duration = durationKnown ? Number(minutes || 0) * 60 + Number(seconds || 0) : undefined
    const blocked = !!(caseInfo.isLocked || caseInfo.isArchived)
    const canSubmit =
        !blocked &&
        !!date &&
        /^\d{2}:\d{2}$/.test(time) &&
        durationOk &&
        !!outcome &&
        (outcome !== 'wrong_number' || !!contactId) &&
        noteValid(note) &&
        callbackValid(callback) &&
        promiseValid(promise, outcome)

    const submit = async (key: string) => {
        if (!outcome) return
        const res = await callsApi.logManual(
            {
                case_id: caseInfo.id,
                ...(contactId ? { contact_id: contactId } : {}),
                started_at: riyadhInstant(date, time),
                ...(duration !== undefined ? { duration_seconds: duration } : {}),
                direction,
                outcome,
                note: noteBody(note),
                ...(callbackValue(callback) ? { callback_at: callbackValue(callback) } : {}),
                ...(promiseValue(promise, outcome) ? { promise: promiseValue(promise, outcome) } : {}),
                ...(outcome === 'wrong_number' && wrongReason.trim() ? { wrong_number_reason: wrongReason.trim() } : {}),
                ...(outcome === 'do_not_call' && dncReason.trim() ? { do_not_call_reason: dncReason.trim() } : {}),
                ...(transcript.trim() ? { transcript: transcript.trim() } : {}),
            },
            key
        )
        ToastNotification(String(t('p5.log.saved')), String(t('p5.log.title')), 'success')
        announceWrapped({ attemptId: res.id, caseId: caseInfo.id, needsReview: false })
        reset()
        onDone?.(res)
    }

    return (
        <ActionDialog
            isOpen={isOpen}
            title={t('p5.log.title')}
            confirmLabel={t('p5.log.save')}
            canSubmit={canSubmit}
            width={720}
            testId="p5-log"
            describeError={describe}
            onClose={() => {
                reset()
                onClose()
            }}
            onSubmit={submit}
        >
            <div className="mb-4 text-[13px] text-gray-700 dark:text-night-body">{t('p5.log.intro')}</div>
            {blocked && (
                <Notice tone="warning" className="mb-3" testId="p5-log-locked">
                    {t(caseInfo.isLocked ? 'p5.log.locked' : 'p5.log.archived')}
                </Notice>
            )}
            <div className="grid grid-cols-1 gap-x-3 sm:grid-cols-3">
                <DialogField label={t('p5.log.date')} required hint={t('p5.log.riyadhHint')}>
                    <Input type="date" size="sm" max={riyadhToday()} value={date} onChange={(e) => setDate(e.target.value)} data-testid="p5-log-date" />
                </DialogField>
                <DialogField label={t('p5.log.time')} required>
                    <Input type="time" size="sm" value={time} onChange={(e) => setTime(e.target.value)} data-testid="p5-log-time" />
                </DialogField>
                <DialogField label={t('p5.log.direction')}>
                    <Segmented<'outbound' | 'inbound'>
                        value={direction}
                        onChange={setDirection}
                        testId="p5-log-direction"
                        options={[
                            { value: 'outbound', label: t('p5.direction.outbound') },
                            { value: 'inbound', label: t('p5.direction.inbound') },
                        ]}
                    />
                </DialogField>
            </div>
            <div className="grid grid-cols-1 gap-x-3 sm:grid-cols-3">
                <DialogField label={t('p5.log.contact')} hint={t('p5.log.contactHint')}>
                    <div data-testid="p5-log-contact">
                        <Select
                            size="sm"
                            menuPosition="fixed"
                            isClearable
                            placeholder={t('p5.log.noContact')}
                            options={contactOptions}
                            value={contactOptions.find((o) => o.value === contactId) || null}
                            onChange={(o) => setContactId((o as Option | null)?.value || undefined)}
                        />
                    </div>
                </DialogField>
                <DialogField label={t('p5.log.minutes')} hint={t('p5.log.durationHint')}>
                    <Input size="sm" dir="ltr" inputMode="numeric" value={minutes} onChange={(e) => setMinutes(e.target.value.replace(/[^0-9]/g, '').slice(0, 4))} data-testid="p5-log-minutes" />
                </DialogField>
                <DialogField label={t('p5.log.seconds')}>
                    <Input size="sm" dir="ltr" inputMode="numeric" value={seconds} onChange={(e) => setSeconds(e.target.value.replace(/[^0-9]/g, '').slice(0, 2))} data-testid="p5-log-seconds" />
                </DialogField>
            </div>
            {!durationKnown && <div className="-mt-2 mb-3 text-[12px] text-gray-600">{t('p5.log.durationUnknown')}</div>}
            <div className="text-eyebrow mb-2">{t('p5.wrapup.outcome')}</div>
            <div className="mb-3">
                <OutcomePicker value={outcome} onChange={setOutcome} testId="p5-log-outcome" />
            </div>
            <PromiseFields outcome={outcome} value={promise} onChange={setPromise} canPromise={canPromise} testId="p5-log-promise" />
            <BlockingOutcomeFields outcome={outcome} wrongReason={wrongReason} dncReason={dncReason} onWrongReason={setWrongReason} onDncReason={setDncReason} />
            {outcome === 'wrong_number' && !contactId && <div className="mb-3 text-[12px] text-danger">{t('p5.log.wrongNeedsContact')}</div>}
            <div className="text-eyebrow mb-2 mt-2">{t('p5.wrapup.noteSection')}</div>
            <NoteFields value={note} onChange={setNote} contractId={caseInfo.contractId || null} active={isOpen} testId="p5-log-note" />
            <div className="text-eyebrow mb-2">{t('p5.wrapup.callbackSection')}</div>
            <CallbackFields value={callback} onChange={setCallback} testId="p5-log-callback" />
            <DialogField label={t('p5.log.transcript')} hint={t('p5.log.transcriptHint')}>
                <Input textArea value={transcript} maxLength={100000} onChange={(e) => setTranscript(e.target.value)} data-testid="p5-log-transcript" />
            </DialogField>
        </ActionDialog>
    )
}

export default LogCallDialog
