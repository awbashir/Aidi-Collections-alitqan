/**
 * Dialog shell for every Phase 1 mutation.
 *
 * - One Idempotency-Key per opening of the dialog (one user action). A retry
 *   after an error reuses it, so a lost response can never double-apply.
 * - Errors stay inside the dialog with the user's draft intact. A 409
 *   (changed by someone else) offers "reload"; nothing is overwritten silently.
 */
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import Dialog from '@/components/ui/Dialog'
import Button from '@/components/ui/Button'
import Input from '@/components/ui/Input'
import { useActionKey } from '@/utils/v2/hooks'
import { InlineError } from './States'
import type { ReactNode } from 'react'
import type { Translate } from '@/services/v2'

export type ActionDialogProps = {
    isOpen: boolean
    title: ReactNode
    /** Called with this action's Idempotency-Key. Throw to keep the dialog open. */
    onSubmit: (idempotencyKey: string) => Promise<void>
    onClose: () => void
    /** Reload the underlying record after a 409 (the dialog closes). */
    onReload?: () => void
    confirmLabel?: ReactNode
    tone?: 'primary' | 'danger'
    canSubmit?: boolean
    width?: number
    children?: ReactNode
    testId?: string
    /** Message builder for errors (default `describeV2Error`). */
    describeError?: (error: unknown, t: Translate) => string
}

const ActionDialog = ({
    isOpen,
    title,
    onSubmit,
    onClose,
    onReload,
    confirmLabel,
    tone = 'primary',
    canSubmit = true,
    width = 560,
    children,
    testId,
    describeError,
}: ActionDialogProps) => {
    const { t } = useTranslation()
    const { key } = useActionKey(isOpen)
    const [busy, setBusy] = useState(false)
    const [error, setError] = useState<unknown>(null)

    const close = () => {
        if (busy) return
        setError(null)
        onClose()
    }

    const submit = async () => {
        if (busy || !canSubmit) return
        setBusy(true)
        setError(null)
        try {
            await onSubmit(key)
            setBusy(false)
            onClose()
        } catch (err) {
            setBusy(false)
            setError(err)
        }
    }

    return (
        <Dialog isOpen={isOpen} width={width} onClose={close} onRequestClose={close} shouldCloseOnOverlayClick={!busy}>
            <div data-testid={testId} className="flex max-h-[80vh] flex-col">
                <h5 className="mb-4 pe-8 text-[17px] font-extrabold text-navy-600 dark:text-gray-100">{title}</h5>
                <div className="min-h-0 flex-1 overflow-y-auto pe-1">
                    {children}
                    <InlineError
                        error={error}
                        describe={describeError}
                        onReload={
                            onReload
                                ? () => {
                                      setError(null)
                                      onClose()
                                      onReload()
                                  }
                                : undefined
                        }
                    />
                </div>
                <div className="mt-5 flex items-center justify-end gap-2">
                    <Button size="sm" type="button" onClick={close} disabled={busy}>
                        {t('cancel')}
                    </Button>
                    <Button
                        size="sm"
                        type="button"
                        variant={tone === 'danger' ? 'destructive' : 'solid'}
                        loading={busy}
                        disabled={!canSubmit}
                        aria-disabled={!canSubmit || busy}
                        onClick={submit}
                        data-testid={testId ? `${testId}-confirm` : undefined}
                    >
                        {confirmLabel || t('confirm')}
                    </Button>
                </div>
            </div>
        </Dialog>
    )
}

export default ActionDialog

/** A labelled field block inside a dialog. */
export const DialogField = ({
    label,
    hint,
    required,
    children,
}: {
    label: ReactNode
    hint?: ReactNode
    required?: boolean
    children: ReactNode
}) => (
    <label className="mb-4 block">
        <span className="mb-[7px] block text-[12.5px] font-extrabold text-navy-600 dark:text-gray-100">
            {label}
            {required && <span className="text-danger"> *</span>}
        </span>
        {children}
        {hint && <span className="mt-[7px] block text-[12px] text-gray-600">{hint}</span>}
    </label>
)

/**
 * A mutation that needs a written reason (reject, reverse, cancel, retire,
 * block, reschedule-less actions). The confirm button stays disabled until
 * the reason has text, mirroring the API's `reason: required`.
 */
export const ReasonDialog = ({
    isOpen,
    title,
    description,
    reasonLabel,
    confirmLabel,
    tone = 'primary',
    onSubmit,
    onClose,
    onReload,
    extra,
    extraValid = true,
    testId,
    describeError,
}: {
    isOpen: boolean
    title: ReactNode
    description?: ReactNode
    reasonLabel?: ReactNode
    confirmLabel?: ReactNode
    tone?: 'primary' | 'danger'
    onSubmit: (reason: string, idempotencyKey: string) => Promise<void>
    onClose: () => void
    onReload?: () => void
    extra?: ReactNode
    extraValid?: boolean
    testId?: string
    /** Message builder for errors (default `describeV2Error`); Phase 3 passes `describeP3Error`. */
    describeError?: (error: unknown, t: Translate) => string
}) => {
    const { t } = useTranslation()
    const [reason, setReason] = useState('')
    const trimmed = reason.trim()
    return (
        <ActionDialog
            isOpen={isOpen}
            title={title}
            tone={tone}
            confirmLabel={confirmLabel}
            canSubmit={trimmed.length > 0 && extraValid}
            testId={testId}
            onReload={onReload}
            describeError={describeError}
            onClose={() => {
                setReason('')
                onClose()
            }}
            onSubmit={async (key) => {
                await onSubmit(trimmed, key)
                setReason('')
            }}
        >
            {description && <div className="mb-4 text-[13.5px] text-gray-700 dark:text-night-body">{description}</div>}
            {extra}
            <DialogField label={reasonLabel || t('p1.fields.reason')} required hint={t('p1.hint.reasonRequired')}>
                <Input
                    textArea
                    value={reason}
                    onChange={(e) => setReason(e.target.value)}
                    data-testid={testId ? `${testId}-reason` : undefined}
                    maxLength={2000}
                />
            </DialogField>
        </ActionDialog>
    )
}
