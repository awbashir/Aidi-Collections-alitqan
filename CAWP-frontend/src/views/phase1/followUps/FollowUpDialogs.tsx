/**
 * Follow-up mutations: complete, reschedule, create (manual).
 *
 * Every dialog runs inside `ActionDialog`: one Idempotency-Key per opening
 * (reused on retry), the draft survives an error, and a 409 revision / not-open
 * conflict offers "reload" instead of retrying with a fresh revision.
 * Client checks mirror the API's required fields only; the API stays the judge.
 */
import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import Input from '@/components/ui/Input'
import Select from '@/components/ui/Select'
import Button from '@/components/ui/Button'
import ActionDialog, { DialogField } from '@/components/v2/ActionDialog'
import { RiyadhTime } from '@/components/v2/Display'
import { BucketBadge } from '@/components/v2/Badges'
import { Note } from '@/components/v2/States'
import { isV2Error, lookups, worklistApi } from '@/services/v2'
import type { CommentStatusOption, FollowUp, Option } from '@/services/v2'
import { mainStatusesFor, subStatusesOf } from '@/utils/commentStatusTree'
import { riyadhInstant } from '@/utils/v2/format'
import { DueFields, FollowUpSummary, Segmented } from './parts'
import { validDue } from './access'

/** The follow-up fields a dialog needs (a list item, the case `current`, or a 409 hint). */
export type FollowUpRef = Pick<FollowUp, 'id' | 'revision' | 'due_at'> &
    Partial<Pick<FollowUp, 'next_action' | 'bucket' | 'days_late' | 'original_due_at' | 'case'>>

/* The legacy note form defaults the contact method to "mobile" (Comments.tsx MOBILE_METHOD_ID). */
const MOBILE_METHOD_ID = 'ff081c7e-4500-4a75-ae36-580f2ae90834'

type NoteLookups = {
    methods: Option[]
    reachability: Option[]
    statuses: CommentStatusOption[]
    loading: boolean
    failed: boolean
}

function useNoteLookups(active: boolean): NoteLookups {
    const [state, setState] = useState<NoteLookups>({
        methods: [],
        reachability: [],
        statuses: [],
        loading: true,
        failed: false,
    })
    useEffect(() => {
        if (!active) return undefined
        let alive = true
        Promise.allSettled([lookups.contactMethods(), lookups.reachability(), lookups.commentStatuses()]).then(
            ([m, r, s]) => {
                if (!alive) return
                setState({
                    methods: m.status === 'fulfilled' ? m.value : [],
                    reachability: r.status === 'fulfilled' ? r.value : [],
                    statuses: s.status === 'fulfilled' ? s.value : [],
                    loading: false,
                    failed: [m, r, s].some((x) => x.status === 'rejected'),
                })
            }
        )
        return () => {
            alive = false
        }
    }, [active])
    return state
}

const selectProps = { size: 'sm' as const, menuPosition: 'fixed' as const, isClearable: true }

const CurrentDue = ({ fu }: { fu: FollowUpRef }) => {
    const { t } = useTranslation()
    return (
        <>
            <span>
                <span className="text-gray-600">{t('p1.followUps.currentDue')}: </span>
                <RiyadhTime iso={fu.due_at} className="font-extrabold" />
            </span>
            {fu.bucket && <BucketBadge bucket={fu.bucket} daysLate={fu.days_late} size="row" />}
            {fu.next_action && (
                <span className="min-w-0 break-words">
                    <span className="text-gray-600">{t('p1.col.nextAction')}: </span>
                    <bdi>{fu.next_action}</bdi>
                </span>
            )}
        </>
    )
}

/* ------------------------------------------------------------------ complete */

export type CompleteFollowUpDialogProps = {
    isOpen: boolean
    followUp: FollowUpRef | null
    /** The case's contract, to filter note statuses like the legacy note form. Falls back to `followUp.case.contract.id`. */
    contractId?: string | null
    onClose: () => void
    /** After a successful completion (reload lists, notify the legacy screen). */
    onDone: (result: { completed: FollowUp; next: FollowUp | null; outcome_comment_id: string }) => void
    /** After a 409 the user asked to reload the follow-up. */
    onReload?: () => void
}

type Mode = 'next' | 'none'

export const CompleteFollowUpDialog = ({
    isOpen,
    followUp,
    contractId,
    onClose,
    onDone,
    onReload,
}: CompleteFollowUpDialogProps) => {
    const { t } = useTranslation()
    const lk = useNoteLookups(isOpen)
    const [text, setText] = useState('')
    const [reach, setReach] = useState<string | undefined>(undefined)
    const [status, setStatus] = useState<string | undefined>(undefined)
    const [subStatus, setSubStatus] = useState<string | undefined>(undefined)
    const [method, setMethod] = useState<string | undefined>(undefined)
    const [mode, setMode] = useState<Mode>('next')
    const [date, setDate] = useState('')
    const [time, setTime] = useState('09:00')
    const [nextAction, setNextAction] = useState('')
    const [nfa, setNfa] = useState('')

    const contract = contractId ?? followUp?.case?.contract?.id ?? null

    useEffect(() => {
        if (!method && lk.methods.some((m) => m.value === MOBILE_METHOD_ID)) setMethod(MOBILE_METHOD_ID)
    }, [lk.methods, method])

    /* The legacy note form's rule (the case's contract and the chosen reachability), plus 0068:
       general statuses fit every contract, sub-statuses have their own select, deleted ones never show. */
    const statusOptions = useMemo(
        () => mainStatusesFor(lk.statuses, contract).filter((s) => s.reachability_id === (reach ?? null)),
        [lk.statuses, contract, reach]
    )
    const subOptions = useMemo(() => subStatusesOf(lk.statuses, status), [lk.statuses, status])

    useEffect(() => {
        if (status && !statusOptions.some((s) => s.value === status)) setStatus(undefined)
    }, [statusOptions, status])

    useEffect(() => {
        if (subStatus && !subOptions.some((s) => s.value === subStatus)) setSubStatus(undefined)
    }, [subOptions, subStatus])

    const reset = () => {
        setText('')
        setReach(undefined)
        setStatus(undefined)
        setSubStatus(undefined)
        setMode('next')
        setDate('')
        setTime('09:00')
        setNextAction('')
        setNfa('')
    }

    const noteOk = text.trim() !== '' && !!status && !!method
    const nextOk = mode === 'next' ? validDue(date, time) && nextAction.trim() !== '' : nfa.trim() !== ''

    const submit = async (key: string) => {
        if (!followUp) return
        const res = await worklistApi.complete(
            followUp.id,
            {
                expected_revision: followUp.revision,
                outcome_note: {
                    text: text.trim(),
                    comment_status_id: status as string,
                    ...(subStatus ? { comment_sub_status_id: subStatus } : {}),
                    contact_method_id: method as string,
                    ...(reach ? { comment_reachability_id: reach } : {}),
                },
                ...(mode === 'next'
                    ? { next: { due_at: riyadhInstant(date, time), next_action: nextAction.trim() } }
                    : { no_further_action_reason: nfa.trim() }),
            },
            key
        )
        reset()
        onDone(res)
    }

    return (
        <ActionDialog
            isOpen={isOpen && !!followUp}
            title={t('p1.followUps.complete.title')}
            confirmLabel={t('p1.followUps.complete.confirm')}
            canSubmit={noteOk && nextOk}
            width={620}
            onSubmit={submit}
            onClose={() => {
                reset()
                onClose()
            }}
            onReload={onReload}
            testId="p1-fu-complete"
        >
            {followUp && (
                <FollowUpSummary caseName={followUp.case?.name} accountNumber={followUp.case?.accountNumber}>
                    <CurrentDue fu={followUp} />
                </FollowUpSummary>
            )}

            <div className="text-eyebrow mb-3">{t('p1.followUps.complete.outcome')}</div>
            <DialogField
                label={t('p1.followUps.complete.noteText')}
                required
                hint={t('p1.followUps.complete.noteHint')}
            >
                <Input
                    textArea
                    value={text}
                    maxLength={5000}
                    onChange={(e) => setText(e.target.value)}
                    data-testid="p1-fu-complete-text"
                />
            </DialogField>
            <div className="grid grid-cols-1 gap-x-3 sm:grid-cols-3">
                <DialogField label={t('p1.followUps.complete.reachability')}>
                    <div data-testid="p1-fu-complete-reach">
                        <Select
                            {...selectProps}
                            placeholder={t('p1.followUps.select')}
                            isLoading={lk.loading}
                            options={lk.reachability}
                            value={lk.reachability.find((o) => o.value === reach) || null}
                            onChange={(o) => setReach((o as Option | null)?.value || undefined)}
                        />
                    </div>
                </DialogField>
                <DialogField
                    label={t('p1.followUps.complete.status')}
                    required
                    hint={!reach ? t('p1.followUps.complete.statusHint') : undefined}
                >
                    <div data-testid="p1-fu-complete-status">
                        <Select
                            {...selectProps}
                            placeholder={t('p1.followUps.select')}
                            isLoading={lk.loading}
                            options={statusOptions}
                            noOptionsMessage={() => t('p1.followUps.complete.noStatuses')}
                            value={statusOptions.find((o) => o.value === status) || null}
                            onChange={(o) => setStatus((o as Option | null)?.value || undefined)}
                        />
                    </div>
                </DialogField>
                <DialogField label={t('p1.followUps.complete.method')} required>
                    <div data-testid="p1-fu-complete-method">
                        <Select
                            {...selectProps}
                            placeholder={t('p1.followUps.select')}
                            isLoading={lk.loading}
                            options={lk.methods}
                            value={lk.methods.find((o) => o.value === method) || null}
                            onChange={(o) => setMethod((o as Option | null)?.value || undefined)}
                        />
                    </div>
                </DialogField>
            </div>
            {subOptions.length > 0 && (
                <DialogField label={t('p7.status.subStatus')} hint={t('p7.status.subStatusHint')}>
                    <div data-testid="p1-fu-complete-sub-status">
                        <Select
                            {...selectProps}
                            placeholder={t('p1.followUps.select')}
                            options={subOptions}
                            value={subOptions.find((o) => o.value === subStatus) || null}
                            onChange={(o) => setSubStatus((o as Option | null)?.value || undefined)}
                        />
                    </div>
                </DialogField>
            )}
            {lk.failed && <Note className="-mt-2 mb-3 text-danger">{t('p1.followUps.lookupsFailed')}</Note>}

            <div className="text-eyebrow mb-2 mt-2">{t('p1.followUps.complete.then')}</div>
            <div className="mb-4">
                <Segmented<Mode>
                    value={mode}
                    onChange={setMode}
                    label={String(t('p1.followUps.complete.then'))}
                    testId="p1-fu-complete-mode"
                    options={[
                        { value: 'next', label: t('p1.followUps.complete.modeNext') },
                        { value: 'none', label: t('p1.followUps.complete.modeNone') },
                    ]}
                />
            </div>
            {mode === 'next' ? (
                <>
                    <DueFields
                        date={date}
                        time={time}
                        onDate={setDate}
                        onTime={setTime}
                        testId="p1-fu-complete-next"
                        dateLabel={t('p1.followUps.complete.nextDate')}
                    />
                    <DialogField label={t('p1.col.nextAction')} required>
                        <Input
                            value={nextAction}
                            maxLength={1000}
                            onChange={(e) => setNextAction(e.target.value)}
                            data-testid="p1-fu-complete-next-action"
                        />
                    </DialogField>
                    <Note>{t('p1.followUps.complete.nextNote')}</Note>
                </>
            ) : (
                <DialogField label={t('p1.followUps.complete.nfaReason')} required hint={t('p1.hint.reasonRequired')}>
                    <Input
                        textArea
                        value={nfa}
                        maxLength={1000}
                        onChange={(e) => setNfa(e.target.value)}
                        data-testid="p1-fu-complete-nfa"
                    />
                </DialogField>
            )}
            {followUp?.bucket === 'late' && <Note className="mt-2">{t('p1.followUps.complete.lateNote')}</Note>}
        </ActionDialog>
    )
}

/* ---------------------------------------------------------------- reschedule */

export type RescheduleFollowUpDialogProps = {
    isOpen: boolean
    followUp: FollowUpRef | null
    onClose: () => void
    onDone: (followUp: FollowUp) => void
    onReload?: () => void
}

export const RescheduleFollowUpDialog = ({
    isOpen,
    followUp,
    onClose,
    onDone,
    onReload,
}: RescheduleFollowUpDialogProps) => {
    const { t } = useTranslation()
    const [date, setDate] = useState('')
    const [time, setTime] = useState('09:00')
    const [reason, setReason] = useState('')
    const [nextAction, setNextAction] = useState('')

    useEffect(() => {
        if (isOpen) setNextAction(followUp?.next_action || '')
    }, [isOpen, followUp?.id, followUp?.next_action])

    const reset = () => {
        setDate('')
        setTime('09:00')
        setReason('')
    }

    const submit = async (key: string) => {
        if (!followUp) return
        const na = nextAction.trim()
        const res = await worklistApi.reschedule(
            followUp.id,
            {
                expected_revision: followUp.revision,
                due_at: riyadhInstant(date, time),
                reason: reason.trim(),
                ...(na && na !== (followUp.next_action || '') ? { next_action: na } : {}),
            },
            key
        )
        reset()
        onDone(res)
    }

    return (
        <ActionDialog
            isOpen={isOpen && !!followUp}
            title={t('p1.followUps.reschedule.title')}
            confirmLabel={t('p1.followUps.reschedule.confirm')}
            canSubmit={validDue(date, time) && reason.trim() !== ''}
            onSubmit={submit}
            onClose={() => {
                reset()
                onClose()
            }}
            onReload={onReload}
            testId="p1-fu-reschedule"
        >
            {followUp && (
                <FollowUpSummary caseName={followUp.case?.name} accountNumber={followUp.case?.accountNumber}>
                    <CurrentDue fu={followUp} />
                </FollowUpSummary>
            )}
            <DueFields
                date={date}
                time={time}
                onDate={setDate}
                onTime={setTime}
                testId="p1-fu-reschedule"
                dateLabel={t('p1.followUps.reschedule.newDate')}
            />
            <DialogField label={t('p1.fields.reason')} required hint={t('p1.hint.reasonRequired')}>
                <Input
                    textArea
                    value={reason}
                    maxLength={1000}
                    onChange={(e) => setReason(e.target.value)}
                    data-testid="p1-fu-reschedule-reason"
                />
            </DialogField>
            <DialogField label={t('p1.col.nextAction')} hint={t('p1.followUps.reschedule.nextActionHint')}>
                <Input
                    value={nextAction}
                    maxLength={1000}
                    onChange={(e) => setNextAction(e.target.value)}
                    data-testid="p1-fu-reschedule-next-action"
                />
            </DialogField>
            {followUp?.bucket === 'late' && <Note>{t('p1.followUps.reschedule.lateNote')}</Note>}
        </ActionDialog>
    )
}

/* -------------------------------------------------------------------- create */

type ExistingHint = { follow_up_id: string; revision: number; due_at: string }

export type CreateFollowUpDialogProps = {
    isOpen: boolean
    caseId: string
    /** Shown at the top of the dialog. */
    caseName?: string | null
    accountNumber?: string | null
    onClose: () => void
    /** After a follow-up was created, or after the existing one was rescheduled from the 409 offer. */
    onDone: (followUp: FollowUp) => void
}

/**
 * Keep this mounted (toggle `isOpen`): after a 409 `open_follow_up_exists` it
 * offers to reschedule the existing follow-up in its own reschedule dialog.
 */
export const CreateFollowUpDialog = ({
    isOpen,
    caseId,
    caseName,
    accountNumber,
    onClose,
    onDone,
}: CreateFollowUpDialogProps) => {
    const { t } = useTranslation()
    const [date, setDate] = useState('')
    const [time, setTime] = useState('09:00')
    const [nextAction, setNextAction] = useState('')
    const [existing, setExisting] = useState<ExistingHint | null>(null)
    const [rescheduling, setRescheduling] = useState<ExistingHint | null>(null)

    const reset = () => {
        setDate('')
        setTime('09:00')
        setNextAction('')
        setExisting(null)
    }

    const submit = async (key: string) => {
        try {
            const res = await worklistApi.create(
                caseId,
                { due_at: riyadhInstant(date, time), next_action: nextAction.trim() },
                key
            )
            reset()
            onDone(res)
        } catch (err) {
            if (isV2Error(err) && err.code === 'open_follow_up_exists' && err.detail) {
                setExisting(err.detail as ExistingHint)
            }
            throw err
        }
    }

    return (
        <>
            <ActionDialog
                isOpen={isOpen && !rescheduling}
                title={t('p1.followUps.create.title')}
                confirmLabel={t('p1.followUps.create.confirm')}
                canSubmit={validDue(date, time) && nextAction.trim() !== ''}
                onSubmit={submit}
                onClose={() => {
                    reset()
                    onClose()
                }}
                testId="p1-fu-create"
            >
                {caseName && <FollowUpSummary caseName={caseName} accountNumber={accountNumber} />}
                <DueFields date={date} time={time} onDate={setDate} onTime={setTime} testId="p1-fu-create" />
                <DialogField label={t('p1.col.nextAction')} required>
                    <Input
                        value={nextAction}
                        maxLength={1000}
                        onChange={(e) => setNextAction(e.target.value)}
                        data-testid="p1-fu-create-next-action"
                    />
                </DialogField>
                {existing && (
                    <div
                        className="mt-1 flex flex-wrap items-center gap-2 rounded-lg border border-gray-300 px-3 py-2 text-[13px] dark:border-night-border"
                        data-testid="p1-fu-create-existing"
                    >
                        <span>
                            {t('p1.followUps.create.existing')}{' '}
                            <RiyadhTime iso={existing.due_at} className="font-extrabold" />
                        </span>
                        <Button
                            size="sm"
                            variant="plain"
                            data-testid="p1-fu-create-reschedule-existing"
                            onClick={() => setRescheduling(existing)}
                        >
                            {t('p1.followUps.create.rescheduleExisting')}
                        </Button>
                    </div>
                )}
            </ActionDialog>
            <RescheduleFollowUpDialog
                isOpen={!!rescheduling}
                followUp={
                    rescheduling
                        ? {
                              id: rescheduling.follow_up_id,
                              revision: rescheduling.revision,
                              due_at: rescheduling.due_at,
                          }
                        : null
                }
                onClose={() => {
                    setRescheduling(null)
                    reset()
                    onClose()
                }}
                onDone={(fu) => {
                    setRescheduling(null)
                    reset()
                    onDone(fu)
                }}
            />
        </>
    )
}
