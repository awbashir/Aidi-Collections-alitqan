/**
 * Calling administration (`/calling-admin`, key `calls_manage`; Intelligence & control).
 *
 * - Service status (`GET /v2/calling/status`): the calling service's latest PBX session as
 *   the database saw it (connected, epoch, a restart), in-flight attempts, active
 *   reservations, AGI allow/deny for 24 h, the Riyadh calling window. "Connected" is the
 *   AMI session at its last report, never a guarantee that a call will go through.
 * - Extensions and ownership (`user_extensions` is the source of truth; `users.extension`
 *   is a mirror): assign (with a reason, `expected_owner_id`, optional replace), release
 *   (reason), history. An extension with an active call reservation is never moved.
 * - Legacy drift: every difference between the legacy `users.extension` and ownership,
 *   classified; "adopt" turns one legacy value into ownership after review (reason).
 * - Calling windows: the reviewed Riyadh policy (days, start inclusive, end exclusive,
 *   overnight ranges, holidays), saved with a reason and the loaded revision.
 * - Do-not-call blocks: the scoped calls wrapped up "do not call", each case's block and
 *   "lift" (reason). The API has no global block list (reported).
 */
import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { HiOutlinePlus, HiOutlineTrash } from 'react-icons/hi'
import Button from '@/components/ui/Button'
import Checkbox from '@/components/ui/Checkbox'
import Input from '@/components/ui/Input'
import Select from '@/components/ui/Select'
import Table from '@/components/ui/Table'
import ActionDialog, { DialogField, ReasonDialog } from '@/components/v2/ActionDialog'
import { Ltr, RiyadhTime } from '@/components/v2/Display'
import { EmptyState, ErrorState, InlineError, Note, SkeletonLines, SkeletonRows } from '@/components/v2/States'
import { CountTabs, PageHead, PagerFooter, TableFrame } from '@/components/v2/ListParts'
import ToastNotification from '@/components/shared/myComponents/ToastNotification'
import { Segmented } from '@/views/phase1/followUps/parts'
import useHashState, { hashCodec } from '@/utils/hooks/useHashState'
import type { HashSchema } from '@/utils/hooks/useHashState'
import { callingAdminApi, callsApi, describeP5Error, newIdempotencyKey } from '@/services/v2'
import type { CallingHoliday, CallingUser, CallingWindow, DriftRow, ExtensionRow, Option, Translate } from '@/services/v2'
import { useV2Query } from '@/utils/v2/hooks'
import { formatCount } from '@/utils/v2/format'
import { Card, CaseCell, Fact, Notice } from '../parts'

const { THead, TBody, Tr, Th, Td } = Table
type Tab = 'status' | 'extensions' | 'drift' | 'windows' | 'blocks'
const TABS: Tab[] = ['status', 'extensions', 'drift', 'windows', 'blocks']
type ExtState = 'active' | 'released' | 'all'
type HashState = { tab: Tab; est: ExtState; q: string | undefined; page: number }
const schema: HashSchema<HashState> = {
    tab: { key: 'tab', codec: hashCodec.oneOf(TABS), default: 'status' },
    est: { key: 'ext', codec: hashCodec.oneOf(['active', 'released', 'all'] as ExtState[]), default: 'active' },
    q: { key: 'q', codec: hashCodec.text({ maxLength: 100 }), default: undefined, history: 'replace' },
    page: { key: 'page', codec: hashCodec.int({ min: 1, max: 100000 }), default: 1 },
}
const PAGE_SIZE = 25
const describe = (e: unknown, t: Translate) => describeP5Error(e, t)

/* ------------------------------------------------------------------ status */

const StatusTab = () => {
    const { t } = useTranslation()
    const q = useV2Query((signal) => callingAdminApi.status(signal), [])
    if (q.loading && !q.data) return <SkeletonLines rows={6} />
    if (!q.data) return <ErrorState error={q.error} onRetry={q.reload} describe={describe} />
    const s = q.data
    const pbx = s.pbx
    return (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2" data-testid="p5-admin-status" data-connected={pbx?.connected ? '1' : '0'}>
            <Card lead title={t('p5.admin.pbxSession')} actions={<Button size="xs" onClick={q.reload}>{t('p1.action.refresh')}</Button>}>
                {pbx ? (
                    <div className="grid grid-cols-2 gap-3">
                        <Fact label={t('p5.admin.connected')} testId="p5-admin-connected">
                            <span className={pbx.connected ? 'font-extrabold text-success' : 'font-extrabold text-danger'}>{pbx.connected ? t('p5.yes') : t('p5.no')}</span>
                        </Fact>
                        <Fact label={t('p5.admin.host')}>{pbx.host ? <Ltr>{pbx.host}</Ltr> : '—'}</Fact>
                        <Fact label={t('p5.admin.epoch')} testId="p5-admin-epoch">
                            {pbx.epoch ? <Ltr className="break-all text-[12px]">{pbx.epoch}</Ltr> : '—'}
                        </Fact>
                        <Fact label={t('p5.admin.epochStrength')}>{pbx.epoch_strength || '—'}</Fact>
                        <Fact label={t('p5.admin.connectedAt')}>
                            <RiyadhTime iso={pbx.connected_at} />
                        </Fact>
                        <Fact label={t('p5.admin.restarted')}>{pbx.pbx_restarted ? t('p5.yes') : t('p5.no')}</Fact>
                        {!pbx.connected && (
                            <Fact label={t('p5.admin.disconnected')}>
                                <RiyadhTime iso={pbx.disconnected_at} /> {pbx.disconnect_code && <Ltr>({pbx.disconnect_code})</Ltr>}
                            </Fact>
                        )}
                    </div>
                ) : (
                    <div className="text-[13px] text-gray-600">{t('p5.admin.noSession')}</div>
                )}
                <div className="mt-3 text-[11.5px] text-gray-500">{t('p5.admin.connectedNote')}</div>
            </Card>
            <Card title={t('p5.admin.activity')}>
                <div className="grid grid-cols-2 gap-3">
                    <Fact label={t('p5.admin.reservations')} testId="p5-admin-reservations">{formatCount(s.active_reservations)}</Fact>
                    <Fact label={t('p5.admin.agi')}>{t('p5.admin.agiCounts', { allow: s.agi_decisions_24h.allow, deny: s.agi_decisions_24h.deny })}</Fact>
                    <Fact label={t('p5.admin.inFlight')}>
                        {Object.keys(s.in_flight_by_state).length
                            ? Object.entries(s.in_flight_by_state)
                                  .map(([k, v]) => `${t(`p5.state.${k}`)}: ${v}`)
                                  .join(String(t('p5.listSep')))
                            : '—'}
                    </Fact>
                    <Fact label={t('p5.ws.window')}>
                        {s.window.configured ? (s.window.open ? t('p5.ws.windowOpen') : t('p5.ws.windowClosed')) : t('p5.ws.windowMissing')} ·{' '}
                        <RiyadhTime iso={s.window.riyadh_time} />
                    </Fact>
                </div>
            </Card>
        </div>
    )
}

/* ------------------------------------------------------------------ extensions */

/** Active users for the picker (`GET /v2/calling/users?q=`). */
function useCallingUsers(q: string) {
    const [users, setUsers] = useState<CallingUser[]>([])
    useEffect(() => {
        let alive = true
        const id = setTimeout(() => {
            callingAdminApi
                .users({ q: q || undefined, page_size: 50 })
                .then((r) => alive && setUsers(r.items))
                .catch(() => alive && setUsers([]))
        }, 250)
        return () => {
            alive = false
            clearTimeout(id)
        }
    }, [q])
    return users
}

const AssignDialog = ({
    isOpen,
    preset,
    onClose,
    onDone,
}: {
    isOpen: boolean
    preset: ExtensionRow | null
    onClose: () => void
    onDone: () => void
}) => {
    const { t } = useTranslation()
    const [ext, setExt] = useState('')
    const [search, setSearch] = useState('')
    const [userId, setUserId] = useState<string | undefined>(undefined)
    const [reason, setReason] = useState('')
    const [replace, setReplace] = useState(false)
    useEffect(() => {
        if (isOpen) {
            setExt(preset?.extension || '')
            setSearch('')
            setUserId(undefined)
            setReason('')
            setReplace(false)
        }
    }, [isOpen, preset])
    const users = useCallingUsers(isOpen ? search : '')
    const options: Option[] = users.map((u) => ({
        value: u.id,
        label: `${u.name || u.user_name} (${u.user_name})${u.owned_extension ? ` · ${u.owned_extension}` : ''}`,
    }))
    const chosen = users.find((u) => u.id === userId)
    const owner = preset && preset.extension === ext && preset.active ? preset.user?.id || null : null
    const [ownerKnown, setOwnerKnown] = useState<{ ext: string; owner: string | null } | null>(null)
    /* The owner I expect (the API refuses with owner_changed if it moved meanwhile). */
    useEffect(() => {
        if (!isOpen || !/^\d{2,6}$/.test(ext)) {
            setOwnerKnown(null)
            return undefined
        }
        let alive = true
        callingAdminApi
            .history(ext)
            .then((h) => alive && setOwnerKnown({ ext, owner: h.items.find((i) => i.active)?.user?.id || null }))
            .catch(() => alive && setOwnerKnown({ ext, owner: null }))
        return () => {
            alive = false
        }
    }, [isOpen, ext])
    const expectedOwner = ownerKnown && ownerKnown.ext === ext ? ownerKnown.owner : owner
    return (
        <ActionDialog
            isOpen={isOpen}
            title={t('p5.admin.assignTitle')}
            confirmLabel={t('p5.admin.assign')}
            canSubmit={/^\d{2,6}$/.test(ext) && !!userId && reason.trim().length > 0 && !!ownerKnown}
            testId="p5-admin-assign"
            describeError={describe}
            onClose={onClose}
            onSubmit={async (key) => {
                if (!userId) return
                const res = await callingAdminApi.assign(ext, { user_id: userId, reason: reason.trim(), expected_owner_id: expectedOwner, ...(replace ? { replace: true } : {}) }, key)
                if (res.legacy_conflicts && res.legacy_conflicts.length) {
                    ToastNotification(String(t('p5.admin.legacyConflicts', { count: res.legacy_conflicts.length })), String(t('p5.admin.assignTitle')), 'warning', 6000)
                }
                onDone()
            }}
        >
            <DialogField label={t('p5.admin.extension')} required hint={t('p5.admin.extensionHint')}>
                <Input size="sm" dir="ltr" inputMode="numeric" value={ext} onChange={(e) => setExt(e.target.value.replace(/[^0-9]/g, '').slice(0, 6))} data-testid="p5-admin-assign-ext" />
            </DialogField>
            {ownerKnown && ownerKnown.ext === ext && (
                <div className="-mt-2 mb-3 text-[12.5px] text-gray-600" data-testid="p5-admin-assign-owner">
                    {ownerKnown.owner ? t('p5.admin.currentOwner') : t('p5.admin.unowned')}
                </div>
            )}
            {/* Two separate fields: a <label> around both would send every click to the search box. */}
            <DialogField label={t('p5.admin.searchUser')}>
                <Input size="sm" value={search} onChange={(e) => setSearch(e.target.value)} data-testid="p5-admin-assign-search" />
            </DialogField>
            <DialogField label={t('p5.admin.user')} required>
                <div data-testid="p5-admin-assign-user">
                    <Select
                        size="sm"
                        menuPosition="fixed"
                        placeholder={t('p1.followUps.select')}
                        noOptionsMessage={() => t('p5.admin.noUsers')}
                        options={options}
                        value={options.find((o) => o.value === userId) || null}
                        onChange={(o) => setUserId((o as Option | null)?.value || undefined)}
                    />
                </div>
            </DialogField>
            {chosen?.legacy_extension && chosen.legacy_extension !== ext && (
                <Notice tone="info" className="mb-3">
                    {t('p5.admin.legacyValue', { value: chosen.legacy_extension })}
                </Notice>
            )}
            <div className="mb-3">
                <Checkbox checked={replace} onChange={(c: boolean) => setReplace(c)} data-testid="p5-admin-assign-replace">
                    {t('p5.admin.replace')}
                </Checkbox>
                <div className="mt-1 text-[12px] text-gray-600">{t('p5.admin.replaceHint')}</div>
            </div>
            <DialogField label={t('p1.fields.reason')} required hint={t('p1.hint.reasonRequired')}>
                <Input textArea value={reason} maxLength={1000} onChange={(e) => setReason(e.target.value)} data-testid="p5-admin-assign-reason" />
            </DialogField>
        </ActionDialog>
    )
}

const HistoryDialog = ({ ext, onClose }: { ext: string | null; onClose: () => void }) => {
    const { t } = useTranslation()
    const q = useV2Query(ext ? (signal) => callingAdminApi.history(ext, signal) : null, [ext])
    return (
        <ActionDialog isOpen={!!ext} title={t('p5.admin.historyTitle', { ext: ext || '' })} confirmLabel={t('p1.action.close')} onClose={onClose} onSubmit={async () => undefined} testId="p5-admin-history">
            {q.loading && !q.data ? (
                <SkeletonLines rows={4} />
            ) : q.data ? (
                <>
                    <ol className="flex flex-col gap-2 text-[13px]">
                        {q.data.items.map((i) => (
                            <li key={i.id} className="rounded-md border border-gray-300 px-3 py-2 dark:border-night-border">
                                <div className="font-extrabold">{i.user?.name || '—'}</div>
                                <div className="text-gray-600">
                                    {t('p5.admin.assignedBy', { name: i.assigned_by?.name || '—' })} · <RiyadhTime iso={i.assigned_at} /> · {i.source}
                                </div>
                                {i.assign_reason && <div dir="auto">{i.assign_reason}</div>}
                                {i.released_at && (
                                    <div className="text-gray-600">
                                        {t('p5.admin.releasedBy', { name: i.released_by?.name || '—' })} · <RiyadhTime iso={i.released_at} /> · <span dir="auto">{i.release_reason}</span>
                                    </div>
                                )}
                            </li>
                        ))}
                    </ol>
                    {q.data.legacy_holders.length > 0 && (
                        <div className="mt-3 text-[12.5px] text-gray-600">
                            {t('p5.admin.legacyHolders')}: {q.data.legacy_holders.map((h) => h.name).join(String(t('p5.listSep')))}
                        </div>
                    )}
                </>
            ) : null}
        </ActionDialog>
    )
}

const ExtensionsTab = ({ state, onState }: { state: HashState; onState: (p: Partial<HashState>) => void }) => {
    const { t } = useTranslation()
    const q = useV2Query((signal) => callingAdminApi.extensions({ state: state.est, q: state.q, page: state.page, page_size: PAGE_SIZE }, signal), [state.est, state.q, state.page])
    const [assigning, setAssigning] = useState<{ preset: ExtensionRow | null } | null>(null)
    const [releasing, setReleasing] = useState<ExtensionRow | null>(null)
    const [history, setHistory] = useState<string | null>(null)
    const data = q.data
    return (
        <div data-testid="p5-admin-extensions">
            <div className="mb-3 flex flex-wrap items-end justify-between gap-3">
                <div className="flex flex-wrap items-end gap-3">
                    <Segmented<ExtState>
                        value={state.est}
                        onChange={(v) => onState({ est: v, page: 1 })}
                        testId="p5-admin-ext-state"
                        options={[
                            { value: 'active', label: t('p5.admin.active') },
                            { value: 'released', label: t('p5.admin.released') },
                            { value: 'all', label: t('p5.admin.all') },
                        ]}
                    />
                    <div className="w-[240px]">
                        <Input size="sm" placeholder={String(t('p5.admin.searchExt'))} value={state.q || ''} onChange={(e) => onState({ q: e.target.value || undefined, page: 1 })} data-testid="p5-admin-ext-search" />
                    </div>
                </div>
                <Button size="sm" variant="solid" icon={<HiOutlinePlus />} onClick={() => setAssigning({ preset: null })} data-testid="p5-admin-assign-open">
                    {t('p5.admin.assign')}
                </Button>
            </div>
            <TableFrame footer={data && data.total > 0 ? <PagerFooter page={state.page} pageSize={PAGE_SIZE} total={data.total} onPage={(p) => onState({ page: p })} /> : undefined}>
                {q.error && !data ? (
                    <ErrorState error={q.error} onRetry={q.reload} describe={describe} />
                ) : (
                    <Table>
                        <THead>
                            <Tr>
                                <Th>{t('p5.admin.extension')}</Th>
                                <Th>{t('p5.admin.owner')}</Th>
                                <Th>{t('p5.admin.assigned')}</Th>
                                <Th>{t('p5.admin.mirror')}</Th>
                                <Th>{t('p5.admin.inUse')}</Th>
                                <Th />
                            </Tr>
                        </THead>
                        <TBody>
                            {q.loading && !data ? (
                                <SkeletonRows cols={6} rows={4} />
                            ) : data && data.items.length ? (
                                data.items.map((x) => (
                                    <Tr key={x.id} data-testid="p5-admin-ext-row" data-ext={x.extension} data-active={x.active ? '1' : '0'} data-user={x.user?.id || ''}>
                                        <Td>
                                            <Ltr className="font-extrabold text-navy-600 dark:text-gray-100">{x.extension}</Ltr>
                                        </Td>
                                        <Td>
                                            {x.user?.name || '—'}
                                            {x.user && x.user.is_active === false && <span className="ms-1 text-[12px] text-warning">{t('p5.admin.userInactive')}</span>}
                                        </Td>
                                        <Td className="text-[12.5px]">
                                            <RiyadhTime iso={x.assigned_at} /> · {x.source}
                                            {!x.active && (
                                                <div className="text-gray-600">
                                                    {t('p5.admin.releasedAt')} <RiyadhTime iso={x.released_at} />
                                                </div>
                                            )}
                                        </Td>
                                        <Td className="text-[12.5px]">{x.active ? (x.mirror_in_sync ? t('p5.admin.inSync') : t('p5.admin.outOfSync', { value: x.legacy_extension || '—' })) : '—'}</Td>
                                        <Td>{x.in_use ? <span className="font-extrabold text-warning">{t('p5.admin.onCall')}</span> : '—'}</Td>
                                        <Td>
                                            <span className="flex gap-1">
                                                <Button size="xs" onClick={() => setHistory(x.extension)} data-testid="p5-admin-ext-history">
                                                    {t('p5.admin.history')}
                                                </Button>
                                                {x.active && (
                                                    <>
                                                        <Button size="xs" onClick={() => setAssigning({ preset: x })} data-testid="p5-admin-ext-reassign">
                                                            {t('p5.admin.reassign')}
                                                        </Button>
                                                        <Button size="xs" onClick={() => setReleasing(x)} data-testid="p5-admin-ext-release">
                                                            {t('p5.admin.release')}
                                                        </Button>
                                                    </>
                                                )}
                                            </span>
                                        </Td>
                                    </Tr>
                                ))
                            ) : (
                                <tr>
                                    <td colSpan={6}>
                                        <EmptyState title={t('p5.admin.noExtensions')} hint={t('p5.admin.noExtensionsHint')} />
                                    </td>
                                </tr>
                            )}
                        </TBody>
                    </Table>
                )}
            </TableFrame>
            <Note className="mt-3">{t('p5.admin.extNote')}</Note>
            <AssignDialog isOpen={!!assigning} preset={assigning?.preset || null} onClose={() => setAssigning(null)} onDone={q.reload} />
            <ReasonDialog
                isOpen={!!releasing}
                title={t('p5.admin.releaseTitle', { ext: releasing?.extension || '' })}
                description={t('p5.admin.releaseHint', { name: releasing?.user?.name || '—' })}
                confirmLabel={t('p5.admin.release')}
                tone="danger"
                testId="p5-admin-release"
                describeError={describe}
                onReload={q.reload}
                onClose={() => setReleasing(null)}
                onSubmit={async (reason, key) => {
                    if (!releasing) return
                    await callingAdminApi.release(releasing.extension, { reason, expected_owner_id: releasing.user?.id || null }, key)
                    q.reload()
                }}
            />
            <HistoryDialog ext={history} onClose={() => setHistory(null)} />
        </div>
    )
}

/* ------------------------------------------------------------------ legacy drift */

const DriftTab = ({ page, onPage }: { page: number; onPage: (p: number) => void }) => {
    const { t } = useTranslation()
    const q = useV2Query((signal) => callingAdminApi.drift({ page, page_size: PAGE_SIZE }, signal), [page])
    const [adopting, setAdopting] = useState<DriftRow | null>(null)
    const data = q.data
    return (
        <div data-testid="p5-admin-drift">
            <Notice tone="info" className="mb-3">
                {t('p5.admin.driftIntro')}
            </Notice>
            <TableFrame footer={data && data.total > 0 ? <PagerFooter page={page} pageSize={PAGE_SIZE} total={data.total} onPage={onPage} /> : undefined}>
                {q.error && !data ? (
                    <ErrorState error={q.error} onRetry={q.reload} describe={describe} />
                ) : (
                    <Table>
                        <THead>
                            <Tr>
                                <Th>{t('p5.admin.user')}</Th>
                                <Th>{t('p5.admin.legacyExt')}</Th>
                                <Th>{t('p5.admin.ownedExt')}</Th>
                                <Th>{t('p5.admin.driftStatus')}</Th>
                                <Th />
                            </Tr>
                        </THead>
                        <TBody>
                            {q.loading && !data ? (
                                <SkeletonRows cols={5} rows={5} />
                            ) : data && data.items.length ? (
                                data.items.map((r) => (
                                    <Tr key={r.user.id} data-testid="p5-admin-drift-row" data-status={r.status} data-user={r.user.id}>
                                        <Td>
                                            {r.user.name || '—'}
                                            {!r.user.is_active && <span className="ms-1 text-[12px] text-warning">{t('p5.admin.userInactive')}</span>}
                                        </Td>
                                        <Td>
                                            {r.legacy_extension ? <Ltr>{r.legacy_extension}</Ltr> : '—'}
                                            {r.legacy_shared_with > 0 && <div className="text-[12px] text-warning">{t('p5.admin.sharedWith', { count: r.legacy_shared_with })}</div>}
                                        </Td>
                                        <Td>{r.owned_extension ? <Ltr>{r.owned_extension}</Ltr> : '—'}</Td>
                                        <Td className="text-[12.5px]">{t(`p5.admin.drift.${r.status}`)}</Td>
                                        <Td>
                                            {r.status === 'adoptable' && (
                                                <Button size="xs" onClick={() => setAdopting(r)} data-testid="p5-admin-adopt">
                                                    {t('p5.admin.adopt')}
                                                </Button>
                                            )}
                                        </Td>
                                    </Tr>
                                ))
                            ) : (
                                <tr>
                                    <td colSpan={5}>
                                        <EmptyState title={t('p5.admin.noDrift')} />
                                    </td>
                                </tr>
                            )}
                        </TBody>
                    </Table>
                )}
            </TableFrame>
            <ReasonDialog
                isOpen={!!adopting}
                title={t('p5.admin.adoptTitle', { ext: adopting?.legacy_extension || '' })}
                description={t('p5.admin.adoptHint', { name: adopting?.user.name || '—', ext: adopting?.legacy_extension || '' })}
                confirmLabel={t('p5.admin.adopt')}
                testId="p5-admin-adopt-dialog"
                describeError={describe}
                onReload={q.reload}
                onClose={() => setAdopting(null)}
                onSubmit={async (reason, key) => {
                    if (!adopting?.legacy_extension) return
                    await callingAdminApi.adopt({ user_id: adopting.user.id, extension: adopting.legacy_extension, reason }, key)
                    q.reload()
                }}
            />
        </div>
    )
}

/* ------------------------------------------------------------------ windows */

const DAYS = [0, 1, 2, 3, 4, 5, 6]

const WindowsTab = () => {
    const { t } = useTranslation()
    const q = useV2Query((signal) => callingAdminApi.windows(signal), [])
    const [windows, setWindows] = useState<CallingWindow[]>([])
    const [holidays, setHolidays] = useState<CallingHoliday[]>([])
    const [reason, setReason] = useState('')
    const [busy, setBusy] = useState(false)
    const [error, setError] = useState<unknown>(null)
    const [key, setKey] = useState(newIdempotencyKey())
    useEffect(() => {
        if (q.data) {
            setWindows(q.data.policy?.windows ? q.data.policy.windows.map((w) => ({ ...w, days: [...w.days] })) : [])
            setHolidays(q.data.policy?.holidays ? q.data.policy.holidays.map((h) => ({ ...h })) : [])
        }
    }, [q.data])
    const valid = useMemo(
        () =>
            windows.every((w) => w.days.length > 0 && /^([01]\d|2[0-3]):[0-5]\d$/.test(w.start) && (/^([01]\d|2[0-3]):[0-5]\d$/.test(w.end) || w.end === '24:00') && w.start !== w.end) &&
            holidays.every((h) => /^\d{4}-\d{2}-\d{2}$/.test(h.date)),
        [windows, holidays]
    )
    if (q.loading && !q.data) return <SkeletonLines rows={6} />
    if (!q.data) return <ErrorState error={q.error} onRetry={q.reload} describe={describe} />
    const view = q.data
    const rev = view.policy?.revision ?? 0
    const save = async () => {
        setBusy(true)
        setError(null)
        try {
            await callingAdminApi.saveWindows({ expected_revision: rev, reason: reason.trim(), windows, holidays: holidays.map((h) => (h.label ? h : { date: h.date })) }, key)
            setReason('')
            setKey(newIdempotencyKey())
            ToastNotification(String(t('p5.admin.windowsSaved')), String(t('p5.admin.tab.windows')), 'success')
            q.reload()
        } catch (e) {
            setError(e)
        } finally {
            setBusy(false)
        }
    }
    return (
        <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1fr)_360px]" data-testid="p5-admin-windows" data-revision={rev}>
            <Card
                title={t('p5.admin.windowsTitle')}
                actions={
                    <span className="text-[12.5px] text-gray-600" data-testid="p5-admin-window-status" data-open={view.status.open ? '1' : '0'}>
                        {view.status.configured ? (view.status.open ? t('p5.ws.windowOpen') : t('p5.ws.windowClosed')) : t('p5.ws.windowMissing')} · <RiyadhTime iso={view.status.riyadh_time} />
                    </span>
                }
            >
                <div className="mb-3 text-[12.5px] text-gray-600">{t('p5.admin.windowsRule')}</div>
                {windows.length === 0 && <Notice tone="warning" className="mb-3">{view.policy ? t('p5.admin.closedAllWeek') : t('p5.admin.noPolicy')}</Notice>}
                <ul className="flex flex-col gap-3">
                    {windows.map((w, i) => (
                        <li key={i} className="rounded-md border border-gray-300 p-3 dark:border-night-border" data-testid="p5-admin-window">
                            <div className="mb-2 flex flex-wrap gap-2">
                                {DAYS.map((d) => (
                                    <Checkbox
                                        key={d}
                                        checked={w.days.includes(d)}
                                        onChange={(c: boolean) =>
                                            setWindows(windows.map((x, j) => (j === i ? { ...x, days: c ? [...x.days, d].sort() : x.days.filter((y) => y !== d) } : x)))
                                        }
                                    >
                                        {t(`p5.day.${d}`)}
                                    </Checkbox>
                                ))}
                            </div>
                            <div className="flex flex-wrap items-end gap-3">
                                <DialogField label={t('p5.admin.start')}>
                                    <Input size="sm" type="time" value={w.start} onChange={(e) => setWindows(windows.map((x, j) => (j === i ? { ...x, start: e.target.value } : x)))} />
                                </DialogField>
                                <DialogField label={t('p5.admin.end')} hint={t('p5.admin.endHint')}>
                                    <Input size="sm" dir="ltr" value={w.end} onChange={(e) => setWindows(windows.map((x, j) => (j === i ? { ...x, end: e.target.value } : x)))} />
                                </DialogField>
                                <Button size="xs" variant="plain" icon={<HiOutlineTrash />} onClick={() => setWindows(windows.filter((_, j) => j !== i))}>
                                    {t('p5.admin.remove')}
                                </Button>
                            </div>
                        </li>
                    ))}
                </ul>
                <div className="mt-3 flex flex-wrap gap-2">
                    <Button size="sm" icon={<HiOutlinePlus />} onClick={() => setWindows([...windows, { days: [0, 1, 2, 3, 4], start: '09:00', end: '17:00' }])} data-testid="p5-admin-window-add">
                        {t('p5.admin.addWindow')}
                    </Button>
                    <Button size="sm" onClick={() => setWindows([{ days: [0, 1, 2, 3, 4, 5, 6], start: '00:00', end: '24:00' }])} data-testid="p5-admin-window-allday">
                        {t('p5.admin.allDay')}
                    </Button>
                </div>
                <div className="mt-4 text-eyebrow mb-2">{t('p5.admin.holidays')}</div>
                <ul className="flex flex-col gap-2">
                    {holidays.map((h, i) => (
                        <li key={i} className="flex flex-wrap items-end gap-2">
                            <Input size="sm" type="date" className="w-[170px]" value={h.date} onChange={(e) => setHolidays(holidays.map((x, j) => (j === i ? { ...x, date: e.target.value } : x)))} />
                            <Input size="sm" className="w-[220px]" placeholder={String(t('p5.admin.holidayLabel'))} value={h.label || ''} onChange={(e) => setHolidays(holidays.map((x, j) => (j === i ? { ...x, label: e.target.value } : x)))} />
                            <Button size="xs" variant="plain" icon={<HiOutlineTrash />} onClick={() => setHolidays(holidays.filter((_, j) => j !== i))} />
                        </li>
                    ))}
                </ul>
                <Button size="sm" className="mt-2" icon={<HiOutlinePlus />} onClick={() => setHolidays([...holidays, { date: '' }])}>
                    {t('p5.admin.addHoliday')}
                </Button>
                <div className="mt-4">
                    <DialogField label={t('p1.fields.reason')} required hint={t('p1.hint.reasonRequired')}>
                        <Input textArea value={reason} maxLength={1000} onChange={(e) => setReason(e.target.value)} data-testid="p5-admin-windows-reason" />
                    </DialogField>
                    <Button
                        size="sm"
                        variant="solid"
                        loading={busy}
                        disabled={!valid || !reason.trim()}
                        aria-disabled={!valid || !reason.trim()}
                        onClick={save}
                        data-testid="p5-admin-windows-save"
                    >
                        {t('p5.admin.saveWindows', { rev })}
                    </Button>
                    <InlineError error={error} describe={describe} onReload={q.reload} />
                </div>
            </Card>
            <Card title={t('p5.admin.windowsHistory')}>
                {view.history.length ? (
                    <ol className="flex flex-col gap-2 text-[12.5px]">
                        {view.history.map((h) => (
                            <li key={h.revision}>
                                <span className="font-extrabold">#{h.revision}</span> · {h.changed_by?.name || '—'} · <RiyadhTime iso={h.changed_at} />
                                {h.reason && <div dir="auto" className="text-gray-600">{h.reason}</div>}
                            </li>
                        ))}
                    </ol>
                ) : (
                    <div className="text-[12.5px] text-gray-600">—</div>
                )}
                <div className="mt-3 text-[11.5px] text-gray-500">{t('p5.admin.windowsNote')}</div>
            </Card>
        </div>
    )
}

/* ------------------------------------------------------------------ do-not-call blocks */

const BlocksTab = () => {
    const { t } = useTranslation()
    const calls = useV2Query((signal) => callsApi.log({ outcome: 'do_not_call', page_size: 200 }, signal), [])
    const caseIds = useMemo(() => Array.from(new Set((calls.data?.items || []).map((c) => c.case.id))), [calls.data])
    const [blocks, setBlocks] = useState<Record<string, Awaited<ReturnType<typeof callsApi.caseBlocks>>>>({})
    const [tick, setTick] = useState(0)
    const [lifting, setLifting] = useState<{ id: string; caseId: string } | null>(null)
    useEffect(() => {
        let alive = true
        Promise.allSettled(caseIds.map((id) => callsApi.caseBlocks(id))).then((res) => {
            if (!alive) return
            const m: typeof blocks = {}
            res.forEach((r, i) => {
                if (r.status === 'fulfilled') m[caseIds[i]] = r.value
            })
            setBlocks(m)
        })
        return () => {
            alive = false
        }
    }, [caseIds, tick])
    const cases = (calls.data?.items || []).filter((c, i, arr) => arr.findIndex((x) => x.case.id === c.case.id) === i)
    return (
        <div data-testid="p5-admin-blocks">
            <Notice tone="info" className="mb-3">
                {t('p5.admin.blocksIntro')}
            </Notice>
            <TableFrame>
                {calls.error && !calls.data ? (
                    <ErrorState error={calls.error} onRetry={calls.reload} describe={describe} />
                ) : (
                    <Table>
                        <THead>
                            <Tr>
                                <Th>{t('p5.col.case')}</Th>
                                <Th>{t('p5.admin.blockState')}</Th>
                                <Th>{t('p5.admin.blockReason')}</Th>
                                <Th>{t('p5.admin.blockSince')}</Th>
                                <Th />
                            </Tr>
                        </THead>
                        <TBody>
                            {calls.loading && !calls.data ? (
                                <SkeletonRows cols={5} rows={3} />
                            ) : cases.length ? (
                                cases.map((c) => {
                                    const b = blocks[c.case.id]
                                    const active = b?.items.find((x) => x.active && x.kind === 'do_not_call')
                                    const last = b?.items[0]
                                    return (
                                        <Tr key={c.case.id} data-testid="p5-admin-block-row" data-case={c.case.id} data-active={active ? '1' : '0'}>
                                            <Td>
                                                <CaseCell kase={c.case} />
                                            </Td>
                                            <Td>{!b ? '…' : active ? <span className="font-extrabold text-danger">{t('p5.admin.blockActive')}</span> : t('p5.admin.blockLifted')}</Td>
                                            <Td className="text-[12.5px]" dir="auto">
                                                {(active || last)?.reason || '—'}
                                                {!active && last?.release_reason && <div className="text-gray-600">{t('p5.admin.liftedBecause', { reason: last.release_reason })}</div>}
                                            </Td>
                                            <Td className="text-[12.5px]">
                                                <RiyadhTime iso={(active || last)?.created_at} />
                                            </Td>
                                            <Td>
                                                {active && (
                                                    <Button size="xs" onClick={() => setLifting({ id: active.id, caseId: c.case.id })} data-testid="p5-admin-block-lift">
                                                        {t('p5.block.lift')}
                                                    </Button>
                                                )}
                                            </Td>
                                        </Tr>
                                    )
                                })
                            ) : (
                                <tr>
                                    <td colSpan={5}>
                                        <EmptyState title={t('p5.admin.noBlocks')} />
                                    </td>
                                </tr>
                            )}
                        </TBody>
                    </Table>
                )}
            </TableFrame>
            <ReasonDialog
                isOpen={!!lifting}
                title={t('p5.block.liftTitle')}
                description={t('p5.block.liftHint')}
                confirmLabel={t('p5.block.lift')}
                testId="p5-admin-lift"
                describeError={describe}
                onClose={() => setLifting(null)}
                onSubmit={async (reason, key) => {
                    if (!lifting) return
                    await callsApi.releaseBlock(lifting.id, reason, key)
                    setTick((n) => n + 1)
                }}
            />
        </div>
    )
}

/* ------------------------------------------------------------------ page */

const CallingAdmin = () => {
    const { t } = useTranslation()
    const { state, setState } = useHashState(schema)
    return (
        <div data-testid="p5-admin">
            <PageHead title={t('p5.admin.title')} subtitle={t('p5.admin.subtitle')} />
            <div className="mb-4">
                <CountTabs<Tab> tabs={TABS.map((tab) => ({ value: tab, label: t(`p5.admin.tab.${tab}`) }))} value={state.tab} onChange={(tab) => setState({ tab, page: 1 })} testId="p5-admin-tabs" />
            </div>
            {state.tab === 'status' && <StatusTab />}
            {state.tab === 'extensions' && <ExtensionsTab state={state} onState={(p) => setState(p)} />}
            {state.tab === 'drift' && <DriftTab page={state.page} onPage={(page) => setState({ page })} />}
            {state.tab === 'windows' && <WindowsTab />}
            {state.tab === 'blocks' && <BlocksTab />}
        </div>
    )
}

export default CallingAdmin
