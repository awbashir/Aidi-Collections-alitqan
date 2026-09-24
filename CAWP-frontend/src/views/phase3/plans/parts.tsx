/**
 * Small display pieces for the repayment-plan screens: state badges (plan and
 * installment), a button-per-option choice control for the wizard.
 * States come from the API as they are; nothing is derived here.
 */
import classNames from 'classnames'
import { useTranslation } from 'react-i18next'
import { HiArrowNarrowRight } from 'react-icons/hi'
import StatusPill from '@/components/shared/StatusPill'
import { CivilDate } from '@/components/v2/Display'
import type { StatusTone } from '@/components/shared/StatusPill'
import type { InstallmentState, PlanState } from '@/services/v2'
import type { ReactNode } from 'react'

type Size = 'row' | 'md'

const PLAN_TONE: Record<PlanState, StatusTone> = {
    active: 'info',
    completed: 'success',
    broken: 'danger',
    cancelled: 'neutral',
}

export const PlanStateBadge = ({ state, size }: { state: PlanState; size?: Size }) => {
    const { t } = useTranslation()
    return (
        <StatusPill tone={PLAN_TONE[state] || 'neutral'} size={size}>
            <span data-state={state}>{t(`p3.plan.state.${state}`, { defaultValue: state })}</span>
        </StatusPill>
    )
}

/* Six installment states over five tones: "partially paid" gets its own teal
 * colour (filled dot) so it reads apart from "due" (amber diamond). */
const INSTALLMENT_TONE: Record<InstallmentState, { tone: StatusTone; color?: string }> = {
    upcoming: { tone: 'info' },
    due: { tone: 'warning' },
    overdue: { tone: 'danger' },
    partially_paid: { tone: 'success', color: '#0F766E' },
    paid: { tone: 'success' },
    cancelled: { tone: 'neutral' },
}

export const InstallmentBadge = ({ state, size }: { state: InstallmentState; size?: Size }) => {
    const { t } = useTranslation()
    const s = INSTALLMENT_TONE[state] || { tone: 'neutral' as StatusTone }
    return (
        <StatusPill tone={s.tone} color={s.color} size={size}>
            <span data-state={state}>{t(`p3.plans.installmentState.${state}`, { defaultValue: state })}</span>
        </StatusPill>
    )
}

/** A segmented choice where every option is its own button (with its own test id). */
export function ChoiceButtons<V extends string>({
    value,
    options,
    onChange,
    label,
}: {
    value: V
    options: { value: V; label: ReactNode; testId?: string }[]
    onChange: (v: V) => void
    label?: string
}) {
    return (
        <div
            role="radiogroup"
            aria-label={label}
            className="inline-flex h-9 overflow-hidden rounded-md border border-gray-400 dark:border-night-border"
        >
            {options.map((o, i) => {
                const active = o.value === value
                return (
                    <button
                        key={o.value}
                        type="button"
                        role="radio"
                        aria-checked={active}
                        data-value={o.value}
                        data-testid={o.testId}
                        onClick={() => onChange(o.value)}
                        className={classNames(
                            'px-4 text-[13px] focus:outline-none focus-visible:ring-2 focus-visible:ring-azure-600',
                            i > 0 && 'border-s border-gray-400 dark:border-night-border',
                            active
                                ? 'bg-navy-600 font-extrabold text-white dark:bg-azure-600'
                                : 'bg-white text-gray-700 hover:bg-gray-50 dark:bg-night-card dark:text-night-body'
                        )}
                    >
                        {o.label}
                    </button>
                )
            })}
        </div>
    )
}

/** "start → end" with the arrow pointing along the reading direction (flips in RTL). */
export const DateRange = ({ start, end }: { start: string | null | undefined; end: string | null | undefined }) => (
    <span className="inline-flex items-center gap-1.5 whitespace-nowrap">
        <CivilDate value={start} />
        <HiArrowNarrowRight aria-hidden className="shrink-0 text-gray-500 rtl:rotate-180" />
        <CivilDate value={end} />
    </span>
)
