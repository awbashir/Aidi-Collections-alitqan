/**
 * Call control on one line (2026-10-20): transfer (blind), consult first (attended transfer:
 * the customer waits on hold, then complete or cancel) and add a participant (conference; remove
 * them again). The PBX does it for the server (AMI), never a browser REFER: endpoints keep
 * allow_transfer = no. Targets are app users' extensions with their presence; external numbers
 * are not offered. The control is followed by polling the API: "submitted" means the PBX
 * accepted the command, the legs' state comes from the PBX channel list.
 *
 * Also the line of a call that call control placed to me: who transfers / invites and the case
 * link (when the case is in my scope), and for a supervisor's own listening call the
 * listen / whisper / join buttons (the keypad is hidden there: ChanSpy reads 4/5/6 as modes).
 */
import classNames from 'classnames'
import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { ArrowRightLeft, Ear, MessageSquare, PhoneForwarded, PhoneOff, Search, UserPlus, Users } from 'lucide-react'
import StatusPill from '@/components/shared/StatusPill'
import type { StatusTone } from '@/components/shared/StatusPill'
import { caseLink } from '@/utils/v2/links'
import { callControlApi, describeControlError, pollControl } from '@/services/softphone'
import type { CallControl, ControlTarget, Line, SpyMode } from '@/services/softphone'
import { btnDanger, btnPrimary, btnSecondary } from './buttons'

type Action = 'blind' | 'attended' | 'conference'

const PRESENCE_TONE: Record<ControlTarget['presence'], StatusTone> = {
    free: 'success',
    on_call: 'warning',
    ringing: 'warning',
    busy: 'neutral',
    unknown: 'neutral',
}

const STATE_TONE: Record<CallControl['state'], StatusTone> = {
    queued: 'info',
    active: 'info',
    completed: 'success',
    cancelled: 'neutral',
    failed: 'danger',
    ended: 'neutral',
}

const who = (name: string | null | undefined, ext: string) => (name ? `${name} (${ext})` : ext)

export const ControlStatus = ({ control, busy, onCommand }: { control: CallControl; busy: boolean; onCommand: (op: 'complete' | 'cancel' | 'remove') => void }) => {
    const { t } = useTranslation()
    const target = who(control.target.name, control.target.extension)
    const phase = control.phase
    const sending = !!control.last_op && ['queued', 'sending'].includes(control.last_op.state)
    const refused = control.last_op && ['refused', 'rejected', 'unacknowledged', 'expired'].includes(control.last_op.state)
    const targetUp = phase.known && phase.target === 'up'
    return (
        <div
            data-testid="sp-control"
            data-kind={control.kind}
            data-state={control.state}
            className="mt-2 rounded-md border border-slate-200 bg-slate-25 p-2.5 text-[12.5px] dark:border-night-border dark:bg-night-raised"
        >
            <div className="flex items-center justify-between gap-2">
                <span className="font-extrabold text-navy-600 dark:text-night-strong">
                    {t(`p10.callctl.kind.${control.kind}`, { target })}
                </span>
                <StatusPill tone={STATE_TONE[control.state]}>{t(`p10.callctl.state.${control.state}`)}</StatusPill>
            </div>
            {phase.known && (
                <div className="mt-1 text-gray-600 dark:text-night-body" data-testid="sp-control-phase">
                    {phase.target && <span>{t(`p10.callctl.phase.target_${phase.target}`, { target })}</span>}
                    {phase.customer && phase.customer !== 'up' && (
                        <span className="ms-2">{t(`p10.callctl.phase.customer_${phase.customer}`)}</span>
                    )}
                </div>
            )}
            {!phase.known && control.state !== 'ended' && <div className="mt-1 text-gray-500">{t('p10.callctl.phase.unknown')}</div>}
            {sending && <div className="mt-1 text-gray-500">{t('p10.callctl.sending')}</div>}
            {refused && control.last_op?.result_code && (
                <div className="mt-1 text-danger" data-testid="sp-control-refused">
                    {t(`p10.callctl.result.${control.last_op.result_code}`, control.last_op.result_code)}
                </div>
            )}
            {control.result_code && !refused && control.state !== 'active' && (
                <div className="mt-1 text-gray-600">{t(`p10.callctl.result.${control.result_code}`, control.result_code)}</div>
            )}
            {control.kind === 'transfer_attended' && control.state === 'active' && (
                <div className="mt-1 text-[11.5px] text-gray-500">{t('p10.callctl.consultHint')}</div>
            )}
            {control.actions.length > 0 && (
                <div className="mt-2 flex flex-wrap gap-2">
                    {control.actions.includes('complete') && (
                        <button
                            type="button"
                            data-testid="sp-control-complete"
                            className={btnPrimary}
                            disabled={busy || !targetUp}
                            title={!targetUp ? (t('p10.callctl.completeWhenAnswered') as string) : undefined}
                            onClick={() => onCommand('complete')}
                        >
                            <PhoneForwarded size={15} aria-hidden />
                            {t('p10.callctl.complete')}
                        </button>
                    )}
                    {control.actions.includes('cancel') && (
                        <button type="button" data-testid="sp-control-cancel" className={btnSecondary} disabled={busy} onClick={() => onCommand('cancel')}>
                            {t('p10.callctl.cancelConsult')}
                        </button>
                    )}
                    {control.actions.includes('remove') && (
                        <button type="button" data-testid="sp-control-remove" className={btnSecondary} disabled={busy} onClick={() => onCommand('remove')}>
                            <PhoneOff size={15} aria-hidden />
                            {t('p10.callctl.remove', { target })}
                        </button>
                    )}
                </div>
            )}
        </div>
    )
}

/** Follow one control; returns it, the last error and a command runner. */
function useControl(initial: CallControl | null) {
    const [control, setControl] = useState<CallControl | null>(initial)
    const [error, setError] = useState<unknown>(null)
    const [busy, setBusy] = useState(false)
    const id = control?.id || null
    useEffect(() => {
        if (!id) return undefined
        return pollControl(
            id,
            (c) => {
                setControl(c)
                setError(null)
            },
            setError
        )
    }, [id])
    const run = async (op: 'complete' | 'cancel' | 'remove' | 'stop' | 'mode', mode?: SpyMode) => {
        if (!control) return
        setBusy(true)
        try {
            setControl(await callControlApi.command(control.id, op, mode))
            setError(null)
        } catch (e) {
            setError(e)
        } finally {
            setBusy(false)
        }
    }
    return { control, setControl, error, setError, busy, setBusy, run }
}

const CallControlBox = ({ line }: { line: Line }) => {
    const { t } = useTranslation()
    const [action, setAction] = useState<Action>('blind')
    const [q, setQ] = useState('')
    const [targets, setTargets] = useState<ControlTarget[] | null>(null)
    const [presenceKnown, setPresenceKnown] = useState(true)
    const [selected, setSelected] = useState<ControlTarget | null>(null)
    const { control, setControl, error, setError, busy, setBusy, run } = useControl(null)

    useEffect(() => {
        const ctrl = new AbortController()
        const timer = setTimeout(() => {
            callControlApi
                .targets(q.trim() || undefined, ctrl.signal)
                .then((res) => {
                    setTargets(res.items)
                    setPresenceKnown(res.presence_known)
                })
                .catch((e) => {
                    if (!(e instanceof DOMException && e.name === 'AbortError')) setError(e)
                })
        }, 250)
        return () => {
            clearTimeout(timer)
            ctrl.abort()
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [q])

    const open = control && !control.ended_at && ['queued', 'active'].includes(control.state)
    const start = async () => {
        if (!selected) return
        setBusy(true)
        setError(null)
        const common = { target_extension: selected.extension, attempt_id: line.attemptId, remote: line.remote || null }
        try {
            const c =
                action === 'conference'
                    ? await callControlApi.conference(common)
                    : await callControlApi.transfer({ ...common, mode: action })
            setControl(c)
        } catch (e) {
            setError(e)
        } finally {
            setBusy(false)
        }
    }
    const list = useMemo(() => targets || [], [targets])
    const ACTIONS: { key: Action; icon: typeof ArrowRightLeft }[] = [
        { key: 'blind', icon: ArrowRightLeft },
        { key: 'attended', icon: MessageSquare },
        { key: 'conference', icon: UserPlus },
    ]

    return (
        <div className="mt-2" data-testid="sp-callctl">
            {!open && (
                <>
                    <div className="flex flex-wrap gap-1.5" role="radiogroup" aria-label={t('p10.callctl.title') as string}>
                        {ACTIONS.map(({ key, icon: Icon }) => (
                            <button
                                key={key}
                                type="button"
                                role="radio"
                                aria-checked={action === key}
                                data-testid={`sp-callctl-${key}`}
                                onClick={() => setAction(key)}
                                className={classNames(btnSecondary, action === key && 'border-azure-500 text-azure-600 ring-1 ring-azure-500')}
                            >
                                <Icon size={15} aria-hidden />
                                {t(`p10.callctl.action.${key}`)}
                            </button>
                        ))}
                    </div>
                    <div className="mt-1 text-[11.5px] text-gray-500">{t(`p10.callctl.actionHint.${action}`)}</div>
                    <label className="mt-2 flex items-center gap-2 rounded-md border border-slate-300 px-2 focus-within:border-azure-500 dark:border-night-border">
                        <Search size={14} aria-hidden className="text-gray-400" />
                        <input
                            data-testid="sp-callctl-search"
                            value={q}
                            onChange={(e) => setQ(e.target.value.slice(0, 60))}
                            placeholder={t('p10.callctl.searchPlaceholder') as string}
                            aria-label={t('p10.callctl.searchPlaceholder') as string}
                            className="h-9 w-full bg-transparent text-[13px] focus:outline-none"
                        />
                    </label>
                    <ul className="mt-1.5 max-h-44 overflow-y-auto rounded-md border border-slate-200 dark:border-night-border" data-testid="sp-callctl-targets">
                        {targets === null && <li className="px-3 py-2 text-gray-500">{t('p10.callctl.loadingTargets')}</li>}
                        {targets !== null && !list.length && <li className="px-3 py-2 text-gray-500">{t('p10.callctl.noTargets')}</li>}
                        {list.map((u) => (
                            <li key={u.extension}>
                                <button
                                    type="button"
                                    data-testid="sp-callctl-target"
                                    data-ext={u.extension}
                                    aria-pressed={selected?.extension === u.extension}
                                    onClick={() => setSelected(u)}
                                    className={classNames(
                                        'flex w-full items-center justify-between gap-2 px-3 py-1.5 text-start text-[13px] hover:bg-slate-50 dark:hover:bg-night-raised',
                                        selected?.extension === u.extension && 'bg-azure-50 dark:bg-night-raised'
                                    )}
                                >
                                    <span className="min-w-0 truncate" dir="auto">
                                        {u.name || u.extension}
                                        <span className="ms-1.5 tabular-nums text-gray-500" dir="ltr">
                                            {u.extension}
                                        </span>
                                    </span>
                                    <StatusPill tone={PRESENCE_TONE[presenceKnown ? u.presence : 'unknown']} size="row">
                                        {t(`p10.callctl.presence.${presenceKnown ? u.presence : 'unknown'}`)}
                                    </StatusPill>
                                </button>
                            </li>
                        ))}
                    </ul>
                    <div className="mt-2 flex flex-wrap items-center gap-2">
                        <button type="button" data-testid="sp-callctl-go" className={btnPrimary} disabled={!selected || busy} onClick={() => void start()}>
                            {action === 'conference' ? <Users size={15} aria-hidden /> : <PhoneForwarded size={15} aria-hidden />}
                            {selected ? t(`p10.callctl.go.${action}`, { target: who(selected.name, selected.extension) }) : t('p10.callctl.pickTarget')}
                        </button>
                    </div>
                    <div className="mt-1 text-[11.5px] text-gray-500">{t('p10.callctl.internalOnly')}</div>
                </>
            )}
            {control && <ControlStatus control={control} busy={busy} onCommand={(op) => void run(op)} />}
            {control && !open && (
                <button type="button" className="mt-2 text-[12px] font-extrabold text-azure-600 underline" onClick={() => setControl(null)}>
                    {t('p10.callctl.again')}
                </button>
            )}
            {error ? (
                <div className="mt-2 text-[12px] text-danger" data-testid="sp-callctl-error">
                    {describeControlError(error, t)}
                </div>
            ) : null}
        </div>
    )
}

/** The line of a call that call control placed to me. */
export const IncomingControlInfo = ({ line }: { line: Line }) => {
    const { t } = useTranslation()
    const c = line.control
    if (!c) {
        return line.controlTag ? <div className="mt-2 text-[12px] text-gray-500">{t('p10.callctl.incoming.unknown')}</div> : null
    }
    const from = who(c.fromName, c.fromExtension)
    return (
        <div data-testid="sp-incoming-control" data-role={c.role} className="mt-2 rounded-md border border-azure-100 bg-info-tint p-2.5 text-[12.5px] dark:border-night-border dark:bg-night-raised">
            <div className="font-extrabold text-navy-600 dark:text-night-strong">
                {t(`p10.callctl.incoming.${c.role}`, { from, target: who(c.targetName, c.targetExtension) })}
            </div>
            {c.role === 'listen' && line.autoAnswered && <div className="mt-1 text-gray-600">{t('p10.callctl.incoming.listenAuto')}</div>}
            {c.caseId && (
                <div className="mt-1 flex flex-wrap items-baseline gap-x-2">
                    <Link data-testid="sp-incoming-case" to={caseLink(c.caseId)} className="font-extrabold text-azure-600 hover:underline">
                        {c.caseName || t('sp.attempt.case')}
                    </Link>
                    {c.caseAccount && (
                        <span dir="ltr" className="tabular-nums text-gray-500">
                            {c.caseAccount}
                        </span>
                    )}
                </div>
            )}
            {c.caseHidden && <div className="mt-1 text-[11.5px] text-gray-500">{t('p10.callctl.incoming.caseHidden')}</div>}
            {(c.role === 'transfer' || c.role === 'consult') && <div className="mt-1 text-[11.5px] text-gray-500">{t('p10.callctl.incoming.wrapupNote')}</div>}
        </div>
    )
}

/** Listen / whisper / join for the supervisor's own listening call (on the softphone line). */
export const ListenModes = ({ controlId, initialMode }: { controlId: string; initialMode: SpyMode | null }) => {
    const { t } = useTranslation()
    const [mode, setMode] = useState<SpyMode | null>(initialMode)
    const [busy, setBusy] = useState(false)
    const [error, setError] = useState<unknown>(null)
    const set = async (m: SpyMode | 'stop') => {
        setBusy(true)
        try {
            const c = m === 'stop' ? await callControlApi.command(controlId, 'stop') : await callControlApi.command(controlId, 'mode', m)
            if (m !== 'stop') setMode(c.last_op?.state === 'done' ? c.mode : m)
            setError(null)
        } catch (e) {
            setError(e)
        } finally {
            setBusy(false)
        }
    }
    return (
        <div className="mt-2" data-testid="sp-listen-modes">
            <div className="flex flex-wrap gap-1.5">
                {(['listen', 'whisper', 'barge'] as SpyMode[]).map((m) => (
                    <button
                        key={m}
                        type="button"
                        data-testid={`sp-listen-${m}`}
                        aria-pressed={mode === m}
                        disabled={busy}
                        onClick={() => void set(m)}
                        className={classNames(btnSecondary, mode === m && 'border-azure-500 text-azure-600 ring-1 ring-azure-500')}
                    >
                        {m === 'listen' ? <Ear size={15} aria-hidden /> : m === 'whisper' ? <MessageSquare size={15} aria-hidden /> : <Users size={15} aria-hidden />}
                        {t(`p10.callctl.mode.${m}`)}
                    </button>
                ))}
                <button type="button" data-testid="sp-listen-stop" className={btnDanger} disabled={busy} onClick={() => void set('stop')}>
                    <PhoneOff size={15} aria-hidden />
                    {t('p10.callctl.stopListening')}
                </button>
            </div>
            <div className="mt-1 text-[11.5px] text-gray-500">{t(`p10.callctl.modeHint.${mode || 'listen'}`)}</div>
            {error ? <div className="mt-1 text-[12px] text-danger">{describeControlError(error, t)}</div> : null}
        </div>
    )
}

export default CallControlBox
