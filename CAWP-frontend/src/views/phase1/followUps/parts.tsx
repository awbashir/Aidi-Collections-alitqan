/**
 * Small pieces shared by the follow-up screen, dialogs and case panel.
 */
import classNames from 'classnames'
import { useTranslation } from 'react-i18next'
import Input from '@/components/ui/Input'
import { riyadhToday } from '@/utils/v2/format'
import { PlainBadge } from '@/components/v2/Badges'
import { DialogField } from '@/components/v2/ActionDialog'
import type { FollowUp } from '@/services/v2'
import type { ReactNode } from 'react'

/** Source of a follow-up (note / manual / imported) and the source-note state. */
export const SourceCell = ({ fu, snippet = true }: { fu: FollowUp; snippet?: boolean }) => {
    const { t } = useTranslation()
    const note = fu.source_note
    return (
        <div className="flex min-w-0 flex-col items-start gap-1" data-testid="p1-fu-source" data-source={fu.source}>
            <PlainBadge tone={fu.source === 'manual' ? 'info' : 'neutral'} size="row">
                {t(`p1.followUps.source.${fu.source}`)}
            </PlainBadge>
            {note && note.missing && (
                <span className="text-[12px] font-extrabold text-danger" data-testid="p1-fu-source-missing">
                    {t('p1.followUps.sourceNoteMissing')}
                </span>
            )}
            {note && !note.missing && note.is_deleted && (
                <span className="text-[12px] font-extrabold text-warning" data-testid="p1-fu-source-retired">
                    {t('p1.followUps.sourceNoteRetired')}
                </span>
            )}
            {snippet && note && !note.missing && note.text && (
                <span dir="auto" title={note.text} className="block max-w-[170px] truncate text-[12px] text-gray-600">
                    {note.status ? <span className="font-extrabold">{note.status.trim()} · </span> : null}
                    {note.text}
                </span>
            )}
        </div>
    )
}

/** A two-option segmented control (radio group), 32px, navy active segment. */
export function Segmented<V extends string>({
    value,
    options,
    onChange,
    testId,
    label,
}: {
    value: V
    options: { value: V; label: ReactNode }[]
    onChange: (v: V) => void
    testId?: string
    label?: string
}) {
    return (
        <div
            role="radiogroup"
            aria-label={label}
            data-testid={testId}
            className="inline-flex h-8 overflow-hidden rounded-md border border-gray-400 dark:border-night-border"
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
                        onClick={() => onChange(o.value)}
                        className={classNames(
                            'px-3.5 text-[12.5px] focus:outline-none focus-visible:ring-2 focus-visible:ring-azure-600',
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

/** Date (Riyadh civil) + time (Riyadh) inputs for a due time. */
export const DueFields = ({
    date,
    time,
    onDate,
    onTime,
    testId,
    dateLabel,
}: {
    date: string
    time: string
    onDate: (v: string) => void
    onTime: (v: string) => void
    testId: string
    dateLabel?: ReactNode
}) => {
    const { t } = useTranslation()
    return (
        <div className="grid grid-cols-1 gap-x-3 sm:grid-cols-2">
            <DialogField label={dateLabel || t('p1.followUps.dueDate')} required hint={t('p1.followUps.riyadhHint')}>
                <Input
                    type="date"
                    size="sm"
                    min={riyadhToday()}
                    value={date}
                    onChange={(e) => onDate(e.target.value)}
                    data-testid={`${testId}-date`}
                />
            </DialogField>
            <DialogField label={t('p1.followUps.dueTime')} required>
                <Input
                    type="time"
                    size="sm"
                    value={time}
                    onChange={(e) => onTime(e.target.value)}
                    data-testid={`${testId}-time`}
                />
            </DialogField>
        </div>
    )
}

/** Short summary of the follow-up being acted on, at the top of a dialog. */
export const FollowUpSummary = ({
    caseName,
    accountNumber,
    children,
}: {
    caseName?: string | null
    accountNumber?: string | null
    children?: ReactNode
}) => (
    <div className="mb-4 rounded-lg border border-gray-300 bg-gray-50 px-4 py-3 text-[13px] dark:border-night-border dark:bg-night-raised">
        {caseName && (
            <div className="mb-1 font-extrabold text-navy-600 dark:text-gray-100">
                {caseName}
                {accountNumber && (
                    <span className="ms-2 text-[12px] font-normal tabular-nums text-gray-600">
                        <bdi dir="ltr">{accountNumber}</bdi>
                    </span>
                )}
            </div>
        )}
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-gray-700 dark:text-night-body">{children}</div>
    </div>
)
