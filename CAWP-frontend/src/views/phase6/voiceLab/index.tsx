/**
 * Voice training lab (`/voice-lab`, key `voice_lab`; Intelligence & control).
 *
 * FICTIONAL TRAINING, NOT A REAL DEBT CALL (docs/phase6-spec.md hard rule 6, PROJECT-NOTES §2.9):
 * the trainee picks a fictional persona and scenario (ar/en) and talks to an AI playing that
 * customer. No case, contact or PBX is involved.
 *
 * - Readiness comes from `GET /v2/ai/status` (`realtime_session`): not configured / no model / no
 *   approved policy / daily AI quota used up are shown as such, and Start stays disabled. There is
 *   no demo or simulated conversation.
 * - Start -> microphone permission (only now) -> `POST /v2/ai/voice-lab/sessions` (the server
 *   builds the persona instructions and mints a short-lived secret) -> WebRTC straight to the
 *   provider (services/voiceLab/realtime.ts) -> a live timer with a hard stop at 5 minutes.
 * - Mute and End; every microphone track is stopped on End, the time limit, an error, leaving the
 *   page or closing the tab. The end is reported with the outcome and duration only.
 */
import { useEffect, useMemo, useState, useSyncExternalStore } from 'react'
import { useTranslation } from 'react-i18next'
import Button from '@/components/ui/Button'
import { PageHead } from '@/components/v2/ListParts'
import { ErrorState, SkeletonLines } from '@/components/v2/States'
import { Segmented } from '@/views/phase1/followUps/parts'
import { Card, Notice } from '@/views/phase5/parts'
import { useV2Query } from '@/utils/v2/hooks'
import { isV2Error } from '@/services/v2'
import { VoiceLabSession, describeVoiceLabApiError, voiceLabApi } from '@/services/voiceLab'
import type { VoiceLang } from '@/services/voiceLab'
import { History, ScenarioPicker, SessionPanel } from './parts'

const REASON_ORDER = ['ai_not_configured', 'model_not_set', 'ai_policy_required', 'ai_policy_category_denied', 'ai_quota_exceeded']

const VoiceLab = () => {
    const { t, i18n } = useTranslation()
    const ar = (i18n.language || 'ar').startsWith('ar')
    const [session] = useState(() => new VoiceLabSession())
    const snap = useSyncExternalStore(session.subscribe, session.snapshot)
    useEffect(() => {
        session.attach()
        return () => session.dispose()
    }, [session])

    const status = useV2Query((signal) => voiceLabApi.status(signal), [])
    const catalogue = useV2Query((signal) => voiceLabApi.scenarios(signal), [])
    const settled = snap.phase === 'ended' || snap.phase === 'failed' ? `${snap.phase}:${snap.endReported}` : 'open'
    const mine = useV2Query((signal) => voiceLabApi.mySessions(signal), [settled])
    const [scenarioId, setScenarioId] = useState<string | null>(null)
    const [language, setLanguage] = useState<VoiceLang>(ar ? 'ar' : 'en')
    const [endingOpen, setEndingOpen] = useState<{ busy: boolean; error: unknown }>({ busy: false, error: null })

    const scenarios = useMemo(() => catalogue.data?.scenarios || [], [catalogue.data])
    useEffect(() => {
        if (!scenarioId && scenarios.length) setScenarioId(scenarios[0].id)
    }, [scenarioId, scenarios])
    const scenario = useMemo(() => scenarios.find((s) => s.id === scenarioId) || null, [scenarios, scenarioId])

    const running = ['mic', 'minting', 'connecting', 'live'].includes(snap.phase)
    const cap = status.data?.capabilities?.realtime_session
    const quotaExceeded = status.data?.quota?.exceeded || null
    const reasons = useMemo(() => {
        const r = [...(cap?.reasons || catalogue.data?.readiness?.reasons || [])]
        if (quotaExceeded && !r.includes('ai_quota_exceeded')) r.push('ai_quota_exceeded')
        return REASON_ORDER.filter((x) => r.includes(x)).concat(r.filter((x) => !REASON_ORDER.includes(x)))
    }, [cap, catalogue.data, quotaExceeded])
    const known = !!(cap || catalogue.data)
    const ready = known && reasons.length === 0
    // an open session on the server while this page runs none (another tab, a tab closed without
    // ending it, or an end report that did not arrive): the one-session rule would refuse Start
    const foreignOpen = mine.data?.active && !running ? mine.data.active : null

    let blocked: string | null = null
    if (!known) blocked = null
    else if (!ready) blocked = String(t('p6vl.blocked.notReady'))
    else if (!catalogue.data) blocked = String(t('p6vl.blocked.noCatalogue'))
    else if (foreignOpen) blocked = String(t('p6vl.blocked.openSession'))
    const canStart = ready && !!scenario && !running && !foreignOpen && !!catalogue.data

    const endForeign = async () => {
        if (!foreignOpen) return
        setEndingOpen({ busy: true, error: null })
        try {
            await voiceLabApi.end(foreignOpen.id, { outcome: 'user_ended' })
            setEndingOpen({ busy: false, error: null })
            mine.reload()
        } catch (err) {
            setEndingOpen({ busy: false, error: err })
        }
    }

    /* the AI status answered but a voice lab route did not: an API without the voice lab (404, or its
     * CORS preflight refused, which the browser reports as a network error) */
    const notDeployed = (err: unknown) => !!status.data && isV2Error(err) && [0, 404].includes(err.status)

    const refresh = () => {
        status.reload()
        catalogue.reload()
        mine.reload()
    }

    return (
        <div data-testid="p6vl-page" className="flex flex-col gap-4">
            <PageHead
                title={t('p6vl.title')}
                subtitle={t('p6vl.subtitle')}
                onRefresh={running ? undefined : refresh}
                refreshing={status.loading || catalogue.loading}
            />

            <Notice tone="warning" title={t('p6vl.banner.title')} testId="p6vl-banner">
                {t('p6vl.banner.body')}
            </Notice>

            {status.loading && !status.data ? (
                <SkeletonLines rows={2} />
            ) : status.error && !status.data && !catalogue.data ? (
                <ErrorState error={status.error} onRetry={status.reload} describe={describeVoiceLabApiError} />
            ) : (
                !ready && (
                    <Notice tone="danger" title={t('p6vl.unconfigured.title')} testId="p6vl-unconfigured">
                        <ul className="mb-1 ms-5 list-disc" data-reasons={reasons.join(',')}>
                            {reasons.map((r) => (
                                <li key={r} data-reason={r}>
                                    {t(`p6vl.reason.${r}`, { defaultValue: r })}
                                </li>
                            ))}
                        </ul>
                        <div className="text-[12.5px]">{t('p6vl.unconfigured.hint')}</div>
                    </Notice>
                )
            )}

            {foreignOpen && !running && (
                <Notice tone="info" title={t('p6vl.open.title')} testId="p6vl-open-session">
                    <div className="mb-2">{t('p6vl.open.body')}</div>
                    <Button size="sm" loading={endingOpen.busy} onClick={endForeign} data-testid="p6vl-end-open">
                        {t('p6vl.open.end')}
                    </Button>
                    {endingOpen.error ? <div className="mt-2 text-danger">{describeVoiceLabApiError(endingOpen.error, t)}</div> : null}
                </Notice>
            )}

            <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]">
                <Card title={t('p6vl.picker.title')} testId="p6vl-picker">
                    <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                        <span className="text-[12.5px] text-gray-600">{t('p6vl.picker.language')}</span>
                        <Segmented<VoiceLang>
                            value={language}
                            onChange={(v) => !running && setLanguage(v)}
                            testId="p6vl-lang"
                            label={String(t('p6vl.picker.language'))}
                            options={[
                                { value: 'ar', label: t('p6vl.lang.ar') },
                                { value: 'en', label: t('p6vl.lang.en') },
                            ]}
                        />
                    </div>
                    {catalogue.loading && !catalogue.data ? (
                        <SkeletonLines rows={5} />
                    ) : catalogue.error && !catalogue.data ? (
                        <Notice tone="warning" testId="p6vl-catalogue-error">
                            {notDeployed(catalogue.error)
                                ? t('p6vl.err.not_deployed')
                                : describeVoiceLabApiError(catalogue.error, t)}
                        </Notice>
                    ) : (
                        <ScenarioPicker scenarios={scenarios} selected={scenarioId} onSelect={setScenarioId} disabled={running} ar={ar} />
                    )}
                </Card>

                <SessionPanel
                    snap={snap}
                    scenario={scenario}
                    language={language}
                    canStart={canStart}
                    startBlockedReason={blocked}
                    onStart={() => scenario && void session.start(scenario.id, language)}
                    onEnd={() => void session.end()}
                    onMute={(m) => session.setMuted(m)}
                />
            </div>

            <Card title={t('p6vl.history.title')} testId="p6vl-history">
                {mine.data ? (
                    <>
                        <History items={mine.data.items} ar={ar} />
                        <p className="mb-0 mt-2 text-[12px] text-gray-600 tabular-nums" data-testid="p6vl-usage">
                            {t('p6vl.history.usage', {
                                hour: mine.data.usage.last_hour,
                                perHour: mine.data.usage.sessions_per_hour,
                                today: mine.data.usage.today,
                                perDay: mine.data.usage.sessions_per_day,
                            })}
                        </p>
                    </>
                ) : mine.error ? (
                    <p className="mb-0 text-[12.5px] text-gray-600">{notDeployed(mine.error) ? t('p6vl.err.not_deployed') : describeVoiceLabApiError(mine.error, t)}</p>
                ) : (
                    <SkeletonLines rows={2} />
                )}
                <p className="mb-0 mt-2 text-[12px] text-gray-600">{t('p6vl.history.privacy')}</p>
            </Card>
        </div>
    )
}

export default VoiceLab
