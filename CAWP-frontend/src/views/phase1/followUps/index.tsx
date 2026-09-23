/**
 * Follow-ups (`/follow-ups`, permission `follow_ups`).
 *
 * GET /v2/follow-ups split into late / today / upcoming by the Riyadh civil date
 * (the API decides the bucket and `days_late`), with exact counts per bucket.
 * Tab, filters, page and the selected follow-up live in the URL hash.
 * Complete / Reschedule are shown only where the user may act (own case, or
 * `follow_ups_manage`); the API enforces the same rule.
 */
import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { HiOutlineClock } from 'react-icons/hi'
import { useTranslation } from 'react-i18next'
import Table from '@/components/ui/Table'
import Button from '@/components/ui/Button'
import ToastNotification from '@/components/shared/myComponents/ToastNotification'
import useHashState, { hashCodec } from '@/utils/hooks/useHashState'
import type { HashSchema } from '@/utils/hooks/useHashState'
import { worklistApi } from '@/services/v2'
import type { FollowUp, FollowUpBucket } from '@/services/v2'
import { useV2Query } from '@/utils/v2/hooks'
import { formatRiyadhDateTime } from '@/utils/v2/format'
import { caseLink } from '@/utils/v2/links'
import { Ltr, RiyadhTime } from '@/components/v2/Display'
import { BucketBadge, PlainBadge } from '@/components/v2/Badges'
import { EmptyState, ErrorState, ForbiddenState, Note, SkeletonRows } from '@/components/v2/States'
import { CountTabs, PageHead, PagerFooter, ScopeFilters, TableFrame, rowClass } from '@/components/v2/ListParts'
import { CompleteFollowUpDialog, RescheduleFollowUpDialog } from './FollowUpDialogs'
import { FollowUpHistoryDialog } from './FollowUpHistory'
import { SourceCell } from './parts'
import { useFollowUpAccess } from './access'

export { CompleteFollowUpDialog, RescheduleFollowUpDialog, CreateFollowUpDialog } from './FollowUpDialogs'
export { FollowUpHistory, FollowUpHistoryDialog } from './FollowUpHistory'
export { default as CaseFollowUpsPanel } from './CaseFollowUpsPanel'

const { THead, TBody, Tr, Th, Td } = Table

const BUCKETS: FollowUpBucket[] = ['late', 'today', 'upcoming']
const PAGE_SIZE = 25

type HashState = {
    tab: FollowUpBucket
    owner: string | number | undefined
    contract: string | number | undefined
    portfolio: string | number | undefined
    page: number
    sel: string | number | undefined
}

const schema: HashSchema<HashState> = {
    tab: { key: 'bucket', codec: hashCodec.oneOf(BUCKETS), default: 'late' },
    owner: { key: 'owner', codec: hashCodec.id(), default: undefined },
    contract: { key: 'contract', codec: hashCodec.id(), default: undefined },
    portfolio: { key: 'portfolio', codec: hashCodec.id(), default: undefined },
    page: {
        key: 'page',
        codec: hashCodec.int({ min: 1, max: 100000 }),
        default: 1,
    },
    sel: {
        key: 'fu',
        codec: hashCodec.id(),
        default: undefined,
        history: 'replace',
    },
}

type Open = null | {
    kind: 'complete' | 'reschedule' | 'history'
    item: FollowUp
}

const FollowUpsScreen = () => {
    const { t } = useTranslation()
    const access = useFollowUpAccess()
    const { state, setState } = useHashState(schema)
    const [open, setOpen] = useState<Open>(null)

    const query = useV2Query(
        access.canView
            ? (signal) =>
                  worklistApi.followUps(
                      {
                          bucket: state.tab,
                          owner: state.owner ? String(state.owner) : undefined,
                          contract_id: state.contract ? String(state.contract) : undefined,
                          portfolio_id: state.portfolio ? String(state.portfolio) : undefined,
                          page: state.page,
                          page_size: PAGE_SIZE,
                      },
                      signal
                  )
            : null,
        [access.canView, state.tab, state.owner, state.contract, state.portfolio, state.page]
    )
    const data = query.data

    const tabs = useMemo(
        () =>
            BUCKETS.map((b) => ({
                value: b,
                label: t(`p1.bucket.${b}`),
                count: data ? data.counts[b] : undefined,
            })),
        [data, t]
    )

    if (!access.canView) {
        return (
            <div data-testid="p1-follow-ups">
                <PageHead title={t('p1.followUps.title')} />
                <ForbiddenState keys={['follow_ups']} />
            </div>
        )
    }

    const select = (item: FollowUp) => setState({ sel: item.id }, { replace: true })
    const act = (kind: 'complete' | 'reschedule' | 'history', item: FollowUp) => {
        select(item)
        setOpen({ kind, item })
    }
    const done = (message: string) => {
        setOpen(null)
        ToastNotification(message, t('p1.followUps.title'), 'success')
        query.reload()
    }

    const cols = 6
    return (
        <div data-testid="p1-follow-ups">
            <PageHead
                title={t('p1.followUps.title')}
                subtitle={
                    data ? (
                        <span dir="auto" data-testid="p1-as-of">
                            {t('p1.list.asOf', { at: formatRiyadhDateTime(data.as_of.at) })}
                        </span>
                    ) : undefined
                }
                onRefresh={query.reload}
                refreshing={query.loading && !!data}
            />

            <ScopeFilters
                owner={state.owner ? String(state.owner) : undefined}
                contract={state.contract ? String(state.contract) : undefined}
                portfolio={state.portfolio ? String(state.portfolio) : undefined}
                onChange={(patch) => setState({ ...patch, page: 1 })}
            />

            <div className="mb-3">
                <CountTabs
                    tabs={tabs}
                    value={state.tab}
                    onChange={(tab) => setState({ tab, page: 1 })}
                    testId="p1-follow-ups-tabs"
                />
            </div>

            <TableFrame
                testId="p1-follow-ups-table"
                footer={
                    data && data.total > 0 ? (
                        <PagerFooter
                            page={state.page}
                            pageSize={PAGE_SIZE}
                            total={data.total}
                            onPage={(page) => setState({ page })}
                        />
                    ) : undefined
                }
            >
                {query.error && !data ? (
                    <ErrorState error={query.error} onRetry={query.reload} />
                ) : (
                    <Table>
                        <THead>
                            <Tr>
                                <Th>{t('p1.col.case')}</Th>
                                <Th>{t('p1.col.due')}</Th>
                                <Th>{t('p1.col.nextAction')}</Th>
                                <Th>{t('p1.col.source')}</Th>
                                <Th>{t('p1.followUps.col.ownerPortfolio')}</Th>
                                <Th className="text-end">{t('p1.col.actions')}</Th>
                            </Tr>
                        </THead>
                        <TBody>
                            {query.loading && !data ? (
                                <SkeletonRows cols={cols} />
                            ) : data && data.items.length > 0 ? (
                                data.items.map((item) => {
                                    const selected = String(state.sel) === item.id
                                    const canAct = access.canActFor(item.owner_user_id)
                                    return (
                                        <Tr
                                            key={item.id}
                                            className={rowClass(selected)}
                                            aria-current={selected ? 'true' : undefined}
                                            onClick={() => select(item)}
                                            data-testid="p1-follow-up-row"
                                            data-follow-up={item.id}
                                            data-case={item.case_id}
                                            data-revision={item.revision}
                                        >
                                            <Td className="min-w-[170px] max-w-[220px]">
                                                {item.case ? (
                                                    <Link
                                                        to={caseLink(item.case.id, 'followUps')}
                                                        onClick={(e) => e.stopPropagation()}
                                                        className="font-extrabold text-navy-600 hover:text-azure-600 hover:underline dark:text-gray-100"
                                                        data-testid="p1-fu-case-link"
                                                    >
                                                        {item.case.name}
                                                    </Link>
                                                ) : (
                                                    <span className="text-gray-500">—</span>
                                                )}
                                                <div className="flex items-center gap-2">
                                                    <Ltr
                                                        className="block max-w-[200px] truncate text-[12.5px] text-gray-600"
                                                        title={item.case?.accountNumber}
                                                    >
                                                        {item.case?.accountNumber}
                                                    </Ltr>
                                                    {item.case?.is_locked && (
                                                        <PlainBadge tone="warning" size="row">
                                                            {t('p1.followUps.locked')}
                                                        </PlainBadge>
                                                    )}
                                                </div>
                                            </Td>
                                            <Td>
                                                <div className="flex flex-col items-start gap-1">
                                                    <RiyadhTime iso={item.due_at} />
                                                    <span
                                                        className="text-[12px] tabular-nums text-gray-600"
                                                        data-testid="p1-fu-reschedules"
                                                        data-count={item.reschedule_count}
                                                    >
                                                        {t('p1.followUps.rescheduledCount', {
                                                            count: item.reschedule_count,
                                                        })}
                                                    </span>
                                                    {item.bucket && (
                                                        <BucketBadge
                                                            bucket={item.bucket}
                                                            daysLate={item.days_late}
                                                            size="row"
                                                        />
                                                    )}
                                                </div>
                                            </Td>
                                            <Td className="max-w-[200px]">
                                                <span
                                                    dir="auto"
                                                    className="line-clamp-2 break-words"
                                                    title={item.next_action || undefined}
                                                >
                                                    {item.next_action || <span className="text-gray-500">—</span>}
                                                </span>
                                            </Td>
                                            <Td>
                                                <SourceCell fu={item} />
                                            </Td>
                                            <Td>
                                                <div
                                                    className="max-w-[180px] truncate text-navy-600 dark:text-gray-100"
                                                    title={item.case?.owner?.name || undefined}
                                                    data-testid="p1-fu-owner"
                                                >
                                                    {item.case?.owner?.name || (
                                                        <span className="text-gray-500">{t('p1.unassigned')}</span>
                                                    )}
                                                </div>
                                                <div
                                                    className="max-w-[180px] truncate text-[12px] text-gray-600"
                                                    title={[item.case?.portfolio?.name, item.case?.contract?.name]
                                                        .filter(Boolean)
                                                        .join(' · ')}
                                                >
                                                    {item.case?.portfolio?.name || '—'}
                                                    {item.case?.contract?.name ? ` · ${item.case.contract.name}` : ''}
                                                </div>
                                            </Td>
                                            <Td className="text-end">
                                                <div className="flex flex-nowrap items-center justify-end gap-1.5">
                                                    {canAct && (
                                                        <>
                                                            <Button
                                                                size="xs"
                                                                variant="solid"
                                                                onClick={(e) => {
                                                                    e.stopPropagation()
                                                                    act('complete', item)
                                                                }}
                                                                data-testid="p1-fu-complete-btn"
                                                            >
                                                                {t('p1.followUps.action.complete')}
                                                            </Button>
                                                            <Button
                                                                size="xs"
                                                                onClick={(e) => {
                                                                    e.stopPropagation()
                                                                    act('reschedule', item)
                                                                }}
                                                                data-testid="p1-fu-reschedule-btn"
                                                            >
                                                                {t('p1.followUps.action.reschedule')}
                                                            </Button>
                                                        </>
                                                    )}
                                                    <Button
                                                        size="xs"
                                                        variant="plain"
                                                        icon={<HiOutlineClock />}
                                                        title={String(t('p1.action.history'))}
                                                        aria-label={String(t('p1.action.history'))}
                                                        onClick={(e) => {
                                                            e.stopPropagation()
                                                            act('history', item)
                                                        }}
                                                        data-testid="p1-fu-history-btn"
                                                    ></Button>
                                                </div>
                                            </Td>
                                        </Tr>
                                    )
                                })
                            ) : (
                                <tr>
                                    <td colSpan={cols}>
                                        <EmptyState
                                            title={t(`p1.followUps.empty.${state.tab}`)}
                                            hint={t('p1.followUps.emptyHint')}
                                        />
                                    </td>
                                </tr>
                            )}
                        </TBody>
                    </Table>
                )}
            </TableFrame>
            {query.error && data ? <ErrorState error={query.error} onRetry={query.reload} /> : null}
            <Note className="mt-3">{t('p1.followUps.listNote')}</Note>

            <CompleteFollowUpDialog
                isOpen={open?.kind === 'complete'}
                followUp={open?.kind === 'complete' ? open.item : null}
                contractId={open?.item.case?.contract?.id ?? null}
                onClose={() => setOpen(null)}
                onReload={query.reload}
                onDone={() => done(t('p1.followUps.toast.completed'))}
            />
            <RescheduleFollowUpDialog
                isOpen={open?.kind === 'reschedule'}
                followUp={open?.kind === 'reschedule' ? open.item : null}
                onClose={() => setOpen(null)}
                onReload={query.reload}
                onDone={() => done(t('p1.followUps.toast.rescheduled'))}
            />
            <FollowUpHistoryDialog
                isOpen={open?.kind === 'history'}
                caseId={open?.kind === 'history' ? open.item.case_id : null}
                caseName={open?.item.case?.name}
                accountNumber={open?.item.case?.accountNumber}
                onClose={() => setOpen(null)}
            />
        </div>
    )
}

export default FollowUpsScreen
