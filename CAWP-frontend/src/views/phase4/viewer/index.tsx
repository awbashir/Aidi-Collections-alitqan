/**
 * Generic report viewer (`/reports/:id`, derived key `reports_any`; the API
 * re-checks the report's own key on every call).
 *
 * Everything comes from the definition (`GET /v2/reports/<id>`): the filter
 * form, the table columns and their types, the sorts, the exact totals, the
 * export formats. Filters, sort, page and page size live in the URL hash (the
 * report id is the path), so reload, Back and a shared link reopen the same
 * result.
 *
 *   /reports/RPT-06#payment_date_from=2026-09-01&status=approved,reversed&sort=-amount&page=2
 */
import { useCallback, useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { HiOutlineArrowLeft, HiOutlineArrowRight } from 'react-icons/hi'
import { SkeletonLines, ErrorState, Note } from '@/components/v2/States'
import { PageHead } from '@/components/v2/ListParts'
import { describeReportError, reportFieldErrors, reportsApi } from '@/services/v2'
import type { ReportDefinition } from '@/services/v2'
import useHashState from '@/utils/hooks/useHashState'
import { usePermissions, useV2Query } from '@/utils/v2/hooks'
import FilterPanel from './FilterPanel'
import TotalsStrip from './TotalsStrip'
import ReportTable from './ReportTable'
import TrendChart from './TrendChart'
import ExportPanel from '../exports/ExportPanel'
import MyExports from '../exports/MyExports'
import {
    CHARTS,
    buildHashSchema,
    classicLinksFor,
    clientDefaults,
    filterParams,
    nonEmpty,
    pick,
    stableKey,
} from '../reportUtils'
import type { ViewerHashState } from '../reportUtils'

function ReportViewer({ def }: { def: ReportDefinition }) {
    const { t, i18n } = useTranslation()
    const ar = (i18n.language || 'ar').startsWith('ar')
    const perms = usePermissions()
    /* Defaults are computed once per visit (a month boundary mid-visit does not move the filter). */
    const defaults = useMemo(() => clientDefaults(def), [def])
    const schema = useMemo(() => buildHashSchema(def, defaults), [def, defaults])
    const { state, setState } = useHashState<ViewerHashState>(schema)

    const values = filterParams(def, state)
    const sent = nonEmpty(values)
    const sort = String(state.__sort)
    const page = Number(state.__page)
    const size = Number(state.__size)
    const sentKey = stableKey(sent)

    const query = useV2Query(
        (signal) => reportsApi.rows(def.id, { ...sent, sort, page, page_size: size }, signal),
        [def.id, sentKey, sort, page, size]
    )
    const fieldErrors = reportFieldErrors(query.error)
    const data = query.error ? undefined : query.data

    const [exportsTick, setExportsTick] = useState(0)

    const paramLabel = useCallback(
        (param: string) => {
            if (param === 'sort') return String(t('p4.table.sortBy'))
            if (param === 'page' || param === 'page_size') return param
            const f = def.filters.find((x) => x.params.includes(param))
            if (!f) return param
            const base = pick(f.labels, ar)
            if (f.params.length < 2) return base
            const end = param.endsWith('_from') || param === 'from_hour' ? t('p4.filter.from') : t('p4.filter.to')
            return `${base} (${end})`
        },
        [def, ar, t]
    )

    const onFilters = (patch: Record<string, string>) => setState({ ...patch, __page: 1 })
    const onReset = () => {
        const patch: ViewerHashState = { __page: 1 }
        Object.keys(values).forEach((p) => {
            patch[p] = defaults[p] ?? ''
        })
        setState(patch)
    }

    const classic = classicLinksFor(def.id, perms)
    const chart = CHARTS[def.id]
    const Back = ar ? HiOutlineArrowRight : HiOutlineArrowLeft

    return (
        <div data-testid="p4-viewer" data-report={def.id}>
            <Link to="/reports" className="mb-2 inline-flex items-center gap-1 text-[12.5px] font-extrabold text-azure-600 hover:underline" data-testid="p4-back">
                <Back aria-hidden /> {t('p4.hub.title')}
            </Link>
            <PageHead
                title={
                    <span className="flex flex-wrap items-baseline gap-2">
                        <span dir="ltr" className="rounded-xs bg-navy-600 px-2 text-[11px] font-extrabold tracking-[0.06em] text-white">
                            {def.id}
                        </span>
                        <span data-testid="p4-title">{pick(def.labels, ar)}</span>
                    </span>
                }
                subtitle={<span className="block max-w-[980px]">{pick(def.description, ar)}</span>}
                onRefresh={query.reload}
                refreshing={query.loading && !!query.data}
            />

            {(def.fixed.length > 0 || classic.length > 0) && (
                <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
                    {def.fixed.length > 0 && (
                        <ul className="flex flex-wrap gap-2" data-testid="p4-fixed" aria-label={String(t('p4.viewer.rules'))}>
                            {def.fixed.map((r) => (
                                <li key={r.en} className="rounded-xs bg-slate-100 px-2 py-1 text-[12px] text-gray-700 dark:bg-night-raised dark:text-night-body">
                                    {pick(r, ar)}
                                </li>
                            ))}
                        </ul>
                    )}
                    {classic.length > 0 && (
                        <div className="text-[12.5px] text-gray-600" data-testid="p4-classic">
                            {t('p4.classic.label')}{' '}
                            {classic.map((c, i) => (
                                <span key={c.path}>
                                    {i > 0 && ' · '}
                                    <Link to={c.path} className="font-extrabold text-azure-600 hover:underline">
                                        {t(c.labelKey)}
                                    </Link>
                                </span>
                            ))}
                        </div>
                    )}
                </div>
            )}

            <FilterPanel def={def} values={values} defaults={defaults} errors={fieldErrors} onChange={onFilters} onReset={onReset} />

            {fieldErrors && (
                <div role="alert" className="mb-4 rounded-md border border-red-200 bg-danger-tint px-4 py-2 text-[13px] text-danger" data-testid="p4-validation">
                    {describeReportError(query.error, t, paramLabel)}
                </div>
            )}

            <TotalsStrip def={def} data={data} />

            {chart && !fieldErrors && (
                <TrendChart
                    def={def}
                    spec={chart}
                    params={sent}
                    onShowSection={chart.section ? () => setState({ section: chart.section as string, __page: 1 }) : undefined}
                />
            )}

            <ExportPanel
                def={def}
                params={sent}
                sort={sort}
                previewTotal={data?.total}
                onChanged={() => setExportsTick((n) => n + 1)}
            />

            <ReportTable
                def={def}
                data={data}
                loading={query.loading}
                error={fieldErrors ? null : query.error}
                onRetry={query.reload}
                sort={sort}
                onSort={(s) => setState({ __sort: s, __page: 1 })}
                page={page}
                size={size}
                onPage={(p) => setState({ __page: p })}
                onSize={(n) => setState({ __size: n, __page: 1 })}
                perms={perms}
                paramLabel={paramLabel}
                toolbar={
                    <span className="text-[15px] font-extrabold text-navy-600 dark:text-gray-100">{t('p4.table.title')}</span>
                }
            />
            <Note className="mb-6 mt-3">{t('p4.viewer.footNote')}</Note>

            <MyExports report={def.id} refreshKey={exportsTick} />
        </div>
    )
}

/** Loads the definition, then renders the viewer keyed by report (fresh hash schema per report). */
export default function ReportViewerPage() {
    const { t } = useTranslation()
    const { id = '' } = useParams()
    const q = useV2Query((signal) => reportsApi.definition(id, signal), [id])
    if (q.loading && !q.data) return <SkeletonLines rows={8} className="py-6" />
    if (q.error || !q.data) {
        return (
            <div data-testid="p4-viewer-error">
                <Link to="/reports" className="mb-3 inline-block text-[12.5px] font-extrabold text-azure-600 hover:underline">
                    {t('p4.hub.title')}
                </Link>
                <ErrorState error={q.error} onRetry={q.reload} describe={(e, tt) => describeReportError(e, tt)} />
            </div>
        )
    }
    return <ReportViewer key={q.data.id} def={q.data} />
}
