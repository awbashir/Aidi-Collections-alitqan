/**
 * The transcript of one recording (key `quality_review` to change it).
 *
 * - Transcribe (`POST …/transcribe`, the `audio` category): only when the provider is ready;
 *   otherwise the honest state, and a refusal is shown as the API gives it. Nothing is invented:
 *   without a diarizing model there are no speakers or timings.
 * - Manual transcript or correction (`PUT …/transcript`): plain text (one line per segment) or
 *   segments with start / end seconds and a speaker label. Every save is a new version.
 * - Speaker roles stay anonymous (A, B) until a human verifies them (agent / customer / other).
 * - Versions (`GET …/transcripts`). A new version clears the current review and approval.
 */
import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { HiOutlinePlus, HiOutlineTrash } from 'react-icons/hi'
import { AudioLines } from 'lucide-react'
import Button from '@/components/ui/Button'
import Input from '@/components/ui/Input'
import Select from '@/components/ui/Select'
import { DialogField } from '@/components/v2/ActionDialog'
import { Ltr, RiyadhTime } from '@/components/v2/Display'
import { InlineError, Note, SkeletonLines } from '@/components/v2/States'
import ToastNotification from '@/components/shared/myComponents/ToastNotification'
import { Segmented } from '@/views/phase1/followUps/parts'
import { describeP6Error, qualityApi } from '@/services/v2'
import type { AiCapability, Option, QcRecording, QcSpeakerRole, QcTranscript, QcTranscriptBody, Translate } from '@/services/v2'
import { useActionKey, useV2Query } from '@/utils/v2/hooks'
import { Card, Notice } from '@/views/phase5/parts'
import { AiErrorNotice, AiReadinessNotice } from '../parts'
import { formatStamp } from '../labels'

const describe = (e: unknown, t: Translate) => describeP6Error(e, t)
type Lang = 'ar' | 'en'
type Mode = 'text' | 'segments'
type SegRow = { start: string; end: string; speaker: string; text: string }

const toRows = (tr: QcTranscript | null): SegRow[] =>
    tr?.segments?.length
        ? tr.segments.map((s) => ({ start: s.start === null ? '' : String(s.start), end: s.end === null ? '' : String(s.end), speaker: s.speaker || '', text: s.text }))
        : [{ start: '', end: '', speaker: '', text: '' }]

const numOrNull = (v: string) => (v.trim() === '' ? null : Number(v))
const rowValid = (r: SegRow, duration: number) => {
    if (!r.text.trim()) return false
    const s = numOrNull(r.start)
    const e = numOrNull(r.end)
    if (s !== null && (!Number.isFinite(s) || s < 0 || s > duration + 1)) return false
    if (e !== null && (!Number.isFinite(e) || e < 0 || e > duration + 1)) return false
    if (s !== null && e !== null && e < s) return false
    return r.speaker.length <= 40
}

/** The saved transcript, segment by segment (ids are what quotations cite). */
export const TranscriptView = ({ transcript }: { transcript: QcTranscript }) => {
    const { t } = useTranslation()
    if (transcript.purged || !transcript.segments) {
        return (
            <Notice tone="neutral" testId="p6-qc-transcript-purged">
                {t('p6.qc.transcript.purged')}
            </Notice>
        )
    }
    return (
        <div className="max-h-[360px] overflow-y-auto rounded-md border border-gray-300 dark:border-night-border" data-testid="p6-qc-transcript-view">
            <table className="min-w-full text-[13px]">
                <tbody>
                    {transcript.segments.map((s) => {
                        const role = s.speaker ? transcript.speakers[s.speaker]?.role : undefined
                        return (
                            <tr key={s.id} className="border-b border-gray-200 align-top last:border-0 dark:border-night-border" data-segment={s.id}>
                                <td className="whitespace-nowrap px-2 py-1.5 text-[11.5px] text-gray-500">
                                    <Ltr>{s.id}</Ltr>
                                </td>
                                <td className="whitespace-nowrap px-2 py-1.5 text-[11.5px] text-gray-600">
                                    <Ltr>{s.start === null ? '—' : `${formatStamp(s.start)}–${formatStamp(s.end)}`}</Ltr>
                                </td>
                                <td className="whitespace-nowrap px-2 py-1.5 text-[12px]">
                                    {s.speaker ? (
                                        <span title={role ? String(t('p6.qc.transcript.verified')) : String(t('p6.qc.transcript.anonymous'))}>
                                            <Ltr className="font-extrabold">{s.speaker}</Ltr>
                                            {role ? <span className="ms-1 text-success">· {t(`p6.qc.role.${role}`)}</span> : <span className="ms-1 text-gray-500">· {t('p6.qc.transcript.unverified')}</span>}
                                        </span>
                                    ) : (
                                        <span className="text-gray-500">—</span>
                                    )}
                                </td>
                                <td className="px-2 py-1.5" dir="auto">
                                    {s.text}
                                </td>
                            </tr>
                        )
                    })}
                </tbody>
            </table>
        </div>
    )
}

const SpeakerRoles = ({ recording, canEdit, onSaved }: { recording: QcRecording; canEdit: boolean; onSaved: (r: QcRecording) => void }) => {
    const { t } = useTranslation()
    const tr = recording.transcript as QcTranscript
    const [roles, setRoles] = useState<Record<string, QcSpeakerRole | ''>>({})
    const [busy, setBusy] = useState(false)
    const [error, setError] = useState<unknown>(null)
    const { key, renew } = useActionKey(true)
    useEffect(() => {
        const r: Record<string, QcSpeakerRole | ''> = {}
        tr.speaker_labels.forEach((l) => {
            r[l] = tr.speakers[l]?.role || ''
        })
        setRoles(r)
    }, [tr])
    if (!tr.speaker_labels.length) return <Note>{t('p6.qc.transcript.noSpeakers')}</Note>
    const options: Option[] = (['agent', 'customer', 'other'] as QcSpeakerRole[]).map((r) => ({ value: r, label: String(t(`p6.qc.role.${r}`)) }))
    const changed = tr.speaker_labels.some((l) => (tr.speakers[l]?.role || '') !== roles[l])
    const save = async () => {
        setBusy(true)
        setError(null)
        try {
            const speakers: Record<string, QcSpeakerRole | null> = {}
            tr.speaker_labels.forEach((l) => {
                if ((tr.speakers[l]?.role || '') !== roles[l]) speakers[l] = (roles[l] || null) as QcSpeakerRole | null
            })
            const r = await qualityApi.saveTranscript(recording.id, { expected_revision: recording.revision, speakers }, key)
            renew()
            ToastNotification(String(t('p6.qc.transcript.rolesSaved')), String(t('p6.qc.title')), 'success', 2000)
            onSaved(r)
        } catch (e) {
            setError(e)
        } finally {
            setBusy(false)
        }
    }
    return (
        <div data-testid="p6-qc-speakers">
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
                {tr.speaker_labels.map((l) => (
                    <div key={l} data-speaker={l}>
                        <div className="text-eyebrow mb-1">
                            {t('p6.qc.transcript.speaker')} <Ltr>{l}</Ltr>
                        </div>
                        <Select
                            size="sm"
                            menuPosition="fixed"
                            isClearable
                            isDisabled={!canEdit}
                            placeholder={t('p6.qc.transcript.unverified')}
                            options={options}
                            value={options.find((o) => o.value === roles[l]) || null}
                            onChange={(o) => setRoles({ ...roles, [l]: ((o as Option | null)?.value || '') as QcSpeakerRole | '' })}
                        />
                        {tr.speakers[l]?.verified_by && (
                            <div className="mt-1 text-[11.5px] text-gray-600">
                                {tr.speakers[l].verified_by?.name || '—'} · <RiyadhTime iso={tr.speakers[l].verified_at} />
                            </div>
                        )}
                    </div>
                ))}
            </div>
            <InlineError error={error} describe={describe} />
            {canEdit && (
                <div className="mt-3 flex justify-end">
                    <Button size="sm" loading={busy} disabled={!changed} aria-disabled={!changed} onClick={save} data-testid="p6-qc-speakers-save">
                        {t('p6.qc.transcript.saveRoles')}
                    </Button>
                </div>
            )}
        </div>
    )
}

const TranscriptEditor = ({
    recording,
    transcribeCap,
    canEdit,
    onSaved,
    onReload,
}: {
    recording: QcRecording
    transcribeCap: AiCapability | undefined
    canEdit: boolean
    onSaved: (r: QcRecording) => void
    onReload: () => void
}) => {
    const { t, i18n } = useTranslation()
    const tr = recording.transcript
    const [mode, setMode] = useState<Mode>(tr?.diarized || tr?.segments?.some((s) => s.start !== null) ? 'segments' : 'text')
    const [text, setText] = useState(tr?.text || '')
    const [rows, setRows] = useState<SegRow[]>(toRows(tr))
    const [lang, setLang] = useState<Lang>((tr?.language as Lang) || ((i18n.language || 'ar').startsWith('ar') ? 'ar' : 'en'))
    const [reason, setReason] = useState('')
    const [busy, setBusy] = useState(false)
    const [error, setError] = useState<unknown>(null)
    const [tBusy, setTBusy] = useState(false)
    const [tError, setTError] = useState<unknown>(null)
    const saveKey = useActionKey(true)
    const transcribeKey = useActionKey(true)
    const versions = useV2Query((signal) => qualityApi.transcripts(recording.id, signal), [recording.id, recording.transcript?.id])
    useEffect(() => {
        setText(tr?.text || '')
        setRows(toRows(tr))
        setReason('')
        setError(null)
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [tr?.id])
    const duration = recording.audio.duration_seconds
    const valid = useMemo(() => (mode === 'text' ? text.trim().length > 0 : rows.length > 0 && rows.every((r) => rowValid(r, duration))), [mode, text, rows, duration])
    const busyElsewhere = !!recording.processing && !recording.processing.stale

    const save = async () => {
        setBusy(true)
        setError(null)
        const body: QcTranscriptBody = { expected_revision: recording.revision, language: lang, ...(reason.trim() ? { reason: reason.trim() } : {}) }
        if (mode === 'text') body.text = text
        else body.segments = rows.map((r) => ({ start: numOrNull(r.start), end: numOrNull(r.end), speaker: r.speaker.trim() || null, text: r.text.trim() }))
        try {
            const r = await qualityApi.saveTranscript(recording.id, body, saveKey.key)
            saveKey.renew()
            ToastNotification(String(t('p6.qc.transcript.saved')), String(t('p6.qc.title')), 'success', 2000)
            onSaved(r)
        } catch (e) {
            setError(e)
        } finally {
            setBusy(false)
        }
    }

    const transcribe = async () => {
        setTBusy(true)
        setTError(null)
        try {
            const r = await qualityApi.transcribe(recording.id, { expected_revision: recording.revision, language: lang }, transcribeKey.key)
            transcribeKey.renew()
            ToastNotification(String(t('p6.qc.transcript.transcribed')), String(t('p6.qc.title')), 'success', 2500)
            onSaved(r)
        } catch (e) {
            setTError(e)
            onReload()
        } finally {
            setTBusy(false)
        }
    }

    const transcribeReady = !!transcribeCap?.ready
    return (
        <div className="flex flex-col gap-4" data-testid="p6-qc-transcript" data-version={tr?.version ?? ''}>
            <Card title={t('p6.qc.transcript.current')} testId="p6-qc-transcript-current">
                {tr ? (
                    <>
                        <div className="mb-2 flex flex-wrap gap-x-4 gap-y-1 text-[12.5px] text-gray-600">
                            <span>{t('p6.qc.transcript.version', { version: tr.version })}</span>
                            <span>{t(`p6.qc.transcript.source.${tr.source}`)}</span>
                            {tr.language && <span>{tr.language === 'ar' ? 'العربية' : 'English'}</span>}
                            {tr.model && (
                                <span>
                                    {t('p6.drafts.model')}: <Ltr>{tr.model}</Ltr>
                                </span>
                            )}
                            <span>{tr.diarized ? t('p6.qc.transcript.diarized') : t('p6.qc.transcript.notDiarized')}</span>
                            <span>
                                {tr.created_by?.name || '—'} · <RiyadhTime iso={tr.created_at} />
                            </span>
                        </div>
                        <TranscriptView transcript={tr} />
                        <div className="mt-3">
                            <div className="text-eyebrow mb-2">{t('p6.qc.transcript.roles')}</div>
                            <SpeakerRoles recording={recording} canEdit={canEdit && !busyElsewhere} onSaved={onSaved} />
                        </div>
                    </>
                ) : (
                    <Note>{t('p6.qc.transcript.none')}</Note>
                )}
            </Card>

            {canEdit && (
                <Card title={t('p6.qc.transcript.transcribeTitle')} testId="p6-qc-transcribe">
                    <p className="mb-3 text-[13px] text-gray-700 dark:text-night-body">{t('p6.qc.transcript.transcribeHint')}</p>
                    <AiReadinessNotice capability={transcribeCap} className="mb-3" testId="p6-qc-transcribe-not-ready" />
                    <div className="flex flex-wrap items-center gap-3">
                        <Segmented<Lang>
                            value={lang}
                            options={[
                                { value: 'ar', label: 'العربية' },
                                { value: 'en', label: 'English' },
                            ]}
                            onChange={setLang}
                            testId="p6-qc-transcribe-lang"
                        />
                        <Button
                            size="sm"
                            icon={<AudioLines size={15} />}
                            loading={tBusy}
                            disabled={!transcribeReady || busyElsewhere || !recording.audio.available}
                            aria-disabled={!transcribeReady || busyElsewhere || !recording.audio.available}
                            onClick={transcribe}
                            data-testid="p6-qc-transcribe-run"
                        >
                            {t('p6.qc.transcript.transcribe')}
                        </Button>
                    </div>
                    <AiErrorNotice error={tError} className="mt-3" testId="p6-qc-transcribe-error" />
                </Card>
            )}

            {canEdit && (
                <Card title={tr ? t('p6.qc.transcript.correctTitle') : t('p6.qc.transcript.manualTitle')} testId="p6-qc-transcript-editor">
                    <p className="mb-3 text-[13px] text-gray-700 dark:text-night-body">{t('p6.qc.transcript.manualHint')}</p>
                    <div className="mb-3 flex flex-wrap items-center gap-4">
                        <Segmented<Mode>
                            value={mode}
                            options={[
                                { value: 'text', label: t('p6.qc.transcript.modeText') },
                                { value: 'segments', label: t('p6.qc.transcript.modeSegments') },
                            ]}
                            onChange={setMode}
                            testId="p6-qc-transcript-mode"
                        />
                        <Segmented<Lang>
                            value={lang}
                            options={[
                                { value: 'ar', label: 'العربية' },
                                { value: 'en', label: 'English' },
                            ]}
                            onChange={setLang}
                            testId="p6-qc-transcript-lang"
                        />
                    </div>
                    {mode === 'text' ? (
                        <DialogField label={t('p6.qc.transcript.text')} required hint={t('p6.qc.transcript.textHint')}>
                            <Input textArea dir="auto" value={text} onChange={(e) => setText(e.target.value)} data-testid="p6-qc-transcript-text" className="min-h-[200px]" />
                        </DialogField>
                    ) : (
                        <div className="mb-3" data-testid="p6-qc-transcript-rows">
                            <div className="mb-1 grid grid-cols-[80px_80px_90px_minmax(0,1fr)_32px] gap-2 text-[11px] font-extrabold uppercase text-gray-500">
                                <span>{t('p6.qc.transcript.start')}</span>
                                <span>{t('p6.qc.transcript.end')}</span>
                                <span>{t('p6.qc.transcript.speaker')}</span>
                                <span>{t('p6.qc.transcript.text')}</span>
                                <span />
                            </div>
                            {rows.map((r, i) => (
                                <div key={i} className="mb-1.5 grid grid-cols-[80px_80px_90px_minmax(0,1fr)_32px] items-start gap-2" data-row={i} data-valid={rowValid(r, duration) ? '1' : '0'}>
                                    <Input size="sm" dir="ltr" inputMode="decimal" placeholder="0" value={r.start} onChange={(e) => setRows(rows.map((x, j) => (j === i ? { ...x, start: e.target.value } : x)))} data-testid="p6-qc-seg-start" />
                                    <Input size="sm" dir="ltr" inputMode="decimal" placeholder="—" value={r.end} onChange={(e) => setRows(rows.map((x, j) => (j === i ? { ...x, end: e.target.value } : x)))} data-testid="p6-qc-seg-end" />
                                    <Input size="sm" dir="ltr" maxLength={40} placeholder="A" value={r.speaker} onChange={(e) => setRows(rows.map((x, j) => (j === i ? { ...x, speaker: e.target.value } : x)))} data-testid="p6-qc-seg-speaker" />
                                    <Input size="sm" dir="auto" value={r.text} onChange={(e) => setRows(rows.map((x, j) => (j === i ? { ...x, text: e.target.value } : x)))} data-testid="p6-qc-seg-text" />
                                    <button
                                        type="button"
                                        aria-label={String(t('p6.qc.transcript.removeRow'))}
                                        className="mt-1.5 rounded p-1 text-gray-600 hover:bg-gray-100 disabled:opacity-40 dark:hover:bg-night-raised"
                                        disabled={rows.length === 1}
                                        onClick={() => setRows(rows.filter((_, j) => j !== i))}
                                    >
                                        <HiOutlineTrash />
                                    </button>
                                </div>
                            ))}
                            <Button size="xs" icon={<HiOutlinePlus />} onClick={() => setRows([...rows, { start: '', end: '', speaker: '', text: '' }])} data-testid="p6-qc-seg-add">
                                {t('p6.qc.transcript.addRow')}
                            </Button>
                            <div className="mt-1 text-[12px] text-gray-600">{t('p6.qc.transcript.rowsHint', { duration: formatStamp(duration) })}</div>
                        </div>
                    )}
                    <DialogField label={t('p6.qc.transcript.reason')} hint={t('p6.qc.transcript.reasonHint')}>
                        <Input size="sm" maxLength={500} value={reason} onChange={(e) => setReason(e.target.value)} data-testid="p6-qc-transcript-reason" />
                    </DialogField>
                    {(recording.review || recording.approval) && (
                        <Notice tone="warning" className="mb-3">
                            {recording.approval ? t('p6.qc.evidence.clearsApproval') : t('p6.qc.evidence.clearsReview')}
                        </Notice>
                    )}
                    <InlineError error={error} describe={describe} onReload={onReload} />
                    <div className="mt-3 flex justify-end">
                        <Button size="sm" variant="solid" loading={busy} disabled={!valid || busyElsewhere} aria-disabled={!valid || busyElsewhere} onClick={save} data-testid="p6-qc-transcript-save">
                            {tr ? t('p6.qc.transcript.saveCorrection') : t('p6.qc.transcript.save')}
                        </Button>
                    </div>
                </Card>
            )}

            <Card title={t('p6.qc.transcript.versions')} testId="p6-qc-transcript-versions">
                {versions.loading && !versions.data ? (
                    <SkeletonLines rows={2} />
                ) : versions.data && versions.data.length ? (
                    <ul className="flex flex-col gap-1 text-[12.5px]">
                        {versions.data.map((v) => (
                            <li key={v.id} data-version={v.version} data-source={v.source} className="flex flex-wrap items-center gap-x-3">
                                <span className="font-extrabold text-navy-600 dark:text-gray-100">{t('p6.qc.transcript.version', { version: v.version })}</span>
                                <span>{t(`p6.qc.transcript.source.${v.source}`)}</span>
                                <span>{t('p6.qc.transcript.segments', { count: v.segment_count })}</span>
                                <span className="text-gray-600">
                                    {v.created_by?.name || '—'} · <RiyadhTime iso={v.created_at} />
                                </span>
                                {v.reason && (
                                    <span className="text-gray-600" dir="auto">
                                        «{v.reason}»
                                    </span>
                                )}
                                {v.id === tr?.id && <span className="text-[11.5px] font-extrabold text-success">{t('p6.qc.current')}</span>}
                                {v.purged && <span className="text-[11.5px] text-gray-500">{t('p6.qc.transcript.purgedShort')}</span>}
                            </li>
                        ))}
                    </ul>
                ) : (
                    <Note>{t('p6.qc.transcript.none')}</Note>
                )}
            </Card>
        </div>
    )
}

export default TranscriptEditor
