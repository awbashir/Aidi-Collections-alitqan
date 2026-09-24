/**
 * Sessions and account status of one user, on the user details page
 * (`/users/details?id=`, `CustomerDetail.tsx`, under the profile card).
 *
 * - POST /v2/users/<id>/sign-out {reason?} (key `users_sessions`): ends every
 *   existing session of the user on the server; the account stays active.
 * - POST /v2/users/<id>/deactivate | activate {reason} (`users_delete` |
 *   `users_details`, the keys of the legacy switch this replaces on the page):
 *   deactivating also ends the sessions; re-activating does not revive them.
 *   The API refuses your own account (`409 cannot_deactivate_self`), so the
 *   button is hidden there.
 *
 * Every action goes through a dialog with its own Idempotency-Key and is
 * audited on the server with the reason.
 */
import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { HiOutlineCheck, HiOutlineLogout, HiOutlineX } from 'react-icons/hi'
import Button from '@/components/ui/Button'
import Input from '@/components/ui/Input'
import ToastNotification from '@/components/shared/myComponents/ToastNotification'
import ActionDialog, { DialogField, ReasonDialog } from '@/components/v2/ActionDialog'
import { RiyadhTime } from '@/components/v2/Display'
import { PlainBadge } from '@/components/v2/Badges'
import { Note } from '@/components/v2/States'
import { adminApi, describeP3Error } from '@/services/v2'
import type { Iso } from '@/services/v2'
import { useAppSelector } from '@/store'
import { formatRiyadhDateTime } from '@/utils/v2/format'
import { usePermissions } from '@/utils/v2/hooks'

export type UserSessionsCardProps = {
    user: { id: string; is_active?: boolean; user_name?: string; frist_name?: string; last_name?: string }
    onChanged?: () => void
}

type StatusResult = { is_active: boolean; sessions_valid_after: Iso | null; at: number }

/* The page reloads (and remounts this card) after a status change; the result
 * of that change is kept here for a few minutes so it is still shown after the
 * reload. */
const lastStatus = new Map<string, StatusResult>()
const RESULT_TTL_MS = 5 * 60 * 1000
const recentStatus = (id: string) => {
    const r = lastStatus.get(id)
    return r && Date.now() - r.at < RESULT_TTL_MS ? r : null
}

const UserSessionsCard = ({ user, onChanged }: UserSessionsCardProps) => {
    const { t } = useTranslation()
    const perms = usePermissions()
    const myId = useAppSelector((state) => state.auth.user.id)
    const canSignOut = !!perms.users_sessions
    const canStatus = !!(perms.users_delete || perms.users_details)
    const isSelf = !!myId && String(myId) === String(user.id)
    // The API's answer wins until the page has reloaded the user.
    const [localActive, setLocalActive] = useState<boolean | null>(null)
    useEffect(() => {
        setLocalActive(null)
    }, [user.id, user.is_active])
    const active = localActive ?? user.is_active !== false

    const [signOutOpen, setSignOutOpen] = useState(false)
    const [signOutReason, setSignOutReason] = useState('')
    const [signOutAt, setSignOutAt] = useState<{ at: Iso | null } | null>(null)
    // the action is fixed when the dialog opens, so its wording does not flip on success
    const [statusAction, setStatusAction] = useState<'deactivate' | 'activate' | null>(null)
    const deactivating = statusAction === 'deactivate'
    const [statusResult, setStatusResult] = useState<StatusResult | null>(() => recentStatus(user.id))
    const changed = useRef(false)

    const name = `${(user.frist_name || '').trim()} ${(user.last_name || '').trim()}`.trim() || user.user_name || ''

    return (
        <section
            className="rounded-lg border border-gray-300 bg-white px-5 py-4 dark:border-night-border dark:bg-night-card"
            data-testid="p3-user-sessions"
            data-active={String(active)}
        >
            <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                <h6 className="mb-0 text-[15px] font-extrabold text-navy-600 dark:text-gray-100">{t('p3.sessions.title')}</h6>
                <PlainBadge tone={active ? 'success' : 'neutral'}>
                    <span data-testid="p3-user-status" data-active={String(active)}>
                        {active ? t('p3.sessions.active') : t('p3.sessions.inactive')}
                    </span>
                </PlainBadge>
            </div>

            {statusResult && (
                <div
                    className="mb-3 rounded-md border border-gray-300 bg-gray-50 px-3 py-2 text-[12.5px] text-gray-700 dark:border-night-border dark:bg-night-raised dark:text-night-body"
                    data-testid="p3-user-status-result"
                    data-active={String(statusResult.is_active)}
                >
                    {statusResult.is_active ? t('p3.sessions.activatedResult') : t('p3.sessions.deactivatedResult')}{' '}
                    {statusResult.sessions_valid_after && (
                        <>
                            {t('p3.sessions.validAfter')} <RiyadhTime iso={statusResult.sessions_valid_after} />
                        </>
                    )}
                </div>
            )}

            {canSignOut && (
                <div className="mb-4">
                    <Note className="mb-2">{t('p3.sessions.signOutHint')}</Note>
                    <Button
                        size="sm"
                        block
                        type="button"
                        icon={<HiOutlineLogout />}
                        onClick={() => setSignOutOpen(true)}
                        data-testid="p3-signout-everywhere"
                    >
                        {t('p3.sessions.signOut')}
                    </Button>
                    {signOutAt && (
                        <div
                            className="mt-2 rounded-md border border-gray-300 bg-gray-50 px-3 py-2 text-[12.5px] text-gray-700 dark:border-night-border dark:bg-night-raised dark:text-night-body"
                            data-testid="p3-signout-result"
                            data-at={signOutAt.at || ''}
                        >
                            {t('p3.sessions.signOutResult')} <RiyadhTime iso={signOutAt.at} />
                        </div>
                    )}
                </div>
            )}

            {canStatus && (
                <div>
                    <Note className="mb-2">{active ? t('p3.sessions.deactivateHint') : t('p3.sessions.activateHint')}</Note>
                    {active ? (
                        isSelf ? (
                            <Note>
                                <span data-testid="p3-user-self">{t('p3.sessions.selfHint')}</span>
                            </Note>
                        ) : (
                            <Button
                                size="sm"
                                block
                                type="button"
                                variant="destructive"
                                icon={<HiOutlineX />}
                                onClick={() => setStatusAction('deactivate')}
                                data-testid="p3-user-deactivate"
                            >
                                {t('p3.sessions.deactivate')}
                            </Button>
                        )
                    ) : (
                        <Button
                            size="sm"
                            block
                            type="button"
                            variant="solid"
                            icon={<HiOutlineCheck />}
                            onClick={() => setStatusAction('activate')}
                            data-testid="p3-user-activate"
                        >
                            {t('p3.sessions.activate')}
                        </Button>
                    )}
                </div>
            )}

            {!canSignOut && !canStatus && <Note>{t('p3.sessions.noActions')}</Note>}

            <ActionDialog
                isOpen={signOutOpen}
                title={t('p3.sessions.signOutTitle', { name })}
                tone="danger"
                confirmLabel={t('p3.sessions.signOutConfirm')}
                testId="p3-signout-dialog"
                describeError={describeP3Error}
                onClose={() => {
                    setSignOutOpen(false)
                    setSignOutReason('')
                }}
                onSubmit={async (key) => {
                    const reason = signOutReason.trim()
                    const res = await adminApi.signOut(user.id, reason ? { reason } : {}, key)
                    setSignOutAt({ at: res.sessions_valid_after })
                    ToastNotification(
                        String(t('p3.sessions.signOutDone', { at: formatRiyadhDateTime(res.sessions_valid_after) })),
                        String(t('p3.sessions.title')),
                        'success'
                    )
                }}
            >
                <div className="mb-4 text-[13.5px] text-gray-700 dark:text-night-body">{t('p3.sessions.signOutBody')}</div>
                <DialogField label={t('p1.fields.reason')} hint={t('p3.sessions.reasonOptional')}>
                    <Input
                        textArea
                        value={signOutReason}
                        maxLength={1000}
                        onChange={(e) => setSignOutReason(e.target.value)}
                        data-testid="p3-signout-dialog-reason"
                    />
                </DialogField>
            </ActionDialog>

            <ReasonDialog
                isOpen={statusAction !== null}
                title={deactivating ? t('p3.sessions.deactivateTitle', { name }) : t('p3.sessions.activateTitle', { name })}
                description={deactivating ? t('p3.sessions.deactivateBody') : t('p3.sessions.activateBody')}
                confirmLabel={deactivating ? t('p3.sessions.deactivate') : t('p3.sessions.activate')}
                tone={deactivating ? 'danger' : 'primary'}
                testId="p3-user-status-dialog"
                describeError={describeP3Error}
                onReload={onChanged}
                onClose={() => {
                    setStatusAction(null)
                    if (changed.current) {
                        changed.current = false
                        onChanged?.()
                    }
                }}
                onSubmit={async (reason, key) => {
                    const res = deactivating
                        ? await adminApi.deactivate(user.id, { reason }, key)
                        : await adminApi.activate(user.id, { reason }, key)
                    const result = { is_active: res.is_active, sessions_valid_after: res.sessions_valid_after, at: Date.now() }
                    lastStatus.set(user.id, result)
                    setStatusResult(result)
                    setLocalActive(res.is_active)
                    changed.current = true
                    ToastNotification(
                        String(res.is_active ? t('p3.sessions.activatedToast') : t('p3.sessions.deactivatedToast')),
                        String(t('p3.sessions.title')),
                        'success'
                    )
                }}
            />
        </section>
    )
}

export default UserSessionsCard
