/**
 * Saving a role's permissions safely.
 *
 * - The legacy PUT/POST /roles handlers set every key they are *listed* to
 *   true. The edit form used to send the stored `permission` object when the
 *   tree was not touched, which listed every key and so granted everything.
 *   The form now always sends the array of checked keys.
 * - After a save the role is read back and any checked key the server did not
 *   keep is reported (the Phase 1 keys were dropped by the legacy handlers
 *   before the API fix).
 */
import { useTranslation } from 'react-i18next'
import Dialog from '@/components/ui/Dialog'
import Button from '@/components/ui/Button'
import { apiGetRolesDetails } from '@/services/PermissionsService'
import { isPhase1PermissionKey, phase1PermissionLabel } from '@/constants/permissions.phase1'

type RoleLike = {
    permission?: unknown
    permission_arr?: unknown
}

/** The checked keys of a role as the tree and the API expect them: a string array. */
export function toCheckedKeys(role: RoleLike | null | undefined): string[] {
    if (!role) return []
    // The form's own value (the tree's checked leaves) wins over the loaded list.
    if (Array.isArray(role.permission)) return role.permission.map(String)
    if (Array.isArray(role.permission_arr)) return role.permission_arr.map(String)
    if (role.permission && typeof role.permission === 'object') {
        return Object.entries(role.permission as Record<string, unknown>)
            .filter(([, v]) => v === true)
            .map(([k]) => k)
    }
    return []
}

/** Keys that were sent as checked but are not true on the saved role. */
export async function findDroppedKeys(roleId: string, sent: string[]): Promise<string[]> {
    const res = await apiGetRolesDetails<{ permission_arr?: string[] }, { id: string }>({ id: roleId })
    const kept = new Set((res.data?.permission_arr || []).map(String))
    return sent.filter((k) => !kept.has(k))
}

export const DroppedKeysDialog = ({ keys, onClose }: { keys: string[] | null; onClose: () => void }) => {
    const { t, i18n } = useTranslation()
    const phase1 = (keys || []).filter(isPhase1PermissionKey)
    return (
        <Dialog isOpen={!!keys && keys.length > 0} width={560} onClose={onClose} onRequestClose={onClose}>
            <div data-testid="p1-perm-dropped">
                <h5 className="mb-3 pe-8 text-[17px] font-extrabold text-navy-600 dark:text-gray-100">
                    {t('p1.perm.droppedTitle')}
                </h5>
                <p className="mb-3 text-[13.5px] text-gray-700 dark:text-night-body">{t('p1.perm.droppedHint')}</p>
                <ul className="mb-3 flex flex-col gap-1 rounded-md border border-gray-300 p-3 text-[13.5px] dark:border-night-border">
                    {(keys || []).map((k) => (
                        <li key={k} className="flex flex-wrap items-center gap-2">
                            <span className="font-extrabold">
                                {isPhase1PermissionKey(k) ? phase1PermissionLabel(k, i18n.language) : k}
                            </span>
                            <span dir="ltr" className="font-mono text-[12px] text-gray-500">
                                {k}
                            </span>
                        </li>
                    ))}
                </ul>
                {phase1.length > 0 && (
                    <p className="mb-3 text-[12.5px] text-gray-600">{t('p1.perm.droppedPhase1')}</p>
                )}
                <div className="flex justify-end">
                    <Button size="sm" variant="solid" onClick={onClose} data-testid="p1-perm-dropped-ok">
                        {t('p1.action.close')}
                    </Button>
                </div>
            </div>
        </Dialog>
    )
}
