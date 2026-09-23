/**
 * Wizard step 3: explicit column mapping (target field → source column) with a
 * sample preview. Required fields are marked; a field used to find the case is
 * marked "match". For `attribute_corrections` an attribute-key → column editor
 * fills `params.attributes` (the API validates keys and columns again).
 */
import classNames from 'classnames'
import { useTranslation } from 'react-i18next'
import { HiPlus, HiX } from 'react-icons/hi'
import Button from '@/components/ui/Button'
import Input from '@/components/ui/Input'
import Select from '@/components/ui/Select'
import { PlainBadge } from '@/components/v2/Badges'
import { Note } from '@/components/v2/States'
import type { BulkKind, Option, UploadSheet } from '@/services/v2'
import { mappingProblems } from './mapping'
import type { AttrRow } from './mapping'
import { useIsArabic } from './shared'
import { BlankCellsChoice } from './params'
import type { Params } from './paramsLogic'

export type { AttrRow } from './mapping'

export const MappingStep = ({
    kind,
    sheet,
    mapping,
    onMapping,
    attrs,
    onAttrs,
    params,
    onParams,
    showProblems,
}: {
    kind: BulkKind
    sheet: UploadSheet
    mapping: Record<string, string>
    onMapping: (m: Record<string, string>) => void
    attrs: AttrRow[]
    onAttrs: (a: AttrRow[]) => void
    params: Params
    onParams: (p: Params) => void
    showProblems: boolean
}) => {
    const { t } = useTranslation()
    const ar = useIsArabic()
    const headers = sheet.headers || []
    const options: Option[] = headers.map((h, i) => ({ value: h, label: h || `#${i + 1}` }))
    const problems = mappingProblems(kind, mapping, attrs)
    const mappedCols = new Map<string, string>()
    kind.mapping_fields.forEach((f) => {
        if (mapping[f.key]) mappedCols.set(mapping[f.key], ar ? f.label_ar : f.label_en)
    })
    attrs.forEach((a) => {
        if (a.column && a.key.trim()) mappedCols.set(a.column, a.key.trim())
    })
    const sample = sheet.sample || []

    return (
        <div data-testid="p2-step-mapping">
            <div className="mb-2 text-[13px] text-gray-700 dark:text-night-body">
                {t('p2.mapping.intro', { sheet: sheet.name, rows: sheet.rows })}
            </div>
            <div className="mb-4 flex flex-col gap-3">
                {kind.mapping_fields.map((f) => (
                    <div key={f.key} className="grid grid-cols-1 items-start gap-2 sm:grid-cols-[260px_minmax(0,360px)]" data-testid="p2-map-field" data-field={f.key}>
                        <div>
                            <div className="text-[13px] font-extrabold text-navy-600 dark:text-gray-100">
                                {ar ? f.label_ar : f.label_en}
                                {f.required && <span className="text-danger" aria-label={String(t('p2.mapping.required'))}> *</span>}
                            </div>
                            <div className="mt-1 flex flex-wrap gap-1">
                                {f.required && <PlainBadge size="row" tone="info">{t('p2.mapping.required')}</PlainBadge>}
                                {f.match && <PlainBadge size="row" tone="neutral">{t('p2.mapping.match')}</PlainBadge>}
                            </div>
                            {f.key === 'login' && <div className="mt-1 text-[12px] text-gray-600">{t('p2.mapping.loginHint')}</div>}
                            {f.key === 'contact_number' && <div className="mt-1 text-[12px] text-gray-600">{t('p2.mapping.mobileHint')}</div>}
                        </div>
                        <div data-testid={`p2-map-${f.key}`}>
                            <Select
                                size="sm"
                                menuPosition="fixed"
                                isClearable={!f.required}
                                options={options}
                                placeholder={String(t('p2.mapping.pickColumn'))}
                                value={options.find((o) => o.value === mapping[f.key]) || null}
                                onChange={(o) => {
                                    const next = { ...mapping }
                                    const v = (o as Option | null)?.value
                                    if (v) next[f.key] = v
                                    else delete next[f.key]
                                    onMapping(next)
                                }}
                            />
                        </div>
                    </div>
                ))}
            </div>

            {kind.key === 'attribute_corrections' && (
                <div className="mb-4 rounded-lg border border-gray-300 p-4 dark:border-night-border" data-testid="p2-attr-editor">
                    <div className="mb-1 text-[13px] font-extrabold text-navy-600 dark:text-gray-100">
                        {t('p2.mapping.attributes')} <span className="text-danger">*</span>
                    </div>
                    <Note className="mb-3">{t('p2.mapping.attributesHint')}</Note>
                    <div className="flex flex-col gap-2">
                        {attrs.map((a, i) => (
                            <div key={i} className="grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto] items-center gap-2" data-testid="p2-attr-row">
                                <Input
                                    size="sm"
                                    dir="auto"
                                    placeholder={String(t('p2.mapping.attrKey'))}
                                    value={a.key}
                                    maxLength={200}
                                    onChange={(e) => onAttrs(attrs.map((x, j) => (j === i ? { ...x, key: e.target.value } : x)))}
                                    data-testid="p2-attr-key"
                                />
                                <div data-testid="p2-attr-column">
                                    <Select
                                        size="sm"
                                        menuPosition="fixed"
                                        options={options}
                                        placeholder={String(t('p2.mapping.pickColumn'))}
                                        value={options.find((o) => o.value === a.column) || null}
                                        onChange={(o) =>
                                            onAttrs(
                                                attrs.map((x, j) =>
                                                    j === i ? { key: x.key || (o as Option | null)?.value || '', column: (o as Option | null)?.value || '' } : x
                                                )
                                            )
                                        }
                                    />
                                </div>
                                <Button size="xs" variant="plain" icon={<HiX />} aria-label={String(t('p2.params.remove'))} onClick={() => onAttrs(attrs.filter((_, j) => j !== i))} />
                            </div>
                        ))}
                    </div>
                    <div className="mt-3 flex flex-wrap gap-2">
                        <Button size="xs" icon={<HiPlus />} onClick={() => onAttrs([...attrs, { key: '', column: '' }])} data-testid="p2-attr-add">
                            {t('p2.mapping.addAttr')}
                        </Button>
                        <Button
                            size="xs"
                            variant="plain"
                            onClick={() => {
                                const used = new Set([...Object.values(mapping), ...attrs.map((a) => a.column)])
                                onAttrs([...attrs.filter((a) => a.key || a.column), ...headers.filter((h) => h && !used.has(h)).map((h) => ({ key: h, column: h }))])
                            }}
                        >
                            {t('p2.mapping.addAllColumns')}
                        </Button>
                    </div>
                    <div className="mt-4">
                        <BlankCellsChoice value={params.blank_cells} onChange={(v) => onParams({ ...params, blank_cells: v })} />
                    </div>
                </div>
            )}

            {showProblems && problems.length > 0 && (
                <div role="alert" className="mb-4 rounded-md border border-red-200 bg-danger-tint px-3 py-2 text-[13px] text-danger" data-testid="p2-mapping-problems">
                    {problems.map((p) => {
                        const [code, field] = p.split(':')
                        const f = kind.mapping_fields.find((x) => x.key === field)
                        return <div key={p}>{t(`p2.mapping.problem.${code}`, { field: f ? (ar ? f.label_ar : f.label_en) : field })}</div>
                    })}
                </div>
            )}

            <div className="text-eyebrow mb-2">{t('p2.mapping.sample')}</div>
            {sample.length === 0 ? (
                <Note>{t('p2.mapping.noSample')}</Note>
            ) : (
                <div className="overflow-x-auto rounded-lg border border-gray-300 dark:border-night-border" data-testid="p2-sample">
                    <table className="w-full text-[12.5px]">
                        <thead>
                            <tr className="bg-[#DFEEF2] dark:bg-night-raised">
                                {headers.map((h, i) => (
                                    <th
                                        key={i}
                                        className={classNames(
                                            'whitespace-nowrap px-3 py-2 text-start font-extrabold',
                                            mappedCols.has(h) ? 'text-navy-600 dark:text-gray-100' : 'text-gray-600'
                                        )}
                                    >
                                        {mappedCols.has(h) && (
                                            <div className="mb-0.5 text-[11px] font-extrabold text-azure-600 dark:text-azure-300" data-mapped-to={mappedCols.get(h)}>
                                                → {mappedCols.get(h)}
                                            </div>
                                        )}
                                        <bdi>{h || `#${i + 1}`}</bdi>
                                    </th>
                                ))}
                            </tr>
                        </thead>
                        <tbody>
                            {sample.map((row, r) => (
                                <tr key={r} className="border-t border-gray-200 dark:border-night-border">
                                    {headers.map((h, c) => {
                                        const v = row[c]
                                        const formula = typeof v === 'string' && v.startsWith('=')
                                        return (
                                            <td
                                                key={c}
                                                className={classNames('whitespace-nowrap px-3 py-1.5', mappedCols.has(h) ? 'bg-azure-50 dark:bg-night-raised' : '', formula && 'text-warning')}
                                                title={formula ? String(t('p2.mapping.formulaCell')) : undefined}
                                            >
                                                <bdi dir="auto">{v === null || v === undefined ? '' : String(v)}</bdi>
                                            </td>
                                        )
                                    })}
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            )}
        </div>
    )
}

export default MappingStep
