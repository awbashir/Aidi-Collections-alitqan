/**
 * The report table, driven by `definition.columns`:
 * - cells by column type (cells.tsx); numbers trailing-aligned, currency in the header;
 * - sort toggles on the columns that have a sort (server-side, with the
 *   definition's id tie-breaker, so paging is stable), plus a "Sort by" select
 *   for sorts without a column;
 * - sectioned reports (RPT-18, RPT-24: a `section` column) render a group row
 *   per section; for RPT-18/23/24 columns empty on every row of the page are
 *   collapsed by default (toggle);
 * - RPT-25 recording links only for holders of a recording key; they use the
 *   existing `/cdr/recording` route (download / play), never a new one;
 * - paging, page size, the row range and the snapshot time in the footer.
 */
import classNames from 'classnames'
import { Fragment, useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { HiArrowDown, HiArrowUp, HiOutlineSwitchVertical } from 'react-icons/hi'
import { Download, Lock, Play, Square } from 'lucide-react'
import Table from '@/components/ui/Table'
import Button from '@/components/ui/Button'
import { EmptyState, ErrorState, SkeletonRows } from '@/components/v2/States'
import { PagerFooter, TableFrame } from '@/components/v2/ListParts'
import { RiyadhTime } from '@/components/v2/Display'
import { apiGetCdrRecordingPath } from '@/services/ClientsService'
import { describeReportError } from '@/services/v2'
import type { ReportColumnDef, ReportDefinition, ReportRow, ReportRows } from '@/services/v2'
import { ReportCellView } from './cells'
import {
    COLLAPSE_EMPTY_DEFAULT,
    PAGE_SIZES,
    RECORDING_KEYS,
    isNumericColumn,
    isSectioned,
    mayViewProtected,
    pick,
    sortForColumn,
} from '../reportUtils'
import type { ReactNode } from 'react'

const { THead, TBody, Tr, Th, Td } = Table

type Props = {
    def: ReportDefinition
    data: ReportRows | undefined
    loading: boolean
    error: unknown
    onRetry: () => void
    sort: string
    onSort: (sort: string) => void
    page: number
    size: number
    onPage: (page: number) => void
    onSize: (size: number) => void
    perms: Record<string, boolean>
    toolbar?: ReactNode
    paramLabel: (param: string) => string
}

/* -------------------------------------------------------------- recordings */

/** Download / play through the unchanged legacy route, as the case call-records tab does. */
function RecordingCell({ row, perms }: { row: ReportRow; perms: Record<string, boolean> }) {
    const { t } = useTranslation()
    const file = typeof row.recording_file === 'string' ? row.recording_file : null
    const canDownload = !!perms.clints_callsRecords_download
    const canPlay = !!perms.clints_callsRecords_play
    const [playing, setPlaying] = useState<HTMLAudioElement | null>(null)
    const [busy, setBusy] = useState(false)
    const [failed, setFailed] = useState(false)
    useEffect(() => () => playing?.pause(), [playing])

    if (!RECORDING_KEYS.some((k) => perms[k])) {
        return row.has_recording ? (
            <span className="inline-flex items-center gap-1 whitespace-nowrap text-[12px] text-gray-600" title={String(t('p4.rec.noKeyTitle'))} data-testid="p4-rec-nokey">
                <Lock aria-hidden size={12} strokeWidth={2} />
                {t('p4.rec.noKey')}
            </span>
        ) : (
            <span className="text-gray-500">—</span>
        )
    }
    if (!file) return <span className="text-gray-500">—</span>

    const fetchBlob = async () => {
        setBusy(true)
        setFailed(false)
        try {
            const res = await apiGetCdrRecordingPath<Blob, { recordingPath: string }>({ recordingPath: file })
            return new Blob([res.data as BlobPart], { type: 'audio/wav' })
        } catch {
            setFailed(true)
            return null
        } finally {
            setBusy(false)
        }
    }
    const download = async () => {
        const blob = await fetchBlob()
        if (!blob) return
        const url = URL.createObjectURL(blob)
        const a = document.createElement('a')
        a.href = url
        a.download = `${String(row.account_number || row.call_id || 'recording')}.wav`
        document.body.appendChild(a)
        a.click()
        a.remove()
        setTimeout(() => URL.revokeObjectURL(url), 10000)
    }
    const play = async () => {
        if (playing) {
            playing.pause()
            setPlaying(null)
            return
        }
        const blob = await fetchBlob()
        if (!blob) return
        const url = URL.createObjectURL(blob)
        const audio = new Audio(url)
        audio.onended = () => {
            setPlaying(null)
            URL.revokeObjectURL(url)
        }
        setPlaying(audio)
        audio.play().catch(() => setFailed(true))
    }
    return (
        <span className="inline-flex items-center gap-1" data-testid="p4-rec-link" data-file={file}>
            {canDownload && (
                <Button size="xs" variant="default" loading={busy} onClick={download} title={String(t('p4.rec.download'))} icon={<Download size={14} />} />
            )}
            {canPlay && (
                <Button
                    size="xs"
                    variant="default"
                    onClick={play}
                    title={String(playing ? t('p4.rec.stop') : t('p4.rec.play'))}
                    icon={playing ? <Square size={14} /> : <Play size={14} />}
                />
            )}
            {failed && <span className="text-[11.5px] text-danger">{t('p4.rec.failed')}</span>}
        </span>
    )
}

/* -------------------------------------------------------------- table */

const firstClickDesc = (col: ReportColumnDef) => ['money', 'int', 'date', 'datetime'].includes(String(col.type))

export default function ReportTable(props: Props) {
    const { def, data, loading, error, onRetry, sort, onSort, page, size, onPage, onSize, perms, toolbar, paramLabel } = props
    const { t, i18n } = useTranslation()
    const ar = (i18n.language || 'ar').startsWith('ar')
    const sectioned = isSectioned(def)
    const [collapse, setCollapse] = useState(COLLAPSE_EMPTY_DEFAULT.has(def.id))
    const sortKey = sort.replace(/^-/, '')
    const desc = sort.startsWith('-')

    const columns = useMemo(() => {
        let cols = def.columns
        if (sectioned) cols = cols.filter((c) => c.key !== 'section')
        if (collapse && data && data.items.length) {
            cols = cols.filter((c) => data.items.some((r) => r[c.key] !== null && r[c.key] !== undefined && r[c.key] !== ''))
        }
        return cols
    }, [def.columns, sectioned, collapse, data])
    const hiddenCount = def.columns.length - (sectioned ? 1 : 0) - columns.length

    const sectionCol = def.columns.find((c) => c.key === 'section')
    const sectionLabel = (v: unknown) => {
        const c = sectionCol?.enum?.find((x) => x.value === v)
        return c ? pick(c.labels, ar) : String(v ?? '')
    }

    const clickSort = (col: ReportColumnDef) => {
        const key = sortForColumn(def, col)
        if (!key) return
        if (sortKey === key) onSort(desc ? key : `-${key}`)
        else onSort(firstClickDesc(col) ? `-${key}` : key)
    }

    const rowsRef = useRef<HTMLDivElement>(null)
    const idCol = def.columns.find((c) => c.type === 'uuid') || def.columns.find((c) => c.key === 'position')

    const sortOptions = def.sorts.flatMap((s) => [
        { value: s.key, label: `${pick(s.labels, ar)} ↑` },
        { value: `-${s.key}`, label: `${pick(s.labels, ar)} ↓` },
    ])

    const bar = (
        <div className="flex w-full flex-wrap items-center justify-between gap-2">
            <div className="flex flex-wrap items-center gap-2">{toolbar}</div>
            <div className="flex flex-wrap items-center gap-2">
                {COLLAPSE_EMPTY_DEFAULT.has(def.id) && (
                    <Button size="xs" variant="plain" onClick={() => setCollapse((c) => !c)} data-testid="p4-collapse-toggle">
                        {collapse ? t('p4.table.showEmpty', { n: hiddenCount }) : t('p4.table.hideEmpty')}
                    </Button>
                )}
                <label className="flex items-center gap-1 text-[12.5px] text-gray-600">
                    <HiOutlineSwitchVertical aria-hidden />
                    <span>{t('p4.table.sortBy')}</span>
                    <select
                        className="h-8 rounded-md border border-[#B8C4CE] bg-white px-2 text-[12.5px] text-gray-800 dark:border-night-border dark:bg-night-card dark:text-gray-100"
                        value={sort}
                        onChange={(e) => onSort(e.target.value)}
                        data-testid="p4-sort-select"
                    >
                        {sortOptions.map((o) => (
                            <option key={o.value} value={o.value}>
                                {o.label}
                            </option>
                        ))}
                    </select>
                </label>
                <label className="flex items-center gap-1 text-[12.5px] text-gray-600">
                    <span>{t('p4.table.pageSize')}</span>
                    <select
                        className="h-8 rounded-md border border-[#B8C4CE] bg-white px-2 text-[12.5px] tabular-nums text-gray-800 dark:border-night-border dark:bg-night-card dark:text-gray-100"
                        value={size}
                        onChange={(e) => onSize(Number(e.target.value))}
                        data-testid="p4-page-size"
                    >
                        {PAGE_SIZES.map((n) => (
                            <option key={n} value={n}>
                                {n}
                            </option>
                        ))}
                    </select>
                </label>
            </div>
        </div>
    )

    const footer = data ? (
        <div>
            <PagerFooter page={page} pageSize={size} total={data.total} onPage={onPage} />
            <div className="flex flex-wrap items-center justify-between gap-2 border-t border-gray-200 bg-gray-50 px-5 py-2 text-[12px] text-gray-600 dark:border-night-border dark:bg-night-page">
                <span data-testid="p4-snapshot">
                    {t('p4.table.snapshot')} <RiyadhTime iso={data.snapshot_at} />
                </span>
                <span className="tabular-nums" data-testid="p4-row-count" data-total={data.total}>
                    {t('p4.table.rowCount', { n: data.total.toLocaleString('en-US') })}
                </span>
            </div>
        </div>
    ) : undefined

    let lastSection: unknown = Symbol('none')
    return (
        <div ref={rowsRef}>
            <TableFrame testId="p4-table" toolbar={bar} footer={footer}>
                {error && !data ? (
                    <ErrorState error={error} onRetry={onRetry} describe={(e, tt) => describeReportError(e, tt, paramLabel)} />
                ) : (
                    <Table>
                        <THead>
                            <Tr>
                                {columns.map((c) => {
                                    const sk = sortForColumn(def, c)
                                    const active = sk !== null && sk === sortKey
                                    const label = (
                                        <>
                                            {pick(c.labels, ar)}
                                            {c.money && c.currency ? ` (${t('p4.sar')})` : ''}
                                            {c.protected && (
                                                <Lock aria-label={String(t('p4.table.protectedCol'))} size={11} strokeWidth={2.2} className="ms-1 inline-block align-[-1px] text-gray-500" />
                                            )}
                                        </>
                                    )
                                    return (
                                        <Th
                                            key={c.key}
                                            className={classNames('whitespace-nowrap', isNumericColumn(c) && 'text-end')}
                                            aria-sort={active ? (desc ? 'descending' : 'ascending') : undefined}
                                            data-col={c.key}
                                        >
                                            {sk ? (
                                                <button
                                                    type="button"
                                                    className={classNames('inline-flex items-center gap-1 uppercase', active && 'text-azure-600')}
                                                    onClick={() => clickSort(c)}
                                                    data-testid={`p4-sort-${c.key}`}
                                                    title={String(t('p4.table.sortTitle'))}
                                                >
                                                    {label}
                                                    {active ? desc ? <HiArrowDown aria-hidden /> : <HiArrowUp aria-hidden /> : null}
                                                </button>
                                            ) : (
                                                label
                                            )}
                                        </Th>
                                    )
                                })}
                            </Tr>
                        </THead>
                        <TBody>
                            {loading && !data ? (
                                <SkeletonRows cols={Math.min(columns.length, 10)} />
                            ) : data && data.items.length > 0 ? (
                                data.items.map((row, i) => {
                                    const newSection = sectioned && row.section !== lastSection
                                    lastSection = row.section
                                    const rowKey = `${page}-${i}-${idCol ? String(row[idCol.key]) : ''}`
                                    return (
                                        <Fragment key={rowKey}>
                                            {newSection && (
                                                <tr className="bg-gray-50 dark:bg-night-raised" data-testid="p4-section-row" data-section={String(row.section)}>
                                                    <td colSpan={columns.length} className="px-5 py-2 text-[12.5px] font-extrabold text-navy-600 dark:text-gray-100">
                                                        {sectionLabel(row.section)}
                                                    </td>
                                                </tr>
                                            )}
                                            <Tr
                                                data-testid="p4-row"
                                                data-section={sectioned ? String(row.section) : undefined}
                                                className={classNames(sectioned && row.section === 'summary' && 'font-extrabold')}
                                            >
                                                {columns.map((c) => (
                                                    <Td key={c.key} className={classNames(isNumericColumn(c) && 'text-end')} data-col={c.key}>
                                                        {c.key === 'recording_url' ? (
                                                            <RecordingCell row={row} perms={perms} />
                                                        ) : (
                                                            <ReportCellView col={c} value={row[c.key]} canSeeProtected={mayViewProtected(c, perms)} />
                                                        )}
                                                    </Td>
                                                ))}
                                            </Tr>
                                        </Fragment>
                                    )
                                })
                            ) : (
                                <tr>
                                    <td colSpan={Math.max(columns.length, 1)}>
                                        <EmptyState title={t('p4.table.emptyTitle')} hint={t('p4.table.emptyHint')} />
                                    </td>
                                </tr>
                            )}
                        </TBody>
                    </Table>
                )}
            </TableFrame>
            {error && data ? (
                <div className="mt-2">
                    <ErrorState error={error} onRetry={onRetry} describe={(e, tt) => describeReportError(e, tt, paramLabel)} />
                </div>
            ) : null}
        </div>
    )
}
