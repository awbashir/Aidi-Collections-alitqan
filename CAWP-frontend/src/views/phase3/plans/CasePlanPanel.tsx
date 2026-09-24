/**
 * Case detail: the repayment plan tab (`/clients/details/plan?id=<caseId>`).
 *
 * GET /v2/cases/<id>/plans gives the balance, the active plan id, the broken
 * threshold and every plan (active first) with installments, allocations and
 * events. The active plan shows with its actions (PlanDetail); earlier plans
 * are listed collapsed and open read-only. "Create a plan" opens the wizard
 * (preview first, then create). Plans never change the debt.
 */
import { useState } from 'react'
import classNames from 'classnames'
import { useTranslation } from 'react-i18next'
import { HiOutlineChevronDown, HiOutlinePlus } from 'react-icons/hi'
import Button from '@/components/ui/Button'
import ToastNotification from '@/components/shared/myComponents/ToastNotification'
import { MoneyText, RiyadhTime } from '@/components/v2/Display'
import { EmptyState, ErrorState, Note, SkeletonLines } from '@/components/v2/States'
import { plansApi } from '@/services/v2'
import type { Plan } from '@/services/v2'
import type { CasePanelProps } from '@/views/phase1/caseTypes'
import { usePermissions, useV2Query } from '@/utils/v2/hooks'
import { formatMoney } from '@/utils/v2/format'
import PlanDetail from './PlanDetail'
import { CancelPlanDialog } from './PlanDialogs'
import PlanWizard from './PlanWizard'
import { DateRange, PlanStateBadge } from './parts'
import { describePlanError } from './planUtils'

const card = 'rounded-lg border border-gray-300 bg-white dark:border-night-border dark:bg-night-card'

const CasePlanPanel = ({ caseData, onChanged }: CasePanelProps) => {
    const { t } = useTranslation()
    const perms = usePermissions()
    const caseId = caseData.id
    const q = useV2Query((signal) => plansApi.casePlans(caseId, signal), [caseId])
    const [wizardOpen, setWizardOpen] = useState(false)
    const [cancelCompleted, setCancelCompleted] = useState<Plan | null>(null)
    const [open, setOpen] = useState<string | null>(null)

    const data = q.data
    const items = data?.items || []
    const active = data?.active_plan_id ? items.find((p) => p.id === data.active_plan_id) : undefined
    const earlier = items.filter((p) => p.id !== data?.active_plan_id)
    const manage = !!perms.plans_manage
    const canCreate = manage && !!data && !data.active_plan_id && !caseData.is_locked

    const changed = (message: string) => {
        ToastNotification(message, t('p3.plans.title'), 'success')
        q.reload()
        onChanged?.()
    }

    const createButton = (
        <Button
            size="sm"
            variant="solid"
            icon={<HiOutlinePlus />}
            onClick={() => setWizardOpen(true)}
            data-testid="p3-plan-create"
        >
            {t('p3.plans.create')}
        </Button>
    )

    return (
        <div data-testid="p3-case-plan" data-active-plan={data?.active_plan_id || ''}>
            <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
                <div className="min-w-0">
                    <h6 className="mb-0 text-[15px] font-extrabold text-navy-600 dark:text-gray-100">
                        {t('p3.plans.caseTitle')}
                    </h6>
                    {data && (
                        <div
                            className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-[13px]"
                            data-testid="p3-case-plan-balance"
                            data-outstanding={data.balance.outstanding}
                        >
                            <span>
                                <span className="text-gray-600">{t('p3.plans.balance.original')}</span>{' '}
                                <MoneyText value={data.balance.original} />
                            </span>
                            <span>
                                <span className="text-gray-600">{t('p3.plans.balance.approved')}</span>{' '}
                                <MoneyText value={data.balance.approved} />
                            </span>
                            <span>
                                <span className="text-gray-600">{t('p3.plans.balance.outstanding')}</span>{' '}
                                <MoneyText value={data.balance.outstanding} strong />
                            </span>
                            <span className="text-gray-600" data-testid="p3-case-plan-threshold">
                                {t('p3.plans.thresholdLine', { n: data.broken_threshold })}
                            </span>
                        </div>
                    )}
                </div>
                {canCreate && items.length > 0 && createButton}
            </div>
            {caseData.is_locked && manage && !data?.active_plan_id && (
                <Note className="mb-3">{t('p3.plans.caseLocked')}</Note>
            )}

            {q.loading && !data ? (
                <div className={classNames(card, 'p-5')}>
                    <SkeletonLines rows={6} />
                </div>
            ) : q.error && !data ? (
                <div className={card}>
                    <ErrorState error={q.error} onRetry={q.reload} describe={describePlanError} />
                </div>
            ) : items.length === 0 ? (
                <div className={card}>
                    <EmptyState
                        title={t('p3.plans.caseEmpty')}
                        hint={t('p3.plans.caseEmptyHint')}
                        action={canCreate ? <div className="mt-2">{createButton}</div> : undefined}
                    />
                </div>
            ) : (
                <>
                    {active ? (
                        <section className={classNames(card, 'p-5')} data-testid="p3-case-plan-active" data-plan={active.id}>
                            {/* Old behaviour only: fully paid but still stored `active`
                                (the API answers 409 plan_already_active, state completed). */}
                            {active.state === 'completed' && active.status === 'active' && manage && (
                                <div
                                    className="mb-4 flex flex-wrap items-center justify-between gap-2 rounded-md border border-gray-300 bg-gray-50 px-3 py-2 text-[13px] dark:border-night-border dark:bg-night-raised"
                                    data-testid="p3-plan-completed-note"
                                >
                                    <span>{t('p3.plans.completedNote')}</span>
                                    <Button
                                        size="xs"
                                        onClick={() => setCancelCompleted(active)}
                                        data-testid="p3-plan-cancel-completed"
                                    >
                                        {t('p3.plans.cancel')}
                                    </Button>
                                </div>
                            )}
                            <PlanDetail
                                plan={active}
                                onReload={q.reload}
                                onChanged={() => changed(String(t('p3.plans.saved')))}
                            />
                        </section>
                    ) : (
                        <div className={classNames(card, 'px-5 py-4 text-[13px] text-gray-700 dark:text-night-body')}>
                            {t('p3.plans.noActive')}
                        </div>
                    )}

                    {earlier.length > 0 && (
                        <section className="mt-6" data-testid="p3-case-plan-earlier">
                            <h6 className="mb-2 text-[14px] font-extrabold text-navy-600 dark:text-gray-100">
                                {t('p3.plans.earlierTitle')}
                            </h6>
                            <ul className="flex flex-col gap-2">
                                {earlier.map((p) => {
                                    const isOpen = open === p.id
                                    return (
                                        <li key={p.id} className={card} data-testid="p3-case-plan-earlier-item" data-plan={p.id} data-state={p.state}>
                                            <button
                                                type="button"
                                                className="flex w-full flex-wrap items-center gap-x-4 gap-y-1 px-4 py-3 text-start text-[13px] hover:bg-gray-50 dark:hover:bg-night-raised"
                                                aria-expanded={isOpen}
                                                onClick={() => setOpen(isOpen ? null : p.id)}
                                            >
                                                <HiOutlineChevronDown aria-hidden className={classNames('text-gray-500', isOpen && 'rotate-180')} />
                                                <PlanStateBadge state={p.state} size="row" />
                                                <MoneyText value={p.total_amount} strong />
                                                <span className="text-gray-600">{t(`p3.plans.freq.${p.frequency}`)}</span>
                                                <span className="whitespace-nowrap">
                                                    <DateRange start={p.start_date} end={p.end_date} />
                                                </span>
                                                <span className="text-gray-600">
                                                    {t('p3.plans.paidOfTotal', { paid: p.counts?.paid ?? 0, total: p.installment_count })}
                                                </span>
                                                {p.status === 'completed' && p.completed_at && (
                                                    <span className="text-gray-600">
                                                        {t('p3.plans.col.completedAt')} <RiyadhTime iso={p.completed_at} />
                                                    </span>
                                                )}
                                                {p.cancellation && (
                                                    <span className="text-gray-600">
                                                        {t('p3.plans.cancelledBy')} {p.cancellation.by?.name || '—'} ·{' '}
                                                        <RiyadhTime iso={p.cancellation.at} />
                                                    </span>
                                                )}
                                            </button>
                                            {isOpen && (
                                                <div className="border-t border-gray-300 px-4 py-4 dark:border-night-border">
                                                    <PlanDetail plan={p} readOnly onReload={q.reload} onChanged={() => q.reload()} />
                                                </div>
                                            )}
                                        </li>
                                    )
                                })}
                            </ul>
                        </section>
                    )}
                    {q.error && data ? <ErrorState error={q.error} onRetry={q.reload} describe={describePlanError} /> : null}
                </>
            )}

            <Note className="mt-4">{t('p3.plans.caseNote')}</Note>

            <PlanWizard
                caseId={caseId}
                isOpen={wizardOpen}
                outstanding={data?.balance.outstanding}
                onClose={() => setWizardOpen(false)}
                onReload={q.reload}
                onCreated={(p) =>
                    changed(
                        String(
                            t('p3.plans.created', {
                                total: formatMoney(p.total_amount),
                                n: p.installment_count,
                            })
                        )
                    )
                }
            />
            <CancelPlanDialog
                plan={cancelCompleted}
                onClose={() => setCancelCompleted(null)}
                onReload={q.reload}
                onDone={() => changed(String(t('p3.plans.cancelled')))}
            />
        </div>
    )
}

export default CasePlanPanel
