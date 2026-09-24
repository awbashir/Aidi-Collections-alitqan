/**
 * One registered softphone per browser profile (PROJECT-NOTES §3.2: multi-tab ownership).
 *
 * The tab that holds the Web Lock `cawp-softphone` is the LEADER: only it creates the SIP user
 * agent, registers and handles calls. Every other tab queues on the same lock and is a FOLLOWER
 * that shows the leader's status only. When the leader tab closes or signs out, the browser
 * releases the lock and the next queued tab takes over by itself.
 *
 * The leader broadcasts a sanitized snapshot on a BroadcastChannel (never a credential).
 * Without Web Locks (old browsers, insecure origins) the tabs elect a leader over the same
 * BroadcastChannel with heartbeats; without BroadcastChannel either, the tab runs alone.
 */
import type { Snapshot } from './types'

const NAME = 'cawp-softphone'
const HEARTBEAT_MS = 1000
const LEADER_TIMEOUT_MS = 3500

type Message =
    | { type: 'snapshot'; from: string; snapshot: Snapshot }
    | { type: 'hello'; from: string }
    | { type: 'heartbeat'; from: string }
    | { type: 'claim'; from: string; at: number }
    | { type: 'bye'; from: string }

type Handlers = {
    onLeader: () => void
    onFollower: () => void
    onSnapshot: (snapshot: Snapshot, from: string) => void
    /** A follower asked for the current state (the leader re-broadcasts). */
    onHello: () => void
}

const randomId = () =>
    (globalThis.crypto && 'randomUUID' in globalThis.crypto
        ? globalThis.crypto.randomUUID()
        : `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`)

export type Coordination = 'web-locks' | 'broadcast-election' | 'solo'

export class TabCoordinator {
    readonly tabId = randomId()
    mode: Coordination
    private channel: BroadcastChannel | null = null
    private releaseLock: (() => void) | null = null
    private abort: AbortController | null = null
    private leader = false
    private stopped = false
    private heartbeat: ReturnType<typeof setInterval> | null = null
    private watchdog: ReturnType<typeof setInterval> | null = null
    private lastLeaderBeat = 0
    private claimAt = 0

    constructor(private readonly handlers: Handlers) {
        const hasLocks = typeof navigator !== 'undefined' && !!navigator.locks && typeof navigator.locks.request === 'function'
        const hasChannel = typeof BroadcastChannel !== 'undefined'
        this.mode = hasLocks ? 'web-locks' : hasChannel ? 'broadcast-election' : 'solo'
        if (hasChannel) {
            this.channel = new BroadcastChannel(NAME)
            this.channel.onmessage = (ev: MessageEvent<Message>) => this.onMessage(ev.data)
        }
    }

    get isLeader() {
        return this.leader
    }

    start() {
        this.stopped = false
        if (this.mode === 'web-locks') {
            this.handlers.onFollower()
            this.post({ type: 'hello', from: this.tabId })
            this.abort = new AbortController()
            navigator.locks
                .request(NAME, { mode: 'exclusive', signal: this.abort.signal }, () => {
                    if (this.stopped) return undefined
                    return new Promise<void>((resolve) => {
                        this.releaseLock = resolve
                        this.becomeLeader()
                    })
                })
                .catch(() => {
                    /* aborted on stop() */
                })
            return
        }
        if (this.mode === 'solo') {
            this.becomeLeader()
            return
        }
        // BroadcastChannel election: wait for a leader's heartbeat, else claim.
        this.handlers.onFollower()
        this.post({ type: 'hello', from: this.tabId })
        this.lastLeaderBeat = Date.now()
        this.watchdog = setInterval(() => {
            if (!this.leader && Date.now() - this.lastLeaderBeat > LEADER_TIMEOUT_MS) this.claim()
        }, 500)
        setTimeout(() => {
            if (!this.leader && Date.now() - this.lastLeaderBeat >= 1400) this.claim()
        }, 1500)
    }

    /** Leave: release the lock (the next tab takes over) and close the channel. */
    stop() {
        this.stopped = true
        if (this.leader) this.post({ type: 'bye', from: this.tabId })
        this.leader = false
        if (this.heartbeat) clearInterval(this.heartbeat)
        if (this.watchdog) clearInterval(this.watchdog)
        this.heartbeat = null
        this.watchdog = null
        this.abort?.abort()
        this.releaseLock?.()
        this.releaseLock = null
        this.channel?.close()
        this.channel = null
    }

    broadcast(snapshot: Snapshot) {
        if (this.leader) this.post({ type: 'snapshot', from: this.tabId, snapshot })
    }

    private becomeLeader() {
        if (this.stopped || this.leader) return
        this.leader = true
        if (this.mode === 'broadcast-election') {
            this.heartbeat = setInterval(() => this.post({ type: 'heartbeat', from: this.tabId }), HEARTBEAT_MS)
            this.post({ type: 'heartbeat', from: this.tabId })
        }
        this.handlers.onLeader()
    }

    private claim() {
        if (this.stopped || this.leader) return
        this.claimAt = Date.now()
        this.post({ type: 'claim', from: this.tabId, at: this.claimAt })
        const at = this.claimAt
        setTimeout(() => {
            // nobody beat us (an older claim or a live leader resets claimAt)
            if (this.claimAt === at && !this.leader && Date.now() - this.lastLeaderBeat > 300) this.becomeLeader()
        }, 300)
    }

    private onMessage(msg: Message) {
        if (!msg || msg.from === this.tabId) return
        switch (msg.type) {
            case 'snapshot':
                this.lastLeaderBeat = Date.now()
                if (!this.leader) this.handlers.onSnapshot(msg.snapshot, msg.from)
                break
            case 'heartbeat':
                this.lastLeaderBeat = Date.now()
                if (this.leader && this.mode === 'broadcast-election' && msg.from < this.tabId) {
                    // two leaders after a partition: the smaller id keeps it
                    this.leader = false
                    if (this.heartbeat) clearInterval(this.heartbeat)
                    this.heartbeat = null
                    this.handlers.onFollower()
                }
                this.claimAt = 0
                break
            case 'hello':
                if (this.leader) this.handlers.onHello()
                break
            case 'claim':
                if (this.leader) {
                    this.post({ type: 'heartbeat', from: this.tabId })
                } else if (this.claimAt && (msg.at < this.claimAt || (msg.at === this.claimAt && msg.from < this.tabId))) {
                    this.claimAt = 0
                    this.lastLeaderBeat = Date.now()
                }
                break
            case 'bye':
                if (this.mode === 'broadcast-election' && !this.leader) {
                    this.lastLeaderBeat = 0
                    setTimeout(() => this.claim(), Math.floor(Math.random() * 200))
                }
                break
            default:
                break
        }
    }

    private post(msg: Message) {
        try {
            this.channel?.postMessage(msg)
        } catch {
            /* channel closed */
        }
    }
}
