/**
 * Shell integration of the in-app softphone: the header status chip and the docked panel.
 * Rendered in the header of every layout; it renders nothing unless the signed-in user holds
 * `calls_make` (the credential route's key). One engine per tab; the tabs decide which one
 * registers (Web Locks), the others show its status only.
 *
 * Keyboard shortcuts (Alt+Shift, outside text fields): P panel, A answer, E end / reject,
 * M mute, H hold / resume.
 */
import classNames from 'classnames'
import { useEffect, useMemo, useState } from 'react'
import { createPortal } from 'react-dom'
import { useTranslation } from 'react-i18next'
import { Phone, PhoneCall } from 'lucide-react'
import { useAppSelector } from '@/store'
import { disposeSoftphoneEngine, softphoneEngine, useSoftphoneSnapshot } from '@/services/softphone'
import type { Snapshot, SoftphoneEngine } from '@/services/softphone'
import SoftphonePanel from './SoftphonePanel'
import { STATUS_TONE, statusLabel } from './labels'

const GLYPH: Record<string, string> = {
    success: 'h-[7px] w-[7px] rounded-full bg-success',
    info: 'h-[7px] w-[7px] rounded-full border-2 border-info',
    warning: 'h-[7px] w-[7px] rotate-45 bg-warning',
    danger: 'h-[2px] w-[8px] bg-danger',
    neutral: 'h-[7px] w-[7px] rounded-full bg-gray-500',
}

function useShortcuts(engine: SoftphoneEngine, snap: Snapshot, togglePanel: () => void) {
    useEffect(() => {
        const onKey = (e: KeyboardEvent) => {
            if (!e.altKey || !e.shiftKey || e.ctrlKey || e.metaKey) return
            const target = e.target as HTMLElement | null
            if (target && (target.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName))) return
            const leader = snap.role !== 'follower'
            const current = snap.lines.find((l) => l.id === snap.activeLineId && l.state !== 'ended')
            const ringing = snap.lines.find((l) => l.state === 'ringing')
            let handled = true
            switch (e.code) {
                case 'KeyP':
                    togglePanel()
                    break
                case 'KeyA':
                    if (leader && ringing) engine.answer(ringing.id)
                    break
                case 'KeyE':
                    if (!leader) break
                    if (current && current.state !== 'ringing') engine.hangup(current.id)
                    else if (ringing) engine.reject(ringing.id)
                    break
                case 'KeyM':
                    if (leader && current) engine.toggleMute(current.id)
                    break
                case 'KeyH':
                    if (leader && current) engine.toggleHold(current.id)
                    break
                default:
                    handled = false
            }
            if (handled) e.preventDefault()
        }
        window.addEventListener('keydown', onKey)
        return () => window.removeEventListener('keydown', onKey)
    }, [engine, snap, togglePanel])
}

const SoftphoneRoot = () => {
    const { t } = useTranslation()
    const engine = useMemo(() => softphoneEngine(), [])
    useEffect(() => {
        engine.start()
        return () => disposeSoftphoneEngine()
    }, [engine])
    const snap = useSoftphoneSnapshot(engine)
    const [open, setOpen] = useState(false)
    useEffect(() => {
        if (snap.openRequest > 0 && snap.role !== 'follower') setOpen(true)
    }, [snap.openRequest, snap.role])
    const toggle = useMemo(() => () => setOpen((o) => !o), [])
    useShortcuts(engine, snap, toggle)

    const tone = STATUS_TONE[snap.status]
    const liveLines = snap.lines.filter((l) => l.state !== 'ended')
    const ringing = liveLines.some((l) => l.state === 'ringing')
    const Icon = liveLines.length ? PhoneCall : Phone
    return (
        <>
            <button
                type="button"
                data-testid="softphone-chip"
                data-status={snap.status}
                data-role={snap.role}
                aria-expanded={open}
                aria-label={`${t('sp.title')}: ${statusLabel(t, snap.status)}`}
                title={t('sp.chipTitle') as string}
                onClick={toggle}
                className={classNames(
                    'me-2 inline-flex h-9 items-center gap-2 rounded-full border px-3 text-[12.5px] font-extrabold focus:outline-none focus-visible:ring-2 focus-visible:ring-azure-500 focus-visible:ring-offset-2',
                    ringing
                        ? 'border-warning bg-warning-tint text-warning'
                        : 'border-slate-200 bg-white text-navy-600 hover:bg-slate-50 dark:border-night-border dark:bg-night-card dark:text-night-strong'
                )}
            >
                <Icon size={15} aria-hidden />
                <span aria-hidden className={classNames('inline-block shrink-0', GLYPH[tone])} />
                <span className="hidden sm:inline">{statusLabel(t, snap.status)}</span>
                {snap.extension && (
                    <span className="hidden tabular-nums text-gray-500 md:inline" dir="ltr">
                        {snap.extension}
                    </span>
                )}
                {liveLines.length > 0 && (
                    <span className="rounded-full bg-navy-600 px-1.5 text-[11px] text-white tabular-nums" data-testid="softphone-chip-lines">
                        {liveLines.length}
                    </span>
                )}
                {snap.role === 'follower' && <span className="sr-only">{t('sp.followerShort')}</span>}
            </button>
            {open && createPortal(<SoftphonePanel engine={engine} snap={snap} onClose={() => setOpen(false)} />, document.body)}
        </>
    )
}

const SoftphoneShell = () => {
    const signedIn = useAppSelector((s) => s.auth.session.signedIn)
    const permissions = useAppSelector((s) => s.auth.user.permissions) as Record<string, unknown> | undefined
    const userId = useAppSelector((s) => s.auth.user.id)
    if (!signedIn || permissions?.calls_make !== true) return null
    // a new user in this tab starts a fresh engine
    return <SoftphoneRoot key={userId || 'me'} />
}

export default SoftphoneShell
