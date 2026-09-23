/**
 * Debtor contacts card for the case "Details" tab (replaces the legacy card).
 *
 * Reads GET /v2/debtors/<identityNumber>/contacts: one row per logical contact
 * (the legacy screen already de-duplicates by number), with default / blocked /
 * inactive badges, the needs-default warning and the duplicate-rows note. It
 * keeps everything the legacy card showed: type icon, number, created date,
 * WhatsApp link.
 *
 * - With `clints_contacts_edit`: add, edit, set default (v2, exactly one),
 *   block (a replacement default when blocking the default) and unblock.
 * - Without it: the legacy default toggle (`clints_contacts_default`).
 * - The legacy delete (`clints_contacts_delete`) stays for everyone who has it
 *   (v2 has no delete route). Both go through the callbacks the legacy screen
 *   passes.
 * - With `clints_history`: the contact history.
 * `onChanged()` runs after every mutation so the legacy contact list (used by
 * the note form) refreshes; this panel also reloads whenever that list does.
 */
import { useEffect, useRef, useState } from 'react'
import { useSelector } from 'react-redux'
import { useTranslation } from 'react-i18next'
import {
    HiBadgeCheck,
    HiOutlineBadgeCheck,
    HiOutlineBan,
    HiOutlineClock,
    HiOutlineMail,
    HiOutlinePencil,
    HiOutlinePlus,
    HiOutlineTrash,
    HiPhone,
} from 'react-icons/hi'
import { FaWhatsapp } from 'react-icons/fa'
import { TbDeviceLandlinePhone } from 'react-icons/tb'
import { LiaFaxSolid } from 'react-icons/lia'
import Button from '@/components/ui/Button'
import Tooltip from '@/components/ui/Tooltip'
import ToastNotification from '@/components/shared/myComponents/ToastNotification'
import { historyApi } from '@/services/v2'
import type { Contact, DebtorContacts } from '@/services/v2'
import { usePermissions, useV2Query } from '@/utils/v2/hooks'
import { riyadhCivilDate } from '@/utils/v2/format'
import { Ltr } from '@/components/v2/Display'
import { PlainBadge } from '@/components/v2/Badges'
import { EmptyState, ErrorState, SkeletonLines } from '@/components/v2/States'
import type { CaseInfo } from '../caseTypes'
import { Banner, buildWhatsAppLink } from './parts'
import { BlockDialog, ContactFormDialog, SetDefaultDialog, UnblockDialog } from './ContactDialogs'
import ContactHistoryDialog from './ContactHistoryDialog'
import type { ReactNode } from 'react'

export type ContactsPanelLegacy = {
    canLegacyDefault: boolean
    canLegacyDelete: boolean
    onLegacyToggleDefault: (contactRowId: string, isDefault: boolean) => Promise<void>
    onLegacyDelete: (contactRowId: string) => void
}

export type ContactsPanelProps = {
    caseData: CaseInfo
    onChanged?: () => void
    legacy: ContactsPanelLegacy
}

const typeIcon = (name: string | undefined): ReactNode => {
    switch (name) {
        case 'phone':
            return <TbDeviceLandlinePhone className="text-lg" />
        case 'email':
            return <HiOutlineMail className="text-lg" />
        case 'fax':
            return <LiaFaxSolid className="text-lg" />
        case 'mobile':
        default:
            return <HiPhone className="text-lg" />
    }
}

/** An icon button: 36px target, tooltip, test id. */
const IconAction = ({
    title,
    onClick,
    children,
    tone = 'default',
    disabled,
    testId,
}: {
    title: string
    onClick: () => void
    children: ReactNode
    tone?: 'default' | 'danger' | 'success'
    disabled?: boolean
    testId?: string
}) => (
    <Tooltip title={title}>
        <button
            type="button"
            aria-label={title}
            disabled={disabled}
            onClick={onClick}
            data-testid={testId}
            className={
                'inline-flex h-9 w-9 items-center justify-center rounded-md transition-colors disabled:cursor-not-allowed disabled:text-gray-400 ' +
                (tone === 'danger'
                    ? 'text-danger hover:bg-danger-tint'
                    : tone === 'success'
                    ? 'text-success hover:bg-success-tint'
                    : 'text-gray-600 hover:bg-gray-100 dark:hover:bg-night-raised')
            }
        >
            {children}
        </button>
    </Tooltip>
)

type LegacyState = { crmCustomerDetails?: { data?: { ContactData?: unknown } } }

const ContactsPanel = ({ caseData, onChanged, legacy }: ContactsPanelProps) => {
    const { t } = useTranslation()
    const perms = usePermissions()
    const canEdit = !!perms.clints_contacts_edit
    const canHistory = !!perms.clints_history
    const identity = (caseData.identityNumber || '').trim() ? caseData.identityNumber : ''

    const query = useV2Query(identity ? (signal) => historyApi.contacts(identity, signal) : null, [identity])
    const [local, setLocal] = useState<DebtorContacts | null>(null)
    useEffect(() => setLocal(null), [query.data])
    const data = local || query.data

    // Reload when the legacy contact list reloads (legacy delete / default, or after onChanged).
    const legacyList = useSelector((s: LegacyState) => s.crmCustomerDetails?.data?.ContactData)
    const first = useRef(true)
    useEffect(() => {
        if (first.current) {
            first.current = false
            return
        }
        query.reload()
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [legacyList])

    const [adding, setAdding] = useState(false)
    const [editing, setEditing] = useState<Contact | null>(null)
    const [defaulting, setDefaulting] = useState<Contact | null>(null)
    const [blocking, setBlocking] = useState<Contact | null>(null)
    const [unblocking, setUnblocking] = useState<Contact | null>(null)
    const [historyOpen, setHistoryOpen] = useState(false)
    const [legacyBusy, setLegacyBusy] = useState<string | null>(null)

    const done = (message: string) => (res: DebtorContacts) => {
        // The mutation answers with the whole debtor list: show it at once.
        setLocal({
            identity_number: res.identity_number,
            default_contact_id: res.default_contact_id,
            default_count: res.default_count,
            needs_default: res.needs_default,
            contacts: res.contacts,
        })
        ToastNotification(message, t('p1.contacts.title'), 'success')
        onChanged?.()
    }

    const legacyToggle = async (c: Contact) => {
        if (legacyBusy) return
        setLegacyBusy(c.id)
        try {
            await legacy.onLegacyToggleDefault(c.id, c.is_default)
        } finally {
            setLegacyBusy(null)
            query.reload()
        }
    }

    const contacts = data?.contacts || []
    const eligibleCount = contacts.filter((c) => c.eligible).length

    return (
        <div
            className="rounded-lg border border-gray-300 bg-white p-5 dark:border-night-border dark:bg-night-card"
            data-testid="p1-contacts"
        >
            <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                <h5 className="mb-0">{t('p1.contacts.title')}</h5>
                <div className="flex flex-wrap items-center gap-2">
                    {canHistory && identity && (
                        <Button
                            size="xs"
                            variant="plain"
                            icon={<HiOutlineClock />}
                            onClick={() => setHistoryOpen(true)}
                            data-testid="p1-contacts-history"
                        >
                            {t('p1.action.history')}
                        </Button>
                    )}
                    {canEdit && identity && (
                        <Button
                            size="xs"
                            icon={<HiOutlinePlus />}
                            onClick={() => setAdding(true)}
                            data-testid="p1-contacts-add"
                        >
                            {t('p1.contacts.add')}
                        </Button>
                    )}
                </div>
            </div>

            {!identity ? (
                <EmptyState title={t('p1.contacts.noIdentity')} />
            ) : query.error && !data ? (
                <ErrorState error={query.error} onRetry={query.reload} />
            ) : !data ? (
                <SkeletonLines rows={3} />
            ) : (
                <>
                    {data.needs_default && eligibleCount > 0 && (
                        <Banner tone="warning" className="mb-3" testId="p1-contacts-needs-default">
                            {canEdit ? t('p1.contacts.needsDefaultEdit') : t('p1.contacts.needsDefault')}
                        </Banner>
                    )}
                    {contacts.length === 0 ? (
                        <EmptyState title={t('p1.contacts.empty')} className="py-6" />
                    ) : (
                        <ul className="flex flex-col">
                            {contacts.map((c) => {
                                const wa = c.contact_type?.name === 'email' ? null : buildWhatsAppLink(c.contact)
                                return (
                                    <li
                                        key={c.id}
                                        className={
                                            'flex min-h-[44px] flex-wrap items-center justify-between gap-x-3 gap-y-1 border-b border-gray-200 py-1.5 transition-colors duration-150 last:border-b-0 hover:bg-gray-50 dark:border-night-border dark:hover:bg-night-raised ' +
                                            (c.is_blocked ? 'opacity-80' : '')
                                        }
                                        data-testid="p1-contact-row"
                                        data-id={c.id}
                                        data-default={c.is_default ? 'true' : 'false'}
                                        data-blocked={c.is_blocked ? 'true' : 'false'}
                                    >
                                        <div className="flex min-w-0 flex-1 items-center gap-2 text-gray-500">
                                            <span aria-hidden title={c.contact_type?.name}>
                                                {typeIcon(c.contact_type?.name)}
                                            </span>
                                            <div className="min-w-0">
                                                <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                                                    <Ltr
                                                        title={c.contact}
                                                        className={
                                                            'truncate font-extrabold text-navy-600 dark:text-gray-100 ' +
                                                            (c.is_blocked ? 'line-through decoration-gray-400' : '')
                                                        }
                                                    >
                                                        {c.contact}
                                                    </Ltr>
                                                    <span className="text-gray-400">-</span>
                                                    <Ltr className="text-[13.5px] text-gray-600">
                                                        {riyadhCivilDate(c.created_at)}
                                                    </Ltr>
                                                    {c.is_default && (
                                                        <PlainBadge tone="success" size="row">
                                                            <span data-testid="p1-contact-default-badge">
                                                                {t('p1.contacts.default')}
                                                            </span>
                                                        </PlainBadge>
                                                    )}
                                                    {c.is_blocked && (
                                                        <PlainBadge tone="danger" size="row">
                                                            <span data-testid="p1-contact-blocked-badge">
                                                                {t('p1.contacts.blocked')}
                                                            </span>
                                                        </PlainBadge>
                                                    )}
                                                    {!c.is_active && (
                                                        <PlainBadge tone="neutral" size="row">
                                                            {t('p1.contacts.inactive')}
                                                        </PlainBadge>
                                                    )}
                                                </div>
                                                {c.is_blocked && c.block_reason && (
                                                    <div className="text-[12px] text-danger" dir="auto">
                                                        {t('p1.contacts.blockedBecause')} {c.block_reason}
                                                    </div>
                                                )}
                                                {c.duplicate_rows > 1 && (
                                                    <div className="text-[12px] text-gray-500" data-testid="p1-contact-duplicates">
                                                        {t('p1.contacts.duplicateRows', { count: c.duplicate_rows })}
                                                    </div>
                                                )}
                                            </div>
                                        </div>

                                        <div className="flex items-center gap-0.5">
                                            {wa && !c.is_blocked && (
                                                <Tooltip title={t('openWhatsapp')}>
                                                    <a
                                                        href={wa}
                                                        target="_blank"
                                                        rel="noopener noreferrer"
                                                        aria-label={String(t('openWhatsapp'))}
                                                        className="inline-flex h-9 w-9 items-center justify-center rounded-md text-success hover:bg-success-tint"
                                                    >
                                                        <FaWhatsapp />
                                                    </a>
                                                </Tooltip>
                                            )}

                                            {canEdit ? (
                                                <>
                                                    {!c.is_default && c.eligible && (
                                                        <IconAction
                                                            title={t('p1.contacts.setDefault')}
                                                            onClick={() => setDefaulting(c)}
                                                            testId="p1-contact-set-default"
                                                        >
                                                            <HiOutlineBadgeCheck />
                                                        </IconAction>
                                                    )}
                                                    <IconAction
                                                        title={t('p1.contacts.edit')}
                                                        onClick={() => setEditing(c)}
                                                        testId="p1-contact-edit"
                                                    >
                                                        <HiOutlinePencil />
                                                    </IconAction>
                                                    {c.is_blocked ? (
                                                        <IconAction
                                                            title={t('p1.contacts.unblock')}
                                                            onClick={() => setUnblocking(c)}
                                                            testId="p1-contact-unblock"
                                                        >
                                                            <HiOutlineBan className="rotate-90" />
                                                        </IconAction>
                                                    ) : (
                                                        <IconAction
                                                            title={t('p1.contacts.block')}
                                                            tone="danger"
                                                            onClick={() => setBlocking(c)}
                                                            testId="p1-contact-block"
                                                        >
                                                            <HiOutlineBan />
                                                        </IconAction>
                                                    )}
                                                </>
                                            ) : (
                                                <>
                                                    {legacy.canLegacyDefault && (
                                                        <IconAction
                                                            title={
                                                                c.is_default
                                                                    ? t('clients.unsetDefaultContact')
                                                                    : t('clients.setDefaultContact')
                                                            }
                                                            tone={c.is_default ? 'success' : 'default'}
                                                            disabled={legacyBusy === c.id}
                                                            onClick={() => legacyToggle(c)}
                                                            testId="p1-contact-legacy-default"
                                                        >
                                                            {c.is_default ? <HiBadgeCheck /> : <HiOutlineBadgeCheck />}
                                                        </IconAction>
                                                    )}
                                                </>
                                            )}
                                            {/* v2 has no delete: the legacy delete stays for everyone who had it. */}
                                            {legacy.canLegacyDelete && (
                                                <IconAction
                                                    title={t('clients.deleteContact')}
                                                    tone="danger"
                                                    onClick={() => legacy.onLegacyDelete(c.id)}
                                                    testId="p1-contact-legacy-delete"
                                                >
                                                    <HiOutlineTrash />
                                                </IconAction>
                                            )}
                                        </div>
                                    </li>
                                )
                            })}
                        </ul>
                    )}
                </>
            )}

            {identity && (
                <>
                    <ContactFormDialog
                        isOpen={adding || !!editing}
                        identity={identity}
                        contact={editing}
                        onClose={() => {
                            setAdding(false)
                            setEditing(null)
                        }}
                        onReload={query.reload}
                        onDone={done(editing ? t('p1.contacts.editedToast') : t('p1.contacts.addedToast'))}
                    />
                    <SetDefaultDialog
                        contact={defaulting}
                        onClose={() => setDefaulting(null)}
                        onReload={query.reload}
                        onDone={done(t('p1.contacts.defaultToast'))}
                    />
                    <BlockDialog
                        contact={blocking}
                        contacts={contacts}
                        onClose={() => setBlocking(null)}
                        onReload={query.reload}
                        onDone={done(t('p1.contacts.blockedToast'))}
                    />
                    <UnblockDialog
                        contact={unblocking}
                        onClose={() => setUnblocking(null)}
                        onReload={query.reload}
                        onDone={done(t('p1.contacts.unblockedToast'))}
                    />
                    {canHistory && (
                        <ContactHistoryDialog
                            isOpen={historyOpen}
                            identity={identity}
                            onClose={() => setHistoryOpen(false)}
                        />
                    )}
                </>
            )}
        </div>
    )
}

export default ContactsPanel
