/**
 * Quality workspace (`/quality`; Intelligence & control). Opens for `quality_review` (library,
 * transcripts, reviews, reports) and for `quality_approve` (approval; an approver can read what
 * they approve). Mirrors the API's keys: mutations need `quality_review`, approval
 * `quality_approve`; the API checks both again.
 *
 * - Overview: the dashboard of approved reviews (compliance, coverage, exact counts; filters by
 *   template, criterion, period and contract; "—" with the reason when nothing was assessed).
 * - Library: the recordings, private import, and the way to "add call recording" from a call.
 * - A recording (`#rec=<id>`): player, evidence, transcript, review per criterion, approval,
 *   approval history and snapshots, report download.
 *
 * Everything that describes where the user is lives in the URL hash (tab, filters, page, the open
 * recording and its section), so reload, Back and a shared link reopen the same view.
 */
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { CountTabs, PageHead } from '@/components/v2/ListParts'
import useHashState, { hashCodec } from '@/utils/hooks/useHashState'
import type { HashSchema } from '@/utils/hooks/useHashState'
import { qualityApi } from '@/services/v2'
import { useV2Query } from '@/utils/v2/hooks'
import Dashboard from './Dashboard'
import type { DashFilters } from './Dashboard'
import Library from './Library'
import RecordingDetail from './RecordingDetail'
import { SECTIONS } from './links'
import type { Section } from './links'

type Tab = 'overview' | 'library'
type Group = 'day' | 'week' | 'month'
type HashState = {
    tab: Tab
    rec: string | number | undefined
    sec: Section
    // dashboard
    tpl: string | undefined
    crit: string | undefined
    contract: string | number | undefined
    from: string | undefined
    to: string | undefined
    group: Group
    // library
    status: string | undefined
    source: string | undefined
    template: string | undefined
    page: number
}

const schema: HashSchema<HashState> = {
    tab: { key: 'tab', codec: hashCodec.oneOf(['overview', 'library'] as Tab[]), default: 'overview' },
    rec: { key: 'rec', codec: hashCodec.id(), default: undefined },
    sec: { key: 'sec', codec: hashCodec.oneOf(SECTIONS), default: 'evidence' },
    tpl: { key: 'tpl', codec: hashCodec.text({ maxLength: 80 }), default: undefined },
    crit: { key: 'crit', codec: hashCodec.text({ maxLength: 60 }), default: undefined },
    contract: { key: 'contract', codec: hashCodec.id(), default: undefined },
    from: { key: 'from', codec: hashCodec.date(), default: undefined },
    to: { key: 'to', codec: hashCodec.date(), default: undefined },
    group: { key: 'group', codec: hashCodec.oneOf(['day', 'week', 'month'] as Group[]), default: 'month' },
    status: { key: 'status', codec: hashCodec.text({ maxLength: 20 }), default: undefined },
    source: { key: 'source', codec: hashCodec.text({ maxLength: 20 }), default: undefined },
    template: { key: 'template', codec: hashCodec.text({ maxLength: 60 }), default: undefined },
    page: { key: 'page', codec: hashCodec.int({ min: 1, max: 100000 }), default: 1 },
}

const Quality = () => {
    const { t } = useTranslation()
    const { state, setState } = useHashState(schema)
    const [tick, setTick] = useState(0)
    const overview = useV2Query((signal) => qualityApi.overview(signal), [tick])
    const rec = state.rec === undefined ? undefined : String(state.rec)
    const dash: DashFilters = {
        tpl: state.tpl,
        crit: state.crit,
        contract: state.contract === undefined ? undefined : String(state.contract),
        from: state.from,
        to: state.to,
        group: state.group,
    }
    return (
        <div data-testid="p6-quality">
            {rec ? (
                <RecordingDetail
                    key={rec}
                    recordingId={rec}
                    overview={overview.data}
                    section={state.sec}
                    onSection={(sec) => setState({ sec })}
                    onBack={() => setState({ rec: undefined, sec: 'evidence', tab: 'library' })}
                    onChanged={() => setTick((n) => n + 1)}
                />
            ) : (
                <>
                    <PageHead
                        title={t('p6.qc.title')}
                        subtitle={overview.data ? t('p6.qc.subtitle', { count: overview.data.counts.recordings }) : undefined}
                        onRefresh={() => setTick((n) => n + 1)}
                        refreshing={overview.loading && !!overview.data}
                    />
                    <div className="mb-4">
                        <CountTabs<Tab>
                            tabs={[
                                { value: 'overview', label: t('p6.qc.tab.overview') },
                                { value: 'library', label: t('p6.qc.tab.library'), count: overview.data?.counts.recordings },
                            ]}
                            value={state.tab}
                            onChange={(tab) => setState({ tab })}
                            testId="p6-qc-tabs"
                        />
                    </div>
                    {state.tab === 'overview' ? (
                        <Dashboard overview={overview.data} filters={dash} onFilters={(f) => setState(f)} />
                    ) : (
                        <Library
                            overview={overview.data}
                            filters={{ status: state.status, source: state.source, template: state.template }}
                            page={state.page}
                            tick={tick}
                            onFilters={(f) => setState({ ...f, page: 1 })}
                            onPage={(page) => setState({ page })}
                            onOpen={(id) => setState({ rec: id, sec: 'evidence' })}
                            onImported={() => setTick((n) => n + 1)}
                        />
                    )}
                </>
            )}
        </div>
    )
}

export default Quality
