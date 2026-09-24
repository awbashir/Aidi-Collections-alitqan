/**
 * WebRTC diagnostics (§3.2): HTTPS, WebRTC support, microphone permission, WebSocket,
 * registration, credential expiry, ICE, codecs and which tab holds the phone. Values come from
 * the engine's snapshot; nothing here shows or stores the credential.
 */
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Headphones, RefreshCw } from 'lucide-react'
import StatusPill from '@/components/shared/StatusPill'
import type { StatusTone } from '@/components/shared/StatusPill'
import type { Snapshot, SoftphoneEngine } from '@/services/softphone'
import { btnSecondary } from './LineCard'
import { clock } from './labels'

const Row = ({ id, label, tone, value, detail }: { id: string; label: string; tone: StatusTone; value: string; detail?: string | null }) => (
    <div data-testid={`sp-diag-${id}`} data-tone={tone} className="flex items-start justify-between gap-3 border-b border-slate-100 py-2 last:border-0 dark:border-night-border">
        <span className="text-[13px] text-gray-700 dark:text-night-body">{label}</span>
        <span className="flex flex-col items-end gap-0.5 text-end">
            <StatusPill tone={tone}>{value}</StatusPill>
            {detail && (
                <span className="max-w-[210px] break-all text-[11.5px] tabular-nums text-gray-500" dir="ltr">
                    {detail}
                </span>
            )}
        </span>
    </div>
)

const DiagnosticsTab = ({ engine, snap }: { engine: SoftphoneEngine; snap: Snapshot }) => {
    const { t } = useTranslation()
    const d = snap.diagnostics
    const [now, setNow] = useState(Date.now())
    const [echoError, setEchoError] = useState<string | null>(null)
    useEffect(() => {
        const timer = setInterval(() => setNow(Date.now()), 1000)
        return () => clearInterval(timer)
    }, [])
    const leader = snap.role !== 'follower'
    const expiresIn = d.credentialExpiresAt ? Date.parse(d.credentialExpiresAt) - now : null
    const yes = t('sp.diag.yes')
    const no = t('sp.diag.no')
    return (
        <div data-testid="sp-diagnostics">
            <Row id="https" label={t('sp.diag.https')} tone={d.secureContext ? 'success' : 'danger'} value={d.secureContext ? yes : no} />
            <Row id="webrtc" label={t('sp.diag.webrtc')} tone={d.webrtc ? 'success' : 'danger'} value={d.webrtc ? yes : no} />
            <Row
                id="mic"
                label={t('sp.diag.mic')}
                tone={d.micPermission === 'granted' ? 'success' : d.micPermission === 'denied' ? 'danger' : 'warning'}
                value={t(`sp.diag.micState.${d.micPermission}`)}
            />
            <Row
                id="websocket"
                label={t('sp.diag.websocket')}
                tone={d.websocket === 'connected' ? 'success' : d.websocket === 'connecting' ? 'info' : 'neutral'}
                value={t(`sp.diag.wsState.${d.websocket}`)}
                detail={d.wssUrl}
            />
            <Row
                id="registration"
                label={t('sp.diag.registration')}
                tone={d.registration === 'registered' ? 'success' : d.registration === 'failed' ? 'danger' : 'neutral'}
                value={t(`sp.diag.regState.${d.registration}`)}
                detail={[d.lastRegistrationCode ? `SIP ${d.lastRegistrationCode}` : null, d.lastRegistrationCause].filter(Boolean).join(' · ') || null}
            />
            <Row
                id="credential"
                label={t('sp.diag.credential')}
                tone={expiresIn === null ? 'neutral' : expiresIn > 0 ? 'success' : 'warning'}
                value={expiresIn === null ? t('sp.diag.none') : expiresIn > 0 ? t('sp.diag.expiresIn', { time: clock(expiresIn) }) : t('sp.diag.expired')}
                detail={t('sp.diag.issued', { n: d.credentialIssues })}
            />
            <Row
                id="ice"
                label={t('sp.diag.ice')}
                tone={!d.ice ? 'neutral' : ['connected', 'completed'].includes(d.ice.state) ? 'success' : d.ice.state === 'failed' ? 'danger' : 'info'}
                value={d.ice ? d.ice.state : t('sp.diag.noCall')}
                detail={d.ice?.candidateType ? t('sp.diag.candidate', { type: d.ice.candidateType }) : null}
            />
            <Row
                id="codecs"
                label={t('sp.diag.codecs')}
                tone={d.codecs.supported.some((c) => c === 'PCMU' || c === 'PCMA') ? 'success' : 'warning'}
                value={d.codecs.negotiated || d.codecs.supported.join(', ') || t('sp.diag.unknown')}
                detail={d.codecs.negotiated ? t('sp.diag.supported', { list: d.codecs.supported.join(', ') }) : null}
            />
            <Row
                id="tab"
                label={t('sp.diag.tab')}
                tone={leader ? 'success' : 'info'}
                value={leader ? t('sp.diag.thisTab') : t('sp.diag.otherTab')}
                detail={t(`sp.diag.coordination.${engine.coordination}`)}
            />
            {d.lastError && <Row id="error" label={t('sp.diag.lastError')} tone="danger" value={t(`sp.error.${d.lastError}`, d.lastError)} />}
            {leader && (
                <div className="mt-3 flex flex-wrap gap-2">
                    <button type="button" data-testid="sp-reconnect" className={btnSecondary} onClick={() => void engine.restart()}>
                        <RefreshCw size={15} aria-hidden />
                        {t('sp.diag.reconnect')}
                    </button>
                    {snap.echoTestExtension && (
                        <button
                            type="button"
                            data-testid="sp-echo-test"
                            className={btnSecondary}
                            disabled={snap.status !== 'registered'}
                            onClick={() => {
                                setEchoError(null)
                                engine.callInternal(snap.echoTestExtension as string).catch((e) => setEchoError((e as { code?: string }).code || 'error'))
                            }}
                        >
                            <Headphones size={15} aria-hidden />
                            {t('sp.diag.echoTest')}
                        </button>
                    )}
                </div>
            )}
            {echoError && <div className="mt-2 text-[12px] text-danger">{t(`sp.error.${echoError}`, echoError)}</div>}
            <p className="mt-3 text-[11.5px] text-gray-500">{t('sp.diag.privacy')}</p>
        </div>
    )
}

export default DiagnosticsTab
