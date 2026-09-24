/**
 * One call's detail (`GET /v2/calls/<id>`): facts, the step timeline, the wrap-up (or a
 * "Wrap up" action when it is pending and mine / I manage calls), the transcript, the raw
 * PBX evidence (legs, AGI decisions, events) and the PBX records next to it
 * (`GET /v2/calls/<id>/pbx-evidence`: live CDR, imported CDR, recordings under the
 * recording keys). PBX records are evidence NEXT TO the attempt; they never change it, and
 * an unreadable CDR is "unknown", not "no records".
 */
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { Download, Lock, Play, Square } from 'lucide-react'
import Button from '@/components/ui/Button'
import { Ltr, RiyadhTime } from '@/components/v2/Display'
import { ErrorState, SkeletonLines } from '@/components/v2/States'
import { apiGetCdrRecordingPath } from '@/services/ClientsService'
import { LIVE_CALL_STATES, callsApi, describeP5Error } from '@/services/v2'
import type { CallDetail, PbxEvidence, PbxRecording, Translate } from '@/services/v2'
import { useAppSelector } from '@/store'
import { usePermissions, useV2Query } from '@/utils/v2/hooks'
import { caseLink } from '@/utils/v2/links'
import { CALL_WRAPPED_EVENT, callDock } from '../live/callDock'
import { CallTimelineSteps } from '../live/LiveCallPanel'
import { CaseCell, CallStateBadge, DurationText, Fact, ModeLabel, Notice, OutcomeBadge } from '../parts'
import { reasonLabel } from '../labels'
/* Phase 6: AI drafts for this call (ai_assist) and "add call recording to quality review" (quality_review). */
import AddCallRecording from '@/views/phase6/quality/AddCallRecording'
import AiDraftDialog from '@/views/phase6/drafts/AiDraftDialog'

const RECORDING_KEYS = ['clints_callsRecords_download', 'clints_callsRecords_play']
const describe = (e: unknown, t: Translate) => describeP5Error(e, t)

/** Download / play through the unchanged legacy route, under the recording keys. */
export const RecordingControls = ({ rec, name }: { rec: PbxRecording; name: string }) => {
    const { t } = useTranslation()
    const perms = usePermissions()
    const canDownload = !!perms.clints_callsRecords_download
    const canPlay = !!perms.clints_callsRecords_play
    const [playing, setPlaying] = useState<HTMLAudioElement | null>(null)
    const [busy, setBusy] = useState(false)
    const [failed, setFailed] = useState(false)
    useEffect(() => () => playing?.pause(), [playing])
    if (!RECORDING_KEYS.some((k) => perms[k]) || !rec.file) {
        return (
            <span className="inline-flex items-center gap-1 text-[12px] text-gray-600" data-testid="p5-rec-nokey" title={String(t('p5.rec.noKeyTitle'))}>
                <Lock aria-hidden size={12} />
                {t('p5.rec.noKey')}
            </span>
        )
    }
    const file = rec.file
    const fetchBlob = async () => {
        setBusy(true)
        setFailed(false)
        try {
            const res = await apiGetCdrRecordingPath<Blob, { recordingPath: string }>({ recordingPath: file })
            return new Blob([res.data as BlobPart], { type: 'audio/wav' })
        } catch {
            setFailed(true)
            return null
        } finally {
            setBusy(false)
        }
    }
    const download = async () => {
        const blob = await fetchBlob()
        if (!blob) return
        const url = URL.createObjectURL(blob)
        const a = document.createElement('a')
        a.href = url
        a.download = `${name}.wav`
        document.body.appendChild(a)
        a.click()
        a.remove()
        setTimeout(() => URL.revokeObjectURL(url), 10000)
    }
    const play = async () => {
        if (playing) {
            playing.pause()
            setPlaying(null)
            return
        }
        const blob = await fetchBlob()
        if (!blob) return
        const url = URL.createObjectURL(blob)
        const audio = new Audio(url)
        audio.onended = () => {
            setPlaying(null)
            URL.revokeObjectURL(url)
        }
        setPlaying(audio)
        audio.play().catch(() => setFailed(true))
    }
    return (
        <span className="inline-flex items-center gap-1" data-testid="p5-rec" data-file={file}>
            {canDownload && <Button size="xs" loading={busy} onClick={download} title={String(t('p5.rec.download'))} icon={<Download size={14} />} />}
            {canPlay && (
                <Button size="xs" onClick={play} title={String(playing ? t('p5.rec.stop') : t('p5.rec.play'))} icon={playing ? <Square size={14} /> : <Play size={14} />} />
            )}
            {failed && <span className="text-[11.5px] text-danger">{t('p5.rec.failed')}</span>}
        </span>
    )
}

const PbxEvidenceSection = ({ callId }: { callId: string }) => {
    const { t } = useTranslation()
    const q = useV2Query((signal) => callsApi.pbxEvidence(callId, signal), [callId])
    if (q.loading && !q.data) return <SkeletonLines rows={3} />
    if (q.error && !q.data) return <ErrorState error={q.error} onRetry={q.reload} describe={describe} />
    const ev = q.data as PbxEvidence
    return (
        <div className="flex flex-col gap-3" data-testid="p5-pbx-evidence" data-cdr-status={ev.cdr.status}>
            <div className="text-[12.5px]">
                <span className="text-gray-600">{t('p5.pbx.cdrStatus')}: </span>
                <span className="font-extrabold" data-testid="p5-pbx-cdr-status">
                    {t(`p5.pbx.status.${ev.cdr.status}`)}
                </span>
            </div>
            {ev.cdr.records.length > 0 ? (
                <div className="overflow-x-auto">
                    <table className="min-w-full text-[12.5px]" data-testid="p5-pbx-cdr">
                        <thead>
                            <tr className="text-start text-gray-600">
                                <th className="pe-3 text-start font-extrabold">{t('p5.pbx.col.at')}</th>
                                <th className="pe-3 text-start font-extrabold">{t('p5.pbx.col.disposition')}</th>
                                <th className="pe-3 text-end font-extrabold">{t('p5.pbx.col.duration')}</th>
                                <th className="pe-3 text-end font-extrabold">{t('p5.pbx.col.billsec')}</th>
                                <th className="pe-3 text-start font-extrabold">{t('p5.pbx.col.link')}</th>
                                <th className="text-start font-extrabold">{t('p5.pbx.col.recording')}</th>
                            </tr>
                        </thead>
                        <tbody>
                            {ev.cdr.records.map((r, i) => (
                                <tr key={`${r.uniqueid}-${i}`} className="border-t border-gray-200 dark:border-night-border">
                                    <td className="py-1 pe-3">
                                        <RiyadhTime iso={r.call_at} />
                                    </td>
                                    <td className="pe-3">{r.disposition || '—'}</td>
                                    <td className="pe-3 text-end">
                                        <DurationText seconds={r.duration_seconds} />
                                    </td>
                                    <td className="pe-3 text-end">
                                        <DurationText seconds={r.billsec} />
                                    </td>
                                    <td className="pe-3">{r.attempt_link ? t(`p5.pbx.link.${r.attempt_link}`, { defaultValue: r.attempt_link }) : '—'}</td>
                                    <td>
                                        {r.recording?.has_recording ? (
                                            <span className="inline-flex flex-wrap items-center gap-1">
                                                <RecordingControls rec={r.recording} name={callId} />
                                                <AddCallRecording attemptId={callId} recordingPath={r.recording.file} />
                                            </span>
                                        ) : (
                                            '—'
                                        )}
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            ) : (
                <div className="text-[12.5px] text-gray-600">{ev.cdr.status === 'available' ? t('p5.pbx.noRecords') : t('p5.pbx.unknownRecords')}</div>
            )}
            {ev.imported.length > 0 && (
                <div data-testid="p5-pbx-imported">
                    <div className="text-eyebrow mb-1">{t('p5.pbx.imported')}</div>
                    <ul className="text-[12.5px]">
                        {ev.imported.map((e) => {
                            const src = (e.source || {}) as { bulk_job_id?: string; row_no?: number; file_name?: string }
                            return (
                                <li key={String(e.id)} className="mb-1">
                                    <RiyadhTime iso={e.call_at as string} /> · {String(e.disposition || '—')} ·{' '}
                                    <DurationText seconds={(e.duration_seconds as number | null) ?? null} /> · {t(`p5.pbx.attribution.${e.attribution}`, { defaultValue: String(e.attribution) })}
                                    {src.bulk_job_id && (
                                        <>
                                            {' · '}
                                            <Link className="text-azure-600 hover:underline" to={`/jobs/run/${src.bulk_job_id}`}>
                                                {t('p5.pbx.importJob', { row: src.row_no, file: src.file_name || '' })}
                                            </Link>
                                        </>
                                    )}
                                </li>
                            )
                        })}
                    </ul>
                </div>
            )}
            {ev.recordings.length > 0 && (
                <div data-testid="p5-pbx-recordings">
                    <div className="text-eyebrow mb-1">{t('p5.pbx.recordings')}</div>
                    {ev.recordings.map((r, i) => (
                        <div key={i} className="flex items-center gap-2 text-[12.5px]">
                            <Ltr className="truncate text-gray-700">{r.file || t('p5.rec.hidden')}</Ltr>
                            <RecordingControls rec={r} name={callId} />
                            <AddCallRecording attemptId={callId} recordingPath={r.file} />
                        </div>
                    ))}
                </div>
            )}
            <div className="text-[11.5px] text-gray-500">{t('p5.pbx.note')}</div>
        </div>
    )
}

const Section = ({ title, children, testId }: { title: React.ReactNode; children: React.ReactNode; testId?: string }) => (
    <section className="border-t border-gray-200 pt-3 dark:border-night-border" data-testid={testId}>
        <div className="text-eyebrow mb-2">{title}</div>
        {children}
    </section>
)

const EvidenceSection = ({ call }: { call: CallDetail }) => {
    const { t } = useTranslation()
    const ev = call.evidence
    if (!ev) return null
    if (!ev.legs.length && !ev.agi_decisions.length && !ev.events.length) {
        return <div className="text-[12.5px] text-gray-600">{call.mode === 'manual' ? t('p5.evidence.manual') : t('p5.evidence.none')}</div>
    }
    return (
        <div className="flex flex-col gap-2 text-[12.5px]" data-testid="p5-evidence">
            {ev.legs.length > 0 && (
                <table className="min-w-full" data-testid="p5-evidence-legs">
                    <thead>
                        <tr className="text-gray-600">
                            <th className="pe-2 text-start font-extrabold">{t('p5.evidence.leg')}</th>
                            <th className="pe-2 text-start font-extrabold">{t('p5.evidence.answered')}</th>
                            <th className="pe-2 text-start font-extrabold">{t('p5.evidence.bridged')}</th>
                            <th className="text-start font-extrabold">{t('p5.evidence.cause')}</th>
                        </tr>
                    </thead>
                    <tbody>
                        {ev.legs.map((l) => (
                            <tr key={`${l.pbx_epoch}-${l.uniqueid}`} className="border-t border-gray-200 dark:border-night-border" data-role={l.role}>
                                <td className="py-1 pe-2">{t(`p5.leg.${l.role}`, { defaultValue: l.role })}</td>
                                <td className="pe-2">{l.answered_pbx_ts ? t('p5.yes') : t('p5.no')}</td>
                                <td className="pe-2">{l.bridged_pbx_ts ? t('p5.yes') : t('p5.no')}</td>
                                <td>{l.hangup_cause ?? '—'}</td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            )}
            {ev.agi_decisions.length > 0 && (
                <div>
                    {ev.agi_decisions.map((a, i) => (
                        <div key={i}>
                            <RiyadhTime iso={a.at} /> · {t('p5.evidence.agi', { decision: t(`p5.evidence.agiDecision.${a.decision}`, { defaultValue: a.decision }) })}
                            {a.reason ? <Ltr className="text-gray-500"> ({a.reason})</Ltr> : null}
                        </div>
                    ))}
                </div>
            )}
            <div className="text-gray-600">{t('p5.evidence.eventsCount', { count: ev.events.length })}</div>
            <div className="text-[11.5px] text-gray-500">{t('p5.evidence.note')}</div>
        </div>
    )
}

export const WrapupSection = ({ call }: { call: CallDetail }) => {
    const { t } = useTranslation()
    const w = call.wrapup
    if (!w) {
        return (
            <div className="text-[12.5px] text-gray-600" data-testid="p5-wrapup-none">
                {call.wrapup_pending ? t('p5.wrapup.pending') : call.mode === 'manual' ? '—' : t('p5.wrapup.none')}
            </div>
        )
    }
    const deferred = w.deferred_actions || {}
    const deferredKeys = Object.entries(deferred)
        .filter(([k, v]) => k !== 'note' && v)
        .map(([k]) => k)
    return (
        <div className="flex flex-col gap-2" data-testid="p5-wrapup-view" data-outcome={w.outcome} data-needs-review={w.needs_review ? '1' : '0'}>
            <div className="flex flex-wrap items-center gap-2">
                <OutcomeBadge outcome={w.outcome} />
                <span className="text-[12px] text-gray-600">
                    {w.reviewed_by?.name || '—'} · <RiyadhTime iso={w.reviewed_at} />
                </span>
            </div>
            {w.note_text && (
                <div className="whitespace-pre-wrap rounded-md bg-gray-50 px-3 py-2 text-[13px] dark:bg-night-raised" dir="auto" data-testid="p5-wrapup-note">
                    {w.note_text}
                </div>
            )}
            {w.needs_review && (
                <Notice tone="warning" title={t('p5.wrapup.reviewTitle')} testId="p5-wrapup-review-note">
                    {t(`p5.wrapup.reviewBody.${w.review_reason || 'case_locked'}`)}
                    {deferredKeys.length > 0 && (
                        <div className="mt-1">
                            {t('p5.wrapup.deferred')}: {deferredKeys.map((k) => t(`p5.wrapup.deferredKey.${k}`, { defaultValue: k })).join(String(t('p5.listSep')))}
                        </div>
                    )}
                </Notice>
            )}
            <div className="grid grid-cols-2 gap-2 text-[12.5px]">
                <Fact label={t('p5.wrapup.callback')}>{w.callback_at ? <RiyadhTime iso={w.callback_at} /> : '—'}</Fact>
                <Fact label={t('p5.wrapup.promise')}>
                    {w.promise_id ? (
                        <Link className="text-azure-600 hover:underline" to={caseLink(call.case.id, 'promises')} data-testid="p5-wrapup-promise">
                            {t('p5.wrapup.promiseCreated')}
                        </Link>
                    ) : (
                        t('p5.wrapup.noPromise')
                    )}
                </Fact>
                {w.contact_blocked && <Fact label={t('p5.wrapup.contact')}>{t('p5.wrapup.contactBlocked')}</Fact>}
                {w.calling_block_id && <Fact label={t('p5.wrapup.case')}>{t('p5.wrapup.caseBlocked')}</Fact>}
            </div>
        </div>
    )
}

const CallDetailView = ({ callId, onChanged }: { callId: string; onChanged?: () => void }) => {
    const { t } = useTranslation()
    const perms = usePermissions()
    const myId = useAppSelector((s) => s.auth.user.id)
    const q = useV2Query((signal) => callsApi.detail(callId, signal), [callId])
    const [aiOpen, setAiOpen] = useState(false)
    const reload = q.reload
    useEffect(() => {
        const on = (e: Event) => {
            const d = (e as CustomEvent<{ attemptId: string }>).detail
            if (d?.attemptId === callId) {
                reload()
                onChanged?.()
            }
        }
        window.addEventListener(CALL_WRAPPED_EVENT, on)
        return () => window.removeEventListener(CALL_WRAPPED_EVENT, on)
    }, [callId, reload, onChanged])
    if (q.loading && !q.data) return <SkeletonLines rows={8} />
    if (q.error && !q.data) return <ErrorState error={q.error} onRetry={q.reload} describe={describe} />
    const call = q.data as CallDetail
    const live = LIVE_CALL_STATES.includes(call.state)
    const pbx = call.mode !== 'manual'
    return (
        <div className="flex flex-col gap-3" data-testid="p5-call-detail" data-call={call.id} data-state={call.state}>
            <div className="flex items-start justify-between gap-2">
                <CaseCell kase={call.case} />
                <CallStateBadge state={call.state} />
            </div>
            <div className="grid grid-cols-2 gap-3">
                <Fact label={t('p5.col.mode')}>
                    <ModeLabel mode={call.mode} /> · {t(`p5.direction.${call.direction}`)}
                </Fact>
                <Fact label={t('p5.col.collector')}>{call.collector?.name || '—'}</Fact>
                <Fact label={t('p5.col.time')}>
                    <RiyadhTime iso={call.started_at || call.created_at} />
                </Fact>
                <Fact label={t('p5.col.duration')}>
                    <DurationText seconds={call.duration_seconds} source={call.duration_source} />
                </Fact>
                <Fact label={t('p5.col.number')}>
                    {call.destination || call.contact?.value ? <Ltr>{call.destination || call.contact?.value}</Ltr> : <span className="text-gray-500">{call.contact ? t('p5.numberHidden') : '—'}</span>}
                </Fact>
                {pbx && <Fact label={t('p5.col.extension')}>{call.extension ? <Ltr>{call.extension}</Ltr> : '—'}</Fact>}
                {call.reason_code && (
                    <Fact label={t('p5.live.reason')}>
                        <span data-reason={call.reason_code}>{reasonLabel(t, call.reason_code)}</span>
                    </Fact>
                )}
                {call.hangup_cause !== null && pbx && <Fact label={t('p5.col.hangupCause')}>{call.hangup_cause}</Fact>}
                {call.campaign_id && (
                    <Fact label={t('p5.col.campaign')}>
                        {perms.campaigns_manage ? (
                            <Link className="text-azure-600 hover:underline" to={`/campaigns/${call.campaign_id}`}>
                                {t('p5.col.openCampaign')}
                            </Link>
                        ) : (
                            t('p5.mode.campaign')
                        )}
                    </Fact>
                )}
            </div>
            {call.state === 'uncertain' && (
                <Notice tone="warning" title={t('p5.live.uncertainTitle')}>
                    {t('p5.live.uncertainBody', { reason: reasonLabel(t, call.reason_code) })}
                </Notice>
            )}
            <div className="flex flex-wrap gap-2">
                {(live || call.state === 'uncertain') && pbx && (
                    <Button size="sm" onClick={() => callDock.show(call.id)} data-testid="p5-detail-show-live">
                        {t('p5.detail.showLive')}
                    </Button>
                )}
                {call.wrapup_pending && (perms.calls_manage || call.collector?.id === myId) && (
                    <Button size="sm" variant="solid" onClick={() => callDock.openWrapup(call.id)} data-testid="p5-detail-wrapup">
                        {t('p5.live.wrapup')}
                    </Button>
                )}
                {perms.ai_assist && call.case && !call.case.missing && (
                    <Button size="sm" onClick={() => setAiOpen(true)} data-testid="p6-call-ai-drafts">
                        {t('p6.drafts.callEntry')}
                    </Button>
                )}
            </div>
            {perms.ai_assist && call.case && !call.case.missing && aiOpen && (
                <AiDraftDialog isOpen={aiOpen} attemptId={call.id} caseId={call.case.id} onClose={() => setAiOpen(false)} onNoteSaved={onChanged} />
            )}
            {pbx && (
                <Section title={t('p5.detail.timeline')}>
                    <CallTimelineSteps view={{ timeline: call.timeline, state: call.state, live }} />
                </Section>
            )}
            <Section title={t('p5.detail.wrapup')} testId="p5-detail-wrapup-section">
                <WrapupSection call={call} />
            </Section>
            {call.transcript && (
                <Section title={t('p5.detail.transcript')}>
                    <div className="max-h-48 overflow-y-auto whitespace-pre-wrap text-[12.5px]" dir="auto" data-testid="p5-transcript">
                        {call.transcript}
                    </div>
                </Section>
            )}
            {pbx && (
                <Section title={t('p5.detail.evidence')}>
                    <EvidenceSection call={call} />
                </Section>
            )}
            {pbx && (
                <Section title={t('p5.detail.pbx')} testId="p5-detail-pbx">
                    <PbxEvidenceSection callId={call.id} />
                </Section>
            )}
        </div>
    )
}

export default CallDetailView
