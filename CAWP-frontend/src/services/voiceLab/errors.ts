/**
 * Voice lab failures in words (keys under `p6vl.err.*`). API codes come from api/voice_lab.py and
 * the Q0/Q1 gateway; microphone and WebRTC codes from session.ts / realtime.ts. Unknown API codes
 * fall back to the shared v2 message, so nothing is swallowed.
 */
import { describeV2Error, isV2Error } from '../v2'
import type { Translate } from '../v2'
import type { VoiceFailure } from './session'

export const VOICE_LAB_API_CODES = [
    'ai_not_configured',
    'model_not_set',
    'ai_policy_required',
    'ai_policy_category_denied',
    'ai_quota_exceeded',
    'ai_audit_unavailable',
    'ai_provider_timeout',
    'ai_provider_unreachable',
    'ai_provider_unavailable',
    'ai_provider_rejected',
    'ai_provider_invalid_response',
    'ai_provider_rate_limited',
    'ai_provider_quota_exceeded',
    'ai_provider_auth_failed',
    'voice_lab_session_active',
    'voice_lab_rate_limited',
    'voice_lab_session_expired',
] as const

const minutes = (seconds: unknown) => Math.max(1, Math.ceil(Number(seconds || 0) / 60))

export function describeVoiceLabApiError(err: unknown, t: Translate): string {
    if (isV2Error(err) && (VOICE_LAB_API_CODES as readonly string[]).includes(err.code)) {
        const d = (err.detail || {}) as Record<string, unknown>
        if (err.code === 'voice_lab_rate_limited') {
            return t(`p6vl.err.voice_lab_rate_limited_${d.limit === 'per_day' ? 'day' : 'hour'}`, {
                minutes: minutes(d.retry_after_seconds),
            })
        }
        return t(`p6vl.err.${err.code}`)
    }
    if (isV2Error(err) && err.status === 404) return t('p6vl.err.not_deployed')
    return describeV2Error(err, t)
}

export function describeVoiceFailure(f: VoiceFailure, t: Translate): string {
    switch (f.kind) {
        case 'api':
            return describeVoiceLabApiError(f.error, t)
        case 'mic':
            return t(`p6vl.err.${f.code}`)
        case 'webrtc':
            return t(`p6vl.err.${f.code}`, { status: f.status ?? '' })
        case 'provider':
            return t('p6vl.err.provider_event', { code: f.code || f.type })
        default:
            return t('p6vl.err.unknown')
    }
}
