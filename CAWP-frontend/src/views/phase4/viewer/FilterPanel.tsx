/**
 * The report's filter form, built from `definition.filters` by type:
 *
 *   date_range       inclusive civil dates (payment / promise dates)
 *   timestamp_range  Riyadh day bounds over an instant (notes, CDR, callbacks)
 *   enum             chips (multi) or a segmented choice (single)
 *   ids              scoped async selector (IdsFilter)
 *   text             literal "contains"; applied on Enter or blur
 *   amount_range     exact SAR bounds; applied on Enter or blur
 *   month / date / hour_range   RPT-19 / RPT-22-23 / RPT-23
 *
 * Every value is a flat query parameter (the definition's `params`). Required
 * filters are marked; 422 field codes from the API are shown under their own
 * filter in the current language. Unknown filter types fall back to text
 * inputs on their params, so a new report still opens.
 */
import classNames from 'classnames'
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { HiOutlineChevronDown, HiOutlineChevronUp, HiOutlineInformationCircle } from 'react-icons/hi'
import Button from '@/components/ui/Button'
import Input from '@/components/ui/Input'
import { fieldErrorText } from '@/services/v2'
import type {
    AmountRangeFilterDef,
    DateRangeFilterDef,
    EnumFilterDef,
    IdsFilterDef,
    ReportDefinition,
    TextFilterDef,
} from '@/services/v2'
import IdsFilter from './IdsFilter'
import { hasServerDefault, pick } from '../reportUtils'
import type { AnyFilterDef } from '../reportUtils'

type Props = {
    def: ReportDefinition
    values: Record<string, string>
    defaults: Record<string, string>
    errors: Record<string, string> | null
    onChange: (patch: Record<string, string>) => void
    onReset: () => void
}

const HOURS = Array.from({ length: 24 }, (_, h) => h)

/** A text-like input that commits on Enter or blur (no request per keystroke). */
function CommitInput({
    value,
    onCommit,
    invalid,
    testId,
    ...rest
}: {
    value: string
    onCommit: (v: string) => void
    invalid?: boolean
    testId?: string
    placeholder?: string
    maxLength?: number
    inputMode?: 'decimal' | 'text'
    dir?: string
    'aria-label'?: string
}) {
    const [draft, setDraft] = useState(value)
    useEffect(() => setDraft(value), [value])
    const commit = () => {
        const v = draft.trim()
        if (v !== value) onCommit(v)
    }
    return (
        <Input
            size="sm"
            value={draft}
            invalid={invalid}
            onChange={(e) => setDraft(e.target.value)}
            onBlur={commit}
            onKeyDown={(e) => {
                if (e.key === 'Enter') commit()
            }}
            data-testid={testId}
            {...rest}
        />
    )
}

export default function FilterPanel({ def, values, defaults, errors, onChange, onReset }: Props) {
    const { t, i18n } = useTranslation()
    const ar = (i18n.language || 'ar').startsWith('ar')
    const [open, setOpen] = useState(true)
    const filterByKey = Object.fromEntries(def.filters.map((f) => [f.key, f]))

    const active = def.filters.filter((f) => f.params.some((p) => (values[p] ?? '') !== (defaults[p] ?? ''))).length

    const errFor = (f: AnyFilterDef) => f.params.map((p) => errors?.[p]).find(Boolean)

    /** Params of the depends_on filters plus every required filter (the selector endpoint needs them). */
    const contextFor = (f: IdsFilterDef) => {
        const ctx: Record<string, string> = {}
        const keys = new Set([...(f.depends_on || []), ...def.filters.filter((x) => x.required).map((x) => x.key)])
        keys.forEach((k) => {
            const dep = filterByKey[k]
            if (!dep) return
            dep.params.forEach((p) => {
                if (values[p]) ctx[p] = values[p]
            })
        })
        return ctx
    }

    /** Changing an ids filter clears the ids filters that depend on it (a portfolio list follows the contract). */
    const changeIds = (f: IdsFilterDef, csv: string) => {
        const patch: Record<string, string> = { [f.params[0]]: csv }
        def.filters.forEach((other) => {
            if (other.type === 'ids' && (other as IdsFilterDef).depends_on?.includes(f.key)) {
                other.params.forEach((p) => {
                    if (values[p]) patch[p] = ''
                })
            }
        })
        onChange(patch)
    }

    const semanticsHint = (f: AnyFilterDef) => {
        const d = f as DateRangeFilterDef
        const parts: string[] = []
        if (d.semantics) parts.push(t(`p4.semantics.${d.semantics}`, { defaultValue: d.semantics }))
        if (d.max_days) parts.push(t('p4.filter.maxDays', { n: d.max_days }))
        if (hasServerDefault(f) && d.default)
            parts.push(t(`p4.filter.serverDefault.${typeof d.default === 'string' ? d.default : 'other'}`, { defaultValue: '' }))
        return parts.filter(Boolean).join(' · ')
    }

    const renderControl = (f: AnyFilterDef) => {
        const err = errFor(f)
        switch (f.type) {
            case 'date_range':
            case 'timestamp_range': {
                const [pFrom, pTo] = f.params
                return (
                    <div className="flex items-center gap-2">
                        <Input
                            size="sm"
                            type="date"
                            dir="ltr"
                            aria-label={String(t('p4.filter.from'))}
                            value={values[pFrom] || ''}
                            max={values[pTo] || undefined}
                            invalid={!!errors?.[pFrom]}
                            onChange={(e) => onChange({ [pFrom]: e.target.value })}
                            data-testid={`p4-in-${pFrom}`}
                        />
                        <span className="text-gray-500">–</span>
                        <Input
                            size="sm"
                            type="date"
                            dir="ltr"
                            aria-label={String(t('p4.filter.to'))}
                            value={values[pTo] || ''}
                            min={values[pFrom] || undefined}
                            invalid={!!errors?.[pTo]}
                            onChange={(e) => onChange({ [pTo]: e.target.value })}
                            data-testid={`p4-in-${pTo}`}
                        />
                    </div>
                )
            }
            case 'month':
                return (
                    <Input
                        size="sm"
                        type="month"
                        dir="ltr"
                        value={values[f.params[0]] || ''}
                        invalid={!!err}
                        onChange={(e) => onChange({ [f.params[0]]: e.target.value })}
                        data-testid={`p4-in-${f.params[0]}`}
                    />
                )
            case 'date':
                return (
                    <Input
                        size="sm"
                        type="date"
                        dir="ltr"
                        value={values[f.params[0]] || ''}
                        invalid={!!err}
                        onChange={(e) => onChange({ [f.params[0]]: e.target.value })}
                        data-testid={`p4-in-${f.params[0]}`}
                    />
                )
            case 'hour_range': {
                const [pFrom, pTo] = f.params
                const sel = (p: string, aria: string) => (
                    <select
                        className={classNames(
                            'h-9 rounded-md border bg-white px-2 text-[13.5px] tabular-nums dark:bg-night-card',
                            errors?.[p] ? 'border-danger' : 'border-[#B8C4CE] dark:border-night-border'
                        )}
                        dir="ltr"
                        aria-label={aria}
                        value={values[p] || ''}
                        onChange={(e) => onChange({ [p]: e.target.value })}
                        data-testid={`p4-in-${p}`}
                    >
                        {HOURS.map((h) => (
                            <option key={h} value={String(h)}>
                                {String(h).padStart(2, '0')}:00
                            </option>
                        ))}
                    </select>
                )
                return (
                    <div className="flex items-center gap-2">
                        {sel(pFrom, String(t('p4.filter.fromHour')))}
                        <span className="text-gray-500">–</span>
                        {sel(pTo, String(t('p4.filter.toHour')))}
                    </div>
                )
            }
            case 'enum': {
                const e = f as EnumFilterDef
                const p = e.params[0]
                const current = (values[p] || '').split(',').filter(Boolean)
                if (!e.multi && e.choices.length > 3) {
                    /* Many single choices: radio chips that wrap cleanly (RPT-24 section). */
                    const opts = [
                        ...(e.default && e.default.length ? [] : [{ value: '', labels: { en: 'All', ar: 'الكل' } }]),
                        ...e.choices,
                    ]
                    return (
                        <div className="flex flex-wrap gap-1.5" role="radiogroup">
                            {opts.map((c) => {
                                const on = (current[0] || '') === c.value
                                return (
                                    <button
                                        key={c.value || 'all'}
                                        type="button"
                                        role="radio"
                                        aria-checked={on}
                                        onClick={() => onChange({ [p]: c.value })}
                                        data-testid={`p4-enum-${e.key}-${c.value || 'all'}`}
                                        className={classNames(
                                            'h-7 rounded-pill px-3 text-[12.5px]',
                                            on
                                                ? 'border border-navy-600 bg-navy-600 font-extrabold text-white dark:bg-azure-500'
                                                : 'border border-[#B8C4CE] text-gray-700 dark:border-night-border dark:text-gray-200'
                                        )}
                                    >
                                        {c.value === '' ? t('p4.filter.all') : pick(c.labels, ar)}
                                    </button>
                                )
                            })}
                        </div>
                    )
                }
                if (!e.multi) {
                    const opts = [
                        ...(e.default && e.default.length ? [] : [{ value: '', labels: { en: 'All', ar: 'الكل' } }]),
                        ...e.choices,
                    ]
                    return (
                        <div className="inline-flex overflow-hidden rounded-md border border-[#B8C4CE] dark:border-night-border" role="radiogroup">
                            {opts.map((c, i) => {
                                const on = (current[0] || '') === c.value
                                return (
                                    <button
                                        key={c.value || 'all'}
                                        type="button"
                                        role="radio"
                                        aria-checked={on}
                                        onClick={() => onChange({ [p]: c.value })}
                                        data-testid={`p4-enum-${e.key}-${c.value || 'all'}`}
                                        className={classNames(
                                            'h-8 px-3 text-[12.5px]',
                                            i > 0 && 'border-s border-[#B8C4CE] dark:border-night-border',
                                            on ? 'bg-navy-600 font-extrabold text-white dark:bg-azure-500' : 'text-gray-800 dark:text-gray-200'
                                        )}
                                    >
                                        {c.value === '' ? t('p4.filter.all') : pick(c.labels, ar)}
                                    </button>
                                )
                            })}
                        </div>
                    )
                }
                return (
                    <div className="flex flex-wrap gap-1.5">
                        {e.choices.map((c) => {
                            const on = current.includes(c.value)
                            return (
                                <button
                                    key={c.value}
                                    type="button"
                                    aria-pressed={on}
                                    onClick={() => {
                                        const next = on ? current.filter((v) => v !== c.value) : [...current, c.value]
                                        const order = e.choices.map((x) => x.value)
                                        next.sort((a, b) => order.indexOf(a) - order.indexOf(b))
                                        onChange({ [p]: next.join(',') })
                                    }}
                                    data-testid={`p4-enum-${e.key}-${c.value}`}
                                    className={classNames(
                                        'h-7 rounded-pill px-3 text-[12.5px]',
                                        on
                                            ? 'border border-azure-500 bg-azure-50 font-extrabold text-azure-600 dark:bg-night-raised'
                                            : 'border border-dashed border-[#B8C4CE] text-gray-600 dark:border-night-border'
                                    )}
                                >
                                    {pick(c.labels, ar)}
                                </button>
                            )
                        })}
                    </div>
                )
            }
            case 'ids': {
                const idf = f as IdsFilterDef
                return (
                    <IdsFilter
                        reportId={def.id}
                        filter={idf}
                        value={values[idf.params[0]] || ''}
                        onChange={(csv) => changeIds(idf, csv)}
                        context={contextFor(idf)}
                        dependsLabels={(idf.depends_on || [])
                            .map((k) => filterByKey[k])
                            .filter(Boolean)
                            .map((x) => pick(x.labels, ar))}
                        invalid={!!err}
                    />
                )
            }
            case 'amount_range': {
                const a = f as AmountRangeFilterDef
                const [pFrom, pTo] = a.params
                return (
                    <div className="flex items-center gap-2">
                        <CommitInput
                            value={values[pFrom] || ''}
                            inputMode="decimal"
                            dir="ltr"
                            aria-label={String(t('p4.filter.min'))}
                            placeholder={String(t('p4.filter.min'))}
                            invalid={!!errors?.[pFrom]}
                            onCommit={(v) => onChange({ [pFrom]: v })}
                            testId={`p4-in-${pFrom}`}
                        />
                        <span className="text-gray-500">–</span>
                        <CommitInput
                            value={values[pTo] || ''}
                            inputMode="decimal"
                            dir="ltr"
                            aria-label={String(t('p4.filter.max'))}
                            placeholder={String(t('p4.filter.max'))}
                            invalid={!!errors?.[pTo]}
                            onCommit={(v) => onChange({ [pTo]: v })}
                            testId={`p4-in-${pTo}`}
                        />
                    </div>
                )
            }
            case 'text': {
                const tf = f as TextFilterDef
                return (
                    <CommitInput
                        value={values[tf.params[0]] || ''}
                        maxLength={tf.max_len || 100}
                        dir="auto"
                        placeholder={String(t('p4.filter.textPlaceholder'))}
                        invalid={!!err}
                        onCommit={(v) => onChange({ [tf.params[0]]: v })}
                        testId={`p4-in-${tf.params[0]}`}
                    />
                )
            }
            default:
                return (
                    <div className="flex flex-col gap-2">
                        {f.params.map((p) => (
                            <CommitInput
                                key={p}
                                value={values[p] || ''}
                                dir="auto"
                                placeholder={p}
                                invalid={!!errors?.[p]}
                                onCommit={(v) => onChange({ [p]: v })}
                                testId={`p4-in-${p}`}
                            />
                        ))}
                    </div>
                )
        }
    }

    return (
        <section
            className="mb-4 rounded-lg border border-gray-300 bg-white dark:border-night-border dark:bg-night-card"
            data-testid="p4-filters"
            aria-label={String(t('p4.filter.title'))}
        >
            <div className="flex flex-wrap items-center justify-between gap-2 border-b border-gray-300 px-5 py-3 dark:border-night-border">
                <button
                    type="button"
                    className="flex items-center gap-2 text-[15px] font-extrabold text-navy-600 dark:text-gray-100"
                    aria-expanded={open}
                    onClick={() => setOpen((o) => !o)}
                    data-testid="p4-filters-toggle"
                >
                    {open ? <HiOutlineChevronUp aria-hidden /> : <HiOutlineChevronDown aria-hidden />}
                    {t('p4.filter.title')}
                    {active > 0 && (
                        <span
                            className="rounded-pill bg-azure-50 px-2 text-[11.5px] font-extrabold text-azure-600 tabular-nums"
                            data-testid="p4-filters-active"
                        >
                            {t('p4.filter.active', { n: active })}
                        </span>
                    )}
                </button>
                <div className="flex items-center gap-2">
                    <span className="text-[12px] text-gray-600">
                        <span className="text-danger">*</span> {t('p4.filter.requiredLegend')}
                    </span>
                    {active > 0 && (
                        <Button size="sm" variant="plain" onClick={onReset} data-testid="p4-filters-reset">
                            {t('p4.filter.reset')}
                        </Button>
                    )}
                </div>
            </div>
            {open && (
                <div className="grid grid-cols-1 gap-x-4 gap-y-4 px-5 py-4 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4">
                    {def.filters.map((f) => {
                        const hint = semanticsHint(f)
                        const help = pick(f.help, ar)
                        const fieldErrors = f.params.filter((p) => errors?.[p])
                        return (
                            <div
                                key={f.key}
                                className="min-w-0"
                                data-testid={`p4-filter-${f.key}`}
                                data-type={f.type}
                                data-required={f.required ? 'true' : 'false'}
                            >
                                <div className="mb-1 flex items-center gap-1 text-[12.5px] font-extrabold text-navy-600 dark:text-gray-100">
                                    <span>{pick(f.labels, ar)}</span>
                                    {f.required && (
                                        <span className="text-danger" aria-label={String(t('p4.filter.required'))} data-testid="p4-required">
                                            *
                                        </span>
                                    )}
                                    {help && (
                                        <span title={help} className="text-gray-500">
                                            <HiOutlineInformationCircle aria-hidden />
                                            <span className="sr-only">{help}</span>
                                        </span>
                                    )}
                                </div>
                                {renderControl(f)}
                                {hint && <div className="mt-1 text-[11.5px] text-gray-600">{hint}</div>}
                                {fieldErrors.map((p) => (
                                    <div
                                        key={p}
                                        role="alert"
                                        className="mt-1 text-[12px] text-danger"
                                        data-testid="p4-field-error"
                                        data-param={p}
                                        data-code={errors?.[p]}
                                    >
                                        {f.params.length > 1 && (
                                            <span className="font-extrabold">
                                                {p.endsWith('_from') || p === 'from_hour'
                                                    ? t('p4.filter.from')
                                                    : p.endsWith('_to') || p === 'to_hour'
                                                    ? t('p4.filter.to')
                                                    : p}
                                                :{' '}
                                            </span>
                                        )}
                                        {fieldErrorText(errors?.[p] || '', t, {
                                            max_days: (f as DateRangeFilterDef).max_days,
                                            max_len: (f as TextFilterDef).max_len,
                                            max_ids: (f as IdsFilterDef).max_ids,
                                        })}
                                    </div>
                                ))}
                            </div>
                        )
                    })}
                </div>
            )}
        </section>
    )
}
