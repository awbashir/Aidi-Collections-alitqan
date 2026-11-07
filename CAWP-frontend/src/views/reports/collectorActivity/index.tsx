/**
 * Employee activity report ("تقرير الموظف", `/reports/collector-activity`, derived key
 * `collector_report` = reports_performance | calls_supervise | calls_manage; customer request 2026-11-05).
 *
 * Pick a collector (the caller's scope: SuperAdmin everyone, a supervisor the team, anyone else
 * themselves) and a period (at most 92 days, default the last 30); the server builds the report
 * (daily attendance to the system, answered calls from the PBX records, charts, the daily table and
 * the weekly totals) as a print-ready Arabic page, shown here in a frame. "Download PDF" prints that
 * frame: the browser's "Save as PDF" writes the file, named after the report's title. "Download Word"
 * saves the same report as a .docx (charts as images).
 */
import { useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import Button from '@/components/ui/Button'
import Input from '@/components/ui/Input'
import Select from '@/components/ui/Select'
import { EmptyState, ErrorState, SkeletonLines } from '@/components/v2/States'
import { PageHead } from '@/components/v2/ListParts'
import { Card, Notice } from '@/views/phase5/parts'
import { describeP5Error } from '@/services/v2'
import type { Option, Translate } from '@/services/v2'
import { collectorReportApi } from '@/services/v2/collectorReportApi'
import { useV2Query } from '@/utils/v2/hooks'
import { riyadhToday } from '@/utils/v2/format'

const describe = (e: unknown, t: Translate) => describeP5Error(e, t)

const shift = (day: string, n: number) => {
    const d = new Date(`${day}T00:00:00Z`)
    d.setUTCDate(d.getUTCDate() + n)
    return d.toISOString().slice(0, 10)
}

const CollectorActivityReport = () => {
    const { t } = useTranslation()
    const today = riyadhToday()
    const opts = useV2Query((signal) => collectorReportApi.options(signal), [])
    const [user, setUser] = useState('')
    const [from, setFrom] = useState(shift(today, -29))
    const [to, setTo] = useState(today)
    const [html, setHtml] = useState<string | null>(null)
    const [loading, setLoading] = useState(false)
    const [error, setError] = useState<unknown>(null)
    const frame = useRef<HTMLIFrameElement>(null)

    const options: Option[] = useMemo(
        () =>
            (opts.data?.users || []).map((u) => ({
                value: u.id,
                label: `${u.name || u.user_name || '—'}${u.user_name ? ` (${u.user_name})` : ''}${u.is_active ? '' : ` — ${t('rca.inactive')}`}`,
            })),
        [opts.data, t]
    )
    const days = Math.round((Date.parse(`${to}T00:00:00Z`) - Date.parse(`${from}T00:00:00Z`)) / 86400000) + 1
    const maxDays = opts.data?.max_days ?? 92
    const tooLong = days > maxDays
    const canRun = !!user && from <= to && !tooLong && !loading

    const run = async () => {
        setLoading(true)
        setError(null)
        try {
            setHtml(await collectorReportApi.html({ user_id: user, from, to }))
        } catch (e) {
            setHtml(null)
            setError(e)
        } finally {
            setLoading(false)
        }
    }
    const print = () => frame.current?.contentWindow?.print()
    const [wordBusy, setWordBusy] = useState(false)
    const word = async () => {
        setWordBusy(true)
        setError(null)
        try {
            await collectorReportApi.docx({ user_id: user, from, to })
        } catch (e) {
            setError(e)
        } finally {
            setWordBusy(false)
        }
    }

    return (
        <div data-testid="rca-page" className="flex flex-col gap-4">
            <PageHead title={t('rca.title')} subtitle={t('rca.subtitle')} />
            <Card>
                {opts.error && !opts.data ? (
                    <ErrorState error={opts.error} onRetry={opts.reload} describe={describe} />
                ) : (
                    <div className="flex flex-wrap items-end gap-3" data-testid="rca-filters">
                        <label className="flex w-full max-w-[340px] flex-col gap-1 text-[12.5px]">
                            <span className="text-gray-600">{t('rca.collector')}</span>
                            <Select size="sm" isSearchable isLoading={opts.loading} options={options}
                                placeholder={t('rca.pickCollector')}
                                value={options.find((o) => o.value === user) || null}
                                onChange={(o) => setUser((o as Option | null)?.value || '')} />
                        </label>
                        <label className="flex flex-col gap-1 text-[12.5px]">
                            <span className="text-gray-600">{t('rca.from')}</span>
                            <Input size="sm" type="date" value={from} max={to} onChange={(e) => setFrom(e.target.value)} className="w-[160px]" />
                        </label>
                        <label className="flex flex-col gap-1 text-[12.5px]">
                            <span className="text-gray-600">{t('rca.to')}</span>
                            <Input size="sm" type="date" value={to} min={from} max={today} onChange={(e) => setTo(e.target.value)} className="w-[160px]" />
                        </label>
                        <Button size="sm" variant="solid" loading={loading} disabled={!canRun} onClick={run} data-testid="rca-run">
                            {t('rca.show')}
                        </Button>
                        <Button size="sm" disabled={!html || loading} onClick={print} data-testid="rca-pdf">
                            {t('rca.pdf')}
                        </Button>
                        <Button size="sm" loading={wordBusy} disabled={!canRun || wordBusy} onClick={word} data-testid="rca-word">
                            {t('rca.word')}
                        </Button>
                    </div>
                )}
                {tooLong && <Notice tone="warning" className="mt-3">{t('rca.tooLong', { days: maxDays })}</Notice>}
                <div className="mt-2 text-[12px] text-gray-600">{t('rca.hint')}</div>
            </Card>

            {error ? (
                <ErrorState error={error} onRetry={run} describe={describe} />
            ) : loading && !html ? (
                <SkeletonLines rows={10} />
            ) : html ? (
                <div className="overflow-hidden rounded-lg border border-gray-300 bg-white dark:border-night-border">
                    <iframe ref={frame} title={t('rca.title') as string} srcDoc={html} className="h-[1200px] w-full border-0"
                        data-testid="rca-frame" />
                </div>
            ) : (
                <EmptyState title={t('rca.empty')} />
            )}
        </div>
    )
}

export default CollectorActivityReport
