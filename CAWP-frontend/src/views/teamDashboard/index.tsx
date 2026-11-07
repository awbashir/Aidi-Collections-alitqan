/**
 * Team performance dashboard ("لوحة أداء الفريق", `/team-dashboard`, derived key `team_dashboard`
 * = calls_supervise | calls_manage; customer request 2026-10-20).
 *
 * One query (`GET /v2/dashboards/team`) feeds the whole page: KPI tiles, calls per user (the call
 * monitor's classification), client status updates per user by main status or sub-status, status
 * updates over time, the team's status distribution and a sortable per-user table. Clicking a
 * user (chart bar or table row) filters the page to that user. The download is the same query as
 * XLSX (audited by the API).
 *
 * The API decides the scope: SuperAdmin every user, a supervisor their team and only notes on
 * cases in their scope. Status updates exclude deleted notes and statuses imported from the old
 * system. Call figures are "—" when the PBX call records are unavailable (unknown, not zero), and
 * the contract filter never narrows calls (a PBX record has no contract).
 */
import { useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import Button from '@/components/ui/Button'
import Input from '@/components/ui/Input'
import Select from '@/components/ui/Select'
import Table from '@/components/ui/Table'
import Chart from '@/components/shared/Chart'
import { useConfig } from '@/components/ui/ConfigProvider'
import { COLORS, COLOR_8 } from '@/constants/chart.constant'
import { Ltr } from '@/components/v2/Display'
import { EmptyState, ErrorState, SkeletonLines } from '@/components/v2/States'
import { PageHead, TableFrame, rowClass } from '@/components/v2/ListParts'
import { Segmented } from '@/views/phase1/followUps/parts'
import { Card, Fact, Notice } from '@/views/phase5/parts'
import { describeP5Error } from '@/services/v2'
import type { Option, Translate } from '@/services/v2'
import { teamDashboardApi } from '@/services/v2/teamDashboardApi'
import type { TeamDashboard, TeamGroup, TeamStatus, TeamUserRow } from '@/services/v2/teamDashboardApi'
import { useV2Query } from '@/utils/v2/hooks'
import UserUpdates from './UserUpdates'
import { formatCount, formatMoney, riyadhToday } from '@/utils/v2/format'

const { THead, TBody, Tr, Th, Td } = Table
const TOP_STATUSES = 7 /* the palette has 8 hues: 7 statuses + "Other" */
const TOP_USERS = 20
const MAX_DAYS = 31
const describe = (e: unknown, t: Translate) => describeP5Error(e, t)

type Preset = 'today' | 'week' | 'month' | 'custom'
type SortKey = 'name' | 'calls_made' | 'answered' | 'answer_rate' | 'talk_seconds' | 'notes' | 'clients' | 'promises'

const addDays = (day: string, n: number) => {
    const d = new Date(`${day}T00:00:00Z`)
    d.setUTCDate(d.getUTCDate() + n)
    return d.toISOString().slice(0, 10)
}
const daysBetween = (a: string, b: string) => Math.round((Date.parse(`${b}T00:00:00Z`) - Date.parse(`${a}T00:00:00Z`)) / 86400000)

const presetRange = (p: Preset, today: string): [string, string] | null => {
    if (p === 'today') return [today, today]
    if (p === 'week') return [addDays(today, -6), today]
    if (p === 'month') return [`${today.slice(0, 8)}01`, today]
    return null
}

const clock = (seconds: number | null) => {
    if (seconds === null) return '—'
    const s = Math.max(0, Math.floor(seconds))
    const h = Math.floor(s / 3600)
    const m = Math.floor((s % 3600) / 60)
    return `${h}:${String(m).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`
}

const count = (n: number | null | undefined) => (n === null || n === undefined ? '—' : formatCount(n))
const percent = (r: number | null) => (r === null ? '—' : `${(r * 100).toFixed(1)}%`)

const userLabel = (u: Pick<TeamUserRow, 'name' | 'extensions'>, t: Translate) =>
    u.name || (u.extensions.length ? (t('p10.teamdash.extensionOnly', { ext: u.extensions.join('، ') }) as string) : '—')

const statusLabel = (s: TeamStatus, t: Translate) => {
    const name = s.name || '—'
    if (s.no_sub) return `${name} — ${t('p10.teamdash.noSub')}`
    return s.parent_name ? `${s.parent_name} / ${name}` : name
}

/** The top statuses by count plus "Other" (the palette has eight hues). */
function useStatusSeries(data: TeamDashboard | undefined, t: Translate) {
    return useMemo(() => {
        const legend = data?.statuses || []
        const top = legend.slice(0, TOP_STATUSES)
        const rest = new Set(legend.slice(TOP_STATUSES).map((s) => s.key))
        const pick = (by: Record<string, number>) => [
            ...top.map((s) => by[s.key] || 0),
            ...(rest.size ? [Object.entries(by).reduce((n, [k, v]) => (rest.has(k) ? n + v : n), 0)] : []),
        ]
        const names = [...top.map((s) => statusLabel(s, t)), ...(rest.size ? [t('p10.teamdash.otherStatuses') as string] : [])]
        const colors = [...COLORS.slice(0, top.length), ...(rest.size ? [COLOR_8] : [])]
        return { top, rest, pick, names, colors }
    }, [data, t])
}

const SortTh = ({ k, sort, onSort, children }: { k: SortKey; sort: [SortKey, 1 | -1]; onSort: (k: SortKey) => void; children: ReactNode }) => (
    <Th aria-sort={sort[0] === k ? (sort[1] === 1 ? 'ascending' : 'descending') : 'none'}>
        <button
            type="button"
            className="inline-flex items-center gap-1 font-extrabold hover:underline"
            onClick={() => onSort(k)}
            data-testid={`p10-teamdash-sort-${k}`}
        >
            {children}
            <span aria-hidden className="text-[10px]">{sort[0] === k ? (sort[1] === 1 ? '▲' : '▼') : ''}</span>
        </button>
    </Th>
)

const TeamDashboardPage = () => {
    const { t } = useTranslation()
    const { direction } = useConfig()
    const today = riyadhToday()
    const [preset, setPreset] = useState<Preset>('today')
    const [from, setFrom] = useState(today)
    const [to, setTo] = useState(today)
    const [contract, setContract] = useState('')
    const [users, setUsers] = useState<string[]>([])
    const [group, setGroup] = useState<TeamGroup>('status')
    const [sort, setSort] = useState<[SortKey, 1 | -1]>(['notes', -1])
    const [downloading, setDownloading] = useState(false)
    const [downloadError, setDownloadError] = useState<unknown>(null)

    const tooLong = daysBetween(from, to) >= MAX_DAYS
    const query = { from, to, contract_id: contract || undefined, user_id: users.join(',') || undefined, group }
    const q = useV2Query(tooLong ? null : (signal) => teamDashboardApi.get(query, signal), [from, to, contract, users.join(','), group, tooLong])
    const data = q.data
    const statusSeries = useStatusSeries(data, t)

    const choosePreset = (p: Preset) => {
        setPreset(p)
        const r = presetRange(p, today)
        if (r) {
            setFrom(r[0])
            setTo(r[1])
        }
    }
    const focusUser = (id: string | null) => {
        if (id) setUsers([id])
    }

    const userOptions: Option[] = (data?.options.users || []).map((u) => ({
        value: u.id,
        label: `${u.name || '—'}${u.extensions.length ? ` (${u.extensions.join('، ')})` : ''}${u.is_active ? '' : ` — ${t('p10.teamdash.inactive')}`}`,
    }))
    const contractOptions: Option[] = [
        { value: '', label: t('p10.teamdash.allContracts') as string },
        ...(data?.options.contracts || []).map((c) => ({ value: c.id, label: c.is_active ? c.name : `${c.name} — ${t('p10.teamdash.inactive')}` })),
    ]

    const rows = useMemo(() => {
        const list = [...(data?.users || [])]
        const [k, dir] = sort
        list.sort((a, b) => {
            if (k === 'name') return dir * userLabel(a, t).localeCompare(userLabel(b, t), 'ar')
            const va = (a[k] as number | null) ?? -1
            const vb = (b[k] as number | null) ?? -1
            return dir * (va - vb)
        })
        return list
    }, [data, sort, t])
    const onSort = (k: SortKey) => setSort(([sk, d]) => (sk === k ? [k, d === 1 ? -1 : 1] : [k, k === 'name' ? 1 : -1]))

    const callUsers = useMemo(
        () => (data?.calls.available ? [...data.users].filter((u) => (u.handled || 0) + (u.inbound_missed || 0) > 0).sort((a, b) => (b.handled || 0) - (a.handled || 0)).slice(0, TOP_USERS) : []),
        [data]
    )
    const noteUsers = useMemo(() => [...(data?.users || [])].filter((u) => u.notes > 0).sort((a, b) => b.notes - a.notes).slice(0, TOP_USERS), [data])
    const barEvents = (list: TeamUserRow[]) => ({
        dataPointSelection: (_e: unknown, _c: unknown, opts: { dataPointIndex: number }) => focusUser(list[opts.dataPointIndex]?.user_id ?? null),
    })

    const download = async () => {
        setDownloading(true)
        setDownloadError(null)
        try {
            await teamDashboardApi.download(query)
        } catch (e) {
            setDownloadError(e)
        } finally {
            setDownloading(false)
        }
    }

    const totals = data?.totals
    const callsKnown = !!data?.calls.available
    const subtitle = data ? t(`p10.teamdash.scope.${data.scope}`) : t('p10.teamdash.subtitle')

    return (
        <div data-testid="p10-teamdash" className="flex flex-col gap-4">
            <PageHead
                title={t('p10.teamdash.title')}
                subtitle={subtitle}
                onRefresh={q.reload}
                refreshing={q.loading}
                actions={
                    <Button size="sm" variant="solid" loading={downloading} disabled={downloading || tooLong} onClick={download} data-testid="p10-teamdash-download">
                        {t('p10.teamdash.download')}
                    </Button>
                }
            />

            {/* filters */}
            <div className="flex flex-wrap items-end gap-3" data-testid="p10-teamdash-filters">
                <div className="flex flex-col gap-1 text-[12.5px]">
                    <span className="text-gray-600">{t('p10.teamdash.period')}</span>
                    <Segmented<Preset>
                        value={preset}
                        onChange={choosePreset}
                        label={t('p10.teamdash.period') as string}
                        testId="p10-teamdash-preset"
                        options={[
                            { value: 'today', label: t('p10.teamdash.preset.today') },
                            { value: 'week', label: t('p10.teamdash.preset.week') },
                            { value: 'month', label: t('p10.teamdash.preset.month') },
                            { value: 'custom', label: t('p10.teamdash.preset.custom') },
                        ]}
                    />
                </div>
                {preset === 'custom' && (
                    <>
                        <label className="flex flex-col gap-1 text-[12.5px]">
                            <span className="text-gray-600">{t('p10.teamdash.from')}</span>
                            <Input
                                size="sm"
                                type="date"
                                value={from}
                                max={today}
                                onChange={(e) => {
                                    setFrom(e.target.value)
                                    if (e.target.value > to) setTo(e.target.value)
                                }}
                                className="w-[160px]"
                            />
                        </label>
                        <label className="flex flex-col gap-1 text-[12.5px]">
                            <span className="text-gray-600">{t('p10.teamdash.to')}</span>
                            <Input size="sm" type="date" value={to} min={from} max={today} onChange={(e) => setTo(e.target.value)} className="w-[160px]" />
                        </label>
                    </>
                )}
                <label className="flex w-full max-w-[260px] flex-col gap-1 text-[12.5px]">
                    <span className="text-gray-600">{t('p10.teamdash.contract')}</span>
                    <Select
                        size="sm"
                        isSearchable
                        options={contractOptions}
                        value={contractOptions.find((o) => o.value === contract) || contractOptions[0]}
                        onChange={(o) => setContract((o as Option | null)?.value || '')}
                    />
                </label>
                <label className="flex w-full max-w-[420px] flex-col gap-1 text-[12.5px]" data-testid="p10-teamdash-users">
                    <span className="text-gray-600">{t('p10.teamdash.users')}</span>
                    <Select
                        size="sm"
                        isMulti
                        isSearchable
                        placeholder={t('p10.teamdash.allUsers')}
                        options={userOptions}
                        value={users.map((id) => userOptions.find((o) => o.value === id) || { value: id, label: id })}
                        onChange={(v) => setUsers(((v as Option[] | null) || []).map((o) => o.value))}
                    />
                </label>
                {users.length > 0 && (
                    <button type="button" className="mb-1 text-[12.5px] font-extrabold text-navy-600 underline dark:text-gray-100" onClick={() => setUsers([])}>
                        {t('p10.teamdash.clearUsers')}
                    </button>
                )}
            </div>

            {tooLong && <Notice tone="warning">{t('p10.teamdash.tooLong', { days: MAX_DAYS })}</Notice>}
            {downloadError ? <Notice tone="danger">{describe(downloadError, t)}</Notice> : null}
            {data && !data.calls.available && (
                <Notice tone="warning" title={t('p10.teamdash.callsUnknownTitle')} testId="p10-teamdash-calls-unknown">
                    {t('p10.teamdash.callsUnknown')}
                </Notice>
            )}
            {data?.calls.truncated && <Notice tone="warning">{t('p10.teamdash.truncated')}</Notice>}
            {data && contract && <Notice tone="info">{t('p10.teamdash.contractCallsNote')}</Notice>}

            {q.error && !data ? (
                <ErrorState error={q.error} onRetry={q.reload} describe={describe} />
            ) : tooLong ? null : !data || !totals ? (
                <SkeletonLines rows={8} />
            ) : (
                <>
                    {/* KPI tiles */}
                    <Card lead testId="p10-teamdash-kpis">
                        <div className="grid grid-cols-2 gap-4 sm:grid-cols-4 xl:grid-cols-7">
                            <Fact label={t('p10.teamdash.kpi.callsMade')} testId="p10-teamdash-kpi-calls">
                                <Ltr className="text-[20px] font-extrabold text-navy-600 dark:text-gray-100">{count(totals.calls_made)}</Ltr>
                            </Fact>
                            <Fact label={t('p10.teamdash.kpi.answered')}>
                                <Ltr className="text-[20px] font-extrabold text-navy-600 dark:text-gray-100">{count(totals.answered)}</Ltr>
                                {callsKnown && <div className="text-[12px] text-gray-600">{t('p10.teamdash.kpi.inbound', { n: formatCount(totals.inbound_answered || 0) })}</div>}
                            </Fact>
                            <Fact label={t('p10.teamdash.kpi.answerRate')}>
                                <Ltr className="text-[20px] font-extrabold text-navy-600 dark:text-gray-100">{percent(totals.answer_rate)}</Ltr>
                            </Fact>
                            <Fact label={t('p10.teamdash.kpi.talkTime')}>
                                <Ltr className="text-[20px] font-extrabold tabular-nums text-navy-600 dark:text-gray-100">{clock(totals.talk_seconds)}</Ltr>
                            </Fact>
                            <Fact label={t('p10.teamdash.kpi.notes')} testId="p10-teamdash-kpi-notes">
                                <Ltr className="text-[20px] font-extrabold text-navy-600 dark:text-gray-100">{formatCount(totals.notes)}</Ltr>
                            </Fact>
                            <Fact label={t('p10.teamdash.kpi.clients')}>
                                <Ltr className="text-[20px] font-extrabold text-navy-600 dark:text-gray-100">{formatCount(totals.clients)}</Ltr>
                            </Fact>
                            <Fact label={t('p10.teamdash.kpi.promises')}>
                                <Ltr className="text-[20px] font-extrabold text-navy-600 dark:text-gray-100">{formatCount(totals.promises)}</Ltr>
                                <div className="text-[12px] text-gray-600">
                                    <Ltr>{formatMoney(totals.promises_amount)}</Ltr>
                                </div>
                            </Fact>
                        </div>
                    </Card>

                    <div className="grid gap-4 xl:grid-cols-2">
                        <Card title={t('p10.teamdash.callsByUser')} testId="p10-teamdash-chart-calls">
                            {!callsKnown ? (
                                <EmptyState title={t('p10.teamdash.callsUnknownTitle')} />
                            ) : callUsers.length ? (
                                <Chart
                                    type="bar"
                                    direction={direction}
                                    height={Math.max(260, callUsers.length * 34)}
                                    series={[
                                        { name: t('p10.teamdash.answered') as string, data: callUsers.map((u) => u.answered || 0) },
                                        { name: t('p10.teamdash.notAnswered') as string, data: callUsers.map((u) => u.not_answered || 0) },
                                        { name: t('p10.teamdash.inbound') as string, data: callUsers.map((u) => u.inbound_answered || 0) },
                                    ]}
                                    xAxis={callUsers.map((u) => userLabel(u, t))}
                                    customOptions={{
                                        colors: [COLORS[1], COLORS[3], COLORS[4]],
                                        chart: { stacked: true, events: barEvents(callUsers) },
                                        plotOptions: { bar: { horizontal: true, borderRadius: 4 } },
                                        legend: { position: 'top' },
                                    }}
                                />
                            ) : (
                                <EmptyState title={t('p10.teamdash.noCalls')} />
                            )}
                        </Card>
                        <Card
                            title={t('p10.teamdash.notesByUser')}
                            testId="p10-teamdash-chart-notes"
                            actions={
                                <Segmented<TeamGroup>
                                    value={group}
                                    onChange={setGroup}
                                    label={t('p10.teamdash.groupBy') as string}
                                    testId="p10-teamdash-group"
                                    options={[
                                        { value: 'status', label: t('p10.teamdash.mainStatus') },
                                        { value: 'sub_status', label: t('p10.teamdash.subStatus') },
                                    ]}
                                />
                            }
                        >
                            {noteUsers.length ? (
                                <Chart
                                    type="bar"
                                    direction={direction}
                                    height={Math.max(260, noteUsers.length * 34)}
                                    series={statusSeries.names.map((name, i) => ({ name, data: noteUsers.map((u) => statusSeries.pick(u.by_status)[i]) }))}
                                    xAxis={noteUsers.map((u) => userLabel(u, t))}
                                    customOptions={{
                                        colors: statusSeries.colors,
                                        chart: { stacked: true, events: barEvents(noteUsers) },
                                        plotOptions: { bar: { horizontal: true, borderRadius: 4 } },
                                        legend: { position: 'top' },
                                    }}
                                />
                            ) : (
                                <EmptyState title={t('p10.teamdash.noNotes')} />
                            )}
                        </Card>
                        <Card title={t(data.granularity === 'hour' ? 'p10.teamdash.overTimeHour' : 'p10.teamdash.overTimeDay')} testId="p10-teamdash-chart-time">
                            {totals.notes ? (
                                <Chart
                                    type="bar"
                                    direction={direction}
                                    height={320}
                                    series={statusSeries.names.map((name, i) => ({ name, data: data.series.map((b) => statusSeries.pick(b.by_status)[i]) }))}
                                    xAxis={data.series.map((b) => (data.granularity === 'hour' ? b.bucket.slice(11, 16) : b.bucket.slice(5)))}
                                    customOptions={{
                                        colors: statusSeries.colors,
                                        chart: { stacked: true },
                                        plotOptions: { bar: { borderRadius: 3, columnWidth: '60%' } },
                                        legend: { position: 'top' },
                                    }}
                                />
                            ) : (
                                <EmptyState title={t('p10.teamdash.noNotes')} />
                            )}
                        </Card>
                        <Card title={t('p10.teamdash.distribution')} testId="p10-teamdash-chart-donut">
                            {totals.notes ? (
                                <Chart
                                    type="donut"
                                    height={320}
                                    series={statusSeries.pick(Object.fromEntries(data.statuses.map((s) => [s.key, s.count])))}
                                    donutTitle={t('p10.teamdash.kpi.notes')}
                                    donutText={formatCount(totals.notes)}
                                    customOptions={{ labels: statusSeries.names, colors: statusSeries.colors, legend: { position: 'bottom' } }}
                                />
                            ) : (
                                <EmptyState title={t('p10.teamdash.noNotes')} />
                            )}
                        </Card>
                    </div>

                    {/* per-user table */}
                    <TableFrame testId="p10-teamdash-table" toolbar={<span className="text-[13px] font-extrabold text-navy-600 dark:text-gray-100">{t('p10.teamdash.tableTitle')}</span>}>
                        <Table>
                            <THead>
                                <Tr>
                                    <SortTh k="name" sort={sort} onSort={onSort}>{t('p10.teamdash.col.user')}</SortTh>
                                    <SortTh k="calls_made" sort={sort} onSort={onSort}>{t('p10.teamdash.col.calls')}</SortTh>
                                    <SortTh k="answered" sort={sort} onSort={onSort}>{t('p10.teamdash.col.answered')}</SortTh>
                                    <SortTh k="answer_rate" sort={sort} onSort={onSort}>{t('p10.teamdash.col.rate')}</SortTh>
                                    <SortTh k="talk_seconds" sort={sort} onSort={onSort}>{t('p10.teamdash.col.talk')}</SortTh>
                                    <SortTh k="notes" sort={sort} onSort={onSort}>{t('p10.teamdash.col.notes')}</SortTh>
                                    <SortTh k="clients" sort={sort} onSort={onSort}>{t('p10.teamdash.col.clients')}</SortTh>
                                    <Th>{t('p10.teamdash.col.topStatus')}</Th>
                                    <SortTh k="promises" sort={sort} onSort={onSort}>{t('p10.teamdash.col.promises')}</SortTh>
                                </Tr>
                            </THead>
                            <TBody>
                                {rows.length ? (
                                    rows.map((u) => (
                                        <Tr
                                            key={u.user_id || `ext-${u.extensions.join('-')}`}
                                            className={rowClass(users.length === 1 && users[0] === u.user_id)}
                                            onClick={() => focusUser(u.user_id)}
                                            data-testid="p10-teamdash-row"
                                        >
                                            <Td dir="auto">
                                                <span className="font-extrabold text-azure-600" title={u.user_id ? (t('p10.teamdash.focusUser') as string) : undefined}>
                                                    {userLabel(u, t)}
                                                </span>
                                                {u.extensions.length > 0 && u.name && (
                                                    <span className="ms-1 text-[12px] text-gray-600">
                                                        <Ltr>{u.extensions.join('، ')}</Ltr>
                                                    </span>
                                                )}
                                            </Td>
                                            <Td>
                                                <Ltr>{count(u.calls_made)}</Ltr>
                                            </Td>
                                            <Td>
                                                <Ltr>{count(u.answered)}</Ltr>
                                            </Td>
                                            <Td>
                                                <Ltr>{percent(u.answer_rate)}</Ltr>
                                            </Td>
                                            <Td className="tabular-nums">
                                                <Ltr>{clock(u.talk_seconds)}</Ltr>
                                            </Td>
                                            <Td className="font-extrabold">
                                                <Ltr>{formatCount(u.notes)}</Ltr>
                                            </Td>
                                            <Td>
                                                <Ltr>{formatCount(u.clients)}</Ltr>
                                            </Td>
                                            <Td dir="auto" className="text-[12.5px]">
                                                {u.top_status ? `${u.top_status.name || '—'} (${formatCount(u.top_status.count)})` : '—'}
                                            </Td>
                                            <Td>
                                                <Ltr>{formatCount(u.promises)}</Ltr>
                                            </Td>
                                        </Tr>
                                    ))
                                ) : (
                                    <tr>
                                        <td colSpan={9}>
                                            <EmptyState title={t('p10.teamdash.noActivity')} />
                                        </td>
                                    </tr>
                                )}
                            </TBody>
                        </Table>
                    </TableFrame>
                    <p className="text-[12px] text-gray-600">{t('p10.teamdash.note')}</p>
                    {users.length === 1 ? (
                        <UserUpdates userId={users[0]} from={data?.from || from} to={data?.to || to} contractId={contract || undefined} />
                    ) : (
                        <p className="text-[12.5px] text-gray-600" data-testid="p10-teamdash-updates-hint">
                            {t('p10.teamdash.updatesHint')}
                        </p>
                    )}
                </>
            )}
        </div>
    )
}

export default TeamDashboardPage
