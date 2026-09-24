/**
 * The softphone engine: our own wrapper around JsSIP (MIT, pinned in package.json). Written
 * from scratch for CAWP; no code from any other softphone.
 *
 * - Only the LEADER tab (tabs.ts) creates the user agent; followers mirror its snapshot.
 * - Credentials: `POST /v2/me/sip-credentials` issues a short-lived password. It is handed to
 *   JsSIP and dropped here; nothing is persisted. It is fetched again before it expires
 *   (`refresh_after_seconds`), on every WebSocket reconnect, and once after a 401/403.
 *   Each issue supersedes the previous credential on the PBX.
 * - Registration is driven here (`register: false` + `registrationExpiring`), so a refresh
 *   always re-registers with the current credential.
 * - Incoming calls: auto-answer ONLY when the INVITE carries `X-CAWP-Attempt` and it is my live
 *   attempt on my extension (policy.ts, checked with `GET /v2/me/calls/active`). Everything else
 *   rings on the ringer device with answer/reject. A browser "confirmed" is agent-leg evidence
 *   only; the customer answer comes from PBX events (the live view).
 * - Lines: up to 4; answering, resuming or switching auto-holds the other lines. Transfer and
 *   dialing only to internal extensions (the server's pattern). No browser-side recording.
 */
import { UA, WebSocketInterface } from 'jssip'
import type { DTMFOptions, RTCSession } from 'jssip/lib/RTCSession'
import type { IncomingRTCSessionEvent, RTCSessionEvent } from 'jssip/lib/UA'
import { isV2Error } from '../v2/http'
import { softphoneApi } from './api'
import { MicMeter, Ringer, micConstraints, microphonePermission, routeTo, supportedCodecs } from './audio'
import { autoAnswerDecision, isDtmf, isInternalTarget, parseAttemptTag } from './policy'
import { TabCoordinator } from './tabs'
import type { AutoAnswerReason, CallingDevices, Diagnostics, Line, LiveAttempt, PhoneStatus, Snapshot } from './types'

export const MAX_LINES = 4
const ENDED_LINE_VISIBLE_MS = 8000

type CredentialMeta = {
    credentialId: string
    extension: string
    sipUri: string
    domain: string
    authorizationUser: string
    displayName: string
    wssUrl: string
    iceServers: RTCIceServer[]
    expiresAt: number
    refreshAt: number
    registerExpires: number
    attemptHeader: string
}

export class SoftphoneError extends Error {
    constructor(readonly code: string) {
        super(code)
    }
}

const emptyDiagnostics = (): Diagnostics => ({
    secureContext: typeof window !== 'undefined' && window.isSecureContext === true,
    webrtc: typeof RTCPeerConnection !== 'undefined',
    webLocks: typeof navigator !== 'undefined' && !!navigator.locks,
    broadcastChannel: typeof BroadcastChannel !== 'undefined',
    micPermission: 'unknown',
    websocket: 'idle',
    registration: 'idle',
    lastRegistrationCode: null,
    lastRegistrationCause: null,
    wssUrl: null,
    credentialExpiresAt: null,
    credentialIssues: 0,
    ice: null,
    codecs: { supported: [], negotiated: null },
    lastError: null,
})

const withTimeout = <T>(p: Promise<T>, ms: number) =>
    Promise.race([p, new Promise<T>((_, reject) => setTimeout(() => reject(new SoftphoneError('timeout')), ms))])

export class SoftphoneEngine {
    private listeners = new Set<() => void>()
    private snapshot: Snapshot
    private readonly tabs: TabCoordinator
    private ua: UA | null = null
    private meta: CredentialMeta | null = null
    /** Only between the issue and the creation of the user agent (then dropped). */
    private pendingPassword: string | null = null
    private refreshing: Promise<void> | null = null
    private refreshTimer: ReturnType<typeof setTimeout> | null = null
    private retryTimer: ReturnType<typeof setTimeout> | null = null
    private statsTimer: ReturnType<typeof setInterval> | null = null
    private sessions = new Map<string, RTCSession>()
    private remoteAudio = new Map<string, HTMLAudioElement>()
    private ringer = new Ringer()
    private prefs: { autoAnswer: boolean; devices: CallingDevices; revision: number } = {
        autoAnswer: true,
        devices: { microphone: null, speaker: null, ringer: null },
        revision: 0,
    }
    private started = false
    private stopping = false
    private connectedOnce = false
    private authRetried = false
    private lineSeq = 0

    constructor() {
        this.snapshot = {
            status: 'starting',
            role: 'follower',
            extension: null,
            lines: [],
            activeLineId: null,
            diagnostics: emptyDiagnostics(),
            statusDetail: null,
            leaderTabId: null,
            openRequest: 0,
            dialPattern: null,
            echoTestExtension: null,
            autoAnswerEnabled: true,
            updatedAt: Date.now(),
        }
        this.tabs = new TabCoordinator({
            onLeader: () => void this.onLeader(),
            onFollower: () => this.set({ role: 'follower', status: this.snapshot.status }),
            onSnapshot: (snap) =>
                this.replace({ ...snap, role: 'follower', diagnostics: { ...snap.diagnostics } }),
            onHello: () => this.tabs.broadcast(this.snapshot),
        })
    }

    // ------------------------------------------------------------------ store
    subscribe = (fn: () => void) => {
        this.listeners.add(fn)
        return () => {
            this.listeners.delete(fn)
        }
    }

    getSnapshot = () => this.snapshot

    get isLeader() {
        return this.tabs.isLeader
    }

    get coordination() {
        return this.tabs.mode
    }

    get devices() {
        return this.prefs.devices
    }

    get preferencesRevision() {
        return this.prefs.revision
    }

    private replace(next: Snapshot) {
        this.snapshot = next
        this.listeners.forEach((fn) => fn())
    }

    private set(patch: Partial<Snapshot>) {
        this.replace({ ...this.snapshot, ...patch, updatedAt: Date.now() })
        if (this.tabs.isLeader) this.tabs.broadcast(this.snapshot)
    }

    private setDiag(patch: Partial<Diagnostics>) {
        this.set({ diagnostics: { ...this.snapshot.diagnostics, ...patch } })
    }

    private setStatus(status: PhoneStatus, detail: string | null = null) {
        this.set({ status, statusDetail: detail })
    }

    private line(id: string) {
        return this.snapshot.lines.find((l) => l.id === id) || null
    }

    private setLine(id: string, patch: Partial<Line>) {
        this.set({ lines: this.snapshot.lines.map((l) => (l.id === id ? { ...l, ...patch } : l)) })
    }

    private liveLines() {
        return this.snapshot.lines.filter((l) => l.state !== 'ended')
    }

    // ------------------------------------------------------------------ lifecycle
    start() {
        if (this.started) return
        this.started = true
        this.stopping = false
        window.addEventListener('pagehide', this.onPageHide)
        this.tabs.start()
    }

    /** Sign-out / unmount: hang up, unregister, forget everything, let another tab take over. */
    stop() {
        if (!this.started) return
        this.started = false
        this.stopping = true
        window.removeEventListener('pagehide', this.onPageHide)
        this.teardownUa()
        this.tabs.stop()
    }

    private onPageHide = () => {
        if (this.tabs.isLeader) this.teardownUa()
        this.tabs.stop()
    }

    private clearTimers() {
        if (this.refreshTimer) clearTimeout(this.refreshTimer)
        if (this.retryTimer) clearTimeout(this.retryTimer)
        if (this.statsTimer) clearInterval(this.statsTimer)
        this.refreshTimer = null
        this.retryTimer = null
        this.statsTimer = null
    }

    private teardownUa() {
        this.clearTimers()
        this.ringer.stop()
        this.sessions.forEach((s) => {
            try {
                if (!s.isEnded()) s.terminate()
            } catch {
                /* already gone */
            }
        })
        this.sessions.clear()
        this.remoteAudio.forEach((el) => {
            el.pause()
            el.srcObject = null
        })
        this.remoteAudio.clear()
        const ua = this.ua
        this.ua = null
        if (ua) {
            ua.removeAllListeners()
            try {
                ua.stop()
            } catch {
                /* socket already closed */
            }
        }
        this.meta = null
        this.pendingPassword = null
        this.connectedOnce = false
    }

    private async onLeader() {
        this.set({ role: this.tabs.mode === 'solo' ? 'solo' : 'leader', leaderTabId: this.tabs.tabId })
        this.setStatus('starting')
        await this.boot()
    }

    private async boot() {
        const diag = emptyDiagnostics()
        this.setDiag({
            ...diag,
            credentialIssues: this.snapshot.diagnostics.credentialIssues,
            codecs: { supported: supportedCodecs(), negotiated: null },
        })
        if (!diag.secureContext) return this.setStatus('unsupported', 'insecure_context')
        if (!diag.webrtc || !navigator.mediaDevices?.getUserMedia) return this.setStatus('unsupported', 'no_webrtc')
        await this.loadPreferences()
        await this.checkMicrophone()
        try {
            await this.refreshCredential(true)
        } catch (e) {
            this.onCredentialError(e)
            return
        }
        this.createUa()
    }

    private async loadPreferences() {
        try {
            const me = await softphoneApi.myCalling()
            this.prefs = {
                autoAnswer: me.preferences.auto_answer_attempts,
                devices: { ...me.preferences.devices },
                revision: me.preferences.revision,
            }
            this.set({ autoAnswerEnabled: this.prefs.autoAnswer })
        } catch {
            /* defaults: auto-answer on for attempt-tagged calls, default devices */
        }
    }

    /** Ask for the microphone once up front, so an auto-answered call never waits on a prompt. */
    private async checkMicrophone() {
        let state = await microphonePermission()
        if (state !== 'denied') {
            try {
                const s = await navigator.mediaDevices.getUserMedia(micConstraints(this.prefs.devices.microphone))
                s.getTracks().forEach((t) => t.stop())
                state = 'granted'
            } catch {
                state = (await microphonePermission()) === 'granted' ? 'granted' : 'denied'
            }
        }
        this.setDiag({ micPermission: state })
    }

    private onCredentialError(e: unknown) {
        const code = isV2Error(e) ? e.code : e instanceof SoftphoneError ? e.code : 'network_error'
        const status = isV2Error(e) ? e.status : 0
        this.setDiag({ lastError: code })
        if (code === 'no_extension') {
            this.teardownUa()
            return this.setStatus('no_extension', code)
        }
        if (code === 'softphone_not_configured') return this.setStatus('not_configured', code)
        if (status === 403) {
            this.teardownUa()
            return this.setStatus('stopped', code)
        }
        // network, 5xx, 429: offline for now, retry
        this.setStatus(this.ua?.isRegistered() ? 'registered' : 'offline', code)
        if (this.retryTimer) clearTimeout(this.retryTimer)
        this.retryTimer = setTimeout(() => void this.restart(), status === 429 ? 60000 : 15000)
    }

    /** Fetch a new credential when forced or due, hand it to the user agent, schedule the next. */
    private refreshCredential(force: boolean): Promise<void> {
        if (!force && this.meta && Date.now() < this.meta.refreshAt) return Promise.resolve()
        if (this.refreshing) return this.refreshing
        this.refreshing = (async () => {
            const cred = await softphoneApi.issueCredential()
            const host = cred.sip_uri.split('@')[1] || ''
            this.meta = {
                credentialId: cred.credential_id,
                extension: cred.extension,
                sipUri: cred.sip_uri,
                domain: host,
                authorizationUser: cred.authorization_user,
                displayName: cred.display_name,
                wssUrl: cred.wss_url,
                iceServers: cred.ice_servers || [],
                expiresAt: Date.parse(cred.expires_at),
                refreshAt: Date.now() + cred.refresh_after_seconds * 1000,
                registerExpires: cred.register_expires,
                attemptHeader: cred.auto_answer?.header || 'X-CAWP-Attempt',
            }
            if (this.ua) {
                this.ua.set('password', cred.password)
            } else {
                this.pendingPassword = cred.password
            }
            this.set({
                extension: cred.extension,
                dialPattern: cred.dial_policy?.internal_pattern || null,
                echoTestExtension: cred.dial_policy?.echo_test_extension || null,
                autoAnswerEnabled: this.prefs.autoAnswer && cred.auto_answer?.enabled !== false,
            })
            this.setDiag({
                wssUrl: cred.wss_url,
                credentialExpiresAt: cred.expires_at,
                credentialIssues: this.snapshot.diagnostics.credentialIssues + 1,
                lastError: null,
            })
            if (this.refreshTimer) clearTimeout(this.refreshTimer)
            this.refreshTimer = setTimeout(() => void this.refreshAndRegister(), cred.refresh_after_seconds * 1000)
        })().finally(() => {
            this.refreshing = null
        })
        return this.refreshing
    }

    private async refreshAndRegister() {
        try {
            await this.refreshCredential(true)
            if (this.ua?.isConnected()) this.ua.register()
        } catch (e) {
            this.onCredentialError(e)
        }
    }

    private createUa() {
        const meta = this.meta
        if (!meta || this.ua) return
        const socket = new WebSocketInterface(meta.wssUrl)
        const password = this.pendingPassword || undefined
        this.pendingPassword = null
        const ua = new UA({
            sockets: [socket],
            uri: meta.sipUri,
            authorization_user: meta.authorizationUser,
            password,
            display_name: meta.displayName,
            register: false,
            register_expires: meta.registerExpires,
            session_timers: false,
            user_agent: 'CAWP softphone',
            connection_recovery_min_interval: 2,
            connection_recovery_max_interval: 30,
            no_answer_timeout: 60,
        })
        this.ua = ua
        this.connectedOnce = false
        ua.on('connecting', () => {
            this.setDiag({ websocket: 'connecting' })
            if (this.snapshot.status !== 'registered') this.setStatus('connecting')
        })
        ua.on('connected', () => {
            this.setDiag({ websocket: 'connected' })
            if (!this.connectedOnce) {
                this.connectedOnce = true
                ua.register()
                return
            }
            // A reconnect: fetch a fresh credential first, then register again.
            this.setStatus('connecting', 'reconnected')
            void this.refreshAndRegister()
        })
        ua.on('disconnected', (e) => {
            this.setDiag({ websocket: 'disconnected', registration: 'unregistered' })
            if (!this.stopping) this.setStatus('offline', e?.code ? `ws_${e.code}` : 'ws_closed')
        })
        ua.on('registered', (e) => {
            this.authRetried = false
            this.setDiag({ registration: 'registered', lastRegistrationCode: e?.response?.status_code ?? 200, lastRegistrationCause: null })
            this.setStatus('registered')
        })
        ua.on('unregistered', (e) => {
            this.setDiag({ registration: 'unregistered', lastRegistrationCode: e?.response?.status_code ?? null })
        })
        ua.on('registrationFailed', (e) => {
            const code = e?.response?.status_code ?? null
            const cause = (e?.cause as string | undefined) || null
            this.setDiag({ registration: 'failed', lastRegistrationCode: code, lastRegistrationCause: cause })
            const authProblem = code === 401 || code === 403 || code === 407 || cause === 'Authentication Error' || cause === 'Rejected'
            if (authProblem && !this.authRetried) {
                // The credential expired or was superseded: fetch a new one and try once more.
                this.authRetried = true
                this.setStatus('connecting', 'credential_refresh')
                void this.refreshAndRegister()
                return
            }
            this.setStatus('failed', cause || (code ? `sip_${code}` : 'registration_failed'))
        })
        ua.on('registrationExpiring', () => {
            void (async () => {
                try {
                    await this.refreshCredential(false)
                } catch (e) {
                    this.onCredentialError(e)
                }
                if (this.ua?.isConnected()) this.ua.register()
            })()
        })
        ua.on('newRTCSession', (e: RTCSessionEvent) => this.onSession(e))
        this.setStatus('connecting')
        ua.start()
        if (this.statsTimer) clearInterval(this.statsTimer)
        this.statsTimer = setInterval(() => void this.collectStats(), 2000)
    }

    /** Diagnostics "reconnect": a new credential and a new WebSocket. */
    async restart() {
        if (!this.tabs.isLeader) return
        this.teardownUa()
        this.set({ lines: [], activeLineId: null })
        await this.boot()
    }

    // ------------------------------------------------------------------ calls
    private pcConfig(): RTCConfiguration {
        return {
            iceServers: this.meta?.iceServers || [],
            iceTransportPolicy: 'all',
            bundlePolicy: 'max-bundle',
            rtcpMuxPolicy: 'require',
        }
    }

    private onSession(e: RTCSessionEvent) {
        const session = e.session
        const incoming = e.originator === 'remote'
        if (incoming && this.liveLines().length >= MAX_LINES) {
            session.terminate({ status_code: 486, reason_phrase: 'Busy Here' })
            return
        }
        this.lineSeq += 1
        const id = `L${this.lineSeq}`
        const header = this.meta?.attemptHeader || 'X-CAWP-Attempt'
        const tag = incoming ? parseAttemptTag((e as IncomingRTCSessionEvent).request.getHeader(header)) : null
        const remoteUri = session.remote_identity?.uri
        const line: Line = {
            id,
            direction: incoming ? 'incoming' : 'outgoing',
            state: incoming ? 'ringing' : 'calling',
            remote: remoteUri?.user || '',
            remoteName: session.remote_identity?.display_name || null,
            attemptTag: tag,
            attemptId: null,
            caseId: null,
            caseName: null,
            autoAnswered: false,
            autoAnswerDecision: null,
            muted: false,
            held: false,
            startedAt: Date.now(),
            answeredAt: null,
            endedAt: null,
            endCause: null,
            transfer: null,
        }
        this.sessions.set(id, session)
        this.set({
            lines: [...this.snapshot.lines, line],
            activeLineId: incoming ? this.snapshot.activeLineId || id : id,
            openRequest: this.snapshot.openRequest + 1,
        })
        this.wire(id, session)
        if (incoming) void this.decideIncoming(id, tag)
    }

    private wire(id: string, session: RTCSession) {
        session.on('peerconnection', (ev) => {
            ev.peerconnection.addEventListener('track', (t: RTCTrackEvent) => this.attachRemote(id, t))
        })
        if (session.connection) {
            session.connection.addEventListener('track', (t: RTCTrackEvent) => this.attachRemote(id, t))
        }
        session.on('progress', () => {
            if (this.line(id)?.state === 'calling') this.setLine(id, { state: 'connecting' })
        })
        session.on('accepted', () => this.setLine(id, { state: 'active', answeredAt: Date.now() }))
        session.on('confirmed', () => {
            const l = this.line(id)
            if (l && l.state !== 'held') this.setLine(id, { state: 'active', answeredAt: l.answeredAt || Date.now() })
        })
        session.on('hold', (ev) => {
            if (ev.originator === 'local') this.setLine(id, { held: true, state: 'held' })
        })
        session.on('unhold', (ev) => {
            if (ev.originator === 'local') this.setLine(id, { held: false, state: 'active' })
        })
        session.on('muted', () => this.setLine(id, { muted: true }))
        session.on('unmuted', () => this.setLine(id, { muted: false }))
        const end = (cause: string) => {
            this.setLine(id, { state: 'ended', endedAt: Date.now(), endCause: cause })
            this.sessions.delete(id)
            const el = this.remoteAudio.get(id)
            if (el) {
                el.pause()
                el.srcObject = null
                this.remoteAudio.delete(id)
            }
            this.syncRinger()
            if (this.snapshot.activeLineId === id) {
                const next = this.liveLines().find((l) => l.id !== id)
                this.set({ activeLineId: next ? next.id : id })
            }
            setTimeout(() => {
                this.set({
                    lines: this.snapshot.lines.filter((l) => l.id !== id),
                    activeLineId: this.snapshot.activeLineId === id ? this.liveLines()[0]?.id || null : this.snapshot.activeLineId,
                })
            }, ENDED_LINE_VISIBLE_MS)
        }
        session.on('ended', (ev) => end((ev?.cause as string) || 'ended'))
        session.on('failed', (ev) => end((ev?.cause as string) || 'failed'))
    }

    private attachRemote(id: string, ev: RTCTrackEvent) {
        if (ev.track.kind !== 'audio') return
        let el = this.remoteAudio.get(id)
        if (!el) {
            el = new Audio()
            el.autoplay = true
            this.remoteAudio.set(id, el)
        }
        el.srcObject = ev.streams[0] || new MediaStream([ev.track])
        void routeTo(el, this.prefs.devices.speaker).then(() => el?.play().catch(() => undefined))
    }

    private syncRinger() {
        const ringing = this.snapshot.lines.some((l) => l.state === 'ringing' && l.direction === 'incoming')
        if (ringing && !this.ringer.ringing) void this.ringer.start(this.prefs.devices.ringer)
        if (!ringing && this.ringer.ringing) this.ringer.stop()
    }

    private async decideIncoming(id: string, tag: string | null) {
        const busy = this.snapshot.lines.some((l) => l.id !== id && l.state === 'active' && !l.held)
        let decision: AutoAnswerReason
        let active: LiveAttempt | null = null
        let checkFailed = false
        if (tag) {
            try {
                active = (await withTimeout(softphoneApi.myActiveAttempt(), 3000)).attempt
            } catch {
                checkFailed = true
            }
        }
        if (checkFailed) {
            decision = 'check_failed'
        } else {
            decision = autoAnswerDecision({
                tag,
                preferenceEnabled: this.prefs.autoAnswer,
                myExtension: this.snapshot.extension,
                active,
                busyOnAnotherLine: busy,
            })
        }
        const linked = active && tag && active.id === tag && active.extension === this.snapshot.extension
        this.setLine(id, {
            autoAnswerDecision: decision,
            attemptId: linked ? active?.id || null : null,
            caseId: linked ? active?.case?.id || null : null,
            caseName: linked ? active?.case?.name || null : null,
        })
        const session = this.sessions.get(id)
        if (!session || session.isEnded()) return
        if (decision === 'auto_answered') {
            this.answer(id, true)
        } else {
            this.syncRinger()
        }
        this.set({ openRequest: this.snapshot.openRequest + 1 })
    }

    answer(id: string, auto = false) {
        const session = this.sessions.get(id)
        const l = this.line(id)
        if (!session || !l || l.state !== 'ringing') return
        this.holdOthers(id)
        this.setLine(id, { state: 'connecting', autoAnswered: auto })
        this.set({ activeLineId: id })
        this.syncRinger()
        session.answer({ mediaConstraints: micConstraints(this.prefs.devices.microphone), pcConfig: this.pcConfig() })
        this.syncRinger()
    }

    reject(id: string) {
        const session = this.sessions.get(id)
        if (!session || this.line(id)?.state !== 'ringing') return
        session.terminate({ status_code: 603, reason_phrase: 'Decline' })
    }

    hangup(id: string) {
        const session = this.sessions.get(id)
        if (!session || session.isEnded()) return
        session.terminate()
    }

    toggleMute(id: string) {
        const session = this.sessions.get(id)
        if (!session || !session.isEstablished()) return
        if (session.isMuted().audio) session.unmute({ audio: true })
        else session.mute({ audio: true })
    }

    toggleHold(id: string) {
        const session = this.sessions.get(id)
        if (!session || !session.isEstablished()) return
        if (session.isOnHold().local) {
            this.holdOthers(id)
            session.unhold()
            this.set({ activeLineId: id })
        } else {
            session.hold()
        }
    }

    /** Switch to a line: every other line is put on hold first (auto-hold). */
    switchTo(id: string) {
        const session = this.sessions.get(id)
        if (!session) return
        this.holdOthers(id)
        if (session.isEstablished() && session.isOnHold().local) session.unhold()
        this.set({ activeLineId: id })
    }

    private holdOthers(exceptId: string) {
        this.sessions.forEach((s, lid) => {
            if (lid === exceptId) return
            try {
                if (s.isEstablished() && !s.isOnHold().local) s.hold()
            } catch {
                /* renegotiation in progress */
            }
        })
    }

    dtmf(id: string, key: string) {
        const session = this.sessions.get(id)
        if (!session || !session.isEstablished() || !isDtmf(key)) return
        session.sendDTMF(key, {
            transportType: 'RFC2833' as unknown as DTMFOptions['transportType'],
            duration: 120,
            interToneGap: 70,
        })
    }

    /** Blind transfer, internal extensions only (the PBX decides whether it allows it). */
    transfer(id: string, target: string) {
        const session = this.sessions.get(id)
        if (!session || !session.isEstablished()) throw new SoftphoneError('no_call')
        if (!isInternalTarget(target, this.snapshot.dialPattern)) throw new SoftphoneError('transfer_not_internal')
        this.setLine(id, { transfer: { target, state: 'sending' } })
        session.refer(`sip:${target}@${this.meta?.domain}`, {
            eventHandlers: {
                accepted: () => this.setLine(id, { transfer: { target, state: 'accepted' } }),
                requestFailed: (ev: { cause?: string; response?: { status_code?: number } }) =>
                    this.setLine(id, {
                        transfer: { target, state: 'failed', detail: ev?.response?.status_code ? `sip_${ev.response.status_code}` : ev?.cause || 'failed' },
                    }),
                failed: (ev: { cause?: string }) =>
                    this.setLine(id, { transfer: { target, state: 'failed', detail: ev?.cause || 'failed' } }),
            },
        })
    }

    /** Call another internal extension (or the echo test). Never an external number. */
    async callInternal(target: string) {
        if (!this.ua || !this.ua.isRegistered()) throw new SoftphoneError('not_registered')
        if (!isInternalTarget(target, this.snapshot.dialPattern)) throw new SoftphoneError('not_internal')
        if (this.liveLines().length >= MAX_LINES) throw new SoftphoneError('too_many_lines')
        try {
            await this.refreshCredential(false)
        } catch (e) {
            this.onCredentialError(e)
        }
        this.holdOthers('')
        this.ua.call(`sip:${target}@${this.meta?.domain}`, {
            mediaConstraints: micConstraints(this.prefs.devices.microphone),
            pcConfig: this.pcConfig(),
        })
    }

    // ------------------------------------------------------------------ devices and preferences
    /** Apply devices now (live calls included) and save them per user through the API. */
    async saveDevices(devices: CallingDevices, autoAnswer: boolean, key: string) {
        const res = await softphoneApi.savePreferences(this.prefs.revision, { devices, auto_answer_attempts: autoAnswer }, key)
        this.prefs = {
            autoAnswer: res.preferences.auto_answer_attempts,
            devices: { ...res.preferences.devices },
            revision: res.preferences.revision,
        }
        this.set({ autoAnswerEnabled: this.prefs.autoAnswer })
        this.remoteAudio.forEach((el) => void routeTo(el, this.prefs.devices.speaker))
        await this.switchMicrophone(this.prefs.devices.microphone)
        return res
    }

    async reloadPreferences() {
        await this.loadPreferences()
        return this.prefs
    }

    private async switchMicrophone(deviceId: string | null) {
        const established = Array.from(this.sessions.values()).filter((s) => s.isEstablished() && s.connection)
        if (!established.length) return
        const stream = await navigator.mediaDevices.getUserMedia(micConstraints(deviceId))
        const track = stream.getAudioTracks()[0]
        await Promise.all(
            established.map(async (s) => {
                const sender = s.connection.getSenders().find((x) => x.track?.kind === 'audio')
                if (sender) await sender.replaceTrack(track)
                if (s.isMuted().audio) track.enabled = false
            })
        )
    }

    newMicMeter() {
        return new MicMeter()
    }

    // ------------------------------------------------------------------ diagnostics
    private async collectStats() {
        const id = this.snapshot.activeLineId
        const session = id ? this.sessions.get(id) : null
        const pc = session?.connection
        if (!id || !pc) {
            if (this.snapshot.diagnostics.ice) this.setDiag({ ice: null })
            return
        }
        try {
            const stats = await pc.getStats()
            let pairLocal: string | null = null
            let codecId: string | null = null
            const byId = new Map<string, Record<string, unknown>>()
            stats.forEach((r: Record<string, unknown>) => byId.set(r.id as string, r))
            stats.forEach((r: Record<string, unknown>) => {
                if (r.type === 'transport' && r.selectedCandidatePairId) {
                    const pair = byId.get(r.selectedCandidatePairId as string)
                    if (pair) pairLocal = pair.localCandidateId as string
                }
                if (r.type === 'inbound-rtp' && r.kind === 'audio' && r.codecId) codecId = r.codecId as string
            })
            const local = pairLocal ? byId.get(pairLocal) : null
            const codec = codecId ? byId.get(codecId) : null
            this.setDiag({
                ice: { state: pc.iceConnectionState, candidateType: (local?.candidateType as string) || null, lineId: id },
                codecs: {
                    supported: this.snapshot.diagnostics.codecs.supported,
                    negotiated: codec ? String(codec.mimeType || '').replace(/^audio\//i, '').toUpperCase() : null,
                },
            })
        } catch {
            /* the connection closed meanwhile */
        }
    }
}

let singleton: SoftphoneEngine | null = null

/** One engine per tab (the tabs coordinate who registers). */
export function softphoneEngine(): SoftphoneEngine {
    if (!singleton) singleton = new SoftphoneEngine()
    return singleton
}

/** Forget the tab's engine (sign-out): the next sign-in starts clean. */
export function disposeSoftphoneEngine() {
    singleton?.stop()
    singleton = null
}
