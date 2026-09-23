/**
 * Shared hooks and helpers of the Phase 2 bulk wizard and jobs screen.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useAppSelector } from '@/store'
import { BULK_ACTIVE, bulkApi } from '@/services/v2'
import type { BulkJobDetail, BulkKind, BulkSource, BulkStatus } from '@/services/v2'

/* ------------------------------------------------------------------ kinds */

export type KindsState = {
    kinds: BulkKind[]
    byKey: Record<string, BulkKind>
    loading: boolean
    error: unknown
    /** Allowed for the caller (the server's `allowed`). False while loading. */
    allowed: (key: string) => boolean
}

/** `GET /v2/bulk/kinds`, cached per signed-in user. */
export function useBulkKinds(): KindsState {
    const user = useAppSelector((s) => s.auth.user.id) as string | undefined
    const [state, setState] = useState<{ kinds: BulkKind[]; loading: boolean; error: unknown }>({
        kinds: [],
        loading: true,
        error: null,
    })
    useEffect(() => {
        let alive = true
        bulkApi
            .kinds(user || 'anonymous')
            .then((kinds) => alive && setState({ kinds, loading: false, error: null }))
            .catch((error) => alive && setState({ kinds: [], loading: false, error }))
        return () => {
            alive = false
        }
    }, [user])
    return useMemo(() => {
        const byKey: Record<string, BulkKind> = {}
        state.kinds.forEach((k) => {
            byKey[k.key] = k
        })
        return { ...state, byKey, allowed: (key: string) => !!byKey[key]?.allowed }
    }, [state])
}

/** The kind's label in the current language. */
export function useKindLabel() {
    const { i18n } = useTranslation()
    const ar = (i18n.language || 'ar').startsWith('ar')
    return useCallback(
        (kind: { label_en?: string; label_ar?: string; en?: string; ar?: string } | null | undefined, fallback = '') => {
            if (!kind) return fallback
            const en = kind.label_en ?? kind.en
            const a = kind.label_ar ?? kind.ar
            return (ar ? a || en : en || a) || fallback
        },
        [ar]
    )
}

/** A mapping field / any bilingual pair in the current language. */
export function useIsArabic() {
    const { i18n } = useTranslation()
    return (i18n.language || 'ar').startsWith('ar')
}

/* ----------------------------------------------------------------- launch */

/**
 * What the calling screen hands to the wizard (`/jobs/new/<kind>`, router
 * state). Everything is optional: the wizard offers only the sources the
 * kind accepts and the caller supplied.
 */
export type BulkLaunch = {
    /** Where "back" goes, and its label key. */
    from?: { path: string; labelKey: string }
    /** Selected case ids (selection source). */
    caseIds?: string[]
    /** The calling list's current filter, exactly as the list sends it, and the count it shows. */
    filter?: { list: 'clients' | 'archive'; query: string; count?: number | null; applied?: number }
    /** Portfolio source (archive a portfolio) or round-robin prefill. */
    portfolioId?: string
    portfolioName?: string
    /** Preselect a source (e.g. "from a spreadsheet"). */
    source?: BulkSource
    /** Prefilled params (follow-up close-out filters, round-robin contract…). */
    params?: Record<string, unknown>
}

/** Opens the wizard for a kind with the caller's context. */
export function useLaunchBulk() {
    const navigate = useNavigate()
    return useCallback(
        (kind: string, launch: BulkLaunch = {}) => {
            navigate(`/jobs/new/${encodeURIComponent(kind)}`, { state: { bulkLaunch: launch } })
        },
        [navigate]
    )
}

/* ------------------------------------------------------------------- jobs */

export const isActive = (s: BulkStatus | undefined) => !!s && BULK_ACTIVE.includes(s)

/**
 * Loads a job and polls it (1.5 s) while it is moving. Keeps the last good copy
 * on screen while a poll is in flight; a failed poll is shown, never hidden.
 */
export function useJob(id: string | undefined) {
    const [job, setJob] = useState<BulkJobDetail | undefined>(undefined)
    const [error, setError] = useState<unknown>(null)
    const [loading, setLoading] = useState(true)
    const [tick, setTick] = useState(0)
    const timer = useRef<ReturnType<typeof setTimeout> | null>(null)

    useEffect(() => {
        if (!id) return undefined
        let alive = true
        const ctrl = new AbortController()
        setLoading(true)
        bulkApi
            .job(id, ctrl.signal)
            .then((j) => {
                if (!alive) return
                setJob(j)
                setError(null)
                setLoading(false)
                if (isActive(j.status)) {
                    timer.current = setTimeout(() => setTick((n) => n + 1), 1500)
                }
            })
            .catch((e) => {
                if (!alive || (e as { code?: string })?.code === 'canceled') return
                setError(e)
                setLoading(false)
            })
        return () => {
            alive = false
            ctrl.abort()
            if (timer.current) clearTimeout(timer.current)
        }
    }, [id, tick])

    const reload = useCallback(() => {
        if (timer.current) clearTimeout(timer.current)
        setTick((n) => n + 1)
    }, [])
    return { job, error, loading, reload, setJob }
}

/** Short id for display: the first 8 characters. */
export const shortId = (id: string | null | undefined) => (id ? id.slice(0, 8) : '—')

/** A person as "Name (login)". */
export const personLabel = (p: { name?: string | null; user_name?: string | null } | null | undefined) => {
    if (!p) return '—'
    const name = (p.name || '').trim()
    if (name && p.user_name) return `${name} (${p.user_name})`
    return name || p.user_name || '—'
}

/** Format bytes as KB / MB with exact integers under 1 KB. */
export const formatBytes = (n: number | null | undefined) => {
    if (n === null || n === undefined) return '—'
    if (n < 1024) return `${n} B`
    if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
    return `${(n / 1024 / 1024).toFixed(2)} MB`
}
