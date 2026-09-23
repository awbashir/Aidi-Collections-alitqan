/**
 * What a job row will do / did, per kind, from the row's `details`
 * (the same keys as the CSV `detail:` columns).
 */
import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { Ltr, RiyadhTime } from '@/components/v2/Display'
import { PlainBadge } from '@/components/v2/Badges'
import { caseLink } from '@/utils/v2/links'
import type { BulkRow } from '@/services/v2'

type D = Record<string, unknown>
const s = (v: unknown) => (v === null || v === undefined || v === '' ? '' : String(v))

const Arrow = () => <span className="mx-1 text-gray-500 rtl:rotate-180 inline-block">→</span>

export const RowDetails = ({ kind, row }: { kind: string; row: BulkRow }) => {
    const { t } = useTranslation()
    const d = (row.details || {}) as D
    if (!row.details || Object.keys(d).length === 0) return <span className="text-gray-500">—</span>
    switch (kind) {
        case 'lock':
        case 'unlock':
            return (
                <span className="text-[12.5px]">
                    {t('p2.detail.locked')}: {t(d.is_locked_before ? 'p2.detail.yes' : 'p2.detail.no')}
                    {d.is_locked_after !== undefined && (
                        <>
                            <Arrow />
                            {t(d.is_locked_after ? 'p2.detail.yes' : 'p2.detail.no')}
                        </>
                    )}
                </span>
            )
        case 'assign':
        case 'round_robin':
            return (
                <span className="text-[12.5px]" data-target={s(d.target_login)}>
                    {d.position !== undefined && <span className="me-1 tabular-nums text-gray-600">#{s(d.position)}</span>}
                    {kind === 'assign' && (
                        <>
                            <Ltr>{s(d.owner_before_login) || t('p1.unassigned')}</Ltr>
                            <Arrow />
                        </>
                    )}
                    <Ltr className="font-extrabold">{s(d.target_login) || s(d.login) || '—'}</Ltr>
                    {d.target_name ? <span className="ms-1 text-gray-600">({s(d.target_name)})</span> : null}
                    {d.owner_after_login ? (
                        <span className="ms-1 text-success">
                            ✓ <Ltr>{s(d.owner_after_login)}</Ltr>
                        </span>
                    ) : null}
                    {row.eligibility_code === 'conflicting_logins' && d.logins ? (
                        <div className="text-danger">
                            <Ltr>{(d.logins as string[]).join(', ')}</Ltr>
                        </div>
                    ) : null}
                </span>
            )
        case 'archive':
            return <span className="text-[12.5px]">{s(d.archive_reason) || '—'}</span>
        case 'restore':
            return (
                <span className="text-[12.5px]">
                    {s(d.archive_reason) || '—'}
                    {d.archived_at ? (
                        <span className="ms-1 text-gray-600">
                            · {t('p2.detail.archivedAt')} <RiyadhTime iso={s(d.archived_at)} />
                        </span>
                    ) : null}
                </span>
            )
        case 'notes':
            return (
                <span className="text-[12.5px]">
                    {s(d.comment_status)}
                    {d.next_contact_date ? (
                        <span className="ms-1 text-gray-600">
                            · {t('p2.detail.callback')} <RiyadhTime iso={s(d.next_contact_date)} />
                        </span>
                    ) : null}
                    {d.follow_up_id && row.case_id ? (
                        <Link className="ms-2 font-extrabold text-azure-600 hover:underline" to={caseLink(row.case_id, 'followUps')} data-testid="p2-row-follow-up">
                            {t('p2.detail.followUp')}
                        </Link>
                    ) : null}
                    {d.status_contract_id && row.eligibility_code === 'status_not_for_contract' ? (
                        <div className="text-gray-600">{t('p2.detail.otherContract')}</div>
                    ) : null}
                </span>
            )
        case 'followup_closeout':
            return (
                <span className="text-[12.5px]">
                    {t('p2.detail.due')} <Ltr>{s(d.due_date)}</Ltr>
                    {d.days_late !== undefined && <span className="ms-1 text-danger">({t('p2.detail.daysLate', { n: s(d.days_late) })})</span>}
                    {d.outcome_note_at ? (
                        <div className="text-gray-600">
                            {t('p2.detail.handledBy')} <RiyadhTime iso={s(d.outcome_note_at)} />
                            {row.case_id && (
                                <Link className="ms-1 font-extrabold text-azure-600 hover:underline" to={caseLink(row.case_id, 'comments')}>
                                    {t('p2.detail.openNotes')}
                                </Link>
                            )}
                            {d.later_notes !== undefined && <span className="ms-1">· {t('p2.detail.laterNotes', { n: s(d.later_notes) })}</span>}
                        </div>
                    ) : null}
                </span>
            )
        case 'attribute_corrections': {
            const changes = (d.changes || {}) as Record<string, { before: unknown; after: unknown }>
            return (
                <span className="text-[12.5px]">
                    {Object.entries(changes).map(([k, v]) => (
                        <div key={k} data-attr={k}>
                            <span className="font-extrabold">
                                <bdi>{k}</bdi>
                            </span>
                            : <bdi className="text-gray-600">{v.before === null || v.before === undefined ? t('p2.detail.empty') : String(v.before)}</bdi>
                            <Arrow />
                            <bdi>{v.after === null || v.after === undefined ? t('p2.detail.removed') : String(v.after)}</bdi>
                            {Array.isArray(d.new_keys) && (d.new_keys as string[]).includes(k) && (
                                <PlainBadge size="row" tone="info">
                                    {t('p2.detail.newKey')}
                                </PlainBadge>
                            )}
                        </div>
                    ))}
                    {Array.isArray(d.unchanged_keys) && (d.unchanged_keys as string[]).length > 0 && (
                        <div className="text-gray-600">
                            {t('p2.detail.unchanged')}: <bdi>{(d.unchanged_keys as string[]).join(', ')}</bdi>
                        </div>
                    )}
                    {!!(d.is_locked || d.is_archived) && (
                        <div className="text-warning">{[d.is_locked && t('p2.detail.caseLocked'), d.is_archived && t('p2.detail.caseArchived')].filter(Boolean).join(' · ')}</div>
                    )}
                    {d.correction_id ? (
                        <Link
                            className="font-extrabold text-azure-600 hover:underline"
                            to={`/corrections#status=pending&sel=${encodeURIComponent(s(d.correction_id))}`}
                            data-testid="p2-row-correction"
                            data-correction={s(d.correction_id)}
                        >
                            {t('p2.detail.openCorrection')}
                        </Link>
                    ) : null}
                </span>
            )
        }
        default:
            return (
                <span className="text-[12px] text-gray-600">
                    {Object.entries(d)
                        .filter(([, v]) => v !== null && typeof v !== 'object')
                        .map(([k, v]) => `${k}: ${String(v)}`)
                        .join(' · ')}
                </span>
            )
    }
}

export default RowDetails
