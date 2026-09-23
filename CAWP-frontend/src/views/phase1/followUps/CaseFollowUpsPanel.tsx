/**
 * Case-detail panel: the case's open follow-up with its actions, then the full
 * follow-up history inline (GET /v2/cases/<id>/follow-ups).
 *
 * Permission `follow_ups` for the panel; acting on someone else's case also
 * needs `follow_ups_manage` (the API answers 403 otherwise). Archived cases are
 * read-only; a locked case is refused by the API with 423 `case_locked`.
 * A completion writes a real note into the legacy timeline, so every mutation
 * calls `onChanged()` for the legacy screen to reload what it shows.
 */
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { HiOutlineRefresh } from 'react-icons/hi'
import Button from '@/components/ui/Button'
import ToastNotification from '@/components/shared/myComponents/ToastNotification'
import { worklistApi } from '@/services/v2'
import type { FollowUp } from '@/services/v2'
import { useV2Query } from '@/utils/v2/hooks'
import { Field, RiyadhTime } from '@/components/v2/Display'
import { BucketBadge } from '@/components/v2/Badges'
import { EmptyState, ErrorState, ForbiddenState, Note, SkeletonLines } from '@/components/v2/States'
import type { CasePanelProps } from '../caseTypes'
import { CompleteFollowUpDialog, CreateFollowUpDialog, RescheduleFollowUpDialog } from './FollowUpDialogs'
import { FollowUpHistory } from './FollowUpHistory'
import { SourceCell } from './parts'
import { useFollowUpAccess } from './access'

type Open = null | 'create' | 'complete' | 'reschedule'

const CaseFollowUpsPanel = ({ caseData, onChanged }: CasePanelProps) => {
    const { t } = useTranslation()
    const access = useFollowUpAccess()
    const [open, setOpen] = useState<Open>(null)
    const query = useV2Query(access.canView ? (signal) => worklistApi.caseFollowUps(caseData.id, signal) : null, [
        caseData.id,
        access.canView,
    ])

    if (!access.canView) return <ForbiddenState keys={['follow_ups']} />

    const data = query.data
    const current: FollowUp | null = data?.current ?? null
    const archived = !!(data?.case_is_archived || caseData.is_archive)
    const ownerId = current?.owner_user_id ?? caseData.user_id ?? null
    const canAct = access.canActFor(ownerId)

    const changed = (message: string) => {
        setOpen(null)
        ToastNotification(message, t('p1.followUps.title'), 'success')
        query.reload()
        onChanged?.()
    }

    return (
        <div data-testid="p1-case-follow-ups" className="flex flex-col gap-4">
            <div className="rounded-lg border border-gray-300 bg-white p-5 dark:border-night-border dark:bg-night-card">
                <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
                    <div className="text-eyebrow">{t('p1.followUps.panel.current')}</div>
                    <div className="flex flex-wrap items-center gap-2">
                        {!archived && canAct && data && !current && (
                            <Button
                                size="sm"
                                variant="solid"
                                onClick={() => setOpen('create')}
                                data-testid="p1-fu-panel-create"
                            >
                                {t('p1.followUps.action.create')}
                            </Button>
                        )}
                        {!archived && canAct && current && (
                            <>
                                <Button
                                    size="sm"
                                    variant="solid"
                                    onClick={() => setOpen('complete')}
                                    data-testid="p1-fu-panel-complete"
                                >
                                    {t('p1.followUps.action.complete')}
                                </Button>
                                <Button
                                    size="sm"
                                    onClick={() => setOpen('reschedule')}
                                    data-testid="p1-fu-panel-reschedule"
                                >
                                    {t('p1.followUps.action.reschedule')}
                                </Button>
                            </>
                        )}
                        <Button
                            size="sm"
                            variant="plain"
                            icon={<HiOutlineRefresh />}
                            loading={query.loading && !!data}
                            onClick={query.reload}
                            data-testid="p1-fu-panel-refresh"
                        >
                            {t('p1.action.refresh')}
                        </Button>
                    </div>
                </div>

                {query.error && !data ? (
                    <ErrorState error={query.error} onRetry={query.reload} />
                ) : !data ? (
                    <SkeletonLines rows={3} />
                ) : current ? (
                    <div
                        className="grid grid-cols-2 gap-4 md:grid-cols-4"
                        data-testid="p1-fu-panel-open"
                        data-follow-up={current.id}
                        data-revision={current.revision}
                    >
                        <Field label={t('p1.col.due')}>
                            <div className="flex flex-col items-start gap-1">
                                <RiyadhTime iso={current.due_at} className="font-extrabold" />
                                {current.bucket && (
                                    <BucketBadge bucket={current.bucket} daysLate={current.days_late} size="row" />
                                )}
                                {current.original_due_at !== current.due_at && (
                                    <span className="text-[12px] text-gray-600">
                                        {t('p1.followUps.originally')} <RiyadhTime iso={current.original_due_at} />
                                    </span>
                                )}
                            </div>
                        </Field>
                        <Field label={t('p1.col.nextAction')}>
                            <span dir="auto">{current.next_action || <span className="text-gray-500">—</span>}</span>
                        </Field>
                        <Field label={t('p1.col.source')}>
                            <SourceCell fu={current} snippet={false} />
                        </Field>
                        <Field label={t('p1.followUps.col.revision')}>
                            <span className="tabular-nums">
                                {current.revision}
                                {current.reschedule_count > 0 && (
                                    <span className="ms-2 text-[12.5px] text-gray-600">
                                        {t('p1.followUps.rescheduledTimes', { count: current.reschedule_count })}
                                    </span>
                                )}
                            </span>
                        </Field>
                    </div>
                ) : (
                    <EmptyState
                        className="py-4"
                        title={t('p1.followUps.panel.noneTitle')}
                        hint={archived ? undefined : t('p1.followUps.panel.noneHint')}
                    />
                )}

                {data && archived && <Note className="mt-3">{t('p1.followUps.panel.archived')}</Note>}
                {data && !archived && caseData.is_locked && (
                    <Note className="mt-3">{t('p1.followUps.panel.locked')}</Note>
                )}
                {data && !archived && !canAct && <Note className="mt-3">{t('p1.followUps.notOwner')}</Note>}
            </div>

            <div className="rounded-lg border border-gray-300 bg-white p-5 dark:border-night-border dark:bg-night-card">
                <div className="text-eyebrow mb-4">{t('p1.followUps.history.title')}</div>
                {query.error && !data ? null : !data ? <SkeletonLines rows={4} /> : <FollowUpHistory data={data} />}
            </div>

            <CreateFollowUpDialog
                isOpen={open === 'create'}
                caseId={caseData.id}
                caseName={caseData.name}
                accountNumber={caseData.accountNumber}
                onClose={() => setOpen(null)}
                onDone={() => changed(t('p1.followUps.toast.created'))}
            />
            <CompleteFollowUpDialog
                isOpen={open === 'complete'}
                followUp={current}
                contractId={caseData.contract?.id ?? null}
                onClose={() => setOpen(null)}
                onReload={query.reload}
                onDone={() => changed(t('p1.followUps.toast.completed'))}
            />
            <RescheduleFollowUpDialog
                isOpen={open === 'reschedule'}
                followUp={current}
                onClose={() => setOpen(null)}
                onReload={query.reload}
                onDone={() => changed(t('p1.followUps.toast.rescheduled'))}
            />
        </div>
    )
}

export default CaseFollowUpsPanel
export { CaseFollowUpsPanel }
