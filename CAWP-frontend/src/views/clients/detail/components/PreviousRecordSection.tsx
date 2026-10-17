/**
 * "السجل السابق / Previous record": shown on a case that a portfolio upload created in place of
 * an ARCHIVED case with the same account number (GET /v2/cases/<id>/previous-records).
 *
 * Read-only. The previous record stays archived under its renamed account number and keeps its
 * own notes, payments, promises and follow-ups; nothing was copied. Its payments are listed with
 * their approval state (pending, rejected and reversed shown as such) and are never added to
 * this case's balance or to any recovered total here. Renders nothing when the case has no
 * previous record or the role cannot read the case.
 */
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import Card from '@/components/ui/Card'
import Tabs from '@/components/ui/Tabs'
import { FollowUpStatusBadge, PaymentBadge, PlainBadge, PromiseBadge } from '@/components/v2/Badges'
import { CivilDate, Field, Ltr, MoneyText, RiyadhTime } from '@/components/v2/Display'
import { Note } from '@/components/v2/States'
import { portfolioImportApi } from '@/services/v2/portfolioImportApi'
import type { PreviousRecord } from '@/services/v2/portfolioImportApi'
import { useV2Query } from '@/utils/v2/hooks'

const { TabNav, TabList } = Tabs

type Section = 'notes' | 'payments' | 'promises' | 'follow_ups'
const SECTIONS: Section[] = ['notes', 'payments', 'promises', 'follow_ups']

const Empty = () => {
    const { t } = useTranslation()
    return <div className="py-3 text-[13px] text-gray-600">{t('p7.import.previous.empty')}</div>
}

const NotPermitted = () => {
    const { t } = useTranslation()
    return <div className="py-3 text-[13px] text-gray-600">{t('p7.import.previous.notPermitted')}</div>
}

const th = 'px-3 py-2 text-start font-extrabold'
const td = 'px-3 py-1.5 align-top'

const RecordSections = ({ record }: { record: PreviousRecord }) => {
    const { t } = useTranslation()
    const [tab, setTab] = useState<Section>('notes')
    const count = (s: Section) => {
        const list = record[s]
        return list ? list.length : null
    }

    return (
        <div>
            <Tabs value={tab} onChange={(v) => setTab(v as Section)}>
                <TabList>
                    {SECTIONS.map((s) => (
                        <TabNav key={s} value={s}>
                            {t(`p7.import.previous.tab.${s}`)}
                            {count(s) !== null && <span className="ms-1 tabular-nums text-gray-500">({count(s)})</span>}
                        </TabNav>
                    ))}
                </TabList>
            </Tabs>
            <div className="max-h-[360px] overflow-auto pt-3" data-testid={`p7-previous-${tab}`}>
                {tab === 'notes' &&
                    (record.notes === null ? (
                        <NotPermitted />
                    ) : record.notes.length === 0 ? (
                        <Empty />
                    ) : (
                        <ul className="flex flex-col gap-3">
                            {record.notes.map((n) => (
                                <li key={n.id} className="rounded-md border border-gray-200 p-3 dark:border-night-border">
                                    <div className="mb-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-[12px] text-gray-600">
                                        <span className="font-extrabold text-navy-600 dark:text-gray-100">{n.author?.name || '—'}</span>
                                        <RiyadhTime iso={n.created_at} />
                                        {n.status?.name && (
                                            <PlainBadge size="row">
                                                {n.status.name}
                                                {n.sub_status?.name ? ` › ${n.sub_status.name}` : ''}
                                            </PlainBadge>
                                        )}
                                        {n.corrected && <PlainBadge size="row" tone="info">{t('p7.import.previous.corrected')}</PlainBadge>}
                                    </div>
                                    <div className="whitespace-pre-wrap break-words text-[13.5px]" dir="auto">
                                        {n.text}
                                    </div>
                                </li>
                            ))}
                        </ul>
                    ))}

                {tab === 'payments' &&
                    (record.payments === null ? (
                        <NotPermitted />
                    ) : record.payments.length === 0 ? (
                        <Empty />
                    ) : (
                        <>
                            <Note className="mb-2">{t('p7.import.previous.paymentsNote')}</Note>
                            <table className="w-full text-[12.5px]">
                                <thead>
                                    <tr className="bg-[#DFEEF2] dark:bg-night-raised">
                                        <th className={th}>{t('p7.import.previous.amount')}</th>
                                        <th className={th}>{t('p7.import.previous.paymentDate')}</th>
                                        <th className={th}>{t('p7.import.previous.state')}</th>
                                        <th className={th}>{t('p7.import.previous.recordedBy')}</th>
                                        <th className={th}>{t('p7.import.previous.decision')}</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {record.payments.map((p) => (
                                        <tr key={p.id} className="border-t border-gray-200 dark:border-night-border">
                                            <td className={td}>
                                                <MoneyText value={p.amount} />
                                            </td>
                                            <td className={td}>
                                                <CivilDate value={p.payment_civil_date} />
                                            </td>
                                            <td className={td}>
                                                <PaymentBadge status={p.status} size="row" />
                                            </td>
                                            <td className={td}>{p.recorder?.name || '—'}</td>
                                            <td className={td}>
                                                {p.reversal ? (
                                                    <span>
                                                        {p.reversal.by?.name || '—'} · <RiyadhTime iso={p.reversal.at} />
                                                        {p.reversal.reason ? ` · ${p.reversal.reason}` : ''}
                                                    </span>
                                                ) : p.decision ? (
                                                    <span>
                                                        {p.decision.by?.name || '—'} · <RiyadhTime iso={p.decision.at} />
                                                        {p.decision.comment ? ` · ${p.decision.comment}` : ''}
                                                    </span>
                                                ) : (
                                                    '—'
                                                )}
                                            </td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </>
                    ))}

                {tab === 'promises' &&
                    (record.promises === null ? (
                        <NotPermitted />
                    ) : record.promises.length === 0 ? (
                        <Empty />
                    ) : (
                        <table className="w-full text-[12.5px]">
                            <thead>
                                <tr className="bg-[#DFEEF2] dark:bg-night-raised">
                                    <th className={th}>{t('p7.import.previous.dueDate')}</th>
                                    <th className={th}>{t('p7.import.previous.amount')}</th>
                                    <th className={th}>{t('p7.import.previous.state')}</th>
                                    <th className={th}>{t('p7.import.previous.recordedBy')}</th>
                                </tr>
                            </thead>
                            <tbody>
                                {record.promises.map((p) => (
                                    <tr key={p.id} className="border-t border-gray-200 dark:border-night-border">
                                        <td className={td}>
                                            <CivilDate value={p.due_date} />
                                        </td>
                                        <td className={td}>
                                            <MoneyText value={p.amount} />
                                        </td>
                                        <td className={td}>
                                            <PromiseBadge state={p.state} size="row" />
                                        </td>
                                        <td className={td}>{p.recorder?.name || '—'}</td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    ))}

                {tab === 'follow_ups' &&
                    (record.follow_ups === null ? (
                        <NotPermitted />
                    ) : record.follow_ups.length === 0 ? (
                        <Empty />
                    ) : (
                        <table className="w-full text-[12.5px]">
                            <thead>
                                <tr className="bg-[#DFEEF2] dark:bg-night-raised">
                                    <th className={th}>{t('p7.import.previous.dueDate')}</th>
                                    <th className={th}>{t('p7.import.previous.state')}</th>
                                    <th className={th}>{t('p7.import.previous.nextAction')}</th>
                                    <th className={th}>{t('p7.import.previous.closedAt')}</th>
                                </tr>
                            </thead>
                            <tbody>
                                {record.follow_ups.map((f) => (
                                    <tr key={f.id} className="border-t border-gray-200 dark:border-night-border">
                                        <td className={td}>
                                            <CivilDate value={f.due_date} />
                                        </td>
                                        <td className={td}>
                                            <FollowUpStatusBadge status={f.status} size="row" />
                                        </td>
                                        <td className={td} dir="auto">
                                            {f.next_action || '—'}
                                        </td>
                                        <td className={td}>
                                            <RiyadhTime iso={f.completed_at} />
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    ))}
            </div>
        </div>
    )
}

const RecordBlock = ({ record }: { record: PreviousRecord }) => {
    const { t } = useTranslation()
    return (
        <div className="rounded-lg border border-gray-300 p-4 dark:border-night-border" data-testid="p7-previous-record" data-case={record.case_id}>
            {record.depth > 1 && (
                <div className="text-eyebrow mb-2">{t('p7.import.previous.olderLevel', { n: record.depth })}</div>
            )}
            <div className="mb-4 grid grid-cols-2 gap-x-6 gap-y-3 sm:grid-cols-4">
                <Field label={t('p7.import.previous.accountNow')}>
                    <Ltr>{record.account_number || '—'}</Ltr>
                </Field>
                <Field label={t('p7.import.previous.originalAccount')}>
                    <Ltr>{record.original_account_number}</Ltr>
                </Field>
                <Field label={t('p7.import.previous.portfolio')}>
                    {record.portfolio?.name || '—'}
                    {record.contract?.name ? <div className="text-[12px] text-gray-600">{record.contract.name}</div> : null}
                </Field>
                <Field label={t('p7.import.previous.originalAmount')}>
                    <MoneyText value={record.original_amount} />
                </Field>
                <Field label={t('p7.import.previous.archivedAt')}>
                    {record.archive ? <RiyadhTime iso={record.archive.at} /> : '—'}
                </Field>
                <Field label={t('p7.import.previous.archiveReason')}>{record.archive?.reason || '—'}</Field>
                <Field label={t('p7.import.previous.linkedAt')}>
                    <RiyadhTime iso={record.linked_at} />
                </Field>
                <Field label={t('p7.import.previous.status')}>
                    {record.exists ? (
                        <PlainBadge size="row" tone={record.is_archived ? 'neutral' : 'info'}>
                            {record.is_archived ? t('p7.import.previous.archived') : t('p7.import.previous.notArchived')}
                        </PlainBadge>
                    ) : (
                        <PlainBadge size="row" tone="warning">
                            {t('p7.import.previous.deleted')}
                        </PlainBadge>
                    )}
                </Field>
            </div>
            {record.exists ? <RecordSections record={record} /> : <Note>{t('p7.import.previous.deletedHint')}</Note>}
        </div>
    )
}

const PreviousRecordSection = ({ caseId }: { caseId: string }) => {
    const { t } = useTranslation()
    const q = useV2Query((signal) => portfolioImportApi.previousRecords(caseId, signal), [caseId])
    const data = q.data
    if (!data || !data.has_previous || data.case_id !== caseId) return null
    return (
        <div className="col-span-2" data-testid="p7-previous-records">
            <Card>
                <h5 className="mb-1">{t('p7.import.previous.title')}</h5>
                <Note className="mb-4">{t('p7.import.previous.intro')}</Note>
                <div className="flex flex-col gap-4">
                    {data.records.map((r) => (
                        <RecordBlock key={r.case_id} record={r} />
                    ))}
                </div>
            </Card>
        </div>
    )
}

export default PreviousRecordSection
