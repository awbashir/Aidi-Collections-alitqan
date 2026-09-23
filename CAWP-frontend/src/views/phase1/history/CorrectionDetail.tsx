/**
 * One case correction: before → after per field with the live current value,
 * the stale / locked / debt-floor / identity warnings, who asked and who
 * decided, and the review actions.
 *
 * - Approve (optional note) and Reject (note required) need
 *   `case_corrections_review` and are hidden on the reviewer's own request.
 *   The API refuses self-review too (403 own_request); that message is shown.
 * - Cancel is for the requester while pending (`case_corrections_request`).
 * - 409 stale_correction: the case changed since the request. The expected
 *   and current values are listed; the correction stays pending, so the
 *   reviewer can reload or reject it.
 */
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { HiOutlineExternalLink } from 'react-icons/hi'
import Button from '@/components/ui/Button'
import Input from '@/components/ui/Input'
import Table from '@/components/ui/Table'
import ToastNotification from '@/components/shared/myComponents/ToastNotification'
import { historyApi, isV2Error } from '@/services/v2'
import type { Correction, V2Error } from '@/services/v2'
import { usePermissions, useV2Query } from '@/utils/v2/hooks'
import { caseLink } from '@/utils/v2/links'
import { Field, Ltr, MoneyText, RiyadhTime } from '@/components/v2/Display'
import { CorrectionBadge, PlainBadge } from '@/components/v2/Badges'
import { ErrorState, SkeletonLines } from '@/components/v2/States'
import ActionDialog, { DialogField, ReasonDialog } from '@/components/v2/ActionDialog'
import { Banner, CorrectionValue, FieldLabel, useCurrentUserId, useDelinquencyReasons } from './parts'

const { THead, TBody, Tr, Th, Td } = Table

type StaleFields = Record<string, { expected: unknown; current: unknown }>

const staleFieldsOf = (err: unknown): StaleFields | null => {
    if (!isV2Error(err) || err.code !== 'stale_correction') return null
    const d = (err as V2Error).detail as { fields?: StaleFields } | null
    return d?.fields || {}
}

/** The review / cancel buttons and their dialogs. */
export const CorrectionActions = ({
    correction,
    onDone,
    onReload,
    size = 'sm',
}: {
    correction: Correction
    /** After a successful decision, with the updated correction. */
    onDone: (c: Correction) => void
    /** Reload the correction after a conflict. */
    onReload: () => void
    size?: 'sm' | 'xs'
}) => {
    const { t } = useTranslation()
    const perms = usePermissions()
    const me = useCurrentUserId()
    const [open, setOpen] = useState<'approve' | 'reject' | 'cancel' | null>(null)
    const [note, setNote] = useState('')
    const [cancelReason, setCancelReason] = useState('')
    const [stale, setStale] = useState<StaleFields | null>(null)
    const reasons = useDelinquencyReasons().options

    if (correction.status !== 'pending') return null
    const own = !!correction.requested_by && correction.requested_by.id === me
    const canReview = !!perms.case_corrections_review && !own
    const canCancel = !!perms.case_corrections_request && own

    const close = () => {
        setOpen(null)
        setNote('')
        setCancelReason('')
        setStale(null)
    }
    const done = (c: Correction, message: string) => {
        ToastNotification(message, t('p1.corr.title'), 'success')
        onDone(c)
    }

    if (!canReview && !canCancel) {
        return own && perms.case_corrections_review ? (
            <p className="text-[12.5px] text-gray-600" data-testid="p1-corr-own-note">
                {t('p1.corr.ownRequestNote')}
            </p>
        ) : null
    }

    return (
        <div className="flex flex-col gap-2">
            <div className="flex flex-wrap items-center gap-2">
                {canReview && (
                    <>
                        <Button
                            size={size}
                            variant="solid"
                            onClick={() => setOpen('approve')}
                            data-testid="p1-corr-approve"
                        >
                            {t('p1.corr.approve')}
                        </Button>
                        <Button size={size} onClick={() => setOpen('reject')} data-testid="p1-corr-reject">
                            {t('p1.corr.reject')}
                        </Button>
                    </>
                )}
                {canCancel && (
                    <Button size={size} variant="plain" onClick={() => setOpen('cancel')} data-testid="p1-corr-cancel">
                        {t('p1.corr.cancelRequest')}
                    </Button>
                )}
            </div>
            {own && perms.case_corrections_review && (
                <p className="text-[12.5px] text-gray-600" data-testid="p1-corr-own-note">
                    {t('p1.corr.ownRequestNote')}
                </p>
            )}

            <ActionDialog
                isOpen={open === 'approve'}
                title={t('p1.corr.approveTitle')}
                confirmLabel={t('p1.corr.approve')}
                testId="p1-corr-approve-dialog"
                onClose={close}
                onReload={onReload}
                onSubmit={async (key) => {
                    setStale(null)
                    try {
                        const c = await historyApi.approveCorrection(
                            correction.id,
                            note.trim() ? { review_note: note.trim() } : {},
                            key
                        )
                        done(c, t('p1.corr.approvedToast'))
                    } catch (err) {
                        setStale(staleFieldsOf(err))
                        throw err
                    }
                }}
            >
                <p className="mb-4 text-[13.5px] text-gray-700 dark:text-night-body">{t('p1.corr.approveHint')}</p>
                <DialogField label={t('p1.corr.reviewNoteOptional')}>
                    <Input
                        textArea
                        value={note}
                        maxLength={2000}
                        onChange={(e) => setNote(e.target.value)}
                        data-testid="p1-corr-approve-dialog-note"
                    />
                </DialogField>
                {stale && (
                    <div data-testid="p1-corr-stale-fields">
                        <Banner tone="warning" title={t('p1.corr.staleTitle')}>
                            {t('p1.corr.staleExplain')}
                        </Banner>
                        <Table compact className="mt-2">
                            <THead>
                                <Tr>
                                    <Th>{t('p1.col.field')}</Th>
                                    <Th>{t('p1.corr.expected')}</Th>
                                    <Th>{t('p1.col.current')}</Th>
                                </Tr>
                            </THead>
                            <TBody>
                                {Object.entries(stale).map(([field, v]) => (
                                    <Tr key={field}>
                                        <Td>
                                            <FieldLabel kind={correction.kind} field={field} />
                                        </Td>
                                        <Td>
                                            <CorrectionValue
                                                kind={correction.kind}
                                                field={field}
                                                value={v.expected}
                                                reasons={reasons}
                                            />
                                        </Td>
                                        <Td className="font-extrabold text-warning-700">
                                            <CorrectionValue
                                                kind={correction.kind}
                                                field={field}
                                                value={v.current}
                                                reasons={reasons}
                                            />
                                        </Td>
                                    </Tr>
                                ))}
                            </TBody>
                        </Table>
                        <div className="mt-2 flex flex-wrap gap-2">
                            <Button
                                size="xs"
                                onClick={() => {
                                    setStale(null)
                                    setOpen('reject')
                                }}
                                data-testid="p1-corr-stale-reject"
                            >
                                {t('p1.corr.rejectInstead')}
                            </Button>
                        </div>
                    </div>
                )}
            </ActionDialog>

            <ReasonDialog
                isOpen={open === 'reject'}
                title={t('p1.corr.rejectTitle')}
                description={t('p1.corr.rejectHint')}
                reasonLabel={t('p1.fields.review_note')}
                confirmLabel={t('p1.corr.reject')}
                tone="danger"
                testId="p1-corr-reject-dialog"
                onClose={close}
                onReload={onReload}
                onSubmit={async (reviewNote, key) => {
                    const c = await historyApi.rejectCorrection(correction.id, { review_note: reviewNote }, key)
                    done(c, t('p1.corr.rejectedToast'))
                }}
            />

            <ActionDialog
                isOpen={open === 'cancel'}
                title={t('p1.corr.cancelTitle')}
                confirmLabel={t('p1.corr.cancelRequest')}
                testId="p1-corr-cancel-dialog"
                onClose={close}
                onReload={onReload}
                onSubmit={async (key) => {
                    const c = await historyApi.cancelCorrection(
                        correction.id,
                        cancelReason.trim() ? { reason: cancelReason.trim() } : {},
                        key
                    )
                    done(c, t('p1.corr.cancelledToast'))
                }}
            >
                <p className="mb-4 text-[13.5px] text-gray-700 dark:text-night-body">{t('p1.corr.cancelHint')}</p>
                <DialogField label={t('p1.corr.cancelReasonOptional')}>
                    <Input
                        textArea
                        value={cancelReason}
                        maxLength={2000}
                        onChange={(e) => setCancelReason(e.target.value)}
                        data-testid="p1-corr-cancel-dialog-reason"
                    />
                </DialogField>
            </ActionDialog>
        </div>
    )
}

/** Before / after / current table of one correction. */
export const CorrectionDiff = ({ correction }: { correction: Correction }) => {
    const { t } = useTranslation()
    const reasons = useDelinquencyReasons().options
    const pending = correction.status === 'pending'
    const rows =
        correction.fields && correction.fields.length > 0
            ? correction.fields
            : Object.entries(correction.changes || {}).map(([field, c]) => ({
                  field,
                  before: c.before,
                  after: c.after,
                  current: undefined as unknown,
                  stale: false,
              }))
    const showCurrent = pending && !!correction.fields
    return (
        <div className="overflow-x-auto rounded-md border border-gray-300 dark:border-night-border">
            <Table compact data-testid="p1-corr-diff">
                <THead>
                    <Tr>
                        <Th>{t('p1.col.field')}</Th>
                        <Th>{t('p1.col.before')}</Th>
                        <Th>{t('p1.col.after')}</Th>
                        {showCurrent && <Th>{t('p1.col.current')}</Th>}
                    </Tr>
                </THead>
                <TBody>
                    {rows.map((r) => (
                        <Tr
                            key={r.field}
                            data-testid="p1-corr-diff-row"
                            data-field={r.field}
                            data-stale={r.stale ? 'true' : 'false'}
                            className={r.stale ? 'bg-warning-tint dark:bg-night-raised' : undefined}
                        >
                            <Td>
                                <div className="flex flex-wrap items-center gap-1.5">
                                    <FieldLabel kind={correction.kind} field={r.field} />
                                    {r.stale && (
                                        <PlainBadge tone="warning" size="row">
                                            {t('p1.corr.staleBadge')}
                                        </PlainBadge>
                                    )}
                                </div>
                            </Td>
                            <Td>
                                <CorrectionValue kind={correction.kind} field={r.field} value={r.before} reasons={reasons} />
                            </Td>
                            <Td className="font-extrabold text-navy-600 dark:text-gray-100">
                                {correction.kind === 'attribute' && r.after === null ? (
                                    <span className="italic text-danger">{t('p1.corr.removed')}</span>
                                ) : (
                                    <CorrectionValue kind={correction.kind} field={r.field} value={r.after} reasons={reasons} />
                                )}
                            </Td>
                            {showCurrent && (
                                <Td className={r.stale ? 'font-extrabold text-warning-700' : undefined}>
                                    <CorrectionValue
                                        kind={correction.kind}
                                        field={r.field}
                                        value={r.current}
                                        reasons={reasons}
                                    />
                                </Td>
                            )}
                        </Tr>
                    ))}
                </TBody>
            </Table>
        </div>
    )
}

/** Detail panel of one correction, loaded live (current values and `stale`). */
const CorrectionDetail = ({
    id,
    onChanged,
    showCaseLink = true,
}: {
    id: string
    /** After approve / reject / cancel, with the updated correction. */
    onChanged?: (c: Correction) => void
    showCaseLink?: boolean
}) => {
    const { t } = useTranslation()
    const query = useV2Query((signal) => historyApi.correction(id, signal), [id])
    const c = query.data

    if (query.error && !c) return <ErrorState error={query.error} onRetry={query.reload} />
    if (!c) return <SkeletonLines rows={6} className="p-2" />

    const warnings = c.warnings || []
    return (
        <div className="flex flex-col gap-4" data-testid="p1-corr-detail" data-status={c.status} data-id={c.id}>
            <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0">
                    <div className="text-eyebrow mb-1">{t(`p1.corrKind.${c.kind}`)}</div>
                    <div className="flex flex-wrap items-center gap-2">
                        <span className="text-[17px] font-extrabold text-navy-600 dark:text-gray-100">
                            {c.case?.name || '—'}
                        </span>
                        <CorrectionBadge status={c.status} size="md" />
                    </div>
                    {c.case && (
                        <div className="mt-1 flex flex-wrap gap-x-4 text-[12.5px] text-gray-600">
                            <span>
                                {t('p1.fields.accountNumber')} <Ltr>{c.case.account_number}</Ltr>
                            </span>
                            <span>
                                {t('p1.fields.identityNumber')} <Ltr>{c.case.identity_number}</Ltr>
                            </span>
                        </div>
                    )}
                </div>
                {showCaseLink && (
                    <Link
                        to={caseLink(c.case_id, 'corrections')}
                        className="inline-flex items-center gap-1 text-[13px] font-extrabold text-azure-600 hover:underline"
                        data-testid="p1-corr-case-link"
                    >
                        <HiOutlineExternalLink aria-hidden />
                        {t('p1.action.openCase')}
                    </Link>
                )}
            </div>

            {c.status === 'pending' && c.stale && (
                <Banner tone="warning" title={t('p1.corr.staleTitle')} testId="p1-corr-stale">
                    {t('p1.corr.staleLive')}
                </Banner>
            )}
            {c.status === 'pending' && c.case_locked && (
                <Banner tone="warning" testId="p1-corr-locked">
                    {t('p1.corr.caseLocked')}
                </Banner>
            )}
            {c.kind === 'original_debt' && c.status === 'pending' && c.approved_total !== undefined && (
                <Banner
                    tone={c.below_approved_payments ? 'danger' : 'info'}
                    testId="p1-corr-debt-floor"
                    title={c.below_approved_payments ? t('p1.corr.belowApprovedTitle') : undefined}
                >
                    {t('p1.corr.approvedTotal')} <MoneyText value={c.approved_total} strong />{' '}
                    {t('p1.currencyCode')}
                    {c.below_approved_payments ? ` · ${t('p1.corr.belowApprovedHint')}` : ''}
                </Banner>
            )}
            {warnings.map((w) => (
                <Banner key={w.code} tone="warning" testId="p1-corr-warning" title={t(`p1.corr.warning.${w.code}`, { defaultValue: w.code })}>
                    {w.code === 'contacts_follow_identity'
                        ? t('p1.corr.contactsFollowIdentity', {
                              before: w.contacts_before ?? 0,
                              after: w.contacts_after ?? 0,
                          })
                        : w.message}
                </Banner>
            ))}

            <CorrectionDiff correction={c} />

            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                <Field label={t('p1.col.reason')} className="sm:col-span-2">
                    <span dir="auto" className="whitespace-pre-wrap">
                        {c.reason}
                    </span>
                </Field>
                <Field label={t('p1.col.requestedBy')}>
                    <span>{c.requested_by?.name || '—'}</span>
                    <div className="text-[12.5px] text-gray-600">
                        <RiyadhTime iso={c.requested_at} />
                    </div>
                </Field>
                <Field label={t('p1.col.reviewedBy')}>
                    {c.reviewed_by ? (
                        <>
                            <span>{c.reviewed_by.name}</span>
                            <div className="text-[12.5px] text-gray-600">
                                <RiyadhTime iso={c.reviewed_at} />
                            </div>
                        </>
                    ) : (
                        <span className="text-gray-500">{t('p1.corr.notReviewed')}</span>
                    )}
                </Field>
                {c.review_note && (
                    <Field
                        label={c.status === 'cancelled' ? t('p1.corr.cancelReason') : t('p1.fields.review_note')}
                        className="sm:col-span-2"
                    >
                        <span dir="auto" className="whitespace-pre-wrap">
                            {c.review_note}
                        </span>
                    </Field>
                )}
                {c.applied_revision !== null && c.applied_revision !== undefined && (
                    <Field label={t('p1.corr.appliedRevision')}>
                        <Ltr>{c.applied_revision}</Ltr>
                    </Field>
                )}
            </div>

            <CorrectionActions
                correction={c}
                onReload={query.reload}
                onDone={(updated) => {
                    query.reload()
                    onChanged?.(updated)
                }}
            />
        </div>
    )
}

export default CorrectionDetail
