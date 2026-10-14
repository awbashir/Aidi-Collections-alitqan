/**
 * Call monitor (`/call-monitor`, derived key `call_monitor` = reports_calls | calls_manage).
 *
 * - Live calls (`GET /v2/calling/monitor/live`, polled every 3 s): the calling service's
 *   channel snapshot grouped into calls, and each extension's state. A stale or failed
 *   snapshot is shown as "unknown", never as "no calls".
 * - Calls by user over time (`GET /v2/calling/monitor/stats`): the PBX call records per agent
 *   extension, by hour (one day) or by day, with a chart per user and a chart over time.
 *
 * The API decides the scope: SuperAdmin every extension, a supervisor their team's, anyone
 * else their own. The user filter (one extension) narrows both sections; its choices are the
 * scope's own extensions (`options`), and the API refuses any other (403 not_in_scope).
 * Extensions are attributed to their current owner.
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import classNames from 'classnames'
import Input from '@/components/ui/Input'
import Select from '@/components/ui/Select'
import Table from '@/components/ui/Table'
import Chart from '@/components/shared/Chart'
import { Ltr } from '@/components/v2/Display'
import { EmptyState, ErrorState, SkeletonLines, SkeletonRows } from '@/components/v2/States'
import { PageHead, TableFrame } from '@/components/v2/ListParts'
import { Segmented } from '@/views/phase1/followUps/parts'
import { callMonitorApi, describeP5Error } from '@/services/v2'
import type { MonitorAgent, MonitorLive, MonitorStats, Option, Translate } from '@/services/v2'
import { useV2Query } from '@/utils/v2/hooks'
import { formatCount } from '@/utils/v2/format'
import { Card, Fact, Notice } from '../parts'

const { THead, TBody, Tr, Th, Td } = Table
const POLL_MS = 3000
const TOP_USERS = 8
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

function useLive(ext: string) {
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
                const res = await callMonitorApi.live({ extension: ext || undefined }, ctrl.current.signal)
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
    }, [ext])
    const elapsed = fetchedAt ? (Date.now() - fetchedAt) / 1000 : 0
    return { data, error, elapsed }
}

const STATE_TONE: Record<string, string> = {
    talking: 'bg-success-tint text-success',
    ringing: 'bg-warning-tint text-warning',
    ivr: 'bg-azure-50 text-azure-700 dark:bg-night-page dark:text-azure-300',
    connecting: 'bg-gray-100 text-gray-700 dark:bg-night-page dark:text-night-body',
}

const LiveSection = ({ ext, onOptions }: { ext: string; onOptions: (o: MonitorAgent[]) => void }) => {
    const { t } = useTranslation()
    const { data, error, elapsed } = useLive(ext)
    useEffect(() => {
        if (data) onOptions(data.options)
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [data?.options.length])
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
                                </Tr>
                            ))
                        ) : (
                            <tr>
                                <td colSpan={5}>
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

const StatsSection = ({ ext }: { ext: string }) => {
    const { t } = useTranslation()
    const [from, setFrom] = useState(riyadhToday())
    const [to, setTo] = useState(riyadhToday())
    const [gran, setGran] = useState<'hour' | 'day'>('hour')
    const q = useV2Query(
        (signal) => callMonitorApi.stats({ from, to, granularity: gran, extension: ext || undefined }, signal),
        [from, to, gran, ext]
    )
    const data: MonitorStats | undefined = q.data
    const byUser = useMemo(() => {
        const users = (data?.users || []).slice(0, 20)
        return {
            categories: users.map(agentLabel),
            series: [
                { name: t('p5.monitor.outAnswered') as string, data: users.map((u) => u.outbound_answered) },
                { name: t('p5.monitor.outUnanswered') as string, data: users.map((u) => u.outbound - u.outbound_answered) },
                { name: t('p5.monitor.inAnswered') as string, data: users.map((u) => u.inbound_answered) },
                { name: t('p5.monitor.internal') as string, data: users.map((u) => u.internal) },
            ],
        }
    }, [data, t])
    const overTime = useMemo(() => {
        const users = (data?.users || []).slice(0, TOP_USERS)
        const top = new Set(users.map((u) => u.extension))
        const series = (data?.series || [])
        const labels = series.map((s) => (data?.granularity === 'hour' ? s.bucket.slice(11, 16) : s.bucket.slice(5)))
        const lines = users.map((u) => ({ name: agentLabel(u), data: series.map((s) => s.by_extension[u.extension] || 0) }))
        const others = series.map((s) => Object.entries(s.by_extension).reduce((n, [e, v]) => (top.has(e) ? n : n + v), 0))
        if (others.some((v) => v > 0)) lines.push({ name: t('p5.monitor.others') as string, data: others })
        return { labels, lines }
    }, [data, t])
    const totals = data?.totals
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
            </div>
            {q.error && !data ? (
                <ErrorState error={q.error} onRetry={q.reload} describe={describe} />
            ) : !data || !totals ? (
                <SkeletonLines rows={6} />
            ) : (
                <>
                    {data.truncated && <Notice tone="warning">{t('p5.monitor.truncated')}</Notice>}
                    <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
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
                    </div>
                    <div className="grid gap-4 xl:grid-cols-2">
                        <Card title={t('p5.monitor.byUserTitle')} testId="p5-monitor-chart-users">
                            {byUser.categories.length ? (
                                <Chart
                                    type="bar"
                                    height={Math.max(260, byUser.categories.length * 34)}
                                    series={byUser.series}
                                    xAxis={byUser.categories}
                                    customOptions={{ chart: { stacked: true }, plotOptions: { bar: { horizontal: true, borderRadius: 4 } }, legend: { position: 'top' } }}
                                />
                            ) : (
                                <EmptyState title={t('p5.monitor.noStats')} />
                            )}
                        </Card>
                        <Card title={t(data.granularity === 'hour' ? 'p5.monitor.overTimeHour' : 'p5.monitor.overTimeDay')} testId="p5-monitor-chart-time">
                            {overTime.lines.length ? (
                                <Chart
                                    type="bar"
                                    height={320}
                                    series={overTime.lines}
                                    xAxis={overTime.labels}
                                    customOptions={{ chart: { stacked: true }, plotOptions: { bar: { borderRadius: 3, columnWidth: '60%' } }, legend: { position: 'top' } }}
                                />
                            ) : (
                                <EmptyState title={t('p5.monitor.noStats')} />
                            )}
                        </Card>
                    </div>
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
                                </Tr>
                            </THead>
                            <TBody>
                                {data.users.length ? (
                                    data.users.map((u) => (
                                        <Tr key={u.extension}>
                                            <Td dir="auto">{agentLabel(u)}</Td>
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
                                        </Tr>
                                    ))
                                ) : q.loading ? (
                                    <SkeletonRows cols={8} rows={3} />
                                ) : (
                                    <tr>
                                        <td colSpan={8}>
                                            <EmptyState title={t('p5.monitor.noStats')} />
                                        </td>
                                    </tr>
                                )}
                            </TBody>
                        </Table>
                    </TableFrame>
                    <p className="text-[12px] text-gray-600">{t('p5.monitor.statsNote')}</p>
                </>
            )}
        </div>
    )
}

/* ------------------------------------------------------------------ page */

const CallMonitor = () => {
    const { t } = useTranslation()
    const [ext, setExt] = useState('')
    const [agents, setAgents] = useState<MonitorAgent[]>([])
    const userOptions: Option[] = [{ value: '', label: t('p5.monitor.allUsers') }, ...agents.map((a) => ({ value: a.extension, label: agentLabel(a) }))]
    return (
        <div data-testid="p5-monitor" className="flex flex-col gap-4">
            <PageHead title={t('p5.monitor.title')} subtitle={t('p5.monitor.subtitle')} />
            <div className="flex flex-wrap items-end gap-3" data-testid="p5-monitor-user-filter">
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
                {ext && (
                    <button type="button" className="mb-1 text-[12.5px] font-extrabold text-navy-600 underline dark:text-gray-100" onClick={() => setExt('')}>
                        {t('p5.monitor.clearUser')}
                    </button>
                )}
            </div>
            <Card lead title={t('p5.monitor.liveTitle')}>
                <LiveSection ext={ext} onOptions={setAgents} />
            </Card>
            <Card title={t('p5.monitor.statsTitle')}>
                <StatsSection ext={ext} />
            </Card>
        </div>
    )
}

export default CallMonitor
