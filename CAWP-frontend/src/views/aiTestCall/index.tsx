/**
 * AI calling agent test ("تجربة وكيل الاتصال الذكي", `/ai-test-call`, derived key `ai_test_call` = ai_admin;
 * customer request 2026-11-04, docs/alitqan-ai-calling-agent-plan.md).
 *
 * Type a Saudi number, pick the outbound number, a voice and edit the agent's script; Start asks the API
 * to queue the call and the AI voice service dials it through the PBX, then the OpenAI Realtime model
 * talks with whoever answers. The page follows the call (state, timer, live transcript) and Stop hangs
 * up. A test only: no client or case is touched. Readiness problems (no approved AI data policy, no
 * model, the service not running) are shown before anything is dialled.
 */
import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import classNames from 'classnames'
import Button from '@/components/ui/Button'
import Input from '@/components/ui/Input'
import Select from '@/components/ui/Select'
import Table from '@/components/ui/Table'
import { Ltr, RiyadhTime } from '@/components/v2/Display'
import { EmptyState, ErrorState, SkeletonLines } from '@/components/v2/States'
import { PageHead, TableFrame, rowClass } from '@/components/v2/ListParts'
import { Card, Fact, Notice } from '@/views/phase5/parts'
import { DialogField } from '@/components/v2/ActionDialog'
import { describeP5Error } from '@/services/v2'
import type { Option, Translate } from '@/services/v2'
import { OPEN_STATES, aiTestCallsApi } from '@/services/v2/aiTestCallsApi'
import type { AiTestCall } from '@/services/v2/aiTestCallsApi'
import { useV2Query } from '@/utils/v2/hooks'

const { THead, TBody, Tr, Th, Td } = Table
const describe = (e: unknown, t: Translate) => describeP5Error(e, t)
const POLL_MS = 1500

const clock = (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, '0')}`

const StateBadge = ({ call, t }: { call: AiTestCall; t: Translate }) => {
    const open = OPEN_STATES.includes(call.state)
    const tone = open
        ? 'bg-azure-50 text-azure-700 dark:bg-azure-500/15 dark:text-azure-300'
        : call.result === 'completed' || call.result === 'time_limit'
          ? 'bg-emerald-50 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300'
          : 'bg-amber-50 text-amber-700 dark:bg-amber-500/15 dark:text-amber-300'
    return (
        <span className={classNames('inline-block rounded-full px-2 py-0.5 text-[12px] font-extrabold', tone)}>
            {open ? t(`p13.aitest.state.${call.state}`) : t(`p13.aitest.result.${call.result || 'unknown'}`)}
        </span>
    )
}

const Transcript = ({ call, t }: { call: AiTestCall; t: Translate }) =>
    call.transcript.length ? (
        <div className="flex flex-col gap-2" data-testid="p13-aitest-transcript">
            {call.transcript.map((m, i) => (
                <div key={i} className={classNames('flex', m.role === 'agent' ? 'justify-start' : 'justify-end')}>
                    <div
                        className={classNames(
                            'max-w-[80%] rounded-lg px-3 py-2 text-[13.5px]',
                            m.role === 'agent' ? 'bg-navy-50 text-navy-700 dark:bg-night-raised dark:text-gray-100' : 'bg-gray-100 text-gray-800 dark:bg-night-border dark:text-night-body'
                        )}
                    >
                        <div className="mb-0.5 text-[11px] font-extrabold opacity-70">{t(`p13.aitest.role.${m.role}`)}</div>
                        {m.text}
                    </div>
                </div>
            ))}
        </div>
    ) : (
        <div className="text-[12.5px] text-gray-600">{t('p13.aitest.noTranscript')}</div>
    )

const AiTestCallPage = () => {
    const { t } = useTranslation()
    const opts = useV2Query((signal) => aiTestCallsApi.options(signal), [])
    const history = useV2Query((signal) => aiTestCallsApi.list(signal), [])
    const [destination, setDestination] = useState('')
    const [callerId, setCallerId] = useState('')
    const [voice, setVoice] = useState('')
    const [script, setScript] = useState('')
    const [busy, setBusy] = useState(false)
    const [error, setError] = useState<unknown>(null)
    const [current, setCurrent] = useState<AiTestCall | null>(null)
    const [viewing, setViewing] = useState<AiTestCall | null>(null)
    const [now, setNow] = useState(Date.now())
    const o = opts.data

    useEffect(() => {
        if (o && !script) setScript(o.default_instructions)
        if (o && !voice) setVoice(o.default_voice)
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [o])

    /* an open call from before (page reload) is followed again */
    useEffect(() => {
        const open = history.data?.items.find((c) => OPEN_STATES.includes(c.state))
        if (open && !current) setCurrent(open)
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [history.data])

    const live = !!current && OPEN_STATES.includes(current.state)
    useEffect(() => {
        if (!live || !current) return
        const id = current.id
        const timer = window.setInterval(async () => {
            try {
                const c = await aiTestCallsApi.get(id)
                setCurrent(c)
                if (!OPEN_STATES.includes(c.state)) history.reload()
            } catch {
                /* the next tick retries */
            }
        }, POLL_MS)
        const tick = window.setInterval(() => setNow(Date.now()), 500)
        return () => {
            window.clearInterval(timer)
            window.clearInterval(tick)
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [live, current?.id])

    const numberOptions: Option[] = useMemo(
        () => [
            { value: '', label: String(t('p13.aitest.autoNumber')) },
            ...(o?.outbound_numbers || []).map((n) => ({ value: n.number, label: n.number })),
        ],
        [o, t]
    )
    const voiceOptions: Option[] = (o?.voices || []).map((v) => ({ value: v, label: v }))
    const canStart = !!o?.ready && !!o?.service.running && !live && destination.trim().length >= 9 && script.trim().length > 0

    const start = async () => {
        setBusy(true)
        setError(null)
        try {
            const c = await aiTestCallsApi.create({ destination, caller_id: callerId || null, voice, instructions: script })
            setCurrent(c)
            setViewing(null)
            history.reload()
        } catch (e) {
            setError(e)
        } finally {
            setBusy(false)
        }
    }
    const stop = async () => {
        if (!current) return
        try {
            setCurrent(await aiTestCallsApi.stop(current.id))
        } catch (e) {
            setError(e)
        }
    }

    const shown = viewing || current
    const talk = shown?.answered_at
        ? Math.max(0, ((shown.ended_at ? Date.parse(shown.ended_at) : now) - Date.parse(shown.answered_at)) / 1000)
        : null

    return (
        <div data-testid="p13-aitest" className="flex flex-col gap-4">
            <PageHead title={t('p13.aitest.title')} subtitle={t('p13.aitest.subtitle')} onRefresh={() => { opts.reload(); history.reload() }} refreshing={opts.loading} />

            {opts.error && !o ? (
                <ErrorState error={opts.error} onRetry={opts.reload} describe={describe} />
            ) : !o ? (
                <SkeletonLines rows={6} />
            ) : (
                <>
                    {!o.ready && (
                        <Notice tone="warning" title={t('p13.aitest.notReady')} testId="p13-aitest-not-ready">
                            <ul className="list-disc ps-5">
                                {o.not_ready_reasons.map((r) => (
                                    <li key={r}>{t(`p13.aitest.reason.${r}`, { defaultValue: r })}</li>
                                ))}
                            </ul>
                        </Notice>
                    )}
                    {o.ready && !o.service.running && <Notice tone="warning" testId="p13-aitest-service">{t('p13.aitest.serviceDown')}</Notice>}
                    <Notice tone="info">
                        {o.limits.per_day
                            ? t('p13.aitest.disclaimer', { secs: o.limits.max_seconds, n: o.limits.per_day })
                            : t('p13.aitest.disclaimerNoDaily', { secs: o.limits.max_seconds })}
                    </Notice>

                    <div className="grid gap-4 xl:grid-cols-2">
                        <Card title={t('p13.aitest.setup')} testId="p13-aitest-form">
                            <div className="grid grid-cols-1 gap-x-3 sm:grid-cols-2">
                                <DialogField label={t('p13.aitest.number')} required hint={t('p13.aitest.numberHint')}>
                                    <Input size="sm" dir="ltr" inputMode="tel" value={destination} disabled={live} placeholder="05XXXXXXXX"
                                        onChange={(e) => setDestination(e.target.value.replace(/[^0-9+]/g, '').slice(0, 14))} data-testid="p13-aitest-number" />
                                </DialogField>
                                <DialogField label={t('p13.aitest.callerId')}>
                                    <Select size="sm" isSearchable isDisabled={live} options={numberOptions}
                                        value={numberOptions.find((x) => x.value === callerId) || numberOptions[0]}
                                        onChange={(x) => setCallerId((x as Option | null)?.value || '')} />
                                </DialogField>
                                <DialogField label={t('p13.aitest.voice')}>
                                    <Select size="sm" isDisabled={live} options={voiceOptions}
                                        value={voiceOptions.find((x) => x.value === voice) || null}
                                        onChange={(x) => setVoice((x as Option | null)?.value || o.default_voice)} />
                                </DialogField>
                            </div>
                            <DialogField label={t('p13.aitest.script')} required hint={t('p13.aitest.scriptHint')}>
                                <textarea
                                    rows={9}
                                    value={script}
                                    disabled={live}
                                    onChange={(e) => setScript(e.target.value.slice(0, 4000))}
                                    className="w-full rounded-md border border-gray-300 bg-white p-2 text-[13.5px] leading-6 focus:border-navy-500 focus:outline-none dark:border-night-border dark:bg-night-card dark:text-gray-100"
                                    data-testid="p13-aitest-script"
                                />
                            </DialogField>
                            <div className="flex flex-wrap items-center gap-3">
                                <Button variant="solid" size="sm" loading={busy} disabled={!canStart || busy} onClick={start} data-testid="p13-aitest-start">
                                    {t('p13.aitest.start')}
                                </Button>
                                <button type="button" className="text-[12.5px] font-extrabold text-navy-600 underline dark:text-gray-100" disabled={live} onClick={() => setScript(o.default_instructions)}>
                                    {t('p13.aitest.resetScript')}
                                </button>
                            </div>
                            {error ? <Notice tone="danger" className="mt-3">{describe(error, t)}</Notice> : null}
                        </Card>

                        <Card title={viewing ? t('p13.aitest.pastCall') : t('p13.aitest.liveCall')} testId="p13-aitest-call"
                            actions={live && !viewing ? (
                                <Button size="sm" variant="solid" color="red-600" onClick={stop} disabled={current?.stop_requested} data-testid="p13-aitest-stop">
                                    {t('p13.aitest.stop')}
                                </Button>
                            ) : viewing ? (
                                <button type="button" className="text-[12.5px] font-extrabold text-navy-600 underline dark:text-gray-100" onClick={() => setViewing(null)}>
                                    {t('p13.aitest.backToLive')}
                                </button>
                            ) : null}>
                            {shown ? (
                                <>
                                    <div className="mb-4 grid grid-cols-2 gap-4 sm:grid-cols-4">
                                        <Fact label={t('p13.aitest.number')}><Ltr>{shown.destination}</Ltr></Fact>
                                        <Fact label={t('p13.aitest.status')}><StateBadge call={shown} t={t} /></Fact>
                                        <Fact label={t('p13.aitest.talk')}><Ltr className="tabular-nums">{talk === null ? '—' : clock(talk)}</Ltr></Fact>
                                        <Fact label={t('p13.aitest.callerId')}><Ltr>{shown.caller_id || t('p13.aitest.autoNumber')}</Ltr></Fact>
                                    </div>
                                    {shown.reason_code && !OPEN_STATES.includes(shown.state) && (
                                        <div className="mb-3 text-[12px] text-gray-600">{t('p13.aitest.reasonLine', { code: t(`p13.aitest.code.${shown.reason_code}`, { defaultValue: shown.reason_code }), sip: shown.sip_status ?? '—' })}</div>
                                    )}
                                    <Transcript call={shown} t={t} />
                                </>
                            ) : (
                                <EmptyState title={t('p13.aitest.noCall')} />
                            )}
                        </Card>
                    </div>

                    <Card title={t('p13.aitest.history')} testId="p13-aitest-history">
                        {history.data && history.data.items.length ? (
                            <TableFrame>
                                <Table compact>
                                    <THead>
                                        <Tr>
                                            <Th>{t('p13.aitest.when')}</Th>
                                            <Th>{t('p13.aitest.number')}</Th>
                                            <Th>{t('p13.aitest.status')}</Th>
                                            <Th>{t('p13.aitest.talk')}</Th>
                                            <Th>{t('p13.aitest.turns')}</Th>
                                        </Tr>
                                    </THead>
                                    <TBody>
                                        {history.data.items.map((c) => (
                                            <Tr key={c.id} className={rowClass(viewing?.id === c.id)} onClick={() => setViewing(c)}>
                                                <Td><RiyadhTime iso={c.created_at} /></Td>
                                                <Td><Ltr>{c.destination}</Ltr></Td>
                                                <Td><StateBadge call={c} t={t} /></Td>
                                                <Td><Ltr className="tabular-nums">{c.talk_seconds === null ? '—' : clock(c.talk_seconds)}</Ltr></Td>
                                                <Td className="tabular-nums">{c.transcript.length}</Td>
                                            </Tr>
                                        ))}
                                    </TBody>
                                </Table>
                            </TableFrame>
                        ) : (
                            <EmptyState title={t('p13.aitest.noHistory')} />
                        )}
                    </Card>
                </>
            )}
        </div>
    )
}

export default AiTestCallPage
