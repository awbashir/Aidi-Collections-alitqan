/**
 * Shared pieces of the call-monitor charts (customer request 2026-10-20): option builders on top
 * of the Aidi ApexCharts defaults, the outcome colours, and the time / duration formatting.
 *
 * `Chart` merges `customOptions` over its defaults ONE level deep (a `chart` / `xaxis` key
 * replaces the default's whole object), so `withDefaults` spreads the default sub-objects back in
 * before the chart's own keys. Functions (formatters, events) survive: only the defaults go
 * through Chart's JSON clone.
 */
import { useConfig } from '@/components/ui/ConfigProvider'
import { apexBarChartDefaultOption } from '@/configs/chart.config'
import { COLORS, COLORS_DARK, COLOR_HIGHLIGHT, COLOR_HIGHLIGHT_DARK } from '@/constants/chart.constant'
import type { ApexOptions } from 'apexcharts'
import type { Direction } from '@/@types/theme'
import type { MonitorOutcome, MonitorUserStats, MonitorWay } from '@/services/v2'

const NESTED = ['chart', 'xaxis', 'yaxis', 'legend', 'tooltip', 'grid', 'plotOptions', 'states', 'dataLabels'] as const

/** The bar defaults' chrome, one level deep, under `over`. */
export function withDefaults(over: ApexOptions, base: ApexOptions = apexBarChartDefaultOption): ApexOptions {
    const out: Record<string, unknown> = { ...over }
    for (const key of NESTED) {
        const b = base[key as keyof ApexOptions]
        const o = over[key as keyof ApexOptions]
        if (b && o && !Array.isArray(b) && !Array.isArray(o)) out[key] = { ...(b as object), ...(o as object) }
    }
    return out as ApexOptions
}

/** The page's theme: dark mode and the writing direction (Chart mirrors the legend under RTL). */
export function useChartTheme() {
    const { mode, direction } = useConfig()
    const dark = mode === 'dark'
    return { dark, direction: direction as Direction, highlight: dark ? COLOR_HIGHLIGHT_DARK : COLOR_HIGHLIGHT }
}

/** Semantic outcome colours (success / warning / lavender / danger / slate) per theme. */
const OUTCOME_LIGHT: Record<MonitorOutcome, string> = {
    answered: '#128A5C',
    no_answer: '#C86A00',
    busy: '#8E86B8',
    failed: '#B3261E',
    unknown: '#B8C4CE',
}
const OUTCOME_DARK: Record<MonitorOutcome, string> = {
    answered: '#3EA87B',
    no_answer: '#D88A2E',
    busy: '#9E95C0',
    failed: '#D4655C',
    unknown: '#565CA6',
}
export const OUTCOMES: MonitorOutcome[] = ['answered', 'no_answer', 'busy', 'failed', 'unknown']
export const outcomeColor = (o: MonitorOutcome, dark: boolean) => (dark ? OUTCOME_DARK : OUTCOME_LIGHT)[o]

/** Direction series: the categorical ramp (Chart lifts it to the dark twins). */
export const WAYS: MonitorWay[] = ['outbound', 'inbound_answered', 'inbound_missed', 'internal']
export const WAY_COLORS = [COLORS[0], COLORS[2], COLORS[4], COLORS[6]]

/** Per-user call mix: answered in success / steel, not answered in warning / lavender, internal slate. */
export const userMixColors = (dark: boolean) =>
    dark
        ? [OUTCOME_DARK.answered, OUTCOME_DARK.no_answer, COLORS_DARK[2], COLORS_DARK[4], COLORS_DARK[6]]
        : [OUTCOME_LIGHT.answered, OUTCOME_LIGHT.no_answer, COLORS[2], COLORS[4], COLORS[6]]

/** A Riyadh wall-clock bucket ("YYYY-MM-DD" or "YYYY-MM-DD HH:00") as a UTC timestamp, so the
 * axis (datetimeUTC) shows the Riyadh time whatever the browser's zone. */
export const bucketTs = (bucket: string) => {
    const [d, h] = bucket.split(' ')
    const [y, m, day] = d.split('-').map(Number)
    return Date.UTC(y, m - 1, day, h ? Number(h.slice(0, 2)) : 0)
}

const pad = (n: number) => String(n).padStart(2, '0')

/** The bucket label of a timestamp from bucketTs. */
export const tsLabel = (ts: number, hour: boolean, withDate = true) => {
    const d = new Date(ts)
    const day = `${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}`
    if (!hour) return day
    const time = `${pad(d.getUTCHours())}:00`
    return withDate ? `${day} ${time}` : time
}

/** m:ss (h:mm:ss over an hour), Western digits. */
export const clock = (seconds: number) => {
    const s = Math.max(0, Math.round(seconds))
    const h = Math.floor(s / 3600)
    const m = Math.floor((s % 3600) / 60)
    const r = s % 60
    return `${h ? `${h}:` : ''}${String(m).padStart(h ? 2 : 1, '0')}:${pad(r)}`
}

/** A share as a whole percentage, null without a denominator (never 0 % of nothing). */
export const pct = (part: number, whole: number) => (whole > 0 ? Math.round((part / whole) * 1000) / 10 : null)

export const agentLabel = (a: { name: string | null; extension: string }) => (a.name ? `${a.name} (${a.extension})` : a.extension)

export const inboundRung = (u: Pick<MonitorUserStats, 'inbound_answered' | 'inbound_missed'>) => u.inbound_answered + u.inbound_missed

/** Weekday names, Sunday first, in the page's language (Asia/Riyadh week). */
export const weekdayNames = (lang: string) => {
    const fmt = new Intl.DateTimeFormat(lang?.startsWith('ar') ? 'ar' : 'en', { weekday: 'long', timeZone: 'UTC' })
    return Array.from({ length: 7 }, (_, i) => fmt.format(new Date(Date.UTC(2026, 9, 18 + i))))   // 2026-10-18 is a Sunday
}

/** Clickable bars / cells show the hand cursor. */
export const CLICKABLE = '[&_.apexcharts-bar-area]:cursor-pointer [&_.apexcharts-heatmap-rect]:cursor-pointer'
