/**
 * Target history of a user or a contract, shown under the legacy edit forms
 * (`UserEdit.tsx`, `ContractEdit.tsx`).
 *
 * - GET /v2/{users|contracts}/<id>/targets: the current Riyadh month, the legacy
 *   columns' current value, the target in force per month and every revision.
 *   Read keys: user `users_edit_target` | `users_details` (or self); contract
 *   `contracts` | `contracts_edit`.
 * - PUT /v2/{users|contracts}/<id>/targets/<YYYY-MM> {kind, value, reason,
 *   expected_revision}: edit keys `users_edit_target` / `contracts_edit`.
 *   Past months are locked (`409 month_locked`); the current month is mirrored
 *   into the legacy columns (`mirrored_to_legacy`), a future month is not.
 *
 * Targets are append-only: a save adds a revision, nothing is overwritten.
 * The month lock, the revision and the target in force are the API's.
 */
import classNames from 'classnames'
import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { HiOutlineLockClosed } from 'react-icons/hi'
import Button from '@/components/ui/Button'
import Input from '@/components/ui/Input'
import Table from '@/components/ui/Table'
import ToastNotification from '@/components/shared/myComponents/ToastNotification'
import { DialogField } from '@/components/v2/ActionDialog'
import { Field, Ltr, RiyadhTime } from '@/components/v2/Display'
import { PlainBadge } from '@/components/v2/Badges'
import { EmptyState, ErrorState, InlineError, Note, SkeletonLines } from '@/components/v2/States'
import { adminApi, describeP3Error, lookups } from '@/services/v2'
import type { TargetHistory, TargetKind, TargetSubject } from '@/services/v2'
import { MONEY_INPUT, formatCount } from '@/utils/v2/format'
import { useActionKey, usePermissions, useV2Query } from '@/utils/v2/hooks'
import { InForceText, TargetValueText } from './targetFormat'
import { MONTH_RE, compareDecimal, isPositiveDecimal, sourceLabel } from './targetUtils'

const { THead, TBody, Tr, Th, Td } = Table

export type TargetsPanelProps = { subject: TargetSubject; subjectId: string; onMirrored?: () => void }

const KINDS: TargetKind[] = ['amount', 'percent', 'none']
const PERCENT_INPUT = /^\d{1,3}(?:\.\d{1,4})?$/

/** The label block of a field that is not a single input (a `<label>` would click its first button). */
const FieldBlock = ({ label, required, hint, children }: { label: string; required?: boolean; hint?: React.ReactNode; children: React.ReactNode }) => (
    <div className="mb-4 block">
        <span className="mb-[7px] block text-[12.5px] font-extrabold text-navy-600 dark:text-gray-100">
            {label}
            {required && <span className="text-danger"> *</span>}
        </span>
        {children}
        {hint && <span className="mt-[7px] block text-[12px] text-gray-600">{hint}</span>}
    </div>
)

/* ------------------------------------------------------------------ set form */

const SetTargetForm = ({
    subject,
    subjectId,
    data,
    onSaved,
    onReload,
}: {
    subject: TargetSubject
    subjectId: string
    data: TargetHistory
    onSaved: (mirrored: boolean) => void
    onReload: () => void
}) => {
    const { t } = useTranslation()
    const current = data.current_month
    const [month, setMonth] = useState(current)
    const [kind, setKind] = useState<TargetKind>('amount')
    const [value, setValue] = useState('')
    const [reason, setReason] = useState('')
    const [busy, setBusy] = useState(false)
    const [error, setError] = useState<unknown>(null)
    const [touched, setTouched] = useState(false)
    // One key per save action: a retry after an error reuses it, a new save gets a new one.
    const { key, renew } = useActionKey(true)

    const validMonth = MONTH_RE.test(month)
    const locked = validMonth && month < current
    const isCurrent = month === current
    const row = data.months.find((m) => m.month === month)
    const expectedRevision = row ? row.revision : 0

    const v = value.trim()
    const valueErr =
        kind === 'none'
            ? ''
            : !v
            ? t('p3.targets.valueRequired')
            : kind === 'amount'
            ? !MONEY_INPUT.test(v)
                ? t('p3.targets.amountFormat')
                : !isPositiveDecimal(v)
                ? t('p3.targets.amountPositive')
                : ''
            : !PERCENT_INPUT.test(v)
            ? t('p3.targets.percentFormat')
            : !isPositiveDecimal(v) || compareDecimal(v, '100') > 0
            ? t('p3.targets.percentRange')
            : ''
    const canSave = validMonth && !locked && !valueErr && reason.trim().length > 0 && !busy

    const save = async () => {
        setTouched(true)
        if (!canSave) return
        setBusy(true)
        setError(null)
        try {
            const res = await adminApi.setTarget(
                subject,
                subjectId,
                month,
                {
                    kind,
                    ...(kind === 'none' ? {} : { value: v }),
                    reason: reason.trim(),
                    expected_revision: expectedRevision,
                },
                key
            )
            renew()
            setValue('')
            setReason('')
            setTouched(false)
            setBusy(false)
            ToastNotification(
                res.mirrored_to_legacy
                    ? String(t('p3.targets.savedMirrored', { month: res.month, revision: res.revision }))
                    : String(t('p3.targets.saved', { month: res.month, revision: res.revision })),
                String(t('p3.targets.title')),
                'success'
            )
            onSaved(res.mirrored_to_legacy)
        } catch (err) {
            setBusy(false)
            setError(err)
        }
    }

    return (
        <section className="mt-6 rounded-lg border border-gray-300 px-5 py-4 dark:border-night-border" data-testid="p3-target-form">
            <h6 className="mb-3 text-[15px] font-extrabold text-navy-600 dark:text-gray-100">{t('p3.targets.setTitle')}</h6>
            <div className="grid grid-cols-1 gap-x-6 md:grid-cols-2">
                <DialogField
                    label={t('p3.fields.month')}
                    required
                    hint={t('p3.targets.monthHint', { current })}
                >
                    <Input
                        size="sm"
                        type="month"
                        dir="ltr"
                        min={current}
                        value={month}
                        onChange={(e) => {
                            setMonth(e.target.value)
                            setError(null)
                        }}
                        data-testid="p3-target-month"
                        data-revision={expectedRevision}
                    />
                </DialogField>
                <FieldBlock label={String(t('p3.fields.kind'))} required>
                    <div
                        role="group"
                        aria-label={String(t('p3.fields.kind'))}
                        className="inline-flex h-8 overflow-hidden rounded-md border border-gray-400 dark:border-night-border"
                    >
                        {KINDS.map((k, i) => (
                            <button
                                key={k}
                                type="button"
                                aria-pressed={kind === k}
                                onClick={() => {
                                    setKind(k)
                                    setError(null)
                                    if (k === 'none') setValue('')
                                }}
                                data-testid={`p3-target-kind-${k}`}
                                className={classNames(
                                    'px-[14px] text-[12.5px]',
                                    i > 0 && 'border-s border-gray-400 dark:border-night-border',
                                    kind === k
                                        ? 'bg-navy-600 font-extrabold text-white dark:bg-azure-600'
                                        : 'bg-white text-gray-700 hover:bg-gray-50 dark:bg-night-card dark:text-night-body'
                                )}
                            >
                                {k === 'none'
                                    ? subject === 'contract'
                                        ? t('p3.targets.kindNoneContract')
                                        : t('p3.targets.kindNoneUser')
                                    : t(`p3.targets.kind.${k}`)}
                            </button>
                        ))}
                    </div>
                </FieldBlock>
                {kind !== 'none' ? (
                    <DialogField
                        label={kind === 'amount' ? t('p3.targets.valueAmount') : t('p3.targets.valuePercent')}
                        required
                        hint={
                            (touched || v) && valueErr ? (
                                <span className="text-danger">{valueErr}</span>
                            ) : kind === 'amount' ? (
                                t('p3.targets.amountHint')
                            ) : (
                                t('p3.targets.percentHint')
                            )
                        }
                    >
                        <Input
                            size="sm"
                            dir="ltr"
                            inputMode="decimal"
                            value={value}
                            invalid={!!v && !!valueErr}
                            onChange={(e) => setValue(e.target.value)}
                            data-testid="p3-target-value"
                        />
                    </DialogField>
                ) : (
                    <FieldBlock label={String(t('p3.fields.value'))}>
                        <Note>{subject === 'contract' ? t('p3.targets.noneHintContract') : t('p3.targets.noneHintUser')}</Note>
                    </FieldBlock>
                )}
                <DialogField label={t('p1.fields.reason')} required hint={t('p1.hint.reasonRequired')}>
                    <Input
                        size="sm"
                        value={reason}
                        maxLength={1000}
                        onChange={(e) => setReason(e.target.value)}
                        data-testid="p3-target-reason"
                    />
                </DialogField>
            </div>

            {locked ? (
                <div
                    role="note"
                    className="mb-3 flex items-start gap-2 rounded-md border border-gray-300 bg-gray-50 px-3 py-2 text-[13px] text-gray-700 dark:border-night-border dark:bg-night-raised dark:text-night-body"
                    data-testid="p3-target-locked"
                >
                    <HiOutlineLockClosed aria-hidden className="mt-0.5 shrink-0" />
                    <span>{t('p3.err.month_locked', { month, current_month: current })}</span>
                </div>
            ) : isCurrent ? (
                <Note className="mb-3">
                    <span data-testid="p3-target-mirror-note">{t('p3.targets.currentMonthNote')}</span>
                </Note>
            ) : validMonth ? (
                <Note className="mb-3">
                    <span data-testid="p3-target-future-note">{t('p3.targets.futureMonthNote', { month })}</span>
                </Note>
            ) : null}

            <InlineError
                error={error}
                describe={describeP3Error}
                onReload={() => {
                    setError(null)
                    onReload()
                }}
            />

            <div className="mt-3 flex flex-wrap items-center justify-end gap-3">
                <span className="text-[12px] text-gray-600">
                    {t('p3.targets.revisionHint', { revision: expectedRevision })}
                </span>
                <Button
                    size="sm"
                    variant="solid"
                    type="button"
                    loading={busy}
                    disabled={!canSave}
                    aria-disabled={!canSave}
                    onClick={save}
                    data-testid="p3-target-save"
                >
                    {t('p3.targets.save')}
                </Button>
            </div>
        </section>
    )
}

/* --------------------------------------------------------------------- panel */

const TargetsPanel = ({ subject, subjectId, onMirrored }: TargetsPanelProps) => {
    const { t } = useTranslation()
    const perms = usePermissions()
    const canEdit = subject === 'user' ? !!perms.users_edit_target : !!perms.contracts_edit
    const [tick, setTick] = useState(0)
    const q = useV2Query((signal) => adminApi.targets(subject, subjectId, signal), [subject, subjectId, tick])
    const data = q.data

    const [users, setUsers] = useState<Record<string, string>>({})
    useEffect(() => {
        let alive = true
        lookups
            .users()
            .then((list) => {
                if (!alive) return
                const map: Record<string, string> = {}
                list.forEach((u) => {
                    map[u.value] = u.label
                })
                setUsers(map)
            })
            .catch(() => undefined)
        return () => {
            alive = false
        }
    }, [])

    const history = useMemo(() => data?.history || [], [data])
    const lc = data?.legacy_current

    return (
        <section
            className="mt-6 rounded-lg border border-gray-300 bg-white px-5 py-4 dark:border-night-border dark:bg-night-card"
            data-testid={`p3-targets-${subject}`}
        >
            <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                <div>
                    <h5 className="mb-0 text-[17px] font-extrabold text-navy-600 dark:text-gray-100">{t('p3.targets.title')}</h5>
                    <div className="mt-1 text-[12.5px] text-gray-600">
                        {subject === 'contract' ? t('p3.targets.subtitleContract') : t('p3.targets.subtitleUser')}
                    </div>
                </div>
                <Button size="sm" type="button" loading={q.loading && !!data} onClick={q.reload} data-testid="p3-targets-refresh">
                    {t('p1.action.refresh')}
                </Button>
            </div>

            {q.loading && !data ? (
                <SkeletonLines rows={6} />
            ) : q.error && !data ? (
                <ErrorState error={q.error} onRetry={q.reload} describe={describeP3Error} />
            ) : data ? (
                <>
                    <div className="mb-4 grid grid-cols-1 gap-4 sm:grid-cols-3">
                        <Field label={t('p3.targets.currentMonth')}>
                            <Ltr className="font-extrabold text-navy-600 dark:text-gray-100">{data.current_month}</Ltr>
                        </Field>
                        <Field label={t('p3.targets.legacyCurrent')}>
                            <span data-testid="p3-target-legacy-current" data-kind={lc?.kind} data-value={lc?.value}>
                                {lc ? (
                                    <>
                                        {lc.kind !== 'none' && <>{t(`p3.targets.kind.${lc.kind}`)} </>}
                                        <TargetValueText kind={lc.kind} value={lc.value} subject={subject} />
                                    </>
                                ) : (
                                    '—'
                                )}
                            </span>
                        </Field>
                        <Note className="self-end">{t('p3.targets.legacyNote')}</Note>
                    </div>

                    {/* months */}
                    <h6 className="mb-2 text-[14px] font-extrabold text-navy-600 dark:text-gray-100">{t('p3.targets.monthsTitle')}</h6>
                    <div className="overflow-x-auto rounded-lg border border-gray-300 dark:border-night-border">
                        <Table data-testid="p3-target-months">
                            <THead>
                                <Tr>
                                    <Th>{t('p3.fields.month')}</Th>
                                    <Th>{t('p3.targets.col.inForce')}</Th>
                                    <Th>{t('p3.targets.col.fromMonth')}</Th>
                                    <Th>{t('p3.targets.col.source')}</Th>
                                    <Th className="text-end">{t('p3.targets.col.revision')}</Th>
                                </Tr>
                            </THead>
                            <TBody>
                                {data.months.map((m) => (
                                    <Tr key={m.month} data-testid="p3-target-month-row" data-month={m.month} data-locked={String(m.locked)} data-revision={m.revision}>
                                        <Td>
                                            <span className="flex flex-wrap items-center gap-2">
                                                <Ltr className="font-extrabold text-navy-600 dark:text-gray-100">{m.month}</Ltr>
                                                {m.month === data.current_month && <PlainBadge tone="info">{t('p3.targets.current')}</PlainBadge>}
                                                {m.locked && (
                                                    <PlainBadge tone="neutral">
                                                        <span className="inline-flex items-center gap-1">
                                                            <HiOutlineLockClosed aria-hidden />
                                                            {t('p3.targets.locked')}
                                                        </span>
                                                    </PlainBadge>
                                                )}
                                            </span>
                                        </Td>
                                        <Td>
                                            {m.in_force.known ? (
                                                <span>
                                                    {m.in_force.kind !== 'none' && <>{t(`p3.targets.kind.${m.in_force.kind}`)} </>}
                                                    <TargetValueText kind={m.in_force.kind} value={m.in_force.value} subject={subject} />
                                                </span>
                                            ) : (
                                                <InForceText target={m.in_force} subject={subject} className="font-extrabold text-warning" />
                                            )}
                                        </Td>
                                        <Td>
                                            <Ltr>{m.in_force.known ? m.in_force.from_month || '—' : '—'}</Ltr>
                                        </Td>
                                        <Td>{m.in_force.known ? sourceLabel(t, m.in_force.source) : '—'}</Td>
                                        <Td className="text-end">
                                            <Ltr>{formatCount(m.revision)}</Ltr>
                                        </Td>
                                    </Tr>
                                ))}
                            </TBody>
                        </Table>
                    </div>
                    <Note className="mt-2">{t('p3.targets.monthsNote')}</Note>

                    {canEdit ? (
                        <SetTargetForm
                            key={subjectId}
                            subject={subject}
                            subjectId={subjectId}
                            data={data}
                            onReload={q.reload}
                            onSaved={(mirrored) => {
                                setTick((n) => n + 1)
                                if (mirrored) onMirrored?.()
                            }}
                        />
                    ) : (
                        <Note className="mt-4">
                            <span data-testid="p3-target-readonly">
                                {subject === 'contract' ? t('p3.targets.readOnlyContract') : t('p3.targets.readOnlyUser')}
                            </span>
                        </Note>
                    )}

                    {/* full history */}
                    <h6 className="mb-2 mt-6 text-[14px] font-extrabold text-navy-600 dark:text-gray-100">{t('p3.targets.historyTitle')}</h6>
                    <div className="overflow-x-auto rounded-lg border border-gray-300 dark:border-night-border">
                        <Table data-testid="p3-target-history">
                            <THead>
                                <Tr>
                                    <Th>{t('p3.fields.month')}</Th>
                                    <Th className="text-end">{t('p3.targets.col.revision')}</Th>
                                    <Th>{t('p3.fields.kind')}</Th>
                                    <Th>{t('p3.fields.value')}</Th>
                                    <Th>{t('p3.targets.col.source')}</Th>
                                    <Th>{t('p3.targets.col.setAt')}</Th>
                                    <Th>{t('p3.targets.col.setBy')}</Th>
                                    <Th>{t('p1.col.reason')}</Th>
                                </Tr>
                            </THead>
                            <TBody>
                                {history.length === 0 ? (
                                    <tr>
                                        <td colSpan={8}>
                                            <EmptyState title={t('p3.targets.historyEmpty')} hint={t('p3.targets.historyEmptyHint')} />
                                        </td>
                                    </tr>
                                ) : (
                                    history.map((h) => (
                                        <Tr key={h.id} data-testid="p3-target-history-row" data-month={h.month} data-revision={h.revision} data-source={h.source}>
                                            <Td>
                                                <Ltr className="font-extrabold text-navy-600 dark:text-gray-100">{h.month}</Ltr>
                                            </Td>
                                            <Td className="text-end">
                                                <Ltr>{h.revision}</Ltr>
                                            </Td>
                                            <Td>{h.kind === 'none' ? t('p3.targets.kindNoneShort') : t(`p3.targets.kind.${h.kind}`)}</Td>
                                            <Td>{h.kind === 'none' ? '—' : <TargetValueText kind={h.kind} value={h.value} subject={subject} />}</Td>
                                            <Td>
                                                {h.imported_baseline ? (
                                                    <PlainBadge tone="neutral">{t('p3.targets.source.imported_baseline')}</PlainBadge>
                                                ) : (
                                                    sourceLabel(t, h.source)
                                                )}
                                            </Td>
                                            <Td>
                                                <RiyadhTime iso={h.set_at} />
                                            </Td>
                                            <Td>
                                                {h.set_by ? (
                                                    users[h.set_by] || <Ltr title={h.set_by}>{h.set_by.slice(0, 8)}…</Ltr>
                                                ) : (
                                                    <span className="text-gray-500">{t('p3.targets.system')}</span>
                                                )}
                                            </Td>
                                            <Td>
                                                <span dir="auto" className="break-words">
                                                    {h.reason || '—'}
                                                </span>
                                            </Td>
                                        </Tr>
                                    ))
                                )}
                            </TBody>
                        </Table>
                    </div>
                </>
            ) : null}
        </section>
    )
}

export default TargetsPanel
