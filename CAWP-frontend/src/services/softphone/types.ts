/**
 * Softphone (Phase 5 P3) types. The softphone is the agent's extension endpoint and nothing
 * more (PROJECT-NOTES §3.4): it registers the caller's own extension with a short-lived
 * credential from `POST /v2/me/sip-credentials`, answers the extension-first agent leg (auto-
 * answer only for our attempt-tagged calls), and never dials an external number.
 */

/** `POST /v2/me/sip-credentials` (201). The password lives in memory only. */
export type SipCredential = {
    credential_id: string
    extension: string
    sip_uri: string
    authorization_user: string
    realm: string
    password: string
    display_name: string
    wss_url: string
    ice_servers: RTCIceServer[]
    /** SIP_RTCP_MUX_POLICY: 'negotiate' for a PBX without rtcp-mux (Asterisk 11); absent = 'require' */
    rtcp_mux_policy?: 'require' | 'negotiate'
    issued_at: string
    expires_at: string
    ttl_seconds: number
    refresh_after_seconds: number
    register_expires: number
    auto_answer: { header: string; enabled: boolean }
    dial_policy: { internal_pattern: string; echo_test_extension: string | null; external: string; external_pattern?: string }
    storage: 'memory_only'
}

/** `GET /v2/me/sip-credentials`: never the secret. */
export type SipCredentialStatus = {
    configured: boolean
    /** 0082: switched on for this user by a calling administrator (off by default) */
    enabled: boolean
    reason: string | null
    extension: string | null
    wss_url: string | null
    ttl_seconds: number | null
    current: { credential_id: string; extension: string; issued_at: string; expires_at: string; active: boolean } | null
}

export type CallingDevices = { microphone: string | null; speaker: string | null; ringer: string | null }

/** `GET /v2/me/calling` (P0/P1). */
export type MyCalling = {
    user_id: string
    extension: { extension: string } | null
    preferences: {
        auto_answer_attempts: boolean
        devices: CallingDevices
        revision: number
        saved: boolean
        updated_at: string | null
    }
    /** 0082: the in-app softphone is switched on for me (off by default: my own phone rings) */
    softphone_enabled: boolean
    can_call: boolean
    reason: string | null
    active_attempt_id: string | null
}

/** The subset of the P1 live-call view (`GET /v2/calls/<id>/live`) the softphone shows. */
export type LiveAttempt = {
    id: string
    mode: string
    state: string
    extension: string | null
    case: { id: string; name: string | null; account_number: string | null; missing?: boolean } | null
    contact: { id: string; value: string | null } | null
    destination: string | null
    timeline: Record<string, string | null>
    live: boolean
    conversation: { evidenced: boolean; customer_answered: boolean; rule?: string }
    uncertain: { reason: string; since: string | null; note: string } | null
    reason_code: string | null
    duration_seconds: number | null
    wrapup_pending?: boolean
    wrapup: unknown | null
    poll_after_ms: number | null
    etag?: string
}

/** Header chip states (§3.2): registered / connecting / offline / failed, plus the clear
 * non-registrable states. */
export type PhoneStatus =
    | 'starting'
    | 'connecting'
    | 'registered'
    | 'offline'
    | 'failed'
    | 'no_extension'
    | 'not_configured'
    | 'unsupported'
    | 'stopped'

export type TabRole = 'leader' | 'follower' | 'solo'

export type LineState = 'ringing' | 'calling' | 'connecting' | 'active' | 'held' | 'ended'

export type Line = {
    id: string
    direction: 'incoming' | 'outgoing'
    state: LineState
    remote: string
    remoteName: string | null
    /** The attempt id from the X-CAWP-Attempt header (unverified until matched with the API). */
    attemptTag: string | null
    /** The attempt this line is linked to (verified against GET /v2/me/calls/active). */
    attemptId: string | null
    caseId: string | null
    caseName: string | null
    autoAnswered: boolean
    autoAnswerDecision: AutoAnswerReason | null
    muted: boolean
    held: boolean
    startedAt: number
    answeredAt: number | null
    endedAt: number | null
    endCause: string | null
    transfer: { target: string; state: 'sending' | 'accepted' | 'failed'; detail?: string } | null
    /** X-CAWP-Control (32 hex) on an incoming call placed by call control (2026-10-20). */
    controlTag?: string | null
    /** What the API says about that call (`GET /v2/calling/control/incoming/<tag>`), or null. */
    control?: LineControl | null
}

/** A ringing call from call control: a transfer, a consultation, a conference invite, the agent's
 * own blind transfer ringing back, or a supervisor's own listening call (the only auto-answered one). */
export type LineControl = {
    id: string
    role: 'listen' | 'transfer' | 'consult' | 'invite' | 'recall'
    kind: string
    fromName: string | null
    fromExtension: string
    targetExtension: string
    targetName: string | null
    mode: 'listen' | 'whisper' | 'barge' | null
    caseId: string | null
    caseName: string | null
    caseAccount: string | null
    caseInScope: boolean
    /** The call has a case that is outside my scope (no details shown). */
    caseHidden: boolean
}

export type AutoAnswerReason =
    | 'auto_answered'
    | 'no_tag'
    | 'disabled_by_preference'
    | 'not_my_active_attempt'
    | 'attempt_not_live'
    | 'check_failed'
    | 'busy_on_another_line'
    | 'own_listen_call'
    | 'control_ring'

export type Diagnostics = {
    secureContext: boolean
    webrtc: boolean
    webLocks: boolean
    broadcastChannel: boolean
    micPermission: 'granted' | 'denied' | 'prompt' | 'unknown'
    websocket: 'connected' | 'connecting' | 'disconnected' | 'idle'
    registration: 'registered' | 'unregistered' | 'failed' | 'idle'
    lastRegistrationCode: number | null
    lastRegistrationCause: string | null
    wssUrl: string | null
    credentialExpiresAt: string | null
    credentialIssues: number
    ice: { state: string; candidateType: string | null; lineId: string } | null
    codecs: { supported: string[]; negotiated: string | null }
    lastError: string | null
}

/** What one tab broadcasts to the others (never the credential). */
export type Snapshot = {
    status: PhoneStatus
    role: TabRole
    extension: string | null
    lines: Line[]
    activeLineId: string | null
    diagnostics: Diagnostics
    statusDetail: string | null
    leaderTabId: string | null
    /** Bumped when a call needs the agent (incoming ring, auto-answer): the panel opens. */
    openRequest: number
    /** Dialing rules from the credential (never the credential itself). */
    dialPattern: string | null
    /** Opt-in (server SIP_EXTERNAL_DIAL_PATTERN): outside numbers the dialpad may call; null = none. */
    externalDialPattern: string | null
    echoTestExtension: string | null
    autoAnswerEnabled: boolean
    updatedAt: number
}
