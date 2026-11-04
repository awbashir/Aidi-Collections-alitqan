/**
 * Self-service BI (`/reports/bi`, key `reports_bi`; customer feedback 2026-11-04,
 * docs/report-builder-plan.md §1.1). Report center, Recovery & team.
 *
 * Pick a dataset, then drag its fields into Rows, Columns, Values and Filters (or use each field's
 * add buttons: they work by click, keyboard and touch). The API builds the SQL from its own catalog,
 * applies the caller's client scope, and returns a pivot with exact row, column and grand totals;
 * the table, the chart (bar, line or pie) and the Excel export show the same numbers. Layouts can be
 * saved (private or shared) and loaded. Dataset, layout, chart type and the loaded report live in
 * the URL hash. "Approved collected" never includes pending, rejected or reversed money.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import classNames from 'classnames'
import { HiOutlineDownload, HiOutlineSave, HiOutlineX, HiOutlineFilter, HiOutlinePlus, HiOutlineSearch } from 'react-icons/hi'
import Button from '@/components/ui/Button'
import Checkbox from '@/components/ui/Checkbox'
import Input from '@/components/ui/Input'
import ActionDialog, { DialogField } from '@/components/v2/ActionDialog'
import { PageHead } from '@/components/v2/ListParts'
import { EmptyState, ErrorState, InlineError, SkeletonLines } from '@/components/v2/States'
import { Notice } from '@/views/phase5/parts'
import { Segmented } from '@/views/phase1/followUps/parts'
import useHashState, { hashCodec } from '@/utils/hooks/useHashState'
import type { HashSchema } from '@/utils/hooks/useHashState'
import { useV2Query } from '@/utils/v2/hooks'
import { riyadhToday } from '@/utils/v2/format'
import { biApi } from '@/services/v2/reportBuilderApi'
import type {
    BiDataset, BiDimension, BiFilter, BiFilterOp, BiResult, BiSpec, ChartKind, Grain, SavedBiReport, ZoneField,
} from '@/services/v2/reportBuilderApi'
import { Chip, describeBuilderError, pick } from '../shared'
import { PivotChart, PivotTable } from './PivotView'

type Zone = 'rows' | 'columns' | 'values' | 'filters'
type DragPayload = { kind: 'dim' | 'measure'; key: string; from?: Zone; index?: number }
type HashState = { ds: string | undefined; chart: ChartKind; spec: string | undefined; rep: string | undefined }

const DRAG_TYPE = 'application/x-rb-field'
const CHARTS: ChartKind[] = ['bar', 'line', 'pie', 'table']
const GRAINS: Grain[] = ['day', 'week', 'month', 'quarter', 'year']
const LIMITS: Record<Zone, number> = { rows: 3, columns: 2, values: 8, filters: 12 }

const schema: HashSchema<HashState> = {
    ds: { key: 'ds', codec: hashCodec.text({ maxLength: 40 }), default: undefined },
    chart: { key: 'chart', codec: hashCodec.oneOf(CHARTS), default: 'bar' },
    spec: { key: 'spec', codec: hashCodec.text({ maxLength: 6000 }), default: undefined, history: 'replace' },
    rep: { key: 'rep', codec: hashCodec.text({ maxLength: 40 }), default: undefined },
}

const monthStart = () => `${riyadhToday().slice(0, 8)}01`

function defaultSpec(ds: BiDataset): BiSpec {
    const first = ds.dimensions.find((d) => d.type === 'text')
    return {
        dataset: ds.key,
        rows: first ? [{ field: first.key }] : [],
        columns: [],
        values: ds.measures.length ? [ds.measures[0].key] : [],
        filters: [{ field: ds.default_date, op: 'between', value: [monthStart(), riyadhToday()] }],
    }
}

function parseSpec(raw: string | undefined, ds: BiDataset | undefined): BiSpec | null {
    if (!raw || !ds) return null
    try {
        const s = JSON.parse(raw) as BiSpec
        if (s && s.dataset === ds.key && Array.isArray(s.rows) && Array.isArray(s.values)) {
            return { dataset: s.dataset, rows: s.rows || [], columns: s.columns || [], values: s.values || [], filters: s.filters || [] }
        }
    } catch {
        /* a hand-edited hash: fall back to the default layout */
    }
    return null
}

const readPayload = (e: React.DragEvent): DragPayload | null => {
    try {
        return JSON.parse(e.dataTransfer.getData(DRAG_TYPE)) as DragPayload
    } catch {
        return null
    }
}

/* ------------------------------------------------------------------ filter editor */
function FilterEditor({ ds, filter, onChange }: { ds: BiDataset; filter: BiFilter; onChange: (f: BiFilter) => void }) {
    const { t } = useTranslation()
    const dim = ds.dimensions.find((d) => d.key === filter.field) as BiDimension
    const [q, setQ] = useState('')
    const needsValues = dim.type === 'text' || dim.type === 'int'
    const listing = filter.op === 'in' || filter.op === 'not_in'
    const values = useV2Query(
        needsValues && listing ? (signal) => biApi.values(ds.key, dim.key, q || undefined, signal) : null,
        [ds.key, dim.key, q, needsValues, listing]
    )
    const ops: BiFilterOp[] =
        dim.type === 'text' ? ['in', 'not_in', 'contains', 'is_empty', 'not_empty']
        : dim.type === 'date' ? ['between', 'is_empty', 'not_empty']
        : dim.type === 'bool' ? ['eq']
        : ['between', 'in', 'is_empty', 'not_empty']
    const selected = (Array.isArray(filter.value) ? filter.value : []) as (string | number | null)[]
    const toggle = (v: string | number) =>
        onChange({ ...filter, value: selected.includes(v) ? selected.filter((x) => x !== v) : [...selected, v] })

    return (
        <div className="mt-2 w-full rounded-md border border-gray-300 bg-white p-3 dark:border-night-border dark:bg-night-card" data-testid="rb-bi-filter-editor">
            <div className="mb-2 flex flex-wrap gap-1">
                {ops.map((op) => (
                    <button key={op} type="button"
                            className={classNames('rounded-md border px-2 py-0.5 text-[12px] font-bold',
                                filter.op === op ? 'border-navy-600 bg-navy-600 text-white' : 'border-gray-300 text-gray-700 dark:border-night-border dark:text-night-body')}
                            onClick={() => onChange({ field: filter.field, op,
                                value: op === 'between' ? [null, null] : op === 'eq' ? true : op === 'contains' ? '' : op === 'in' || op === 'not_in' ? [] : undefined })}>
                        {t(`rb.bi.op.${op}`)}
                    </button>
                ))}
            </div>
            {filter.op === 'between' && (
                <div className="flex flex-wrap items-center gap-2">
                    {[0, 1].map((i) => (
                        <Input key={i} size="sm" className="w-40" type={dim.type === 'date' ? 'date' : 'number'}
                               value={String((filter.value as (string | null)[])?.[i] ?? '')}
                               aria-label={String(t(i ? 'rb.bi.to' : 'rb.bi.from'))}
                               placeholder={String(t(i ? 'rb.bi.to' : 'rb.bi.from'))}
                               onChange={(e) => {
                                   const v = [...((filter.value as (string | null)[]) || [null, null])]
                                   v[i] = e.target.value || null
                                   onChange({ ...filter, value: v })
                               }} />
                    ))}
                </div>
            )}
            {filter.op === 'eq' && (
                <Segmented<'true' | 'false'> value={filter.value ? 'true' : 'false'} label={String(t('rb.bi.value'))}
                    options={[{ value: 'true', label: t('rb.yes') }, { value: 'false', label: t('rb.no') }]}
                    onChange={(v) => onChange({ ...filter, value: v === 'true' })} />
            )}
            {filter.op === 'contains' && (
                <Input size="sm" value={String(filter.value || '')} maxLength={200} placeholder={String(t('rb.bi.containsHint'))}
                       onChange={(e) => onChange({ ...filter, value: e.target.value })} />
            )}
            {listing && (
                <div>
                    <Input size="sm" prefix={<HiOutlineSearch className="text-gray-500" />} value={q} maxLength={100}
                           placeholder={String(t('rb.bi.searchValues'))} onChange={(e) => setQ(e.target.value)} />
                    <div className="mt-2 max-h-48 overflow-y-auto">
                        {values.loading && !values.data ? <SkeletonLines rows={3} /> : null}
                        {values.error ? <InlineError error={values.error} describe={describeBuilderError} /> : null}
                        {[...new Set([...selected.filter((v) => v !== null), ...(values.data?.items || []).map((i) => i.value)])].map((v) => (
                            <label key={String(v)} className="flex items-center gap-2 py-0.5 text-[12.5px]">
                                <Checkbox checked={selected.includes(v as string | number)} onChange={() => toggle(v as string | number)} />
                                <span className="truncate">{String(v)}</span>
                                <span className="ms-auto text-[11px] text-gray-500 tabular-nums">
                                    {values.data?.items.find((i) => i.value === v)?.count ?? ''}
                                </span>
                            </label>
                        ))}
                    </div>
                </div>
            )}
        </div>
    )
}

function filterSummary(f: BiFilter, t: (k: string, o?: object) => string): string {
    const v = f.value
    if (f.op === 'between' && Array.isArray(v)) return `${v[0] || '…'} → ${v[1] || '…'}`
    if ((f.op === 'in' || f.op === 'not_in') && Array.isArray(v)) {
        const head = v.slice(0, 2).map((x) => String(x ?? t('rb.empty'))).join('، ')
        return `${t(`rb.bi.op.${f.op}`)} ${head}${v.length > 2 ? ` +${v.length - 2}` : ''}`
    }
    if (f.op === 'eq') return t(v ? 'rb.yes' : 'rb.no')
    if (f.op === 'contains') return `${t('rb.bi.op.contains')} "${v || ''}"`
    return t(`rb.bi.op.${f.op}`)
}

/* ------------------------------------------------------------------ page */
const BiPage = () => {
    const { t, i18n } = useTranslation()
    const ar = (i18n.language || 'ar').startsWith('ar')
    const { state, setState } = useHashState(schema)
    const meta = useV2Query((signal) => biApi.datasets(signal), [])
    const saved = useV2Query((signal) => biApi.reports(signal), [])
    const datasets = meta.data?.items || []
    const ds = datasets.find((d) => d.key === state.ds) || datasets[0]
    const spec: BiSpec | null = useMemo(() => (ds ? parseSpec(state.spec, ds) || defaultSpec(ds) : null), [ds, state.spec])
    const setSpec = useCallback((s: BiSpec) => setState({ spec: JSON.stringify(s) }), [setState])

    const [fieldQ, setFieldQ] = useState('')
    const [openFilter, setOpenFilter] = useState<number | null>(null)
    const [over, setOver] = useState<Zone | null>(null)
    const [result, setResult] = useState<BiResult | null>(null)
    const [running, setRunning] = useState(false)
    const [error, setError] = useState<unknown>(null)
    const [exporting, setExporting] = useState(false)
    const [saveOpen, setSaveOpen] = useState(false)
    const [saveName, setSaveName] = useState('')
    const [saveShared, setSaveShared] = useState(false)
    const [savedError, setSavedError] = useState<unknown>(null)
    const seq = useRef(0)

    const loaded = (saved.data?.items || []).find((r) => r.id === state.rep)

    /* ---------- zone edits */
    const dimLabel = (key: string) => pick(ds?.dimensions.find((d) => d.key === key)?.labels, ar) || key
    const measureLabel = (key: string) => pick(ds?.measures.find((m) => m.key === key)?.labels, ar) || key

    const place = (p: DragPayload, to: Zone, at?: number) => {
        if (!spec || !ds) return
        const next: BiSpec = { ...spec, rows: [...spec.rows], columns: [...spec.columns], values: [...spec.values], filters: [...spec.filters] }
        if (p.kind === 'measure' && to !== 'values') return
        if (p.kind === 'dim' && to === 'values') return
        // take it out of where it was (a move inside or between zones)
        if (p.from && p.index !== undefined) {
            if (p.from === 'values') next.values.splice(p.index, 1)
            else if (p.from === 'filters') next.filters.splice(p.index, 1)
            else next[p.from].splice(p.index, 1)
        }
        if (to === 'values') {
            if (next.values.includes(p.key) || next.values.length >= LIMITS.values) return setSpec(next)
            next.values.splice(at ?? next.values.length, 0, p.key)
        } else if (to === 'filters') {
            if (next.filters.length >= LIMITS.filters) return setSpec(next)
            const dim = ds.dimensions.find((d) => d.key === p.key)
            if (!dim) return
            const f: BiFilter = dim.type === 'date' ? { field: dim.key, op: 'between', value: [monthStart(), riyadhToday()] }
                : dim.type === 'bool' ? { field: dim.key, op: 'eq', value: true }
                : { field: dim.key, op: 'in', value: [] }
            next.filters.splice(at ?? next.filters.length, 0, f)
            setOpenFilter(at ?? next.filters.length - 1)
        } else {
            const dim = ds.dimensions.find((d) => d.key === p.key)
            if (!dim) return
            const zf: ZoneField = { field: dim.key, grain: dim.type === 'date' ? 'month' : undefined }
            // the same field cannot be on both axes (except a date at another grain)
            const clash = [...next.rows, ...next.columns].some((z) => z.field === zf.field && (z.grain || null) === (zf.grain || null))
            if (clash || next[to].length >= LIMITS[to]) return setSpec(next)
            next[to].splice(at ?? next[to].length, 0, zf)
        }
        setSpec(next)
    }

    const removeAt = (zone: Zone, i: number) => {
        if (!spec) return
        const next: BiSpec = { ...spec, rows: [...spec.rows], columns: [...spec.columns], values: [...spec.values], filters: [...spec.filters] }
        if (zone === 'values') next.values.splice(i, 1)
        else if (zone === 'filters') next.filters.splice(i, 1)
        else next[zone].splice(i, 1)
        if (zone === 'filters') setOpenFilter(null)
        setSpec(next)
    }

    const setGrain = (zone: 'rows' | 'columns', i: number, grain: Grain) => {
        if (!spec) return
        const z = [...spec[zone]]
        z[i] = { ...z[i], grain }
        setSpec({ ...spec, [zone]: z })
    }

    const swapAxes = () => spec && setSpec({ ...spec, rows: spec.columns.slice(0, LIMITS.rows), columns: spec.rows.slice(0, LIMITS.columns) })

    /* ---------- run (debounced; a newer run wins) */
    const specKey = spec ? JSON.stringify(spec) : ''
    const runnable = !!spec && spec.values.length > 0 && spec.filters.every((f) => !((f.op === 'in' || f.op === 'not_in') && Array.isArray(f.value) && f.value.length === 0))
    const run = useCallback(async () => {
        if (!spec || !runnable) return
        const mine = ++seq.current
        setRunning(true)
        setError(null)
        try {
            const r = await biApi.query(spec)
            if (mine === seq.current) setResult(r)
        } catch (e) {
            if (mine === seq.current) setError(e)
        } finally {
            if (mine === seq.current) setRunning(false)
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [specKey, runnable])
    useEffect(() => {
        if (!runnable) return
        const id = setTimeout(run, 450)
        return () => clearTimeout(id)
    }, [run, runnable])

    const doExport = async () => {
        if (!spec) return
        setExporting(true)
        setError(null)
        try {
            await biApi.exportXlsx(spec, ar ? 'ar' : 'en')
        } catch (e) {
            setError(e)
        } finally {
            setExporting(false)
        }
    }

    const loadReport = (r: SavedBiReport) => {
        const { chart, ...s } = r.spec
        setResult(null)
        setState({ ds: s.dataset, spec: JSON.stringify(s), chart: chart || 'bar', rep: r.id })
    }

    const updateLoaded = async () => {
        if (!loaded || !spec) return
        setSavedError(null)
        try {
            await biApi.update(loaded.id, { spec, chart: state.chart, expected_revision: loaded.revision })
            saved.reload()
        } catch (e) {
            setSavedError(e)
        }
    }

    const removeReport = async (r: SavedBiReport) => {
        if (!window.confirm(String(t('rb.bi.confirmDelete', { name: r.name })))) return
        setSavedError(null)
        try {
            await biApi.remove(r.id)
            if (state.rep === r.id) setState({ rep: undefined })
            saved.reload()
        } catch (e) {
            setSavedError(e)
        }
    }

    /* ---------- render */
    if (meta.loading && !meta.data) return <SkeletonLines rows={10} />
    if (meta.error && !meta.data) return <ErrorState error={meta.error} onRetry={meta.reload} describe={describeBuilderError} />
    if (!ds || !spec) return <EmptyState title={t('rb.bi.noDatasets')} />

    const needle = fieldQ.trim().toLocaleLowerCase()
    const match = (l: { en: string; ar: string }) => !needle || l.en.toLowerCase().includes(needle) || l.ar.includes(needle)

    const dropProps = (zone: Zone, at?: number) => ({
        onDragOver: (e: React.DragEvent) => {
            if (!e.dataTransfer.types.includes(DRAG_TYPE)) return
            e.preventDefault()
            e.stopPropagation()
            setOver(zone)
        },
        onDragLeave: () => setOver((z) => (z === zone ? null : z)),
        onDrop: (e: React.DragEvent) => {
            e.preventDefault()
            e.stopPropagation()
            setOver(null)
            const p = readPayload(e)
            if (p) place(p, zone, at)
        },
    })
    const dragProps = (p: DragPayload) => ({
        draggable: true,
        onDragStart: (e: React.DragEvent) => {
            e.dataTransfer.setData(DRAG_TYPE, JSON.stringify(p))
            e.dataTransfer.effectAllowed = 'move'
        },
    })

    const zoneBox = (zone: Zone, hint: string, children: React.ReactNode) => (
        <div
            {...dropProps(zone)}
            data-testid={`rb-bi-zone-${zone}`}
            className={classNames(
                'min-h-[64px] rounded-lg border-2 border-dashed p-2 transition-colors',
                over === zone ? 'border-azure-500 bg-info-tint dark:bg-night-raised' : 'border-gray-300 dark:border-night-border'
            )}
        >
            <div className="mb-1.5 flex items-center justify-between">
                <span className="text-eyebrow">{t(`rb.bi.zone.${zone}`)}</span>
                <span className="text-[11px] text-gray-500 tabular-nums">
                    {(zone === 'values' ? spec.values.length : zone === 'filters' ? spec.filters.length : spec[zone].length)}/{LIMITS[zone]}
                </span>
            </div>
            <div className="flex flex-wrap gap-1.5">{children}</div>
            {(zone === 'values' ? spec.values.length : zone === 'filters' ? spec.filters.length : spec[zone].length) === 0 && (
                <div className="text-[12px] text-gray-500">{hint}</div>
            )}
        </div>
    )

    const removeBtn = (zone: Zone, i: number, label: string) => (
        <button type="button" className="rounded p-0.5 hover:bg-black/10" aria-label={String(t('rb.bi.remove', { name: label }))}
                onClick={() => removeAt(zone, i)}>
            <HiOutlineX />
        </button>
    )

    const axisChips = (zone: 'rows' | 'columns') =>
        spec[zone].map((z, i) => {
            const dim = ds.dimensions.find((d) => d.key === z.field)
            return (
                <Chip key={`${z.field}-${z.grain}-${i}`} {...dragProps({ kind: 'dim', key: z.field, from: zone, index: i })} {...dropProps(zone, i)}
                      className="cursor-grab" data-testid="rb-bi-chip">
                    <span className="truncate">{dimLabel(z.field)}</span>
                    {dim?.type === 'date' && (
                        <select className="rounded border border-gray-300 bg-white px-1 text-[11.5px] dark:border-night-border dark:bg-night-card"
                                value={z.grain || 'day'} aria-label={String(t('rb.bi.grain'))}
                                onChange={(e) => setGrain(zone, i, e.target.value as Grain)}>
                            {GRAINS.map((g) => <option key={g} value={g}>{t(`rb.bi.grains.${g}`)}</option>)}
                        </select>
                    )}
                    {removeBtn(zone, i, dimLabel(z.field))}
                </Chip>
            )
        })

    return (
        <div data-testid="rb-bi">
            <PageHead
                title={t('rb.bi.title')}
                subtitle={t('rb.bi.subtitle')}
                actions={
                    <>
                        <Button size="sm" icon={<HiOutlineDownload />} loading={exporting} disabled={!runnable} onClick={doExport} data-testid="rb-bi-export">
                            {t('rb.exportExcel')}
                        </Button>
                        {loaded?.mine && (
                            <Button size="sm" onClick={updateLoaded} data-testid="rb-bi-update">{t('rb.bi.saveChanges')}</Button>
                        )}
                        <Button size="sm" variant="solid" icon={<HiOutlineSave />} disabled={!runnable}
                                onClick={() => { setSaveName(loaded && loaded.mine ? `${loaded.name}` : ''); setSaveShared(false); setSaveOpen(true) }}
                                data-testid="rb-bi-save">
                            {t('rb.bi.saveAs')}
                        </Button>
                    </>
                }
            />
            <Notice tone="info" className="mb-4">{t('rb.bi.scopeNote')}</Notice>

            <div className="grid grid-cols-1 gap-4 lg:grid-cols-[260px_minmax(0,1fr)]">
                {/* ---------- field list */}
                <aside className="min-w-0 rounded-lg border border-gray-300 bg-white p-3 dark:border-night-border dark:bg-night-card" data-testid="rb-bi-fields">
                    <label className="text-eyebrow mb-1 block" htmlFor="rb-bi-ds">{t('rb.bi.dataset')}</label>
                    <select id="rb-bi-ds" data-testid="rb-bi-dataset"
                            className="mb-3 h-9 w-full rounded-md border border-gray-300 bg-white px-2 text-[13px] font-bold dark:border-night-border dark:bg-night-card dark:text-gray-100"
                            value={ds.key}
                            onChange={(e) => { setResult(null); setState({ ds: e.target.value, spec: undefined, rep: undefined }) }}>
                        {datasets.map((d) => <option key={d.key} value={d.key}>{pick(d.labels, ar)}</option>)}
                    </select>
                    <p className="mb-3 text-[12px] text-gray-600">{pick(ds.description, ar)}</p>
                    <Input size="sm" prefix={<HiOutlineSearch className="text-gray-500" />} value={fieldQ} maxLength={60}
                           placeholder={String(t('rb.bi.searchFields'))} onChange={(e) => setFieldQ(e.target.value)} />
                    <div className="text-eyebrow mb-1 mt-3">{t('rb.bi.dimensions')}</div>
                    <ul className="space-y-1">
                        {ds.dimensions.filter((d) => match(d.labels)).map((d) => (
                            <li key={d.key} {...dragProps({ kind: 'dim', key: d.key })} title={pick(d.description, ar)}
                                className="group flex cursor-grab items-center gap-1 rounded-md px-1.5 py-1 text-[12.5px] hover:bg-gray-50 dark:hover:bg-night-raised"
                                data-testid="rb-bi-field" data-field={d.key}>
                                <span className="me-auto truncate font-bold text-navy-600 dark:text-gray-100">{pick(d.labels, ar)}</span>
                                {(['rows', 'columns', 'filters'] as Zone[]).map((z) => (
                                    <button key={z} type="button" title={String(t(`rb.bi.addTo.${z}`))} aria-label={`${t(`rb.bi.addTo.${z}`)}: ${pick(d.labels, ar)}`}
                                            className="rounded border border-gray-300 px-1 text-[10.5px] font-extrabold text-gray-600 hover:border-azure-500 hover:text-azure-600 dark:border-night-border"
                                            onClick={() => place({ kind: 'dim', key: d.key }, z)}>
                                        {z === 'filters' ? <HiOutlineFilter className="inline" /> : t(`rb.bi.short.${z}`)}
                                    </button>
                                ))}
                            </li>
                        ))}
                    </ul>
                    <div className="text-eyebrow mb-1 mt-3">{t('rb.bi.measures')}</div>
                    <ul className="space-y-1">
                        {ds.measures.filter((m) => match(m.labels)).map((m) => (
                            <li key={m.key} {...dragProps({ kind: 'measure', key: m.key })}
                                className="flex cursor-grab items-center gap-1 rounded-md px-1.5 py-1 text-[12.5px] hover:bg-gray-50 dark:hover:bg-night-raised"
                                data-testid="rb-bi-measure" data-field={m.key}>
                                <span className="me-auto truncate font-bold text-gray-800 dark:text-night-body">{pick(m.labels, ar)}</span>
                                <button type="button" title={String(t('rb.bi.addTo.values'))} aria-label={`${t('rb.bi.addTo.values')}: ${pick(m.labels, ar)}`}
                                        className="rounded border border-gray-300 px-1 text-gray-600 hover:border-azure-500 hover:text-azure-600 dark:border-night-border"
                                        onClick={() => place({ kind: 'measure', key: m.key }, 'values')}>
                                    <HiOutlinePlus className="inline" />
                                </button>
                            </li>
                        ))}
                    </ul>

                    {/* ---------- saved reports */}
                    <div className="text-eyebrow mb-1 mt-5">{t('rb.bi.saved')}</div>
                    {savedError ? <InlineError error={savedError} describe={describeBuilderError} onReload={saved.reload} /> : null}
                    {saved.loading && !saved.data ? <SkeletonLines rows={2} /> : null}
                    {(saved.data?.items || []).length === 0 && !saved.loading && <div className="text-[12px] text-gray-500">{t('rb.bi.noSaved')}</div>}
                    <ul className="space-y-1" data-testid="rb-bi-saved">
                        {(saved.data?.items || []).map((r) => (
                            <li key={r.id} className={classNames('flex items-center gap-1 rounded-md px-1.5 py-1 text-[12.5px]',
                                state.rep === r.id ? 'bg-info-tint dark:bg-night-raised' : 'hover:bg-gray-50 dark:hover:bg-night-raised')}>
                                <button type="button" className="me-auto min-w-0 truncate text-start font-bold text-azure-600 hover:underline" onClick={() => loadReport(r)}
                                        title={r.mine ? undefined : String(t('rb.sharedBy', { name: r.owner_name || '—' }))}>
                                    {r.name}
                                </button>
                                {r.shared && <span className="rounded-xs border border-gray-300 px-1 text-[10px] text-gray-600 dark:border-night-border">{t('rb.shared')}</span>}
                                {r.mine && (
                                    <button type="button" className="rounded p-0.5 text-gray-500 hover:text-danger" aria-label={String(t('rb.bi.remove', { name: r.name }))}
                                            onClick={() => removeReport(r)}>
                                        <HiOutlineX />
                                    </button>
                                )}
                            </li>
                        ))}
                    </ul>
                </aside>

                {/* ---------- zones + result */}
                <div className="min-w-0">
                    <div className="mb-3 grid grid-cols-1 gap-3 md:grid-cols-2">
                        {zoneBox('rows', String(t('rb.bi.dropHint')), axisChips('rows'))}
                        {zoneBox('columns', String(t('rb.bi.dropHint')), axisChips('columns'))}
                        {zoneBox('values', String(t('rb.bi.dropMeasureHint')), <>
                            {spec.values.map((k, i) => (
                                <Chip key={k} tone="measure" {...dragProps({ kind: 'measure', key: k, from: 'values', index: i })} {...dropProps('values', i)}
                                      className="cursor-grab" data-testid="rb-bi-chip">
                                    <span className="truncate">{measureLabel(k)}</span>
                                    {removeBtn('values', i, measureLabel(k))}
                                </Chip>
                            ))}
                        </>)}
                        {zoneBox('filters', String(t('rb.bi.dropFilterHint')), <>
                            {spec.filters.map((f, i) => (
                                <div key={`${f.field}-${i}`} className={classNames(openFilter === i && 'w-full')}>
                                    <Chip tone="filter" {...dragProps({ kind: 'dim', key: f.field, from: 'filters', index: i })} data-testid="rb-bi-filter">
                                        <button type="button" className="truncate text-start" onClick={() => setOpenFilter(openFilter === i ? null : i)}
                                                aria-expanded={openFilter === i}>
                                            {dimLabel(f.field)}: <span className="font-normal">{filterSummary(f, t)}</span>
                                        </button>
                                        {removeBtn('filters', i, dimLabel(f.field))}
                                    </Chip>
                                    {openFilter === i && (
                                        <FilterEditor ds={ds} filter={f} onChange={(nf) => {
                                            const filters = [...spec.filters]
                                            filters[i] = nf
                                            setSpec({ ...spec, filters })
                                        }} />
                                    )}
                                </div>
                            ))}
                        </>)}
                    </div>

                    <div className="mb-3 flex flex-wrap items-center gap-2">
                        <Segmented<ChartKind> value={state.chart} label={String(t('rb.bi.chartType'))} testId="rb-bi-chart-kind"
                            options={CHARTS.map((c) => ({ value: c, label: t(`rb.bi.chart.${c}`) }))} onChange={(chart) => setState({ chart })} />
                        <Button size="sm" onClick={swapAxes} disabled={!spec.rows.length && !spec.columns.length}>{t('rb.bi.swap')}</Button>
                        <Button size="sm" onClick={run} loading={running} disabled={!runnable} data-testid="rb-bi-run">{t('rb.run')}</Button>
                        {result && (
                            <span className="text-[12px] text-gray-600 tabular-nums" data-testid="rb-bi-meta">
                                {t('rb.bi.meta', { rows: result.row_keys.length, ms: result.ms })}
                            </span>
                        )}
                        {loaded && <span className="text-[12px] text-gray-600">{t('rb.bi.loaded', { name: loaded.name })}</span>}
                    </div>

                    {!spec.values.length && <Notice tone="neutral" className="mb-3">{t('rb.bi.needValue')}</Notice>}
                    {error ? <InlineError error={error} describe={describeBuilderError} /> : null}
                    {result?.truncated && <Notice tone="warning" className="mb-3">{t('rb.bi.truncated')}</Notice>}
                    {result?.columns_truncated && <Notice tone="warning" className="mb-3">{t('rb.bi.columnsTruncated')}</Notice>}

                    {result && result.dataset === ds.key ? (
                        <div className={classNames('space-y-4 transition-opacity', running && 'opacity-60')}>
                            {result.row_keys.length === 0 && result.column_keys.length === 0 && (result.fields.rows.length || result.fields.columns.length) ? (
                                <EmptyState title={t('rb.bi.noRows')} hint={t('rb.bi.noRowsHint')} />
                            ) : (
                                <>
                                    {state.chart !== 'table' && (
                                        <div className="rounded-lg border border-gray-300 bg-white p-3 dark:border-night-border dark:bg-night-card">
                                            <PivotChart result={result} dataset={ds} kind={state.chart} />
                                        </div>
                                    )}
                                    <PivotTable result={result} dataset={ds} />
                                </>
                            )}
                        </div>
                    ) : running ? (
                        <SkeletonLines rows={8} />
                    ) : null}
                    <p className="mt-3 text-[12px] text-gray-600">{t('rb.bi.moneyNote')}</p>
                </div>
            </div>

            <ActionDialog
                isOpen={saveOpen}
                title={t('rb.bi.saveTitle')}
                onClose={() => setSaveOpen(false)}
                canSubmit={saveName.trim().length > 0 && saveName.trim().length <= 120}
                confirmLabel={t('rb.save')}
                describeError={describeBuilderError}
                testId="rb-bi-save-dialog"
                onSubmit={async (key) => {
                    const r = await biApi.save({ name: saveName.trim(), spec, chart: state.chart, shared: saveShared }, key)
                    setState({ rep: r.id })
                    saved.reload()
                }}
            >
                <DialogField label={t('rb.name')} required>
                    <Input size="sm" value={saveName} maxLength={120} onChange={(e) => setSaveName(e.target.value)} data-testid="rb-bi-save-name" />
                </DialogField>
                <Checkbox checked={saveShared} onChange={(c: boolean) => setSaveShared(c)}>{t('rb.shareHint')}</Checkbox>
            </ActionDialog>
        </div>
    )
}

export default BiPage

