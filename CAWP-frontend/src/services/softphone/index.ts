/**
 * The in-app softphone (Phase 5 P3). See engine.ts for the design and review/phase5-softphone.md
 * for the credential model, the tab ownership and the test evidence.
 */
import { useSyncExternalStore } from 'react'
import type { SoftphoneEngine } from './engine'

export { SoftphoneEngine, SoftphoneError, MAX_LINES, softphoneEngine, disposeSoftphoneEngine } from './engine'
export { softphoneApi, fetchLiveAttempt } from './api'
export { autoAnswerDecision, internalDialRegex, isExternalTarget, isInternalTarget, parseAttemptTag, DTMF_KEYS, isDtmf } from './policy'
export { callControlApi, describeControlError, isOpen as isControlOpen, parseControlTag, pollControl, CONTROL_HEADER } from './control'
export type { CallControl, ControlCapabilities, ControlOp, ControlTarget, IncomingControl, SpyMode } from './control'
export { listAudioDevices, playTestTone, canSelectOutput, MicMeter } from './audio'
export type { AudioDevice } from './audio'
export * from './types'

/** The engine's snapshot as React state. */
export function useSoftphoneSnapshot(engine: SoftphoneEngine) {
    return useSyncExternalStore(engine.subscribe, engine.getSnapshot, engine.getSnapshot)
}
