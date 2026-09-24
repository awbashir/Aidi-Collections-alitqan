/**
 * Create / edit a campaign (`POST /v2/campaigns`, `PATCH /v2/campaigns/<id>`, key
 * `campaigns_manage`).
 *
 * The pool is a DEFINITION the calling service evaluates at every dispatch, never a frozen
 * list: contracts and/or portfolios and/or a clients-list filter (the list's own query
 * parameters, so "the filter I am looking at" can be saved as it is, plus the list's column
 * filter). It is bounded by my current scope, and a case only ever goes to a member whose
 * own scope contains it. Settings: auto/manual next call, wrap-up delay, max attempts per
 * day, cooldown, readiness lease. Progressive only: there is no dial ratio.
 */
import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { HiOutlineX } from 'react-icons/hi'
import Input from '@/components/ui/Input'
import Select from '@/components/ui/Select'
import ActionDialog, { DialogField } from '@/components/v2/ActionDialog'
import { Segmented } from '@/views/phase1/followUps/parts'
import { campaignsApi, describeP5Error, lookups } from '@/services/v2'
import type { Campaign, CampaignPool, CampaignSettings, Option, Translate } from '@/services/v2'
import { Notice } from '../parts'
import { DEFAULT_SETTINGS } from './poolFilter'

const LIMITS: Record<Exclude<keyof CampaignSettings, 'next_mode'>, [number, number]> = {
    wrapup_delay_seconds: [0, 600],
    max_attempts_per_day: [1, 50],
    cooldown_minutes: [0, 10080],
    lease_seconds: [10, 300],
}

/** The grid (column) filter of the clients list, as the pool accepts it. */
const GRID_FIELDS: Array<{ value: string; type: 'number' | 'string' }> = [
    { value: 'accountNumber', type: 'string' },
    { value: 'identityNumber', type: 'string' },
    { value: 'name', type: 'string' },
    { value: 'portfolios.name', type: 'string' },
    { value: 'contract.name', type: 'string' },
    { value: 'amount', type: 'number' },
]
const STRING_OPS = ['equals', 'contains', 'startsWith', 'endsWith']
const NUMBER_OPS = ['>', '>=', '<', '<=', '=', '!=']
const GRID_KEYS = ['filterField', 'filterOperator', 'filterFieldType', 'filterValue', 'filterValue[]']

type Draft = {
    name: string
    contracts: string[]
    portfolios: string[]
    filter: Record<string, unknown>
    grid: { field: string; op: string; value: string }
    settings: CampaignSettings
    members: string[]
}

const draftFrom = (c: Campaign | null, filter?: Record<string, unknown>): Draft => {
    const f = { ...(c?.pool.filter || filter || {}) }
    const grid = {
        field: String(f.filterField || ''),
        op: String(f.filterOperator || ''),
        value: f.filterValue === undefined || f.filterValue === null ? '' : String(f.filterValue),
    }
    GRID_KEYS.forEach((k) => delete f[k])
    return {
        name: c?.name || '',
        contracts: c?.pool.contract_ids || [],
        portfolios: c?.pool.portfolio_ids || [],
        filter: f,
        grid,
        settings: { ...DEFAULT_SETTINGS, ...(c?.settings || {}) },
        members: [],
    }
}

const poolOf = (d: Draft): CampaignPool => {
    const filter: Record<string, unknown> = { ...d.filter }
    if (d.grid.field && d.grid.op && d.grid.value.trim() !== '') {
        const type = GRID_FIELDS.find((g) => g.value === d.grid.field)?.type || 'string'
        filter.filterField = d.grid.field
        filter.filterOperator = d.grid.op
        filter.filterFieldType = type
        filter.filterValue = d.grid.value.trim()
    }
    return { contract_ids: d.contracts, portfolio_ids: d.portfolios, filter }
}

const describe = (e: unknown, t: Translate) => describeP5Error(e, t)

const useOptions = (load: () => Promise<Option[]>, active: boolean) => {
    const [opts, setOpts] = useState<{ options: Option[]; loading: boolean }>({ options: [], loading: true })
    useEffect(() => {
        if (!active) return undefined
        let alive = true
        load()
            .then((o) => alive && setOpts({ options: o, loading: false }))
            .catch(() => alive && setOpts({ options: [], loading: false }))
        return () => {
            alive = false
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [active])
    return opts
}

const multi = { size: 'sm' as const, menuPosition: 'fixed' as const, isMulti: true as const }

const CampaignForm = ({
    isOpen,
    campaign,
    initialFilter,
    onClose,
    onSaved,
}: {
    isOpen: boolean
    /** null = create */
    campaign: Campaign | null
    /** A clients-list filter handed over by the clients list ("New campaign from this filter"). */
    initialFilter?: Record<string, unknown>
    onClose: () => void
    onSaved: (c: Campaign) => void
}) => {
    const { t } = useTranslation()
    const [d, setD] = useState<Draft>(() => draftFrom(campaign, initialFilter))
    useEffect(() => {
        if (isOpen) setD(draftFrom(campaign, initialFilter))
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [isOpen, campaign?.id, campaign?.revision])
    const contracts = useOptions(lookups.contracts, isOpen)
    const portfolios = useOptions(lookups.portfolios as () => Promise<Option[]>, isOpen)
    const users = useOptions(lookups.users, isOpen && !campaign)
    const portfolioOptions = useMemo(
        () =>
            (portfolios.options as Array<Option & { contract_id?: string | null }>).filter(
                (p) => d.contracts.length === 0 || (p.contract_id && d.contracts.includes(p.contract_id)) || d.portfolios.includes(p.value)
            ),
        [portfolios.options, d.contracts, d.portfolios]
    )
    const gridType = GRID_FIELDS.find((g) => g.value === d.grid.field)?.type
    const settingsOk = (Object.keys(LIMITS) as Array<keyof typeof LIMITS>).every((k) => {
        const v = d.settings[k]
        return Number.isInteger(v) && v >= LIMITS[k][0] && v <= LIMITS[k][1]
    })
    const pool = poolOf(d)
    const hasCriterion = pool.contract_ids.length > 0 || pool.portfolio_ids.length > 0 || Object.keys(pool.filter).length > 0
    const gridIncomplete = !!(d.grid.field || d.grid.op || d.grid.value) && !(d.grid.field && d.grid.op && d.grid.value.trim())
    const canSubmit = d.name.trim().length > 0 && d.name.trim().length <= 200 && hasCriterion && settingsOk && !gridIncomplete

    const setSetting = <K extends keyof CampaignSettings>(k: K, v: CampaignSettings[K]) => setD((x) => ({ ...x, settings: { ...x.settings, [k]: v } }))
    const numberField = (k: keyof typeof LIMITS, label: string, hint: string) => (
        <DialogField label={label} required hint={hint}>
            <Input
                size="sm"
                dir="ltr"
                inputMode="numeric"
                value={String(d.settings[k])}
                onChange={(e) => {
                    const raw = e.target.value.replace(/[^0-9]/g, '').slice(0, 5)
                    setSetting(k, raw === '' ? (NaN as number) : Number(raw))
                }}
                data-testid={`p5-cf-${k}`}
            />
        </DialogField>
    )

    return (
        <ActionDialog
            isOpen={isOpen}
            title={campaign ? t('p5.cf.editTitle') : t('p5.cf.newTitle')}
            confirmLabel={campaign ? t('p5.cf.save') : t('p5.cf.create')}
            canSubmit={canSubmit}
            width={760}
            testId="p5-cf"
            describeError={describe}
            onClose={onClose}
            onSubmit={async (key) => {
                const settings = { ...d.settings }
                const res = campaign
                    ? await campaignsApi.update(campaign.id, { expected_revision: campaign.revision, name: d.name.trim(), pool, settings }, key)
                    : await campaignsApi.create({ name: d.name.trim(), pool, settings, member_ids: d.members }, key)
                onSaved(res)
            }}
        >
            <DialogField label={t('p5.cf.name')} required>
                <Input size="sm" value={d.name} maxLength={200} onChange={(e) => setD({ ...d, name: e.target.value })} data-testid="p5-cf-name" />
            </DialogField>

            <div className="text-eyebrow mb-2">{t('p5.cf.pool')}</div>
            <div className="mb-3 text-[12.5px] text-gray-600">{t('p5.cf.poolHint')}</div>
            <div className="grid grid-cols-1 gap-x-3 sm:grid-cols-2">
                <DialogField label={t('p5.cf.contracts')}>
                    <div data-testid="p5-cf-contracts">
                        <Select
                            {...multi}
                            isLoading={contracts.loading}
                            options={contracts.options}
                            value={contracts.options.filter((o) => d.contracts.includes(o.value))}
                            onChange={(v) => setD({ ...d, contracts: ((v as Option[]) || []).map((o) => o.value) })}
                        />
                    </div>
                </DialogField>
                <DialogField label={t('p5.cf.portfolios')}>
                    <div data-testid="p5-cf-portfolios">
                        <Select
                            {...multi}
                            isLoading={portfolios.loading}
                            options={portfolioOptions}
                            value={portfolios.options.filter((o) => d.portfolios.includes(o.value))}
                            onChange={(v) => setD({ ...d, portfolios: ((v as Option[]) || []).map((o) => o.value) })}
                        />
                    </div>
                </DialogField>
            </div>
            {Object.keys(d.filter).length > 0 && (
                <div className="mb-3" data-testid="p5-cf-filter">
                    <div className="mb-1 text-[12.5px] font-extrabold text-navy-600 dark:text-gray-100">{t('p5.cf.listFilter')}</div>
                    <div className="flex flex-wrap gap-2">
                        {Object.entries(d.filter).map(([k, v]) => (
                            <span key={k} className="inline-flex h-7 items-center gap-1 rounded-pill border border-azure-500 bg-azure-50 px-3 text-[12px] text-azure-600" data-key={k}>
                                <span dir="ltr">
                                    {t(`p5.cf.filterKey.${k.replace(/\[\]$/, '')}`, { defaultValue: k })}: {Array.isArray(v) ? t('p5.cf.nValues', { count: v.length }) : String(v)}
                                </span>
                                <button
                                    type="button"
                                    aria-label={String(t('p1.filter.clear'))}
                                    onClick={() => {
                                        const f = { ...d.filter }
                                        delete f[k]
                                        setD({ ...d, filter: f })
                                    }}
                                >
                                    <HiOutlineX />
                                </button>
                            </span>
                        ))}
                    </div>
                </div>
            )}
            <div className="mb-1 text-[12.5px] font-extrabold text-navy-600 dark:text-gray-100">{t('p5.cf.columnFilter')}</div>
            <div className="grid grid-cols-1 gap-x-3 sm:grid-cols-3" data-testid="p5-cf-grid">
                <DialogField label={t('p5.cf.gridField')}>
                    <div data-testid="p5-cf-grid-field">
                        <Select
                            size="sm"
                            menuPosition="fixed"
                            isClearable
                            options={GRID_FIELDS.map((g) => ({ value: g.value, label: String(t(`p5.cf.gridFieldName.${g.value.replace('.', '_')}`)) }))}
                            value={
                                d.grid.field
                                    ? { value: d.grid.field, label: String(t(`p5.cf.gridFieldName.${d.grid.field.replace('.', '_')}`)) }
                                    : null
                            }
                            onChange={(o) => setD({ ...d, grid: { field: (o as Option | null)?.value || '', op: '', value: d.grid.value } })}
                        />
                    </div>
                </DialogField>
                <DialogField label={t('p5.cf.gridOp')}>
                    <div data-testid="p5-cf-grid-op">
                        <Select
                            size="sm"
                            menuPosition="fixed"
                            isClearable
                            isDisabled={!gridType}
                            options={(gridType === 'number' ? NUMBER_OPS : STRING_OPS).map((o) => ({ value: o, label: String(t(`p5.cf.op.${o}`, { defaultValue: o })) }))}
                            value={d.grid.op ? { value: d.grid.op, label: String(t(`p5.cf.op.${d.grid.op}`, { defaultValue: d.grid.op })) } : null}
                            onChange={(o) => setD({ ...d, grid: { ...d.grid, op: (o as Option | null)?.value || '' } })}
                        />
                    </div>
                </DialogField>
                <DialogField label={t('p5.cf.gridValue')}>
                    <Input size="sm" dir="auto" value={d.grid.value} onChange={(e) => setD({ ...d, grid: { ...d.grid, value: e.target.value } })} data-testid="p5-cf-grid-value" />
                </DialogField>
            </div>
            {!hasCriterion && <Notice tone="warning" className="mb-3">{t('p5.cf.needCriterion')}</Notice>}
            <Notice tone="info" className="mb-4">
                {t('p5.cf.scopeRule')}
            </Notice>

            <div className="text-eyebrow mb-2">{t('p5.cf.settings')}</div>
            <DialogField label={t('p5.cf.nextMode')} hint={t(`p5.cf.nextModeHint.${d.settings.next_mode}`)}>
                <Segmented<'auto' | 'manual'>
                    value={d.settings.next_mode}
                    onChange={(v) => setSetting('next_mode', v)}
                    testId="p5-cf-mode"
                    options={[
                        { value: 'auto', label: t('p5.campaign.mode.auto') },
                        { value: 'manual', label: t('p5.campaign.mode.manual') },
                    ]}
                />
            </DialogField>
            <div className="grid grid-cols-1 gap-x-3 sm:grid-cols-2">
                {numberField('wrapup_delay_seconds', String(t('p5.cf.delay')), String(t('p5.cf.delayHint')))}
                {numberField('max_attempts_per_day', String(t('p5.cf.maxAttempts')), String(t('p5.cf.maxAttemptsHint')))}
                {numberField('cooldown_minutes', String(t('p5.cf.cooldown')), String(t('p5.cf.cooldownHint')))}
                {numberField('lease_seconds', String(t('p5.cf.lease')), String(t('p5.cf.leaseHint')))}
            </div>
            <div className="mb-3 text-[12px] text-gray-600">{t('p5.cf.progressive')}</div>

            {!campaign && (
                <>
                    <div className="text-eyebrow mb-2">{t('p5.cf.members')}</div>
                    <DialogField label={t('p5.cf.membersLabel')} hint={t('p5.cf.membersHint')}>
                        <div data-testid="p5-cf-members">
                            <Select
                                {...multi}
                                isLoading={users.loading}
                                options={users.options}
                                value={users.options.filter((o) => d.members.includes(o.value))}
                                onChange={(v) => setD({ ...d, members: ((v as Option[]) || []).map((o) => o.value) })}
                            />
                        </div>
                    </DialogField>
                </>
            )}
        </ActionDialog>
    )
}

export default CampaignForm
