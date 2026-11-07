/**
 * Team performance dashboard: one user's client updates (customer request 2026-10-21).
 * Shown when one user is selected (a click on their row). `GET /v2/dashboards/team/user-updates`:
 * each update (note) with the status it set and the call it reports, i.e. that user's call to one of
 * the client's numbers at most 3 h before the update. No such call = "No call found" (unknown, never
 * guessed); PBX records unavailable = a notice and no call columns filled.
 */
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import Button from '@/components/ui/Button'
import Table from '@/components/ui/Table'
import { Ltr, RiyadhTime } from '@/components/v2/Display'
import { EmptyState, ErrorState, SkeletonRows } from '@/components/v2/States'
import { TableFrame } from '@/components/v2/ListParts'
import { CaseCell, Notice } from '@/views/phase5/parts'
import { ResultPill, clock } from '@/views/phase5/monitor/UserCalls'
import { describeP5Error } from '@/services/v2'
import type { Translate } from '@/services/v2'
import { teamDashboardApi } from '@/services/v2/teamDashboardApi'
import type { TeamUserUpdate } from '@/services/v2/teamDashboardApi'
import { useV2Query } from '@/utils/v2/hooks'
import { formatCount } from '@/utils/v2/format'

const { THead, TBody, Tr, Th, Td } = Table
const PAGE_SIZE = 50
const COLS = 6
const describe = (e: unknown, t: Translate) => describeP5Error(e, t)

const StatusChip = ({ s }: { s: { name: string | null; color: string | null } | null }) =>
    s ? (
        <span className="inline-flex items-center gap-1.5" dir="auto">
            <span aria-hidden className="inline-block h-2.5 w-2.5 shrink-0 rounded-full" style={{ background: s.color || '#94a3b8' }} />
            <span>{s.name || '—'}</span>
        </span>
    ) : (
        <span className="text-gray-500">—</span>
    )

const Row = ({ u }: { u: TeamUserUpdate }) => {
    const { t } = useTranslation()
    return (
        <Tr data-testid="p10-teamdash-update" data-note={u.note_id}>
            <Td className="whitespace-nowrap text-[12.5px]">
                <RiyadhTime iso={u.at} />
            </Td>
            <Td>
                <CaseCell kase={{ id: u.client.case_id, name: u.client.name, account_number: u.client.account_number }} tab="comments" />
            </Td>
            <Td className="text-[12.5px]">
                <StatusChip s={u.status} />
                {u.sub_status && (
                    <div className="mt-0.5 text-[12px] text-gray-600">
                        <StatusChip s={u.sub_status} />
                    </div>
                )}
            </Td>
            <Td>
                {u.call ? (
                    <span className="flex flex-col items-start gap-0.5">
                        <ResultPill result={u.call.answered ? 'ANSWERED' : u.call.result || ''} />
                        <span className="text-[11.5px] text-gray-600">
                            <RiyadhTime iso={u.call.at} />
                            {u.call.direction === 'inbound' ? ` · ${t('p10.teamdash.upd.inbound')}` : ''}
                        </span>
                    </span>
                ) : (
                    <span className="text-[12.5px] text-gray-500" title={t('p10.teamdash.upd.noCallHint') as string}>
                        {t('p10.teamdash.upd.noCall')}
                    </span>
                )}
            </Td>
            <Td className="tabular-nums">
                <Ltr>{u.call ? (u.call.answered ? clock(u.call.talk_seconds) : '0:00') : '—'}</Ltr>
            </Td>
            <Td className="max-w-[320px] text-[12.5px] text-gray-700 dark:text-night-body">
                <span className="line-clamp-2" dir="auto" title={u.comment || undefined}>
                    {u.comment || '—'}
                </span>
            </Td>
        </Tr>
    )
}

const UserUpdates = ({ userId, from, to, contractId }: { userId: string; from: string; to: string; contractId?: string }) => {
    const { t } = useTranslation()
    const [page, setPage] = useState(1)
    useEffect(() => setPage(1), [userId, from, to, contractId])
    const q = useV2Query(
        (signal) => teamDashboardApi.userUpdates({ user_id: userId, from, to, contract_id: contractId || undefined, page, page_size: PAGE_SIZE }, signal),
        [userId, from, to, contractId, page]
    )
    const data = q.data && q.data.user.id === userId ? q.data : undefined
    const pages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1
    return (
        <div className="flex flex-col gap-2" data-testid="p10-teamdash-updates" data-user={userId}>
            <div className="flex flex-wrap items-end justify-between gap-2">
                <div>
                    <div className="text-[15px] font-extrabold text-navy-600 dark:text-gray-100" dir="auto">
                        {t('p10.teamdash.updatesTitle', { name: data?.user.name || '…' })}
                    </div>
                    {data && (
                        <div className="text-[12.5px] text-gray-600">
                            {t('p10.teamdash.updatesSubtitle', { updates: formatCount(data.updates), clients: formatCount(data.clients) })}
                        </div>
                    )}
                </div>
                {data && pages > 1 && (
                    <div className="flex items-center gap-2 text-[12.5px]">
                        <Button size="xs" disabled={page <= 1 || q.loading} onClick={() => setPage((p) => p - 1)}>
                            {t('p10.teamdash.upd.prev')}
                        </Button>
                        <span>{t('p10.teamdash.upd.page', { page, pages })}</span>
                        <Button size="xs" disabled={page >= pages || q.loading} onClick={() => setPage((p) => p + 1)}>
                            {t('p10.teamdash.upd.next')}
                        </Button>
                    </div>
                )}
            </div>
            {data && !data.calls.available && <Notice tone="warning">{t('p10.teamdash.upd.callsUnavailable')}</Notice>}
            <TableFrame testId="p10-teamdash-updates-table">
                {q.error && !data ? (
                    <ErrorState error={q.error} onRetry={q.reload} describe={describe} />
                ) : (
                    <Table>
                        <THead>
                            <Tr>
                                <Th>{t('p10.teamdash.upd.time')}</Th>
                                <Th>{t('p10.teamdash.upd.client')}</Th>
                                <Th>{t('p10.teamdash.upd.status')}</Th>
                                <Th>{t('p10.teamdash.upd.callStatus')}</Th>
                                <Th>{t('p10.teamdash.upd.duration')}</Th>
                                <Th>{t('p10.teamdash.upd.note')}</Th>
                            </Tr>
                        </THead>
                        <TBody>
                            {q.loading && !data ? (
                                <SkeletonRows cols={COLS} rows={5} />
                            ) : data && data.items.length ? (
                                data.items.map((u) => <Row key={u.note_id} u={u} />)
                            ) : (
                                <tr>
                                    <td colSpan={COLS}>
                                        <EmptyState title={t('p10.teamdash.upd.empty')} />
                                    </td>
                                </tr>
                            )}
                        </TBody>
                    </Table>
                )}
            </TableFrame>
        </div>
    )
}

export default UserUpdates
