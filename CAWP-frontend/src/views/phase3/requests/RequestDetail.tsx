/**
 * One internal request: facts, body, attachments, the immutable event trail
 * (comments are events too), a comment box, and the transitions the API says
 * the caller may perform (`actions`). Used by the /requests side panel and the
 * case Requests tab.
 *
 * Transitions send the revision this view loaded. A 409 (`revision_conflict`
 * or `invalid_transition`: someone else took, resolved or cancelled it) keeps
 * the dialog open with "Changed by someone else" and a reload.
 */
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import Button from '@/components/ui/Button'
import Checkbox from '@/components/ui/Checkbox'
import Input from '@/components/ui/Input'
import ToastNotification from '@/components/shared/myComponents/ToastNotification'
import ActionDialog, { DialogField, ReasonDialog } from '@/components/v2/ActionDialog'
import { CivilDate, Field, Ltr, MoneyText, RiyadhTime } from '@/components/v2/Display'
import { PlainBadge } from '@/components/v2/Badges'
import { InlineError, Note } from '@/components/v2/States'
import { describeP3Error, requestsApi } from '@/services/v2'
import type { RequestDetail as RequestDetailType, RequestEvent, RequestTransition, RequestViewer } from '@/services/v2'
import { useActionKey } from '@/utils/v2/hooks'
import { caseLink } from '@/utils/v2/links'
import { BlockTitle, MoneyTimeline } from '@/views/phase1/money/Timeline'
import type { TimelineEntry, TimelineTone } from '@/views/phase1/money/Timeline'
import { DepartmentLabel, RequestStatusBadge } from './parts'
import { docLabel, openDocument, useCaseDocuments } from './documents'

const EVENT_TONE: Record<RequestEvent['event'], TimelineTone> = {
    created: 'info',
    commented: 'neutral',
    taken: 'warning',
    resolved: 'success',
    reopened: 'warning',
    cancelled: 'neutral',
}

type Props = {
    request: RequestDetailType
    /** From the list response (`viewer`), when known: explains why an action is missing. */
    viewer?: RequestViewer | null
    onReload: () => void
    /** The API's new version of the request after a comment or transition. */
    onChanged: (updated: RequestDetailType) => void
    showCaseLink?: boolean
}

const TakeDialog = ({
    isOpen,
    onClose,
    onReload,
    onSubmit,
}: {
    isOpen: boolean
    onClose: () => void
    onReload: () => void
    onSubmit: (reason: string, key: string) => Promise<void>
}) => {
    const { t } = useTranslation()
    const [reason, setReason] = useState('')
    return (
        <ActionDialog
            isOpen={isOpen}
            title={t('p3.req.takeTitle')}
            confirmLabel={t('p3.req.action.take')}
            testId="p3-req-take-dialog"
            describeError={describeP3Error}
            onReload={onReload}
            onClose={() => {
                setReason('')
                onClose()
            }}
            onSubmit={async (key) => {
                await onSubmit(reason.trim(), key)
                setReason('')
            }}
        >
            <div className="mb-4 text-[13.5px] text-gray-700 dark:text-night-body">{t('p3.req.takeHint')}</div>
            <DialogField label={t('p3.req.takeNote')}>
                <Input
                    textArea
                    value={reason}
                    maxLength={2000}
                    onChange={(e) => setReason(e.target.value)}
                    data-testid="p3-req-take-dialog-reason"
                />
            </DialogField>
        </ActionDialog>
    )
}

const CommentBox = ({
    request,
    onChanged,
    onReload,
}: {
    request: RequestDetailType
    onChanged: (r: RequestDetailType) => void
    onReload: () => void
}) => {
    const { t } = useTranslation()
    const { key, renew } = useActionKey(true)
    const [text, setText] = useState('')
    const [docIds, setDocIds] = useState<string[]>([])
    const [showDocs, setShowDocs] = useState(false)
    const [busy, setBusy] = useState(false)
    const [error, setError] = useState<unknown>(null)
    const docs = useCaseDocuments(request.case_id, showDocs && !request.case.missing)
    const attached = new Set(request.attachments.map((a) => a.document_id))
    const body = text.trim()

    const send = async () => {
        if (!body || busy) return
        setBusy(true)
        setError(null)
        try {
            const updated = await requestsApi.comment(
                request.id,
                docIds.length ? { body, document_ids: docIds } : { body },
                key
            )
            setText('')
            setDocIds([])
            setShowDocs(false)
            renew()
            onChanged(updated)
        } catch (err) {
            setError(err)
        } finally {
            setBusy(false)
        }
    }

    return (
        <div className="mt-3" data-testid="p3-req-comment-box">
            <Input
                textArea
                value={text}
                maxLength={5000}
                placeholder={String(t('p3.req.commentPlaceholder'))}
                onChange={(e) => setText(e.target.value)}
                data-testid="p3-req-comment"
            />
            {showDocs && (
                <div className="mt-2 rounded-md border border-gray-300 px-3 py-2 dark:border-night-border">
                    {docs.loading ? (
                        <div className="text-[12.5px] text-gray-600">{t('p1.state.loading')}</div>
                    ) : !docs.available ? (
                        <div className="text-[12.5px] text-gray-600">{t('p3.req.docsUnavailable')}</div>
                    ) : docs.docs.length === 0 ? (
                        <div className="text-[12.5px] text-gray-600">{t('p3.req.noDocs')}</div>
                    ) : (
                        docs.docs.map((d) => (
                            <div key={d.id} className="py-1 text-[13px]">
                                <Checkbox
                                    checked={docIds.includes(d.id) || attached.has(d.id)}
                                    disabled={attached.has(d.id)}
                                    onChange={(v: boolean) =>
                                        setDocIds((ids) => (v ? [...ids, d.id] : ids.filter((x) => x !== d.id)))
                                    }
                                    data-testid="p3-req-comment-doc"
                                    data-doc={d.id}
                                >
                                    <span dir="auto">{docLabel(d)}</span>
                                    {attached.has(d.id) && (
                                        <span className="ms-2 text-[12px] text-gray-600">({t('p3.req.alreadyAttached')})</span>
                                    )}
                                </Checkbox>
                            </div>
                        ))
                    )}
                </div>
            )}
            <InlineError error={error} describe={describeP3Error} onReload={onReload} />
            <div className="mt-2 flex flex-wrap items-center justify-end gap-2">
                {!request.case.missing && (
                    <Button size="sm" variant="plain" onClick={() => setShowDocs((v) => !v)}>
                        {showDocs ? t('p3.req.hideDocs') : t('p3.req.attachDocs')}
                    </Button>
                )}
                <Button
                    size="sm"
                    variant="solid"
                    disabled={!body}
                    aria-disabled={!body || busy}
                    loading={busy}
                    onClick={send}
                    data-testid="p3-req-comment-send"
                >
                    {t('p3.req.sendComment')}
                </Button>
            </div>
        </div>
    )
}

const RequestDetail = ({ request, viewer, onReload, onChanged, showCaseLink = true }: Props) => {
    const { t } = useTranslation()
    const [dialog, setDialog] = useState<RequestTransition | null>(null)
    const a = request.actions
    const isRequester = !!viewer && request.requested_by?.id === viewer.user_id
    const reviewsDept = !!viewer && viewer.reviewer_departments.includes(request.department)

    const run = async (action: RequestTransition, reason: string, key: string) => {
        const body: { expected_revision: number; reason?: string } = { expected_revision: request.revision }
        if (reason) body.reason = reason
        const updated = await requestsApi.transition(request.id, action, body, key)
        ToastNotification(t(`p3.req.done.${action}`), t('p3.req.title'), 'success')
        onChanged(updated)
    }

    const entries: TimelineEntry[] = request.events.map((e) => ({
        key: String(e.id),
        tone: EVENT_TONE[e.event] || 'neutral',
        at: e.occurred_at,
        actor: e.actor?.name || null,
        title: (
            <span data-testid="p3-req-event" data-event={e.event}>
                {t(`p3.req.event.${e.event}`)}
                {e.status_before && e.status_before !== e.status_after && (
                    <span className="ms-2 text-[12px] font-normal text-gray-600">
                        {t(`p3.req.status.${e.status_before}`)} → {t(`p3.req.status.${e.status_after}`)}
                    </span>
                )}
            </span>
        ),
        children:
            e.reason || e.body || e.details?.document_ids?.length ? (
                <div className="flex flex-col gap-0.5">
                    {e.reason && (
                        <div>
                            <span className="text-gray-600">{t('p1.fields.reason')}: </span>
                            <span dir="auto" className="whitespace-pre-wrap">
                                {e.reason}
                            </span>
                        </div>
                    )}
                    {e.body && (
                        <div dir="auto" className="whitespace-pre-wrap">
                            {e.body}
                        </div>
                    )}
                    {!!e.details?.document_ids?.length && (
                        <div className="text-gray-600">
                            {t('p3.req.attachedN', { count: e.details.document_ids.length })}
                        </div>
                    )}
                </div>
            ) : undefined,
    }))

    return (
        <div data-testid="p3-req-detail" data-status={request.status} data-revision={request.revision} data-request={request.id}>
            <div className="mb-3 flex flex-wrap items-center gap-2">
                <RequestStatusBadge status={request.status} size="md" />
                <DepartmentLabel department={request.department} />
                {request.reopen_count > 0 && (
                    <PlainBadge tone="warning" size="md">
                        {t('p3.req.reopenedN', { count: request.reopen_count })}
                    </PlainBadge>
                )}
            </div>
            <h4 dir="auto" className="mb-1 text-[17px] font-extrabold text-navy-600 dark:text-gray-100">
                {request.subject}
            </h4>
            <div dir="auto" className="mb-4 whitespace-pre-wrap text-[14px] text-gray-700 dark:text-night-body">
                {request.body}
            </div>

            <div className="grid grid-cols-2 gap-x-4 gap-y-3">
                <Field label={t('p3.req.col.case')} className="col-span-2">
                    {request.case.missing ? (
                        <span className="text-gray-600">{t('p3.req.caseMissing')}</span>
                    ) : showCaseLink ? (
                        <Link className="text-azure-600 hover:underline" to={caseLink(request.case_id, 'requests')}>
                            <span dir="auto">{request.case.name}</span> · <Ltr>{request.case.accountNumber}</Ltr>
                        </Link>
                    ) : (
                        <span>
                            <span dir="auto">{request.case.name}</span> · <Ltr>{request.case.accountNumber}</Ltr>
                        </span>
                    )}
                    {(request.case.is_archived || request.case.is_locked) && (
                        <span className="ms-2 inline-flex gap-1">
                            {request.case.is_archived && <PlainBadge size="row">{t('archived')}</PlainBadge>}
                            {request.case.is_locked && <PlainBadge size="row">{t('p3.req.locked')}</PlainBadge>}
                        </span>
                    )}
                </Field>
                <Field label={t('p3.req.col.requester')}>{request.requested_by?.name || '—'}</Field>
                <Field label={t('p3.req.col.created')}>
                    <RiyadhTime iso={request.created_at} />
                </Field>
                {request.amount && (
                    <Field label={t('p3.fields.amount')}>
                        <MoneyText value={request.amount} strong />
                    </Field>
                )}
                {request.related_date && (
                    <Field label={t('p3.fields.related_date')}>
                        <CivilDate value={request.related_date} />
                    </Field>
                )}
                <Field label={t('p3.req.col.reviewer')}>
                    {request.reviewer?.name || <span className="text-gray-600">{t('p3.req.notTaken')}</span>}
                </Field>
                {request.review_started_at && (
                    <Field label={t('p3.req.reviewStarted')}>
                        <RiyadhTime iso={request.review_started_at} />
                    </Field>
                )}
                {request.status === 'resolved' && (
                    <>
                        <Field label={t('p3.req.resolvedBy')}>
                            {request.resolved_by?.name || '—'} · <RiyadhTime iso={request.resolved_at} />
                        </Field>
                        <Field label={t('p3.req.resolution')} className="col-span-2">
                            <span dir="auto" className="whitespace-pre-wrap">
                                {request.resolution}
                            </span>
                        </Field>
                    </>
                )}
                {request.status === 'cancelled' && (
                    <>
                        <Field label={t('p3.req.cancelledBy')}>
                            {request.cancelled_by?.name || '—'} · <RiyadhTime iso={request.cancelled_at} />
                        </Field>
                        <Field label={t('p3.req.cancelReason')} className="col-span-2">
                            <span dir="auto" className="whitespace-pre-wrap">
                                {request.cancel_reason}
                            </span>
                        </Field>
                    </>
                )}
            </div>

            {(a.take || a.resolve || a.reopen || a.cancel) && (
                <div className="mt-4 flex flex-wrap gap-2" data-testid="p3-req-actions">
                    {a.take && (
                        <Button size="sm" variant="solid" onClick={() => setDialog('take')} data-testid="p3-req-take">
                            {t('p3.req.action.take')}
                        </Button>
                    )}
                    {a.resolve && (
                        <Button size="sm" variant="solid" onClick={() => setDialog('resolve')} data-testid="p3-req-resolve">
                            {t('p3.req.action.resolve')}
                        </Button>
                    )}
                    {a.reopen && (
                        <Button size="sm" onClick={() => setDialog('reopen')} data-testid="p3-req-reopen">
                            {t('p3.req.action.reopen')}
                        </Button>
                    )}
                    {a.cancel && (
                        <Button size="sm" variant="plain" onClick={() => setDialog('cancel')} data-testid="p3-req-cancel">
                            {t('p3.req.action.cancel')}
                        </Button>
                    )}
                </div>
            )}
            {request.status === 'open' && !a.take && isRequester && reviewsDept && (
                <Note className="mt-3">
                    <span data-testid="p3-req-own-note">{t('p3.req.ownRequestNote')}</span>
                </Note>
            )}

            <BlockTitle>{t('p3.req.attachments', { count: request.attachments.length })}</BlockTitle>
            {request.attachments.length === 0 ? (
                <div className="text-[13px] text-gray-600">{t('p3.req.noAttachments')}</div>
            ) : (
                <ul className="flex flex-col gap-1.5" data-testid="p3-req-attachments">
                    {request.attachments.map((att) => {
                        const d = att.document
                        const openable = !d.missing && !d.is_deleted && !!d.download_url
                        return (
                            <li key={att.document_id} className="flex flex-wrap items-center gap-2 text-[13px]" data-testid="p3-req-attachment" data-doc={att.document_id}>
                                {openable ? (
                                    <button
                                        type="button"
                                        className="text-azure-600 hover:underline"
                                        onClick={() =>
                                            openDocument(d.id, t('p3.req.title'), t('p3.req.openDocFailed'))
                                        }
                                    >
                                        <span dir="auto">{docLabel({ ...d, id: att.document_id })}</span>
                                    </button>
                                ) : (
                                    <span dir="auto" className="text-gray-600">
                                        {docLabel({ ...d, id: att.document_id })}
                                    </span>
                                )}
                                {d.file_type && <span className="text-[12px] uppercase text-gray-600">{d.file_type}</span>}
                                {d.missing && <PlainBadge size="row">{t('p3.req.docMissing')}</PlainBadge>}
                                {d.is_deleted && <PlainBadge size="row">{t('p3.req.docDeleted')}</PlainBadge>}
                                <span className="text-[12px] text-gray-600">
                                    · {att.added_by?.name} · <RiyadhTime iso={att.added_at} />
                                </span>
                            </li>
                        )
                    })}
                </ul>
            )}

            <BlockTitle>{t('p3.req.trail')}</BlockTitle>
            <MoneyTimeline entries={entries} testId="p3-req-trail" />
            <Note className="mt-2">{t('p3.req.trailNote')}</Note>

            {a.comment && <CommentBox request={request} onChanged={onChanged} onReload={onReload} />}

            <TakeDialog
                isOpen={dialog === 'take'}
                onClose={() => setDialog(null)}
                onReload={onReload}
                onSubmit={(reason, key) => run('take', reason, key)}
            />
            {(['resolve', 'reopen', 'cancel'] as const).map((action) => (
                <ReasonDialog
                    key={action}
                    isOpen={dialog === action}
                    title={t(`p3.req.dialog.${action}Title`)}
                    description={t(`p3.req.dialog.${action}Hint`)}
                    reasonLabel={t(`p3.req.dialog.${action}Reason`)}
                    confirmLabel={t(`p3.req.action.${action}`)}
                    tone={action === 'cancel' ? 'danger' : 'primary'}
                    testId={`p3-req-${action}-dialog`}
                    describeError={describeP3Error}
                    onReload={onReload}
                    onClose={() => setDialog(null)}
                    onSubmit={(reason, key) => run(action, reason, key)}
                />
            ))}
        </div>
    )
}

export default RequestDetail
