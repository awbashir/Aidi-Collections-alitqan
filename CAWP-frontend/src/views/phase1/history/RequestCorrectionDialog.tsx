/**
 * Request a reviewed case correction (POST /v2/cases/<id>/corrections).
 *
 * One form per kind; only fields that really change are sent (the API answers
 * 422 no_change otherwise). A reason is always required. Amounts are typed
 * text validated with MONEY_INPUT and sent as strings. The correction is only
 * applied when another user with `case_corrections_review` approves it.
 */
import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { HiOutlinePlus, HiOutlineTrash, HiOutlineReply } from 'react-icons/hi'
import Button from '@/components/ui/Button'
import Input from '@/components/ui/Input'
import Select from '@/components/ui/Select'
import ToastNotification from '@/components/shared/myComponents/ToastNotification'
import { CORRECTION_KINDS, historyApi } from '@/services/v2'
import type { CorrectionKind } from '@/services/v2'
import { MONEY_INPUT, formatMoney } from '@/utils/v2/format'
import { Ltr } from '@/components/v2/Display'
import ActionDialog, { DialogField } from '@/components/v2/ActionDialog'
import type { CaseInfo } from '../caseTypes'
import { Banner, useDelinquencyReasons } from './parts'

export type RequestCorrectionDialogProps = {
    isOpen: boolean
    onClose: () => void
    caseData: CaseInfo
    onDone?: () => void
}

type AttrRow = {
    key: string
    original: unknown
    value: string
    removed: boolean
}

type NewAttr = { key: string; value: string }

const IDENTITY_RE = /^[12]\d{9}$/
const NUMBER_RE = /^-?\d+(\.\d+)?$/

const attrText = (v: unknown) => (v === null || v === undefined ? '' : typeof v === 'object' ? JSON.stringify(v) : String(v))

/** Keep a number a number and a boolean a boolean when the user did not change its kind. */
const attrValue = (original: unknown, text: string): unknown => {
    if (typeof original === 'number' && NUMBER_RE.test(text.trim())) return Number(text.trim())
    if (typeof original === 'boolean' && (text === 'true' || text === 'false')) return text === 'true'
    return text
}

const NO_REASON = '__none__'

const RequestCorrectionDialog = ({ isOpen, onClose, caseData, onDone }: RequestCorrectionDialogProps) => {
    const { t } = useTranslation()
    const delinquency = useDelinquencyReasons()
    const [kind, setKind] = useState<CorrectionKind>('profile')
    const [reason, setReason] = useState('')
    const [identityNumber, setIdentityNumber] = useState('')
    const [accountNumber, setAccountNumber] = useState('')
    const [name, setName] = useState('')
    const [amount, setAmount] = useState('')
    const [reasonId, setReasonId] = useState<string>(NO_REASON)
    const [attrs, setAttrs] = useState<AttrRow[]>([])
    const [added, setAdded] = useState<NewAttr[]>([])

    const currentDebt = caseData.amount === null || caseData.amount === undefined ? '' : String(caseData.amount)
    const currentReason = caseData.delinquencyReason_id || NO_REASON

    // A fresh draft from the case each time the dialog opens.
    useEffect(() => {
        if (!isOpen) return
        setKind('profile')
        setReason('')
        setIdentityNumber(caseData.identityNumber || '')
        setAccountNumber(caseData.accountNumber || '')
        setName(caseData.name || '')
        setAmount('')
        setReasonId(caseData.delinquencyReason_id || NO_REASON)
        setAttrs(
            Object.entries(caseData.attribute || {}).map(([key, v]) => ({
                key,
                original: v,
                value: attrText(v),
                removed: false,
            }))
        )
        setAdded([])
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [isOpen])

    /** The `changes` body for the current kind, or null when nothing changed / invalid. */
    const built = useMemo((): { changes: Record<string, unknown> | null; problem?: unknown } => {
        if (kind === 'identity') {
            const changes: Record<string, unknown> = {}
            const idn = identityNumber.trim()
            const acc = accountNumber.trim()
            if (idn !== (caseData.identityNumber || '').trim()) {
                if (!IDENTITY_RE.test(idn)) return { changes: null, problem: t('p1.corr.form.identityInvalid') }
                changes.identityNumber = idn
            }
            if (acc !== (caseData.accountNumber || '').trim()) {
                if (!acc || /\s/.test(acc) || acc.length > 64)
                    return { changes: null, problem: t('p1.corr.form.accountInvalid') }
                changes.accountNumber = acc
            }
            return { changes: Object.keys(changes).length ? changes : null }
        }
        if (kind === 'profile') {
            const v = name.trim()
            if (!v || v.length > 200) return { changes: null, problem: v ? t('p1.corr.form.nameTooLong') : undefined }
            return { changes: v !== (caseData.name || '').trim() ? { name: v } : null }
        }
        if (kind === 'original_debt') {
            const v = amount.trim()
            if (!v) return { changes: null }
            if (!MONEY_INPUT.test(v) || /^0+(\.0+)?$/.test(v))
                return { changes: null, problem: t('p1.corr.form.amountInvalid') }
            return { changes: { amount: v } }
        }
        if (kind === 'delinquency') {
            if (reasonId === currentReason) return { changes: null }
            return { changes: { delinquencyReason_id: reasonId === NO_REASON ? null : reasonId } }
        }
        // attribute
        const changes: Record<string, unknown> = {}
        attrs.forEach((row) => {
            if (row.removed) {
                if (row.original !== null && row.original !== undefined) changes[row.key] = null
                return
            }
            if (row.value !== attrText(row.original)) changes[row.key] = attrValue(row.original, row.value)
        })
        const existing = new Set(attrs.map((r) => r.key))
        for (const row of added) {
            const key = row.key.trim()
            if (!key && !row.value) continue
            if (!key) return { changes: null, problem: t('p1.corr.form.attrKeyRequired') }
            if (existing.has(key) || key in changes)
                return { changes: null, problem: t('p1.corr.form.attrKeyExists', { key }) }
            changes[key] = row.value
        }
        return { changes: Object.keys(changes).length ? changes : null }
    }, [kind, identityNumber, accountNumber, name, amount, reasonId, attrs, added, caseData, currentReason, t])

    const canSubmit = !!built.changes && reason.trim().length > 0

    const kindOptions = CORRECTION_KINDS.map((k) => ({ value: k, label: t(`p1.corrKind.${k}`) }))
    const reasonOptions = [
        { value: NO_REASON, label: t('p1.corr.form.noDelinquency') },
        ...delinquency.options,
    ]

    return (
        <ActionDialog
            isOpen={isOpen}
            width={640}
            title={t('p1.corr.requestTitle')}
            confirmLabel={t('p1.corr.submitRequest')}
            canSubmit={canSubmit}
            testId="p1-corr-request-dialog"
            onClose={onClose}
            onSubmit={async (key) => {
                if (!built.changes) return
                await historyApi.requestCorrection(
                    caseData.id,
                    { kind, changes: built.changes, reason: reason.trim() },
                    key
                )
                ToastNotification(t('p1.corr.requestedToast'), t('p1.corr.title'), 'success')
                onDone?.()
            }}
        >
            <Banner tone="info" className="mb-4">
                {t('p1.corr.requestHint')}
            </Banner>

            <DialogField label={t('p1.fields.kind')} required>
                <div data-testid="p1-corr-kind">
                    <Select
                        size="sm"
                        isSearchable={false}
                        options={kindOptions}
                        value={kindOptions.find((o) => o.value === kind) || null}
                        onChange={(o) => o && setKind((o as { value: CorrectionKind }).value)}
                    />
                </div>
            </DialogField>

            {kind === 'identity' && (
                <div className="grid grid-cols-1 gap-x-4 sm:grid-cols-2" data-testid="p1-corr-form-identity">
                    <DialogField
                        label={t('p1.fields.identityNumber')}
                        hint={
                            <>
                                {t('p1.corr.form.current')} <Ltr>{caseData.identityNumber || '—'}</Ltr>
                            </>
                        }
                    >
                        <Input
                            size="sm"
                            dir="ltr"
                            value={identityNumber}
                            maxLength={10}
                            onChange={(e) => setIdentityNumber(e.target.value)}
                            data-testid="p1-corr-identity"
                        />
                    </DialogField>
                    <DialogField
                        label={t('p1.fields.accountNumber')}
                        hint={
                            <>
                                {t('p1.corr.form.current')} <Ltr>{caseData.accountNumber || '—'}</Ltr>
                            </>
                        }
                    >
                        <Input
                            size="sm"
                            dir="ltr"
                            value={accountNumber}
                            maxLength={64}
                            onChange={(e) => setAccountNumber(e.target.value)}
                            data-testid="p1-corr-account"
                        />
                    </DialogField>
                    <div className="sm:col-span-2">
                        <p className="mb-4 text-[12.5px] text-gray-600">{t('p1.corr.form.identityHint')}</p>
                    </div>
                </div>
            )}

            {kind === 'profile' && (
                <DialogField
                    label={t('p1.fields.name')}
                    hint={
                        <>
                            {t('p1.corr.form.current')} <span dir="auto">{caseData.name || '—'}</span>
                        </>
                    }
                >
                    <Input
                        size="sm"
                        dir="auto"
                        value={name}
                        maxLength={200}
                        onChange={(e) => setName(e.target.value)}
                        data-testid="p1-corr-name"
                    />
                </DialogField>
            )}

            {kind === 'original_debt' && (
                <DialogField
                    label={`${t('p1.corr.form.newDebt')} (${t('p1.currencyCode')})`}
                    hint={
                        <>
                            {t('p1.corr.form.currentDebt')}{' '}
                            <Ltr className="font-extrabold">{formatMoney(currentDebt)}</Ltr> {t('p1.currencyCode')} ·{' '}
                            {t('p1.corr.form.debtFloorHint')}
                        </>
                    }
                >
                    <Input
                        size="sm"
                        dir="ltr"
                        inputMode="decimal"
                        placeholder="0.00"
                        value={amount}
                        invalid={!!amount && !MONEY_INPUT.test(amount.trim())}
                        onChange={(e) => setAmount(e.target.value)}
                        data-testid="p1-corr-amount"
                    />
                </DialogField>
            )}

            {kind === 'delinquency' && (
                <DialogField
                    label={t('p1.fields.delinquencyReason_id')}
                    hint={
                        <>
                            {t('p1.corr.form.current')}{' '}
                            {reasonOptions.find((o) => o.value === currentReason)?.label || '—'}
                        </>
                    }
                >
                    <div data-testid="p1-corr-delinquency">
                        <Select
                            size="sm"
                            isLoading={delinquency.loading}
                            options={reasonOptions}
                            value={reasonOptions.find((o) => o.value === reasonId) || null}
                            onChange={(o) => o && setReasonId((o as { value: string }).value)}
                        />
                    </div>
                </DialogField>
            )}

            {kind === 'attribute' && (
                <div className="mb-4" data-testid="p1-corr-form-attribute">
                    <div className="mb-[7px] text-[12.5px] font-extrabold text-navy-600 dark:text-gray-100">
                        {t('p1.corr.form.attributes')}
                    </div>
                    {attrs.length === 0 && added.length === 0 && (
                        <p className="mb-2 text-[13px] text-gray-600">{t('p1.corr.form.noAttributes')}</p>
                    )}
                    <div className="flex flex-col gap-2">
                        {attrs.map((row, i) => (
                            <div key={row.key} className="flex items-center gap-2" data-testid="p1-corr-attr-row" data-key={row.key}>
                                <span
                                    dir="auto"
                                    title={row.key}
                                    className="w-[34%] shrink-0 truncate text-[13px] font-extrabold text-gray-700 dark:text-night-body"
                                >
                                    {row.key}
                                </span>
                                {row.removed ? (
                                    <span className="flex h-9 flex-1 items-center text-[13px] italic text-danger line-through">
                                        <span dir="auto">{attrText(row.original) || t('p1.corr.emptyValue')}</span>
                                    </span>
                                ) : (
                                    <Input
                                        size="sm"
                                        dir="auto"
                                        className="flex-1"
                                        value={row.value}
                                        maxLength={2000}
                                        onChange={(e) =>
                                            setAttrs((prev) =>
                                                prev.map((r, j) => (j === i ? { ...r, value: e.target.value } : r))
                                            )
                                        }
                                    />
                                )}
                                <Button
                                    size="xs"
                                    variant="plain"
                                    type="button"
                                    icon={row.removed ? <HiOutlineReply /> : <HiOutlineTrash />}
                                    aria-label={String(row.removed ? t('p1.corr.form.undoRemove') : t('p1.corr.form.removeKey'))}
                                    title={String(row.removed ? t('p1.corr.form.undoRemove') : t('p1.corr.form.removeKey'))}
                                    onClick={() =>
                                        setAttrs((prev) =>
                                            prev.map((r, j) =>
                                                j === i ? { ...r, removed: !r.removed, value: attrText(r.original) } : r
                                            )
                                        )
                                    }
                                    data-testid="p1-corr-attr-remove"
                                />
                            </div>
                        ))}
                        {added.map((row, i) => (
                            <div key={`new-${i}`} className="flex items-center gap-2" data-testid="p1-corr-attr-new">
                                <Input
                                    size="sm"
                                    dir="auto"
                                    className="w-[34%] shrink-0"
                                    placeholder={String(t('p1.corr.form.newKey'))}
                                    value={row.key}
                                    maxLength={200}
                                    onChange={(e) =>
                                        setAdded((prev) => prev.map((r, j) => (j === i ? { ...r, key: e.target.value } : r)))
                                    }
                                />
                                <Input
                                    size="sm"
                                    dir="auto"
                                    className="flex-1"
                                    placeholder={String(t('p1.corr.form.newValue'))}
                                    value={row.value}
                                    maxLength={2000}
                                    onChange={(e) =>
                                        setAdded((prev) =>
                                            prev.map((r, j) => (j === i ? { ...r, value: e.target.value } : r))
                                        )
                                    }
                                />
                                <Button
                                    size="xs"
                                    variant="plain"
                                    type="button"
                                    icon={<HiOutlineTrash />}
                                    aria-label={String(t('p1.corr.form.removeKey'))}
                                    onClick={() => setAdded((prev) => prev.filter((_, j) => j !== i))}
                                />
                            </div>
                        ))}
                    </div>
                    <Button
                        size="xs"
                        type="button"
                        className="mt-2"
                        icon={<HiOutlinePlus />}
                        onClick={() => setAdded((prev) => [...prev, { key: '', value: '' }])}
                        data-testid="p1-corr-attr-add"
                    >
                        {t('p1.corr.form.addKey')}
                    </Button>
                </div>
            )}

            {!!built.problem && (
                <p className="mb-3 text-[12.5px] font-extrabold text-danger" data-testid="p1-corr-form-problem">
                    {String(built.problem)}
                </p>
            )}

            <DialogField label={t('p1.fields.reason')} required hint={t('p1.hint.reasonRequired')}>
                <Input
                    textArea
                    value={reason}
                    maxLength={2000}
                    onChange={(e) => setReason(e.target.value)}
                    data-testid="p1-corr-request-dialog-reason"
                />
            </DialogField>
        </ActionDialog>
    )
}

export default RequestCorrectionDialog
