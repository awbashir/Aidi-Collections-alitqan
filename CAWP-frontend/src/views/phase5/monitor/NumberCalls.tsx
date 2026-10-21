/**
 * The call monitor's page-level search by the client's number (customer request 2026-10-19):
 * the calls of the period with that number across every user of the caller's scope (or the
 * page's user filter), newest first, with the drawer's columns plus the user, and play /
 * download for holders of the recording keys. `GET /v2/calling/monitor/calls?number=` (the
 * server normalises the number: digits only, the last 9).
 */
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { callMonitorApi } from '@/services/v2'
import { usePermissions, useV2Query } from '@/utils/v2/hooks'
import { formatCount } from '@/utils/v2/format'
import { Ltr } from '@/components/v2/Display'
import { Notice } from '../parts'
import { CallsTable } from './UserCalls'

const PAGE_SIZE = 50

const NumberCalls = ({ from, to, ext, contract, number }: { from: string; to: string; ext: string; contract?: string; number: string }) => {
    const { t } = useTranslation()
    const perms = usePermissions()
    const canListen = !!(perms.clints_callsRecords_play || perms.clints_callsRecords_download)
    const [page, setPage] = useState(1)
    useEffect(() => setPage(1), [from, to, ext, contract, number])
    const q = useV2Query(
        (signal) => callMonitorApi.calls({ from, to, extension: ext || undefined, contract: contract || undefined, number, page, page_size: PAGE_SIZE }, signal),
        [from, to, ext, contract, number, page]
    )
    const data = q.data
    return (
        <section className="flex flex-col gap-2" data-testid="p9-monitor-number-calls" data-number={number}>
            <div className="flex flex-wrap items-baseline gap-2">
                <h3 className="text-[14px] font-extrabold">{t('p9.monfix.monitor.searchTitle')}</h3>
                <Ltr className="text-[13px] text-gray-600">{number}</Ltr>
            </div>
            {data?.truncated && <Notice tone="warning">{t('p5.monitor.truncated')}</Notice>}
            <CallsTable
                showUser
                data={data}
                loading={q.loading}
                error={q.error}
                onRetry={q.reload}
                onPage={setPage}
                canListen={canListen}
                empty={t('p9.monfix.monitor.searchEmpty')}
            />
            {data && <p className="text-[12px] text-gray-600">{t('p9.monfix.monitor.searchNote', { total: formatCount(data.total) })}</p>}
        </section>
    )
}

export default NumberCalls
