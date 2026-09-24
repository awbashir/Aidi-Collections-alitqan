/**
 * The docked live-call panel (Phase 5): mounted once in the app shell, for users who hold
 * `calls` or `calls_make`. It shows the call the collector just placed (click-to-call), a
 * campaign call dispatched to them, or any attempt that still holds their reservation
 * (`GET /v2/me/calls/active`, polled every 15 s while the tab is visible), with the live
 * view polled at the API's pace. When the call ends it opens the wrap-up by itself, once.
 *
 * It sits on the inline-end edge at the bottom and moves beside the softphone panel when
 * that one is open, so both stay usable; it stays under modal dialogs (their scrim is
 * z-30). Closing a live or unwrapped call only minimizes it.
 */
import classNames from 'classnames'
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ChevronDown, ChevronUp, PhoneCall, X } from 'lucide-react'
import { callsApi } from '@/services/v2'
import { useAppSelector } from '@/store'
import { usePermissions } from '@/utils/v2/hooks'
import { CallStateBadge } from '../parts'
import WrapupDialog from '../wrapup/WrapupDialog'
import LiveCallPanel from './LiveCallPanel'
import { CALL_WRAPPED_EVENT, callDock, useCallDock } from './callDock'
import type { CallWrappedDetail } from './callDock'
import { useLiveCall } from './useLiveCall'

const IDLE_POLL_MS = 15000

/** True while the softphone's docked panel (a body portal) is open. */
function useSoftphonePanelOpen(): boolean {
    const [open, setOpen] = useState(false)
    useEffect(() => {
        const check = () => setOpen(!!document.querySelector('[data-testid="softphone-panel"]'))
        check()
        const obs = new MutationObserver(check)
        obs.observe(document.body, { childList: true })
        return () => obs.disconnect()
    }, [])
    return open
}

const DockBody = () => {
    const { t } = useTranslation()
    const dock = useCallDock()
    const live = useLiveCall(dock.attemptId, callDock.seedFor(dock.attemptId))
    const view = live.view
    const softphoneOpen = useSoftphonePanelOpen()

    /* A wrap-up was saved (here or on another screen): show the wrapped call. */
    const reload = live.reload
    useEffect(() => {
        const on = (e: Event) => {
            const d = (e as CustomEvent<CallWrappedDetail>).detail
            if (d && d.attemptId === callDock.get().attemptId) reload()
        }
        window.addEventListener(CALL_WRAPPED_EVENT, on)
        return () => window.removeEventListener(CALL_WRAPPED_EVENT, on)
    }, [reload])

    /* The call ended and may be wrapped up: open the wrap-up once, by itself. */
    useEffect(() => {
        if (view && view.actions.can_wrapup) callDock.openWrapup(view.id, true)
    }, [view])

    if (!dock.attemptId) return null
    const busy = !!view && (view.live || view.state === 'uncertain' || view.wrapup_pending || view.actions.can_wrapup)
    const close = () => (busy ? callDock.minimize(true) : callDock.close())

    return (
        <aside
            data-testid="p5-dock"
            data-state={view?.state || ''}
            data-minimized={dock.minimized ? '1' : '0'}
            aria-label={String(t('p5.dock.title'))}
            className={classNames(
                'fixed bottom-4 z-[25] w-[400px] max-w-[calc(100vw-2rem)] rounded-lg border border-gray-300 bg-white shadow-[0_8px_24px_rgba(7,10,75,0.10)] dark:border-night-border dark:bg-night-card',
                softphoneOpen ? 'end-[396px]' : 'end-4'
            )}
        >
            <header className="flex items-center justify-between gap-2 border-b border-gray-300 px-4 py-2.5 dark:border-night-border">
                <button
                    type="button"
                    className="flex min-w-0 items-center gap-2 text-start"
                    onClick={() => callDock.minimize(!dock.minimized)}
                    aria-expanded={!dock.minimized}
                    data-testid="p5-dock-toggle"
                >
                    <PhoneCall size={16} aria-hidden className="shrink-0 text-navy-600 dark:text-gray-100" />
                    <span className="truncate text-[14px] font-extrabold text-navy-600 dark:text-gray-100">
                        {dock.minimized && view?.case?.name ? view.case.name : t('p5.dock.title')}
                    </span>
                    {view && <CallStateBadge state={view.state} size="row" />}
                </button>
                <span className="flex items-center gap-1">
                    <button
                        type="button"
                        aria-label={String(dock.minimized ? t('p5.dock.expand') : t('p5.dock.minimize'))}
                        onClick={() => callDock.minimize(!dock.minimized)}
                        className="inline-flex h-8 w-8 items-center justify-center rounded-md text-gray-600 hover:bg-gray-100 dark:hover:bg-night-raised"
                    >
                        {dock.minimized ? <ChevronUp size={16} aria-hidden /> : <ChevronDown size={16} aria-hidden />}
                    </button>
                    <button
                        type="button"
                        aria-label={String(t('p5.dock.close'))}
                        onClick={close}
                        data-testid="p5-dock-close"
                        className="inline-flex h-8 w-8 items-center justify-center rounded-md text-gray-600 hover:bg-gray-100 dark:hover:bg-night-raised"
                    >
                        <X size={16} aria-hidden />
                    </button>
                </span>
            </header>
            {!dock.minimized && (
                <div className="max-h-[calc(100vh-9rem)] overflow-y-auto px-4 py-3">
                    {view ? (
                        <LiveCallPanel view={view} compact onChanged={live.seed} onWrapup={() => callDock.openWrapup(view.id)} />
                    ) : live.error ? (
                        <div className="text-[13px] text-danger">{t('p5.dock.loadFailed')}</div>
                    ) : (
                        <div className="text-[13px] text-gray-600">{t('p5.dock.loading')}</div>
                    )}
                </div>
            )}
        </aside>
    )
}

const LiveCallDockInner = () => {
    const dock = useCallDock()
    const userId = useAppSelector((s) => s.auth.user.id)

    /* A new user in this tab starts clean. */
    useEffect(() => () => callDock.reset(), [userId])

    /* Adopt my active attempt (placed elsewhere, a campaign call, or after a reload). */
    useEffect(() => {
        let stop = false
        let timer: ReturnType<typeof setTimeout> | null = null
        const poll = async () => {
            if (!document.hidden) {
                try {
                    const res = await callsApi.myActive()
                    if (!stop && res.attempt) callDock.adopt(res.attempt.id)
                } catch {
                    /* a transient failure: try again at the next beat */
                }
            }
            if (!stop) timer = setTimeout(poll, IDLE_POLL_MS)
        }
        void poll()
        const onFocus = () => {
            if (timer) clearTimeout(timer)
            void poll()
        }
        window.addEventListener('focus', onFocus)
        return () => {
            stop = true
            if (timer) clearTimeout(timer)
            window.removeEventListener('focus', onFocus)
        }
    }, [userId])

    return (
        <>
            <DockBody />
            <WrapupDialog
                attemptId={dock.wrapupFor}
                isOpen={!!dock.wrapupFor}
                onClose={() => callDock.closeWrapup()}
                onDone={() => callDock.closeWrapup()}
            />
        </>
    )
}

const LiveCallDock = () => {
    const perms = usePermissions()
    const signedIn = useAppSelector((s) => s.auth.session.signedIn)
    if (!signedIn || !(perms.calls || perms.calls_make)) return null
    return <LiveCallDockInner />
}

export default LiveCallDock
