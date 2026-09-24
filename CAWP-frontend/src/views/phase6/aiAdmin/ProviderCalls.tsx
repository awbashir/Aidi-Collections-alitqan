/**
 * The provider-call audit (`GET /v2/ai/provider-calls`, key `ai_admin`): one row per provider
 * action, refused ones included. Metadata only: purpose, capability, model, outcome and error
 * code, the data categories declared, how many identifiers were masked, token counts, latency
 * and the actor. There is no prompt, output, transcript, audio or key in it. A count the
 * provider did not report is unknown ("—"), never 0.
 */
import { useTranslation } from 'react-i18next'
import Table from '@/components/ui/Table'
import { Ltr, RiyadhTime } from '@/components/v2/Display'
import { EmptyState, ErrorState, Note, SkeletonRows } from '@/components/v2/States'
import { FilterSelect, PagerFooter, TableFrame } from '@/components/v2/ListParts'
import { AI_CALL_OUTCOMES, AI_CAPABILITIES, aiApi, categoryLabel, describeP6Error } from '@/services/v2'
import type { AiCallOutcome } from '@/services/v2'
import { useV2Query } from '@/utils/v2/hooks'
import { formatCount } from '@/utils/v2/format'
import { CallOutcomeBadge } from '../parts'
import { capabilityLabel, purposeLabel } from '../labels'

const { THead, TBody, Tr, Th, Td } = Table
const PAGE_SIZE = 25
const PURPOSES = [
    'connectivity_check',
    'ai_draft_call_script',
    'ai_draft_case_guidance',
    'ai_draft_narrative',
    'ai_draft_followup_message',
    'ai_draft_call_summary',
    'ai_draft_coaching',
    'qc_transcription',
    'qc_review_draft',
    'voice_lab',
]

type Filters = { outcome?: string; capability?: string; purpose?: string }

const num = (v: number | null | undefined) => (v === null || v === undefined ? '—' : formatCount(v))

const ProviderCalls = ({
    filters,
    page,
    onFilters,
    onPage,
}: {
    filters: Filters
    page: number
    onFilters: (patch: Filters) => void
    onPage: (p: number) => void
}) => {
    const { t } = useTranslation()
    const outcome = filters.outcome && (AI_CALL_OUTCOMES as readonly string[]).includes(filters.outcome) ? filters.outcome : undefined
    const capability = filters.capability && (AI_CAPABILITIES as readonly string[]).includes(filters.capability) ? filters.capability : undefined
    const purpose = filters.purpose || undefined
    const q = useV2Query(
        (signal) => aiApi.providerCalls({ outcome, capability, purpose, page, page_size: PAGE_SIZE }, signal),
        [outcome, capability, purpose, page]
    )
    const data = q.data
    const cols = 9
    return (
        <div data-testid="p6-provider-calls">
            <div className="mb-4 flex flex-wrap items-end gap-3">
                <FilterSelect
                    label={t('p6.calls.outcome')}
                    options={AI_CALL_OUTCOMES.map((o) => ({ value: o, label: String(t(`p6.callOutcome.${o}`)) }))}
                    value={outcome}
                    width={180}
                    testId="p6-calls-outcome"
                    onChange={(v) => onFilters({ outcome: v })}
                />
                <FilterSelect
                    label={t('p6.calls.capability')}
                    options={AI_CAPABILITIES.map((c) => ({ value: c, label: String(capabilityLabel(t, c)) }))}
                    value={capability}
                    width={220}
                    testId="p6-calls-capability"
                    onChange={(v) => onFilters({ capability: v })}
                />
                <FilterSelect
                    label={t('p6.calls.purpose')}
                    options={PURPOSES.map((p) => ({ value: p, label: String(purposeLabel(t, p)) }))}
                    value={purpose}
                    width={260}
                    testId="p6-calls-purpose"
                    onChange={(v) => onFilters({ purpose: v })}
                />
            </div>
            <TableFrame
                testId="p6-calls-table"
                footer={data && data.total > 0 ? <PagerFooter page={page} pageSize={PAGE_SIZE} total={data.total} onPage={onPage} /> : undefined}
            >
                {q.error && !data ? (
                    <ErrorState error={q.error} onRetry={q.reload} describe={(e, tt) => describeP6Error(e, tt)} />
                ) : (
                    <Table>
                        <THead>
                            <Tr>
                                <Th>{t('p6.calls.started')}</Th>
                                <Th>{t('p6.calls.purpose')}</Th>
                                <Th>{t('p6.calls.model')}</Th>
                                <Th>{t('p6.calls.outcome')}</Th>
                                <Th>{t('p6.calls.categories')}</Th>
                                <Th className="text-end">{t('p6.calls.masked')}</Th>
                                <Th className="text-end">{t('p6.calls.tokens')}</Th>
                                <Th className="text-end">{t('p6.calls.latency')}</Th>
                                <Th>{t('p6.calls.actor')}</Th>
                            </Tr>
                        </THead>
                        <TBody>
                            {q.loading && !data ? (
                                <SkeletonRows cols={cols} rows={4} />
                            ) : data && data.items.length > 0 ? (
                                data.items.map((c) => (
                                    <Tr key={c.id} data-testid="p6-call-row" data-outcome={c.outcome} data-error={c.error_code || ''} data-purpose={c.purpose}>
                                        <Td>
                                            <RiyadhTime iso={c.started_at} />
                                        </Td>
                                        <Td className="min-w-[180px] text-[12.5px]">
                                            <div>{purposeLabel(t, c.purpose)}</div>
                                            <div className="text-gray-600">{capabilityLabel(t, c.capability)}</div>
                                        </Td>
                                        <Td className="text-[12.5px]">{c.model ? <Ltr>{c.model}</Ltr> : <span className="text-gray-500">—</span>}</Td>
                                        <Td>
                                            <CallOutcomeBadge outcome={c.outcome as AiCallOutcome} size="row" />
                                            {c.error_code && (
                                                <div className="mt-0.5 text-[11.5px] text-gray-600">
                                                    {t(`p6.reason.${c.error_code}`, { defaultValue: c.error_code.replace(/_/g, ' ') })}
                                                </div>
                                            )}
                                        </Td>
                                        <Td className="text-[12.5px]">{c.data_categories.length ? c.data_categories.map((x) => categoryLabel(t, x)).join(String(t('p6.listSep'))) : '—'}</Td>
                                        <Td className="text-end tabular-nums">{c.masked ? num(c.masked_count) : t('p6.calls.notMasked')}</Td>
                                        <Td className="text-end tabular-nums" title={`${num(c.usage.input_tokens)} / ${num(c.usage.output_tokens)}`}>
                                            {num(c.usage.total_tokens)}
                                        </Td>
                                        <Td className="text-end tabular-nums">{c.latency_ms === null ? '—' : `${formatCount(c.latency_ms)} ms`}</Td>
                                        <Td className="text-[12.5px]">{c.actor?.name || '—'}</Td>
                                    </Tr>
                                ))
                            ) : (
                                <tr>
                                    <td colSpan={cols}>
                                        <EmptyState title={t('p6.calls.empty')} hint={t('p6.calls.emptyHint')} />
                                    </td>
                                </tr>
                            )}
                        </TBody>
                    </Table>
                )}
            </TableFrame>
            <Note className="mt-3">{t('p6.calls.note')}</Note>
        </div>
    )
}

export default ProviderCalls
