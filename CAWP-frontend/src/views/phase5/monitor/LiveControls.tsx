/**
 * Call monitor, live calls: supervisor listening (2026-10-20, `POST /v2/calling/control/spy`).
 *
 * A supervisor (calls_supervise or calls_manage) with an extension can LISTEN to an agent's
 * connected call in their scope (SuperAdmin every extension, a supervisor their team's; the API
 * refuses the rest), then switch to WHISPER (only the agent hears them) or JOIN (barge: everyone
 * hears), and stop. The PBX rings the supervisor's own extension: with the in-app softphone
 * switched on (0082) the softphone opens and answers that call by itself (it carries our
 * X-CAWP-Control tag); with it off (the default) the supervisor's own phone rings and they pick
 * it up. Nobody hears the supervisor while listening. Every start, mode change and stop is
 * audited on the server.
 */
import classNames from 'classnames'
import { useTranslation } from 'react-i18next'
import { Ear, MessageSquare, PhoneOff, Users } from 'lucide-react'
import { describeControlError } from '@/services/softphone'
import type { SpyMode } from '@/services/softphone'
import { Notice } from '../parts'
import type { ListenState, LiveCall } from './useListen'

const MODES: SpyMode[] = ['listen', 'whisper', 'barge']
const MODE_ICON = { listen: Ear, whisper: MessageSquare, barge: Users }

const chip =
    'inline-flex h-8 items-center gap-1.5 rounded-md border px-2.5 text-[12.5px] font-extrabold focus:outline-none focus-visible:ring-2 focus-visible:ring-azure-500 disabled:cursor-not-allowed disabled:opacity-50'
const chipIdle = 'border-slate-300 bg-white text-navy-600 hover:bg-slate-50 dark:border-night-border dark:bg-night-card dark:text-night-strong'
const chipOn = 'border-azure-500 bg-azure-50 text-azure-700 dark:bg-night-raised dark:text-azure-300'

/** The "Listen" button(s) of one live call row (one per visible agent). */
export const ListenButton = ({ listen, call }: { listen: ListenState; call: LiveCall }) => {
    const { t } = useTranslation()
    if (!listen.caps?.listen) return null
    const agents = call.agents.filter((a) => a.extension !== listen.caps?.extension)
    if (call.state !== 'talking' || !agents.length) return <span className="text-gray-400">—</span>
    return (
        <div className="flex flex-wrap gap-1.5">
            {agents.map((a) => {
                const mine = listen.open && listen.control?.target.extension === a.extension
                return (
                    <button
                        key={a.extension}
                        type="button"
                        data-testid="p10-listen"
                        data-ext={a.extension}
                        disabled={listen.busy || (listen.open && !mine)}
                        className={classNames(chip, mine ? chipOn : chipIdle)}
                        title={t('p10.callctl.monitor.listenTo', { agent: a.name || a.extension }) as string}
                        onClick={() => !mine && void listen.start(call, a.extension, 'listen')}
                    >
                        <Ear size={14} aria-hidden />
                        {mine ? t('p10.callctl.monitor.listening') : t('p10.callctl.monitor.listen')}
                        {call.agents.length > 1 && (
                            <span className="tabular-nums text-gray-500" dir="ltr">
                                {a.extension}
                            </span>
                        )}
                    </button>
                )
            })}
        </div>
    )
}

/** The small control panel while listening: mode (listen / whisper / join) and stop. */
export const ListenPanel = ({ listen }: { listen: ListenState }) => {
    const { t } = useTranslation()
    const c = listen.control
    if (!listen.caps?.listen) return null
    if (!c) {
        return listen.error ? (
            <Notice tone="danger" testId="p10-listen-error">
                {describeControlError(listen.error, t)}
            </Notice>
        ) : null
    }
    const agent = c.target.name ? `${c.target.name} (${c.target.extension})` : c.target.extension
    const leg = c.phase.known ? c.phase.target : undefined
    const status = !listen.open
        ? t(`p10.callctl.state.${c.state}`)
        : leg === 'up'
        ? t('p10.callctl.monitor.connected')
        : leg === 'ringing'
        ? listen.softphone === false
            ? t('p10.callctl.monitor.ringingDesk', { ext: c.from.extension })
            : listen.softphone
            ? t('p10.callctl.monitor.ringingSoftphone', { ext: c.from.extension })
            : t('p10.callctl.monitor.ringingYou', { ext: c.from.extension })
        : t('p10.callctl.sending')
    const refused = c.last_op && ['refused', 'rejected', 'unacknowledged', 'expired'].includes(c.last_op.state)
    return (
        <div
            data-testid="p10-listen-panel"
            data-state={c.state}
            data-mode={c.mode || ''}
            className="rounded-lg border border-azure-100 bg-info-tint px-3 py-2.5 text-[13px] dark:border-night-border dark:bg-night-raised"
        >
            <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="min-w-0">
                    <div className="font-extrabold text-navy-600 dark:text-gray-100" dir="auto">
                        {t('p10.callctl.monitor.title', { agent })}
                    </div>
                    <div className="text-[12.5px] text-gray-600 dark:text-night-body">{status}</div>
                </div>
                {listen.open ? (
                    <div className="flex flex-wrap items-center gap-1.5" role="radiogroup" aria-label={t('p10.callctl.monitor.modeLabel') as string}>
                        {MODES.map((m) => {
                            const Icon = MODE_ICON[m]
                            return (
                                <button
                                    key={m}
                                    type="button"
                                    role="radio"
                                    aria-checked={c.mode === m}
                                    data-testid={`p10-listen-mode-${m}`}
                                    disabled={listen.busy || c.mode === m || leg !== 'up'}
                                    className={classNames(chip, c.mode === m ? chipOn : chipIdle)}
                                    onClick={() => void listen.command('mode', m)}
                                >
                                    <Icon size={14} aria-hidden />
                                    {t(`p10.callctl.mode.${m}`)}
                                </button>
                            )
                        })}
                        <button
                            type="button"
                            data-testid="p10-listen-stop"
                            disabled={listen.busy}
                            className={classNames(chip, 'border-danger bg-danger text-white hover:opacity-90')}
                            onClick={() => void listen.command('stop')}
                        >
                            <PhoneOff size={14} aria-hidden />
                            {t('p10.callctl.stopListening')}
                        </button>
                    </div>
                ) : (
                    <button type="button" className="text-[12.5px] font-extrabold text-navy-600 underline dark:text-gray-100" onClick={listen.dismiss}>
                        {t('p10.callctl.monitor.dismiss')}
                    </button>
                )}
            </div>
            {listen.open && <div className="mt-1 text-[11.5px] text-gray-500">{t(`p10.callctl.modeHint.${c.mode || 'listen'}`)}</div>}
            {refused && c.last_op?.result_code && (
                <div className="mt-1 text-[12.5px] text-danger">{t(`p10.callctl.result.${c.last_op.result_code}`, c.last_op.result_code)}</div>
            )}
            {!listen.open && c.result_code && (
                <div className="mt-1 text-[12.5px] text-danger">{t(`p10.callctl.result.${c.result_code}`, c.result_code)}</div>
            )}
            {listen.error ? <div className="mt-1 text-[12.5px] text-danger">{describeControlError(listen.error, t)}</div> : null}
        </div>
    )
}
