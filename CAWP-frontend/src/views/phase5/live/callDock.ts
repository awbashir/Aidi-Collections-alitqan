/**
 * The docked live-call panel's state, shared by every screen in the tab (one small store,
 * read with useSyncExternalStore). The case page and the calling workspace ask it to show
 * a call; the dock polls the live view and opens the wrap-up when the call ends.
 *
 * Nothing here decides anything about a call: the API's live view (actions.can_cancel,
 * actions.can_wrapup, wrapup_outcomes) is the only authority.
 */
import { useSyncExternalStore } from 'react'
import type { LiveCall } from '@/services/v2'

export type DockState = {
    /** The attempt the dock shows (null = nothing to show). */
    attemptId: string | null
    /** Collapsed to a one-line bar (the call keeps being watched). */
    minimized: boolean
    /** The attempt whose wrap-up dialog is open. */
    wrapupFor: string | null
    /** Attempts whose wrap-up already opened automatically once (never re-opened by itself). */
    autoOpened: string[]
    /** Attempts the user closed from the dock (not shown again unless asked). */
    dismissed: string[]
}

let state: DockState = { attemptId: null, minimized: false, wrapupFor: null, autoOpened: [], dismissed: [] }
const listeners = new Set<() => void>()
/** A live view the caller already holds (the POST /v2/calls answer), shown before the first poll. */
const seeds = new Map<string, LiveCall>()

const set = (patch: Partial<DockState>) => {
    state = { ...state, ...patch }
    listeners.forEach((l) => l())
}

export const callDock = {
    subscribe(fn: () => void) {
        listeners.add(fn)
        return () => {
            listeners.delete(fn)
        }
    },
    get: () => state,
    /** Show (and expand) a call: after "Call" on a contact, a campaign dispatch, or a click in a list. */
    show(attemptId: string, seed?: LiveCall) {
        if (seed) seeds.set(attemptId, seed)
        set({ attemptId, minimized: false, dismissed: state.dismissed.filter((d) => d !== attemptId) })
    },
    /** The API reports an active attempt (poll): show it unless the user closed it. */
    adopt(attemptId: string | null) {
        if (!attemptId) return
        if (state.attemptId === attemptId || state.dismissed.includes(attemptId)) return
        set({ attemptId, minimized: false })
    },
    minimize(minimized: boolean) {
        set({ minimized })
    },
    close() {
        const id = state.attemptId
        set({ attemptId: null, dismissed: id ? [...state.dismissed.slice(-20), id] : state.dismissed })
    },
    /** Forget the shown attempt without marking it dismissed (it was wrapped up / is gone). */
    clear(attemptId: string) {
        if (state.attemptId === attemptId) set({ attemptId: null })
    },
    openWrapup(attemptId: string, auto = false) {
        if (auto && state.autoOpened.includes(attemptId)) return
        set({
            wrapupFor: attemptId,
            autoOpened: auto ? [...state.autoOpened.slice(-50), attemptId] : state.autoOpened,
        })
    },
    seedFor(attemptId: string | null): LiveCall | null {
        return attemptId ? seeds.get(attemptId) || null : null
    },
    closeWrapup() {
        set({ wrapupFor: null })
    },
    /** Sign-out / user change: start clean. */
    reset() {
        seeds.clear()
        state = { attemptId: null, minimized: false, wrapupFor: null, autoOpened: [], dismissed: [] }
        listeners.forEach((l) => l())
    },
}

export function useCallDock(): DockState {
    return useSyncExternalStore(callDock.subscribe, callDock.get, callDock.get)
}

/** Screens that show a case listen for this to reload after a wrap-up saved a note. */
export const CALL_WRAPPED_EVENT = 'cawp:call-wrapped'
export type CallWrappedDetail = { attemptId: string; caseId: string | null; needsReview: boolean }

export function announceWrapped(detail: CallWrappedDetail) {
    window.dispatchEvent(new CustomEvent<CallWrappedDetail>(CALL_WRAPPED_EVENT, { detail }))
}
