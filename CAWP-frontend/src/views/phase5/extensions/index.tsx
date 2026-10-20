/**
 * Extensions management ("التحويلات"; `/calling-extensions`, key `calling_extensions` = calls_manage;
 * Intelligence & control). Customer request 2026-10-20: create and edit the agents' extensions,
 * and create one for every new user.
 *
 * - One list of every extension the PBX or the app knows (`GET /v2/calling/pbx-extensions`): the
 *   PBX state (exists, registered, in a call; "unknown" when the PBX could not be read, never
 *   "absent"), the app owner (`user_extensions`) and the provisioning request, if any.
 * - Create on the PBX (number optional: the next free number of the range), optionally assigned
 *   to a user; rename (PBX name); delete (unassigned, created by the app); retry a pending /
 *   failed request. A create while the PBX is down is kept as "pending" and retried from here.
 * - Assign / release reuse the ownership routes of Calling admin (reason, expected owner).
 * - "Users without an extension" with one-click create + assign, or assign an existing one.
 * - Settings: auto-create for new users (on by default) and the allocation range.
 */
import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { HiOutlinePlus } from 'react-icons/hi'
import Button from '@/components/ui/Button'
import Checkbox from '@/components/ui/Checkbox'
import Input from '@/components/ui/Input'
import Select from '@/components/ui/Select'
import Table from '@/components/ui/Table'
import StatusPill from '@/components/shared/StatusPill'
import type { StatusTone } from '@/components/shared/StatusPill'
import ActionDialog, { DialogField, ReasonDialog } from '@/components/v2/ActionDialog'
import { Ltr, RiyadhTime } from '@/components/v2/Display'
import { EmptyState, ErrorState, Note, SkeletonLines, SkeletonRows } from '@/components/v2/States'
import { CountTabs, PageHead, TableFrame } from '@/components/v2/ListParts'
import ToastNotification from '@/components/shared/myComponents/ToastNotification'
import useHashState, { hashCodec } from '@/utils/hooks/useHashState'
import type { HashSchema } from '@/utils/hooks/useHashState'
import { callingAdminApi, describeP5Error, isV2Error, newIdempotencyKey } from '@/services/v2'
import type { Option, Translate } from '@/services/v2'
import { extProvisionApi } from '@/services/v2/extProvisionApi'
import type { ExtSettings, ExtStatus, ExtView, ManagedExtension, ProvisionRequest, UserWithoutExtension } from '@/services/v2/extProvisionApi'
import { useV2Query } from '@/utils/v2/hooks'
import { formatCount } from '@/utils/v2/format'
import { Card, Fact, Notice } from '../parts'

const { THead, TBody, Tr, Th, Td } = Table
const VIEWS: ExtView[] = ['all', 'unassigned', 'assigned', 'attention']
type HashState = { view: ExtView; q: string | undefined }
const schema: HashSchema<HashState> = {
    view: { key: 'view', codec: hashCodec.oneOf(VIEWS), default: 'all' },
    q: { key: 'q', codec: hashCodec.text({ maxLength: 100 }), default: undefined, history: 'replace' },
}
const COLS = 6

/** Our own error codes first (p10.extprov.err.*), then the calling ones. */
const OWN_CODES = new Set([
    'pbx_extension_exists',
    'pbx_provision_disabled',
    'pbx_unavailable',
    'pbx_not_managed',
    'pbx_in_call',
    'pbx_in_ring_group',
    'pbx_extension_not_found',
    'pbx_busy',
    'pbx_numbers_taken',
    'provisioning_pending',
    'provisioning_not_retryable',
    'provisioning_in_progress',
    'provisioning_not_found',
    'range_exhausted',
])
export const describeExtError = (e: unknown, t: Translate): string => {
    if (isV2Error(e)) {
        if (OWN_CODES.has(e.code)) return t(`p10.extprov.err.${e.code}`)
        if (e.code.startsWith('pbx_')) return t('p10.extprov.err.pbx_other', { message: e.serverMessage || e.code })
    }
    return describeP5Error(e, t)
}

const STATUS_TONE: Record<ExtStatus, StatusTone> = {
    assigned: 'success',
    unassigned: 'warning',
    pending: 'info',
    provisioning: 'info',
    failed: 'danger',
    missing_on_pbx: 'danger',
    unknown: 'neutral',
}

const StatusBadge = ({ status }: { status: ExtStatus }) => {
    const { t } = useTranslation()
    return (
        <StatusPill tone={STATUS_TONE[status]} size="row">
            <span data-ext-status={status}>{t(`p10.extprov.status.${status}`)}</span>
        </StatusPill>
    )
}

/** Registered / in a call / offline / not on the PBX / unknown (the PBX could not be read). */
const PbxBadge = ({ x }: { x: ManagedExtension }) => {
    const { t } = useTranslation()
    let tone: StatusTone = 'neutral'
    let key = 'unknown'
    if (x.on_pbx === false) {
        tone = 'danger'
        key = 'absent'
    } else if (x.pbx) {
        if (x.pbx.in_call) {
            tone = 'info'
            key = 'inCall'
        } else if (x.pbx.registered) {
            tone = 'success'
            key = 'registered'
        } else if (x.pbx.registered === false) {
            key = 'offline'
        }
    }
    return (
        <span className="flex flex-col items-start gap-0.5">
            <StatusPill tone={tone} size="row">
                <span data-pbx-state={key}>{t(`p10.extprov.pbx.${key}`)}</span>
            </StatusPill>
            {x.pbx?.managed && <span className="text-[11.5px] text-gray-500">{t('p10.extprov.managed')}</span>}
        </span>
    )
}

/** The provisioning request in one line: state, attempts, last error. */
const RequestCell = ({ r }: { r: ProvisionRequest | null }) => {
    const { t } = useTranslation()
    if (!r) return <>—</>
    return (
        <div className="text-[12.5px]">
            <div>
                {t(`p10.extprov.req.${r.stale ? 'stale' : r.state}`)} · {t('p10.extprov.attempts', { count: r.attempts })}
            </div>
            {r.for_user && r.state !== 'active' && <div className="text-gray-600">{t('p10.extprov.forUser', { name: r.for_user.name || r.for_user.user_name || '—' })}</div>}
            {r.error_code && (
                <div className="text-danger" dir="auto" title={r.error_message || undefined}>
                    {r.error_code}
                    {r.error_message ? `: ${r.error_message}` : ''}
                </div>
            )}
            {r.assign_error && <div className="text-warning" dir="auto">{t('p10.extprov.assignError', { error: r.assign_error })}</div>}
            {r.last_attempt_at && (
                <div className="text-gray-500">
                    <RiyadhTime iso={r.last_attempt_at} />
                </div>
            )}
        </div>
    )
}

/** What a create / retry answer means for the admin (toast). */
function announce(t: Translate, r: ProvisionRequest) {
    const title = String(t('p10.extprov.title'))
    if (r.state === 'active') {
        const assigned = r.assignment?.assigned
        ToastNotification(
            String(t(assigned ? 'p10.extprov.toast.createdAssigned' : 'p10.extprov.toast.created', { ext: r.extension, name: r.for_user?.name || '' })),
            title,
            'success',
            5000
        )
        if (r.assignment && !r.assignment.assigned) {
            ToastNotification(String(t('p10.extprov.toast.assignFailed', { ext: r.extension, error: r.assignment.message || r.assignment.code || '' })), title, 'warning', 8000)
        }
    } else if (r.state === 'pending') {
        ToastNotification(String(t('p10.extprov.toast.pending', { ext: r.extension })), title, 'warning', 8000)
    } else {
        ToastNotification(String(t('p10.extprov.toast.failed', { ext: r.extension, error: r.error_code || '' })), title, 'danger', 8000)
    }
    if (r.reallocated_from && r.reallocated_from.length) {
        ToastNotification(String(t('p10.extprov.toast.reallocated', { from: r.reallocated_from.join(', '), ext: r.extension })), title, 'info', 8000)
    }
}

/* ------------------------------------------------------------------ user picker */

/** Active users without an extension (`GET /v2/calling/pbx-extensions/users-without?q=`). */
function useUsersWithout(q: string, enabled: boolean) {
    const [users, setUsers] = useState<UserWithoutExtension[]>([])
    useEffect(() => {
        if (!enabled) return undefined
        let alive = true
        const id = setTimeout(() => {
            extProvisionApi
                .usersWithout({ q: q || undefined })
                .then((r) => alive && setUsers(r.items))
                .catch(() => alive && setUsers([]))
        }, 250)
        return () => {
            alive = false
            clearTimeout(id)
        }
    }, [q, enabled])
    return users
}

const userLabel = (u: UserWithoutExtension) => `${u.user.name || u.user.user_name} (${u.user.user_name})${u.request ? ` · ${u.request.extension}…` : ''}`

const UserPicker = ({ isOpen, value, onChange, preset }: { isOpen: boolean; value: string | undefined; onChange: (v: string | undefined) => void; preset?: UserWithoutExtension | null }) => {
    const { t } = useTranslation()
    const [search, setSearch] = useState('')
    useEffect(() => {
        if (isOpen) setSearch('')
    }, [isOpen])
    const users = useUsersWithout(search, isOpen)
    const all = preset && !users.some((u) => u.user.id === preset.user.id) ? [preset, ...users] : users
    const options: Option[] = all.map((u) => ({ value: u.user.id, label: userLabel(u) }))
    return (
        <>
            {/* Two separate fields: a <label> around both would send every click to the search box. */}
            <DialogField label={t('p10.extprov.searchUser')}>
                <Input size="sm" value={search} onChange={(e) => setSearch(e.target.value)} data-testid="p10-ext-user-search" />
            </DialogField>
            <DialogField label={t('p10.extprov.user')} hint={t('p10.extprov.userHint')}>
                <div data-testid="p10-ext-user">
                    <Select
                        size="sm"
                        menuPosition="fixed"
                        isClearable
                        placeholder={t('p1.followUps.select')}
                        noOptionsMessage={() => t('p10.extprov.noUsers')}
                        options={options}
                        value={options.find((o) => o.value === value) || null}
                        onChange={(o) => onChange((o as Option | null)?.value || undefined)}
                    />
                </div>
            </DialogField>
        </>
    )
}

/* ------------------------------------------------------------------ dialogs */

const CreateDialog = ({ isOpen, preset, settings, onClose, onDone }: { isOpen: boolean; preset: UserWithoutExtension | null; settings: ExtSettings | undefined; onClose: () => void; onDone: () => void }) => {
    const { t } = useTranslation()
    const [ext, setExt] = useState('')
    const [name, setName] = useState('')
    const [userId, setUserId] = useState<string | undefined>(undefined)
    const [reason, setReason] = useState('')
    useEffect(() => {
        if (isOpen) {
            setExt('')
            setName('')
            setUserId(preset?.user.id)
            setReason(preset ? String(t('p10.extprov.defaultReason')) : '')
        }
    }, [isOpen, preset, t])
    const extOk = ext === '' || /^[1-9]\d{1,5}$/.test(ext)
    return (
        <ActionDialog
            isOpen={isOpen}
            title={preset ? t('p10.extprov.createForTitle', { name: preset.user.name || preset.user.user_name }) : t('p10.extprov.createTitle')}
            confirmLabel={t('p10.extprov.create')}
            canSubmit={extOk && (!!userId || name.trim().length > 0) && reason.trim().length > 0}
            testId="p10-ext-create"
            describeError={describeExtError}
            onClose={onClose}
            onSubmit={async (key) => {
                const res = await extProvisionApi.create(
                    { ...(ext ? { extension: ext } : {}), ...(name.trim() ? { name: name.trim() } : {}), ...(userId ? { user_id: userId } : {}), reason: reason.trim() },
                    key
                )
                announce(t, res)
                onDone()
            }}
        >
            <DialogField
                label={t('p10.extprov.number')}
                hint={settings ? t('p10.extprov.numberHint', { from: settings.range_from, to: settings.range_to }) : undefined}
            >
                <Input size="sm" dir="ltr" inputMode="numeric" value={ext} invalid={!extOk} onChange={(e) => setExt(e.target.value.replace(/[^0-9]/g, '').slice(0, 6))} data-testid="p10-ext-create-number" />
            </DialogField>
            {!preset && <UserPicker isOpen={isOpen} value={userId} onChange={setUserId} />}
            <DialogField label={t('p10.extprov.name')} hint={t('p10.extprov.nameHint')}>
                <Input size="sm" value={name} maxLength={40} onChange={(e) => setName(e.target.value)} data-testid="p10-ext-create-name" />
            </DialogField>
            <DialogField label={t('p1.fields.reason')} required hint={t('p1.hint.reasonRequired')}>
                <Input textArea value={reason} maxLength={1000} onChange={(e) => setReason(e.target.value)} data-testid="p10-ext-create-reason" />
            </DialogField>
        </ActionDialog>
    )
}

/** Assign an extension that exists on the PBX to a user (the Calling admin ownership route). */
const AssignDialog = ({
    isOpen,
    ext: presetExt,
    user: presetUser,
    unassigned,
    onClose,
    onDone,
}: {
    isOpen: boolean
    ext: string | null
    user: UserWithoutExtension | null
    unassigned: ManagedExtension[]
    onClose: () => void
    onDone: () => void
}) => {
    const { t } = useTranslation()
    const [ext, setExt] = useState<string | undefined>(undefined)
    const [userId, setUserId] = useState<string | undefined>(undefined)
    const [reason, setReason] = useState('')
    useEffect(() => {
        if (isOpen) {
            setExt(presetExt || undefined)
            setUserId(presetUser?.user.id)
            setReason('')
        }
    }, [isOpen, presetExt, presetUser])
    const extOptions: Option[] = unassigned.map((x) => ({ value: x.extension, label: `${x.extension}${x.name ? ` · ${x.name}` : ''}` }))
    return (
        <ActionDialog
            isOpen={isOpen}
            title={t('p10.extprov.assignTitle')}
            confirmLabel={t('p10.extprov.assign')}
            canSubmit={!!ext && !!userId && reason.trim().length > 0}
            testId="p10-ext-assign"
            describeError={describeExtError}
            onClose={onClose}
            onSubmit={async (key) => {
                if (!ext || !userId) return
                await callingAdminApi.assign(ext, { user_id: userId, reason: reason.trim(), expected_owner_id: null }, key)
                ToastNotification(String(t('p10.extprov.toast.assigned', { ext })), String(t('p10.extprov.title')), 'success', 4000)
                onDone()
            }}
        >
            <DialogField label={t('p10.extprov.extension')} required>
                <div data-testid="p10-ext-assign-ext">
                    <Select
                        size="sm"
                        menuPosition="fixed"
                        placeholder={t('p1.followUps.select')}
                        noOptionsMessage={() => t('p10.extprov.noUnassigned')}
                        options={extOptions}
                        isDisabled={!!presetExt}
                        value={extOptions.find((o) => o.value === ext) || (ext ? { value: ext, label: ext } : null)}
                        onChange={(o) => setExt((o as Option | null)?.value || undefined)}
                    />
                </div>
            </DialogField>
            <UserPicker isOpen={isOpen} value={userId} onChange={setUserId} preset={presetUser} />
            <DialogField label={t('p1.fields.reason')} required hint={t('p1.hint.reasonRequired')}>
                <Input textArea value={reason} maxLength={1000} onChange={(e) => setReason(e.target.value)} data-testid="p10-ext-assign-reason" />
            </DialogField>
        </ActionDialog>
    )
}

const RenameDialog = ({ x, onClose, onDone }: { x: ManagedExtension | null; onClose: () => void; onDone: () => void }) => {
    const { t } = useTranslation()
    const [name, setName] = useState('')
    const [reason, setReason] = useState('')
    useEffect(() => {
        if (x) {
            setName(x.name || '')
            setReason('')
        }
    }, [x])
    return (
        <ActionDialog
            isOpen={!!x}
            title={t('p10.extprov.renameTitle', { ext: x?.extension || '' })}
            confirmLabel={t('p10.extprov.rename')}
            canSubmit={name.trim().length > 0 && reason.trim().length > 0}
            testId="p10-ext-rename"
            describeError={describeExtError}
            onClose={onClose}
            onSubmit={async (key) => {
                if (!x) return
                await extProvisionApi.rename(x.extension, { name: name.trim(), reason: reason.trim() }, key)
                onDone()
            }}
        >
            <DialogField label={t('p10.extprov.name')} required hint={t('p10.extprov.nameHint')}>
                <Input size="sm" value={name} maxLength={40} onChange={(e) => setName(e.target.value)} data-testid="p10-ext-rename-name" />
            </DialogField>
            <DialogField label={t('p1.fields.reason')} required hint={t('p1.hint.reasonRequired')}>
                <Input textArea value={reason} maxLength={1000} onChange={(e) => setReason(e.target.value)} data-testid="p10-ext-rename-reason" />
            </DialogField>
        </ActionDialog>
    )
}

const SettingsDialog = ({ settings, isOpen, onClose, onDone }: { settings: ExtSettings | undefined; isOpen: boolean; onClose: () => void; onDone: () => void }) => {
    const { t } = useTranslation()
    const [auto, setAuto] = useState(true)
    const [from, setFrom] = useState('')
    const [to, setTo] = useState('')
    const [reason, setReason] = useState('')
    useEffect(() => {
        if (isOpen && settings) {
            setAuto(settings.auto_provision)
            setFrom(String(settings.range_from))
            setTo(String(settings.range_to))
            setReason('')
        }
    }, [isOpen, settings])
    const lo = Number(from)
    const hi = Number(to)
    const rangeOk = /^\d{2,6}$/.test(from) && /^\d{2,6}$/.test(to) && hi >= lo && hi - lo <= 10000
    return (
        <ActionDialog
            isOpen={isOpen}
            title={t('p10.extprov.settingsTitle')}
            confirmLabel={t('p1.action.save')}
            canSubmit={!!settings && rangeOk && reason.trim().length > 0}
            testId="p10-ext-settings"
            describeError={describeExtError}
            onReload={onDone}
            onClose={onClose}
            onSubmit={async (key) => {
                if (!settings) return
                await extProvisionApi.saveSettings({ auto_provision: auto, range_from: lo, range_to: hi, expected_revision: settings.revision, reason: reason.trim() }, key)
                onDone()
            }}
        >
            <div className="mb-3">
                <Checkbox checked={auto} onChange={(c: boolean) => setAuto(c)} data-testid="p10-ext-settings-auto">
                    {t('p10.extprov.autoProvision')}
                </Checkbox>
                <div className="mt-1 text-[12px] text-gray-600">{t('p10.extprov.autoProvisionHint')}</div>
            </div>
            <div className="grid grid-cols-2 gap-3">
                <DialogField label={t('p10.extprov.rangeFrom')} required>
                    <Input size="sm" dir="ltr" inputMode="numeric" value={from} onChange={(e) => setFrom(e.target.value.replace(/[^0-9]/g, '').slice(0, 6))} data-testid="p10-ext-settings-from" />
                </DialogField>
                <DialogField label={t('p10.extprov.rangeTo')} required>
                    <Input size="sm" dir="ltr" inputMode="numeric" value={to} onChange={(e) => setTo(e.target.value.replace(/[^0-9]/g, '').slice(0, 6))} data-testid="p10-ext-settings-to" />
                </DialogField>
            </div>
            <DialogField label={t('p1.fields.reason')} required hint={t('p1.hint.reasonRequired')}>
                <Input textArea value={reason} maxLength={1000} onChange={(e) => setReason(e.target.value)} data-testid="p10-ext-settings-reason" />
            </DialogField>
        </ActionDialog>
    )
}

/* ------------------------------------------------------------------ users without an extension */

const UsersWithout = ({ reloadKey, canCreate, onCreate, onAssign }: { reloadKey: number; canCreate: boolean; onCreate: (u: UserWithoutExtension) => void; onAssign: (u: UserWithoutExtension) => void }) => {
    const { t } = useTranslation()
    const q = useV2Query((signal) => extProvisionApi.usersWithout({}, signal), [reloadKey])
    const data = q.data
    return (
        <Card title={t('p10.extprov.usersWithout', { count: data?.total ?? 0 })} testId="p10-ext-users-without" actions={<Button size="xs" onClick={q.reload}>{t('p1.action.refresh')}</Button>}>
            {q.error && !data ? (
                <ErrorState error={q.error} onRetry={q.reload} describe={describeExtError} />
            ) : q.loading && !data ? (
                <SkeletonLines rows={3} />
            ) : data && data.items.length ? (
                <Table>
                    <THead>
                        <Tr>
                            <Th>{t('p10.extprov.user')}</Th>
                            <Th>{t('p10.extprov.role')}</Th>
                            <Th>{t('p10.extprov.legacy')}</Th>
                            <Th>{t('p10.extprov.request')}</Th>
                            <Th />
                        </Tr>
                    </THead>
                    <TBody>
                        {data.items.map((u) => (
                            <Tr key={u.user.id} data-testid="p10-ext-user-row" data-user={u.user.id}>
                                <Td>
                                    <div className="font-extrabold">{u.user.name || '—'}</div>
                                    <Ltr className="text-[12px] text-gray-500">{u.user.user_name}</Ltr>
                                </Td>
                                <Td className="text-[12.5px]">{u.role || '—'}</Td>
                                <Td>{u.legacy_extension ? <Ltr>{u.legacy_extension}</Ltr> : '—'}</Td>
                                <Td>
                                    {u.request ? (
                                        <span className="text-[12.5px]">
                                            <Ltr className="font-extrabold">{u.request.extension}</Ltr> · {t(`p10.extprov.req.${u.request.stale ? 'stale' : u.request.state}`)}
                                        </span>
                                    ) : (
                                        '—'
                                    )}
                                </Td>
                                <Td>
                                    <span className="flex flex-wrap gap-1">
                                        {!u.request && canCreate && (
                                            <Button size="xs" variant="solid" onClick={() => onCreate(u)} data-testid="p10-ext-user-create">
                                                {t('p10.extprov.createAssign')}
                                            </Button>
                                        )}
                                        <Button size="xs" onClick={() => onAssign(u)} data-testid="p10-ext-user-assign">
                                            {t('p10.extprov.assignExisting')}
                                        </Button>
                                    </span>
                                </Td>
                            </Tr>
                        ))}
                    </TBody>
                </Table>
            ) : (
                <EmptyState title={t('p10.extprov.allHaveExtensions')} />
            )}
        </Card>
    )
}

/* ------------------------------------------------------------------ page */

const ExtensionsPage = () => {
    const { t } = useTranslation()
    const { state, setState } = useHashState(schema)
    const [search, setSearch] = useState(state.q || '')
    useEffect(() => setSearch(state.q || ''), [state.q])
    useEffect(() => {
        const next = search.trim() || undefined
        if (next === (state.q || undefined)) return undefined
        const id = window.setTimeout(() => setState({ q: next }), 350)
        return () => window.clearTimeout(id)
    }, [search, state.q, setState])
    const all = useV2Query((signal) => extProvisionApi.overview({ q: state.q }, signal), [state.q])
    const [reloadKey, setReloadKey] = useState(0)
    const reload = () => {
        all.reload()
        setReloadKey((k) => k + 1)
    }
    const [creating, setCreating] = useState<{ preset: UserWithoutExtension | null } | null>(null)
    const [assigning, setAssigning] = useState<{ ext: string | null; user: UserWithoutExtension | null } | null>(null)
    const [renaming, setRenaming] = useState<ManagedExtension | null>(null)
    const [releasing, setReleasing] = useState<ManagedExtension | null>(null)
    const [deleting, setDeleting] = useState<ManagedExtension | null>(null)
    const [settingsOpen, setSettingsOpen] = useState(false)
    const [retrying, setRetrying] = useState<string | null>(null)
    const data = all.data
    const settings = data?.settings
    const items = useMemo(() => {
        const list = data?.items || []
        if (state.view === 'attention') return list.filter((i) => ['pending', 'provisioning', 'failed', 'missing_on_pbx'].includes(i.status))
        if (state.view === 'all') return list
        return list.filter((i) => i.status === state.view)
    }, [data, state.view])
    const unassigned = useMemo(() => (data?.items || []).filter((i) => i.status === 'unassigned'), [data])
    const c = data?.counts
    const pbxDown = data?.pbx_status === 'unavailable'
    const canCreate = data?.pbx_status !== 'disabled'

    const retry = async (x: ManagedExtension) => {
        if (retrying) return
        setRetrying(x.extension)
        try {
            const res = await extProvisionApi.retry(x.extension, newIdempotencyKey())
            announce(t, res)
        } catch (e) {
            ToastNotification(describeExtError(e, t), String(t('p10.extprov.title')), 'danger', 8000)
        } finally {
            setRetrying(null)
            reload()
        }
    }

    return (
        <div data-testid="p10-extensions" data-pbx={data?.pbx_status || ''}>
            <PageHead
                title={t('p10.extprov.title')}
                subtitle={c ? t('p10.extprov.subtitle', { total: formatCount(c.total), assigned: formatCount(c.assigned), unassigned: formatCount(c.unassigned), registered: formatCount(c.registered) }) : undefined}
                onRefresh={reload}
                refreshing={all.loading}
                actions={
                    <Button size="sm" variant="solid" icon={<HiOutlinePlus />} disabled={!canCreate} onClick={() => setCreating({ preset: null })} data-testid="p10-ext-create-open">
                        {t('p10.extprov.create')}
                    </Button>
                }
            />
            {data?.pbx_status === 'disabled' && (
                <Notice tone="warning" className="mb-3" testId="p10-ext-disabled">
                    {t('p10.extprov.disabled')}
                </Notice>
            )}
            {pbxDown && (
                <Notice tone="warning" className="mb-3" testId="p10-ext-pbx-down">
                    {t('p10.extprov.pbxDown')}
                </Notice>
            )}
            {data && data.asterisk === false && (
                <Notice tone="warning" className="mb-3">
                    {t('p10.extprov.asteriskDown')}
                </Notice>
            )}

            <Card
                className="mb-4"
                title={t('p10.extprov.settings')}
                testId="p10-ext-settings-card"
                actions={
                    <Button size="xs" disabled={!settings} onClick={() => setSettingsOpen(true)} data-testid="p10-ext-settings-open">
                        {t('p10.extprov.edit')}
                    </Button>
                }
            >
                {settings ? (
                    <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
                        <Fact label={t('p10.extprov.autoProvision')} testId="p10-ext-auto">
                            <span className={settings.auto_effective ? 'font-extrabold text-success' : 'font-extrabold text-gray-600'}>
                                {settings.auto_effective ? t('p10.extprov.on') : t('p10.extprov.off')}
                            </span>
                            {settings.auto_provision && !settings.configured && <div className="text-[12px] text-warning">{t('p10.extprov.notConfigured')}</div>}
                        </Fact>
                        <Fact label={t('p10.extprov.range')}>
                            <Ltr>
                                {settings.range_from}–{settings.range_to}
                            </Ltr>
                        </Fact>
                        <Fact label={t('p10.extprov.inCall')}>{c ? formatCount(c.in_call) : '—'}</Fact>
                        <Fact label={t('p10.extprov.updated')}>
                            {settings.updated_at ? (
                                <>
                                    <RiyadhTime iso={settings.updated_at} /> {settings.updated_by?.name ? `· ${settings.updated_by.name}` : ''}
                                </>
                            ) : (
                                '—'
                            )}
                        </Fact>
                    </div>
                ) : (
                    <SkeletonLines rows={1} />
                )}
            </Card>

            <div className="mb-3 flex flex-wrap items-end justify-between gap-3">
                <CountTabs<ExtView>
                    value={state.view}
                    onChange={(v) => setState({ view: v })}
                    testId="p10-ext-views"
                    tabs={[
                        { value: 'all', label: t('p10.extprov.view.all'), count: c?.total },
                        { value: 'unassigned', label: t('p10.extprov.view.unassigned'), count: c?.unassigned },
                        { value: 'assigned', label: t('p10.extprov.view.assigned'), count: c?.assigned },
                        { value: 'attention', label: t('p10.extprov.view.attention'), count: c ? c.pending + c.provisioning + c.failed + c.missing_on_pbx : undefined },
                    ]}
                />
                <div className="w-[240px] min-w-[180px]">
                    <Input size="sm" placeholder={String(t('p10.extprov.search'))} value={search} onChange={(e) => setSearch(e.target.value)} data-testid="p10-ext-search" />
                </div>
            </div>
            <TableFrame testId="p10-ext-table">
                {all.error && !data ? (
                    <ErrorState error={all.error} onRetry={all.reload} describe={describeExtError} />
                ) : (
                    <Table>
                        <THead>
                            <Tr>
                                <Th>{t('p10.extprov.extension')}</Th>
                                <Th>{t('p10.extprov.pbxCol')}</Th>
                                <Th>{t('p10.extprov.owner')}</Th>
                                <Th>{t('p10.extprov.statusCol')}</Th>
                                <Th>{t('p10.extprov.request')}</Th>
                                <Th />
                            </Tr>
                        </THead>
                        <TBody>
                            {all.loading && !data ? (
                                <SkeletonRows cols={COLS} rows={6} />
                            ) : items.length ? (
                                items.map((x) => {
                                    const r = x.provisioning
                                    const canDelete = !x.owner && !x.in_use && (x.pbx?.managed || (r && (r.retryable || r.state === 'failed')))
                                    return (
                                        <Tr key={x.extension} data-testid="p10-ext-row" data-ext={x.extension} data-status={x.status}>
                                            <Td>
                                                <Ltr className="font-extrabold text-navy-600 dark:text-gray-100">{x.extension}</Ltr>
                                                <div className="text-[12.5px] text-gray-600" dir="auto">
                                                    {x.name || '—'}
                                                </div>
                                                {!x.in_range && <div className="text-[11.5px] text-gray-500">{t('p10.extprov.outOfRange')}</div>}
                                            </Td>
                                            <Td>
                                                <PbxBadge x={x} />
                                            </Td>
                                            <Td className="text-[12.5px]">
                                                {x.owner ? (
                                                    <>
                                                        <div className="font-extrabold">{x.owner.name || '—'}</div>
                                                        <Ltr className="text-gray-500">{x.owner.user_name}</Ltr>
                                                        {x.owner.is_active === false && <span className="ms-1 text-warning">{t('p10.extprov.userInactive')}</span>}
                                                    </>
                                                ) : (
                                                    '—'
                                                )}
                                                {x.in_use && <div className="font-extrabold text-warning">{t('p10.extprov.onCall')}</div>}
                                            </Td>
                                            <Td>
                                                <StatusBadge status={x.status} />
                                            </Td>
                                            <Td>
                                                <RequestCell r={r} />
                                            </Td>
                                            <Td>
                                                <span className="flex flex-wrap gap-1">
                                                    {r && r.retryable && (
                                                        <Button size="xs" variant="solid" loading={retrying === x.extension} disabled={!!retrying} onClick={() => retry(x)} data-testid="p10-ext-retry">
                                                            {t('p10.extprov.retry')}
                                                        </Button>
                                                    )}
                                                    {!x.owner && x.on_pbx !== false && x.status !== 'pending' && x.status !== 'provisioning' && x.status !== 'failed' && (
                                                        <Button size="xs" onClick={() => setAssigning({ ext: x.extension, user: null })} data-testid="p10-ext-assign">
                                                            {t('p10.extprov.assign')}
                                                        </Button>
                                                    )}
                                                    {x.owner && (
                                                        <Button size="xs" onClick={() => setReleasing(x)} data-testid="p10-ext-release">
                                                            {t('p10.extprov.release')}
                                                        </Button>
                                                    )}
                                                    {x.on_pbx && (
                                                        <Button size="xs" onClick={() => setRenaming(x)} data-testid="p10-ext-rename-open">
                                                            {t('p10.extprov.rename')}
                                                        </Button>
                                                    )}
                                                    {canDelete && (
                                                        <Button size="xs" variant="plain" className="text-danger" onClick={() => setDeleting(x)} data-testid="p10-ext-delete">
                                                            {x.on_pbx ? t('p10.extprov.delete') : t('p10.extprov.cancel')}
                                                        </Button>
                                                    )}
                                                </span>
                                            </Td>
                                        </Tr>
                                    )
                                })
                            ) : (
                                <tr>
                                    <td colSpan={COLS}>
                                        <EmptyState title={t('p10.extprov.empty')} />
                                    </td>
                                </tr>
                            )}
                        </TBody>
                    </Table>
                )}
            </TableFrame>
            <Note className="mb-6 mt-3">{t('p10.extprov.note')}</Note>

            <UsersWithout reloadKey={reloadKey} canCreate={canCreate} onCreate={(u) => setCreating({ preset: u })} onAssign={(u) => setAssigning({ ext: null, user: u })} />

            <CreateDialog isOpen={!!creating} preset={creating?.preset || null} settings={settings} onClose={() => setCreating(null)} onDone={reload} />
            <AssignDialog isOpen={!!assigning} ext={assigning?.ext || null} user={assigning?.user || null} unassigned={unassigned} onClose={() => setAssigning(null)} onDone={reload} />
            <RenameDialog x={renaming} onClose={() => setRenaming(null)} onDone={reload} />
            <SettingsDialog settings={settings} isOpen={settingsOpen} onClose={() => setSettingsOpen(false)} onDone={reload} />
            <ReasonDialog
                isOpen={!!releasing}
                title={t('p10.extprov.releaseTitle', { ext: releasing?.extension || '' })}
                description={t('p10.extprov.releaseHint', { name: releasing?.owner?.name || '—' })}
                confirmLabel={t('p10.extprov.release')}
                tone="danger"
                testId="p10-ext-release-dialog"
                describeError={describeExtError}
                onReload={reload}
                onClose={() => setReleasing(null)}
                onSubmit={async (reason, key) => {
                    if (!releasing) return
                    await callingAdminApi.release(releasing.extension, { reason, expected_owner_id: releasing.owner?.id || null }, key)
                    reload()
                }}
            />
            <ReasonDialog
                isOpen={!!deleting}
                title={t(deleting?.on_pbx ? 'p10.extprov.deleteTitle' : 'p10.extprov.cancelTitle', { ext: deleting?.extension || '' })}
                description={t(deleting?.on_pbx ? 'p10.extprov.deleteHint' : 'p10.extprov.cancelHint')}
                confirmLabel={deleting?.on_pbx ? t('p10.extprov.delete') : t('p10.extprov.cancel')}
                tone="danger"
                testId="p10-ext-delete-dialog"
                describeError={describeExtError}
                onReload={reload}
                onClose={() => setDeleting(null)}
                onSubmit={async (reason, key) => {
                    if (!deleting) return
                    await extProvisionApi.remove(deleting.extension, { reason }, key)
                    reload()
                }}
            />
        </div>
    )
}

export default ExtensionsPage
