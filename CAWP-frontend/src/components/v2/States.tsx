/**
 * Loading, empty, error, unauthorized and conflict states for the Phase 1
 * screens. Loading is a skeleton at the real size (the layout must not jump);
 * errors say what happened and offer the one action that helps.
 */
import classNames from 'classnames'
import { useEffect, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { HiOutlineExclamation, HiOutlineLockClosed, HiOutlineRefresh, HiOutlineInbox } from 'react-icons/hi'
import Button from '@/components/ui/Button'
import Skeleton from '@/components/ui/Skeleton'
import { describeV2Error, isV2Error } from '@/services/v2'
import type { ReactNode } from 'react'

export const SkeletonLines = ({ rows = 3, className }: { rows?: number; className?: string }) => (
    <div className={classNames('flex flex-col gap-3', className)} aria-busy="true" data-testid="p1-loading">
        {Array.from({ length: rows }).map((_, i) => (
            <Skeleton key={i} height={16} width={`${90 - ((i * 17) % 40)}%`} />
        ))}
    </div>
)

/** Skeleton rows inside a `<tbody>`, at the 44px row height. */
export const SkeletonRows = ({ cols, rows = 6 }: { cols: number; rows?: number }) => (
    <>
        {Array.from({ length: rows }).map((_, r) => (
            <tr key={r} data-testid="p1-loading">
                {Array.from({ length: cols }).map((__, c) => (
                    <td key={c}>
                        <Skeleton height={12} width={c === 0 ? '70%' : '55%'} />
                    </td>
                ))}
            </tr>
        ))}
    </>
)

export const EmptyState = ({
    title,
    hint,
    action,
    className,
}: {
    title: ReactNode
    hint?: ReactNode
    action?: ReactNode
    className?: string
}) => (
    <div
        data-testid="p1-empty"
        className={classNames('flex flex-col items-center justify-center gap-2 py-10 text-center', className)}
    >
        <HiOutlineInbox aria-hidden className="text-[22px] text-gray-500" />
        <div className="text-[14px] font-extrabold text-navy-600 dark:text-gray-100">{title}</div>
        {hint && <div className="max-w-[520px] text-[13px] text-gray-600">{hint}</div>}
        {action}
    </div>
)

export const ForbiddenState = ({ keys, className }: { keys?: string[]; className?: string }) => {
    const { t } = useTranslation()
    return (
        <div
            data-testid="p1-forbidden"
            className={classNames('flex flex-col items-center justify-center gap-2 py-10 text-center', className)}
        >
            <HiOutlineLockClosed aria-hidden className="text-[22px] text-gray-500" />
            <div className="text-[14px] font-extrabold text-navy-600 dark:text-gray-100">{t('p1.state.forbiddenTitle')}</div>
            <div className="max-w-[520px] text-[13px] text-gray-600">
                {t('p1.state.forbiddenHint')}
                {keys && keys.length > 0 && (
                    <>
                        {' '}
                        <span dir="ltr" className="font-mono text-[12px]">
                            {keys.join(', ')}
                        </span>
                    </>
                )}
            </div>
        </div>
    )
}

/** Whole-panel error. A 403 renders the unauthorized state instead. */
export const ErrorState = ({
    error,
    onRetry,
    className,
}: {
    error: unknown
    onRetry?: () => void
    className?: string
}) => {
    const { t } = useTranslation()
    if (isV2Error(error) && error.status === 403) {
        const keys = (error.detail as { required_any?: string[] } | null)?.required_any
        return <ForbiddenState keys={keys} className={className} />
    }
    return (
        <div
            role="alert"
            data-testid="p1-error"
            className={classNames('flex flex-col items-center justify-center gap-2 py-10 text-center', className)}
        >
            <HiOutlineExclamation aria-hidden className="text-[22px] text-danger" />
            <div className="text-[14px] font-extrabold text-navy-600 dark:text-gray-100">{t('p1.state.errorTitle')}</div>
            <div className="max-w-[560px] text-[13px] text-gray-600">{describeV2Error(error, t)}</div>
            {onRetry && (
                <Button size="sm" icon={<HiOutlineRefresh />} onClick={onRetry}>
                    {t('p1.action.retry')}
                </Button>
            )}
        </div>
    )
}

/**
 * Error line inside a dialog or form. On a revision/stale conflict it says the
 * record changed and offers a reload; the user's draft stays in the form.
 */
export const InlineError = ({
    error,
    onReload,
    className,
}: {
    error: unknown
    onReload?: () => void
    className?: string
}) => {
    const { t } = useTranslation()
    const ref = useRef<HTMLDivElement>(null)
    /* In a tall dialog the message sits at the bottom of the scrolling body;
     * bring it (and its reload link) into view when it appears. */
    useEffect(() => {
        if (error) ref.current?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
    }, [error])
    if (!error) return null
    const conflict = isV2Error(error) && error.isConflict
    return (
        <div
            ref={ref}
            role="alert"
            data-testid={conflict ? 'p1-conflict' : 'p1-inline-error'}
            className="mt-3 flex items-start gap-2 rounded-md border border-red-200 bg-danger-tint px-3 py-2 text-[13px] text-danger dark:border-night-border dark:bg-night-raised"
        >
            <HiOutlineExclamation aria-hidden className="mt-0.5 shrink-0 text-[16px]" />
            <div className={classNames('min-w-0 flex-1', className)}>
                {conflict && <div className="font-extrabold">{t('p1.state.conflictTitle')}</div>}
                <div className="break-words">{describeV2Error(error, t)}</div>
                {conflict && onReload && (
                    <button
                        type="button"
                        className="mt-1 font-extrabold text-azure-600 underline underline-offset-2"
                        onClick={onReload}
                    >
                        {t('p1.action.reloadLatest')}
                    </button>
                )}
            </div>
        </div>
    )
}

/** A short honest note under a panel ("imported baseline", "queue authorizes nothing"). */
export const Note = ({ children, className }: { children: ReactNode; className?: string }) => (
    <p className={classNames('text-[12.5px] text-gray-600 dark:text-night-muted', className)}>{children}</p>
)
