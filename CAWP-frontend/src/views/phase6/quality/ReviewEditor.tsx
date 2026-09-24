/**
 * The review of one recording, criterion by criterion (key `quality_review`; approval
 * `quality_approve`).
 *
 * The evidence rule (docs/phase6-spec.md hard rule 5; services/ai/quality_evidence.py):
 * - every criterion needs a result and a reason;
 * - "met" / "needs improvement" need an exact transcript quotation (picked from the saved
 *   transcript's segments and checked here before saving, the same way the server checks it) or
 *   a human-reviewed audio timestamp captured from the player;
 * - a "human listening only" criterion (audible delivery) needs an audio timestamp: a quotation is
 *   not enough, and AI can never give it;
 * - a criterion that compares the employee notes / the recorded action needs that input.
 *
 * Saving (`PUT …/review`) writes a new immutable revision; a refusal (`422 evidence_rule_failed`)
 * is shown at each criterion. An AI review draft (`POST …/draft`) is only a starting point: the
 * items the server repaired to "insufficient evidence" say why, and it cannot be approved until a
 * human saves the review. Approval (`POST …/approve`) is an explicit, confirmed human act that
 * writes an immutable snapshot.
 */
import classNames from 'classnames'
import { useEffect, useMemo, useState } from 'react'
import type { MutableRefObject } from 'react'
import { useTranslation } from 'react-i18next'
import { HiOutlineTrash } from 'react-icons/hi'
import { BadgeCheck, Clock, Quote, Sparkles } from 'lucide-react'
import Button from '@/components/ui/Button'
import Input from '@/components/ui/Input'
import ActionDialog, { DialogField } from '@/components/v2/ActionDialog'
import { Ltr, RiyadhTime } from '@/components/v2/Display'
import { InlineError, Note } from '@/components/v2/States'
import ToastNotification from '@/components/shared/myComponents/ToastNotification'
import { Segmented } from '@/views/phase1/followUps/parts'
import { QC_RESULTS, describeP6Error, evidenceCodeText, evidenceErrors, qualityApi } from '@/services/v2'
import type { AiCapability, QcCriterion, QcEvidence, QcRecording, QcResult, QcReviewItem, QcSegment, QcTemplate, Translate } from '@/services/v2'
import { useActionKey } from '@/utils/v2/hooks'
import { Card, Notice } from '@/views/phase5/parts'
import { AiErrorNotice, AiReadinessNotice, CheckField, RatioText, ResultBadge } from '../parts'
import { criterionAppliesWhen, criterionGuidance, criterionName, formatStamp, resultLabel } from '../labels'
import { checkQuote, segmentSpan } from './quotes'

const describe = (e: unknown, t: Translate) => describeP6Error(e, t)
type Lang = 'ar' | 'en'

type QuoteDraft = { kind: 'quote'; text: string; segment_ids: string[] }
type AudioDraft = { kind: 'audio'; start: string; end: string; human_reviewed: boolean }
type EvidenceDraft = QuoteDraft | AudioDraft
type ItemDraft = { result: QcResult | ''; reason: string; evidence: EvidenceDraft[]; ai?: { result: QcResult | null | undefined; codes: string[] } }

const ASSESSED: QcResult[] = ['met', 'needs_improvement']

const itemFrom = (it: QcReviewItem | undefined, fromAi: boolean): ItemDraft => ({
    result: it?.result || '',
    reason: it?.reason || '',
    evidence: (it?.evidence || []).map((e) =>
        e.type === 'quote'
            ? { kind: 'quote' as const, text: e.text, segment_ids: e.segment_ids || [] }
            : { kind: 'audio' as const, start: String(e.start), end: e.end === null || e.end === undefined ? '' : String(e.end), human_reviewed: !!e.human_reviewed }
    ),
    ...(fromAi && it ? { ai: { result: it.ai_result, codes: it.validation || [] } } : {}),
})

const toEvidence = (e: EvidenceDraft): QcEvidence =>
    e.kind === 'quote'
        ? { type: 'quote', text: e.text, ...(e.segment_ids.length ? { segment_ids: e.segment_ids } : {}) }
        : ({ type: 'audio', start: Number(e.start), ...(e.end.trim() ? { end: Number(e.end) } : {}), human_reviewed: e.human_reviewed } as unknown as QcEvidence)

/** What the client can already see before saving (the server still decides). */
function clientHints(c: QcCriterion, it: ItemDraft, recording: QcRecording): string[] {
    const hints: string[] = []
    if (!it.result) hints.push('invalid_result')
    if (!it.reason.trim()) hints.push('reason_required')
    const tr = recording.transcript
    const duration = recording.audio.duration_seconds
    let validQuotes = 0
    let validAudio = 0
    it.evidence.forEach((e) => {
        if (e.kind === 'quote') {
            const r = checkQuote(e.text, e.segment_ids, tr?.purged ? null : tr?.segments, tr?.purged ? null : tr?.text)
            if (r.ok) validQuotes += 1
            else hints.push(r.code)
        } else {
            const s = Number(e.start)
            const en = e.end.trim() ? Number(e.end) : null
            if (!recording.audio.available) hints.push('audio_unavailable')
            else if (!e.human_reviewed) hints.push('audio_not_human_reviewed')
            else if (e.start.trim() === '' || !Number.isFinite(s) || s < 0 || s > duration || (en !== null && (!Number.isFinite(en) || en < s || en > duration)))
                hints.push('invalid_timestamp')
            else validAudio += 1
        }
    })
    if (it.result && ASSESSED.includes(it.result)) {
        if (c.human_listening_only) {
            if (!validAudio) hints.push('audio_evidence_required')
        } else if (!validQuotes && !validAudio) hints.push('evidence_required')
        if (c.requires_input) {
            const v = c.requires_input === 'notes' ? recording.evidence.notes : recording.evidence.recorded_action
            if (!(v || '').trim()) hints.push('input_missing')
        }
    }
    return Array.from(new Set(hints))
}

/* ------------------------------------------------------------------ segment picker */

const SegmentPicker = ({ segments, onPick, onCancel }: { segments: QcSegment[]; onPick: (ids: string[]) => void; onCancel: () => void }) => {
    const { t } = useTranslation()
    const [picked, setPicked] = useState<string[]>([])
    return (
        <div className="mt-2 rounded-md border border-azure-100 bg-info-tint p-2 dark:border-night-border dark:bg-night-raised" data-testid="p6-qc-seg-picker">
            <div className="mb-1 text-[12px] text-gray-700 dark:text-night-body">{t('p6.qc.review.pickHint')}</div>
            <div className="max-h-[220px] overflow-y-auto">
                {segments.map((s) => (
                    <div key={s.id} className="py-0.5" data-segment={s.id}>
                        <CheckField
                            checked={picked.includes(s.id)}
                            onChange={(c) => setPicked(c ? [...picked, s.id] : picked.filter((x) => x !== s.id))}
                            testId={`p6-qc-pick-${s.id}`}
                        >
                            <span className="text-[12.5px]">
                                <Ltr className="me-1 text-gray-500">{s.id}</Ltr>
                                {s.speaker && <Ltr className="me-1 font-extrabold">{s.speaker}</Ltr>}
                                <span dir="auto">{s.text}</span>
                            </span>
                        </CheckField>
                    </div>
                ))}
            </div>
            <div className="mt-2 flex justify-end gap-2">
                <Button size="xs" onClick={onCancel}>
                    {t('cancel')}
                </Button>
                <Button size="xs" variant="solid" disabled={!picked.length} aria-disabled={!picked.length} onClick={() => onPick(picked)} data-testid="p6-qc-pick-use">
                    {t('p6.qc.review.useSegments')}
                </Button>
            </div>
        </div>
    )
}

/* ------------------------------------------------------------------ one criterion */

const CriterionCard = ({
    criterion,
    item,
    onChange,
    recording,
    serverErrors,
    canEdit,
    getTime,
    seek,
}: {
    criterion: QcCriterion
    item: ItemDraft
    onChange: (it: ItemDraft) => void
    recording: QcRecording
    serverErrors: string[] | undefined
    canEdit: boolean
    getTime: () => number | null
    seek: (s: number) => void
}) => {
    const { t, i18n } = useTranslation()
    const [picking, setPicking] = useState(false)
    const tr = recording.transcript
    const segments = tr && !tr.purged ? tr.segments || [] : []
    const hints = clientHints(criterion, item, recording)
    const appliesWhen = criterionAppliesWhen(criterion, i18n.language)
    const setEv = (i: number, e: EvidenceDraft) => onChange({ ...item, evidence: item.evidence.map((x, j) => (j === i ? e : x)) })
    const now = getTime()
    return (
        <section
            className={classNames(
                'rounded-lg border bg-white p-4 dark:bg-night-card',
                serverErrors?.length ? 'border-red-300 dark:border-danger' : 'border-gray-300 dark:border-night-border'
            )}
            data-testid="p6-qc-criterion"
            data-criterion={criterion.id}
            data-result={item.result}
            data-server-errors={(serverErrors || []).join(',')}
            data-hints={hints.join(',')}
        >
            <div className="mb-1 flex flex-wrap items-center justify-between gap-2">
                <h6 className="mb-0 text-[14.5px] font-extrabold text-navy-600 dark:text-gray-100">{criterionName(criterion, i18n.language)}</h6>
                <div className="flex flex-wrap gap-1.5 text-[11px]">
                    {criterion.human_listening_only && (
                        <span className="rounded-pill bg-warning-tint px-2 py-0.5 font-extrabold text-warning" data-flag="human_listening_only">
                            {t('p6.qc.flag.humanListening')}
                        </span>
                    )}
                    {criterion.conditional && (
                        <span className="rounded-pill bg-slate-100 px-2 py-0.5 font-extrabold text-gray-700 dark:bg-night-raised dark:text-night-body" data-flag="conditional">
                            {t('p6.qc.flag.conditional')}
                        </span>
                    )}
                    {criterion.requires_input && (
                        <span className="rounded-pill bg-slate-100 px-2 py-0.5 font-extrabold text-gray-700 dark:bg-night-raised dark:text-night-body" data-flag="requires_input">
                            {t(`p6.qc.flag.requires_${criterion.requires_input}`)}
                        </span>
                    )}
                </div>
            </div>
            <div className="mb-2 text-[12.5px] text-gray-600">{criterionGuidance(criterion, i18n.language)}</div>
            {appliesWhen && <div className="mb-2 text-[12px] text-gray-600">{t('p6.qc.review.appliesWhen', { when: appliesWhen })}</div>}
            {item.ai && (
                <Notice tone={item.ai.codes.length ? 'warning' : 'info'} className="mb-2" testId="p6-qc-ai-item">
                    <div>
                        {t('p6.qc.review.aiSaid')} <ResultBadge result={item.ai.result || null} size="row" />
                    </div>
                    {item.ai.codes.length > 0 && (
                        <ul className="mt-1 list-disc ps-5 text-[12.5px]" data-codes={item.ai.codes.join(',')}>
                            {item.ai.codes.map((c) => (
                                <li key={c}>{evidenceCodeText(t, c)}</li>
                            ))}
                        </ul>
                    )}
                    {item.result === 'insufficient_evidence' && item.ai.codes.length > 0 && (
                        <div className="mt-1 text-[12px] text-gray-600">{t('p6.qc.review.aiRepaired')}</div>
                    )}
                </Notice>
            )}
            <div role="radiogroup" aria-label={String(t('p6.qc.review.result'))} className="mb-2 flex flex-wrap gap-2" data-testid="p6-qc-result">
                {QC_RESULTS.map((r) => (
                    <button
                        key={r}
                        type="button"
                        role="radio"
                        aria-checked={item.result === r}
                        disabled={!canEdit}
                        data-result={r}
                        onClick={() => onChange({ ...item, result: r })}
                        className={classNames(
                            'h-8 rounded-md border px-3 text-[12.5px] focus:outline-none focus-visible:ring-2 focus-visible:ring-azure-500',
                            item.result === r ? 'border-navy-600 bg-navy-600 font-extrabold text-white dark:bg-azure-600' : 'border-gray-400 bg-white text-gray-700 hover:bg-gray-50 dark:border-night-border dark:bg-night-card dark:text-night-body'
                        )}
                    >
                        {resultLabel(t, r)}
                    </button>
                ))}
            </div>
            <DialogField label={t('p6.qc.review.reason')} required>
                <Input textArea dir="auto" maxLength={2000} disabled={!canEdit} value={item.reason} onChange={(e) => onChange({ ...item, reason: e.target.value })} data-testid="p6-qc-reason" />
            </DialogField>
            <div className="text-eyebrow mb-1">{t('p6.qc.review.evidence')}</div>
            {item.evidence.length === 0 && <div className="mb-2 text-[12.5px] text-gray-600">{t('p6.qc.review.noEvidence')}</div>}
            <div className="flex flex-col gap-2">
                {item.evidence.map((e, i) => {
                    if (e.kind === 'quote') {
                        const r = checkQuote(e.text, e.segment_ids, tr?.purged ? null : tr?.segments, tr?.purged ? null : tr?.text)
                        const span = segmentSpan(r.ok ? r.segmentIds : e.segment_ids, segments)
                        return (
                            <div key={i} className="rounded-md border border-gray-300 p-2 dark:border-night-border" data-testid="p6-qc-quote" data-verbatim={r.ok ? '1' : '0'} data-code={r.ok ? '' : r.code}>
                                <div className="mb-1 flex flex-wrap items-center justify-between gap-2 text-[12px]">
                                    <span className="flex items-center gap-1 text-gray-600">
                                        <Quote aria-hidden size={13} />
                                        {e.segment_ids.length ? <Ltr>{e.segment_ids.join(', ')}</Ltr> : t('p6.qc.review.anywhere')}
                                        {span.start !== null && (
                                            <button type="button" className="ms-1 text-azure-600 hover:underline" onClick={() => seek(span.start as number)}>
                                                <Ltr>{formatStamp(span.start)}</Ltr>
                                            </button>
                                        )}
                                    </span>
                                    <span className={r.ok ? 'font-extrabold text-success' : 'font-extrabold text-danger'}>
                                        {r.ok ? t('p6.qc.review.verbatim') : evidenceCodeText(t, r.code)}
                                    </span>
                                </div>
                                <div className="flex items-start gap-2">
                                    <Input
                                        size="sm"
                                        dir="auto"
                                        disabled={!canEdit}
                                        value={e.text}
                                        maxLength={1000}
                                        onChange={(ev) => setEv(i, { ...e, text: ev.target.value })}
                                        data-testid="p6-qc-quote-text"
                                    />
                                    {canEdit && (
                                        <button
                                            type="button"
                                            aria-label={String(t('p6.qc.review.remove'))}
                                            className="mt-1.5 rounded p-1 text-gray-600 hover:bg-gray-100 dark:hover:bg-night-raised"
                                            onClick={() => onChange({ ...item, evidence: item.evidence.filter((_, j) => j !== i) })}
                                        >
                                            <HiOutlineTrash />
                                        </button>
                                    )}
                                </div>
                            </div>
                        )
                    }
                    return (
                        <div key={i} className="rounded-md border border-gray-300 p-2 dark:border-night-border" data-testid="p6-qc-stamp" data-human={e.human_reviewed ? '1' : '0'}>
                            <div className="flex flex-wrap items-end gap-2">
                                <div className="w-[110px]">
                                    <div className="text-eyebrow mb-1">{t('p6.qc.transcript.start')}</div>
                                    <Input size="sm" dir="ltr" inputMode="decimal" disabled={!canEdit} value={e.start} onChange={(ev) => setEv(i, { ...e, start: ev.target.value })} data-testid="p6-qc-stamp-start" />
                                </div>
                                <div className="w-[110px]">
                                    <div className="text-eyebrow mb-1">{t('p6.qc.transcript.end')}</div>
                                    <Input size="sm" dir="ltr" inputMode="decimal" disabled={!canEdit} value={e.end} onChange={(ev) => setEv(i, { ...e, end: ev.target.value })} data-testid="p6-qc-stamp-end" />
                                </div>
                                {canEdit && (
                                    <>
                                        <Button size="xs" icon={<Clock size={13} />} disabled={now === null} onClick={() => setEv(i, { ...e, start: (getTime() ?? 0).toFixed(1) })} data-testid="p6-qc-stamp-capture-start">
                                            {t('p6.qc.review.captureStart')}
                                        </Button>
                                        <Button size="xs" icon={<Clock size={13} />} disabled={now === null} onClick={() => setEv(i, { ...e, end: (getTime() ?? 0).toFixed(1) })} data-testid="p6-qc-stamp-capture-end">
                                            {t('p6.qc.review.captureEnd')}
                                        </Button>
                                    </>
                                )}
                                {e.start.trim() !== '' && Number.isFinite(Number(e.start)) && (
                                    <Button size="xs" variant="plain" onClick={() => seek(Number(e.start))}>
                                        {t('p6.qc.review.playFrom', { at: formatStamp(Number(e.start)) })}
                                    </Button>
                                )}
                                {canEdit && (
                                    <button
                                        type="button"
                                        aria-label={String(t('p6.qc.review.remove'))}
                                        className="ms-auto rounded p-1 text-gray-600 hover:bg-gray-100 dark:hover:bg-night-raised"
                                        onClick={() => onChange({ ...item, evidence: item.evidence.filter((_, j) => j !== i) })}
                                    >
                                        <HiOutlineTrash />
                                    </button>
                                )}
                            </div>
                            <div className="mt-2">
                                <CheckField checked={e.human_reviewed} disabled={!canEdit} onChange={(c) => setEv(i, { ...e, human_reviewed: c })} testId="p6-qc-stamp-human">
                                    <span className="text-[12.5px]">{t('p6.qc.review.listened')}</span>
                                </CheckField>
                            </div>
                        </div>
                    )
                })}
            </div>
            {canEdit && (
                <div className="mt-2 flex flex-wrap gap-2">
                    <Button size="xs" icon={<Quote size={13} />} disabled={!segments.length} onClick={() => setPicking(true)} data-testid="p6-qc-add-quote">
                        {t('p6.qc.review.addQuote')}
                    </Button>
                    <Button
                        size="xs"
                        icon={<Clock size={13} />}
                        disabled={!recording.audio.available}
                        aria-disabled={!recording.audio.available}
                        onClick={() => onChange({ ...item, evidence: [...item.evidence, { kind: 'audio', start: now === null ? '' : now.toFixed(1), end: '', human_reviewed: false }] })}
                        data-testid="p6-qc-add-stamp"
                    >
                        {t('p6.qc.review.addStamp')}
                    </Button>
                    {now === null && recording.audio.available && <span className="self-center text-[11.5px] text-gray-600">{t('p6.qc.review.loadAudioFirst')}</span>}
                </div>
            )}
            {picking && (
                <SegmentPicker
                    segments={segments}
                    onCancel={() => setPicking(false)}
                    onPick={(ids) => {
                        const order = segments.filter((s) => ids.includes(s.id))
                        onChange({ ...item, evidence: [...item.evidence, { kind: 'quote', text: order.map((s) => s.text).join(' '), segment_ids: order.map((s) => s.id) }] })
                        setPicking(false)
                    }}
                />
            )}
            {serverErrors && serverErrors.length > 0 && (
                <div role="alert" className="mt-2 rounded-md border border-red-200 bg-danger-tint px-3 py-2 text-[12.5px] text-danger dark:border-night-border dark:bg-night-raised" data-testid="p6-qc-criterion-errors">
                    <div className="font-extrabold">{t('p6.qc.review.serverRefused')}</div>
                    <ul className="list-disc ps-5">
                        {serverErrors.map((c) => (
                            <li key={c} data-code={c}>
                                {evidenceCodeText(t, c)}
                            </li>
                        ))}
                    </ul>
                </div>
            )}
            {!serverErrors?.length && hints.length > 0 && canEdit && (
                <div className="mt-2 text-[12px] text-warning" data-testid="p6-qc-criterion-hints">
                    {t('p6.qc.review.beforeSaving')} {hints.map((h) => evidenceCodeText(t, h)).join(String(t('p6.listSep')))}
                </div>
            )}
        </section>
    )
}

/* ------------------------------------------------------------------ editor */

const ReviewEditor = ({
    recording,
    template,
    draftCap,
    canEdit,
    canApprove,
    audioRef,
    onSaved,
    onReload,
    onShowApprovals,
}: {
    recording: QcRecording
    template: QcTemplate | null
    draftCap: AiCapability | undefined
    canEdit: boolean
    canApprove: boolean
    audioRef: MutableRefObject<HTMLAudioElement | null>
    onSaved: (r: QcRecording) => void
    onReload: () => void
    onShowApprovals: () => void
}) => {
    const { t, i18n } = useTranslation()
    const review = recording.review
    const [items, setItems] = useState<Record<string, ItemDraft>>({})
    const [summary, setSummary] = useState('')
    const [coaching, setCoaching] = useState('')
    const [lang, setLang] = useState<Lang>((i18n.language || 'ar').startsWith('ar') ? 'ar' : 'en')
    const [busy, setBusy] = useState(false)
    const [error, setError] = useState<unknown>(null)
    const [serverErrors, setServerErrors] = useState<Record<string, string[]>>({})
    const [aiBusy, setAiBusy] = useState(false)
    const [aiError, setAiError] = useState<unknown>(null)
    const [approveOpen, setApproveOpen] = useState(false)
    const [approveConfirmed, setApproveConfirmed] = useState(false)
    const [, setTick] = useState(0)
    const saveKey = useActionKey(true)
    const aiKey = useActionKey(true)

    useEffect(() => {
        if (!template) return
        const map: Record<string, ItemDraft> = {}
        template.criteria.forEach((c) => {
            map[c.id] = itemFrom(review?.results.find((r) => r.criterion_id === c.id), review?.origin === 'ai')
        })
        setItems(map)
        setSummary(review?.summary || '')
        setCoaching(review?.coaching || '')
        if (review?.language === 'ar' || review?.language === 'en') setLang(review.language)
        setServerErrors({})
        setError(null)
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [review?.id, template?.key, template?.version])

    /* re-render every second while the audio plays, so "capture" buttons know a position exists */
    useEffect(() => {
        const id = setInterval(() => setTick((n) => n + 1), 1000)
        return () => clearInterval(id)
    }, [])
    const getTime = () => (audioRef.current ? audioRef.current.currentTime : null)
    const seek = (s: number) => {
        const a = audioRef.current
        if (!a) return
        a.currentTime = s
        a.play().catch(() => undefined)
    }

    const tr = recording.transcript
    const busyElsewhere = !!recording.processing && !recording.processing.stale
    const topErrors = serverErrors._ || []
    const failedCount = Object.keys(serverErrors).length
    const metricsNow = useMemo(() => {
        if (!template) return null
        const c = { met: 0, needs_improvement: 0, not_applicable: 0, insufficient_evidence: 0 }
        template.criteria.forEach((cr) => {
            const r = items[cr.id]?.result
            if (r) c[r] += 1
        })
        return c
    }, [items, template])

    if (!recording.evidence.template || !template) {
        return (
            <Notice tone="info" testId="p6-qc-review-needs-template">
                {t('p6.qc.review.needsTemplate')}
            </Notice>
        )
    }

    const save = async () => {
        setBusy(true)
        setError(null)
        setServerErrors({})
        try {
            const r = await qualityApi.saveReview(
                recording.id,
                {
                    expected_revision: recording.revision,
                    results: template.criteria.map((c) => {
                        const it = items[c.id] || itemFrom(undefined, false)
                        return { criterion_id: c.id, result: it.result, reason: it.reason, evidence: it.evidence.map(toEvidence) }
                    }),
                    ...(summary.trim() ? { summary: summary.trim() } : {}),
                    ...(coaching.trim() ? { coaching: coaching.trim() } : {}),
                    language: lang,
                    ...(review ? { based_on_review_id: review.id } : {}),
                },
                saveKey.key
            )
            saveKey.renew()
            ToastNotification(String(t('p6.qc.review.saved')), String(t('p6.qc.title')), 'success', 2500)
            onSaved(r)
        } catch (e) {
            const crit = evidenceErrors(e)
            if (crit) setServerErrors(crit)
            setError(e)
        } finally {
            setBusy(false)
        }
    }

    const aiDraft = async () => {
        setAiBusy(true)
        setAiError(null)
        try {
            const r = await qualityApi.aiDraft(recording.id, { expected_revision: recording.revision, language: lang }, aiKey.key)
            aiKey.renew()
            ToastNotification(String(t('p6.qc.review.aiSaved')), String(t('p6.qc.title')), 'success', 2500)
            onSaved(r)
        } catch (e) {
            setAiError(e)
            onReload()
        } finally {
            setAiBusy(false)
        }
    }

    const approvable = !!review && review.origin === 'human' && !recording.approval
    return (
        <div className="flex flex-col gap-4" data-testid="p6-qc-review" data-review={review?.id || ''} data-origin={review?.origin || ''}>
            <Card title={t('p6.qc.review.stateTitle')} testId="p6-qc-review-state">
                {review ? (
                    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[13px]">
                        <span className="font-extrabold text-navy-600 dark:text-gray-100">{t('p6.qc.review.revision', { version: review.version })}</span>
                        <span data-testid="p6-qc-review-origin" data-origin={review.origin}>
                            {review.origin === 'ai' ? t('p6.qc.review.originAi') : t('p6.qc.review.originHuman')}
                        </span>
                        <span className="text-gray-600">
                            {review.created_by?.name || '—'} · <RiyadhTime iso={review.created_at} />
                        </span>
                        {review.model && (
                            <span className="text-gray-600">
                                {t('p6.drafts.model')}: <Ltr>{review.model}</Ltr>
                            </span>
                        )}
                        <span>
                            {t('p6.qc.metric.compliance')}: <RatioText ratio={review.metrics.compliance} testId="p6-qc-review-compliance" />
                        </span>
                        <span>
                            {t('p6.qc.metric.coverage')}: <RatioText ratio={review.metrics.coverage} />
                        </span>
                    </div>
                ) : (
                    <Note>{t('p6.qc.review.none')}</Note>
                )}
                {review && !review.metrics.has_percentage && <Note className="mt-1">{t('p6.qc.noPercentHint')}</Note>}
                {review?.origin === 'ai' && (
                    <Notice tone="warning" className="mt-3" testId="p6-qc-review-ai-notice">
                        {t('p6.qc.review.aiNotice')}
                    </Notice>
                )}
                {recording.approval && (
                    <Notice tone="success" className="mt-3" testId="p6-qc-review-approved">
                        {t('p6.qc.review.approvedNotice', { by: recording.approval.approved_by?.name || '—' })} <RiyadhTime iso={recording.approval.approved_at} />{' '}
                        <button type="button" className="font-extrabold text-azure-600 underline underline-offset-2" onClick={onShowApprovals}>
                            {t('p6.qc.review.seeSnapshot')}
                        </button>
                    </Notice>
                )}
                <div className="mt-3 flex flex-wrap items-center gap-2">
                    {canApprove && (
                        <Button
                            size="sm"
                            variant="solid"
                            icon={<BadgeCheck size={15} />}
                            disabled={!approvable || busyElsewhere}
                            aria-disabled={!approvable || busyElsewhere}
                            onClick={() => {
                                setApproveConfirmed(false)
                                setApproveOpen(true)
                            }}
                            data-testid="p6-qc-approve"
                        >
                            {t('p6.qc.review.approve')}
                        </Button>
                    )}
                    {canApprove && !approvable && !recording.approval && <span className="text-[12px] text-gray-600">{review ? t('p6.qc.review.approveNeedsHuman') : t('p6.qc.review.approveNeedsReview')}</span>}
                    {!canApprove && <span className="text-[12px] text-gray-600">{t('p6.qc.review.noApproveKey')}</span>}
                </div>
            </Card>

            {canEdit && (
                <Card title={t('p6.qc.review.aiTitle')} testId="p6-qc-ai-draft">
                    <p className="mb-3 text-[13px] text-gray-700 dark:text-night-body">{t('p6.qc.review.aiHint')}</p>
                    <AiReadinessNotice capability={draftCap} className="mb-3" testId="p6-qc-ai-not-ready" />
                    {!tr && <Note className="mb-2">{t('p6.qc.review.aiNeedsTranscript')}</Note>}
                    <Button size="sm" icon={<Sparkles size={15} />} loading={aiBusy} disabled={!draftCap?.ready || !tr || tr.purged || busyElsewhere} onClick={aiDraft} data-testid="p6-qc-ai-draft-run">
                        {t('p6.qc.review.aiRun')}
                    </Button>
                    <AiErrorNotice error={aiError} className="mt-3" testId="p6-qc-ai-draft-error" />
                </Card>
            )}

            {!tr && <Notice tone="info">{t('p6.qc.review.noTranscript')}</Notice>}
            {failedCount > 0 && (
                <Notice tone="danger" testId="p6-qc-review-failed" title={t('p6.qc.review.failedTitle')}>
                    {t('p6.qc.review.failedBody', { count: failedCount })}
                    {topErrors.length > 0 && <div className="mt-1">{topErrors.map((c) => evidenceCodeText(t, c)).join(String(t('p6.listSep')))}</div>}
                </Notice>
            )}
            <div className="flex flex-col gap-3" data-testid="p6-qc-criteria">
                {template.criteria.map((c) => (
                    <CriterionCard
                        key={c.id}
                        criterion={c}
                        item={items[c.id] || itemFrom(undefined, false)}
                        onChange={(it) => setItems({ ...items, [c.id]: it })}
                        recording={recording}
                        serverErrors={serverErrors[c.id]}
                        canEdit={canEdit && !busyElsewhere}
                        getTime={getTime}
                        seek={seek}
                    />
                ))}
            </div>
            <Card title={t('p6.qc.review.summaryTitle')}>
                <div className="grid grid-cols-1 gap-x-4 md:grid-cols-2">
                    <DialogField label={t('p6.qc.review.summary')}>
                        <Input textArea dir="auto" maxLength={4000} disabled={!canEdit} value={summary} onChange={(e) => setSummary(e.target.value)} data-testid="p6-qc-summary" />
                    </DialogField>
                    <DialogField label={t('p6.qc.review.coaching')} hint={t('p6.qc.review.coachingHint')}>
                        <Input textArea dir="auto" maxLength={4000} disabled={!canEdit} value={coaching} onChange={(e) => setCoaching(e.target.value)} data-testid="p6-qc-coaching" />
                    </DialogField>
                </div>
                <div className="flex flex-wrap items-center justify-between gap-3">
                    <div className="flex items-center gap-3">
                        <span className="text-eyebrow">{t('p6.drafts.language')}</span>
                        <Segmented<Lang>
                            value={lang}
                            options={[
                                { value: 'ar', label: 'العربية' },
                                { value: 'en', label: 'English' },
                            ]}
                            onChange={setLang}
                            testId="p6-qc-review-lang"
                        />
                    </div>
                    {metricsNow && (
                        <span className="text-[12.5px] text-gray-600" data-testid="p6-qc-review-counts">
                            {QC_RESULTS.map((r) => `${resultLabel(t, r)}: ${metricsNow[r]}`).join(' · ')}
                        </span>
                    )}
                </div>
                <InlineError error={error} describe={describe} onReload={onReload} />
                {canEdit && (
                    <div className="mt-3 flex items-center justify-between gap-2">
                        <Note>{t('p6.qc.review.saveNote')}</Note>
                        <Button size="sm" variant="solid" loading={busy} disabled={busyElsewhere} aria-disabled={busyElsewhere} onClick={save} data-testid="p6-qc-review-save">
                            {t('p6.qc.review.save')}
                        </Button>
                    </div>
                )}
            </Card>
            <Note>{recording.note}</Note>

            <ActionDialog
                isOpen={approveOpen}
                title={t('p6.qc.review.approveTitle')}
                confirmLabel={t('p6.qc.review.approve')}
                canSubmit={approveConfirmed}
                testId="p6-qc-approve-dialog"
                describeError={describe}
                onReload={onReload}
                onClose={() => setApproveOpen(false)}
                onSubmit={async (key) => {
                    const r = await qualityApi.approve(recording.id, { expected_revision: recording.revision, confirmed: true }, key)
                    ToastNotification(String(t('p6.qc.review.approved')), String(t('p6.qc.title')), 'success', 2500)
                    onSaved(r)
                }}
            >
                {review && (
                    <div className="mb-3 grid grid-cols-2 gap-2 text-[13px]">
                        <span>{t('p6.qc.review.revision', { version: review.version })}</span>
                        <span>
                            {t('p6.qc.metric.compliance')}: <RatioText ratio={review.metrics.compliance} />
                        </span>
                        <span>{QC_RESULTS.map((r) => `${resultLabel(t, r)}: ${review.metrics[r]}`).join(' · ')}</span>
                        <span>
                            {t('p6.qc.metric.coverage')}: <RatioText ratio={review.metrics.coverage} />
                        </span>
                    </div>
                )}
                <p className="mb-3 text-[13px] text-gray-700 dark:text-night-body">{t('p6.qc.review.approveHint')}</p>
                <CheckField checked={approveConfirmed} onChange={setApproveConfirmed} testId="p6-qc-approve-confirm">
                    <span className="text-[13.5px]">{t('p6.qc.review.approveCheck')}</span>
                </CheckField>
            </ActionDialog>
        </div>
    )
}

export default ReviewEditor
