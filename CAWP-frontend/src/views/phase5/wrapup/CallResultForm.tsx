/**
 * "نتيجة المكالمة" — the client page's note tab while one of this case's calls waits for its
 * wrap-up (customer decision 2026-10-17: ONE entry). The collector enters the outcome and the
 * note here, once; Save posts `POST /v2/calls/<id>/wrapup`, which writes the case note, closes
 * the call and releases the reservation. No wrap-up dialog opens while this page is open.
 *
 * After the save (`CallResultDone`): call the next callable number of the same client (the
 * contacts panel's callability rules; the API still decides), go to the previous / next client
 * of the list, or write another note.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { PhoneOutgoing } from 'lucide-react'
import Button from '@/components/ui/Button'
import Card from '@/components/ui/Card'
import ToastNotification from '@/components/shared/myComponents/ToastNotification'
import { InlineError, SkeletonLines } from '@/components/v2/States'
import { Ltr } from '@/components/v2/Display'
import { callsApi, describeP5Error, fetchLiveCall, historyApi, isV2Error, newIdempotencyKey } from '@/services/v2'
import type { CallDetail, Contact, LiveCall, Translate } from '@/services/v2'
import { useActionKey, usePermissions } from '@/utils/v2/hooks'
import type { CaseListNav } from '@/utils/v2/caseListNav'
import CaseListNavButtons from '@/views/clients/detail/components/CaseListNavButtons'
import { CallStateBadge, DurationText, Notice } from '../parts'
import { callDock, dialedOf } from '../live/callDock'
import type { DialedNumber } from '../live/callDock'
import { useCallability } from '../case/callability'
import type { CaseFacts } from './caseFacts'
import { WrapupFields, useWrapupForm } from './WrapupForm'

const describe = (e: unknown, t: Translate) => describeP5Error(e, t)

export const CallResultForm = ({
    attemptId,
    facts,
    onSaved,
}: {
    attemptId: string
    /** The case as the client page loaded it (contract, lock, archive). */
    facts: CaseFacts
    onSaved: (res: CallDetail, dialed: DialedNumber) => void
}) => {
    const { t } = useTranslation()
    const [view, setView] = useState<LiveCall | null>(null)
    const [loadError, setLoadError] = useState<unknown>(null)
    const [tick, setTick] = useState(0)
    const [busy, setBusy] = useState(false)
    const [error, setError] = useState<unknown>(null)
    const { key, renew } = useActionKey(true)
    const form = useWrapupForm(view, facts)

    /* A fresh live view: the revision and the outcomes the API allows now. */
    useEffect(() => {
        let alive = true
        setLoadError(null)
        fetchLiveCall(attemptId, null)
            .then((v) => alive && v && setView(v))
            .catch((e) => alive && setLoadError(e))
        return () => {
            alive = false
        }
    }, [attemptId, tick])

    const reload = () => {
        setError(null)
        setTick((n) => n + 1)
    }

    const save = async () => {
        if (busy || !form.canSubmit || !view) return
        setBusy(true)
        setError(null)
        try {
            const res = await form.submit(key)
            renew()
            if (res) onSaved(res, dialedOf(view))
        } catch (e) {
            setError(e)
        } finally {
            setBusy(false)
        }
    }

    const number = view ? dialedOf(view).value : null
    return (
        <Card>
            <div data-testid="p8-call-result" data-attempt={attemptId}>
                <h5 className="mb-3 text-[17px] font-extrabold text-navy-600 dark:text-gray-100">{t('p8.clientpage.resultTitle')}</h5>
                {loadError ? (
                    <Notice tone="danger">{describeP5Error(loadError, t)}</Notice>
                ) : !view ? (
                    <SkeletonLines rows={5} />
                ) : (
                    <>
                        <div
                            className="mb-4 grid grid-cols-1 gap-3 rounded-lg border border-azure-100 bg-info-tint px-4 py-3 text-[13px] dark:border-night-border dark:bg-night-raised sm:grid-cols-3"
                            data-testid="p8-call-result-banner"
                        >
                            <div>
                                <div className="text-eyebrow mb-1">{t('p8.clientpage.number')}</div>
                                {number ? (
                                    <Ltr className="font-extrabold tabular-nums text-navy-600 dark:text-gray-100">{number}</Ltr>
                                ) : (
                                    <span className="text-gray-500">{t('p8.clientpage.numberHidden')}</span>
                                )}
                            </div>
                            <div>
                                <div className="text-eyebrow mb-1">{t('p8.clientpage.duration')}</div>
                                <DurationText seconds={view.duration_seconds} source={view.duration_source} className="font-extrabold" />
                            </div>
                            <div>
                                <div className="text-eyebrow mb-1">{t('p8.clientpage.callOutcome')}</div>
                                <div className="flex flex-wrap items-center gap-2">
                                    <CallStateBadge state={view.state} size="row" />
                                    <span className="text-[12px] text-gray-600" data-evidenced={view.conversation.evidenced ? '1' : '0'}>
                                        {view.conversation.evidenced ? t('p8.clientpage.answered') : t('p8.clientpage.notAnswered')}
                                    </span>
                                </div>
                            </div>
                        </div>
                        <div className="mb-3 text-[12.5px] text-gray-600">{t('p8.clientpage.resultIntro')}</div>
                        <WrapupFields form={form} view={view} facts={facts} active testId="p8-result" />
                        <InlineError error={error} describe={describe} onReload={isV2Error(error) && error.isConflict ? reload : undefined} />
                        <div className="mt-4 flex flex-wrap items-center justify-end gap-2">
                            {!view.actions.can_wrapup && (
                                <Button size="sm" type="button" onClick={() => callDock.resultDone(attemptId)} data-testid="p8-call-result-dismiss">
                                    {t('p8.clientpage.backToNote')}
                                </Button>
                            )}
                            <Button
                                size="sm"
                                variant="solid"
                                type="button"
                                loading={busy}
                                disabled={!form.canSubmit}
                                onClick={save}
                                data-testid="p8-call-result-save"
                            >
                                {t('p8.clientpage.saveResult')}
                            </Button>
                        </div>
                    </>
                )}
            </div>
        </Card>
    )
}

/** The next callable number of the case after `dialed` (in the contacts panel's order, wrapping round). */
function nextCallable(contacts: Contact[], dialed: DialedNumber, callable: (c: Contact) => boolean): Contact | null {
    const phones = contacts.filter((c) => c.contact_type?.name !== 'email')
    const at = phones.findIndex((c) => !!dialed.contactId && (c.id === dialed.contactId || c.row_ids.includes(dialed.contactId)))
    const order = at < 0 ? phones : [...phones.slice(at + 1), ...phones.slice(0, at)]
    return order.find((c) => callable(c)) || null
}

export const CallResultDone = ({
    caseId,
    facts,
    dialed,
    nav,
    onNewNote,
}: {
    caseId: string
    facts: CaseFacts
    dialed: DialedNumber
    nav: CaseListNav
    onNewNote: () => void
}) => {
    const { t } = useTranslation()
    const perms = usePermissions()
    const canCall = !!perms.calls_make
    const calling = useCallability(caseId, { isLocked: facts.isLocked, isArchived: facts.isArchived }, canCall)
    const [contacts, setContacts] = useState<Contact[] | null>(null)
    const [busy, setBusy] = useState(false)
    const [refusal, setRefusal] = useState<string | null>(null)

    /* Read the contacts again: a "wrong number" result has just blocked the dialed one. */
    useEffect(() => {
        if (!canCall || !facts.identityNumber) return undefined
        let alive = true
        historyApi
            .contacts(facts.identityNumber)
            .then((d) => alive && setContacts(d.contacts))
            .catch(() => alive && setContacts([]))
        return () => {
            alive = false
        }
    }, [canCall, facts.identityNumber])

    const { reasonFor } = calling
    const callable = useCallback((c: Contact) => reasonFor(c) === null, [reasonFor])
    const next = useMemo(
        () => (contacts && !calling.loading ? nextCallable(contacts, dialed, callable) : null),
        [contacts, calling.loading, dialed, callable]
    )

    const place = async () => {
        if (!next || busy) return
        setBusy(true)
        setRefusal(null)
        try {
            const view = await callsApi.place({ case_id: caseId, contact_id: next.id }, newIdempotencyKey())
            callDock.show(view.id, view)
            ToastNotification(String(t('p5.call.placed')), String(t('p5.call.action')), 'info')
            onNewNote()
        } catch (e) {
            setRefusal(describeP5Error(e, t, 'dispatch'))
            calling.reload()
        } finally {
            setBusy(false)
        }
    }

    return (
        <Card>
            <div data-testid="p8-call-result-done">
                <Notice tone="success" className="mb-4" title={t('p8.clientpage.savedTitle')}>
                    {t('p8.clientpage.savedBody')}
                </Notice>
                <div className="flex flex-wrap items-center gap-3">
                    {canCall && (
                        <Button
                            size="sm"
                            variant="solid"
                            icon={<PhoneOutgoing size={15} aria-hidden />}
                            loading={busy}
                            disabled={!next || calling.loading || contacts === null}
                            onClick={place}
                            data-testid="p8-call-next-number"
                            data-contact={next?.id || ''}
                        >
                            {t('p8.clientpage.callNext')}
                            {next && (
                                <Ltr className="ms-1.5 tabular-nums">{next.contact}</Ltr>
                            )}
                        </Button>
                    )}
                    <CaseListNavButtons nav={nav} tab="details" labels testId="p8-result-case-nav" />
                    <Button size="sm" type="button" onClick={onNewNote} data-testid="p8-call-result-new-note">
                        {t('p8.clientpage.newNote')}
                    </Button>
                </div>
                {canCall && contacts !== null && !calling.loading && !next && (
                    <div className="mt-2 text-[12px] text-gray-600" data-testid="p8-no-next-number">
                        {t('p8.clientpage.noNextNumber')}
                    </div>
                )}
                {refusal && (
                    <div className="mt-2 text-[12px] text-warning" role="status" data-testid="p8-next-number-refused">
                        {t('p5.call.cannotCall')} {refusal}
                    </div>
                )}
            </div>
        </Card>
    )
}
