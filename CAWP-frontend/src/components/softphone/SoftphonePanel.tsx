/**
 * The docked softphone panel (inline-end side, non-modal: the agent keeps working while on a
 * call). Tabs: Phone (lines + internal dialpad), Devices, Diagnostics. A status-only tab shows
 * the lines of the tab that holds the phone, without controls.
 */
import classNames from 'classnames'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { X } from 'lucide-react'
import StatusPill from '@/components/shared/StatusPill'
import type { Snapshot, SoftphoneEngine } from '@/services/softphone'
import DevicesTab from './DevicesTab'
import DiagnosticsTab from './DiagnosticsTab'
import Dialpad from './Dialpad'
import LineCard from './LineCard'
import { STATUS_TONE, statusLabel } from './labels'

type Tab = 'phone' | 'devices' | 'diagnostics'

const Blocked = ({ snap }: { snap: Snapshot }) => {
    const { t } = useTranslation()
    const key = snap.status === 'unsupported' ? `unsupported_${snap.statusDetail || 'no_webrtc'}` : snap.status
    return (
        <div data-testid={`softphone-blocked`} data-reason={snap.status} className="rounded-lg border border-warning/30 bg-warning-tint p-3">
            <div className="text-[14px] font-extrabold text-navy-600">{t(`sp.blocked.${key}.title`)}</div>
            <div className="mt-1 text-[12.5px] text-gray-700">{t(`sp.blocked.${key}.hint`)}</div>
        </div>
    )
}

const SoftphonePanel = ({ engine, snap, onClose }: { engine: SoftphoneEngine; snap: Snapshot; onClose: () => void }) => {
    const { t } = useTranslation()
    const [tab, setTab] = useState<Tab>('phone')
    const follower = snap.role === 'follower'
    const blocked = ['no_extension', 'not_configured', 'unsupported', 'stopped'].includes(snap.status)
    const lines = snap.lines
    const current = snap.activeLineId
    return (
        <aside
            data-testid="softphone-panel"
            data-role={snap.role}
            aria-label={t('sp.title') as string}
            className="fixed bottom-0 end-0 top-14 z-40 flex w-[380px] max-w-full flex-col border-s border-slate-200 bg-white shadow-[0_8px_24px_rgba(7,10,75,0.10)] dark:border-night-border dark:bg-night-card"
        >
            <header className="flex items-center justify-between gap-2 border-b border-slate-200 px-4 py-3 dark:border-night-border">
                <div className="min-w-0">
                    <div className="text-[15px] font-extrabold text-navy-600 dark:text-night-strong">{t('sp.title')}</div>
                    <div className="mt-0.5 flex items-center gap-2 text-[12.5px] text-gray-600">
                        <StatusPill tone={STATUS_TONE[snap.status]}>
                            <span data-testid="softphone-panel-status" data-status={snap.status}>
                                {statusLabel(t, snap.status)}
                            </span>
                        </StatusPill>
                        {snap.extension && (
                            <span className="tabular-nums" dir="ltr" data-testid="softphone-extension">
                                {t('sp.extension', { ext: snap.extension })}
                            </span>
                        )}
                    </div>
                </div>
                <button
                    type="button"
                    data-testid="softphone-close"
                    aria-label={t('sp.close') as string}
                    onClick={onClose}
                    className="inline-flex h-9 w-9 items-center justify-center rounded-md text-gray-600 hover:bg-slate-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-azure-500"
                >
                    <X size={18} aria-hidden />
                </button>
            </header>
            {follower && (
                <div data-testid="softphone-follower" className="border-b border-slate-200 bg-info-tint px-4 py-2 text-[12.5px] text-info dark:border-night-border">
                    {t('sp.followerNote')}
                </div>
            )}
            <div className="flex gap-4 border-b border-slate-200 px-4 dark:border-night-border" role="tablist" aria-label={t('sp.title') as string}>
                {(['phone', 'devices', 'diagnostics'] as Tab[]).map((k) => (
                    <button
                        key={k}
                        type="button"
                        role="tab"
                        aria-selected={tab === k}
                        data-testid={`sp-tab-${k}`}
                        onClick={() => setTab(k)}
                        className={classNames(
                            '-mb-px h-10 border-b-2 text-[13px] font-extrabold focus:outline-none focus-visible:ring-2 focus-visible:ring-azure-500',
                            tab === k ? 'border-signal-500 text-navy-600 dark:text-night-strong' : 'border-transparent text-gray-500 hover:text-navy-600'
                        )}
                    >
                        {t(`sp.tab.${k}`)}
                    </button>
                ))}
            </div>
            <div className="flex-1 overflow-y-auto px-4 py-3">
                {tab === 'phone' && (
                    <div className="flex flex-col gap-3">
                        {blocked && <Blocked snap={snap} />}
                        {!blocked && snap.status !== 'registered' && (
                            <div data-testid="softphone-not-ready" className="text-[12.5px] text-gray-600">
                                {t(`sp.statusHint.${snap.status}`)}
                                {snap.statusDetail ? <span className="ms-1 font-mono text-[11px] text-gray-400" dir="ltr">({snap.statusDetail})</span> : null}
                            </div>
                        )}
                        {lines.length > 0 ? (
                            <ul className="flex flex-col gap-2" aria-label={t('sp.lines') as string} data-testid="softphone-lines">
                                {lines.map((l) => (
                                    <LineCard
                                        key={l.id}
                                        engine={engine}
                                        line={l}
                                        current={l.id === current}
                                        controls={!follower}
                                        pattern={snap.dialPattern}
                                    />
                                ))}
                            </ul>
                        ) : (
                            !blocked && <div className="text-[12.5px] text-gray-500">{t('sp.noCalls')}</div>
                        )}
                        {!blocked && !follower && (
                            <Dialpad engine={engine} pattern={snap.dialPattern} enabled={snap.status === 'registered'} />
                        )}
                        <div className="text-[11.5px] text-gray-500">{t('sp.outboundNote')}</div>
                    </div>
                )}
                {tab === 'devices' && <DevicesTab engine={engine} editable={!follower} />}
                {tab === 'diagnostics' && <DiagnosticsTab engine={engine} snap={snap} />}
            </div>
            <footer className="border-t border-slate-200 px-4 py-2 text-[11px] text-gray-500 dark:border-night-border" data-testid="sp-shortcuts">
                {t('sp.shortcuts')}
            </footer>
        </aside>
    )
}

export default SoftphonePanel
