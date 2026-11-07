/**
 * Shared helpers of the report center builders (self-service BI and the SQL builder).
 */
import classNames from 'classnames'
import type { ReactNode } from 'react'
import type { Translate } from '@/services/v2'
import { describeV2Error } from '@/services/v2'
import type { Grain, Labels, MeasureType } from '@/services/v2/reportBuilderApi'
import { isV2Error } from '@/services/v2/http'
import { formatCount, formatMoney } from '@/utils/v2/format'

export const pick = (l: Labels | undefined, ar: boolean) => (l ? (ar ? l.ar || l.en : l.en || l.ar) : '')

type KeyValue = string | number | boolean | null | undefined

/** A dimension value as shown in headers and chart labels. */
export function keyLabel(v: KeyValue, t: Translate, grain?: Grain | null): string {
    if (v === null || v === undefined || v === '') return String(t('rb.empty'))
    if (typeof v === 'boolean') return String(t(v ? 'rb.yes' : 'rb.no'))
    const s = String(v)
    if (grain && /^\d{4}-\d{2}-\d{2}/.test(s)) {
        if (grain === 'month') return s.slice(0, 7)
        if (grain === 'year') return s.slice(0, 4)
        if (grain === 'quarter') return `${s.slice(0, 4)} Q${Math.floor((Number(s.slice(5, 7)) - 1) / 3) + 1}`
        if (grain === 'week') return String(t('rb.weekOf', { d: s.slice(0, 10) }))
    }
    return s
}

export function measureValue(v: string | number | null | undefined, type: MeasureType): string {
    if (v === null || v === undefined) return '—'
    if (type === 'money') return formatMoney(String(v), 2)
    if (type === 'int') return formatCount(Number(v))
    return String(v)
}

export const toNumber = (v: string | number | null | undefined) => (v === null || v === undefined ? 0 : Number(v) || 0)

/** Error text for builder errors: the API's stable codes first, then the generic v2 text. */
export function describeBuilderError(e: unknown, t: Translate): string {
    if (isV2Error(e)) {
        const known = [
            'query_timeout', 'sql_runner_not_configured', 'sql_runner_unavailable', 'sql_reader_misconfigured',
            'sql_requires_all_scope', 'sql_multiple_statements', 'sql_not_select', 'sql_permission_denied',
            'sql_required', 'sql_too_long', 'sql_syntax', 'validation_failed', 'not_owner', 'revision_conflict',
        ]
        if (e.code === 'sql_error') return String(t('rb.err.sql_error', { msg: e.serverMessage }))
        if (known.includes(e.code)) return String(t(`rb.err.${e.code}`))
    }
    return describeV2Error(e, t)
}

/** A chip (field) in the BI zones and the dictionary. */
export const Chip = ({
    children,
    tone = 'dim',
    className,
    ...rest
}: { children: ReactNode; tone?: 'dim' | 'measure' | 'filter'; className?: string } & React.HTMLAttributes<HTMLSpanElement>) => (
    <span
        {...rest}
        className={classNames(
            'inline-flex max-w-full items-center gap-1 rounded-md border px-2 py-1 text-[12.5px] font-bold',
            tone === 'dim' && 'border-azure-100 bg-info-tint text-navy-600 dark:border-night-border dark:bg-night-raised dark:text-gray-100',
            tone === 'measure' && 'border-success/30 bg-success-tint text-gray-800 dark:border-night-border dark:bg-night-raised dark:text-night-body',
            tone === 'filter' && 'border-warning/30 bg-warning-tint text-gray-800 dark:border-night-border dark:bg-night-raised dark:text-night-body',
            className
        )}
    >
        {children}
    </span>
)
