/**
 * Cancel / retry / reopen for a job, shown only when the API's `actions` allows
 * them. Each dialog opening is one user action with its own Idempotency-Key.
 */
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import Button from '@/components/ui/Button'
import Input from '@/components/ui/Input'
import ActionDialog, { DialogField } from '@/components/v2/ActionDialog'
import { bulkApi, describeBulkError } from '@/services/v2'
import type { BulkJob } from '@/services/v2'
import { formatCount } from '@/utils/v2/format'

type Open = null | 'cancel' | 'retry' | 'reopen'

export const JobActions = ({
    job,
    onChanged,
    only,
    size = 'sm',
}: {
    job: BulkJob
    onChanged: () => void
    only?: Array<'cancel' | 'retry' | 'reopen'>
    size?: 'sm' | 'xs'
}) => {
    const { t } = useTranslation()
    const navigate = useNavigate()
    const [open, setOpen] = useState<Open>(null)
    const [reason, setReason] = useState('')
    const want = (a: 'cancel' | 'retry' | 'reopen') => !only || only.includes(a)
    const a = job.actions
    const any = (a.cancel && want('cancel')) || (a.retry && want('retry')) || (a.reopen && want('reopen'))
    if (!any && !open) return null
    return (
        <>
            <div className="flex flex-wrap items-center gap-2" data-testid="p2-job-actions">
                {a.cancel && want('cancel') && (
                    <Button size={size} variant="default" onClick={() => setOpen('cancel')} data-testid="p2-cancel-btn">
                        {t('p2.action.cancel')}
                    </Button>
                )}
                {a.retry && want('retry') && (
                    <Button size={size} onClick={() => setOpen('retry')} data-testid="p2-retry-btn">
                        {job.phase === 'prepare' ? t('p2.action.retryValidation') : t('p2.action.retry')}
                    </Button>
                )}
                {a.reopen && want('reopen') && (
                    <Button size={size} onClick={() => setOpen('reopen')} data-testid="p2-reopen-btn">
                        {t('p2.action.reopen')}
                    </Button>
                )}
            </div>

            <ActionDialog
                isOpen={open === 'cancel'}
                title={t('p2.action.cancelTitle')}
                tone="danger"
                confirmLabel={t('p2.action.cancelConfirm')}
                testId="p2-cancel-dialog"
                describeError={describeBulkError}
                onReload={onChanged}
                onClose={() => {
                    setOpen(null)
                    setReason('')
                }}
                onSubmit={async (key) => {
                    await bulkApi.cancel(job.id, reason.trim() ? { reason: reason.trim() } : {}, key)
                    onChanged()
                }}
            >
                <p className="mb-4 text-[13.5px] text-gray-700 dark:text-night-body">
                    {job.status === 'running' || job.status === 'validating' ? t('p2.action.cancelRunning') : t('p2.action.cancelNow')}
                </p>
                <DialogField label={t('p2.action.cancelReason')}>
                    <Input textArea value={reason} maxLength={1000} onChange={(e) => setReason(e.target.value)} data-testid="p2-cancel-dialog-reason" />
                </DialogField>
            </ActionDialog>

            <ActionDialog
                isOpen={open === 'retry'}
                title={t('p2.action.retryTitle')}
                confirmLabel={t('p2.action.retryConfirm')}
                testId="p2-retry-dialog"
                describeError={describeBulkError}
                onReload={onChanged}
                onClose={() => setOpen(null)}
                onSubmit={async (key) => {
                    await bulkApi.retry(job.id, { expected_revision: job.revision }, key)
                    onChanged()
                }}
            >
                <p className="text-[13.5px] text-gray-700 dark:text-night-body">
                    {job.phase === 'prepare'
                        ? t('p2.action.retryValidationBody')
                        : t('p2.action.retryBody', { failed: formatCount(job.counts.failed) })}
                </p>
            </ActionDialog>

            <ActionDialog
                isOpen={open === 'reopen'}
                title={t('p2.action.reopenTitle')}
                confirmLabel={t('p2.action.reopenConfirm')}
                testId="p2-reopen-dialog"
                describeError={describeBulkError}
                onClose={() => setOpen(null)}
                onSubmit={async (key) => {
                    const next = await bulkApi.reopen(job.id, key)
                    navigate(`/jobs/run/${next.id}`)
                }}
            >
                <p className="text-[13.5px] text-gray-700 dark:text-night-body">{t('p2.action.reopenBody')}</p>
            </ActionDialog>
        </>
    )
}

export default JobActions
