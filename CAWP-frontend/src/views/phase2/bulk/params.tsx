/**
 * The params form of each bulk kind (wizard step 4), its validity rule and the
 * body it sends. Names follow the API (`services/bulk_*.py validate_params`).
 * Fields that only drive the form (the notes contract filter, callback date and
 * time) are kept under `__ui` and never sent.
 */
import classNames from 'classnames'
import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { HiArrowDown, HiArrowUp, HiX } from 'react-icons/hi'
import Input from '@/components/ui/Input'
import Select from '@/components/ui/Select'
import Button from '@/components/ui/Button'
import { DialogField } from '@/components/v2/ActionDialog'
import { Note } from '@/components/v2/States'
import { RiyadhTime } from '@/components/v2/Display'
import { bulkApi, lookups } from '@/services/v2'
import type { BulkCollector, BulkSource, CommentStatusOption, Option } from '@/services/v2'
import { riyadhToday } from '@/utils/v2/format'
import { notesCallback, str } from './paramsLogic'
import type { Params } from './paramsLogic'
import type { ReactNode } from 'react'
import { Segmented } from '@/views/phase1/followUps/parts'
import { mainStatusesFor } from '@/utils/commentStatusTree'

const selectProps = { size: 'sm' as const, menuPosition: 'fixed' as const }

type FormProps = {
    kind: string
    source: BulkSource | undefined
    value: Params
    onChange: (next: Params) => void
}

const useOptions = (load: () => Promise<Option[]>) => {
    const [state, setState] = useState<{ options: Option[]; loading: boolean; failed: boolean }>({
        options: [],
        loading: true,
        failed: false,
    })
    useEffect(() => {
        let alive = true
        load()
            .then((options) => alive && setState({ options, loading: false, failed: false }))
            .catch(() => alive && setState({ options: [], loading: false, failed: true }))
        return () => {
            alive = false
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [])
    return state
}

const pick = (options: Option[], v: unknown) => options.find((o) => o.value === v) || null

/* ---------------------------------------------------------------- reason */

const ReasonField = ({
    value,
    onChange,
    required,
    label,
    hint,
    testId = 'p2-param-reason',
}: {
    value: unknown
    onChange: (v: string) => void
    required?: boolean
    label?: ReactNode
    hint?: ReactNode
    testId?: string
}) => {
    const { t } = useTranslation()
    return (
        <DialogField label={label || t('p2.params.reason')} required={required} hint={hint}>
            <Input
                textArea
                value={str(value)}
                maxLength={500}
                onChange={(e) => onChange(e.target.value)}
                data-testid={testId}
            />
        </DialogField>
    )
}

/* ------------------------------------------------------- collector picker */

function useCollectors() {
    const [state, setState] = useState<{ items: BulkCollector[]; loading: boolean; error: unknown }>({
        items: [],
        loading: true,
        error: null,
    })
    useEffect(() => {
        const ctrl = new AbortController()
        bulkApi
            .collectors(ctrl.signal)
            .then((r) => setState({ items: r.items, loading: false, error: null }))
            .catch((error) => {
                if ((error as { code?: string })?.code === 'canceled') return
                setState({ items: [], loading: false, error })
            })
        return () => ctrl.abort()
    }, [])
    return state
}

const collectorLabel = (c: BulkCollector) => `${(c.name || '').trim() || c.user_name} · ${c.user_name}`

/* ------------------------------------------------------------------ forms */

const AssignForm = ({ source, value, onChange }: FormProps) => {
    const { t } = useTranslation()
    const collectors = useCollectors()
    const options = collectors.items.map((c) => ({ value: c.id, label: collectorLabel(c) }))
    return (
        <>
            {source === 'spreadsheet' ? (
                <Note className="mb-4">{t('p2.params.assignFromFile')}</Note>
            ) : (
                <DialogField label={t('p2.params.collector')} required hint={t('p2.params.collectorHint')}>
                    <div data-testid="p2-param-collector">
                        <Select
                            {...selectProps}
                            isLoading={collectors.loading}
                            options={options}
                            value={pick(options, value.target_user_id)}
                            placeholder={String(t('p2.params.pickCollector'))}
                            onChange={(o) => onChange({ ...value, target_user_id: (o as Option | null)?.value || '' })}
                        />
                    </div>
                </DialogField>
            )}
            <ReasonField value={value.reason} onChange={(reason) => onChange({ ...value, reason })} label={t('p2.params.reasonOptional')} />
        </>
    )
}

const RoundRobinForm = ({ value, onChange }: FormProps) => {
    const { t } = useTranslation()
    const collectors = useCollectors()
    const portfolios = useOptions(lookups.portfolios)
    const contracts = useOptions(lookups.contracts)
    const scope = value.__ui?.scope === 'contract' ? 'contract' : 'portfolio'
    const ids = (value.collector_ids as string[]) || []
    const byId = useMemo(() => new Map(collectors.items.map((c) => [c.id, c])), [collectors.items])
    const available = collectors.items.filter((c) => !ids.includes(c.id)).map((c) => ({ value: c.id, label: collectorLabel(c) }))
    const move = (i: number, d: -1 | 1) => {
        const next = [...ids]
        const j = i + d
        if (j < 0 || j >= next.length) return
        ;[next[i], next[j]] = [next[j], next[i]]
        onChange({ ...value, collector_ids: next })
    }
    return (
        <>
            <DialogField label={t('p2.params.rrScope')} required>
                <Segmented
                    value={scope}
                    testId="p2-param-rr-scope"
                    options={[
                        { value: 'portfolio', label: t('p2.params.portfolio') },
                        { value: 'contract', label: t('p2.params.contract') },
                    ]}
                    onChange={(v) => onChange({ ...value, __ui: { ...(value.__ui || {}), scope: v } })}
                />
            </DialogField>
            {scope === 'portfolio' ? (
                <DialogField label={t('p2.params.portfolio')} required>
                    <div data-testid="p2-param-portfolio">
                        <Select
                            {...selectProps}
                            isLoading={portfolios.loading}
                            options={portfolios.options}
                            value={pick(portfolios.options, value.portfolio_id)}
                            onChange={(o) => onChange({ ...value, portfolio_id: (o as Option | null)?.value || '' })}
                        />
                    </div>
                </DialogField>
            ) : (
                <DialogField label={t('p2.params.contract')} required>
                    <div data-testid="p2-param-contract">
                        <Select
                            {...selectProps}
                            isLoading={contracts.loading}
                            options={contracts.options}
                            value={pick(contracts.options, value.contract_id)}
                            onChange={(o) => onChange({ ...value, contract_id: (o as Option | null)?.value || '' })}
                        />
                    </div>
                </DialogField>
            )}
            <DialogField label={t('p2.params.rrCollectors')} required hint={t('p2.params.rrOrderHint')}>
                <div data-testid="p2-param-rr-add">
                    <Select
                        {...selectProps}
                        isLoading={collectors.loading}
                        options={available}
                        value={null}
                        placeholder={String(t('p2.params.addCollector'))}
                        onChange={(o) => {
                            const id = (o as Option | null)?.value
                            if (id) onChange({ ...value, collector_ids: [...ids, id] })
                        }}
                    />
                </div>
            </DialogField>
            {ids.length > 0 && (
                <ol className="mb-4 flex flex-col gap-1.5" data-testid="p2-param-rr-list">
                    {ids.map((id, i) => {
                        const c = byId.get(id)
                        return (
                            <li
                                key={id}
                                data-collector={id}
                                className="flex items-center gap-2 rounded-md border border-gray-300 px-3 py-1.5 text-[13.5px] dark:border-night-border"
                            >
                                <span className="w-6 font-extrabold tabular-nums text-navy-600 dark:text-gray-100">{i + 1}</span>
                                <span className="min-w-0 flex-1 truncate">{c ? collectorLabel(c) : id}</span>
                                <Button size="xs" variant="plain" icon={<HiArrowUp />} disabled={i === 0} aria-label={String(t('p2.params.moveUp'))} onClick={() => move(i, -1)} />
                                <Button size="xs" variant="plain" icon={<HiArrowDown />} disabled={i === ids.length - 1} aria-label={String(t('p2.params.moveDown'))} onClick={() => move(i, 1)} />
                                <Button size="xs" variant="plain" icon={<HiX />} aria-label={String(t('p2.params.remove'))} onClick={() => onChange({ ...value, collector_ids: ids.filter((x) => x !== id) })} />
                            </li>
                        )
                    })}
                </ol>
            )}
            <DialogField label={t('p2.params.limit')} hint={t('p2.params.limitHint')}>
                <Input size="sm" inputMode="numeric" value={str(value.limit)} onChange={(e) => onChange({ ...value, limit: e.target.value.replace(/[^\d]/g, '') })} data-testid="p2-param-limit" />
            </DialogField>
            <ReasonField value={value.reason} onChange={(reason) => onChange({ ...value, reason })} label={t('p2.params.reasonOptional')} />
        </>
    )
}

const ArchiveForm = ({ value, onChange }: FormProps) => {
    const { t } = useTranslation()
    const reasons = useOptions(lookups.archiveReasons)
    return (
        <>
            <DialogField label={t('p2.params.archiveReason')} required>
                <div data-testid="p2-param-archive-reason">
                    <Select
                        {...selectProps}
                        isLoading={reasons.loading}
                        options={reasons.options}
                        value={pick(reasons.options, value.archive_reason_id)}
                        onChange={(o) => onChange({ ...value, archive_reason_id: (o as Option | null)?.value || '' })}
                    />
                </div>
            </DialogField>
            <ReasonField value={value.note} onChange={(note) => onChange({ ...value, note })} label={t('p2.params.archiveNote')} testId="p2-param-note" />
        </>
    )
}

const ReasonOnlyForm = ({ kind, value, onChange }: FormProps) => {
    const { t } = useTranslation()
    return (
        <ReasonField
            required
            value={value.reason}
            onChange={(reason) => onChange({ ...value, reason })}
            label={t(`p2.params.reasonFor.${kind}`, { defaultValue: t('p2.params.reason') })}
            hint={t('p2.params.reasonKept')}
        />
    )
}

const NotesForm = ({ value, onChange }: FormProps) => {
    const { t } = useTranslation()
    const methods = useOptions(lookups.contactMethods)
    const reach = useOptions(lookups.reachability)
    const contracts = useOptions(lookups.contracts)
    const [statuses, setStatuses] = useState<CommentStatusOption[]>([])
    useEffect(() => {
        let alive = true
        lookups
            .commentStatuses()
            .then((s) => alive && setStatuses(s))
            .catch(() => undefined)
        return () => {
            alive = false
        }
    }, [])
    const ui = value.__ui || {}
    const setUi = (patch: Record<string, string>) => onChange({ ...value, __ui: { ...ui, ...patch } })
    /* Same rule as the legacy note dialogs: the contract, then the reachability, then the status.
       0068: general statuses fit every contract; sub-statuses and deleted statuses are not offered. */
    const statusOptions = mainStatusesFor(statuses, ui.contract).filter(
        (s) => !value.comment_reachability_id || s.reachability_id === value.comment_reachability_id
    )
    return (
        <>
            <DialogField label={t('p2.params.noteContract')} hint={t('p2.params.noteContractHint')}>
                <div data-testid="p2-param-note-contract">
                    <Select
                        {...selectProps}
                        isClearable
                        isLoading={contracts.loading}
                        options={contracts.options}
                        value={pick(contracts.options, ui.contract)}
                        onChange={(o) => onChange({ ...value, comment_status_id: '', __ui: { ...ui, contract: (o as Option | null)?.value || '' } })}
                    />
                </div>
            </DialogField>
            <div className="grid grid-cols-1 gap-x-3 sm:grid-cols-2">
                <DialogField label={t('p2.params.reachability')}>
                    <div data-testid="p2-param-reachability">
                        <Select
                            {...selectProps}
                            isClearable
                            isLoading={reach.loading}
                            options={reach.options}
                            value={pick(reach.options, value.comment_reachability_id)}
                            onChange={(o) => onChange({ ...value, comment_reachability_id: (o as Option | null)?.value || '', comment_status_id: '' })}
                        />
                    </div>
                </DialogField>
                <DialogField label={t('p2.params.noteStatus')} required>
                    <div data-testid="p2-param-status">
                        <Select
                            {...selectProps}
                            options={statusOptions}
                            value={pick(statusOptions, value.comment_status_id)}
                            placeholder={ui.contract ? undefined : String(t('p2.params.pickContractFirst'))}
                            onChange={(o) => {
                                const s = o as CommentStatusOption | null
                                onChange({
                                    ...value,
                                    comment_status_id: s?.value || '',
                                    // the API stores the status's own reachability when none is given
                                    comment_reachability_id: value.comment_reachability_id || s?.reachability_id || '',
                                })
                            }}
                        />
                    </div>
                </DialogField>
            </div>
            <DialogField label={t('p2.params.contactMethod')} required>
                <div data-testid="p2-param-method">
                    <Select
                        {...selectProps}
                        isLoading={methods.loading}
                        options={methods.options}
                        value={pick(methods.options, value.contactMethod_id)}
                        onChange={(o) => onChange({ ...value, contactMethod_id: (o as Option | null)?.value || '' })}
                    />
                </div>
            </DialogField>
            <DialogField label={t('p2.params.noteText')} required hint={t('p2.params.noteTextHint')}>
                <Input textArea value={str(value.comment)} maxLength={5000} onChange={(e) => onChange({ ...value, comment: e.target.value })} data-testid="p2-param-comment" />
            </DialogField>
            <div className="grid grid-cols-1 gap-x-3 sm:grid-cols-2">
                <DialogField label={t('p2.params.callbackDate')} hint={t('p2.params.callbackHint')}>
                    <Input type="date" size="sm" min={riyadhToday()} value={ui.cbDate || ''} onChange={(e) => setUi({ cbDate: e.target.value })} data-testid="p2-param-cb-date" />
                </DialogField>
                <DialogField label={t('p2.params.callbackTime')}>
                    <Input type="time" size="sm" value={ui.cbTime || ''} onChange={(e) => setUi({ cbTime: e.target.value })} data-testid="p2-param-cb-time" />
                </DialogField>
            </div>
            {ui.cbDate && (
                <Note className="-mt-2 mb-4">
                    {t('p2.params.callbackSent')} <span dir="ltr" className="font-mono text-[12px]" data-testid="p2-param-cb-instant">{notesCallback(value)}</span>
                </Note>
            )}
            <DialogField label={t('p2.params.contactNumber')}>
                <Input size="sm" dir="ltr" maxLength={50} value={str(value.contactNumber)} onChange={(e) => onChange({ ...value, contactNumber: e.target.value })} data-testid="p2-param-contact-number" />
            </DialogField>
            <Note>{t('p2.params.notesContractRule')}</Note>
        </>
    )
}

const InquiryForm = ({ value, onChange }: FormProps) => {
    const { t } = useTranslation()
    const mode = value.match_mode === 'any' ? 'any' : 'all'
    return (
        <>
            <DialogField label={t('p2.params.matchMode')} hint={t(`p2.params.matchModeHint.${mode}`)}>
                <Segmented
                    value={mode}
                    testId="p2-param-match-mode"
                    options={[
                        { value: 'all', label: t('p2.params.matchAll') },
                        { value: 'any', label: t('p2.params.matchAny') },
                    ]}
                    onChange={(v) => onChange({ ...value, match_mode: v })}
                />
            </DialogField>
            <Note>{t('p2.params.inquiryScope')}</Note>
        </>
    )
}

const CloseoutForm = ({ value, onChange }: FormProps) => {
    const { t } = useTranslation()
    const users = useOptions(lookups.users)
    const contracts = useOptions(lookups.contracts)
    const portfolios = useOptions(lookups.portfolios as () => Promise<Option[]>)
    return (
        <>
            <ReasonField
                value={value.reason}
                onChange={(reason) => onChange({ ...value, reason })}
                label={t('p2.params.closeoutReason')}
                hint={t('p2.params.closeoutReasonHint')}
            />
            <div className="grid grid-cols-1 gap-x-3 sm:grid-cols-3">
                <DialogField label={t('p1.filter.owner')}>
                    <Select {...selectProps} isClearable isLoading={users.loading} options={users.options} value={pick(users.options, value.owner)} onChange={(o) => onChange({ ...value, owner: (o as Option | null)?.value || '' })} />
                </DialogField>
                <DialogField label={t('p1.filter.contract')}>
                    <Select {...selectProps} isClearable isLoading={contracts.loading} options={contracts.options} value={pick(contracts.options, value.contract_id)} onChange={(o) => onChange({ ...value, contract_id: (o as Option | null)?.value || '' })} />
                </DialogField>
                <DialogField label={t('p1.filter.portfolio')}>
                    <Select {...selectProps} isClearable isLoading={portfolios.loading} options={portfolios.options} value={pick(portfolios.options, value.portfolio_id)} onChange={(o) => onChange({ ...value, portfolio_id: (o as Option | null)?.value || '' })} />
                </DialogField>
            </div>
            <Note>{t('p2.params.closeoutRule')}</Note>
        </>
    )
}

const AttributeParamsForm = ({ value, onChange }: FormProps) => {
    const { t } = useTranslation()
    return (
        <>
            <ReasonField required value={value.reason} onChange={(reason) => onChange({ ...value, reason })} hint={t('p2.params.attrReasonHint')} />
            <Note>{t('p2.params.attrReview')}</Note>
        </>
    )
}

/* Phase 5: the reviewed PBX CDR import. Nothing is attributed until the preview; unmatched
 * and ambiguous records stay in the preview and the error file, never imported. */
const PbxCdrParamsForm = ({ value, onChange }: FormProps) => {
    const { t } = useTranslation()
    const windowOk = str(value.match_window_seconds) === '' || /^\d{1,4}$/.test(str(value.match_window_seconds))
    return (
        <>
            <ReasonField required value={value.reason} onChange={(reason) => onChange({ ...value, reason })} hint={t('p5.cdr.reasonHint')} />
            <div className="grid grid-cols-1 gap-x-3 sm:grid-cols-3">
                <DialogField label={t('p5.cdr.sourceName')} hint={t('p5.cdr.sourceNameHint')}>
                    <Input size="sm" dir="ltr" value={str(value.source_name)} placeholder="issabel" maxLength={100} onChange={(e) => onChange({ ...value, source_name: e.target.value })} data-testid="p5-cdr-source" />
                </DialogField>
                <DialogField label={t('p5.cdr.timezone')} hint={t('p5.cdr.timezoneHint')}>
                    <Input size="sm" dir="ltr" value={str(value.timezone)} placeholder="Asia/Riyadh" maxLength={60} onChange={(e) => onChange({ ...value, timezone: e.target.value })} data-testid="p5-cdr-timezone" />
                </DialogField>
                <DialogField label={t('p5.cdr.window')} hint={t('p5.cdr.windowHint')}>
                    <Input
                        size="sm"
                        dir="ltr"
                        inputMode="numeric"
                        value={str(value.match_window_seconds)}
                        placeholder="300"
                        onChange={(e) => onChange({ ...value, match_window_seconds: e.target.value.replace(/[^0-9]/g, '').slice(0, 4) })}
                        data-testid="p5-cdr-window"
                    />
                </DialogField>
            </div>
            {!windowOk && <Note className="text-danger">{t('p5.cdr.windowInvalid')}</Note>}
            <Note>{t('p5.cdr.rules')}</Note>
        </>
    )
}

/** Step 4 body for the kind. */
export const ParamsForm = (props: FormProps) => {
    switch (props.kind) {
        case 'assign':
            return <AssignForm {...props} />
        case 'round_robin':
            return <RoundRobinForm {...props} />
        case 'archive':
            return <ArchiveForm {...props} />
        case 'lock':
        case 'unlock':
        case 'restore':
            return <ReasonOnlyForm {...props} />
        case 'notes':
            return <NotesForm {...props} />
        case 'inquiry':
            return <InquiryForm {...props} />
        case 'followup_closeout':
            return <CloseoutForm {...props} />
        case 'attribute_corrections':
            return <AttributeParamsForm {...props} />
        case 'pbx_cdr_import':
            return <PbxCdrParamsForm {...props} />
        default:
            return null
    }
}

/** Blank-cell rule for attribute corrections (mapping step). */
export const BlankCellsChoice = ({ value, onChange }: { value: unknown; onChange: (v: 'skip' | 'remove') => void }) => {
    const { t } = useTranslation()
    const v = value === 'remove' ? 'remove' : 'skip'
    return (
        <DialogField label={t('p2.mapping.blankCells')} hint={t(`p2.mapping.blankHint.${v}`)}>
            <Segmented
                value={v}
                testId="p2-blank-cells"
                options={[
                    { value: 'skip', label: t('p2.mapping.blankSkip') },
                    { value: 'remove', label: t('p2.mapping.blankRemove') },
                ]}
                onChange={onChange}
            />
        </DialogField>
    )
}

/** Human summary of the params a job was created with (run page, jobs detail). */
export const ParamsSummary = ({ kind, params, className }: { kind: string; params: Record<string, unknown>; className?: string }) => {
    const { t } = useTranslation()
    const entries = Object.entries(params || {}).filter(([, v]) => v !== null && v !== undefined && v !== '')
    if (entries.length === 0) return <span className="text-gray-500">—</span>
    return (
        <dl className={classNames('grid grid-cols-[max-content_1fr] gap-x-3 gap-y-1 text-[13px]', className)} data-testid="p2-params-summary" data-kind={kind}>
            {entries.map(([k, v]) => (
                <div key={k} className="contents">
                    <dt className="text-gray-600">{t(`p2.field.${k}`, { defaultValue: k })}</dt>
                    <dd className="min-w-0 break-words text-gray-800 dark:text-night-body">
                        <ParamValue k={k} v={v} />
                    </dd>
                </div>
            ))}
        </dl>
    )
}

const ENUM_LABELS: Record<string, Record<string, string>> = {
    match_mode: { all: 'p2.params.matchAll', any: 'p2.params.matchAny' },
    blank_cells: { skip: 'p2.mapping.blankSkip', remove: 'p2.mapping.blankRemove' },
}

const ParamValue = ({ k, v }: { k: string; v: unknown }) => {
    const { t } = useTranslation()
    const [names, setNames] = useState<Record<string, string>>({})
    const wantsNames = ['target_user_id', 'collector_ids', 'owner', 'archive_reason_id', 'comment_status_id', 'contactMethod_id', 'comment_reachability_id', 'portfolio_id', 'contract_id'].includes(k)
    useEffect(() => {
        if (!wantsNames) return undefined
        let alive = true
        const load: Promise<Option[]> =
            k === 'archive_reason_id'
                ? lookups.archiveReasons()
                : k === 'comment_status_id'
                ? lookups.commentStatuses()
                : k === 'contactMethod_id'
                ? lookups.contactMethods()
                : k === 'comment_reachability_id'
                ? lookups.reachability()
                : k === 'portfolio_id'
                ? lookups.portfolios()
                : k === 'contract_id'
                ? lookups.contracts()
                : lookups.users()
        load
            .then((opts) => {
                if (!alive) return
                const m: Record<string, string> = {}
                opts.forEach((o) => {
                    m[o.value] = o.label
                })
                setNames(m)
            })
            .catch(() => undefined)
        return () => {
            alive = false
        }
    }, [k, wantsNames])
    if (Array.isArray(v)) {
        return (
            <ol className="list-inside list-decimal">
                {v.map((x, i) => (
                    <li key={i}>{names[String(x)] || String(x)}</li>
                ))}
            </ol>
        )
    }
    if (v && typeof v === 'object') {
        return (
            <span dir="auto">
                {Object.entries(v as Record<string, unknown>)
                    .map(([a, b]) => `${a} ← ${String(b)}`)
                    .join(' · ')}
            </span>
        )
    }
    const s = String(v)
    const enumKey = ENUM_LABELS[k]?.[s]
    if (enumKey) return <span>{t(enumKey)}</span>
    if (k === 'next_contact_date') return <RiyadhTime iso={s} />
    return <span dir="auto">{names[s] || s}</span>
}
