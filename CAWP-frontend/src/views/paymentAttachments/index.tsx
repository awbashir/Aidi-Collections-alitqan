/**
 * Payment receipts (attachments) UI, shared by the case payments tab, the approval desk, the
 * payment timeline and the legacy collections screens. API: services/v2/payAttApi.ts.
 *
 * - `ReceiptLinks`: the receipts of a payment as "view" links (and an optional remove).
 * - `ReceiptPicker`: optional file chooser with client-side type/size checks.
 * - `PaymentReceiptsDialog`: list, view, attach and remove for one payment.
 * - `LegacyReceiptButton`: a paperclip that opens the dialog read-only (legacy screens).
 *
 * Who may attach: clints_Payments / accounts_payments / payments_approve (the API also checks the
 * case scope). Removal: pending payments only, by the uploader or an accounts_payments holder.
 */
import { useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { HiOutlinePaperClip, HiOutlineTrash, HiOutlineX } from 'react-icons/hi'
import Button from '@/components/ui/Button'
import Dialog from '@/components/ui/Dialog'
import Notification from '@/components/ui/Notification'
import toast from '@/components/ui/toast'
import Tooltip from '@/components/ui/Tooltip'
import { InlineError, SkeletonLines } from '@/components/v2/States'
import { RiyadhTime } from '@/components/v2/Display'
import { describeV2Error, isV2Error, newIdempotencyKey } from '@/services/v2'
import type { PaymentAttachment, Translate } from '@/services/v2'
import {
    RECEIPT_ACCEPT,
    RECEIPT_MAX_PER_PAYMENT,
    openPaymentAttachment,
    paymentAttachmentsApi,
    receiptFileProblem,
} from '@/services/v2/payAttApi'
import { usePermissions, useV2Query } from '@/utils/v2/hooks'
import { useAppSelector } from '@/store'

/** Codes with their own `p7.payAtt.err.<code>` message; the rest go to the Phase 1 messages. */
const CODES = new Set([
    'type_mismatch',
    'unsupported_file_type',
    'file_too_large',
    'empty_file',
    'too_many_attachments',
    'payment_not_pending',
    'attachment_not_found',
    'file_missing',
])

export function describeReceiptError(err: unknown, t: Translate): string {
    if (isV2Error(err) && CODES.has(err.code)) return t(`p7.payAtt.err.${err.code}`)
    return describeV2Error(err, t)
}

/** True when the role may attach receipts (the API also checks the case scope). */
export function useCanAttachReceipts(): boolean {
    const perms = usePermissions()
    return !!(perms.clints_Payments || perms.accounts_payments || perms.payments_approve)
}

const notify = (title: string, type: 'success' | 'danger' | 'warning', body?: string) =>
    toast.push(
        <Notification title={title} type={type}>
            {body}
        </Notification>,
        { placement: 'top-center' }
    )

export function useOpenReceipt() {
    const { t } = useTranslation()
    const [opening, setOpening] = useState<string | null>(null)
    const open = async (a: PaymentAttachment) => {
        setOpening(a.document_id)
        try {
            await openPaymentAttachment(a)
        } catch (err) {
            notify(t('p7.payAtt.openFail'), 'danger', describeReceiptError(err, t))
        } finally {
            setOpening(null)
        }
    }
    return { open, opening }
}

const sizeText = (n: number | null | undefined) => {
    if (!n) return ''
    return n >= 1024 * 1024 ? `${(n / (1024 * 1024)).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`
}

/** The receipts of one payment as view links. */
export const ReceiptLinks = ({
    attachments,
    compact,
    onRemove,
    canRemove,
    removing,
}: {
    attachments: PaymentAttachment[]
    /** One short link per file (table cells). */
    compact?: boolean
    onRemove?: (a: PaymentAttachment) => void
    canRemove?: (a: PaymentAttachment) => boolean
    removing?: string | null
}) => {
    const { t } = useTranslation()
    const { open, opening } = useOpenReceipt()
    if (attachments.length === 0) return null
    return (
        <ul className={compact ? 'flex flex-wrap gap-x-2 gap-y-1' : 'flex flex-col gap-1.5'} data-testid="payatt-links">
            {attachments.map((a) => (
                <li key={a.document_id} className="flex min-w-0 items-center gap-1.5">
                    <button
                        type="button"
                        className="inline-flex min-w-0 items-center gap-1 text-[13px] font-semibold text-azure-600 hover:underline disabled:opacity-60"
                        onClick={(e) => {
                            e.stopPropagation()
                            open(a)
                        }}
                        disabled={opening === a.document_id}
                        title={`${t('p7.payAtt.view')}: ${a.name}`}
                        data-testid="payatt-view"
                    >
                        <HiOutlinePaperClip className="shrink-0" />
                        <span className={compact ? 'max-w-[140px] truncate' : 'truncate'}>
                            {compact ? t('p7.payAtt.view') : a.name}
                        </span>
                    </button>
                    {!compact && (
                        <span className="shrink-0 text-[12px] text-gray-600">
                            {[a.added_by?.name, sizeText(a.size_bytes)].filter(Boolean).join(' · ')}{' '}
                            <RiyadhTime iso={a.added_at} />
                        </span>
                    )}
                    {onRemove && canRemove?.(a) && (
                        <button
                            type="button"
                            className="shrink-0 p-1 text-red-600 hover:text-red-700 disabled:opacity-60"
                            onClick={(e) => {
                                e.stopPropagation()
                                onRemove(a)
                            }}
                            disabled={removing === a.document_id}
                            aria-label={String(t('p7.payAtt.remove'))}
                            title={String(t('p7.payAtt.remove'))}
                            data-testid="payatt-remove"
                        >
                            <HiOutlineTrash size={15} />
                        </button>
                    )}
                </li>
            ))}
        </ul>
    )
}

/** Optional receipt chooser: shows the chosen name with a remove button; rejects bad files at once. */
export const ReceiptPicker = ({
    file,
    onChange,
    disabled,
    label,
}: {
    file: File | null
    onChange: (f: File | null) => void
    disabled?: boolean
    label?: string
}) => {
    const { t } = useTranslation()
    const input = useRef<HTMLInputElement>(null)
    const [problem, setProblem] = useState<string | null>(null)
    return (
        <div className="flex flex-col gap-1">
            <div className="flex flex-wrap items-center gap-2">
                <input
                    ref={input}
                    type="file"
                    accept={RECEIPT_ACCEPT}
                    className="hidden"
                    disabled={disabled}
                    data-testid="payatt-file"
                    onChange={(e) => {
                        const f = e.target.files?.[0] || null
                        e.target.value = ''
                        if (!f) return
                        const p = receiptFileProblem(f)
                        if (p) {
                            setProblem(t(`p7.payAtt.invalid.${p}`))
                            return
                        }
                        setProblem(null)
                        onChange(f)
                    }}
                />
                <Button
                    type="button"
                    size="sm"
                    icon={<HiOutlinePaperClip />}
                    disabled={disabled}
                    onClick={() => input.current?.click()}
                >
                    {label || (file ? t('p7.payAtt.change') : t('p7.payAtt.choose'))}
                </Button>
                {file ? (
                    <span className="inline-flex min-w-0 items-center gap-1 rounded-pill border border-gray-300 px-2.5 py-0.5 text-[13px] text-gray-700 dark:border-night-border dark:text-night-body">
                        <span className="max-w-[220px] truncate" title={file.name}>
                            {file.name}
                        </span>
                        <span className="text-[12px] text-gray-500">{sizeText(file.size)}</span>
                        <button
                            type="button"
                            className="p-0.5 text-gray-600 hover:text-red-600"
                            aria-label={String(t('p7.payAtt.clear'))}
                            title={String(t('p7.payAtt.clear'))}
                            onClick={() => onChange(null)}
                            disabled={disabled}
                        >
                            <HiOutlineX size={14} />
                        </button>
                    </span>
                ) : (
                    <span className="text-[12.5px] text-gray-600">{t('p7.payAtt.hint')}</span>
                )}
            </div>
            {problem && (
                <div role="alert" className="text-[12.5px] text-red-600">
                    {problem}
                </div>
            )}
        </div>
    )
}

/**
 * Receipts of one payment: list, view, attach (when `canAttach`) and remove (pending payments,
 * by the uploader or accounts_payments). `onChanged` runs after a successful attach / remove.
 */
export const PaymentReceiptsDialog = ({
    paymentId,
    isOpen,
    onClose,
    canAttach,
    locked,
    onChanged,
}: {
    paymentId: string | null
    isOpen: boolean
    onClose: () => void
    canAttach?: boolean
    /** The case is locked / archived: no attach or remove. */
    locked?: boolean
    onChanged?: (items: PaymentAttachment[]) => void
}) => {
    const { t } = useTranslation()
    const perms = usePermissions()
    const me = useAppSelector((state) => state.auth.user.id) as string | undefined
    const [file, setFile] = useState<File | null>(null)
    const [busy, setBusy] = useState(false)
    const [removing, setRemoving] = useState<string | null>(null)
    const [error, setError] = useState<unknown>(null)
    const [tick, setTick] = useState(0)
    const q = useV2Query(
        isOpen && paymentId ? (signal) => paymentAttachmentsApi.list(paymentId, signal) : null,
        [isOpen, paymentId, tick]
    )
    const data = q.data
    const status = data?.payment_status
    const items = data?.items || []
    const writable = !locked && status !== 'deleted' && status !== 'reversed'
    const full = items.length >= (data?.max_attachments ?? RECEIPT_MAX_PER_PAYMENT)

    const close = () => {
        if (busy) return
        setFile(null)
        setError(null)
        onClose()
    }

    const upload = async () => {
        if (!paymentId || !file) return
        setBusy(true)
        setError(null)
        try {
            const res = await paymentAttachmentsApi.upload(paymentId, file, newIdempotencyKey())
            setFile(null)
            notify(t('p7.payAtt.attached'), 'success')
            onChanged?.(res.items)
            setTick((n) => n + 1)
        } catch (err) {
            setError(err)
        } finally {
            setBusy(false)
        }
    }

    const remove = async (a: PaymentAttachment) => {
        if (!paymentId || !window.confirm(String(t('p7.payAtt.confirmRemove')))) return
        setRemoving(a.document_id)
        setError(null)
        try {
            const res = await paymentAttachmentsApi.remove(paymentId, a.document_id, newIdempotencyKey())
            notify(t('p7.payAtt.removed'), 'success')
            onChanged?.(res.items)
            setTick((n) => n + 1)
        } catch (err) {
            setError(err)
        } finally {
            setRemoving(null)
        }
    }

    const canRemove = (a: PaymentAttachment) =>
        !locked && status === 'pending' && (!!perms.accounts_payments || (!!me && a.added_by?.id === me))

    return (
        <Dialog isOpen={isOpen} width={560} onClose={close} onRequestClose={close} shouldCloseOnOverlayClick={!busy}>
            <div className="flex max-h-[80vh] flex-col" data-testid="payatt-dialog">
                <h5 className="mb-4 pe-8 text-[17px] font-extrabold text-navy-600 dark:text-gray-100">
                    {t('p7.payAtt.title')}
                </h5>
                <div className="min-h-0 flex-1 overflow-y-auto pe-1">
                    {q.loading && !data ? (
                        <SkeletonLines rows={2} />
                    ) : q.error && !data ? (
                        <InlineError error={q.error} describe={describeReceiptError} />
                    ) : items.length === 0 ? (
                        <div className="text-[13.5px] text-gray-600">{t('p7.payAtt.none')}</div>
                    ) : (
                        <ReceiptLinks attachments={items} onRemove={remove} canRemove={canRemove} removing={removing} />
                    )}

                    {data && canAttach && writable && (
                        <div className="mt-4 border-t border-gray-200 pt-4 dark:border-night-border">
                            {full ? (
                                <div className="text-[13px] text-gray-600">
                                    {t('p7.payAtt.full', { max: data.max_attachments })}
                                </div>
                            ) : (
                                <div className="flex flex-wrap items-center gap-2">
                                    <ReceiptPicker file={file} onChange={setFile} disabled={busy} />
                                    {file && (
                                        <Button
                                            size="sm"
                                            variant="solid"
                                            type="button"
                                            loading={busy}
                                            onClick={upload}
                                            data-testid="payatt-upload"
                                        >
                                            {t('p7.payAtt.attach')}
                                        </Button>
                                    )}
                                </div>
                            )}
                        </div>
                    )}
                    <InlineError error={error} describe={describeReceiptError} className="mt-3" />
                </div>
                <div className="mt-5 flex items-center justify-end">
                    <Button size="sm" type="button" onClick={close} disabled={busy}>
                        {t('p7.payAtt.close')}
                    </Button>
                </div>
            </div>
        </Dialog>
    )
}

/** Legacy screens: a paperclip that opens the payment's receipts (view only). */
export const LegacyReceiptButton = ({ paymentId }: { paymentId: string }) => {
    const { t } = useTranslation()
    const [open, setOpen] = useState(false)
    return (
        <>
            <Tooltip title={t('p7.payAtt.title')}>
                <span
                    role="button"
                    tabIndex={0}
                    className="cursor-pointer p-2 hover:text-azure-600"
                    aria-label={String(t('p7.payAtt.title'))}
                    onClick={() => setOpen(true)}
                    onKeyDown={(e) => {
                        if (e.key === 'Enter' || e.key === ' ') setOpen(true)
                    }}
                    data-testid="payatt-legacy"
                >
                    <HiOutlinePaperClip />
                </span>
            </Tooltip>
            <PaymentReceiptsDialog paymentId={paymentId} isOpen={open} onClose={() => setOpen(false)} />
        </>
    )
}
