/**
 * Reports hub (`/reports`, derived key `reports_any`, Recovery & team).
 *
 * Lists exactly the reports `GET /v2/reports` returns for the caller (the API
 * applies each report's screen key), grouped by family in the API's order,
 * with the labels and descriptions in the current language and the other
 * language underneath. A literal search matches the id, both labels and both
 * descriptions. Each card opens the generic viewer and links to its "classic"
 * legacy screen(s), which stay. A second tab lists "My exports".
 * Tab and search live in the URL hash.
 */
import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { HiOutlineSearch } from 'react-icons/hi'
import Input from '@/components/ui/Input'
import { EmptyState, ErrorState, SkeletonLines, Note } from '@/components/v2/States'
import { CountTabs, PageHead } from '@/components/v2/ListParts'
import { REPORT_EXPORT_STATUSES, describeReportError, reportsApi } from '@/services/v2'
import type { ReportExportStatus, ReportSummary } from '@/services/v2'
import useHashState, { hashCodec } from '@/utils/hooks/useHashState'
import type { HashSchema } from '@/utils/hooks/useHashState'
import { usePermissions, useV2Query } from '@/utils/v2/hooks'
import MyExports from '../exports/MyExports'
import { CLASSIC_LINKS, classicLinksFor, pick } from '../reportUtils'

type Tab = 'reports' | 'exports'
type HubState = { tab: Tab; q: string | undefined; xstatus: ReportExportStatus | 'all'; xpage: number }

const schema: HashSchema<HubState> = {
    tab: { key: 'tab', codec: hashCodec.oneOf(['reports', 'exports'] as const), default: 'reports' },
    q: { key: 'q', codec: hashCodec.text({ maxLength: 100 }), default: undefined, history: 'replace' },
    xstatus: { key: 'xstatus', codec: hashCodec.oneOf(['all', ...REPORT_EXPORT_STATUSES] as const), default: 'all' },
    xpage: { key: 'xpage', codec: hashCodec.int({ min: 1, max: 100000 }), default: 1 },
}

const FORMAT_SHORT: Record<string, string> = { csv: 'CSV', xlsx: 'XLSX', xlsx_ar: 'XLSX AR' }

const norm = (s: string) => s.toLocaleLowerCase('en-US')

function ReportCard({ r, ar }: { r: ReportSummary; ar: boolean }) {
    const { t } = useTranslation()
    const perms = usePermissions()
    const classic = classicLinksFor(r.id, perms)
    return (
        <article
            className="flex min-w-0 flex-col rounded-lg border border-gray-300 bg-white p-5 transition-colors hover:border-azure-500 dark:border-night-border dark:bg-night-card"
            data-testid="p4-report-card"
            data-report={r.id}
            data-family={r.family}
        >
            <div className="mb-1 flex items-center justify-between gap-2">
                <span dir="ltr" className="rounded-xs bg-navy-600 px-2 text-[11px] font-extrabold tracking-[0.06em] text-white">
                    {r.id}
                </span>
                <span className="flex gap-1" aria-label={String(t('p4.hub.formats'))}>
                    {r.formats.map((f) => (
                        <span
                            key={f}
                            className="rounded-xs border border-gray-300 px-1.5 text-[10.5px] font-extrabold text-gray-600 dark:border-night-border"
                            title={String(t(`p4.format.${f}`))}
                            data-format={f}
                        >
                            {FORMAT_SHORT[f] || f}
                        </span>
                    ))}
                </span>
            </div>
            <Link
                to={`/reports/${encodeURIComponent(r.id)}`}
                className="text-[15px] font-extrabold text-navy-600 hover:text-azure-600 hover:underline dark:text-gray-100"
                data-testid="p4-report-open"
            >
                {pick(r.labels, ar)}
            </Link>
            <div className="text-[12px] text-gray-500" dir={ar ? 'ltr' : 'rtl'}>
                {ar ? r.labels.en : r.labels.ar}
            </div>
            <p className="mt-2 line-clamp-3 text-[12.5px] text-gray-600" title={pick(r.description, ar)}>
                {pick(r.description, ar)}
            </p>
            <div className="mt-auto flex flex-wrap items-center gap-x-3 gap-y-1 pt-3 text-[12px]">
                <Link to={`/reports/${encodeURIComponent(r.id)}`} className="font-extrabold text-azure-600 hover:underline">
                    {t('p4.hub.open')}
                </Link>
                {classic.map((c) => (
                    <Link key={c.path} to={c.path} className="text-gray-600 hover:text-azure-600 hover:underline" data-testid="p4-classic-link">
                        {t('p4.classic.prefix')} {t(c.labelKey)}
                    </Link>
                ))}
            </div>
        </article>
    )
}

export default function ReportsHub() {
    const { t, i18n } = useTranslation()
    const ar = (i18n.language || 'ar').startsWith('ar')
    const perms = usePermissions()
    const { state, setState } = useHashState(schema)
    const q = useV2Query((signal) => reportsApi.list(signal), [])
    const data = q.data

    /* The box is local state; the hash follows it (debounced replace), and a
     * Back/Forward or shared link sets the box. */
    const [text, setText] = useState(state.q || '')
    useEffect(() => setText(state.q || ''), [state.q])
    const needle = norm(text.trim())
    const filtered = useMemo(() => {
        if (!data) return []
        if (!needle) return data.items
        return data.items.filter((r) =>
            [r.id, r.labels.en, r.labels.ar, r.description.en, r.description.ar].some((s) => norm(s || '').includes(needle))
        )
    }, [data, needle])

    const families = useMemo(() => {
        if (!data) return []
        const byId = new Map(filtered.map((r) => [r.id, r]))
        return data.families
            .map((f) => ({ family: f.family, reports: f.reports.map((id) => byId.get(id)).filter(Boolean) as ReportSummary[] }))
            .filter((f) => f.reports.length > 0)
    }, [data, filtered])

    /* Every legacy report screen the user can open (they stay available). */
    const classicAll = useMemo(() => {
        const seen = new Map<string, { path: string; labelKey: string }>()
        ;(data?.items || []).forEach((r) =>
            (CLASSIC_LINKS[r.id] || []).forEach((c) => {
                if (perms[c.key] === true && !seen.has(c.path)) seen.set(c.path, c)
            })
        )
        return Array.from(seen.values())
    }, [data, perms])

    return (
        <div data-testid="p4-hub">
            <PageHead
                title={t('p4.hub.title')}
                subtitle={data ? t('p4.hub.subtitle', { n: data.total }) : undefined}
                onRefresh={q.reload}
                refreshing={q.loading && !!data}
            />
            <div className="mb-4">
                <CountTabs
                    tabs={[
                        { value: 'reports' as Tab, label: t('p4.hub.tabReports'), count: data ? data.total : undefined },
                        { value: 'exports' as Tab, label: t('p4.hub.tabExports') },
                    ]}
                    value={state.tab}
                    onChange={(tab) => setState({ tab })}
                    testId="p4-hub-tabs"
                />
            </div>

            {state.tab === 'exports' ? (
                <MyExports
                    status={state.xstatus === 'all' ? '' : state.xstatus}
                    onStatus={(s) => setState({ xstatus: s || 'all', xpage: 1 })}
                    page={state.xpage}
                    onPage={(p) => setState({ xpage: p })}
                />
            ) : q.loading && !data ? (
                <SkeletonLines rows={8} />
            ) : q.error && !data ? (
                <ErrorState error={q.error} onRetry={q.reload} describe={(e, tt) => describeReportError(e, tt)} />
            ) : data ? (
                <>
                    <div className="mb-5 flex flex-wrap items-center gap-3">
                        <div className="w-full max-w-[420px]">
                            <Input
                                size="sm"
                                prefix={<HiOutlineSearch className="text-gray-500" />}
                                value={text}
                                maxLength={100}
                                placeholder={String(t('p4.hub.search'))}
                                onChange={(e) => {
                                    setText(e.target.value)
                                    setState({ q: e.target.value || undefined })
                                }}
                                data-testid="p4-hub-search"
                            />
                        </div>
                        <span className="text-[12.5px] text-gray-600 tabular-nums" data-testid="p4-hub-count" data-count={filtered.length}>
                            {t('p4.hub.shown', { n: filtered.length, total: data.total })}
                        </span>
                    </div>

                    {data.total === 0 ? (
                        <EmptyState title={t('p4.hub.noneTitle')} hint={t('p4.hub.noneHint')} />
                    ) : families.length === 0 ? (
                        <EmptyState
                            title={t('p4.hub.noMatchTitle')}
                            hint={t('p4.hub.noMatchHint', { q: text })}
                            action={
                                <button type="button" className="text-[13px] font-extrabold text-azure-600 underline" onClick={() => {
                                    setText('')
                                    setState({ q: undefined })
                                }}>
                                    {t('p4.hub.clearSearch')}
                                </button>
                            }
                        />
                    ) : (
                        families.map((f) => (
                            <section key={f.family} className="mb-8" data-testid="p4-family" data-family={f.family}>
                                <h4 className="mb-3 flex items-baseline gap-2 text-[15px] font-extrabold text-navy-600 dark:text-gray-100">
                                    {t(`p4.family.${f.family}`, { defaultValue: f.family })}
                                    <span className="text-[12px] font-normal text-gray-600 tabular-nums">{f.reports.length}</span>
                                </h4>
                                <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
                                    {f.reports.map((r) => (
                                        <ReportCard key={r.id} r={r} ar={ar} />
                                    ))}
                                </div>
                            </section>
                        ))
                    )}

                    {classicAll.length > 0 && (
                        <section className="mb-6 rounded-lg border border-gray-300 bg-white p-5 dark:border-night-border dark:bg-night-card" data-testid="p4-classic-all">
                            <div className="text-eyebrow mb-2">{t('p4.classic.sectionTitle')}</div>
                            <Note className="mb-3">{t('p4.classic.sectionHint')}</Note>
                            <div className="flex flex-wrap gap-x-5 gap-y-2 text-[13px]">
                                {classicAll.map((c) => (
                                    <Link key={c.path} to={c.path} className="text-azure-600 hover:underline">
                                        {t(c.labelKey)}
                                    </Link>
                                ))}
                            </div>
                        </section>
                    )}
                </>
            ) : null}
        </div>
    )
}
