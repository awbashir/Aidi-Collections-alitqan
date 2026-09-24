/**
 * The provider-specific part of the voice lab: the OpenAI Realtime WebRTC handshake.
 *
 *   1. an RTCPeerConnection with the microphone track (sendrecv audio) and the `oai-events`
 *      data channel;
 *   2. the SDP offer is POSTed to the Realtime calls endpoint the server named (`webrtc_url`,
 *      default https://api.openai.com/v1/realtime/calls) with the EPHEMERAL client secret as the
 *      bearer and `Content-Type: application/sdp`;
 *   3. the answer SDP (the response body) is applied; the provider's audio arrives as a remote
 *      track and its events on the data channel.
 *
 * The permanent API key never reaches the browser: the server minted the secret (short-lived,
 * bound to the persona instructions and model it chose). This module never changes the session
 * (no `session.update`); it only asks the persona to answer the phone (`response.create`).
 * Nothing is recorded: no MediaRecorder, no transcript kept.
 */

export type RealtimeHandlers = {
    onRemoteStream: (stream: MediaStream) => void
    onConnectionState: (state: RTCPeerConnectionState) => void
    onChannelOpen: () => void
    /** a provider `error` event (type and code only, never content) */
    onProviderError: (info: { type: string; code: string | null }) => void
}

export type RealtimeConnection = {
    pc: RTCPeerConnection
    channel: RTCDataChannel
    close: () => void
}

export class RealtimeSetupError extends Error {
    code: 'webrtc_unsupported' | 'webrtc_url_invalid' | 'provider_sdp_failed' | 'provider_unreachable' | 'sdp_invalid'
    status: number | null

    constructor(code: RealtimeSetupError['code'], status: number | null = null) {
        super(code)
        this.name = 'RealtimeSetupError'
        this.code = code
        this.status = status
    }
}

const LOOPBACK = new Set(['localhost', '127.0.0.1', '[::1]'])

/** https anywhere, http only to a loopback host (a local test double). */
export function safeWebrtcUrl(raw: string | null | undefined): string | null {
    if (!raw) return null
    try {
        const u = new URL(raw)
        if (u.username || u.password) return null
        if (u.protocol === 'https:') return u.toString()
        if (u.protocol === 'http:' && LOOPBACK.has(u.hostname)) return u.toString()
    } catch {
        return null
    }
    return null
}

export const webrtcSupported = () => typeof window !== 'undefined' && typeof window.RTCPeerConnection === 'function'

export async function connectRealtime(opts: {
    webrtcUrl: string | null
    clientSecret: string
    microphone: MediaStream
    handlers: RealtimeHandlers
    signal: AbortSignal
}): Promise<RealtimeConnection> {
    const { clientSecret, microphone, handlers, signal } = opts
    if (!webrtcSupported()) throw new RealtimeSetupError('webrtc_unsupported')
    const url = safeWebrtcUrl(opts.webrtcUrl)
    if (!url) throw new RealtimeSetupError('webrtc_url_invalid')

    const pc = new RTCPeerConnection()
    let closed = false
    const close = () => {
        if (closed) return
        closed = true
        pc.ontrack = null
        pc.onconnectionstatechange = null
        try {
            channel.close()
        } catch {
            /* already closed */
        }
        try {
            pc.getSenders().forEach((s) => s.track?.stop())
        } catch {
            /* nothing to stop */
        }
        pc.close()
    }
    pc.ontrack = (e) => {
        const stream = e.streams[0] || new MediaStream([e.track])
        handlers.onRemoteStream(stream)
    }
    pc.onconnectionstatechange = () => handlers.onConnectionState(pc.connectionState)
    microphone.getAudioTracks().forEach((t) => pc.addTrack(t, microphone))
    const channel = pc.createDataChannel('oai-events')
    channel.onopen = () => {
        handlers.onChannelOpen()
        try {
            // the persona "answers the phone" (its instructions say so); nothing else is sent
            channel.send(JSON.stringify({ type: 'response.create' }))
        } catch {
            /* the channel closed meanwhile */
        }
    }
    channel.onmessage = (e) => {
        let event: { type?: string; error?: { type?: string; code?: string } } | null = null
        try {
            event = JSON.parse(typeof e.data === 'string' ? e.data : '')
        } catch {
            return
        }
        if (event?.type === 'error') {
            handlers.onProviderError({ type: String(event.error?.type || 'error'), code: event.error?.code ? String(event.error.code) : null })
        }
    }

    try {
        const offer = await pc.createOffer()
        await pc.setLocalDescription(offer)
        if (signal.aborted) throw new DOMException('aborted', 'AbortError')
        let res: Response
        try {
            res = await fetch(url, {
                method: 'POST',
                body: offer.sdp,
                headers: { Authorization: `Bearer ${clientSecret}`, 'Content-Type': 'application/sdp' },
                credentials: 'omit',
                referrerPolicy: 'no-referrer',
                cache: 'no-store',
                signal,
            })
        } catch (err) {
            if ((err as Error)?.name === 'AbortError') throw err
            throw new RealtimeSetupError('provider_unreachable')
        }
        if (!res.ok) throw new RealtimeSetupError('provider_sdp_failed', res.status)
        const answer = await res.text()
        if (!answer.startsWith('v=0')) throw new RealtimeSetupError('sdp_invalid', res.status)
        if (signal.aborted) throw new DOMException('aborted', 'AbortError')
        await pc.setRemoteDescription({ type: 'answer', sdp: answer })
    } catch (err) {
        close()
        throw err
    }
    return { pc, channel, close }
}
