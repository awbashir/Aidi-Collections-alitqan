/**
 * Step 2 of the portfolio upload: every system field is a select of the file's columns
 * (prefilled by the server's Arabic/English guess), any number of columns can be mapped to a
 * contact type, and the collector username is optional. Every original column is still kept
 * in the case's additional information. Sample rows show what each choice picks up.
 */
import classNames from 'classnames'
import { useTranslation } from 'react-i18next'
import { HiPlus, HiX } from 'react-icons/hi'
import Button from '@/components/ui/Button'
import Select from '@/components/ui/Select'
import { PlainBadge } from '@/components/v2/Badges'
import { Note } from '@/components/v2/States'
import type { UploadSheet } from '@/services/v2'
import type { ContactType, ImportField, ImportMapping } from '@/services/v2/portfolioImportApi'
import { mappingProblems } from './importLogic'

type Opt = { value: string; label: string }

const ImportMappingStep = ({
    fields,
    contactTypes,
    sheet,
    mapping,
    onMapping,
    showProblems,
}: {
    fields: ImportField[]
    contactTypes: ContactType[]
    sheet: UploadSheet
    mapping: ImportMapping
    onMapping: (m: ImportMapping) => void
    showProblems: boolean
}) => {
    const { t, i18n } = useTranslation()
    const ar = (i18n.language || '').startsWith('ar')
    const headers = sheet.headers || []
    const columnOptions: Opt[] = headers.map((h) => ({ value: h, label: h }))
    const typeOptions: Opt[] = contactTypes.map((c) => ({ value: c.id, label: c.name }))
    const problems = mappingProblems(fields, mapping)
    const fieldLabel = (f: ImportField) => (ar ? f.label_ar : f.label_en)

    const mappedTo = new Map<string, string>()
    fields.forEach((f) => {
        const col = mapping.fields[f.key]
        if (col) mappedTo.set(col, fieldLabel(f))
    })
    mapping.contacts.forEach((c) => {
        const type = contactTypes.find((x) => x.id === c.contact_type_id)
        if (c.column) mappedTo.set(c.column, `${t('p7.import.mapping.contact')}: ${type?.name || '—'}`)
    })
    const sample = sheet.sample || []

    const setField = (key: string, col: string | undefined) => {
        const next = { ...mapping.fields }
        if (col) next[key as keyof ImportMapping['fields']] = col
        else delete next[key as keyof ImportMapping['fields']]
        onMapping({ ...mapping, fields: next })
    }
    const setContact = (i: number, patch: Partial<ImportMapping['contacts'][number]>) =>
        onMapping({ ...mapping, contacts: mapping.contacts.map((c, j) => (j === i ? { ...c, ...patch } : c)) })

    return (
        <div data-testid="p7-import-mapping">
            <Note className="mb-3">{t('p7.import.mapping.intro', { sheet: sheet.name, rows: sheet.rows })}</Note>

            <div className="mb-4 flex flex-col gap-3">
                {fields.map((f) => (
                    <div
                        key={f.key}
                        className="grid grid-cols-1 items-start gap-2 sm:grid-cols-[220px_minmax(0,340px)]"
                        data-field={f.key}
                    >
                        <div>
                            <div className="text-[13px] font-extrabold text-navy-600 dark:text-gray-100">
                                {fieldLabel(f)}
                                {f.required && <span className="text-danger"> *</span>}
                            </div>
                            {f.required ? (
                                <PlainBadge size="row" tone="info">
                                    {t('p7.import.mapping.required')}
                                </PlainBadge>
                            ) : (
                                <PlainBadge size="row" tone="neutral">
                                    {t('p7.import.mapping.optional')}
                                </PlainBadge>
                            )}
                            {f.key === 'collector_username' && (
                                <div className="mt-1 text-[12px] text-gray-600">{t('p7.import.mapping.collectorHint')}</div>
                            )}
                        </div>
                        <div data-testid={`p7-import-map-${f.key}`}>
                            <Select
                                size="sm"
                                menuPosition="fixed"
                                isClearable={!f.required}
                                options={columnOptions}
                                placeholder={String(t('p7.import.mapping.pickColumn'))}
                                value={columnOptions.find((o) => o.value === mapping.fields[f.key]) || null}
                                onChange={(o) => setField(f.key, (o as Opt | null)?.value)}
                            />
                        </div>
                    </div>
                ))}
            </div>

            <div className="mb-4 rounded-lg border border-gray-300 p-4 dark:border-night-border" data-testid="p7-import-contacts">
                <div className="mb-1 text-[13px] font-extrabold text-navy-600 dark:text-gray-100">
                    {t('p7.import.mapping.contactsTitle')}
                </div>
                <Note className="mb-3">{t('p7.import.mapping.contactsHint')}</Note>
                <div className="flex flex-col gap-2">
                    {mapping.contacts.map((c, i) => (
                        <div key={i} className="grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto] items-center gap-2">
                            <Select
                                size="sm"
                                menuPosition="fixed"
                                options={columnOptions}
                                placeholder={String(t('p7.import.mapping.pickColumn'))}
                                value={columnOptions.find((o) => o.value === c.column) || null}
                                onChange={(o) => setContact(i, { column: (o as Opt | null)?.value || '' })}
                            />
                            <Select
                                size="sm"
                                menuPosition="fixed"
                                options={typeOptions}
                                placeholder={String(t('p7.import.mapping.pickType'))}
                                value={typeOptions.find((o) => o.value === c.contact_type_id) || null}
                                onChange={(o) => setContact(i, { contact_type_id: (o as Opt | null)?.value || '' })}
                            />
                            <Button
                                size="xs"
                                variant="plain"
                                icon={<HiX />}
                                aria-label={String(t('p7.import.mapping.removeContact'))}
                                onClick={() => onMapping({ ...mapping, contacts: mapping.contacts.filter((_, j) => j !== i) })}
                            />
                        </div>
                    ))}
                </div>
                <Button
                    className="mt-3"
                    size="xs"
                    icon={<HiPlus />}
                    onClick={() => onMapping({ ...mapping, contacts: [...mapping.contacts, { column: '', contact_type_id: '' }] })}
                >
                    {t('p7.import.mapping.addContact')}
                </Button>
            </div>

            <Note className="mb-3">{t('p7.import.mapping.attributesNote')}</Note>
            {sheet.unnamed_data_columns > 0 && (
                <div className="mb-3 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-[12.5px] text-warning dark:border-night-border dark:bg-night-raised">
                    {t('p7.import.mapping.unnamedColumns', { count: sheet.unnamed_data_columns })}
                </div>
            )}

            {showProblems && problems.length > 0 && (
                <div role="alert" className="mb-4 rounded-md border border-red-200 bg-danger-tint px-3 py-2 text-[13px] text-danger">
                    {problems.map((p) => {
                        const [code, rest] = [p.split(':')[0], p.slice(p.indexOf(':') + 1)]
                        const f = fields.find((x) => x.key === rest)
                        return (
                            <div key={p}>
                                {t(`p7.import.mapping.problem.${code}`, { field: f ? fieldLabel(f) : rest, column: rest })}
                            </div>
                        )
                    })}
                </div>
            )}

            <div className="text-eyebrow mb-2">{t('p7.import.mapping.sample')}</div>
            {sample.length === 0 ? (
                <Note>{t('p7.import.mapping.noSample')}</Note>
            ) : (
                <div className="overflow-x-auto rounded-lg border border-gray-300 dark:border-night-border">
                    <table className="w-full text-[12.5px]">
                        <thead>
                            <tr className="bg-[#DFEEF2] dark:bg-night-raised">
                                {headers.map((h, i) => (
                                    <th
                                        key={i}
                                        className={classNames(
                                            'whitespace-nowrap px-3 py-2 text-start font-extrabold',
                                            mappedTo.has(h) ? 'text-navy-600 dark:text-gray-100' : 'text-gray-600'
                                        )}
                                    >
                                        {mappedTo.has(h) && (
                                            <div className="mb-0.5 text-[11px] font-extrabold text-azure-600 dark:text-azure-300">
                                                → {mappedTo.get(h)}
                                            </div>
                                        )}
                                        <bdi>{h}</bdi>
                                    </th>
                                ))}
                            </tr>
                        </thead>
                        <tbody>
                            {sample.map((row, r) => (
                                <tr key={r} className="border-t border-gray-200 dark:border-night-border">
                                    {headers.map((h, c) => {
                                        const v = row[c]
                                        return (
                                            <td
                                                key={c}
                                                className={classNames(
                                                    'whitespace-nowrap px-3 py-1.5',
                                                    mappedTo.has(h) && 'bg-azure-50 dark:bg-night-raised'
                                                )}
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

export default ImportMappingStep
