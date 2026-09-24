/**
 * Phase 6 Q3: the voice training lab (fictional training; review/phase6-voicelab.md).
 * `realtime.ts` holds the provider-specific WebRTC handshake; `session.ts` the browser session
 * (microphone, timer, cleanup, end report); `api.ts` the server endpoints.
 */
export * from './types'
export { voiceLabApi, endOnUnload } from './api'
export { VoiceLabSession, HARD_LIMIT_SECONDS } from './session'
export type { VoicePhase, VoiceFailure, VoiceSnapshot } from './session'
export { connectRealtime, safeWebrtcUrl, webrtcSupported, RealtimeSetupError } from './realtime'
export { describeVoiceFailure, describeVoiceLabApiError, VOICE_LAB_API_CODES } from './errors'
