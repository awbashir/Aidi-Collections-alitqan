/**
 * Error boundary for one card or section.
 *
 * A render error inside `children` (a field the API did not send, a lookup that missed) is
 * caught here and replaced by a small "this card could not be shown" panel with a retry
 * button, so one broken card can never take the whole page down (2026-10-20: the home page
 * went blank for everyone because one card read `.name` of a missing contract).
 *
 * `resetKeys`: when any of them changes (new data, a filter), the boundary clears itself and
 * renders `children` again.
 */
import { Component } from 'react'
import type { ErrorInfo, ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { HiOutlineExclamation, HiOutlineRefresh } from 'react-icons/hi'
import classNames from 'classnames'

type Props = {
    children: ReactNode
    /** Shown in the fallback and the console, e.g. the card's title. */
    name?: string
    resetKeys?: unknown[]
    className?: string
    /** Replace the default fallback panel. */
    fallback?: (reset: () => void) => ReactNode
}

type State = { error: Error | null }

const Fallback = ({ name, onRetry, className }: { name?: string; onRetry: () => void; className?: string }) => {
    const { t } = useTranslation()
    return (
        <div
            role="alert"
            data-testid="card-error"
            className={classNames(
                'flex min-h-[120px] flex-col items-center justify-center gap-2 rounded-lg border border-gray-300 bg-white p-5 text-center dark:border-night-border dark:bg-night-card',
                className
            )}
        >
            <HiOutlineExclamation aria-hidden className="text-[20px] text-danger" />
            <div className="text-[13.5px] font-extrabold text-navy-600 dark:text-gray-100">
                {t('p10.homedash.cardError', { defaultValue: 'This card could not be shown' })}
            </div>
            {name && <div className="text-[12px] text-gray-600 dark:text-night-muted">{name}</div>}
            <button
                type="button"
                onClick={onRetry}
                className="inline-flex items-center gap-1 text-[12.5px] font-extrabold text-azure-600 hover:underline"
            >
                <HiOutlineRefresh aria-hidden />
                {t('p10.homedash.retry', { defaultValue: 'Retry' })}
            </button>
        </div>
    )
}

class ErrorBoundary extends Component<Props, State> {
    state: State = { error: null }

    static getDerivedStateFromError(error: Error): State {
        return { error }
    }

    componentDidCatch(error: Error, info: ErrorInfo) {
        // eslint-disable-next-line no-console
        console.error(`[ErrorBoundary${this.props.name ? `: ${this.props.name}` : ''}]`, error, info?.componentStack)
    }

    componentDidUpdate(prev: Props) {
        if (!this.state.error) return
        const a = prev.resetKeys || []
        const b = this.props.resetKeys || []
        if (a.length !== b.length || a.some((v, i) => !Object.is(v, b[i]))) this.reset()
    }

    reset = () => this.setState({ error: null })

    render() {
        if (this.state.error) {
            return this.props.fallback ? this.props.fallback(this.reset) : <Fallback name={this.props.name} onRetry={this.reset} className={this.props.className} />
        }
        return this.props.children
    }
}

export default ErrorBoundary
