/**
 * "حسابات أخرى لنفس العميل / Other accounts of this debtor": the other cases (clints rows) whose
 * identity number is the same as this case's (GET /v2/cases/<id>/same-identity).
 *
 * A compact summary row (accounts, amount, paid, remaining) that expands into a table. Every
 * account of the identity is listed READ-ONLY; only the accounts inside the viewer's own scope
 * (`can_open`) open, in the same tab like the other case lists. Paid counts approved payments
 * only, the same rule as the balance card. Archived accounts (also the renamed
 * `<account>-أرشيف<n>` records) appear only when the checkbox is on.
 * Renders nothing when the role cannot read the case.
 */
import { useState } from 'react'
import type { KeyboardEvent, MouseEvent, ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useNavigate } from 'react-router-dom'
import { HiChevronDown, HiOutlineLockClosed } from 'react-icons/hi'
import classNames from 'classnames'
import Card from '@/components/ui/Card'
import Checkbox from '@/components/ui/Checkbox'
import Table from '@/components/ui/Table'
import StatusPill from '@/components/shared/StatusPill'
import { PlainBadge } from '@/components/v2/Badges'
import { Ltr, MoneyText, RiyadhTime } from '@/components/v2/Display'
import { TableFrame } from '@/components/v2/ListParts'
import { ErrorState, Note, SkeletonLines } from '@/components/v2/States'
import { isV2Error } from '@/services/v2/http'
import { sameIdentityApi } from '@/services/v2/sameIdentityApi'
import type { SameIdentityAccount } from '@/services/v2/sameIdentityApi'
import { useV2Query } from '@/utils/v2/hooks'
import { caseLink } from '@/utils/v2/links'
import { isPlainLeftClick, openCaseInApp, shouldIgnoreRowClick } from '@/utils/v2/rowNavigation'

const { THead, TBody, Tr, Th, Td } = Table

const Stat = ({ label, children }: { label: string; children: ReactNode }) => (
    <div className="min-w-0">
        <div className="text-eyebrow mb-1">{label}</div>
        <div className="text-[15px] font-extrabold text-navy-600 dark:text-gray-100">{children}</div>
    </div>
)

const AccountRow = ({ a }: { a: SameIdentityAccount }) => {
    const { t } = useTranslation()
    const navigate = useNavigate()
    const readOnly = String(t('p8.sameid.readOnly'))
    const open = (e: MouseEvent<HTMLElement> | KeyboardEvent<HTMLElement>) => {
        if (!a.can_open || shouldIgnoreRowClick(e)) return
        openCaseInApp(navigate, a.id, e)
    }
    return (
        <Tr
            className={a.can_open ? 'cursor-pointer hover:bg-gray-50 dark:hover:bg-night-raised' : undefined}
            onClick={a.can_open ? open : undefined}
            title={a.can_open ? undefined : readOnly}
            data-testid="p8-sameid-row"
            data-case={a.id}
            data-can-open={a.can_open ? 'true' : 'false'}
        >
            <Td className="whitespace-nowrap">
                <div className="flex flex-wrap items-center gap-2">
                    {a.can_open ? (
                        <Link
                            to={caseLink(a.id)}
                            className="font-extrabold text-azure-600 hover:underline"
                            onClick={(e) => {
                                if (isPlainLeftClick(e)) {
                                    e.preventDefault()
                                    openCaseInApp(navigate, a.id)
                                }
                            }}
                        >
                            <Ltr>{a.account_number}</Ltr>
                        </Link>
                    ) : (
                        <span className="inline-flex items-center gap-1 text-gray-700 dark:text-night-body">
                            <HiOutlineLockClosed aria-label={readOnly} className="shrink-0 text-gray-500" />
                            <Ltr>{a.account_number}</Ltr>
                        </span>
                    )}
                    {a.is_archive && (
                        <PlainBadge size="row" tone="neutral">
                            {t('p8.sameid.archived')}
                        </PlainBadge>
                    )}
                </div>
                <div className="mt-0.5 text-[12px] text-gray-600" dir="auto">
                    {a.name}
                </div>
            </Td>
            <Td dir="auto">{a.contract?.name || '—'}</Td>
            <Td dir="auto">{a.portfolio?.name || '—'}</Td>
            <Td className="text-end">
                <MoneyText value={a.amount} />
            </Td>
            <Td className="text-end">
                <MoneyText value={a.paid} />
            </Td>
            <Td className="text-end">
                <MoneyText value={a.remaining} strong />
            </Td>
            <Td>
                {a.last_note?.status ? (
                    <div className="flex flex-col items-start gap-1">
                        <StatusPill size="row" color={a.last_note.color} tone="neutral">
                            {a.last_note.status}
                        </StatusPill>
                        {a.last_note.sub_status && (
                            <span className="text-[12px] text-gray-600" dir="auto">
                                {a.last_note.sub_status}
                            </span>
                        )}
                    </div>
                ) : (
                    <span className="text-gray-500">{t('p8.sameid.noNote')}</span>
                )}
            </Td>
            <Td>{a.last_note?.at ? <RiyadhTime iso={a.last_note.at} /> : '—'}</Td>
            <Td dir="auto">{a.owner?.name || <span className="text-gray-500">{t('p8.sameid.unassigned')}</span>}</Td>
        </Tr>
    )
}

const SameIdentitySection = ({ caseId }: { caseId: string }) => {
    const { t } = useTranslation()
    const [expanded, setExpanded] = useState(false)
    const [withArchived, setWithArchived] = useState(false)
    const q = useV2Query((signal) => sameIdentityApi.get(caseId, withArchived, signal), [caseId, withArchived])
    const data = q.data && q.data.case_id === caseId ? q.data : undefined

    // a role that cannot read this case (or a case outside its scope) sees nothing here
    if (q.error && isV2Error(q.error) && (q.error.status === 403 || q.error.status === 404)) return null

    const count = data?.summary.count ?? 0
    const canExpand = count > 0

    return (
        <div className="col-span-2" data-testid="p8-same-identity">
            <Card>
                <div className="mb-3 flex flex-wrap items-start justify-between gap-2">
                    <div className="min-w-0">
                        <h5 className="mb-0">{t('p8.sameid.title')}</h5>
                        <Note>
                            {t('p8.sameid.subtitle')}
                            {data?.identity ? (
                                <>
                                    {' '}
                                    <Ltr className="font-extrabold">{data.identity}</Ltr>
                                </>
                            ) : null}
                        </Note>
                    </div>
                    <span data-testid="p8-sameid-archived">
                        <Checkbox checked={withArchived} onChange={(v: boolean) => setWithArchived(v)}>
                            {t('p8.sameid.showArchived')}
                        </Checkbox>
                    </span>
                </div>

                {q.error && !data ? (
                    <ErrorState error={q.error} onRetry={q.reload} />
                ) : !data ? (
                    <SkeletonLines rows={2} />
                ) : (
                    <>
                        <button
                            type="button"
                            className={classNames(
                                'grid w-full grid-cols-2 items-center gap-4 rounded-lg border border-gray-300 px-4 py-3 text-start dark:border-night-border sm:grid-cols-[repeat(4,minmax(0,1fr))_auto]',
                                canExpand ? 'hover:bg-gray-50 dark:hover:bg-night-raised' : 'cursor-default'
                            )}
                            aria-expanded={canExpand ? expanded : undefined}
                            disabled={!canExpand}
                            onClick={() => setExpanded((v) => !v)}
                            data-testid="p8-sameid-summary"
                        >
                            <Stat label={t('p8.sameid.count')}>
                                <span className="tabular-nums">{count}</span>
                            </Stat>
                            <Stat label={t('p8.sameid.amount')}>
                                <MoneyText value={data.summary.amount} />
                            </Stat>
                            <Stat label={t('p8.sameid.paid')}>
                                <MoneyText value={data.summary.paid} />
                            </Stat>
                            <Stat label={t('p8.sameid.remaining')}>
                                <MoneyText value={data.summary.remaining} />
                            </Stat>
                            {canExpand && (
                                <span className="col-span-2 inline-flex items-center gap-1 text-[13px] font-extrabold text-azure-600 sm:col-span-1">
                                    {expanded ? t('p8.sameid.hide') : t('p8.sameid.show')}
                                    <HiChevronDown
                                        aria-hidden
                                        className={classNames('transition-transform', expanded && 'rotate-180')}
                                    />
                                </span>
                            )}
                        </button>

                        {count === 0 && (
                            <Note className="mt-2">
                                {withArchived ? t('p8.sameid.emptyWithArchived') : t('p8.sameid.empty')}
                            </Note>
                        )}

                        {expanded && canExpand && (
                            <div className="mt-3">
                                <TableFrame testId="p8-sameid-table">
                                    <Table>
                                        <THead>
                                            <Tr>
                                                <Th>{t('p8.sameid.col.account')}</Th>
                                                <Th>{t('p8.sameid.col.contract')}</Th>
                                                <Th>{t('p8.sameid.col.portfolio')}</Th>
                                                <Th className="text-end">{t('p8.sameid.col.amount')}</Th>
                                                <Th className="text-end">{t('p8.sameid.col.paid')}</Th>
                                                <Th className="text-end">{t('p8.sameid.col.remaining')}</Th>
                                                <Th>{t('p8.sameid.col.lastStatus')}</Th>
                                                <Th>{t('p8.sameid.col.lastNoteAt')}</Th>
                                                <Th>{t('p8.sameid.col.owner')}</Th>
                                            </Tr>
                                        </THead>
                                        <TBody>
                                            {data.accounts.map((a) => (
                                                <AccountRow key={a.id} a={a} />
                                            ))}
                                        </TBody>
                                    </Table>
                                </TableFrame>
                                {data.truncated && (
                                    <Note className="mt-2">
                                        {t('p8.sameid.truncated', { shown: data.accounts.length, total: count })}
                                    </Note>
                                )}
                                <Note className="mt-2">{t('p8.sameid.note')}</Note>
                            </div>
                        )}
                    </>
                )}
            </Card>
        </div>
    )
}

export default SameIdentitySection
