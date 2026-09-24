/**
 * One voice lab training session in the browser: microphone -> server-minted ephemeral secret ->
 * WebRTC to the provider -> live timer with a hard 5-minute stop -> cleanup and an end report.
 *
 * Rules (docs/phase6-spec.md hard rule 6, review/phase6-voicelab.md):
 * - the microphone is requested only by `start()` (a click on Start), never on page load;
 * - every microphone track is stopped, the peer connection closed and the remote audio detached on
 *   every way out: End, the time limit, a connection failure, a provider error, a lost device,
 *   leaving the page (`dispose()`) or closing the tab (`pagehide`);
 * - the time limit is `min(max_seconds from the server, 300)` seconds from the moment the provider's
 *   answer is applied, enforced here with a timer (the minted secret also expires after it);
 * - the end is reported with the outcome and the connected seconds only: no audio, no transcript;
 * - the client secret is kept in this object's memory for the handshake only and dropped after.
 *
 * Framework-free: the page subscribes with `useSyncExternalStore(session.subscribe, session.snapshot)`.
 */
import { V2Error } from '../v2/http'
import { endOnUnload, voiceLabApi } from './api'
import { RealtimeSetupError, connectRealtime } from './realtime'
import type { RealtimeConnection } from './realtime'
import type { MintedSession, VoiceEndOutcome, VoiceLang, VoiceSessionRecord } from './types'

export const HARD_LIMIT_SECONDS = 300
const CONNECT_TIMEOUT_MS = 20_000
const DISCONNECT_GRACE_MS = 5_000
const TICK_MS = 500

export type VoicePhase = 'idle' | 'mic' | 'minting' | 'connecting' | 'live' | 'ended' | 'failed'

export type VoiceFailure =
    | { kind: 'api'; error: unknown }
    | { kind: 'mic'; code: 'mic_unavailable' | 'mic_denied' | 'mic_missing' | 'mic_busy' | 'mic_lost' | 'mic_error' }
    | { kind: 'webrtc'; code: RealtimeSetupError['code'] | 'connection_failed' | 'connect_timeout'; status: number | null }
    | { kind: 'provider'; type: string; code: string | null }

export type VoiceSnapshot = {
    phase: VoicePhase
    scenarioId: string | null
    language: VoiceLang | null
    session: VoiceSessionRecord | null
    personaName: string | null
    summary: string | null
    /** true while at least one microphone track is live (the indicator) */
    micLive: boolean
    muted: boolean
    connection: RTCPeerConnectionState | null
    channelOpen: boolean
    remoteAudio: boolean
    /** ms epoch when the answer was applied (the timer's zero), the hard stop, and "now" */
    startedAt: number | null
    deadlineAt: number | null
    now: number
    limitSeconds: number
    endOutcome: VoiceEndOutcome | null
    endReported: 'pending' | 'reported' | 'failed' | null
    failure: VoiceFailure | null
}

const initial = (): VoiceSnapshot => ({
    phase: 'idle',
    scenarioId: null,
    language: null,
    session: null,
    personaName: null,
    summary: null,
    micLive: false,
    muted: false,
    connection: null,
    channelOpen: false,
    remoteAudio: false,
    startedAt: null,
    deadlineAt: null,
    now: Date.now(),
    limitSeconds: HARD_LIMIT_SECONDS,
    endOutcome: null,
    endReported: null,
    failure: null,
})

function micFailure(err: unknown): VoiceFailure {
    const name = (err as { name?: string })?.name || ''
    if (name === 'NotAllowedError' || name === 'SecurityError') return { kind: 'mic', code: 'mic_denied' }
    if (name === 'NotFoundError' || name === 'OverconstrainedError') return { kind: 'mic', code: 'mic_missing' }
    if (name === 'NotReadableError' || name === 'AbortError') return { kind: 'mic', code: 'mic_busy' }
    return { kind: 'mic', code: 'mic_error' }
}

export class VoiceLabSession {
    private snap: VoiceSnapshot = initial()
    private listeners = new Set<() => void>()
    private generation = 0
    private stream: MediaStream | null = null
    private conn: RealtimeConnection | null = null
    private audio: HTMLAudioElement | null = null
    private abort: AbortController | null = null
    private timers: Array<ReturnType<typeof setTimeout>> = []
    private ticker: ReturnType<typeof setInterval> | null = null
    private disconnectTimer: ReturnType<typeof setTimeout> | null = null
    private connectedAt: number | null = null
    private sessionId: string | null = null
    private reported = false

    /** Mount: listen for the tab closing (pagehide). Pair with `dispose()`. */
    attach() {
        if (typeof window !== 'undefined') {
            window.removeEventListener('pagehide', this.onPageHide)
            window.addEventListener('pagehide', this.onPageHide)
        }
    }

    // ------------------------------------------------------------------ store
    subscribe = (fn: () => void) => {
        this.listeners.add(fn)
        return () => {
            this.listeners.delete(fn)
        }
    }

    snapshot = () => this.snap

    private set(patch: Partial<VoiceSnapshot>) {
        this.snap = { ...this.snap, ...patch }
        this.listeners.forEach((fn) => fn())
    }

    get busy() {
        return ['mic', 'minting', 'connecting', 'live'].includes(this.snap.phase)
    }

    // ------------------------------------------------------------------ start
    async start(scenarioId: string, language: VoiceLang) {
        if (this.busy) return
        const gen = ++this.generation
        this.reset()
        this.set({ ...initial(), phase: 'mic', scenarioId, language })

        // 1. the microphone, only now (after the click)
        if (typeof navigator === 'undefined' || !navigator.mediaDevices?.getUserMedia) {
            this.set({ phase: 'failed', failure: { kind: 'mic', code: 'mic_unavailable' } })
            return
        }
        let stream: MediaStream
        try {
            stream = await navigator.mediaDevices.getUserMedia({
                audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
                video: false,
            })
        } catch (err) {
            if (gen === this.generation) this.set({ phase: 'failed', failure: micFailure(err) })
            return
        }
        if (gen !== this.generation) {
            stream.getTracks().forEach((t) => t.stop())
            return
        }
        this.stream = stream
        stream.getTracks().forEach((t) => {
            t.onended = () => {
                if (gen === this.generation && this.busy) void this.finish('error', { kind: 'mic', code: 'mic_lost' })
            }
        })
        this.set({ phase: 'minting', micLive: true })

        // 2. the ephemeral secret (server-side instructions, limits, audit)
        let minted: MintedSession
        try {
            minted = await voiceLabApi.start({ scenario_id: scenarioId, language })
        } catch (err) {
            if (gen === this.generation) await this.finish(null, { kind: 'api', error: err })
            return
        }
        if (gen !== this.generation) {
            // ended (or the page left) while the secret was minted: report it, never connect
            minted.client_secret = ''
            const outcome = this.snap.endOutcome || 'navigated_away'
            voiceLabApi.end(minted.session.id, { outcome }).catch(() => undefined)
            return
        }
        this.sessionId = minted.session.id
        const limitSeconds = Math.min(Math.max(1, Math.floor(minted.max_seconds || HARD_LIMIT_SECONDS)), HARD_LIMIT_SECONDS)
        this.set({
            phase: 'connecting',
            session: minted.session,
            personaName: minted.persona_name?.[language] || null,
            summary: minted.instructions_summary,
            limitSeconds,
        })

        // 3. WebRTC straight to the provider with the ephemeral secret
        const abort = new AbortController()
        this.abort = abort
        try {
            this.conn = await connectRealtime({
                webrtcUrl: minted.webrtc_url,
                clientSecret: minted.client_secret,
                microphone: stream,
                signal: abort.signal,
                handlers: {
                    onRemoteStream: (remote) => this.attachRemote(remote, gen),
                    onConnectionState: (state) => this.onConnection(state, gen),
                    onChannelOpen: () => gen === this.generation && this.set({ channelOpen: true }),
                    onProviderError: (info) => {
                        if (gen === this.generation) void this.finish('error', { kind: 'provider', ...info })
                    },
                },
            })
        } catch (err) {
            if (gen !== this.generation) return
            const failure: VoiceFailure =
                err instanceof RealtimeSetupError
                    ? { kind: 'webrtc', code: err.code, status: err.status }
                    : { kind: 'webrtc', code: 'connection_failed', status: null }
            await this.finish('setup_failed', failure)
            return
        }
        minted.client_secret = ''          // not needed any more
        if (gen !== this.generation) {
            this.conn?.close()
            return
        }

        // 4. the clock: hard stop at the limit, and a connect timeout
        const startedAt = Date.now()
        this.set({ startedAt, deadlineAt: startedAt + limitSeconds * 1000, now: startedAt })
        this.timers.push(setTimeout(() => gen === this.generation && void this.finish('time_limit'), limitSeconds * 1000))
        this.timers.push(
            setTimeout(() => {
                if (gen === this.generation && this.snap.phase === 'connecting') {
                    void this.finish('connection_failed', { kind: 'webrtc', code: 'connect_timeout', status: null })
                }
            }, CONNECT_TIMEOUT_MS)
        )
        this.ticker = setInterval(() => gen === this.generation && this.set({ now: Date.now() }), TICK_MS)
        if (this.conn.pc.connectionState === 'connected') this.onConnection('connected', gen)
    }

    private attachRemote(remote: MediaStream, gen: number) {
        if (gen !== this.generation) return
        if (!this.audio) {
            this.audio = new Audio()
            this.audio.autoplay = true
        }
        this.audio.srcObject = remote
        this.audio.play().catch(() => undefined)
        this.set({ remoteAudio: true })
    }

    private onConnection(state: RTCPeerConnectionState, gen: number) {
        if (gen !== this.generation) return
        this.set({ connection: state })
        if (state === 'connected') {
            if (this.disconnectTimer) clearTimeout(this.disconnectTimer)
            this.disconnectTimer = null
            if (this.connectedAt === null) this.connectedAt = Date.now()
            if (this.snap.phase === 'connecting') this.set({ phase: 'live' })
        } else if (state === 'failed' || state === 'closed') {
            void this.finish('connection_failed', { kind: 'webrtc', code: 'connection_failed', status: null })
        } else if (state === 'disconnected' && !this.disconnectTimer) {
            this.disconnectTimer = setTimeout(() => {
                this.disconnectTimer = null
                if (gen === this.generation && this.conn?.pc.connectionState === 'disconnected') {
                    void this.finish('connection_failed', { kind: 'webrtc', code: 'connection_failed', status: null })
                }
            }, DISCONNECT_GRACE_MS)
        }
    }

    // ------------------------------------------------------------------ controls
    setMuted(muted: boolean) {
        this.stream?.getAudioTracks().forEach((t) => {
            t.enabled = !muted
        })
        this.set({ muted })
    }

    /** End button. */
    end() {
        return this.finish('user_ended')
    }

    /** The page is unmounting (navigation inside the app). */
    dispose() {
        if (this.busy) void this.finish('navigated_away')
        else this.cleanupMedia()
        if (typeof window !== 'undefined') window.removeEventListener('pagehide', this.onPageHide)
    }

    private onPageHide = () => {
        if (!this.busy) return
        this.generation++
        const connected = this.connectedSeconds()
        this.cleanupMedia()
        if (this.sessionId && !this.reported) {
            this.reported = true
            endOnUnload(this.sessionId, 'navigated_away', connected)
        }
        this.set({ phase: 'ended', endOutcome: 'navigated_away', micLive: false })
    }

    private connectedSeconds(): number | null {
        if (this.connectedAt === null) return null
        const s = Math.floor((Date.now() - this.connectedAt) / 1000)
        return Math.max(0, Math.min(s, this.snap.limitSeconds))
    }

    /** Every way out ends here: media first (the microphone is released at once), then the report. */
    private async finish(outcome: VoiceEndOutcome | null, failure: VoiceFailure | null = null) {
        if (!this.busy) return
        this.generation++
        const connected = this.connectedSeconds()
        this.cleanupMedia()
        this.set({
            phase: failure ? 'failed' : 'ended',
            failure,
            endOutcome: outcome,
            micLive: false,
            now: Date.now(),
        })
        if (outcome) await this.reportAndWait(outcome, connected)
    }

    private async reportAndWait(outcome: VoiceEndOutcome, connected: number | null) {
        if (!this.sessionId || this.reported) return
        this.reported = true
        this.set({ endReported: 'pending' })
        try {
            const out = await voiceLabApi.end(this.sessionId, { outcome, connected_seconds: connected })
            this.set({ endReported: 'reported', session: out.session })
        } catch (err) {
            // the server expires the session by itself; the duration then stays unknown
            this.set({ endReported: err instanceof V2Error && err.status === 404 ? 'reported' : 'failed' })
        }
    }

    private cleanupMedia() {
        this.timers.forEach((t) => clearTimeout(t))
        this.timers = []
        if (this.ticker) clearInterval(this.ticker)
        this.ticker = null
        if (this.disconnectTimer) clearTimeout(this.disconnectTimer)
        this.disconnectTimer = null
        this.abort?.abort()
        this.abort = null
        this.conn?.close()
        this.conn = null
        this.stream?.getTracks().forEach((t) => {
            t.onended = null
            t.stop()
        })
        this.stream = null
        if (this.audio) {
            this.audio.pause()
            this.audio.srcObject = null
        }
        this.audio = null
    }

    private reset() {
        this.cleanupMedia()
        this.connectedAt = null
        this.sessionId = null
        this.reported = false
    }
}
