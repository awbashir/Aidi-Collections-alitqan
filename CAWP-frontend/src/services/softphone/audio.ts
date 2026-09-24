/**
 * Audio devices for the softphone: device lists, output routing (setSinkId), a microphone
 * level meter, a test tone and the ringer. Nothing is recorded: the meter reads levels from an
 * AnalyserNode and keeps no samples (no browser-side recording, PROJECT-NOTES §3.2).
 */

export type AudioDevice = { deviceId: string; label: string; kind: 'audioinput' | 'audiooutput' }

type SinkElement = HTMLAudioElement & { setSinkId?: (id: string) => Promise<void>; sinkId?: string }

export const canSelectOutput = () =>
    typeof HTMLMediaElement !== 'undefined' && 'setSinkId' in HTMLMediaElement.prototype

export async function listAudioDevices(): Promise<{ inputs: AudioDevice[]; outputs: AudioDevice[] }> {
    if (!navigator.mediaDevices?.enumerateDevices) return { inputs: [], outputs: [] }
    const all = await navigator.mediaDevices.enumerateDevices()
    const pick = (kind: 'audioinput' | 'audiooutput') =>
        all
            .filter((d) => d.kind === kind)
            .map((d, i) => ({ deviceId: d.deviceId, label: d.label || `${kind === 'audioinput' ? 'Microphone' : 'Speaker'} ${i + 1}`, kind }))
    return { inputs: pick('audioinput'), outputs: pick('audiooutput') }
}

/** Route an element to an output device; unknown/unsupported ids fall back to the default. */
export async function routeTo(el: HTMLAudioElement, deviceId: string | null | undefined) {
    const sink = el as SinkElement
    if (!sink.setSinkId) return
    try {
        await sink.setSinkId(deviceId || '')
    } catch {
        try {
            await sink.setSinkId('')
        } catch {
            /* keep the browser default */
        }
    }
}

export const micConstraints = (deviceId: string | null | undefined): MediaStreamConstraints => ({
    audio: deviceId ? { deviceId: { ideal: deviceId }, echoCancellation: true, noiseSuppression: true } : true,
    video: false,
})

let sharedContext: AudioContext | null = null
function context(): AudioContext | null {
    const Ctor = (window.AudioContext || (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext) as
        | typeof AudioContext
        | undefined
    if (!Ctor) return null
    if (!sharedContext || sharedContext.state === 'closed') sharedContext = new Ctor()
    if (sharedContext.state === 'suspended') sharedContext.resume().catch(() => undefined)
    return sharedContext
}

/** Microphone level 0..1 (RMS), for the input meter. */
export class MicMeter {
    private stream: MediaStream | null = null
    private timer: ReturnType<typeof setInterval> | null = null
    private source: MediaStreamAudioSourceNode | null = null

    async start(deviceId: string | null, onLevel: (level: number) => void) {
        this.stop()
        const ctx = context()
        if (!ctx) throw new Error('no AudioContext')
        this.stream = await navigator.mediaDevices.getUserMedia(micConstraints(deviceId))
        this.source = ctx.createMediaStreamSource(this.stream)
        const analyser = ctx.createAnalyser()
        analyser.fftSize = 1024
        this.source.connect(analyser)
        const buf = new Float32Array(analyser.fftSize)
        this.timer = setInterval(() => {
            analyser.getFloatTimeDomainData(buf)
            let sum = 0
            for (let i = 0; i < buf.length; i += 1) sum += buf[i] * buf[i]
            onLevel(Math.min(1, Math.sqrt(sum / buf.length) * 3))
        }, 100)
    }

    stop() {
        if (this.timer) clearInterval(this.timer)
        this.timer = null
        this.source?.disconnect()
        this.source = null
        this.stream?.getTracks().forEach((t) => t.stop())
        this.stream = null
    }
}

/** A tone of `ms` on the chosen output (the "test tone" button). */
export async function playTestTone(deviceId: string | null, ms = 1200, frequency = 440): Promise<void> {
    const ctx = context()
    if (!ctx) return
    const osc = ctx.createOscillator()
    const gain = ctx.createGain()
    const dest = ctx.createMediaStreamDestination()
    osc.frequency.value = frequency
    gain.gain.value = 0.15
    osc.connect(gain).connect(dest)
    const el = new Audio() as SinkElement
    el.srcObject = dest.stream
    await routeTo(el, deviceId)
    osc.start()
    await el.play().catch(() => undefined)
    await new Promise((r) => setTimeout(r, ms))
    osc.stop()
    el.pause()
    el.srcObject = null
}

/** The ringtone (440+480 Hz, 2 s on / 4 s off) on the ringer device. */
export class Ringer {
    private el: SinkElement | null = null
    private oscs: OscillatorNode[] = []
    private gain: GainNode | null = null
    private cadence: ReturnType<typeof setInterval> | null = null
    ringing = false

    async start(deviceId: string | null) {
        if (this.ringing) return
        const ctx = context()
        if (!ctx) return
        this.ringing = true
        const dest = ctx.createMediaStreamDestination()
        this.gain = ctx.createGain()
        this.gain.gain.value = 0
        this.gain.connect(dest)
        this.oscs = [440, 480].map((f) => {
            const o = ctx.createOscillator()
            o.frequency.value = f
            o.connect(this.gain as GainNode)
            o.start()
            return o
        })
        this.el = new Audio() as SinkElement
        this.el.srcObject = dest.stream
        await routeTo(this.el, deviceId)
        this.el.play().catch(() => undefined)
        let on = true
        const apply = () => {
            if (this.gain) this.gain.gain.value = on ? 0.12 : 0
        }
        apply()
        let elapsed = 0
        this.cadence = setInterval(() => {
            elapsed += 500
            if (on && elapsed >= 2000) {
                on = false
                elapsed = 0
            } else if (!on && elapsed >= 4000) {
                on = true
                elapsed = 0
            }
            apply()
        }, 500)
    }

    stop() {
        this.ringing = false
        if (this.cadence) clearInterval(this.cadence)
        this.cadence = null
        this.oscs.forEach((o) => {
            try {
                o.stop()
            } catch {
                /* already stopped */
            }
        })
        this.oscs = []
        this.gain?.disconnect()
        this.gain = null
        if (this.el) {
            this.el.pause()
            this.el.srcObject = null
        }
        this.el = null
    }
}

export async function microphonePermission(): Promise<'granted' | 'denied' | 'prompt' | 'unknown'> {
    try {
        const status = await navigator.permissions.query({ name: 'microphone' as PermissionName })
        return status.state as 'granted' | 'denied' | 'prompt'
    } catch {
        return 'unknown'
    }
}

export function supportedCodecs(): string[] {
    try {
        const caps = (RTCRtpSender as unknown as { getCapabilities?: (k: string) => RTCRtpCapabilities | null }).getCapabilities?.('audio')
        const names = (caps?.codecs || [])
            .map((c) => c.mimeType.replace(/^audio\//i, '').toUpperCase())
            .filter((n) => ['PCMU', 'PCMA', 'OPUS', 'G722'].includes(n))
        return Array.from(new Set(names))
    } catch {
        return []
    }
}
