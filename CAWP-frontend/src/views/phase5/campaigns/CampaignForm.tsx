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
 *
 * Client status (customer 2026-10-19): the case's latest note status and its sub-status as two
 * dropdowns (`customeFilterCommentStatusValue[]` / `customeFilterCommentSubStatusValue[]`,
 * ANDed like the clients list). Offered: the live general statuses plus those of the contracts
 * the pool reaches within my scope (`GET /v2/campaigns/pool-scope`); the API checks the same.
 *
 * Unassigned clients only (customer 2026-10-21): `pool.unassigned_only` = cases with no collector,
 * combinable with the status filters (e.g. last status «لم يتم الرد»), not with a collector
 * filter. It grants nothing: such a case only goes to a member whose own scope contains it. A
 * promise recorded on such a call assigns the case to the agent who made it.
 *
 * Customer feedback 2026-10-24: "Choose customers" = by filter (the above) OR a customer list
 * (`pool.case_list`: filled after creation from Excel or pasted national ids / account numbers on
 * the detail's Pool tab, where the list is reviewed and customers removed before the start), and
 * `pool.skip_any_payment` (on by default for a new campaign): no case with any approved payment.
 * 2026-10-25 (0087): an end date and time (Asia/Riyadh; the campaign drains and stops by itself
 * then) and random fill (an agent with no callable client of their own gets random clients of the
 * campaign, any owner; a promise moves such a client to them).
 */
import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import Checkbox from '@/components/ui/Checkbox'
import Input from '@/components/ui/Input'
import Select from '@/components/ui/Select'
import ActionDialog, { DialogField } from '@/components/v2/ActionDialog'
import { Segmented } from '@/views/phase1/followUps/parts'
import { campaignsApi, describeP5Error, isV2Error, lookups } from '@/services/v2'
import type { Campaign, CampaignPool, CampaignSettings, CommentStatusOption, Option, Translate } from '@/services/v2'
import { Notice } from '../parts'
import PoolFilterChips, { usePoolNames } from './PoolFilterChips'
import {
    DEFAULT_SETTINGS,
    STATUS_KEY,
    SUB_STATUS_KEY,
    poolStatusOptions,
    poolSubStatusOptions,
    pruneStatuses,
    statusContracts,
    takeStatuses,
} from './poolFilter'

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
    /** the latest note's main statuses / sub-statuses (kept out of `filter`, merged by poolOf) */
    statuses: string[]
    subStatuses: string[]
    grid: { field: string; op: string; value: string }
    /** only cases with no collector (2026-10-21) */
    unassigned: boolean
    /** 2026-10-24: by filter, or the campaign's customer list */
    source: 'filter' | 'list'
    /** 2026-10-24: no case with any approved payment */
    skipPaid: boolean
    /** 2026-10-25: end date-time in Riyadh wall clock ("YYYY-MM-DDTHH:mm"), '' = no limit */
    endsAt: string
    fillRandom: boolean
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
    const { statuses, subStatuses, rest } = takeStatuses(f)
    return {
        name: c?.name || '',
        contracts: c?.pool.contract_ids || [],
        portfolios: c?.pool.portfolio_ids || [],
        filter: rest,
        statuses,
        subStatuses,
        grid,
        unassigned: c?.pool.unassigned_only === true,
        source: c?.pool.case_list ? 'list' : 'filter',
        skipPaid: c ? c.pool.skip_any_payment === true : true,
        endsAt: c?.ends_at ? toRiyadhLocal(c.ends_at) : '',
        fillRandom: c?.fill_random === true,
        settings: { ...DEFAULT_SETTINGS, ...(c?.settings || {}) },
        members: [],
    }
}

/** ISO instant -> "YYYY-MM-DDTHH:mm" on the Riyadh wall clock (UTC+3, no DST). */
const toRiyadhLocal = (iso: string) => new Date(new Date(iso).getTime() + 3 * 3600 * 1000).toISOString().slice(0, 16)
/** "YYYY-MM-DDTHH:mm" Riyadh -> ISO with the +03:00 offset the API requires. */
const fromRiyadhLocal = (local: string) => `${local}:00+03:00`

const poolOf = (d: Draft): CampaignPool => {
    const skip = d.skipPaid ? { skip_any_payment: true } : {}
    if (d.source === 'list') return { contract_ids: [], portfolio_ids: [], filter: {}, case_list: true, ...skip }
    const filter: Record<string, unknown> = { ...d.filter }
    if (d.statuses.length) filter[STATUS_KEY] = d.statuses
    if (d.subStatuses.length) filter[SUB_STATUS_KEY] = d.subStatuses
    if (d.grid.field && d.grid.op && d.grid.value.trim() !== '') {
        const type = GRID_FIELDS.find((g) => g.value === d.grid.field)?.type || 'string'
        filter.filterField = d.grid.field
        filter.filterOperator = d.grid.op
        filter.filterFieldType = type
        filter.filterValue = d.grid.value.trim()
    }
    return { contract_ids: d.contracts, portfolio_ids: d.portfolios, filter, ...(d.unassigned ? { unassigned_only: true } : {}), ...skip }
}

const USER_KEY = 'customeFilterUserValue[]'

/** A refused note status reads as a sentence of its own; everything else as usual. */
const describe = (e: unknown, t: Translate) => {
    if (isV2Error(e) && e.detail && typeof e.detail === 'object') {
        const hit = Object.entries(e.detail as Record<string, unknown>).find(([k]) =>
            /^pool\.filter\.(customeFilterCommentStatusValue|customeFilterStatusesValue|customeFilterCommentSubStatusValue)/.test(k)
        )
        if (hit) {
            const sub = hit[0].includes('SubStatus')
            return String(
                t(`p9.campaign.err.${String(hit[1])}`, {
                    defaultValue: t('p9.campaign.err.generic'),
                    field: t(sub ? 'p9.campaign.subStatus' : 'p9.campaign.status'),
                })
            )
        }
    }
    return describeP5Error(e, t)
}

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
    const noteStatuses = useOptions(lookups.commentStatuses as () => Promise<Option[]>, isOpen)
    const statusList = noteStatuses.options as CommentStatusOption[]
    const names = usePoolNames(isOpen)
    /* my scope's contracts: null = every contract (also while unknown: the API is the authority) */
    const [scope, setScope] = useState<string[] | null>(null)
    useEffect(() => {
        if (!isOpen) return undefined
        const ac = new AbortController()
        campaignsApi
            .poolScope(ac.signal)
            .then((r) => setScope(r.contract_ids))
            .catch(() => setScope(null))
        return () => ac.abort()
    }, [isOpen])
    const portfolioOptions = useMemo(
        () =>
            (portfolios.options as Array<Option & { contract_id?: string | null }>).filter(
                (p) => d.contracts.length === 0 || (p.contract_id && d.contracts.includes(p.contract_id)) || d.portfolios.includes(p.value)
            ),
        [portfolios.options, d.contracts, d.portfolios]
    )
    const portfolioContracts = portfolios.options as Array<Option & { contract_id?: string | null }>
    const contractsFor = (x: Draft) => statusContracts(poolOf(x), portfolioContracts, scope)
    /** a pool criterion changed: drop the statuses that no longer fit its contracts */
    const setPool = (patch: Partial<Draft>) =>
        setD((x) => {
            const next = { ...x, ...patch }
            return { ...next, ...pruneStatuses(statusList, contractsFor(next), next.statuses, next.subStatuses) }
        })
    const statusOptions = poolStatusOptions(statusList, contractsFor(d))
    const subOptions = poolSubStatusOptions(statusList, d.statuses)
    const statusLabel = (id: string) => {
        const s = statusList.find((o) => o.value === id)
        return s ? s.label : names[id] || String(t('p9.campaign.unknownStatus', { id: id.slice(0, 8) }))
    }
    const subLabel = (o: CommentStatusOption) =>
        d.statuses.length > 1 && o.parent_id ? `${statusLabel(o.parent_id)} › ${o.label}` : o.label
    const picked = (list: string[], label: (id: string) => string) => list.map((id) => ({ value: id, label: label(id) }))
    const gridType = GRID_FIELDS.find((g) => g.value === d.grid.field)?.type
    const settingsOk = (Object.keys(LIMITS) as Array<keyof typeof LIMITS>).every((k) => {
        const v = d.settings[k]
        return Number.isInteger(v) && v >= LIMITS[k][0] && v <= LIMITS[k][1]
    })
    const pool = poolOf(d)
    const hasCriterion =
        pool.case_list === true ||
        pool.contract_ids.length > 0 ||
        pool.portfolio_ids.length > 0 ||
        Object.keys(pool.filter).length > 0 ||
        pool.unassigned_only === true
    /* unassigned cases have no collector: the API refuses the two together */
    const unassignedConflict = d.source === 'filter' && d.unassigned && Array.isArray(d.filter[USER_KEY]) && (d.filter[USER_KEY] as unknown[]).length > 0
    const gridIncomplete =
        d.source === 'filter' && !!(d.grid.field || d.grid.op || d.grid.value) && !(d.grid.field && d.grid.op && d.grid.value.trim())
    const endsAtPast = !!d.endsAt && new Date(fromRiyadhLocal(d.endsAt)).getTime() <= Date.now()
    const canSubmit =
        d.name.trim().length > 0 &&
        d.name.trim().length <= 200 &&
        hasCriterion &&
        settingsOk &&
        !gridIncomplete &&
        !unassignedConflict &&
        !endsAtPast

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
                const extra = { ends_at: d.endsAt ? fromRiyadhLocal(d.endsAt) : null, fill_random: d.fillRandom }
                const res = campaign
                    ? await campaignsApi.update(campaign.id, { expected_revision: campaign.revision, name: d.name.trim(), pool, settings, ...extra }, key)
                    : await campaignsApi.create({ name: d.name.trim(), pool, settings, member_ids: d.members, ...extra }, key)
                onSaved(res)
            }}
        >
            <DialogField label={t('p5.cf.name')} required>
                <Input size="sm" value={d.name} maxLength={200} onChange={(e) => setD({ ...d, name: e.target.value })} data-testid="p5-cf-name" />
            </DialogField>

            <div className="text-eyebrow mb-2">{t('p5.cf.pool')}</div>
            <DialogField label={t('p12.campcases.source.label')}>
                <Segmented<'filter' | 'list'>
                    value={d.source}
                    onChange={(v) => setD({ ...d, source: v })}
                    testId="p12-cf-source"
                    label={String(t('p12.campcases.source.label'))}
                    options={[
                        { value: 'filter', label: t('p12.campcases.source.filter') },
                        { value: 'list', label: t('p12.campcases.source.list') },
                    ]}
                />
            </DialogField>
            <div className="grid grid-cols-1 gap-x-3 sm:grid-cols-2">
                <DialogField label={t('p12.campcases.endsAt.label')} hint={t('p12.campcases.endsAt.hint')}>
                    <Input
                        size="sm"
                        type="datetime-local"
                        dir="ltr"
                        value={d.endsAt}
                        onChange={(e) => setD({ ...d, endsAt: e.target.value })}
                        data-testid="p12-cf-ends-at"
                    />
                    {endsAtPast && <div className="mt-1 text-[12px] text-danger">{t('p12.campcases.endsAt.past')}</div>}
                </DialogField>
                <div className="mb-3 pt-6" data-testid="p12-cf-fill">
                    <Checkbox checked={d.fillRandom} onChange={(checked: boolean) => setD({ ...d, fillRandom: checked })}>
                        <span className="text-[13.5px] font-bold text-navy-600 dark:text-gray-100">{t('p12.campcases.fill.label')}</span>
                    </Checkbox>
                    <div className="mt-1 text-[12px] text-gray-600">{t('p12.campcases.fill.hint')}</div>
                </div>
            </div>
            <div className="mb-3" data-testid="p12-cf-skip-paid">
                <Checkbox checked={d.skipPaid} onChange={(checked: boolean) => setD({ ...d, skipPaid: checked })}>
                    <span className="text-[13.5px] font-bold text-navy-600 dark:text-gray-100">{t('p12.campcases.skipPaid.label')}</span>
                </Checkbox>
                <div className="mt-1 text-[12px] text-gray-600">{t('p12.campcases.skipPaid.hint')}</div>
            </div>
            {d.source === 'list' ? (
                <Notice tone="info" className="mb-3" testId="p12-cf-list-hint">
                    {t('p12.campcases.source.listHint')}
                </Notice>
            ) : (
            <>
            <div className="mb-3 text-[12.5px] text-gray-600">{t('p5.cf.poolHint')}</div>
            <div className="grid grid-cols-1 gap-x-3 sm:grid-cols-2">
                <DialogField label={t('p5.cf.contracts')}>
                    <div data-testid="p5-cf-contracts">
                        <Select
                            {...multi}
                            isLoading={contracts.loading}
                            options={contracts.options}
                            value={contracts.options.filter((o) => d.contracts.includes(o.value))}
                            onChange={(v) => setPool({ contracts: ((v as Option[]) || []).map((o) => o.value) })}
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
                            onChange={(v) => setPool({ portfolios: ((v as Option[]) || []).map((o) => o.value) })}
                        />
                    </div>
                </DialogField>
            </div>
            <div className="grid grid-cols-1 gap-x-3 sm:grid-cols-2" data-testid="p9-campaign-statuses">
                <DialogField label={t('p9.campaign.status')} hint={t('p9.campaign.statusHint')}>
                    <div data-testid="p9-campaign-status">
                        <Select
                            {...multi}
                            isLoading={noteStatuses.loading}
                            placeholder={t('p9.campaign.statusPlaceholder')}
                            noOptionsMessage={() => t('p9.campaign.noStatuses')}
                            options={statusOptions}
                            value={picked(d.statuses, statusLabel)}
                            onChange={(v) => {
                                const statuses = ((v as Option[]) || []).map((o) => o.value)
                                /* the sub-statuses depend on the main choice: a removed main status takes its own with it */
                                const subStatuses = statuses.length
                                    ? d.subStatuses.filter((id) => {
                                          const s = statusList.find((o) => o.value === id)
                                          return !!s && !!s.parent_id && statuses.includes(s.parent_id)
                                      })
                                    : []
                                setD({ ...d, statuses, subStatuses })
                            }}
                        />
                    </div>
                </DialogField>
                <DialogField
                    label={t('p9.campaign.subStatus')}
                    hint={d.statuses.length || d.subStatuses.length ? t('p9.campaign.subHint') : t('p9.campaign.subNeedsMain')}
                >
                    <div data-testid="p9-campaign-sub-status">
                        <Select
                            {...multi}
                            isLoading={noteStatuses.loading}
                            isDisabled={d.statuses.length === 0 && d.subStatuses.length === 0}
                            placeholder={t('p9.campaign.subPlaceholder')}
                            noOptionsMessage={() => t('p9.campaign.noSubStatuses')}
                            options={subOptions.map((o) => ({ value: o.value, label: subLabel(o) }))}
                            value={picked(d.subStatuses, (id) => {
                                const s = statusList.find((o) => o.value === id)
                                return s ? subLabel(s) : statusLabel(id)
                            })}
                            onChange={(v) => setD({ ...d, subStatuses: ((v as Option[]) || []).map((o) => o.value) })}
                        />
                    </div>
                </DialogField>
            </div>
            <div className="mb-3" data-testid="p11-campaign-unassigned">
                <Checkbox checked={d.unassigned} onChange={(checked: boolean) => setD({ ...d, unassigned: checked })}>
                    <span className="text-[13.5px] font-bold text-navy-600 dark:text-gray-100">{t('p11.camprules.unassigned.label')}</span>
                </Checkbox>
                <div className="mt-1 text-[12px] text-gray-600">{t('p11.camprules.unassigned.hint')}</div>
                {unassignedConflict && (
                    <Notice tone="warning" className="mt-2">
                        {t('p11.camprules.unassigned.conflict')}
                    </Notice>
                )}
            </div>
            {Object.keys(d.filter).length > 0 && (
                <div className="mb-3" data-testid="p5-cf-filter">
                    <div className="mb-1 text-[12.5px] font-extrabold text-navy-600 dark:text-gray-100">{t('p5.cf.listFilter')}</div>
                    <PoolFilterChips
                        filter={d.filter}
                        names={names}
                        onRemove={(k) => {
                            const f = { ...d.filter }
                            delete f[k]
                            setPool({ filter: f })
                        }}
                    />
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
            </>
            )}
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
