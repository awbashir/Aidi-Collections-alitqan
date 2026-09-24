/**
 * One report cell, rendered by the column's type from the definition:
 * money exactly as returned, instants in Asia/Riyadh, civil dates as they are,
 * enum labels in the current language, and protected employee fields shown as
 * "Hidden" when the API redacted them (null for a viewer without the right).
 */
import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { Lock } from 'lucide-react'
import StatusPill from '@/components/shared/StatusPill'
import type { StatusTone } from '@/components/shared/StatusPill'
import { CivilDate, Ltr, MoneyText, RiyadhTime } from '@/components/v2/Display'
import { formatCount } from '@/utils/v2/format'
import { caseLink } from '@/utils/v2/links'
import type { ReportCell, ReportColumnDef } from '@/services/v2'
import { pick } from '../reportUtils'

/** Enum values that read as a status get a pill (tone + glyph); others are plain labels. */
const STATUS_COLUMNS = new Set(['status', 'state', 'follow_up_status', 'pbx_status', 'attribution'])
const TONE: Record<string, StatusTone> = {
    pending: 'warning',
    approved: 'success',
    rejected: 'danger',
    reversed: 'neutral',
    open: 'info',
    overdue: 'danger',
    fulfilled: 'success',
    cancelled: 'neutral',
    late: 'danger',
    completed: 'success',
    superseded: 'neutral',
    untracked: 'neutral',
    answered: 'success',
    no_answer: 'warning',
    busy: 'warning',
    failed: 'danger',
    other: 'neutral',
    unknown: 'neutral',
    active: 'success',
    inactive: 'neutral',
    case: 'success',
    ambiguous: 'warning',
    unmatched: 'neutral',
}

/** A protected employee field the API returned as null because of redaction. */
export const HiddenValue = () => {
    const { t } = useTranslation()
    return (
        <span
            className="inline-flex items-center gap-1 whitespace-nowrap rounded-pill bg-slate-100 px-2 py-[1px] text-[12px] font-extrabold text-gray-600 dark:bg-night-raised"
            title={String(t('p4.cell.hiddenTitle'))}
            data-testid="p4-hidden"
        >
            <Lock aria-hidden size={12} strokeWidth={2} />
            {t('p4.cell.hidden')}
        </span>
    )
}

const Dash = () => <span className="text-gray-500">—</span>

export function ReportCellView({
    col,
    value,
    canSeeProtected,
}: {
    col: ReportColumnDef
    value: ReportCell | undefined
    canSeeProtected: boolean
}) {
    const { t, i18n } = useTranslation()
    const ar = (i18n.language || 'ar').startsWith('ar')
    if (value === null || value === undefined || value === '') {
        if (col.protected && !canSeeProtected) return <HiddenValue />
        return <Dash />
    }
    switch (col.type) {
        case 'money':
            return <MoneyText value={String(value)} />
        case 'int':
            return (
                <span dir="ltr" className="tabular-nums whitespace-nowrap">
                    {typeof value === 'number' ? formatCount(value) : String(value)}
                </span>
            )
        case 'date':
            return <CivilDate value={String(value)} />
        case 'datetime':
            return <RiyadhTime iso={String(value)} />
        case 'bool':
            return <span className="whitespace-nowrap">{value ? t('p4.bool.yes') : t('p4.bool.no')}</span>
        case 'enum': {
            const v = String(value)
            const choice = col.enum?.find((c) => c.value === v)
            const label = choice ? pick(choice.labels, ar) : v
            if (STATUS_COLUMNS.has(col.key) && TONE[v]) {
                return (
                    <StatusPill tone={TONE[v]} size="row">
                        {label}
                    </StatusPill>
                )
            }
            return <span className="whitespace-nowrap">{label}</span>
        }
        case 'uuid': {
            const v = String(value)
            if (col.key === 'case_id') {
                return (
                    <Link to={caseLink(v)} className="font-mono text-[12px] text-azure-600 underline-offset-2 hover:underline" title={v} dir="ltr">
                        {v.slice(0, 8)}
                    </Link>
                )
            }
            return (
                <Ltr className="font-mono text-[12px] text-gray-600" title={v}>
                    {v.slice(0, 8)}
                </Ltr>
            )
        }
        case 'json':
            return <span className="font-mono text-[12px]">{JSON.stringify(value)}</span>
        default: {
            const text = String(value)
            return (
                <span dir="auto" className="inline-block max-w-[320px] truncate align-bottom" title={text.length > 40 ? text : undefined}>
                    {text}
                </span>
            )
        }
    }
}
