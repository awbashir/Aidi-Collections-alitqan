/**
 * Call monitor (`/call-monitor`, derived key `call_monitor` = reports_calls | calls_manage).
 *
 * - Live calls (`GET /v2/calling/monitor/live`, polled every 3 s): the calling service's
 *   channel snapshot grouped into calls, and each extension's state. A stale or failed
 *   snapshot is shown as "unknown", never as "no calls".
 * - Calls by user over time (`GET /v2/calling/monitor/stats`): the PBX call records per agent
 *   extension, by hour (one day) or by day. Charts (2026-10-20, ./charts): calls over time by
 *   outcome / direction / user (zoom; a day opens by hour), calls by user, answer rate, talk-time
 *   distribution, average ring / talk time, busy hours; a user's bar opens their calls. They follow
 *   the number search too (the stats with `number`); the tiles and the table stay every call.
 * - Downloads (XLSX, same period and user filter): the per-user summary and the call detail
 *   (one row per call: time, number, client, line, result, ring/talk time, recorded).
 * - Talk-time counts (2026-10-17): answered calls longer than 1 / 2 / 3 minutes (strictly), per
 *   user and in the totals. A user's row opens their calls of the period (UserCalls.tsx), where
 *   a holder of the recording keys can play and download the recordings.
 * - Search by the client's number (2026-10-19): the period's calls with that number across the
 *   scope (NumberCalls.tsx) and in a user's drawer; the call-detail download follows it.
 *
 * The API decides the scope: SuperAdmin every extension, a supervisor their team's, anyone
 * else their own. The user filter (one extension) narrows both sections; its choices are the
 * scope's own extensions (`options`), and the API refuses any other (403 not_in_scope).
 * The contract filter (2026-10-21) narrows both sections to the users of one contract
 * (`users.contract_id`); its choices are the contracts of the scope's users (`contracts`), and
 * the user filter then offers that contract's users only.
 * Extensions are attributed to their current owner.
 */
import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import classNames from 'classnames'
import Button from '@/components/ui/Button'
import Input from '@/components/ui/Input'
import Select from '@/components/ui/Select'
import Table from '@/components/ui/Table'
import { Ltr } from '@/components/v2/Display'
import { EmptyState, ErrorState, SkeletonLines, SkeletonRows } from '@/components/v2/States'
import { PageHead, TableFrame, rowClass } from '@/components/v2/ListParts'
import { Segmented } from '@/views/phase1/followUps/parts'
import { callMonitorApi, describeP5Error } from '@/services/v2'
import type { MonitorAgent, MonitorContract, MonitorLive, MonitorStats, Option, Translate } from '@/services/v2'
import { useV2Query } from '@/utils/v2/hooks'
import { formatCount } from '@/utils/v2/format'
import { Card, Fact, Notice } from '../parts'
import NumberSearch from '../calls/NumberSearch'
import NumberCalls from './NumberCalls'
import UserCalls from './UserCalls'
import { ListenButton, ListenPanel } from './LiveControls'
import { useListen } from './useListen'
import MonitorCharts from './charts'

const { THead, TBody, Tr, Th, Td } = Table
const POLL_MS = 3000
const describe = (e: unknown, t: Translate) => describeP5Error(e, t)

const clock = (seconds: number) => {
    const s = Math.max(0, Math.floor(seconds))
    const h = Math.floor(s / 3600)
    const m = Math.floor((s % 3600) / 60)
    const r = s % 60
    return `${h ? `${h}:` : ''}${String(m).padStart(h ? 2 : 1, '0')}:${String(r).padStart(2, '0')}`
}

const riyadhToday = () => new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Riyadh' }).format(new Date())

const agentLabel = (a: { name: string | null; extension: string }) => (a.name ? `${a.name} (${a.extension})` : a.extension)

/* ------------------------------------------------------------------ live */

function useLive(ext: string, contract: string) {
    const [data, setData] = useState<MonitorLive | null>(null)
    const [error, setError] = useState<unknown>(null)
    const [fetchedAt, setFetchedAt] = useState(0)
    const [, setTick] = useState(0)
    const timer = useRef<number | null>(null)
    useEffect(() => {
        let alive = true
        const ctrl = { current: null as AbortController | null }
        const load = async () => {
            ctrl.current?.abort()
            ctrl.current = new AbortController()
            try {
                const res = await callMonitorApi.live({ extension: ext || undefined, contract: contract || undefined }, ctrl.current.signal)
                if (!alive) return
                setData(res)
                setError(null)
                setFetchedAt(Date.now())
            } catch (e) {
                if (alive && !(e instanceof DOMException && e.name === 'AbortError')) setError(e)
            }
        }
        void load()
        timer.current = window.setInterval(() => {
            if (document.visibilityState === 'visible') void load()
        }, POLL_MS)
        const ticker = window.setInterval(() => setTick((n) => n + 1), 1000)
        return () => {
            alive = false
            ctrl.current?.abort()
            if (timer.current) window.clearInterval(timer.current)
            window.clearInterval(ticker)
        }
    }, [ext, contract])
    const elapsed = fetchedAt ? (Date.now() - fetchedAt) / 1000 : 0
    return { data, error, elapsed }
}

const STATE_TONE: Record<string, string> = {
    talking: 'bg-success-tint text-success',
    ringing: 'bg-warning-tint text-warning',
    ivr: 'bg-azure-50 text-azure-700 dark:bg-night-page dark:text-azure-300',
    connecting: 'bg-gray-100 text-gray-700 dark:bg-night-page dark:text-night-body',
}

const LiveSection = ({ ext, contract, onOptions, onContracts }: { ext: string; contract: string; onOptions: (o: MonitorAgent[]) => void; onContracts: (c: MonitorContract[]) => void }) => {
    const { t } = useTranslation()
    const { data, error, elapsed } = useLive(ext, contract)
    const listen = useListen()
    const canListen = !!listen.caps?.listen
    useEffect(() => {
        if (data) onOptions(data.options)
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [data?.options.length, data?.contract])
    useEffect(() => {
        if (data) onContracts(data.contracts || [])
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [data?.contracts?.length])
    if (error && !data) return <ErrorState error={error} onRetry={() => window.location.reload()} describe={describe} />
    if (!data) return <SkeletonLines rows={5} />
    const stale = data.snapshot.stale
    return (
        <div className="flex flex-col gap-4" data-testid="p5-monitor-live" data-stale={stale ? '1' : '0'}>
            {stale && (
                <Notice tone="warning" title={t('p5.monitor.staleTitle')}>
                    {t('p5.monitor.staleHint', { age: data.snapshot.age_seconds === null ? '—' : Math.round(data.snapshot.age_seconds) })}
                </Notice>
            )}
            <ListenPanel listen={listen} />
            <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
                <Fact label={t('p5.monitor.activeCalls')}>
                    <Ltr>{stale ? '—' : formatCount(data.summary.active_calls)}</Ltr>
                </Fact>
                <Fact label={t('p5.monitor.talking')}>
                    <Ltr>{stale ? '—' : formatCount(data.summary.talking)}</Ltr>
                </Fact>
                <Fact label={t('p5.monitor.ringing')}>
                    <Ltr>{stale ? '—' : formatCount(data.summary.ringing)}</Ltr>
                </Fact>
                <Fact label={t('p5.monitor.inIvr')}>
                    <Ltr>{stale ? '—' : formatCount(data.summary.in_ivr)}</Ltr>
                </Fact>
                <Fact label={t('p5.monitor.agentsOnCall')}>
                    <Ltr>{stale ? '—' : `${formatCount(data.summary.agents_on_call)} / ${formatCount(data.summary.agents)}`}</Ltr>
                </Fact>
            </div>
            <TableFrame>
                <Table data-testid="p5-monitor-calls">
                    <THead>
                        <Tr>
                            <Th>{t('p5.monitor.state')}</Th>
                            <Th>{t('p5.monitor.direction')}</Th>
                            <Th>{t('p5.monitor.agent')}</Th>
                            <Th>{t('p5.monitor.number')}</Th>
                            <Th>{t('p5.monitor.duration')}</Th>
                            {canListen && <Th>{t('p10.callctl.monitor.actions')}</Th>}
                        </Tr>
                    </THead>
                    <TBody>
                        {data.calls.length && !stale ? (
                            data.calls.map((c) => (
                                <Tr key={c.id} data-testid="p5-monitor-call" data-state={c.state}>
                                    <Td>
                                        <span className={classNames('rounded-full px-2.5 py-1 text-[12px] font-extrabold', STATE_TONE[c.state])}>{t(`p5.monitor.stateValue.${c.state}`)}</span>
                                    </Td>
                                    <Td>{t(`p5.monitor.directionValue.${c.direction}`)}</Td>
                                    <Td className="text-[12.5px]" dir="auto">
                                        {c.agents.length ? c.agents.map(agentLabel).join('، ') : c.ringing.length ? t('p5.monitor.ringingAt', { exts: c.ringing.join('، ') }) : '—'}
                                    </Td>
                                    <Td>
                                        <Ltr>{c.external_number || '—'}</Ltr>
                                    </Td>
                                    <Td className="tabular-nums">
                                        <Ltr>{clock(c.duration_seconds + elapsed)}</Ltr>
                                    </Td>
                                    {canListen && (
                                        <Td>
                                            <ListenButton listen={listen} call={c} />
                                        </Td>
                                    )}
                                </Tr>
                            ))
                        ) : (
                            <tr>
                                <td colSpan={canListen ? 6 : 5}>
                                    <EmptyState title={stale ? t('p5.monitor.unknown') : t('p5.monitor.noCalls')} />
                                </td>
                            </tr>
                        )}
                    </TBody>
                </Table>
            </TableFrame>
            <div>
                <div className="text-eyebrow mb-2">{t('p5.monitor.agentsTitle')}</div>
                <div className="flex flex-wrap gap-2" data-testid="p5-monitor-agents">
                    {data.agents.map((a) => (
                        <span
                            key={a.extension}
                            data-status={a.status}
                            className={classNames(
                                'inline-flex items-center gap-2 rounded-full border px-3 py-1 text-[12.5px]',
                                a.status === 'on_call' && 'border-success bg-success-tint text-success',
                                a.status === 'ringing' && 'border-warning bg-warning-tint text-warning',
                                a.status === 'free' && 'border-gray-200 text-gray-700 dark:border-night-border dark:text-night-body'
                            )}
                        >
                            <span className="h-2 w-2 rounded-full bg-current" aria-hidden />
                            <span dir="auto">{agentLabel(a)}</span>
                            <span className="font-extrabold">{stale ? '?' : t(`p5.monitor.agentStatus.${a.status}`)}</span>
                        </span>
                    ))}
                    {!data.agents.length && <span className="text-[12.5px] text-gray-600">{t('p5.monitor.noAgents')}</span>}
                </div>
            </div>
        </div>
    )
}

/* ------------------------------------------------------------------ stats */

const StatsSection = ({ ext, contract }: { ext: string; contract: string }) => {
    const { t } = useTranslation()
    const [from, setFrom] = useState(riyadhToday())
    const [to, setTo] = useState(riyadhToday())
    const [gran, setGran] = useState<'hour' | 'day'>('hour')
    const [number, setNumber] = useState<string | undefined>(undefined)
    const q = useV2Query(
        (signal) => callMonitorApi.stats({ from, to, granularity: gran, extension: ext || undefined, contract: contract || undefined }, signal),
        [from, to, gran, ext, contract]
    )
    const data: MonitorStats | undefined = q.data
    /* The charts follow the number search too (2026-10-20): the same statistics for that number's calls. */
    const nq = useV2Query(
        number ? (signal) => callMonitorApi.stats({ from, to, granularity: gran, extension: ext || undefined, contract: contract || undefined, number }, signal) : null,
        [from, to, gran, ext, contract, number]
    )
    const chartData: MonitorStats | undefined = number ? (nq.loading ? undefined : nq.data) : data
    const drillDay = (day: string) => {
        setFrom(day)
        setTo(day)
        setGran('hour')
    }
    const totals = data?.totals
    const [opened, setOpened] = useState<MonitorAgent | null>(null)
    const [downloading, setDownloading] = useState<'summary' | 'calls' | null>(null)
    const [downloadError, setDownloadError] = useState<unknown>(null)
    const download = async (kind: 'summary' | 'calls') => {
        setDownloading(kind)
        setDownloadError(null)
        try {
            await callMonitorApi.download(kind, { from, to, extension: ext || undefined, contract: contract || undefined, number: kind === 'calls' ? number : undefined })
        } catch (e) {
            setDownloadError(e)
        } finally {
            setDownloading(null)
        }
    }
    return (
        <div className="flex flex-col gap-4" data-testid="p5-monitor-stats">
            <div className="flex flex-wrap items-end gap-3">
                <label className="flex flex-col gap-1 text-[12.5px]">
                    <span className="text-gray-600">{t('p5.monitor.from')}</span>
                    <Input
                        size="sm"
                        type="date"
                        value={from}
                        onChange={(e) => {
                            setFrom(e.target.value)
                            if (e.target.value > to) setTo(e.target.value)
                        }}
                        className="w-[160px]"
                    />
                </label>
                <label className="flex flex-col gap-1 text-[12.5px]">
                    <span className="text-gray-600">{t('p5.monitor.to')}</span>
                    <Input size="sm" type="date" value={to} min={from} onChange={(e) => setTo(e.target.value)} className="w-[160px]" />
                </label>
                <Segmented<'hour' | 'day'>
                    value={gran}
                    onChange={setGran}
                    label={t('p5.monitor.groupBy') as string}
                    options={[
                        { value: 'hour', label: t('p5.monitor.byHour') },
                        { value: 'day', label: t('p5.monitor.byDay') },
                    ]}
                />
                <NumberSearch value={number} onChange={setNumber} testId="p9-monitor-number" />
                <div className="ms-auto flex flex-wrap gap-2">
                    <Button
                        size="sm"
                        loading={downloading === 'summary'}
                        disabled={downloading !== null}
                        onClick={() => download('summary')}
                        data-testid="p5-monitor-download-summary"
                    >
                        {t('p5.monitor.downloadSummary')}
                    </Button>
                    <Button
                        size="sm"
                        variant="solid"
                        loading={downloading === 'calls'}
                        disabled={downloading !== null}
                        onClick={() => download('calls')}
                        data-testid="p5-monitor-download-calls"
                    >
                        {t('p5.monitor.downloadCalls')}
                    </Button>
                </div>
            </div>
            {downloadError ? <Notice tone="danger">{describe(downloadError, t)}</Notice> : null}
            {number && <NumberCalls from={from} to={to} ext={ext} contract={contract} number={number} />}
            {q.error && !data ? (
                <ErrorState error={q.error} onRetry={q.reload} describe={describe} />
            ) : !data || !totals ? (
                <SkeletonLines rows={6} />
            ) : (
                <>
                    {data.truncated && <Notice tone="warning">{t('p5.monitor.truncated')}</Notice>}
                    <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
                        <Fact label={t('p5.monitor.handled')}>
                            <Ltr>{formatCount(totals.handled)}</Ltr>
                        </Fact>
                        <Fact label={t('p5.monitor.outbound')}>
                            <Ltr>{`${formatCount(totals.outbound)} (${formatCount(totals.outbound_answered)})`}</Ltr>
                        </Fact>
                        <Fact label={t('p5.monitor.inAnswered')}>
                            <Ltr>{formatCount(totals.inbound_answered)}</Ltr>
                        </Fact>
                        <Fact label={t('p5.monitor.inMissed')}>
                            <Ltr>{formatCount(totals.inbound_missed)}</Ltr>
                        </Fact>
                        <Fact label={t('p5.monitor.talkTime')}>
                            <Ltr>{clock(totals.talk_seconds)}</Ltr>
                        </Fact>
                        <Fact label={t('p8.monitor.overTile')} testId="p8-monitor-talk-over">
                            <Ltr>{`${formatCount(totals.talk_over_60)} / ${formatCount(totals.talk_over_120)} / ${formatCount(totals.talk_over_180)}`}</Ltr>
                        </Fact>
                    </div>
                    {number && nq.error ? (
                        <ErrorState error={nq.error} onRetry={nq.reload} describe={describe} />
                    ) : chartData ? (
                        <>
                            {number && <Notice tone="info">{t('p10.moncharts.byNumber', { number: chartData.number || number })}</Notice>}
                            <MonitorCharts data={chartData} onOpenUser={setOpened} onDrillDay={drillDay} />
                        </>
                    ) : (
                        <SkeletonLines rows={8} />
                    )}
                    <TableFrame>
                        <Table data-testid="p5-monitor-users">
                            <THead>
                                <Tr>
                                    <Th>{t('p5.monitor.agent')}</Th>
                                    <Th>{t('p5.monitor.handled')}</Th>
                                    <Th>{t('p5.monitor.outbound')}</Th>
                                    <Th>{t('p5.monitor.outAnswered')}</Th>
                                    <Th>{t('p5.monitor.inAnswered')}</Th>
                                    <Th>{t('p5.monitor.inMissed')}</Th>
                                    <Th>{t('p5.monitor.internal')}</Th>
                                    <Th>{t('p5.monitor.talkTime')}</Th>
                                    <Th title={t('p8.monitor.overHint') as string}>{t('p8.monitor.over60')}</Th>
                                    <Th title={t('p8.monitor.overHint') as string}>{t('p8.monitor.over120')}</Th>
                                    <Th title={t('p8.monitor.overHint') as string}>{t('p8.monitor.over180')}</Th>
                                </Tr>
                            </THead>
                            <TBody>
                                {data.users.length ? (
                                    data.users.map((u) => (
                                        <Tr
                                            key={u.extension}
                                            className={rowClass(opened?.extension === u.extension)}
                                            onClick={() => setOpened(u)}
                                            data-testid="p8-monitor-user-row"
                                        >
                                            <Td dir="auto">
                                                <button
                                                    type="button"
                                                    className="text-start font-extrabold text-azure-600 hover:underline"
                                                    title={t('p8.monitor.openCalls') as string}
                                                    onClick={(e) => {
                                                        e.stopPropagation()
                                                        setOpened(u)
                                                    }}
                                                >
                                                    {agentLabel(u)}
                                                </button>
                                            </Td>
                                            <Td className="font-extrabold">
                                                <Ltr>{formatCount(u.handled)}</Ltr>
                                            </Td>
                                            <Td>
                                                <Ltr>{formatCount(u.outbound)}</Ltr>
                                            </Td>
                                            <Td>
                                                <Ltr>{formatCount(u.outbound_answered)}</Ltr>
                                            </Td>
                                            <Td>
                                                <Ltr>{formatCount(u.inbound_answered)}</Ltr>
                                            </Td>
                                            <Td>
                                                <Ltr>{formatCount(u.inbound_missed)}</Ltr>
                                            </Td>
                                            <Td>
                                                <Ltr>{formatCount(u.internal)}</Ltr>
                                            </Td>
                                            <Td className="tabular-nums">
                                                <Ltr>{clock(u.talk_seconds)}</Ltr>
                                            </Td>
                                            <Td>
                                                <Ltr>{formatCount(u.talk_over_60)}</Ltr>
                                            </Td>
                                            <Td>
                                                <Ltr>{formatCount(u.talk_over_120)}</Ltr>
                                            </Td>
                                            <Td>
                                                <Ltr>{formatCount(u.talk_over_180)}</Ltr>
                                            </Td>
                                        </Tr>
                                    ))
                                ) : q.loading ? (
                                    <SkeletonRows cols={11} rows={3} />
                                ) : (
                                    <tr>
                                        <td colSpan={11}>
                                            <EmptyState title={t('p5.monitor.noStats')} />
                                        </td>
                                    </tr>
                                )}
                            </TBody>
                        </Table>
                    </TableFrame>
                    <p className="text-[12px] text-gray-600">
                        {t('p5.monitor.statsNote')} {t('p8.monitor.overNote')}
                    </p>
                </>
            )}
            <UserCalls agent={opened} from={data?.from || from} to={data?.to || to} onClose={() => setOpened(null)} />
        </div>
    )
}

/* ------------------------------------------------------------------ page */

const CallMonitor = () => {
    const { t } = useTranslation()
    const [ext, setExt] = useState('')
    const [contract, setContract] = useState('')
    const [agents, setAgents] = useState<MonitorAgent[]>([])
    const [contracts, setContracts] = useState<MonitorContract[]>([])
    const userOptions: Option[] = [{ value: '', label: t('p5.monitor.allUsers') }, ...agents.map((a) => ({ value: a.extension, label: agentLabel(a) }))]
    const contractOptions: Option[] = [{ value: '', label: t('p5.monitor.allContracts') }, ...contracts.map((c) => ({ value: c.id, label: `${c.name} (${formatCount(c.users)})` }))]
    return (
        <div data-testid="p5-monitor" className="flex flex-col gap-4">
            <PageHead title={t('p5.monitor.title')} subtitle={t('p5.monitor.subtitle')} />
            <div className="flex flex-wrap items-end gap-3" data-testid="p5-monitor-user-filter">
                {contracts.length > 0 && (
                    <label className="flex w-full max-w-[300px] flex-col gap-1 text-[12.5px]" data-testid="p5-monitor-contract-filter">
                        <span className="text-gray-600">{t('p5.monitor.contract')}</span>
                        <Select
                            size="sm"
                            isSearchable
                            options={contractOptions}
                            value={contractOptions.find((o) => o.value === contract) || contractOptions[0]}
                            onChange={(o) => {
                                setContract((o as Option | null)?.value || '')
                                setExt('')            // the user list follows the contract
                            }}
                        />
                    </label>
                )}
                <label className="flex w-full max-w-[340px] flex-col gap-1 text-[12.5px]">
                    <span className="text-gray-600">{t('p5.monitor.user')}</span>
                    <Select
                        size="sm"
                        isSearchable
                        options={userOptions}
                        value={userOptions.find((o) => o.value === ext) || userOptions[0]}
                        onChange={(o) => setExt((o as Option | null)?.value || '')}
                    />
                </label>
                {(ext || contract) && (
                    <button
                        type="button"
                        className="mb-1 text-[12.5px] font-extrabold text-navy-600 underline dark:text-gray-100"
                        onClick={() => {
                            setExt('')
                            setContract('')
                        }}
                    >
                        {t('p5.monitor.clearUser')}
                    </button>
                )}
            </div>
            <Card lead title={t('p5.monitor.liveTitle')}>
                <LiveSection ext={ext} contract={contract} onOptions={setAgents} onContracts={setContracts} />
            </Card>
            <Card title={t('p5.monitor.statsTitle')}>
                <StatsSection ext={ext} contract={contract} />
            </Card>
        </div>
    )
}

export default CallMonitor
