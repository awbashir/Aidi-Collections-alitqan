/**
 * ‹ › previous / next client of the list the case was opened from, with "n من m"
 * (utils/v2/caseListNav.ts). Hidden when the case was not opened from a list in this tab;
 * a button is disabled at the ends of the list's page.
 */
import classNames from 'classnames'
import { useTranslation } from 'react-i18next'
import { HiChevronLeft, HiChevronRight } from 'react-icons/hi'
import type { CaseListNav } from '@/utils/v2/caseListNav'

const btn =
    'inline-flex h-8 min-w-[32px] items-center justify-center gap-1 rounded-md border border-gray-300 px-2 text-[13px] font-extrabold text-navy-600 hover:bg-gray-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-azure-500 disabled:cursor-not-allowed disabled:text-gray-400 disabled:hover:bg-transparent dark:border-night-border dark:text-gray-100 dark:hover:bg-night-raised'

const CaseListNavButtons = ({
    nav,
    tab,
    labels,
    className,
    testId = 'p8-case-nav',
}: {
    nav: CaseListNav
    /** The tab to open the other case on (default: the current one). */
    tab?: string
    /** Show "previous client" / "next client" next to the arrows. */
    labels?: boolean
    className?: string
    testId?: string
}) => {
    const { t } = useTranslation()
    if (!nav.available) return null
    /* The arrows point to the start (previous) and end (next) sides: mirrored in RTL. */
    return (
        <div className={classNames('inline-flex items-center gap-2', className)} data-testid={testId}>
            <button
                type="button"
                className={btn}
                disabled={!nav.prevId}
                onClick={() => nav.prevId && nav.go(nav.prevId, tab)}
                aria-label={String(t('p8.clientpage.prevClient'))}
                title={String(t('p8.clientpage.prevClient'))}
                data-testid={`${testId}-prev`}
            >
                <HiChevronLeft className="rtl:rotate-180" aria-hidden="true" />
                {labels && <span>{t('p8.clientpage.prevClient')}</span>}
            </button>
            <span className="whitespace-nowrap text-[12.5px] tabular-nums text-gray-600" data-testid={`${testId}-position`}>
                {t('p8.clientpage.position', { n: nav.position, m: nav.total })}
            </span>
            <button
                type="button"
                className={btn}
                disabled={!nav.nextId}
                onClick={() => nav.nextId && nav.go(nav.nextId, tab)}
                aria-label={String(t('p8.clientpage.nextClient'))}
                title={String(t('p8.clientpage.nextClient'))}
                data-testid={`${testId}-next`}
            >
                {labels && <span>{t('p8.clientpage.nextClient')}</span>}
                <HiChevronRight className="rtl:rotate-180" aria-hidden="true" />
            </button>
        </div>
    )
}

export default CaseListNavButtons
