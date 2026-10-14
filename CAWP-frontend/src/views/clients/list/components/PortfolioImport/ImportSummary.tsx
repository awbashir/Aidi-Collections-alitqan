/**
 * Counts and problem rows of a portfolio upload, shared by the preview step and the result.
 * Outcomes: new / from archive (old record kept and linked) / skipped (active case exists) /
 * rejected; collector usernames that matched nobody are imported unassigned and counted.
 */
import classNames from 'classnames'
import { useTranslation } from 'react-i18next'
import StatusPill from '@/components/shared/StatusPill'
import type { StatusTone } from '@/components/shared/StatusPill'
import { formatCount } from '@/utils/v2/format'
import type { ImportCounts, ImportOutcome, ImportProblem, ImportRow } from '@/services/v2/portfolioImportApi'
import { codeLabel, warningLabel } from './importLogic'

const OUTCOME_TONE: Record<ImportOutcome, StatusTone> = {
    created: 'success',
    created_from_archive: 'info',
    skipped_active_exists: 'neutral',
    rejected: 'danger',
}

export const OutcomeBadge = ({ outcome }: { outcome: ImportOutcome }) => {
    const { t } = useTranslation()
    return (
        <StatusPill tone={OUTCOME_TONE[outcome] || 'neutral'} size="row">
            <span data-outcome={outcome}>{t(`p7.import.outcome.${outcome}`)}</span>
        </StatusPill>
    )
}

const Tile = ({ label, value, tone, testId }: { label: string; value: number | undefined; tone?: string; testId: string }) => (
    <div className="min-w-0 rounded-lg border border-gray-300 px-3 py-2 dark:border-night-border" data-testid={testId} data-value={value ?? 0}>
        <div className="text-eyebrow mb-1">{label}</div>
        <div className={classNames('text-[18px] font-extrabold tabular-nums text-navy-600 dark:text-gray-100', tone)}>
            {formatCount(value ?? 0)}
        </div>
    </div>
)

export const CountTiles = ({ counts }: { counts: Partial<ImportCounts> }) => {
    const { t } = useTranslation()
    return (
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3" data-testid="p7-import-counts">
            <Tile testId="p7-count-total" label={t('p7.import.count.total')} value={counts.total} />
            <Tile testId="p7-count-created" label={t('p7.import.count.created')} value={counts.created} tone="text-success" />
            <Tile
                testId="p7-count-archive"
                label={t('p7.import.count.createdFromArchive')}
                value={counts.created_from_archive}
                tone="text-azure-600"
            />
            <Tile testId="p7-count-skipped" label={t('p7.import.count.skipped')} value={counts.skipped_active_exists} />
            <Tile testId="p7-count-rejected" label={t('p7.import.count.rejected')} value={counts.rejected} tone="text-danger" />
            <Tile
                testId="p7-count-collector"
                label={t('p7.import.count.collectorNotMatched')}
                value={counts.collector_not_matched}
                tone="text-warning"
            />
            <Tile testId="p7-count-assigned" label={t('p7.import.count.assigned')} value={counts.assigned} />
            <Tile testId="p7-count-contacts" label={t('p7.import.count.contactsNew')} value={counts.contacts_new} />
            <Tile testId="p7-count-contacts-existing" label={t('p7.import.count.contactsExisting')} value={counts.contacts_existing} />
        </div>
    )
}

type RowLike = Pick<ImportProblem, 'row_number' | 'account_number' | 'outcome' | 'code' | 'message' | 'warnings'> &
    Partial<Pick<ImportProblem, 'detail'>> &
    Partial<Pick<ImportRow, 'case_id'>>

export const ProblemsTable = ({ rows }: { rows: RowLike[] }) => {
    const { t } = useTranslation()
    if (rows.length === 0) {
        return <div className="py-3 text-[13px] text-gray-600">{t('p7.import.problems.none')}</div>
    }
    return (
        <div className="max-h-[300px] overflow-auto rounded-lg border border-gray-300 dark:border-night-border" data-testid="p7-import-problems">
            <table className="w-full text-[12.5px]">
                <thead className="sticky top-0">
                    <tr className="bg-[#DFEEF2] dark:bg-night-raised">
                        <th className="px-3 py-2 text-start font-extrabold">{t('p7.import.problems.row')}</th>
                        <th className="px-3 py-2 text-start font-extrabold">{t('p7.import.problems.account')}</th>
                        <th className="px-3 py-2 text-start font-extrabold">{t('p7.import.problems.outcome')}</th>
                        <th className="px-3 py-2 text-start font-extrabold">{t('p7.import.problems.reason')}</th>
                    </tr>
                </thead>
                <tbody>
                    {rows.map((r) => (
                        <tr key={r.row_number} className="border-t border-gray-200 align-top dark:border-night-border">
                            <td className="px-3 py-1.5 tabular-nums">{r.row_number}</td>
                            <td className="px-3 py-1.5">
                                <bdi dir="ltr">{r.account_number || '—'}</bdi>
                            </td>
                            <td className="whitespace-nowrap px-3 py-1.5">
                                <OutcomeBadge outcome={r.outcome} />
                            </td>
                            <td className="px-3 py-1.5">
                                {r.outcome !== 'created' && (
                                    <div>
                                        {codeLabel(r.code, r.message, t)}
                                        {r.detail ? <bdi dir="auto"> ({r.detail})</bdi> : null}
                                    </div>
                                )}
                                {(r.warnings || []).map((w, i) => (
                                    <div key={i} className="text-warning">
                                        {warningLabel(w, t)}
                                    </div>
                                ))}
                            </td>
                        </tr>
                    ))}
                </tbody>
            </table>
        </div>
    )
}
