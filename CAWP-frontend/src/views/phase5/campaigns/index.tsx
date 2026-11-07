/**
 * Campaigns (`/campaigns`, derived `campaigns_any` = campaigns | campaigns_manage; Daily
 * operations): the campaigns I may see (`GET /v2/campaigns`: a manager sees the ones of
 * their team, a member the ones they are on), filtered by status in the URL hash, and
 * "New campaign" (`campaigns_manage`). A row opens the campaign (`/campaigns/:id`).
 * The clients list can hand its current filter over ("New campaign from this filter").
 */
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { HiOutlinePlus } from 'react-icons/hi'
import Button from '@/components/ui/Button'
import Table from '@/components/ui/Table'
import useHashState, { hashCodec } from '@/utils/hooks/useHashState'
import type { HashSchema } from '@/utils/hooks/useHashState'
import { campaignsApi, describeP5Error } from '@/services/v2'
import type { CampaignStatus, Translate } from '@/services/v2'
import { usePermissions, useV2Query } from '@/utils/v2/hooks'
import { RiyadhTime } from '@/components/v2/Display'
import { EmptyState, ErrorState, Note, SkeletonRows } from '@/components/v2/States'
import { CountTabs, PageHead, PagerFooter, TableFrame, rowClass } from '@/components/v2/ListParts'
import { CampaignStatusBadge } from '../parts'
import CampaignForm from './CampaignForm'

const { THead, TBody, Tr, Th, Td } = Table
type Tab = 'active' | CampaignStatus | 'all'
const TABS: Tab[] = ['active', 'draft', 'running', 'paused', 'draining', 'stopped', 'all']
const TAB_STATUS: Record<Tab, string | undefined> = {
    active: 'draft,running,paused,draining',
    draft: 'draft',
    running: 'running',
    paused: 'paused',
    draining: 'draining',
    stopped: 'stopped',
    all: undefined,
}
const PAGE_SIZE = 25

type HashState = { tab: Tab; page: number }
const schema: HashSchema<HashState> = {
    tab: { key: 'status', codec: hashCodec.oneOf(TABS), default: 'active' },
    page: { key: 'page', codec: hashCodec.int({ min: 1, max: 100000 }), default: 1 },
}
const describe = (e: unknown, t: Translate) => describeP5Error(e, t)

export type CampaignLaunchState = { newCampaign?: { filter?: Record<string, unknown> } }

const Campaigns = () => {
    const { t } = useTranslation()
    const perms = usePermissions()
    const navigate = useNavigate()
    const location = useLocation()
    const { state, setState } = useHashState(schema)
    const launch = (location.state as CampaignLaunchState | null)?.newCampaign
    const [creating, setCreating] = useState(false)
    const [launchFilter, setLaunchFilter] = useState<Record<string, unknown> | undefined>(undefined)
    useEffect(() => {
        if (launch && perms.campaigns_manage) {
            setLaunchFilter(launch.filter)
            setCreating(true)
            /* consume the router state so a reload does not reopen the form */
            navigate({ pathname: location.pathname, hash: location.hash }, { replace: true, state: null })
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [launch])
    const q = useV2Query((signal) => campaignsApi.list({ status: TAB_STATUS[state.tab], page: state.page, page_size: PAGE_SIZE }, signal), [state.tab, state.page])
    const data = q.data
    const cols = 7
    return (
        <div data-testid="p5-campaigns">
            <PageHead
                title={t('p5.campaigns.title')}
                subtitle={t('p5.campaigns.subtitle')}
                onRefresh={q.reload}
                refreshing={q.loading && !!data}
                actions={
                    perms.campaigns_manage ? (
                        <Button
                            size="sm"
                            variant="solid"
                            icon={<HiOutlinePlus />}
                            onClick={() => {
                                setLaunchFilter(undefined)
                                setCreating(true)
                            }}
                            data-testid="p5-campaigns-new"
                        >
                            {t('p5.campaigns.new')}
                        </Button>
                    ) : undefined
                }
            />
            <div className="mb-3">
                <CountTabs<Tab>
                    tabs={TABS.map((tab) => ({ value: tab, label: t(`p5.campaigns.tab.${tab}`) }))}
                    value={state.tab}
                    onChange={(tab) => setState({ tab, page: 1 })}
                    testId="p5-campaigns-tabs"
                />
            </div>
            <TableFrame
                testId="p5-campaigns-table"
                footer={data && data.total > 0 ? <PagerFooter page={state.page} pageSize={PAGE_SIZE} total={data.total} onPage={(page) => setState({ page })} /> : undefined}
            >
                {q.error && !data ? (
                    <ErrorState error={q.error} onRetry={q.reload} describe={describe} />
                ) : (
                    <Table>
                        <THead>
                            <Tr>
                                <Th>{t('p5.campaigns.col.name')}</Th>
                                <Th>{t('p5.campaigns.col.status')}</Th>
                                <Th>{t('p5.campaigns.col.mode')}</Th>
                                <Th className="text-end">{t('p5.campaigns.col.members')}</Th>
                                <Th>{t('p5.campaigns.col.createdBy')}</Th>
                                <Th>{t('p5.campaigns.col.started')}</Th>
                                <Th>{t('p5.campaigns.col.mine')}</Th>
                            </Tr>
                        </THead>
                        <TBody>
                            {q.loading && !data ? (
                                <SkeletonRows cols={cols} />
                            ) : data && data.items.length > 0 ? (
                                data.items.map((c) => (
                                    <Tr
                                        key={c.id}
                                        className={rowClass(false)}
                                        onClick={() => navigate(perms.campaigns_manage ? `/campaigns/${c.id}` : `/calling#campaign=${c.id}`)}
                                        data-testid="p5-campaign-row"
                                        data-campaign={c.id}
                                        data-status={c.status}
                                    >
                                        <Td className="min-w-[220px] font-extrabold text-navy-600 dark:text-gray-100">
                                            <span dir="auto">{c.name}</span>
                                        </Td>
                                        <Td>
                                            <CampaignStatusBadge status={c.status} size="row" />
                                            {perms.campaigns_manage && (c.status === 'stopped' || c.status === 'draining') && (
                                                <Link
                                                    to={`/campaigns/${c.id}#tab=report`}
                                                    onClick={(e) => e.stopPropagation()}
                                                    className="ms-2 whitespace-nowrap text-[12px] font-extrabold text-azure-600 hover:underline"
                                                    data-testid="p11-campaign-row-report"
                                                >
                                                    {t('p11.campreport.open')}
                                                </Link>
                                            )}
                                        </Td>
                                        <Td>{t(`p5.campaign.mode.${c.settings.next_mode}`)}</Td>
                                        <Td className="text-end tabular-nums">
                                            {c.members.active}
                                            {c.members.total !== c.members.active && <span className="text-gray-500"> / {c.members.total}</span>}
                                        </Td>
                                        <Td>{c.created_by?.name || '—'}</Td>
                                        <Td>
                                            <RiyadhTime iso={c.started_at} />
                                        </Td>
                                        <Td>{c.my_membership?.active ? t('p5.campaigns.member') : '—'}</Td>
                                    </Tr>
                                ))
                            ) : (
                                <tr>
                                    <td colSpan={cols}>
                                        <EmptyState title={t('p5.campaigns.emptyTitle')} hint={perms.campaigns_manage ? t('p5.campaigns.emptyHintManage') : t('p5.campaigns.emptyHint')} />
                                    </td>
                                </tr>
                            )}
                        </TBody>
                    </Table>
                )}
            </TableFrame>
            <Note className="mt-3">{t('p5.campaigns.footNote')}</Note>
            {perms.campaigns_manage && (
                <CampaignForm
                    isOpen={creating}
                    campaign={null}
                    initialFilter={launchFilter}
                    onClose={() => setCreating(false)}
                    onSaved={(c) => navigate(c.pool.case_list || c.kind === 'message' ? `/campaigns/${c.id}#tab=pool` : `/campaigns/${c.id}`)}
                />
            )}
        </div>
    )
}

export default Campaigns
