/**
 * Quality overview (`GET /v2/quality/dashboard`): approved reviews only (each recording's current
 * approval), in the caller's scope, with exact counts. Compliance = met / (met + needs
 * improvement), pooled; coverage = (met + needs improvement) / all criteria. When nothing was
 * assessed there is no percentage: "—", explained, never 0 %. Call-level findings, not an employee
 * ranking (there is no grouping by employee). Filters (template, criterion, period, contract,
 * grouping) live in the URL hash.
 */
import { useTranslation } from 'react-i18next'
import Table from '@/components/ui/Table'
import { Ltr } from '@/components/v2/Display'
import { EmptyState, ErrorState, Note, SkeletonLines } from '@/components/v2/States'
import { FilterSelect, useScopeOptions } from '@/components/v2/ListParts'
import { DateFilter } from '@/views/phase1/money/listParts'
import { Segmented } from '@/views/phase1/followUps/parts'
import { aiReasonText, describeP6Error, qualityApi } from '@/services/v2'
import type { QcDashboardEntry, QcOverview, Translate } from '@/services/v2'
import { useV2Query } from '@/utils/v2/hooks'
import { formatCount } from '@/utils/v2/format'
import { Card, Notice } from '@/views/phase5/parts'
import { AiAdminLink, RatioText } from '../parts'
import { capabilityLabel, criterionName, templateName } from '../labels'

const { THead, TBody, Tr, Th, Td } = Table
const describe = (e: unknown, t: Translate) => describeP6Error(e, t)

export type DashFilters = {
    tpl?: string
    crit?: string
    contract?: string
    from?: string
    to?: string
    group: 'day' | 'week' | 'month'
}

const Kpi = ({ label, children, sub, lead, testId }: { label: React.ReactNode; children: React.ReactNode; sub?: React.ReactNode; lead?: boolean; testId?: string }) => (
    <div className={`rounded-lg border border-gray-300 bg-white px-5 py-4 dark:border-night-border dark:bg-night-card ${lead ? 'card-lead' : ''}`} data-testid={testId}>
        <div className="text-eyebrow mb-1">{label}</div>
        <div className="text-[28px] font-extrabold leading-tight text-navy-600 tabular-nums dark:text-gray-100">{children}</div>
        {sub && <div className="mt-1 text-[12px] text-gray-600 tabular-nums">{sub}</div>}
    </div>
)

const CountCells = ({ e }: { e: QcDashboardEntry }) => (
    <>
        <Td className="text-end tabular-nums">{formatCount(e.reviews)}</Td>
        <Td className="text-end tabular-nums">{formatCount(e.met)}</Td>
        <Td className="text-end tabular-nums">{formatCount(e.needs_improvement)}</Td>
        <Td className="text-end tabular-nums">{formatCount(e.not_applicable)}</Td>
        <Td className="text-end tabular-nums">{formatCount(e.insufficient_evidence)}</Td>
        <Td className="text-end">
            <RatioText ratio={e.compliance} />
        </Td>
        <Td className="text-end">
            <RatioText ratio={e.coverage} />
        </Td>
    </>
)

const CountHeads = ({ first, reviewsLabel }: { first: React.ReactNode; reviewsLabel?: React.ReactNode }) => {
    const { t } = useTranslation()
    return (
        <Tr>
            <Th>{first}</Th>
            <Th className="text-end">{reviewsLabel || t('p6.qc.dash.reviews')}</Th>
            <Th className="text-end">{t('p6.result.met')}</Th>
            <Th className="text-end">{t('p6.result.needs_improvement')}</Th>
            <Th className="text-end">{t('p6.result.not_applicable')}</Th>
            <Th className="text-end">{t('p6.result.insufficient_evidence')}</Th>
            <Th className="text-end">{t('p6.qc.metric.compliance')}</Th>
            <Th className="text-end">{t('p6.qc.metric.coverage')}</Th>
        </Tr>
    )
}

const Dashboard = ({ overview, filters, onFilters }: { overview: QcOverview | undefined; filters: DashFilters; onFilters: (f: Partial<DashFilters>) => void }) => {
    const { t, i18n } = useTranslation()
    const scope = useScopeOptions()
    const templates = overview?.templates || []
    const [tkey, tver] = (filters.tpl || '').split('@')
    const tplObj = templates.find((x) => x.key === tkey && String(x.version) === tver) || (tkey ? templates.find((x) => x.key === tkey) : undefined)
    const q = useV2Query(
        (signal) =>
            qualityApi.dashboard(
                {
                    template: tkey || undefined,
                    template_version: tver ? Number(tver) : undefined,
                    contract_id: filters.contract,
                    from: filters.from,
                    to: filters.to,
                    group: filters.group,
                },
                signal
            ),
        [tkey, tver, filters.contract, filters.from, filters.to, filters.group]
    )
    const d = q.data
    const criteriaOptions = (tplObj?.criteria || templates[0]?.criteria || []).map((c) => ({ value: c.id, label: criterionName(c, i18n.language) }))
    const byCriterion = (d?.by_criterion || []).filter((c) => !filters.crit || c.criterion_id === filters.crit)
    const ai = overview?.ai
    return (
        <div className="flex flex-col gap-4" data-testid="p6-qc-dashboard">
            <div className="flex flex-wrap items-end gap-3" data-testid="p6-qc-dash-filters">
                <FilterSelect
                    label={t('p6.qc.template')}
                    options={templates.map((x) => ({ value: `${x.key}@${x.version}`, label: `${templateName(x, i18n.language)} · v${x.version}` }))}
                    value={filters.tpl}
                    width={260}
                    testId="p6-qc-dash-template"
                    onChange={(v) => onFilters({ tpl: v, crit: undefined })}
                />
                <FilterSelect label={t('p6.qc.criterion')} options={criteriaOptions} value={filters.crit} width={240} testId="p6-qc-dash-criterion" onChange={(v) => onFilters({ crit: v })} />
                <FilterSelect label={t('p1.filter.contract')} options={scope.contracts} loading={scope.loading} value={filters.contract} width={220} testId="p6-qc-dash-contract" onChange={(v) => onFilters({ contract: v })} />
                <DateFilter label={t('p6.qc.dash.from')} value={filters.from} max={filters.to} testId="p6-qc-dash-from" onChange={(v) => onFilters({ from: v })} />
                <DateFilter label={t('p6.qc.dash.to')} value={filters.to} min={filters.from} testId="p6-qc-dash-to" onChange={(v) => onFilters({ to: v })} />
                <div>
                    <div className="text-eyebrow mb-1">{t('p6.qc.dash.group')}</div>
                    <Segmented
                        value={filters.group}
                        options={(['day', 'week', 'month'] as const).map((g) => ({ value: g, label: t(`p6.qc.dash.groupValue.${g}`) }))}
                        onChange={(v) => onFilters({ group: v })}
                        testId="p6-qc-dash-group"
                    />
                </div>
            </div>

            {q.loading && !d ? (
                <SkeletonLines rows={8} />
            ) : !d ? (
                <ErrorState error={q.error} onRetry={q.reload} describe={describe} />
            ) : (
                <>
                    <div className="grid grid-cols-2 gap-4 lg:grid-cols-4" data-testid="p6-qc-kpis">
                        <Kpi lead label={t('p6.qc.dash.approvedReviews')} testId="p6-qc-kpi-reviews" sub={t('p6.qc.dash.withoutPct', { count: d.totals.reviews_without_percentage })}>
                            <span data-value={d.totals.reviews}>{formatCount(d.totals.reviews)}</span>
                        </Kpi>
                        <Kpi label={t('p6.qc.metric.compliance')} testId="p6-qc-kpi-compliance" sub={d.totals.compliance ? t('p6.qc.dash.fraction', { n: d.totals.compliance.numerator, d: d.totals.compliance.denominator }) : t('p6.qc.dash.noPct')}>
                            <RatioText ratio={d.totals.compliance} />
                        </Kpi>
                        <Kpi label={t('p6.qc.metric.coverage')} testId="p6-qc-kpi-coverage" sub={d.totals.coverage ? t('p6.qc.dash.fraction', { n: d.totals.coverage.numerator, d: d.totals.coverage.denominator }) : t('p6.qc.dash.noPct')}>
                            <RatioText ratio={d.totals.coverage} />
                        </Kpi>
                        <Kpi label={t('p6.qc.dash.library')} testId="p6-qc-kpi-library" sub={overview ? t('p6.qc.dash.librarySub', { drafts: overview.counts.drafts, approved: overview.counts.approved }) : undefined}>
                            {overview ? formatCount(overview.counts.recordings) : '—'}
                        </Kpi>
                    </div>
                    <Note>{t('p6.qc.dash.formulas')}</Note>

                    <Card title={t('p6.qc.dash.byCriterion')} testId="p6-qc-dash-criteria">
                        {byCriterion.length ? (
                            <div className="overflow-x-auto">
                                <Table>
                                    <THead>
                                        <CountHeads first={t('p6.qc.criterion')} reviewsLabel={t('p6.qc.dash.findings')} />
                                    </THead>
                                    <TBody>
                                        {byCriterion.map((c) => (
                                            <Tr key={`${c.template.key}@${c.template.version}:${c.criterion_id}`} data-testid="p6-qc-dash-criterion-row" data-criterion={c.criterion_id} data-met={c.met} data-reviews={c.reviews}>
                                                <Td className="min-w-[200px]">
                                                    {criterionName(c, i18n.language) || c.criterion_id}
                                                    {c.human_listening_only && <div className="text-[11px] text-warning">{t('p6.qc.flag.humanListening')}</div>}
                                                </Td>
                                                <CountCells e={c} />
                                            </Tr>
                                        ))}
                                    </TBody>
                                </Table>
                            </div>
                        ) : (
                            <EmptyState title={t('p6.qc.dash.empty')} hint={t('p6.qc.dash.emptyHint')} />
                        )}
                    </Card>

                    <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
                        <Card title={t('p6.qc.dash.byPeriod', { group: t(`p6.qc.dash.groupValue.${d.filters.group}`) })} testId="p6-qc-dash-period">
                            {d.by_period.length ? (
                                <div className="overflow-x-auto">
                                    <Table>
                                        <THead>
                                            <CountHeads first={t('p6.qc.dash.period')} />
                                        </THead>
                                        <TBody>
                                            {d.by_period.map((p) => (
                                                <Tr key={p.period} data-period={p.period}>
                                                    <Td>
                                                        <Ltr>{p.period}</Ltr>
                                                    </Td>
                                                    <CountCells e={p} />
                                                </Tr>
                                            ))}
                                        </TBody>
                                    </Table>
                                </div>
                            ) : (
                                <EmptyState title={t('p6.qc.dash.empty')} />
                            )}
                            <Note className="mt-2">{t('p6.qc.dash.periodNote')}</Note>
                        </Card>
                        <Card title={t('p6.qc.dash.byContract')} testId="p6-qc-dash-contract-table">
                            {d.by_contract.length ? (
                                <div className="overflow-x-auto">
                                    <Table>
                                        <THead>
                                            <CountHeads first={t('p1.filter.contract')} />
                                        </THead>
                                        <TBody>
                                            {d.by_contract.map((c) => (
                                                <Tr key={c.contract?.id || 'none'}>
                                                    <Td>{c.contract ? c.contract.name || c.contract.id : <span className="text-gray-500">{t('p6.qc.dash.noContract')}</span>}</Td>
                                                    <CountCells e={c} />
                                                </Tr>
                                            ))}
                                        </TBody>
                                    </Table>
                                </div>
                            ) : (
                                <EmptyState title={t('p6.qc.dash.empty')} />
                            )}
                        </Card>
                    </div>

                    <Card title={t('p6.qc.dash.byTemplate')} testId="p6-qc-dash-templates">
                        {d.by_template.length ? (
                            <div className="overflow-x-auto">
                                <Table>
                                    <THead>
                                        <CountHeads first={t('p6.qc.template')} />
                                    </THead>
                                    <TBody>
                                        {d.by_template.map((x) => (
                                            <Tr key={`${x.template.key}@${x.template.version}`}>
                                                <Td>
                                                    {templateName(x.template, i18n.language)} · v{x.template.version}
                                                </Td>
                                                <CountCells e={x} />
                                            </Tr>
                                        ))}
                                    </TBody>
                                </Table>
                            </div>
                        ) : (
                            <EmptyState title={t('p6.qc.dash.empty')} />
                        )}
                    </Card>
                    <Notice tone="neutral" testId="p6-qc-dash-note">
                        <div>{t('p6.qc.dash.notRanking')}</div>
                        <div className="mt-1 text-[12px] text-gray-600">{t('p6.qc.noPercentHint')}</div>
                    </Notice>
                </>
            )}

            {ai && (
                <Card title={t('p6.qc.dash.aiTitle')} testId="p6-qc-ai-status">
                    <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
                        {(['transcribe', 'draft'] as const).map((k) => {
                            const cap = ai[k]
                            return (
                                <div key={k} data-cap={k} data-ready={cap.ready ? '1' : '0'}>
                                    <div className="text-eyebrow mb-1">{capabilityLabel(t, k === 'draft' ? 'qc_draft' : 'transcribe')}</div>
                                    <div className={cap.ready ? 'font-extrabold text-success' : 'font-extrabold text-warning'}>{cap.ready ? t('p6.admin.ready') : t('p6.admin.notReady')}</div>
                                    {cap.reasons.length > 0 && (
                                        <ul className="mt-1 list-disc ps-5 text-[12.5px]">
                                            {cap.reasons.map((r) => (
                                                <li key={r}>{aiReasonText(r, null, t)}</li>
                                            ))}
                                        </ul>
                                    )}
                                </div>
                            )
                        })}
                    </div>
                    <div className="mt-2 text-[12px] text-gray-600">{t('p6.qc.dash.aiNote')}</div>
                    <AiAdminLink />
                </Card>
            )}
        </div>
    )
}

export default Dashboard
