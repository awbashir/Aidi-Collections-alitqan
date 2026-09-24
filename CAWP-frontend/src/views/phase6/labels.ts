/**
 * Labels, tones and formatting shared by the Phase 6 screens (no components here, so the
 * component files keep fast refresh).
 *
 * Honesty rules: a percentage the API did not compute (`null`: nothing was assessed) is "—"
 * with an explanation, never 0 %; an unknown count / model / latency stays unknown.
 */
import type { StatusTone } from '@/components/shared/StatusPill'
import type { AiCallOutcome, DraftStatus, QcCriterion, QcRatio, QcResult, QcStatus, QcTemplate, Translate } from '@/services/v2'

export const QC_STATUS_TONE: Record<QcStatus, StatusTone> = {
    new: 'neutral',
    transcribed: 'info',
    ai_draft: 'warning',
    human_draft: 'info',
    approved: 'success',
}

export const QC_RESULT_TONE: Record<QcResult, StatusTone> = {
    met: 'success',
    needs_improvement: 'warning',
    not_applicable: 'neutral',
    insufficient_evidence: 'neutral',
}

export const CALL_OUTCOME_TONE: Record<AiCallOutcome, StatusTone> = {
    started: 'info',
    succeeded: 'success',
    failed: 'danger',
    refused: 'warning',
    interrupted: 'warning',
}

export const DRAFT_STATUS_TONE: Record<DraftStatus, StatusTone> = {
    generated: 'info',
    edited: 'info',
    saved_as_note: 'success',
    discarded: 'neutral',
}

/** "72.5 %" or "—" (null = nothing assessed; the caller explains why). */
export const formatPercent = (r: QcRatio | undefined) => (r ? `${r.percent.toFixed(1)} %` : '—')

/** "3 / 4" for a ratio, or "—". */
export const formatFraction = (r: QcRatio | undefined) => (r ? `${r.numerator} / ${r.denominator}` : '—')

/** Seconds as m:ss.s (timestamps on a recording); null stays "—". */
export function formatStamp(seconds: number | null | undefined): string {
    if (seconds === null || seconds === undefined || Number.isNaN(seconds)) return '—'
    const s = Math.max(0, seconds)
    const m = Math.floor(s / 60)
    const rest = s - m * 60
    return `${m}:${rest < 10 ? '0' : ''}${rest.toFixed(1)}`
}

/** Bytes as KB / MB, isolated left-to-right so "93.8 KB" keeps its order inside Arabic text. */
export function formatBytes(n: number | null | undefined): string {
    if (n === null || n === undefined) return '—'
    const text = n < 1024 ? `${n} B` : n < 1024 * 1024 ? `${(n / 1024).toFixed(1)} KB` : `${(n / (1024 * 1024)).toFixed(2)} MB`
    return `⁦${text}⁩`
}

const isAr = (lang: string | undefined) => (lang || 'ar').toLowerCase().startsWith('ar')

export const criterionName = (c: Pick<QcCriterion, 'en' | 'ar'> | { name_en?: string | null; name_ar?: string | null }, lang: string | undefined) => {
    const x = c as { en?: string; ar?: string; name_en?: string | null; name_ar?: string | null }
    return (isAr(lang) ? x.ar || x.name_ar : x.en || x.name_en) || x.en || x.name_en || ''
}

export const criterionGuidance = (c: QcCriterion, lang: string | undefined) => (isAr(lang) ? c.guidance_ar : c.guidance_en)

export const criterionAppliesWhen = (c: QcCriterion, lang: string | undefined) => (isAr(lang) ? c.applies_when_ar : c.applies_when_en)

export const templateName = (tpl: Pick<QcTemplate, 'name_en' | 'name_ar'> | null | undefined, lang: string | undefined) =>
    tpl ? (isAr(lang) ? tpl.name_ar : tpl.name_en) : ''

export const resultLabel = (t: Translate, r: string | null | undefined) => (r ? t(`p6.result.${r}`, { defaultValue: r }) : '—')

export const qcStatusLabel = (t: Translate, s: string) => t(`p6.qc.status.${s}`, { defaultValue: s })

export const draftKindLabel = (t: Translate, k: string) => t(`p6.kind.${k}`, { defaultValue: k })

export const capabilityLabel = (t: Translate, c: string) => t(`p6.capability.${c}`, { defaultValue: c })

/** A provider-call purpose in words (`ai_draft_<kind>`, `qc_transcription`, ...). */
export const purposeLabel = (t: Translate, p: string) => {
    const kind = /^ai_draft_(.+)$/.exec(p)
    if (kind) return `${t('p6.purpose.ai_draft')} · ${draftKindLabel(t, kind[1])}`
    return t(`p6.purpose.${p}`, { defaultValue: p.replace(/_/g, ' ') })
}
