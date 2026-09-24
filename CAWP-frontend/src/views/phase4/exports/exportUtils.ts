/** Plain helpers for the export pieces (kept out of component files for fast refresh). */
import type { StatusTone } from '@/components/shared/StatusPill'
import type { ReportExportStatus, ReportFormat } from '@/services/v2'

export const EXPORT_TONE: Record<ReportExportStatus, StatusTone> = {
    queued: 'info',
    running: 'info',
    retrying: 'warning',
    succeeded: 'success',
    failed: 'danger',
    expired: 'neutral',
}

export const formatLabelKey = (f: ReportFormat) => `p4.format.${f}`

export function formatBytes(n: number | null | undefined): string {
    if (n === null || n === undefined) return '—'
    if (n < 1024) return `${n} B`
    if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
    return `${(n / 1024 / 1024).toFixed(1)} MB`
}

