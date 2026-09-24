/**
 * AI administration (`/ai-admin`, key `ai_admin`; Intelligence & control).
 *
 * - Readiness (`GET /v2/ai/status` with the admin details): each capability (drafts,
 *   transcription, the voice lab's realtime session) ready or not, with every reason the API
 *   gives; the configuration WITHOUT the key (only whether one is configured), the models, the
 *   timeouts and limits; today's quota; and "Check the connection" (`POST /v2/ai/check`, one
 *   fixed sentence, no data), which answers the honest refusal while nothing is configured.
 * - Data policy (`GET/PUT /v2/ai/policy`): categories that may be sent, masking per identifier
 *   class, retention days, a required reason and an explicit confirmation; saved as a new
 *   revision against the one loaded (409 on a stale edit). History of every revision.
 * - Provider calls (`GET /v2/ai/provider-calls`): the audit, metadata only (no prompt, no
 *   output, no transcript, no key); filters and paging in the URL hash.
 */
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Activity } from 'lucide-react'
import Button from '@/components/ui/Button'
import { Ltr, RiyadhTime } from '@/components/v2/Display'
import { ErrorState, SkeletonLines } from '@/components/v2/States'
import { CountTabs, PageHead } from '@/components/v2/ListParts'
import useHashState, { hashCodec } from '@/utils/hooks/useHashState'
import type { HashSchema } from '@/utils/hooks/useHashState'
import { AI_CAPABILITIES, aiApi, aiReasonText, categoryLabel, describeP6Error, newIdempotencyKey } from '@/services/v2'
import type { AiCheckResult, AiStatus, Translate } from '@/services/v2'
import { useV2Query } from '@/utils/v2/hooks'
import { formatCount } from '@/utils/v2/format'
import { Card, Fact, Notice } from '@/views/phase5/parts'
import { AiErrorNotice, QuotaLine } from '../parts'
import { capabilityLabel } from '../labels'
import PolicyEditor, { PolicyHistory } from './PolicyEditor'
import ProviderCalls from './ProviderCalls'

type Tab = 'readiness' | 'policy' | 'history' | 'calls'
const TABS: Tab[] = ['readiness', 'policy', 'history', 'calls']
type HashState = {
    tab: Tab
    outcome: string | undefined
    capability: string | undefined
    purpose: string | undefined
    page: number
}
const schema: HashSchema<HashState> = {
    tab: { key: 'tab', codec: hashCodec.oneOf(TABS), default: 'readiness' },
    outcome: { key: 'outcome', codec: hashCodec.text({ maxLength: 20 }), default: undefined },
    capability: { key: 'cap', codec: hashCodec.text({ maxLength: 40 }), default: undefined },
    purpose: { key: 'purpose', codec: hashCodec.text({ maxLength: 60 }), default: undefined },
    page: { key: 'page', codec: hashCodec.int({ min: 1, max: 100000 }), default: 1 },
}
const describe = (e: unknown, t: Translate) => describeP6Error(e, t)

const YesNo = ({ value, testId }: { value: boolean; testId?: string }) => {
    const { t } = useTranslation()
    return (
        <span data-testid={testId} data-value={value ? '1' : '0'} className={value ? 'font-extrabold text-success' : 'font-extrabold text-danger'}>
            {value ? t('p5.yes') : t('p5.no')}
        </span>
    )
}

const CheckPanel = ({ onDone }: { onDone: () => void }) => {
    const { t } = useTranslation()
    const [busy, setBusy] = useState(false)
    const [result, setResult] = useState<AiCheckResult | null>(null)
    const [error, setError] = useState<unknown>(null)
    const run = async () => {
        setBusy(true)
        setError(null)
        setResult(null)
        try {
            setResult(await aiApi.check(newIdempotencyKey()))
        } catch (e) {
            setError(e)
        } finally {
            setBusy(false)
            onDone()
        }
    }
    return (
        <Card title={t('p6.admin.check.title')} testId="p6-admin-check">
            <p className="mb-3 text-[13px] text-gray-700 dark:text-night-body">{t('p6.admin.check.hint')}</p>
            <Button size="sm" icon={<Activity size={15} />} loading={busy} onClick={run} data-testid="p6-admin-check-run">
                {t('p6.admin.check.run')}
            </Button>
            {result && (
                <Notice tone={result.ok ? 'success' : 'warning'} className="mt-3" testId="p6-admin-check-result">
                    <div data-ok={result.ok ? '1' : '0'}>{result.ok ? t('p6.admin.check.ok') : t('p6.admin.check.notOk')}</div>
                    <div className="mt-1 text-[12px] text-gray-600">
                        {t('p6.admin.check.detail', {
                            model: result.model || '—',
                            revision: result.policy_revision,
                            latency: result.latency_ms ?? '—',
                            attempts: result.attempts,
                        })}
                    </div>
                </Notice>
            )}
            <AiErrorNotice error={error} className="mt-3" testId="p6-admin-check-error" />
        </Card>
    )
}

const Readiness = ({ status, reload }: { status: AiStatus; reload: () => void }) => {
    const { t } = useTranslation()
    const cfg = status.configuration
    const pol = status.policy
    return (
        <div className="flex flex-col gap-4" data-testid="p6-admin-readiness" data-reasons={status.reasons.join(',')}>
            <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
                {AI_CAPABILITIES.map((cap, i) => {
                    const c = status.capabilities[cap]
                    return (
                        <Card
                            key={cap}
                            lead={i === 0}
                            title={capabilityLabel(t, cap)}
                            testId={`p6-admin-cap-${cap}`}
                            actions={
                                <span
                                    data-ready={c.ready ? '1' : '0'}
                                    className={c.ready ? 'text-[12.5px] font-extrabold text-success' : 'text-[12.5px] font-extrabold text-warning'}
                                >
                                    {c.ready ? t('p6.admin.ready') : t('p6.admin.notReady')}
                                </span>
                            }
                        >
                            <div className="mb-2 text-[12.5px] text-gray-600">{t(`p6.capabilityHint.${cap}`)}</div>
                            <div className="grid grid-cols-1 gap-2">
                                <Fact label={t('p6.admin.model')}>{c.model ? <Ltr>{c.model}</Ltr> : <span className="text-gray-500">{t('p6.admin.notSet')}</span>}</Fact>
                                <Fact label={t('p6.admin.requiredCategories')}>
                                    {c.required_categories.length ? c.required_categories.map((x) => categoryLabel(t, x)).join(String(t('p6.listSep'))) : t('p6.admin.noCategory')}
                                </Fact>
                            </div>
                            {c.reasons.length > 0 && (
                                <ul className="mt-3 list-disc ps-5 text-[13px] text-gray-800 dark:text-night-body" data-testid="p6-admin-cap-reasons" data-reasons={c.reasons.join(',')}>
                                    {c.reasons.map((r) => (
                                        <li key={r} data-reason={r}>
                                            {aiReasonText(r, null, t)}
                                        </li>
                                    ))}
                                </ul>
                            )}
                        </Card>
                    )
                })}
            </div>
            <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
                <Card title={t('p6.admin.configuration')} testId="p6-admin-config">
                    {cfg ? (
                        <div className="grid grid-cols-2 gap-3">
                            <Fact label={t('p6.admin.provider')}>
                                <Ltr>{cfg.provider}</Ltr>
                            </Fact>
                            <Fact label={t('p6.admin.keyConfigured')}>
                                <YesNo value={cfg.key_configured} testId="p6-admin-key-configured" />
                            </Fact>
                            <Fact label={t('p6.admin.orgConfigured')}>
                                <YesNo value={cfg.organization_configured} />
                            </Fact>
                            <Fact label={t('p6.admin.projectConfigured')}>
                                <YesNo value={cfg.project_configured} />
                            </Fact>
                            <Fact label={t('p6.admin.baseUrl')}>
                                <Ltr className="break-all text-[12.5px]">{cfg.base_url}</Ltr>
                                {cfg.base_url_is_default ? <span className="ms-1 text-[11.5px] text-gray-500">({t('p6.admin.default')})</span> : null}
                                {cfg.base_url_problem && <div className="text-[12px] text-danger">{cfg.base_url_problem}</div>}
                            </Fact>
                            <Fact label={t('p6.admin.retries')}>{cfg.max_retries}</Fact>
                            <Fact label={t('p6.admin.timeouts')}>
                                <Ltr className="text-[12.5px]">
                                    {AI_CAPABILITIES.map((k) => `${cfg.timeouts_seconds[k]} s`).join(' / ')}
                                </Ltr>
                            </Fact>
                            <Fact label={t('p6.admin.limits')}>
                                {t('p6.admin.limitsValue', { chars: formatCount(cfg.max_input_chars), mb: (cfg.transcribe_max_bytes / (1024 * 1024)).toFixed(0) })}
                            </Fact>
                        </div>
                    ) : (
                        <div className="text-[13px] text-gray-600">{t('p6.admin.noDetails')}</div>
                    )}
                    <div className="mt-3 text-[11.5px] text-gray-500">{t('p6.admin.keyNote')}</div>
                </Card>
                <div className="flex flex-col gap-4">
                    <Card title={t('p6.admin.policySummary')} testId="p6-admin-policy-summary">
                        <div className="grid grid-cols-2 gap-3" data-policy-state={pol.state} data-policy-revision={pol.revision}>
                            <Fact label={t('p6.policy.state')}>{t(`p6.policy.stateValue.${pol.state}`)}</Fact>
                            <Fact label={t('p6.policy.revision')}>
                                <Ltr>{pol.revision || '—'}</Ltr>
                            </Fact>
                            <Fact label={t('p6.policy.categories')} className="col-span-2">
                                {pol.allowed_categories.length ? pol.allowed_categories.map((x) => categoryLabel(t, x)).join(String(t('p6.listSep'))) : t('p6.policy.noCategories')}
                            </Fact>
                        </div>
                        {pol.state === 'missing' && (
                            <Notice tone="warning" className="mt-3" testId="p6-admin-no-policy">
                                {t('p6.policy.missing')}
                            </Notice>
                        )}
                    </Card>
                    <Card title={t('p6.admin.quota')} testId="p6-admin-quota">
                        {status.quota ? (
                            <>
                                <div className="grid grid-cols-2 gap-3">
                                    <Fact label={t('p6.quota.user')} testId="p6-admin-quota-user">
                                        {status.quota.user ? (
                                            <Ltr>
                                                {status.quota.user.used} / {status.quota.user.limit}
                                            </Ltr>
                                        ) : (
                                            '—'
                                        )}
                                    </Fact>
                                    <Fact label={t('p6.quota.total')} testId="p6-admin-quota-total">
                                        <Ltr>
                                            {status.quota.total.used} / {status.quota.total.limit}
                                        </Ltr>
                                    </Fact>
                                    <Fact label={t('p6.quota.day')}>
                                        <Ltr>{status.quota.day}</Ltr>
                                    </Fact>
                                    <Fact label={t('p6.quota.resetsAt')}>
                                        <RiyadhTime iso={status.quota.resets_at} />
                                    </Fact>
                                </div>
                                {status.quota.exceeded && (
                                    <Notice tone="warning" className="mt-3">
                                        {t(`p6.quota.exceeded_${status.quota.exceeded}`)}
                                    </Notice>
                                )}
                                <div className="mt-2 text-[11.5px] text-gray-500">{t('p6.quota.note')}</div>
                            </>
                        ) : (
                            <QuotaLine quota={null} />
                        )}
                    </Card>
                </div>
            </div>
            <CheckPanel onDone={reload} />
        </div>
    )
}

const AiAdmin = () => {
    const { t } = useTranslation()
    const { state, setState } = useHashState(schema)
    const status = useV2Query((signal) => aiApi.status(signal), [])
    const [historyTick, setHistoryTick] = useState(0)
    return (
        <div data-testid="p6-ai-admin">
            <PageHead
                title={t('p6.admin.title')}
                subtitle={t('p6.admin.subtitle')}
                onRefresh={status.reload}
                refreshing={status.loading && !!status.data}
            />
            <div className="mb-4">
                <CountTabs<Tab>
                    tabs={TABS.map((v) => ({ value: v, label: t(`p6.admin.tab.${v}`) }))}
                    value={state.tab}
                    onChange={(tab) => setState({ tab, page: 1 })}
                    testId="p6-admin-tabs"
                />
            </div>
            {state.tab === 'readiness' &&
                (status.loading && !status.data ? (
                    <SkeletonLines rows={8} />
                ) : status.data ? (
                    <Readiness status={status.data} reload={status.reload} />
                ) : (
                    <ErrorState error={status.error} onRetry={status.reload} describe={describe} />
                ))}
            {state.tab === 'policy' && (
                <PolicyEditor
                    onSaved={() => {
                        status.reload()
                        setHistoryTick((n) => n + 1)
                    }}
                />
            )}
            {state.tab === 'history' && <PolicyHistory tick={historyTick} />}
            {state.tab === 'calls' && (
                <ProviderCalls
                    filters={{ outcome: state.outcome, capability: state.capability, purpose: state.purpose }}
                    page={state.page}
                    onFilters={(patch) => setState({ ...patch, page: 1 })}
                    onPage={(page) => setState({ page })}
                />
            )}
        </div>
    )
}

export default AiAdmin
