/**
 * Small pieces shared by the history screens (corrections, notes, contacts).
 */
import classNames from 'classnames'
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { HiOutlineExclamation, HiOutlineInformationCircle } from 'react-icons/hi'
import { useAppSelector } from '@/store'
import { lookups } from '@/services/v2'
import type { Actor, Correction, CorrectionKind } from '@/services/v2'
import { Ltr, MoneyText } from '@/components/v2/Display'
import type { ReactNode } from 'react'

/** Signed-in user id (self-review is hidden; the API refuses it too). */
export function useCurrentUserId(): string {
    return useAppSelector((state) => state.auth.user.id) || ''
}

/** A bordered notice inside a panel. Tones follow the status palette. */
export const Banner = ({
    tone = 'info',
    title,
    children,
    action,
    testId,
    className,
}: {
    tone?: 'info' | 'warning' | 'danger'
    title?: ReactNode
    children?: ReactNode
    action?: ReactNode
    testId?: string
    className?: string
}) => (
    <div
        role={tone === 'info' ? 'note' : 'alert'}
        data-testid={testId}
        className={classNames(
            'flex items-start gap-2 rounded-md border px-3 py-2 text-[13px]',
            tone === 'info' && 'border-azure-100 bg-info-tint text-info',
            tone === 'warning' && 'border-warning-100 bg-warning-tint text-warning-700',
            tone === 'danger' && 'border-red-200 bg-danger-tint text-danger',
            'dark:border-night-border dark:bg-night-raised',
            className
        )}
    >
        {tone === 'info' ? (
            <HiOutlineInformationCircle aria-hidden className="mt-0.5 shrink-0 text-[16px]" />
        ) : (
            <HiOutlineExclamation aria-hidden className="mt-0.5 shrink-0 text-[16px]" />
        )}
        <div className="min-w-0 flex-1 break-words">
            {title && <div className="font-extrabold">{title}</div>}
            {children}
            {action && <div className="mt-1.5 flex flex-wrap gap-2">{action}</div>}
        </div>
    </div>
)

/** Name of an actor, or an honest "unknown (legacy)" when the API has none. */
export const ActorName = ({ actor, source }: { actor: Actor | undefined; source?: string | null }) => {
    const { t } = useTranslation()
    if (actor) return <span>{actor.name}</span>
    return (
        <span className="text-gray-500" title={source || undefined}>
            {source === 'db_trigger' ? t('p1.hist.actorLegacy') : t('p1.hist.actorUnknown')}
        </span>
    )
}

/** Delinquency reason names, loaded once (lookups are cached per session). */
export function useDelinquencyReasons() {
    const [options, setOptions] = useState<{ value: string; label: string }[]>([])
    const [loading, setLoading] = useState(true)
    useEffect(() => {
        let alive = true
        lookups
            .delinquencyReasons()
            .then((o) => alive && setOptions(o))
            .catch(() => alive && setOptions([]))
            .finally(() => alive && setLoading(false))
        return () => {
            alive = false
        }
    }, [])
    return { options, loading }
}

/** Label of a changed field: attribute keys are shown as they are stored. */
export const FieldLabel = ({ kind, field }: { kind: CorrectionKind; field: string }) => {
    const { t } = useTranslation()
    if (kind === 'attribute') {
        return (
            <span dir="auto" className="font-extrabold">
                {field}
            </span>
        )
    }
    return <span className="font-extrabold">{t(`p1.fields.${field}`, { defaultValue: field })}</span>
}

const isEmptyValue = (v: unknown) => v === null || v === undefined || v === ''

/** One before / after / current value, formatted for its field. */
export const CorrectionValue = ({
    kind,
    field,
    value,
    reasons,
}: {
    kind: CorrectionKind
    field: string
    value: unknown
    reasons?: { value: string; label: string }[]
}) => {
    const { t } = useTranslation()
    if (isEmptyValue(value)) {
        return <span className="italic text-gray-500">{t('p1.corr.emptyValue')}</span>
    }
    if (kind === 'original_debt' && field === 'amount') {
        return <MoneyText value={String(value)} />
    }
    if (kind === 'delinquency') {
        const name = reasons?.find((r) => r.value === value)?.label
        return (
            <span dir="auto" title={String(value)}>
                {name || <Ltr className="text-[12px] text-gray-600">{String(value)}</Ltr>}
            </span>
        )
    }
    if (kind === 'identity') {
        return <Ltr>{String(value)}</Ltr>
    }
    if (typeof value === 'boolean') {
        return <span>{value ? t('p1.corr.true') : t('p1.corr.false')}</span>
    }
    if (typeof value === 'number') {
        return <Ltr>{String(value)}</Ltr>
    }
    if (typeof value === 'object') {
        return (
            <Ltr className="font-mono text-[12px]">
                {JSON.stringify(value)}
            </Ltr>
        )
    }
    return <span dir="auto">{String(value)}</span>
}

/** "name: A → B · city: removed" in one line, for list rows. */
export const ChangeSummary = ({
    correction,
    reasons,
    max = 3,
}: {
    correction: Pick<Correction, 'kind' | 'changes'>
    reasons?: { value: string; label: string }[]
    max?: number
}) => {
    const { t } = useTranslation()
    const entries = Object.entries(correction.changes || {})
    const shown = entries.slice(0, max)
    return (
        <div className="flex min-w-0 flex-col gap-0.5 text-[12.5px]" data-testid="p1-corr-summary">
            {shown.map(([field, change]) => (
                <div key={field} className="flex min-w-0 flex-wrap items-center gap-x-1.5">
                    <FieldLabel kind={correction.kind} field={field} />
                    <span className="text-gray-400">:</span>
                    <span className="text-gray-600 line-through decoration-gray-400">
                        <CorrectionValue kind={correction.kind} field={field} value={change.before} reasons={reasons} />
                    </span>
                    <span aria-hidden className="inline-block text-gray-400 rtl:rotate-180">
                        →
                    </span>
                    <span className="text-navy-600 dark:text-gray-100">
                        {correction.kind === 'attribute' && change.after === null ? (
                            <span className="italic text-danger">{t('p1.corr.removed')}</span>
                        ) : (
                            <CorrectionValue kind={correction.kind} field={field} value={change.after} reasons={reasons} />
                        )}
                    </span>
                </div>
            ))}
            {entries.length > max && (
                <span className="text-gray-500">{t('p1.corr.moreFields', { count: entries.length - max })}</span>
            )}
        </div>
    )
}

/** The contact type icon names the legacy panel used. */
export const buildWhatsAppLink = (rawNumber: string): string | null => {
    const digits = (rawNumber || '').replace(/\D/g, '')
    if (digits.length < 9) return null
    const last9 = digits.slice(-9)
    return `https://wa.me/966${last9}`
}
