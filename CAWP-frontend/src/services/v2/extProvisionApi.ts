/**
 * Extensions management ("التحويلات") endpoints, checked against CAWP-API/api/calling_extensions.py
 * (migration 0077). Gate: calls_manage.
 *
 * The API creates, renames and deletes the agents' extensions ON THE PBX (Issabel provisioning API)
 * and keeps a request row per create. Ownership (assign / release) stays on the existing
 * `callingAdminApi.assign` / `release` routes. PBX state the API could not read is `null`
 * (unknown), never "absent". Every function returns the envelope's `data` or throws `V2Error`.
 */
import { v2Get, v2Post, v2Put } from './http'

const enc = encodeURIComponent

export type ExtStatus = 'assigned' | 'unassigned' | 'pending' | 'provisioning' | 'failed' | 'missing_on_pbx' | 'unknown'
export type ExtView = 'all' | 'unassigned' | 'assigned' | 'attention'
export type PbxStatus = 'available' | 'unavailable' | 'disabled'
export type ProvisionState = 'pending' | 'provisioning' | 'active' | 'failed' | 'cancelled' | 'deleted'

export type ExtUserRef = { id: string; name: string | null; user_name?: string | null }

export type ProvisionRequest = {
    id: string
    extension: string
    display_name: string
    state: ProvisionState
    origin: 'admin' | 'user_created'
    requested_number: boolean
    for_user: ExtUserRef | null
    assignment_id: string | null
    attempts: number
    last_attempt_at: string | null
    error_code: string | null
    error_message: string | null
    assign_error: string | null
    created_by: ExtUserRef | null
    created_at: string
    updated_at: string
    provisioned_at: string | null
    closed_at: string | null
    close_reason: string | null
    stale: boolean
    retryable: boolean
    /** create / retry answers only */
    assignment?: { assigned: boolean; assignment_id?: string | null; code?: string; message?: string }
    reallocated_from?: string[]
    pbx_status?: PbxStatus
}

export type PbxExtensionInfo = {
    name: string | null
    tech: string | null
    /** created by the app (cawp-ext-provision); only these can be deleted from here */
    managed: boolean
    endpoint: boolean | null
    state: string | null
    registered: boolean | null
    in_call: boolean | null
    channels: number | null
}

export type ManagedExtension = {
    extension: string
    name: string | null
    status: ExtStatus
    /** null = the PBX could not be read */
    on_pbx: boolean | null
    pbx: PbxExtensionInfo | null
    owner: (ExtUserRef & { is_active: boolean | null; assignment_id: string; assigned_at: string; softphone_enabled: boolean }) | null
    provisioning: ProvisionRequest | null
    in_use: boolean
    in_range: boolean
}

export type ExtSettings = {
    auto_provision: boolean
    range_from: number
    range_to: number
    revision: number
    updated_at: string | null
    updated_by: ExtUserRef | null
    /** PBX_PROVISION_URL / TOKEN are set on the server */
    configured: boolean
    auto_effective: boolean
}

export type ExtOverview = {
    items: ManagedExtension[]
    counts: Record<ExtStatus | 'total' | 'registered' | 'in_call', number>
    pbx_status: PbxStatus
    asterisk: boolean | null
    pbx_range: [number, number] | null
    /** "sip" on a chan_sip PBX (desk phones: offer the SIP password); null otherwise / unknown */
    pbx_tech?: string | null
    settings: ExtSettings
}

/** `POST .../<ext>/sip-password` (chan_sip PBX): shown once, never stored anywhere in the app. */
export type SipPassword = {
    extension: string
    sip_username: string
    sip_password: string
    sip_server: string | null
    transport: string
    port: number
}

export type UserWithoutExtension = {
    user: ExtUserRef
    role: string | null
    legacy_extension: string | null
    request: ProvisionRequest | null
}

export const extProvisionApi = {
    overview: (q: { view?: ExtView; q?: string }, signal?: AbortSignal) =>
        v2Get<ExtOverview>('/v2/calling/pbx-extensions', q, signal),

    usersWithout: (q: { q?: string }, signal?: AbortSignal) =>
        v2Get<{ items: UserWithoutExtension[]; total: number }>('/v2/calling/pbx-extensions/users-without', q, signal),

    settings: (signal?: AbortSignal) => v2Get<ExtSettings>('/v2/calling/pbx-extensions/settings', undefined, signal),

    saveSettings: (
        body: { auto_provision: boolean; range_from: number; range_to: number; expected_revision: number; reason: string },
        key: string
    ) => v2Put<ExtSettings>('/v2/calling/pbx-extensions/settings', body, key),

    create: (body: { extension?: string; name?: string; user_id?: string; reason: string }, key: string) =>
        v2Post<ProvisionRequest>('/v2/calling/pbx-extensions', body, key),

    retry: (extension: string, key: string) =>
        v2Post<ProvisionRequest>(`/v2/calling/pbx-extensions/${enc(extension)}/retry`, {}, key),

    rename: (extension: string, body: { name: string; reason: string }, key: string) =>
        v2Post<{ extension: string; renamed: boolean; pbx: PbxExtensionInfo | null }>(
            `/v2/calling/pbx-extensions/${enc(extension)}/rename`,
            body,
            key
        ),

    /** 0082: the user's in-app softphone (off by default); off revokes their SIP credentials */
    setSoftphone: (userId: string, body: { enabled: boolean; reason: string }, key: string) =>
        v2Put<{ user_id: string; enabled: boolean; revision: number; credentials_revoked: number }>(
            `/v2/calling/users/${enc(userId)}/softphone`,
            body,
            key
        ),

    /** A new SIP password for an app-created desk-phone extension; NOT idempotent on the server
     * (the key the transport requires is sent and ignored). Keep the answer in component state only. */
    newSipPassword: (extension: string, body: { reason: string }, key: string) =>
        v2Post<SipPassword>(`/v2/calling/pbx-extensions/${enc(extension)}/sip-password`, body, key),

    remove: (extension: string, body: { reason: string }, key: string) =>
        v2Post<{ extension: string; deleted: boolean; cancelled: boolean }>(`/v2/calling/pbx-extensions/${enc(extension)}/delete`, body, key),
}
