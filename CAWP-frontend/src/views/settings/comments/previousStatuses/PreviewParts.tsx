/**
 * Counts, status lists and problem rows of the previous statuses import, shared by the preview
 * and the result. Codes come from services/previous_status_import.py (CODES); an unknown code
 * falls back to the server's Arabic message.
 */
import classNames from 'classnames'
import { useTranslation } from 'react-i18next'
import StatusPill from '@/components/shared/StatusPill'
import { formatCount } from '@/utils/v2/format'
import type { Translate } from '@/services/v2'
import type { PrevStatusCounts, PrevStatusProblem } from '@/services/v2/previousStatusImportApi'

export function codeText(code: string, message: string | null | undefined, t: Translate): string {
    const key = `p9.prevstatus.code.${code}`
    const text = t(key)
    return text === key ? message || code : text
}

const Tile = ({ label, value, tone, testId }: { label: string; value: number | undefined; tone?: string; testId: string }) => (
    <div className="min-w-0 rounded-lg border border-gray-300 px-3 py-2 dark:border-night-border" data-testid={testId} data-value={value ?? 0}>
        <div className="text-eyebrow mb-1">{label}</div>
        <div className={classNames('text-[18px] font-extrabold tabular-nums text-navy-600 dark:text-gray-100', tone)}>
            {formatCount(value ?? 0)}
        </div>
    </div>
)

/** Skip codes shown as tiles when they occur (in this order). */
const SKIP_TILES = [
    'no_status',
    'account_not_found',
    'case_archived',
    'case_locked',
    'status_not_found',
    'sub_status_not_found',
    'already_imported',
    'duplicate_in_file',
    'missing_account',
    'account_ambiguous',
    'status_ambiguous',
    'sub_status_ambiguous',
    'formula_not_allowed',
    'value_too_long',
] as const

export const CountTiles = ({ counts, done }: { counts: Partial<PrevStatusCounts>; done?: boolean }) => {
    const { t } = useTranslation()
    const ready = done ? counts.imported : counts.ready
    return (
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-4" data-testid="p9-prevstatus-counts">
            <Tile testId="p9-prevstatus-count-total" label={t('p9.prevstatus.count.total')} value={counts.total} />
            <Tile
                testId="p9-prevstatus-count-ready"
                label={t(done ? 'p9.prevstatus.count.imported' : 'p9.prevstatus.count.ready')}
                value={ready}
                tone="text-success"
            />
            <Tile
                testId="p9-prevstatus-count-subs"
                label={t(done ? 'p9.prevstatus.count.subsCreated' : 'p9.prevstatus.count.subsToCreate')}
                value={done ? counts.sub_statuses_created : counts.sub_statuses_to_create}
                tone="text-azure-600"
            />
            {SKIP_TILES.filter((c) => (counts[c] ?? 0) > 0).map((c) => (
                <Tile
                    key={c}
                    testId={`p9-prevstatus-count-${c}`}
                    label={codeText(c, null, t)}
                    value={counts[c]}
                    tone={c === 'no_status' || c === 'already_imported' ? undefined : 'text-warning'}
                />
            ))}
        </div>
    )
}

/** A short list of status names with their row counts (to create / missing). */
export const StatusList = ({
    title,
    items,
    tone,
    testId,
}: {
    title: string
    items: Array<{ main?: string | null; name: string; rows: number }>
    tone: 'info' | 'warning'
    testId: string
}) => {
    const { t } = useTranslation()
    if (items.length === 0) return null
    return (
        <div
            data-testid={testId}
            className={classNames(
                'rounded-lg border px-3 py-2 text-[13px]',
                tone === 'info'
                    ? 'border-azure-100 bg-info-tint dark:border-night-border dark:bg-night-raised'
                    : 'border-warning/30 bg-warning-tint dark:border-night-border dark:bg-night-raised'
            )}
        >
            <div className="mb-1 font-extrabold text-navy-600 dark:text-gray-100">{title}</div>
            <ul className="max-h-[180px] list-inside list-disc overflow-auto">
                {items.map((s, i) => (
                    <li key={`${s.main || ''}|${s.name}|${i}`}>
                        {s.main ? (
                            <>
                                <bdi dir="auto">{s.main}</bdi> ‹ <bdi dir="auto">{s.name}</bdi>
                            </>
                        ) : (
                            <bdi dir="auto">{s.name}</bdi>
                        )}{' '}
                        <span className="tabular-nums text-gray-600">({t('p9.prevstatus.rows', { n: formatCount(s.rows) })})</span>
                    </li>
                ))}
            </ul>
        </div>
    )
}

export const ProblemsTable = ({ rows }: { rows: PrevStatusProblem[] }) => {
    const { t } = useTranslation()
    if (rows.length === 0) {
        return <div className="py-3 text-[13px] text-gray-600">{t('p9.prevstatus.problems.none')}</div>
    }
    return (
        <div className="max-h-[340px] overflow-auto rounded-lg border border-gray-300 dark:border-night-border" data-testid="p9-prevstatus-problems">
            <table className="w-full text-[12.5px]">
                <thead className="sticky top-0">
                    <tr className="bg-[#DFEEF2] dark:bg-night-raised">
                        <th className="px-3 py-2 text-start font-extrabold">{t('p9.prevstatus.problems.row')}</th>
                        <th className="px-3 py-2 text-start font-extrabold">{t('p9.prevstatus.problems.account')}</th>
                        <th className="px-3 py-2 text-start font-extrabold">{t('p9.prevstatus.problems.main')}</th>
                        <th className="px-3 py-2 text-start font-extrabold">{t('p9.prevstatus.problems.sub')}</th>
                        <th className="px-3 py-2 text-start font-extrabold">{t('p9.prevstatus.problems.result')}</th>
                    </tr>
                </thead>
                <tbody>
                    {rows.map((r) => (
                        <tr key={r.row_number} className="border-t border-gray-200 align-top dark:border-night-border" data-code={r.code}>
                            <td className="px-3 py-1.5 tabular-nums">{r.row_number}</td>
                            <td className="px-3 py-1.5">
                                <bdi dir="ltr">{r.account_number || '—'}</bdi>
                            </td>
                            <td className="px-3 py-1.5">
                                <bdi dir="auto">{r.main_status || '—'}</bdi>
                            </td>
                            <td className="px-3 py-1.5">
                                <bdi dir="auto">{r.sub_status || '—'}</bdi>
                            </td>
                            <td className="px-3 py-1.5">
                                {r.code === 'ready' ? (
                                    <StatusPill tone="info" size="row">
                                        {t('p9.prevstatus.problems.createsSub')}
                                    </StatusPill>
                                ) : (
                                    <StatusPill tone={r.code === 'already_imported' ? 'neutral' : 'warning'} size="row">
                                        {codeText(r.code, r.message, t)}
                                    </StatusPill>
                                )}
                            </td>
                        </tr>
                    ))}
                </tbody>
            </table>
        </div>
    )
}
