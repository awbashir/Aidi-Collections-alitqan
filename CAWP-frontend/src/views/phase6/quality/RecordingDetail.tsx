/**
 * One QC recording: the player (byte ranges), then Evidence, Transcript, Review and Approvals as
 * sections (the open section is in the URL hash). Every mutation answers the whole recording,
 * which replaces what is on screen; a stale edit is refused (409) and offers a reload.
 */
import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { HiOutlineArrowLeft } from 'react-icons/hi'
import { Ltr, RiyadhTime } from '@/components/v2/Display'
import { ErrorState, Note, SkeletonLines } from '@/components/v2/States'
import { CountTabs } from '@/components/v2/ListParts'
import { describeP6Error, qualityApi } from '@/services/v2'
import type { QcOverview, QcRecording, QcTemplate, Translate } from '@/services/v2'
import { usePermissions, useV2Query } from '@/utils/v2/hooks'
import { caseLink } from '@/utils/v2/links'
import { Card, DurationText, Fact, Notice } from '@/views/phase5/parts'
import { QcStatusBadge } from '../parts'
import { formatBytes, templateName } from '../labels'
import Approvals from './Approvals'
import AudioPlayer from './AudioPlayer'
import EvidenceForm from './EvidenceForm'
import ReportButtons from './ReportButtons'
import ReviewEditor from './ReviewEditor'
import TranscriptEditor from './TranscriptEditor'

import { SECTIONS } from './links'
import type { Section } from './links'

const describe = (e: unknown, t: Translate) => describeP6Error(e, t)

function useTemplate(rec: QcRecording | null, overview: QcOverview | undefined): QcTemplate | null {
    const ref = rec?.review?.template || rec?.evidence.template || null
    const fromList = ref ? overview?.templates.find((x) => x.key === ref.key && x.version === ref.version) : undefined
    const q = useV2Query(ref && !fromList && overview ? (signal) => qualityApi.template(ref.key, ref.version, signal) : null, [ref?.key, ref?.version, !!fromList, !!overview])
    return fromList || q.data || null
}

const RecordingDetail = ({
    recordingId,
    overview,
    section,
    onSection,
    onBack,
    onChanged,
}: {
    recordingId: string
    overview: QcOverview | undefined
    section: Section
    onSection: (s: Section) => void
    onBack: () => void
    onChanged: () => void
}) => {
    const { t, i18n } = useTranslation()
    const perms = usePermissions()
    const q = useV2Query((signal) => qualityApi.detail(recordingId, signal), [recordingId])
    const [rec, setRec] = useState<QcRecording | null>(null)
    const audioRef = useRef<HTMLAudioElement | null>(null)
    useEffect(() => {
        if (q.data) setRec(q.data)
    }, [q.data])
    const template = useTemplate(rec, overview)
    const canEdit = !!perms.quality_review
    const canApprove = !!perms.quality_approve

    if (!rec) {
        if (q.error) return <ErrorState error={q.error} onRetry={q.reload} describe={describe} />
        return <SkeletonLines rows={10} />
    }
    const saved = (r: QcRecording) => {
        setRec(r)
        onChanged()
    }
    const provenance = rec.provenance as { kind?: string; recording_path?: string; explicit_user_action?: boolean }
    return (
        <div data-testid="p6-qc-detail" data-recording={rec.id} data-status={rec.status} data-revision={rec.revision}>
            <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0">
                    <button type="button" onClick={onBack} className="mb-1 inline-flex items-center gap-1 text-[12.5px] font-extrabold text-azure-600 hover:underline" data-testid="p6-qc-back">
                        <HiOutlineArrowLeft className="rtl:rotate-180" /> {t('p6.qc.backToLibrary')}
                    </button>
                    <div className="flex flex-wrap items-center gap-2">
                        <h3 className="mb-0 text-[20px] font-extrabold text-navy-600 dark:text-gray-100" dir="auto">
                            {rec.title}
                        </h3>
                        <QcStatusBadge status={rec.status} />
                    </div>
                    <div className="mt-1 text-[12.5px] text-gray-600">
                        {t('p6.qc.revisionLine', { revision: rec.revision })} · {t(`p6.qc.source.${rec.source}`)} · {rec.created_by?.name || '—'} · <RiyadhTime iso={rec.created_at} />
                    </div>
                </div>
                <ReportButtons recordingId={rec.id} />
            </div>

            {rec.processing && (
                <Notice tone="warning" className="mb-3" testId="p6-qc-processing">
                    {rec.processing.stale ? t('p6.qc.processingStale', { kind: t(`p6.qc.processingKind.${rec.processing.kind}`, { defaultValue: rec.processing.kind }) }) : t('p6.qc.processing', { kind: t(`p6.qc.processingKind.${rec.processing.kind}`, { defaultValue: rec.processing.kind }) })}
                </Notice>
            )}
            {rec.last_error && (
                <Notice tone="warning" className="mb-3" testId="p6-qc-last-error" title={t('p6.qc.lastErrorTitle')}>
                    <span data-code={rec.last_error}>{t(`p6.reason.${rec.last_error}`, { defaultValue: t(`p6.err.${rec.last_error}`, { defaultValue: rec.last_error.replace(/_/g, ' ') }) })}</span>
                </Notice>
            )}

            <div className="mb-4 grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1fr)_380px]">
                <Card title={t('p6.qc.audio.title')} testId="p6-qc-audio-card" lead>
                    <AudioPlayer recordingId={rec.id} audio={rec.audio} audioRef={audioRef} />
                </Card>
                <Card title={t('p6.qc.facts')} testId="p6-qc-facts">
                    <div className="grid grid-cols-2 gap-3">
                        <Fact label={t('p6.qc.col.duration')}>
                            <DurationText seconds={rec.audio.duration_seconds} />
                        </Fact>
                        <Fact label={t('p6.qc.format')}>
                            <Ltr>
                                {rec.audio.format.toUpperCase()} · {formatBytes(rec.audio.size_bytes)}
                            </Ltr>
                        </Fact>
                        <Fact label={t('p6.qc.template')}>
                            {template ? `${templateName(template, i18n.language)} · v${template.version}` : <span className="text-gray-500">{t('p6.qc.noTemplate')}</span>}
                        </Fact>
                        <Fact label={t('p6.qc.col.case')}>
                            {rec.link.case_id ? (
                                <Link className="text-azure-600 hover:underline" to={caseLink(rec.link.case_id)} data-testid="p6-qc-case-link">
                                    {t('p6.qc.openCase')}
                                </Link>
                            ) : (
                                <span className="text-gray-500">{t('p6.qc.unlinked')}</span>
                            )}
                        </Fact>
                        {rec.link.attempt_id && (
                            <Fact label={t('p6.qc.call')}>
                                <Link className="text-azure-600 hover:underline" to={`/calls#scope=team&call=${rec.link.attempt_id}`}>
                                    {t('p6.qc.openCall')}
                                </Link>
                            </Fact>
                        )}
                        <Fact label={t('p6.qc.sha')} className="col-span-2">
                            <Ltr className="break-all font-mono text-[11px]">{rec.audio.sha256}</Ltr>
                        </Fact>
                        {provenance?.kind === 'pbx_call' && (
                            <Fact label={t('p6.qc.provenance')} className="col-span-2">
                                {t('p6.qc.provenanceCall')}
                                {provenance.recording_path && <Ltr className="block break-all text-[11px] text-gray-600">{provenance.recording_path}</Ltr>}
                            </Fact>
                        )}
                    </div>
                </Card>
            </div>

            <div className="mb-4">
                <CountTabs<Section>
                    tabs={SECTIONS.map((s) => ({
                        value: s,
                        label: t(`p6.qc.section.${s}`),
                        count: s === 'transcript' ? rec.history.transcripts : s === 'review' ? rec.history.reviews : s === 'approvals' ? rec.history.approvals : undefined,
                    }))}
                    value={section}
                    onChange={onSection}
                    testId="p6-qc-sections"
                />
            </div>
            {section === 'evidence' && <EvidenceForm recording={rec} templates={overview?.templates || []} canEdit={canEdit} onSaved={saved} onReload={q.reload} />}
            {section === 'transcript' && <TranscriptEditor recording={rec} transcribeCap={overview?.ai.transcribe} canEdit={canEdit} onSaved={saved} onReload={q.reload} />}
            {section === 'review' && (
                <ReviewEditor
                    recording={rec}
                    template={template}
                    draftCap={overview?.ai.draft}
                    canEdit={canEdit}
                    canApprove={canApprove}
                    audioRef={audioRef}
                    onSaved={saved}
                    onReload={q.reload}
                    onShowApprovals={() => onSection('approvals')}
                />
            )}
            {section === 'approvals' && <Approvals recording={rec} />}
            {q.error && rec ? (
                <div className="mt-3">
                    <ErrorState error={q.error} onRetry={q.reload} describe={describe} />
                </div>
            ) : null}
            {!canEdit && <Note className="mt-3">{t('p6.qc.readOnly')}</Note>}
        </div>
    )
}

export default RecordingDetail
