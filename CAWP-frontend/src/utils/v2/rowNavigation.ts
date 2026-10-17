/**
 * Opening a case from a list row, in the same tab.
 *
 * A row opens the case when it is clicked anywhere that is not itself a
 * control. Ctrl/Cmd/Shift-click and middle-click open it in a new tab. The
 * list's URL travels in `location.state.from`, so the case screen can go
 * straight back to it, and the list's scroll position is saved first.
 */
import type { KeyboardEvent, MouseEvent } from 'react'
import type { NavigateFunction } from 'react-router-dom'
import { caseLink } from './links'
import { saveListScrollNow } from '@/utils/hooks/useListScrollRestore'

export type RowActivateEvent = MouseEvent<HTMLElement> | KeyboardEvent<HTMLElement>

/** Anything a click on which means "use this control", not "open the row". */
const INTERACTIVE_SELECTOR = [
    'a',
    'button',
    'input',
    'select',
    'textarea',
    'label',
    'summary',
    '[contenteditable="true"]',
    '[role="button"]',
    '[role="checkbox"]',
    '[role="menuitem"]',
    '[role="option"]',
    '[role="switch"]',
    '[role="dialog"]',
    '[role="listbox"]',
    '[role="combobox"]',
    '[data-no-row-click]',
].join(',')

/**
 * True when a click (or Enter) on a row must not open it: it landed on a
 * control inside the row, it came from outside the row's DOM (a dialog the
 * row opened is rendered in a portal, and React still bubbles its events to
 * the row), or the user has just selected text in the row (to copy it).
 */
export function shouldIgnoreRowClick(event: RowActivateEvent): boolean {
    if (event.defaultPrevented) return true
    const row = event.currentTarget
    const target = event.target
    if (!(target instanceof Node) || !row.contains(target)) return true
    const element = target instanceof Element ? target : target.parentElement
    const control = element?.closest(INTERACTIVE_SELECTOR)
    if (control && control !== row && row.contains(control)) return true
    const selection = typeof window.getSelection === 'function' ? window.getSelection() : null
    if (
        selection &&
        !selection.isCollapsed &&
        selection.toString().trim() !== '' &&
        ((selection.anchorNode && row.contains(selection.anchorNode)) ||
            (selection.focusNode && row.contains(selection.focusNode)))
    ) {
        return true
    }
    return false
}

/** Ctrl/Cmd/Shift-click or middle-click: the user asked for a new tab. */
export function wantsNewTab(event?: RowActivateEvent | null): boolean {
    if (!event) return false
    if ('button' in event && event.button === 1) return true
    return !!(event.ctrlKey || event.metaKey || event.shiftKey)
}

/** A plain left click (what a same-tab link should take over). */
export function isPlainLeftClick(event: MouseEvent<HTMLElement>): boolean {
    return event.button === 0 && !wantsNewTab(event)
}

/** Where we are now, as a path to come back to. Read from `window.location`
 *  so a hash written a moment ago (e.g. `case=`) is included. */
export function currentListPath(): string {
    const { pathname, search, hash } = window.location
    return `${pathname}${search}${hash}`
}

export type CaseNavState = { from?: string | { path?: string } }

/** The list to go back to, from a case screen's `location.state`. */
export function backPathFromState(state: unknown): string | null {
    const from = (state as CaseNavState | null)?.from
    const path = typeof from === 'string' ? from : from?.path
    return typeof path === 'string' && path.startsWith('/') && !path.startsWith('//') ? path : null
}

/* ---- The list's order, for the case screen's previous / next (caseListNav.ts) ---- */

const STORE_PREFIX = 'cawp:caseList:'
const MAX_IDS = 1000
const live = new Map<symbol, string[]>()

const clean = (ids: unknown[]): string[] => {
    const out: string[] = []
    const seen = new Set<string>()
    for (const raw of ids) {
        if (raw === undefined || raw === null || raw === '') continue
        const id = String(raw)
        if (!seen.has(id)) {
            seen.add(id)
            out.push(id)
        }
    }
    return out
}

/** A list shows these rows (in order); returns the unregister. */
export function registerListRows(ids: unknown[]): () => void {
    const token = Symbol('caseListRows')
    live.set(token, clean(ids))
    return () => {
        live.delete(token)
    }
}

/** A row of a registered list is being opened from `from` (the list URL): keep its page's order. */
export function rememberListOrder(from: string, caseId: string): void {
    const id = String(caseId)
    for (const ids of Array.from(live.values()).reverse()) {
        if (!ids.includes(id)) continue
        try {
            sessionStorage.setItem(STORE_PREFIX + from, JSON.stringify(ids.slice(0, MAX_IDS)))
        } catch {
            /* storage full or blocked: no prev/next, nothing else changes */
        }
        return
    }
}

export function listOrder(from: string | null): string[] | null {
    if (!from) return null
    try {
        const raw = sessionStorage.getItem(STORE_PREFIX + from)
        const ids = raw ? (JSON.parse(raw) as unknown) : null
        return Array.isArray(ids) ? clean(ids) : null
    } catch {
        return null
    }
}

/**
 * Open a case: in a new tab when the event asks for one, otherwise in this
 * tab with `state.from` set to the current list URL (and the order of the
 * list's page remembered under it, for previous / next).
 */
export function openCaseInApp(
    navigate: NavigateFunction,
    caseId: string | number,
    event?: RowActivateEvent | null
): void {
    const url = caseLink(String(caseId))
    if (wantsNewTab(event)) {
        window.open(url, '_blank', 'noopener')
        return
    }
    saveListScrollNow()
    const from = currentListPath()
    rememberListOrder(from, String(caseId))
    navigate(url, { state: { from } })
}
