/**
 * The AI data policy (`GET/PUT /v2/ai/policy`, `GET /v2/ai/policy/history`; key `ai_admin`).
 *
 * No provider call is made without an approved policy. The form is a full replacement of the
 * policy: provider, state (approved / suspended), the data categories that may be sent, masking
 * per customer-identifier class (on = masked before sending), retention days, a required reason
 * and an explicit confirmation. It is saved against the revision that was loaded, so a stale
 * edit is refused (409) and nothing is overwritten silently. Every revision stays in history.
 */
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import Button from '@/components/ui/Button'
import Input from '@/components/ui/Input'
import Table from '@/components/ui/Table'
import { DialogField } from '@/components/v2/ActionDialog'
import { Ltr, RiyadhTime } from '@/components/v2/Display'
import { EmptyState, ErrorState, InlineError, SkeletonLines, SkeletonRows } from '@/components/v2/States'
import { PagerFooter, TableFrame } from '@/components/v2/ListParts'
import ToastNotification from '@/components/shared/myComponents/ToastNotification'
import { Segmented } from '@/views/phase1/followUps/parts'
import { aiApi, categoryLabel, describeP6Error, isV2Error } from '@/services/v2'
import type { AiMasking, AiPolicy, AiPolicyView, AiRetention, Translate } from '@/services/v2'
import { useActionKey, useV2Query } from '@/utils/v2/hooks'
import { Card, Fact, Notice } from '@/views/phase5/parts'
import { CheckField } from '../parts'

const { THead, TBody, Tr, Th, Td } = Table
const MASK_KEYS: (keyof AiMasking)[] = ['name', 'identity_number', 'phone', 'account']
const RETENTION_KEYS: (keyof AiRetention)[] = ['transcripts', 'audio', 'drafts', 'provider_logs']

type Form = {
    state: 'approved' | 'suspended'
    provider: string
    categories: string[]
    masking: AiMasking
    retention: Record<keyof AiRetention, string>
    reason: string
    confirmed: boolean
}

/** A new policy starts with every identifier masked and no category allowed. */
const fromPolicy = (p: AiPolicy | null, providers: string[]): Form => ({
    state: p?.state || 'approved',
    provider: p?.provider || providers[0] || 'openai',
    categories: p ? [...p.allowed_categories] : [],
    masking: p ? { ...p.masking } : { name: true, identity_number: true, phone: true, account: true },
    retention: {
        transcripts: String(p?.retention_days.transcripts ?? 30),
        audio: String(p?.retention_days.audio ?? 30),
        drafts: String(p?.retention_days.drafts ?? 14),
        provider_logs: String(p?.retention_days.provider_logs ?? 365),
    },
    reason: '',
    confirmed: false,
})

/** Field errors of a 422 in the policy's own words. */
function describePolicyError(err: unknown, t: Translate): string {
    if (isV2Error(err) && err.code === 'validation_failed' && err.detail && typeof err.detail === 'object') {
        const parts = Object.entries(err.detail as Record<string, unknown>).map(
            ([k, v]) => `${t(`p6.policy.field.${k.replace(/\./g, '_')}`, { defaultValue: k })}: ${String(v)}`
        )
        if (parts.length) return t('p1.err.validation_failed_fields', { fields: parts.join(' · ') })
    }
    return describeP6Error(err, t)
}

const CurrentPolicy = ({ view }: { view: AiPolicyView }) => {
    const { t } = useTranslation()
    const p = view.policy
    if (!p) {
        return (
            <Notice tone="warning" title={t('p6.policy.noneTitle')} testId="p6-policy-none">
                {t('p6.policy.missing')}
            </Notice>
        )
    }
    return (
        <Card title={t('p6.policy.currentTitle', { revision: p.revision })} testId="p6-policy-current" lead>
            <div className="grid grid-cols-2 gap-3 md:grid-cols-4" data-revision={p.revision} data-state={p.state}>
                <Fact label={t('p6.policy.state')}>{t(`p6.policy.stateValue.${p.state}`)}</Fact>
                <Fact label={t('p6.policy.provider')}>
                    <Ltr>{p.provider}</Ltr>
                </Fact>
                <Fact label={t('p6.policy.approvedBy')}>{p.approved_by?.name || '—'}</Fact>
                <Fact label={t('p6.policy.approvedAt')}>
                    <RiyadhTime iso={p.approved_at} />
                </Fact>
                <Fact label={t('p6.policy.categories')} className="col-span-2">
                    <span data-testid="p6-policy-current-categories" data-categories={p.allowed_categories.join(',')}>
                        {p.allowed_categories.length ? p.allowed_categories.map((c) => categoryLabel(t, c)).join(String(t('p6.listSep'))) : t('p6.policy.noCategories')}
                    </span>
                </Fact>
                <Fact label={t('p6.policy.masking')} className="col-span-2">
                    {MASK_KEYS.filter((k) => p.masking[k])
                        .map((k) => t(`p6.policy.mask.${k}`))
                        .join(String(t('p6.listSep'))) || t('p6.policy.maskNone')}
                </Fact>
                <Fact label={t('p6.policy.retention')} className="col-span-2 md:col-span-4">
                    {RETENTION_KEYS.map((k) => t('p6.policy.retentionItem', { what: t(`p6.policy.retentionKey.${k}`), days: p.retention_days[k] })).join(
                        String(t('p6.listSep'))
                    )}
                </Fact>
                <Fact label={t('p6.policy.reason')} className="col-span-2 md:col-span-4">
                    <span dir="auto" className="whitespace-pre-wrap">
                        {p.reason || '—'}
                    </span>
                </Fact>
            </div>
            {p.state === 'suspended' && (
                <Notice tone="warning" className="mt-3">
                    {t('p6.policy.suspendedNote')}
                </Notice>
            )}
        </Card>
    )
}

const PolicyEditor = ({ onSaved }: { onSaved: () => void }) => {
    const { t } = useTranslation()
    const q = useV2Query((signal) => aiApi.policy(signal), [])
    const view = q.data
    const [form, setForm] = useState<Form | null>(null)
    const [busy, setBusy] = useState(false)
    const [error, setError] = useState<unknown>(null)
    const { key, renew } = useActionKey(true)
    useEffect(() => {
        if (view) setForm(fromPolicy(view.policy, view.options.providers))
    }, [view])

    if (q.loading && !view) return <SkeletonLines rows={8} />
    if (!view) return <ErrorState error={q.error} onRetry={q.reload} describe={(e, tt) => describeP6Error(e, tt)} />
    if (!form) return <SkeletonLines rows={8} />
    const opts = view.options
    const retentionValid = RETENTION_KEYS.every((k) => {
        const v = form.retention[k]
        const n = Number(v)
        return /^\d{1,4}$/.test(v) && n >= opts.retention_days[k].min && n <= opts.retention_days[k].max
    })
    const canSave = !busy && form.reason.trim().length > 0 && form.confirmed && retentionValid
    const unmasked = MASK_KEYS.filter((k) => !form.masking[k])

    const save = async () => {
        if (!canSave) return
        setBusy(true)
        setError(null)
        try {
            const res = await aiApi.savePolicy(
                {
                    expected_revision: view.revision,
                    state: form.state,
                    provider: form.provider,
                    allowed_categories: opts.categories.filter((c) => form.categories.includes(c)),
                    masking: form.masking,
                    retention_days: {
                        transcripts: Number(form.retention.transcripts),
                        audio: Number(form.retention.audio),
                        drafts: Number(form.retention.drafts),
                        provider_logs: Number(form.retention.provider_logs),
                    },
                    reason: form.reason.trim(),
                    confirmed: true,
                },
                key
            )
            ToastNotification(String(t('p6.policy.saved', { revision: res.revision })), String(t('p6.policy.title')), 'success', 3000)
            renew()
            q.reload()
            onSaved()
        } catch (e) {
            setError(e)
        } finally {
            setBusy(false)
        }
    }

    return (
        <div className="flex flex-col gap-4" data-testid="p6-policy">
            <CurrentPolicy view={view} />
            <Card title={view.policy ? t('p6.policy.editTitle') : t('p6.policy.createTitle')} testId="p6-policy-form">
                <p className="mb-4 text-[13px] text-gray-700 dark:text-night-body">{t('p6.policy.formHint')}</p>
                <div className="grid grid-cols-1 gap-x-6 md:grid-cols-2">
                    <DialogField label={t('p6.policy.state')} required hint={t('p6.policy.stateHint')}>
                        <Segmented
                            value={form.state}
                            options={opts.states.map((s) => ({ value: s as Form['state'], label: t(`p6.policy.stateValue.${s}`) }))}
                            onChange={(v) => setForm({ ...form, state: v })}
                            testId="p6-policy-state"
                        />
                    </DialogField>
                    <DialogField label={t('p6.policy.provider')} required>
                        <Segmented value={form.provider} options={opts.providers.map((p) => ({ value: p, label: p }))} onChange={(v) => setForm({ ...form, provider: v })} testId="p6-policy-provider" />
                    </DialogField>
                </div>

                <div className="text-eyebrow mb-2 mt-2">{t('p6.policy.categories')}</div>
                <div className="mb-4 grid grid-cols-1 gap-2 md:grid-cols-2" data-testid="p6-policy-categories">
                    {opts.categories.map((c) => (
                        <div key={c} className="rounded-md border border-gray-300 p-3 dark:border-night-border" data-category={c}>
                            <CheckField
                                checked={form.categories.includes(c)}
                                onChange={(checked) =>
                                    setForm({ ...form, categories: checked ? [...form.categories, c] : form.categories.filter((x) => x !== c) })
                                }
                                testId={`p6-policy-category-${c}`}
                            >
                                <span className="block text-[13.5px] font-extrabold text-navy-600 dark:text-gray-100">{categoryLabel(t, c)}</span>
                                <span className="block text-[12px] text-gray-600">{t(`p6.categoryHint.${c}`)}</span>
                            </CheckField>
                        </div>
                    ))}
                </div>

                <div className="text-eyebrow mb-2">{t('p6.policy.masking')}</div>
                <div className="mb-2 grid grid-cols-1 gap-2 md:grid-cols-4" data-testid="p6-policy-masking">
                    {MASK_KEYS.map((k) => (
                        <CheckField
                            key={k}
                            className="text-[13.5px]"
                            checked={form.masking[k]}
                            onChange={(checked) => setForm({ ...form, masking: { ...form.masking, [k]: checked } })}
                            testId={`p6-policy-mask-${k}`}
                        >
                            {t(`p6.policy.mask.${k}`)}
                        </CheckField>
                    ))}
                </div>
                <div className="mb-4 text-[12px] text-gray-600">{t('p6.policy.maskHint')}</div>
                {unmasked.length > 0 && (
                    <Notice tone="warning" className="mb-4" testId="p6-policy-unmasked">
                        {t('p6.policy.unmaskedWarning', { which: unmasked.map((k) => t(`p6.policy.mask.${k}`)).join(String(t('p6.listSep'))) })}
                    </Notice>
                )}

                <div className="text-eyebrow mb-2">{t('p6.policy.retention')}</div>
                <div className="grid grid-cols-2 gap-x-3 md:grid-cols-4" data-testid="p6-policy-retention">
                    {RETENTION_KEYS.map((k) => (
                        <DialogField key={k} label={t(`p6.policy.retentionKey.${k}`)} required hint={t('p6.policy.daysRange', opts.retention_days[k])}>
                            <Input
                                size="sm"
                                dir="ltr"
                                inputMode="numeric"
                                value={form.retention[k]}
                                onChange={(e) => setForm({ ...form, retention: { ...form.retention, [k]: e.target.value.trim() } })}
                                data-testid={`p6-policy-days-${k}`}
                            />
                        </DialogField>
                    ))}
                </div>
                <div className="mb-4 text-[12px] text-gray-600">{t('p6.policy.retentionHint')}</div>

                <DialogField label={t('p6.policy.reason')} required hint={t('p6.policy.reasonHint')}>
                    <Input textArea maxLength={2000} value={form.reason} onChange={(e) => setForm({ ...form, reason: e.target.value })} data-testid="p6-policy-reason" />
                </DialogField>
                <div className="mb-2 rounded-md border border-gray-300 p-3 dark:border-night-border">
                    <CheckField checked={form.confirmed} onChange={(c) => setForm({ ...form, confirmed: c })} testId="p6-policy-confirm">
                        <span className="text-[13.5px] text-gray-800 dark:text-night-body">{t('p6.policy.confirm')}</span>
                    </CheckField>
                </div>
                <InlineError
                    error={error}
                    describe={describePolicyError}
                    onReload={() => {
                        setError(null)
                        q.reload()
                    }}
                />
                <div className="mt-4 flex items-center justify-between gap-2">
                    <span className="text-[12px] text-gray-600" data-testid="p6-policy-expected" data-expected={view.revision}>
                        {t('p6.policy.expected', { revision: view.revision })}
                    </span>
                    <div className="flex gap-2">
                        <Button size="sm" disabled={busy} aria-disabled={busy} onClick={() => setForm(fromPolicy(view.policy, opts.providers))}>
                            {t('p6.policy.reset')}
                        </Button>
                        <Button size="sm" variant="solid" loading={busy} disabled={!canSave} aria-disabled={!canSave} onClick={save} data-testid="p6-policy-save">
                            {t('p6.policy.save')}
                        </Button>
                    </div>
                </div>
            </Card>
        </div>
    )
}

export default PolicyEditor

const PAGE_SIZE = 20

export const PolicyHistory = ({ tick }: { tick: number }) => {
    const { t } = useTranslation()
    const [page, setPage] = useState(1)
    const q = useV2Query((signal) => aiApi.policyHistory({ page, page_size: PAGE_SIZE }, signal), [page, tick])
    const data = q.data
    const cols = 7
    return (
        <TableFrame
            testId="p6-policy-history"
            footer={data && data.total > 0 ? <PagerFooter page={page} pageSize={PAGE_SIZE} total={data.total} onPage={setPage} /> : undefined}
        >
            {q.error && !data ? (
                <ErrorState error={q.error} onRetry={q.reload} describe={(e, tt) => describeP6Error(e, tt)} />
            ) : (
                <Table>
                    <THead>
                        <Tr>
                            <Th>{t('p6.policy.revision')}</Th>
                            <Th>{t('p6.policy.state')}</Th>
                            <Th>{t('p6.policy.categories')}</Th>
                            <Th>{t('p6.policy.masking')}</Th>
                            <Th>{t('p6.policy.retention')}</Th>
                            <Th>{t('p6.policy.approvedBy')}</Th>
                            <Th>{t('p6.policy.reason')}</Th>
                        </Tr>
                    </THead>
                    <TBody>
                        {q.loading && !data ? (
                            <SkeletonRows cols={cols} rows={3} />
                        ) : data && data.items.length > 0 ? (
                            data.items.map((p) => (
                                <Tr key={p.revision} data-testid="p6-policy-history-row" data-revision={p.revision} data-state={p.state}>
                                    <Td>
                                        <Ltr className="font-extrabold">{p.revision}</Ltr>
                                    </Td>
                                    <Td>{t(`p6.policy.stateValue.${p.state}`)}</Td>
                                    <Td className="min-w-[180px] text-[12.5px]">
                                        {p.allowed_categories.length ? p.allowed_categories.map((c) => categoryLabel(t, c)).join(String(t('p6.listSep'))) : t('p6.policy.noCategories')}
                                    </Td>
                                    <Td className="text-[12.5px]">
                                        {MASK_KEYS.filter((k) => p.masking[k])
                                            .map((k) => t(`p6.policy.maskShort.${k}`))
                                            .join(String(t('p6.listSep'))) || t('p6.policy.maskNone')}
                                    </Td>
                                    <Td className="text-[12.5px]">
                                        <Ltr>{RETENTION_KEYS.map((k) => p.retention_days[k]).join(' / ')}</Ltr>
                                    </Td>
                                    <Td className="whitespace-nowrap text-[12.5px]">
                                        {p.approved_by?.name || '—'}
                                        <div>
                                            <RiyadhTime iso={p.approved_at} className="text-gray-600" />
                                        </div>
                                    </Td>
                                    <Td className="min-w-[200px] text-[12.5px]">
                                        <span dir="auto">{p.reason || '—'}</span>
                                    </Td>
                                </Tr>
                            ))
                        ) : (
                            <tr>
                                <td colSpan={cols}>
                                    <EmptyState title={t('p6.policy.historyEmpty')} hint={t('p6.policy.missing')} />
                                </td>
                            </tr>
                        )}
                    </TBody>
                </Table>
            )}
            <div className="border-t border-gray-300 px-5 py-2 text-[11.5px] text-gray-500 dark:border-night-border">{t('p6.policy.retentionOrder')}</div>
        </TableFrame>
    )
}
