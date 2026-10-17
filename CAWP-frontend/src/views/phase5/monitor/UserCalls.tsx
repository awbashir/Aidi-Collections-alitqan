/**
 * One user's calls in the call monitor (customer request 2026-10-17): opened from a row of the
 * per-user table, for the page's period. `GET /v2/calling/monitor/calls?from&to&extension&page`
 * (newest first, 50 a page; the API applies the same scope as the table).
 *
 * Recordings play and download through the legacy `GET /cdr/recording` (RecordingControls of the
 * call detail), which authorizes each file again; the buttons appear only with the recording keys
 * (`clints_callsRecords_play` / `_download`), and without them the API sends no recording path.
 */
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import StatusPill from '@/components/shared/StatusPill'
import type { StatusTone } from '@/components/shared/StatusPill'
import Drawer from '@/components/ui/Drawer'
import Table from '@/components/ui/Table'
import { Ltr, RiyadhTime } from '@/components/v2/Display'
import { PagerFooter, TableFrame } from '@/components/v2/ListParts'
import { EmptyState, ErrorState, SkeletonRows } from '@/components/v2/States'
import { callMonitorApi, describeP5Error } from '@/services/v2'
import type { MonitorAgent, MonitorCall, Translate } from '@/services/v2'
import { useAppSelector } from '@/store'
import { usePermissions, useV2Query } from '@/utils/v2/hooks'
import { formatCount } from '@/utils/v2/format'
import { RecordingControls } from '../calls/CallDetail'
import { Notice } from '../parts'

const { THead, TBody, Tr, Th, Td } = Table
const PAGE_SIZE = 50
const COLS = 9
const describe = (e: unknown, t: Translate) => describeP5Error(e, t)

const RESULT_KEY: Record<string, { key: string; tone: StatusTone }> = {
    ANSWERED: { key: 'answered', tone: 'success' },
    'NO ANSWER': { key: 'noAnswer', tone: 'warning' },
    BUSY: { key: 'busy', tone: 'warning' },
    FAILED: { key: 'failed', tone: 'danger' },
    CONGESTION: { key: 'congestion', tone: 'danger' },
}

export const clock = (seconds: number) => {
    const s = Math.max(0, Math.floor(seconds))
    const h = Math.floor(s / 3600)
    const m = Math.floor((s % 3600) / 60)
    const r = s % 60
    return `${h ? `${h}:` : ''}${String(m).padStart(h ? 2 : 1, '0')}:${String(r).padStart(2, '0')}`
}

const ResultPill = ({ result }: { result: string }) => {
    const { t } = useTranslation()
    const r = RESULT_KEY[result]
    return (
        <StatusPill tone={r?.tone || 'neutral'} size="row">
            <span data-result={result}>{r ? t(`p8.monitor.result.${r.key}`) : result || '—'}</span>
        </StatusPill>
    )
}

const recordingName = (c: MonitorCall) => (c.recording || '').split('/').pop()?.replace(/\.wav$/i, '') || `${c.extension}-${c.call_id}`

const CallRow = ({ c, canListen }: { c: MonitorCall; canListen: boolean }) => {
    const { t } = useTranslation()
    return (
        <Tr data-testid="p8-monitor-user-call" data-call-id={c.call_id}>
            <Td>
                <RiyadhTime iso={c.at} />
            </Td>
            <Td className="whitespace-nowrap">{t(`p5.monitor.directionValue.${c.direction}`)}</Td>
            <Td>
                <Ltr>{c.number || '—'}</Ltr>
            </Td>
            <Td className="max-w-[220px]">
                {c.client ? (
                    <span className="flex min-w-0 flex-col">
                        <span className="truncate" dir="auto">
                            {c.client.name || '—'}
                        </span>
                        {c.client.account_number && <Ltr className="text-[12px] text-gray-600">{c.client.account_number}</Ltr>}
                    </span>
                ) : (
                    <span className="text-gray-500">—</span>
                )}
            </Td>
            <Td>
                <Ltr>{c.line || '—'}</Ltr>
            </Td>
            <Td>
                <ResultPill result={c.result} />
            </Td>
            <Td className="tabular-nums">
                <Ltr>{clock(c.ring_seconds)}</Ltr>
            </Td>
            <Td className="tabular-nums">
                <Ltr>{c.answered ? clock(c.talk_seconds) : '—'}</Ltr>
            </Td>
            {canListen && (
                <Td>
                    {c.recording ? (
                        <RecordingControls rec={{ has_recording: true, file: c.recording, url: null }} name={recordingName(c)} />
                    ) : (
                        <span className="text-[12px] text-gray-500">{c.recorded ? t('p5.rec.hidden') : t('p8.monitor.notRecorded')}</span>
                    )}
                </Td>
            )}
        </Tr>
    )
}

const UserCalls = ({
    agent,
    from,
    to,
    onClose,
}: {
    agent: MonitorAgent | null
    from: string
    to: string
    onClose: () => void
}) => {
    const { t } = useTranslation()
    const perms = usePermissions()
    const canListen = !!(perms.clints_callsRecords_play || perms.clints_callsRecords_download)
    const rtl = useAppSelector((state) => state.theme.direction) === 'rtl'
    const ext = agent?.extension || ''
    const [page, setPage] = useState(1)
    useEffect(() => setPage(1), [ext, from, to])
    const q = useV2Query(
        ext ? (signal) => callMonitorApi.calls({ from, to, extension: ext, page, page_size: PAGE_SIZE }, signal) : null,
        [ext, from, to, page]
    )
    const data = q.data && q.data.extension === ext ? q.data : undefined
    const cols = canListen ? COLS : COLS - 1
    const width = typeof window !== 'undefined' ? Math.min(1080, window.innerWidth) : 1080
    return (
        <Drawer
            isOpen={!!agent}
            onClose={onClose}
            onRequestClose={onClose}
            placement={rtl ? 'left' : 'right'}
            width={width}
            bodyClass="p-4"
            title={
                <span className="flex flex-col">
                    <span className="text-[16px] font-extrabold" dir="auto">
                        {t('p8.monitor.userCallsTitle', { name: agent ? (agent.name ? `${agent.name} (${agent.extension})` : agent.extension) : '' })}
                    </span>
                    <span className="text-[12.5px] font-normal text-gray-600">
                        <Ltr>{from === to ? from : `${from} → ${to}`}</Ltr>
                    </span>
                </span>
            }
        >
            <div className="flex flex-col gap-3" data-testid="p8-monitor-user-calls" data-extension={ext}>
                {data?.truncated && <Notice tone="warning">{t('p5.monitor.truncated')}</Notice>}
                {q.error && !data ? (
                    <ErrorState error={q.error} onRetry={q.reload} describe={describe} />
                ) : (
                    <TableFrame
                        footer={
                            data && data.total > 0 ? (
                                <PagerFooter page={data.page} pageSize={data.page_size} total={data.total} onPage={setPage} />
                            ) : undefined
                        }
                    >
                        <Table>
                            <THead>
                                <Tr>
                                    <Th>{t('p8.monitor.time')}</Th>
                                    <Th>{t('p5.monitor.direction')}</Th>
                                    <Th>{t('p5.monitor.number')}</Th>
                                    <Th>{t('p8.monitor.client')}</Th>
                                    <Th>{t('p8.monitor.line')}</Th>
                                    <Th>{t('p8.monitor.result.title')}</Th>
                                    <Th>{t('p8.monitor.ring')}</Th>
                                    <Th>{t('p5.monitor.talkTime')}</Th>
                                    {canListen && <Th>{t('p8.monitor.recording')}</Th>}
                                </Tr>
                            </THead>
                            <TBody>
                                {!data || q.loading ? (
                                    <SkeletonRows cols={cols} rows={6} />
                                ) : data.items.length ? (
                                    data.items.map((c) => <CallRow key={`${c.call_id}-${c.extension}-${c.direction}`} c={c} canListen={canListen} />)
                                ) : (
                                    <tr>
                                        <td colSpan={cols}>
                                            <EmptyState title={t('p5.monitor.noStats')} />
                                        </td>
                                    </tr>
                                )}
                            </TBody>
                        </Table>
                    </TableFrame>
                )}
                {data && (
                    <p className="text-[12px] text-gray-600">
                        {t('p8.monitor.userCallsNote', { total: formatCount(data.total) })}
                    </p>
                )}
            </div>
        </Drawer>
    )
}

export default UserCalls
