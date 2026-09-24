/**
 * Phase 6 Q3: voice training lab types (api/voice_lab.py, review/phase6-voicelab.md).
 *
 * The lab is FICTIONAL training: no case, contact or PBX is involved.
 */

export type VoiceLang = 'ar' | 'en'
export type Bilingual = { ar: string; en: string }

export type VoiceScenario = {
    id: string
    version: number
    difficulty: 'easy' | 'medium' | 'hard'
    persona_name: Bilingual
    title: Bilingual
    summary: Bilingual
    brief: Bilingual
    goals: { ar: string[]; en: string[] }
    fictional: true
}

export type VoiceLabLimits = { max_seconds: number; sessions_per_hour: number; sessions_per_day: number }

export type Readiness = { ready: boolean; reasons: string[] }

export type VoiceLabCatalogue = {
    scenarios: VoiceScenario[]
    languages: VoiceLang[]
    limits: VoiceLabLimits
    readiness: Readiness
    fictional: true
}

export type VoiceSessionState = 'minting' | 'active' | 'ended' | 'failed' | 'expired'
export type VoiceEndOutcome = 'user_ended' | 'time_limit' | 'connection_failed' | 'setup_failed' | 'navigated_away' | 'error'
export type VoiceSessionOutcome = VoiceEndOutcome | 'expired' | 'mint_refused' | 'mint_failed'

export type VoiceSessionRecord = {
    id: string
    user_id: string
    scenario_id: string
    scenario_version: number
    scenario_title: Bilingual | null
    language: VoiceLang
    state: VoiceSessionState
    outcome: VoiceSessionOutcome | null
    error_code: string | null
    max_seconds: number
    started_at: string
    expires_at: string
    activated_at: string | null
    ended_at: string | null
    /** null = unknown (an expired session nobody ended); never shown as 0 */
    duration_seconds: number | null
    connected_seconds: number | null
    provider_call_id: string | null
    model: string | null
    policy_revision: number | null
}

/** POST /v2/ai/voice-lab/sessions. `client_secret` lives in memory only, for one connection. */
export type MintedSession = {
    session: VoiceSessionRecord
    client_secret: string
    /** when the ephemeral secret stops being usable (ISO), as the provider reported it */
    expires_at: string | null
    model: string
    max_seconds: number
    webrtc_url: string | null
    persona_name: Bilingual
    instructions_summary: string
}

export type VoiceUsage = { last_hour: number; today: number; sessions_per_hour: number; sessions_per_day: number }

export type MySessions = {
    items: VoiceSessionRecord[]
    total: number
    page: number
    page_size: number
    active: VoiceSessionRecord | null
    usage: VoiceUsage
}

/** The part of GET /v2/ai/status this page reads (Q0; `quota` from Q1 when present). */
export type AiStatus = {
    provider: string | null
    capabilities: Record<string, { ready: boolean; reasons: string[]; required_categories: string[] }>
    reasons: string[]
    quota?: {
        day: string
        resets_at: string
        user: { limit: number; used: number; remaining: number } | null
        total: { limit: number; used: number; remaining: number }
        exceeded: 'user' | 'total' | null
    }
}
