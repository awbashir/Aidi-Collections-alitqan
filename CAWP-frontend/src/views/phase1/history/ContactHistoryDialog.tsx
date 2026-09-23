/**
 * A debtor's contact history (GET /v2/debtors/<identity>/contacts/history),
 * newest first, paged. Row events of one statement are already collapsed by
 * the API (`row_count`). Legacy writes have no known actor: that is shown as
 * such, never guessed.
 */
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import Dialog from '@/components/ui/Dialog'
import Table from '@/components/ui/Table'
import { historyApi } from '@/services/v2'
import type { ContactHistoryItem } from '@/services/v2'
import { useV2Query } from '@/utils/v2/hooks'
import { Ltr, RiyadhTime } from '@/components/v2/Display'
import { PlainBadge } from '@/components/v2/Badges'
import { EmptyState, ErrorState, SkeletonRows } from '@/components/v2/States'
import { PagerFooter } from '@/components/v2/ListParts'
import { ActorName } from './parts'
import { useContactTypes } from './ContactDialogs'

const { THead, TBody, Tr, Th, Td } = Table
const PAGE_SIZE = 20

/** Columns that change on every write and say nothing to a reader. */
const NOISE = new Set(['update_at', 'updated_at', 'create_at', 'created_at', 'revision', 'blocked_at', 'blocked_by', 'id'])
/** Insert / delete images: the columns worth showing. */
const VALUE_KEYS = ['contact', 'contact_type_id', 'is_default', 'is_active', 'is_blocked', 'block_reason', 'is_deleted']

const HistoryValue = ({ field, value, typeName }: { field: string; value: unknown; typeName: (id: string) => string }) => {
    const { t } = useTranslation()
    if (value === null || value === undefined || value === '') return <span className="italic text-gray-500">{t('p1.corr.emptyValue')}</span>
    if (typeof value === 'boolean') return <span>{value ? t('p1.corr.true') : t('p1.corr.false')}</span>
    if (field === 'contact_type_id') return <span>{typeName(String(value))}</span>
    if (field === 'contact') return <Ltr>{String(value)}</Ltr>
    return <span dir="auto">{typeof value === 'object' ? JSON.stringify(value) : String(value)}</span>
}

const Details = ({ item, typeName }: { item: ContactHistoryItem; typeName: (id: string) => string }) => {
    const { t } = useTranslation()
    const label = (f: string) => t(`p1.contacts.field.${f}`, { defaultValue: f })
    if (item.kind === 'update' && item.changes) {
        const entries = Object.entries(item.changes).filter(([f]) => !NOISE.has(f))
        return (
            <div className="flex flex-col gap-0.5 text-[12.5px]">
                {entries.map(([f, c]) => (
                    <div key={f} className="flex flex-wrap items-center gap-x-1.5">
                        <span className="font-extrabold">{label(f)}</span>
                        <span className="text-gray-400">:</span>
                        <span className="text-gray-600">
                            <HistoryValue field={f} value={c.before} typeName={typeName} />
                        </span>
                        <span aria-hidden className="inline-block text-gray-400 rtl:rotate-180">
                            →
                        </span>
                        <span className="text-navy-600 dark:text-gray-100">
                            <HistoryValue field={f} value={c.after} typeName={typeName} />
                        </span>
                    </div>
                ))}
            </div>
        )
    }
    const values = item.values || {}
    return (
        <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-[12.5px]">
            {VALUE_KEYS.filter((k) => k in values && k !== 'contact').map((k) => (
                <span key={k}>
                    <span className="text-gray-600">{label(k)}: </span>
                    <HistoryValue field={k} value={values[k]} typeName={typeName} />
                </span>
            ))}
        </div>
    )
}

const ContactHistoryDialog = ({
    isOpen,
    identity,
    onClose,
}: {
    isOpen: boolean
    identity: string
    onClose: () => void
}) => {
    const { t } = useTranslation()
    const [page, setPage] = useState(1)
    const types = useContactTypes()
    useEffect(() => {
        if (isOpen) setPage(1)
    }, [isOpen])
    const query = useV2Query(
        isOpen ? (signal) => historyApi.contactHistory(identity, { page, page_size: PAGE_SIZE }, signal) : null,
        [isOpen, identity, page]
    )
    const data = query.data
    const typeName = (id: string) => types.options.find((o) => o.value === id)?.label || id
    const cols = 6

    return (
        <Dialog isOpen={isOpen} width={980} onClose={onClose} onRequestClose={onClose}>
            <div data-testid="p1-contact-history" className="flex max-h-[80vh] flex-col">
                <h5 className="mb-1 pe-8 text-[17px] font-extrabold text-navy-600 dark:text-gray-100">
                    {t('p1.contacts.historyTitle')}
                </h5>
                <div className="mb-4 text-[12.5px] text-gray-600">
                    {t('p1.fields.identityNumber')} <Ltr>{identity}</Ltr>
                </div>
                <div className="min-h-0 flex-1 overflow-auto rounded-md border border-gray-300 dark:border-night-border">
                    {query.error && !data ? (
                        <ErrorState error={query.error} onRetry={query.reload} />
                    ) : (
                        <Table compact>
                            <THead>
                                <Tr>
                                    <Th>{t('p1.col.when')}</Th>
                                    <Th>{t('p1.col.event')}</Th>
                                    <Th>{t('p1.col.contact')}</Th>
                                    <Th>{t('p1.col.actor')}</Th>
                                    <Th>{t('p1.col.reason')}</Th>
                                    <Th>{t('p1.contacts.changes')}</Th>
                                </Tr>
                            </THead>
                            <TBody>
                                {query.loading && !data ? (
                                    <SkeletonRows cols={cols} rows={4} />
                                ) : data && data.items.length > 0 ? (
                                    data.items.map((item, i) => (
                                        <Tr key={`${item.occurred_at}-${i}`} data-testid="p1-contact-history-row" data-action={item.action}>
                                            <Td className="whitespace-nowrap">
                                                <RiyadhTime iso={item.occurred_at} />
                                            </Td>
                                            <Td>
                                                <div className="font-extrabold text-navy-600 dark:text-gray-100">
                                                    {t(`p1.contacts.action.${item.action.replace('.', '_')}`, {
                                                        defaultValue: item.action,
                                                    })}
                                                </div>
                                                {item.row_count > 1 && (
                                                    <PlainBadge tone="neutral" size="row">
                                                        {t('p1.contacts.rowCount', { count: item.row_count })}
                                                    </PlainBadge>
                                                )}
                                            </Td>
                                            <Td>
                                                <Ltr>{item.contact || '—'}</Ltr>
                                            </Td>
                                            <Td>
                                                <ActorName actor={item.actor} source={item.source} />
                                            </Td>
                                            <Td className="max-w-[200px]">
                                                <span dir="auto" className="break-words">
                                                    {item.reason || '—'}
                                                </span>
                                            </Td>
                                            <Td className="min-w-[220px]">
                                                <Details item={item} typeName={typeName} />
                                            </Td>
                                        </Tr>
                                    ))
                                ) : (
                                    <tr>
                                        <td colSpan={cols}>
                                            <EmptyState title={t('p1.contacts.historyEmpty')} />
                                        </td>
                                    </tr>
                                )}
                            </TBody>
                        </Table>
                    )}
                </div>
                {data && data.total > 0 && (
                    <PagerFooter page={page} pageSize={PAGE_SIZE} total={data.total} onPage={setPage} />
                )}
            </div>
        </Dialog>
    )
}

export default ContactHistoryDialog
