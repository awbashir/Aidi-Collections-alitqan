/**
 * Contact mutations (key `clints_contacts_edit`): add, edit, set default,
 * block (with a replacement default when the default is blocked) and unblock.
 * Each dialog opening is one action with one Idempotency-Key; edits send the
 * revision they loaded.
 */
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import Checkbox from '@/components/ui/Checkbox'
import Input from '@/components/ui/Input'
import Select from '@/components/ui/Select'
import { historyApi, isV2Error, lookups } from '@/services/v2'
import type { Contact, DebtorContacts, Option } from '@/services/v2'
import { Ltr } from '@/components/v2/Display'
import ActionDialog, { DialogField, ReasonDialog } from '@/components/v2/ActionDialog'
import { Banner } from './parts'

export function useContactTypes() {
    const [options, setOptions] = useState<Option[]>([])
    const [loading, setLoading] = useState(true)
    useEffect(() => {
        let alive = true
        lookups
            .contactTypes()
            .then((o) => alive && setOptions(o))
            .catch(() => alive && setOptions([]))
            .finally(() => alive && setLoading(false))
        return () => {
            alive = false
        }
    }, [])
    return { options, loading }
}

/** Optional reason field shared by add / edit / default. */
const OptionalReason = ({
    value,
    onChange,
    testId,
}: {
    value: string
    onChange: (v: string) => void
    testId?: string
}) => {
    const { t } = useTranslation()
    return (
        <DialogField label={t('p1.contacts.reasonOptional')}>
            <Input textArea value={value} maxLength={2000} onChange={(e) => onChange(e.target.value)} data-testid={testId} />
        </DialogField>
    )
}

export const ContactFormDialog = ({
    isOpen,
    identity,
    contact,
    onClose,
    onDone,
    onReload,
}: {
    isOpen: boolean
    identity: string
    /** Edit this contact; add a new one when absent. */
    contact?: Contact | null
    onClose: () => void
    onDone: (res: DebtorContacts) => void
    onReload: () => void
}) => {
    const { t } = useTranslation()
    const types = useContactTypes()
    const [typeId, setTypeId] = useState<string>('')
    const [number, setNumber] = useState('')
    const [makeDefault, setMakeDefault] = useState(false)
    const [reason, setReason] = useState('')
    const editing = !!contact

    useEffect(() => {
        if (!isOpen) return
        setTypeId(contact?.contact_type?.id || '')
        setNumber(contact?.contact || '')
        setMakeDefault(false)
        setReason('')
    }, [isOpen, contact])

    const typeChanged = editing && !!typeId && typeId !== contact?.contact_type?.id
    const numberChanged = editing && number.trim() !== (contact?.contact || '').trim()
    const canSubmit = editing ? !!number.trim() && (typeChanged || numberChanged) : !!typeId && !!number.trim()
    const testId = editing ? 'p1-contact-edit-dialog' : 'p1-contact-add-dialog'

    return (
        <ActionDialog
            isOpen={isOpen}
            title={editing ? t('p1.contacts.editTitle') : t('p1.contacts.addTitle')}
            confirmLabel={editing ? t('p1.action.save') : t('p1.contacts.add')}
            canSubmit={canSubmit}
            testId={testId}
            onClose={onClose}
            onReload={onReload}
            onSubmit={async (key) => {
                const r = reason.trim() || undefined
                const res = contact
                    ? await historyApi.editContact(
                          contact.id,
                          {
                              contact: numberChanged ? number.trim() : undefined,
                              contact_type_id: typeChanged ? typeId : undefined,
                              reason: r,
                              expected_revision: contact.revision,
                          },
                          key
                      )
                    : await historyApi.addContact(
                          identity,
                          { contact_type_id: typeId, contact: number.trim(), make_default: makeDefault, reason: r },
                          key
                      )
                onDone(res)
            }}
        >
            {editing && contact && contact.duplicate_rows > 1 && (
                <Banner tone="info" className="mb-4">
                    {t('p1.contacts.editAllCopies', { count: contact.duplicate_rows })}
                </Banner>
            )}
            <DialogField label={t('p1.fields.contact_type_id')} required>
                <div data-testid={`${testId}-type`}>
                    <Select
                        size="sm"
                        isLoading={types.loading}
                        options={types.options}
                        value={types.options.find((o) => o.value === typeId) || null}
                        onChange={(o) => o && setTypeId((o as Option).value)}
                    />
                </div>
            </DialogField>
            <DialogField label={t('p1.fields.contact')} required hint={t('p1.contacts.numberHint')}>
                <Input
                    size="sm"
                    dir="ltr"
                    value={number}
                    maxLength={200}
                    onChange={(e) => setNumber(e.target.value)}
                    data-testid={`${testId}-number`}
                />
            </DialogField>
            {!editing && (
                <div className="mb-4">
                    <Checkbox checked={makeDefault} onChange={(v) => setMakeDefault(v)} data-testid={`${testId}-default`}>
                        {t('p1.contacts.makeDefault')}
                    </Checkbox>
                    <p className="mt-1 text-[12px] text-gray-600">{t('p1.contacts.firstBecomesDefault')}</p>
                </div>
            )}
            <OptionalReason value={reason} onChange={setReason} testId={`${testId}-reason`} />
        </ActionDialog>
    )
}

export const SetDefaultDialog = ({
    contact,
    onClose,
    onDone,
    onReload,
}: {
    contact: Contact | null
    onClose: () => void
    onDone: (res: DebtorContacts) => void
    onReload: () => void
}) => {
    const { t } = useTranslation()
    const [reason, setReason] = useState('')
    useEffect(() => setReason(''), [contact])
    return (
        <ActionDialog
            isOpen={!!contact}
            title={t('p1.contacts.setDefaultTitle')}
            confirmLabel={t('p1.contacts.setDefault')}
            testId="p1-contact-default-dialog"
            onClose={onClose}
            onReload={onReload}
            onSubmit={async (key) => {
                if (!contact) return
                const res = await historyApi.setDefault(
                    contact.id,
                    { expected_revision: contact.revision, reason: reason.trim() || undefined },
                    key
                )
                onDone(res)
            }}
        >
            <p className="mb-4 text-[13.5px] text-gray-700 dark:text-night-body">
                {t('p1.contacts.setDefaultHint')} <Ltr className="font-extrabold">{contact?.contact}</Ltr>
            </p>
            <OptionalReason value={reason} onChange={setReason} testId="p1-contact-default-dialog-reason" />
        </ActionDialog>
    )
}

export const BlockDialog = ({
    contact,
    contacts,
    onClose,
    onDone,
    onReload,
}: {
    contact: Contact | null
    /** Every contact of the debtor, for the replacement default. */
    contacts: Contact[]
    onClose: () => void
    onDone: (res: DebtorContacts) => void
    onReload: () => void
}) => {
    const { t } = useTranslation()
    const [replacement, setReplacement] = useState<string>('')
    /** Candidate ids from a 422 replacement_default_required, when the list was stale. */
    const [serverCandidates, setServerCandidates] = useState<string[] | null>(null)

    useEffect(() => {
        setReplacement('')
        setServerCandidates(null)
    }, [contact])

    const localCandidates = contacts.filter((c) => contact && c.id !== contact.id && c.eligible && !c.is_blocked)
    const candidateIds = serverCandidates || localCandidates.map((c) => c.id)
    const needsReplacement = !!contact && (contact.is_default || !!serverCandidates) && candidateIds.length > 0
    const options = candidateIds.map((id) => {
        const c = contacts.find((x) => x.id === id)
        return { value: id, label: c ? `${c.contact}${c.contact_type ? ` · ${c.contact_type.name}` : ''}` : id }
    })

    return (
        <ReasonDialog
            isOpen={!!contact}
            title={t('p1.contacts.blockTitle')}
            description={
                <>
                    {t('p1.contacts.blockHint')} <Ltr className="font-extrabold">{contact?.contact}</Ltr>
                </>
            }
            confirmLabel={t('p1.contacts.block')}
            tone="danger"
            testId="p1-contact-block-dialog"
            extraValid={!needsReplacement || !!replacement}
            onClose={onClose}
            onReload={onReload}
            extra={
                needsReplacement ? (
                    <DialogField
                        label={t('p1.fields.replacement_default_id')}
                        required
                        hint={t('p1.contacts.replacementHint')}
                    >
                        <div data-testid="p1-contact-block-dialog-replacement">
                            <Select
                                size="sm"
                                options={options}
                                value={options.find((o) => o.value === replacement) || null}
                                onChange={(o) => o && setReplacement((o as Option).value)}
                            />
                        </div>
                    </DialogField>
                ) : contact?.is_default ? (
                    <Banner tone="warning" className="mb-4">
                        {t('p1.contacts.blockLastDefault')}
                    </Banner>
                ) : null
            }
            onSubmit={async (reason, key) => {
                if (!contact) return
                try {
                    const res = await historyApi.block(
                        contact.id,
                        {
                            reason,
                            expected_revision: contact.revision,
                            replacement_default_id: needsReplacement ? replacement : undefined,
                        },
                        key
                    )
                    onDone(res)
                } catch (err) {
                    if (isV2Error(err) && err.code === 'replacement_default_required') {
                        const d = err.detail as { candidates?: string[] } | null
                        setServerCandidates(d?.candidates || [])
                    }
                    throw err
                }
            }}
        />
    )
}

export const UnblockDialog = ({
    contact,
    onClose,
    onDone,
    onReload,
}: {
    contact: Contact | null
    onClose: () => void
    onDone: (res: DebtorContacts) => void
    onReload: () => void
}) => {
    const { t } = useTranslation()
    return (
        <ReasonDialog
            isOpen={!!contact}
            title={t('p1.contacts.unblockTitle')}
            description={
                <>
                    {t('p1.contacts.unblockHint')} <Ltr className="font-extrabold">{contact?.contact}</Ltr>
                    {contact?.block_reason ? (
                        <div className="mt-2 text-[12.5px] text-gray-600">
                            {t('p1.contacts.blockedBecause')} <span dir="auto">{contact.block_reason}</span>
                        </div>
                    ) : null}
                </>
            }
            confirmLabel={t('p1.contacts.unblock')}
            testId="p1-contact-unblock-dialog"
            onClose={onClose}
            onReload={onReload}
            onSubmit={async (reason, key) => {
                if (!contact) return
                const res = await historyApi.unblock(contact.id, { reason, expected_revision: contact.revision }, key)
                onDone(res)
            }}
        />
    )
}
