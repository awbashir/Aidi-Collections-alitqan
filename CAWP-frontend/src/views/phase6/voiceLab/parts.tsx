/**
 * Pieces of the voice lab page: scenario picker, the session panel (timer, microphone indicator,
 * mute, end) and my recent sessions. Fictional training only (review/phase6-voicelab.md).
 */
import classNames from 'classnames'
import { useTranslation } from 'react-i18next'
import { HiOutlineMicrophone, HiOutlineStop, HiOutlineVolumeOff, HiOutlinePlay } from 'react-icons/hi'
import Button from '@/components/ui/Button'
import StatusPill from '@/components/shared/StatusPill'
import type { StatusTone } from '@/components/shared/StatusPill'
import Table from '@/components/ui/Table'
import { RiyadhTime } from '@/components/v2/Display'
import { describeVoiceFailure } from '@/services/voiceLab'
import type { VoiceLang, VoiceScenario, VoiceSessionRecord, VoiceSnapshot } from '@/services/voiceLab'
import { Card, Notice } from '@/views/phase5/parts'
import { clock, pick } from './format'

const { THead, TBody, Tr, Th, Td } = Table

const DIFFICULTY_TONE: Record<VoiceScenario['difficulty'], StatusTone> = { easy: 'success', medium: 'warning', hard: 'danger' }

// ------------------------------------------------------------------------------ picker
export const ScenarioPicker = ({
    scenarios,
    selected,
    onSelect,
    disabled,
    ar,
}: {
    scenarios: VoiceScenario[]
    selected: string | null
    onSelect: (id: string) => void
    disabled: boolean
    ar: boolean
}) => {
    const { t } = useTranslation()
    return (
        <div role="radiogroup" aria-label={String(t('p6vl.picker.title'))} className="flex flex-col gap-2" data-testid="p6vl-scenarios">
            {scenarios.map((s) => {
                const active = s.id === selected
                return (
                    <button
                        key={s.id}
                        type="button"
                        role="radio"
                        aria-checked={active}
                        disabled={disabled}
                        data-testid="p6vl-scenario"
                        data-id={s.id}
                        onClick={() => onSelect(s.id)}
                        className={classNames(
                            'rounded-lg border px-4 py-3 text-start transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-azure-600 focus-visible:ring-offset-2',
                            active
                                ? 'border-navy-600 bg-info-tint dark:border-azure-600 dark:bg-night-raised'
                                : 'border-gray-300 bg-white hover:bg-gray-50 dark:border-night-border dark:bg-night-card',
                            disabled && 'cursor-not-allowed opacity-70'
                        )}
                    >
                        <div className="flex flex-wrap items-center justify-between gap-2">
                            <span className="text-[14px] font-extrabold text-navy-600 dark:text-gray-100">{pick(s.title, ar)}</span>
                            <StatusPill tone={DIFFICULTY_TONE[s.difficulty]} size="row">
                                {t(`p6vl.difficulty.${s.difficulty}`)}
                            </StatusPill>
                        </div>
                        <div className="mt-1 text-[12.5px] text-gray-600 dark:text-night-muted">
                            {t('p6vl.picker.persona', { name: pick(s.persona_name, ar) })} · {pick(s.summary, ar)}
                        </div>
                    </button>
                )
            })}
        </div>
    )
}

// ------------------------------------------------------------------------------ session panel
const PHASE_TONE: Record<VoiceSnapshot['phase'], StatusTone> = {
    idle: 'neutral',
    mic: 'info',
    minting: 'info',
    connecting: 'info',
    live: 'success',
    ended: 'neutral',
    failed: 'danger',
}

export const MicIndicator = ({ live, muted }: { live: boolean; muted: boolean }) => {
    const { t } = useTranslation()
    return (
        <span
            data-testid="p6vl-mic"
            data-live={live ? 'true' : 'false'}
            data-muted={muted ? 'true' : 'false'}
            className={classNames(
                'inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-[12px] font-extrabold',
                live && !muted && 'border-red-200 bg-danger-tint text-danger',
                live && muted && 'border-warning/30 bg-warning-tint text-gray-800',
                !live && 'border-gray-300 bg-gray-50 text-gray-600 dark:border-night-border dark:bg-night-raised'
            )}
        >
            <span
                aria-hidden
                className={classNames('inline-block h-2 w-2 rounded-full', live && !muted ? 'animate-pulse bg-danger' : live ? 'bg-warning' : 'bg-gray-400')}
            />
            {live ? (muted ? t('p6vl.mic.muted') : t('p6vl.mic.on')) : t('p6vl.mic.off')}
        </span>
    )
}

export const SessionTimer = ({ snap }: { snap: VoiceSnapshot }) => {
    const { t } = useTranslation()
    const limit = snap.limitSeconds
    const elapsed = snap.startedAt ? Math.min(limit, (snap.now - snap.startedAt) / 1000) : 0
    const remaining = snap.deadlineAt ? Math.max(0, (snap.deadlineAt - snap.now) / 1000) : limit
    const pct = Math.min(100, (elapsed / limit) * 100)
    return (
        <div data-testid="p6vl-timer" data-elapsed={Math.floor(elapsed)} data-remaining={Math.ceil(remaining)} data-limit={limit}>
            <div className="flex items-baseline justify-between gap-3">
                <span dir="ltr" className="text-[32px] font-extrabold tabular-nums text-navy-600 dark:text-gray-100">
                    {clock(elapsed)}
                </span>
                <span className="text-[12.5px] text-gray-600 tabular-nums">
                    {t('p6vl.timer.remaining', { time: clock(Math.ceil(remaining)) })} · {t('p6vl.timer.limit', { time: clock(limit) })}
                </span>
            </div>
            <div
                className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-gray-200 dark:bg-night-border"
                role="progressbar"
                aria-valuemin={0}
                aria-valuemax={limit}
                aria-valuenow={Math.floor(elapsed)}
                aria-label={String(t('p6vl.timer.aria'))}
            >
                <div className={classNames('h-full rounded-full', pct >= 90 ? 'bg-danger' : 'bg-navy-600 dark:bg-azure-600')} style={{ width: `${pct}%` }} />
            </div>
        </div>
    )
}

export const SessionPanel = ({
    snap,
    scenario,
    language,
    canStart,
    startBlockedReason,
    onStart,
    onEnd,
    onMute,
}: {
    snap: VoiceSnapshot
    scenario: VoiceScenario | null
    language: VoiceLang
    canStart: boolean
    startBlockedReason: string | null
    onStart: () => void
    onEnd: () => void
    onMute: (muted: boolean) => void
}) => {
    const { t } = useTranslation()
    const running = ['mic', 'minting', 'connecting', 'live'].includes(snap.phase)
    const finished = snap.phase === 'ended' || snap.phase === 'failed'
    return (
        <Card
            lead
            testId="p6vl-session"
            title={t('p6vl.session.title')}
            actions={
                <StatusPill tone={PHASE_TONE[snap.phase]} size="md">
                    <span data-testid="p6vl-phase" data-phase={snap.phase}>
                        {t(`p6vl.phase.${snap.phase}`)}
                    </span>
                </StatusPill>
            }
        >
            {scenario && (
                <div className="mb-4">
                    <div className="text-eyebrow mb-1">{t('p6vl.session.brief')}</div>
                    <p className="mb-2 text-[13.5px] text-gray-800 dark:text-night-body" data-testid="p6vl-brief">
                        {pick(scenario.brief, language === 'ar')}
                    </p>
                    <div className="text-eyebrow mb-1">{t('p6vl.session.goals')}</div>
                    <ul className="ms-5 list-disc text-[13px] text-gray-700 dark:text-night-body" dir={language === 'ar' ? 'rtl' : 'ltr'}>
                        {(language === 'ar' ? scenario.goals.ar : scenario.goals.en).map((g) => (
                            <li key={g}>{g}</li>
                        ))}
                    </ul>
                </div>
            )}

            {running || snap.startedAt ? (
                <div className="mb-4 flex flex-col gap-3">
                    <div className="flex flex-wrap items-center gap-2">
                        <MicIndicator live={snap.micLive} muted={snap.muted} />
                        {snap.personaName && running && (
                            <span className="text-[13px] text-gray-700 dark:text-night-body" data-testid="p6vl-persona">
                                {t('p6vl.session.persona', { name: snap.personaName })}
                            </span>
                        )}
                        {snap.phase === 'connecting' && <span className="text-[12.5px] text-gray-600">{t('p6vl.session.connecting')}</span>}
                    </div>
                    <SessionTimer snap={snap} />
                    {snap.summary && <p className="mb-0 text-[12.5px] text-gray-600 dark:text-night-muted">{snap.summary}</p>}
                </div>
            ) : (
                <p className="mb-4 text-[12.5px] text-gray-600 dark:text-night-muted">{t('p6vl.session.micNote')}</p>
            )}

            {finished && snap.endOutcome && (
                <Notice tone={snap.endOutcome === 'time_limit' || snap.endOutcome === 'user_ended' ? 'info' : 'warning'} className="mb-3" testId="p6vl-outcome">
                    <span data-outcome={snap.endOutcome}>{t(`p6vl.outcome.${snap.endOutcome}`)}</span>{' '}
                    {snap.session?.duration_seconds !== null && snap.session?.duration_seconds !== undefined && snap.endReported === 'reported'
                        ? t('p6vl.session.recorded', { time: clock(snap.session.duration_seconds) })
                        : snap.endReported === 'failed'
                        ? t('p6vl.session.notRecorded')
                        : null}
                </Notice>
            )}
            {snap.phase === 'failed' && snap.failure && (
                <Notice tone="danger" className="mb-3" testId="p6vl-error">
                    {describeVoiceFailure(snap.failure, t)}
                </Notice>
            )}

            <div className="flex flex-wrap items-center gap-2">
                {!running && (
                    <Button
                        variant="solid"
                        icon={<HiOutlinePlay />}
                        disabled={!canStart}
                        aria-disabled={!canStart}
                        onClick={() => canStart && onStart()}
                        data-testid="p6vl-start"
                        title={startBlockedReason || undefined}
                    >
                        {finished ? t('p6vl.action.startAgain') : t('p6vl.action.start')}
                    </Button>
                )}
                {running && (
                    <>
                        <Button
                            icon={snap.muted ? <HiOutlineMicrophone /> : <HiOutlineVolumeOff />}
                            disabled={!snap.micLive}
                            onClick={() => onMute(!snap.muted)}
                            data-testid="p6vl-mute"
                            aria-pressed={snap.muted}
                        >
                            {snap.muted ? t('p6vl.action.unmute') : t('p6vl.action.mute')}
                        </Button>
                        <Button variant="destructive" icon={<HiOutlineStop />} onClick={onEnd} data-testid="p6vl-end">
                            {t('p6vl.action.end')}
                        </Button>
                    </>
                )}
            </div>
            {!running && startBlockedReason && (
                <p className="mb-0 mt-2 text-[12.5px] text-gray-600" data-testid="p6vl-start-blocked">
                    {startBlockedReason}
                </p>
            )}
            {!scenario && !running && <p className="mb-0 mt-2 text-[12.5px] text-gray-600">{t('p6vl.session.pick')}</p>}
        </Card>
    )
}

// ------------------------------------------------------------------------------ history
export const History = ({ items, ar }: { items: VoiceSessionRecord[]; ar: boolean }) => {
    const { t } = useTranslation()
    if (!items.length) return <p className="mb-0 text-[12.5px] text-gray-600">{t('p6vl.history.empty')}</p>
    return (
        <Table>
            <THead>
                <Tr>
                    <Th>{t('p6vl.history.started')}</Th>
                    <Th>{t('p6vl.history.scenario')}</Th>
                    <Th>{t('p6vl.history.language')}</Th>
                    <Th>{t('p6vl.history.duration')}</Th>
                    <Th>{t('p6vl.history.outcome')}</Th>
                </Tr>
            </THead>
            <TBody>
                {items.map((s) => (
                    <Tr key={s.id} data-testid="p6vl-history-row" data-id={s.id}>
                        <Td>
                            <RiyadhTime iso={s.started_at} />
                        </Td>
                        <Td>{pick(s.scenario_title, ar) || s.scenario_id}</Td>
                        <Td>{t(`p6vl.lang.${s.language}`)}</Td>
                        <Td>
                            {s.duration_seconds === null ? (
                                <span className="text-gray-500" title={String(t('p6vl.history.unknownHint'))}>
                                    {t('p6vl.history.unknown')}
                                </span>
                            ) : (
                                <span dir="ltr" className="tabular-nums">
                                    {clock(s.duration_seconds)}
                                </span>
                            )}
                        </Td>
                        <Td>
                            {s.outcome ? t(`p6vl.outcome.${s.outcome}`) : t(`p6vl.state.${s.state}`)}
                        </Td>
                    </Tr>
                ))}
            </TBody>
        </Table>
    )
}
