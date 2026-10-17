/**
 * The docked live-call panel's state, shared by every screen in the tab (one small store,
 * read with useSyncExternalStore). The case page and the calling workspace ask it to show
 * a call; the dock polls the live view and asks for the wrap-up when the call ends: in the
 * call's client page (note tab) when that page is open, else in the wrap-up dialog.
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
    /**
     * 2026-10-17: the call whose result is entered in its client page's note tab, because that
     * page is open (no dialog then). Back to the dialog when the page closes unsaved.
     */
    resultFor: { attemptId: string; caseId: string } | null
    /** Bumped to ask the client page to show its result form; `force` = the user asked (switch tab). */
    focus: { tick: number; force: boolean }
}

const initial = (): DockState => ({
    attemptId: null,
    minimized: false,
    wrapupFor: null,
    autoOpened: [],
    dismissed: [],
    resultFor: null,
    focus: { tick: 0, force: false },
})

let state: DockState = initial()
const listeners = new Set<() => void>()
/** A live view the caller already holds (the POST /v2/calls answer), shown before the first poll. */
const seeds = new Map<string, LiveCall>()
/** Client pages open in this tab (case id -> how many), see `claimCase`. */
const claimed = new Map<string, number>()
/** The number last dialed per case in this tab (prefills the client page's note form). */
const dialed = new Map<string, DialedNumber>()

export type DialedNumber = { contactId: string | null; value: string | null; attemptId: string }

/** The number a call dialed (`value` null when hidden: no `clints_details`). */
export const dialedOf = (v: Pick<LiveCall, 'id' | 'contact' | 'destination'>): DialedNumber => ({
    attemptId: v.id,
    contactId: v.contact?.id ?? null,
    value: v.contact?.value ?? v.destination ?? null,
})

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
    /**
     * Ask for the wrap-up of a call. When the call's client page is open in this tab (`caseId`
     * claimed), the result is entered there, in the note tab, and no dialog opens; otherwise
     * (campaign calls, other screens) the wrap-up dialog opens.
     */
    openWrapup(attemptId: string, auto = false, caseId?: string | null) {
        if (auto && state.autoOpened.includes(attemptId)) return
        const autoOpened = auto ? [...state.autoOpened.slice(-50), attemptId] : state.autoOpened
        if (caseId && claimed.has(caseId)) {
            set({ resultFor: { attemptId, caseId }, wrapupFor: null, autoOpened, focus: { tick: state.focus.tick + 1, force: !auto } })
            return
        }
        set({ wrapupFor: attemptId, autoOpened })
    },
    /** The dock's "go to the form" button: the client page switches to its note tab. */
    focusResult() {
        if (state.resultFor) set({ focus: { tick: state.focus.tick + 1, force: true } })
    },
    /** The result was saved in the client page. */
    resultDone(attemptId: string) {
        if (state.resultFor?.attemptId === attemptId) set({ resultFor: null })
    },
    /**
     * A client page is open on `caseId`: a call of that case is wrapped up in the page. Returns
     * the release; a result still pending when its page closes goes back to the dialog.
     */
    claimCase(caseId: string) {
        claimed.set(caseId, (claimed.get(caseId) || 0) + 1)
        return () => {
            const n = (claimed.get(caseId) || 1) - 1
            if (n > 0) claimed.set(caseId, n)
            else claimed.delete(caseId)
            /* after this render: a re-mount (same case) claims it again first */
            setTimeout(() => {
                const r = state.resultFor
                if (r && r.caseId === caseId && !claimed.has(caseId)) set({ resultFor: null, wrapupFor: r.attemptId })
            }, 0)
        }
    },
    rememberDialed(caseId: string, d: DialedNumber) {
        dialed.set(caseId, d)
    },
    lastDialed(caseId: string | null | undefined): DialedNumber | null {
        return caseId ? dialed.get(caseId) || null : null
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
        dialed.clear()
        state = initial()
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
