/**
 * "New request" on a case (POST /v2/cases/<id>/requests, key `requests`).
 *
 * Department, subject, details, an optional amount (exact, 2 decimals) and
 * related date, and optional links to the case's existing documents (no new
 * file storage). One Idempotency-Key per opening of the dialog; the draft
 * stays when the API refuses it.
 */
import classNames from 'classnames'
import { useState } from 'react'
import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import Checkbox from '@/components/ui/Checkbox'
import Input from '@/components/ui/Input'
import ActionDialog, { DialogField } from '@/components/v2/ActionDialog'
import { Note } from '@/components/v2/States'
import { REQUEST_DEPARTMENTS, describeP3Error, requestsApi } from '@/services/v2'
import type { NewRequestBody, RequestDepartment, RequestDetail } from '@/services/v2'
import { MONEY_INPUT, isZeroMoney } from '@/utils/v2/format'
import { docLabel, useCaseDocuments } from './documents'

/** Like DialogField, but a <div>: a <label> around buttons or checkboxes would forward stray clicks. */
const Block = ({ label, required, hint, children }: { label: ReactNode; required?: boolean; hint?: ReactNode; children: ReactNode }) => (
    <div className="mb-4">
        <span className="mb-[7px] block text-[12.5px] font-extrabold text-navy-600 dark:text-gray-100">
            {label}
            {required && <span className="text-danger"> *</span>}
        </span>
        {children}
        {hint && <span className="mt-[7px] block text-[12px] text-gray-600">{hint}</span>}
    </div>
)

const SUBJECT_MAX = 200
const BODY_MAX = 5000

const NewRequestDialog = ({
    caseId,
    isOpen,
    onClose,
    onCreated,
}: {
    caseId: string
    isOpen: boolean
    onClose: () => void
    onCreated: (r: RequestDetail) => void
}) => {
    const { t } = useTranslation()
    const [department, setDepartment] = useState<RequestDepartment | null>(null)
    const [subject, setSubject] = useState('')
    const [body, setBody] = useState('')
    const [amount, setAmount] = useState('')
    const [date, setDate] = useState('')
    const [docIds, setDocIds] = useState<string[]>([])
    const docs = useCaseDocuments(caseId, isOpen)

    const s = subject.trim()
    const b = body.trim()
    const a = amount.trim()
    const amountErr = a && (!MONEY_INPUT.test(a) || isZeroMoney(a)) ? t('p3.req.new.amountFormat') : ''
    const canSubmit = !!department && !!s && !!b && s.length <= SUBJECT_MAX && b.length <= BODY_MAX && !amountErr

    const reset = () => {
        setDepartment(null)
        setSubject('')
        setBody('')
        setAmount('')
        setDate('')
        setDocIds([])
    }

    return (
        <ActionDialog
            isOpen={isOpen}
            width={640}
            title={t('p3.req.new.title')}
            confirmLabel={t('p3.req.new.submit')}
            canSubmit={canSubmit}
            testId="p3-req-new-dialog"
            describeError={describeP3Error}
            onClose={() => {
                reset()
                onClose()
            }}
            onSubmit={async (key) => {
                if (!department) return
                const payload: NewRequestBody = { department, subject: s, body: b }
                if (a) payload.amount = a
                if (date) payload.related_date = date
                if (docIds.length) payload.document_ids = docIds
                const created = await requestsApi.create(caseId, payload, key)
                reset()
                onCreated(created)
            }}
        >
            <Block label={t('p3.fields.department')} required>
                <div className="flex flex-wrap gap-2" role="radiogroup" aria-label={String(t('p3.fields.department'))}>
                    {REQUEST_DEPARTMENTS.map((d) => (
                        <button
                            key={d}
                            type="button"
                            role="radio"
                            aria-checked={department === d}
                            onClick={() => setDepartment(d)}
                            data-testid={`p3-req-new-dept-${d}`}
                            className={classNames(
                                'h-9 rounded-md border px-4 text-[13.5px] font-extrabold',
                                department === d
                                    ? 'border-navy-600 bg-navy-600 text-white dark:border-azure-400 dark:bg-night-raised'
                                    : 'border-gray-300 text-navy-600 hover:bg-gray-50 dark:border-night-border dark:text-gray-100'
                            )}
                        >
                            {t(`p3.req.dept.${d}`)}
                        </button>
                    ))}
                </div>
                {department && (
                    <span className="mt-[7px] block text-[12px] text-gray-600">{t(`p3.req.deptHint.${department}`)}</span>
                )}
            </Block>
            <DialogField
                label={t('p3.fields.subject')}
                required
                hint={t('p3.req.new.charsLeft', { n: SUBJECT_MAX - subject.length })}
            >
                <Input
                    value={subject}
                    maxLength={SUBJECT_MAX}
                    onChange={(e) => setSubject(e.target.value)}
                    data-testid="p3-req-new-subject"
                />
            </DialogField>
            <DialogField label={t('p3.fields.body')} required>
                <Input
                    textArea
                    value={body}
                    maxLength={BODY_MAX}
                    onChange={(e) => setBody(e.target.value)}
                    data-testid="p3-req-new-body"
                />
            </DialogField>
            <div className="grid grid-cols-1 gap-x-4 sm:grid-cols-2">
                <DialogField
                    label={t('p3.req.new.amountOptional')}
                    hint={amountErr ? <span className="text-danger">{amountErr}</span> : t('p3.req.new.amountHint')}
                >
                    <Input
                        dir="ltr"
                        inputMode="decimal"
                        value={amount}
                        invalid={!!amountErr}
                        onChange={(e) => setAmount(e.target.value)}
                        data-testid="p3-req-new-amount"
                    />
                </DialogField>
                <DialogField label={t('p3.req.new.dateOptional')} hint={t('p3.req.new.dateHint')}>
                    <Input
                        type="date"
                        dir="ltr"
                        value={date}
                        onChange={(e) => setDate(e.target.value)}
                        data-testid="p3-req-new-date"
                    />
                </DialogField>
            </div>
            <Block label={t('p3.req.new.documents')} hint={t('p3.req.new.documentsHint')}>
                <div className="rounded-md border border-gray-300 px-3 py-2 dark:border-night-border" data-testid="p3-req-new-docs">
                    {docs.loading ? (
                        <div className="text-[12.5px] text-gray-600">{t('p1.state.loading')}</div>
                    ) : !docs.available ? (
                        <div className="text-[12.5px] text-gray-600">{t('p3.req.docsUnavailable')}</div>
                    ) : docs.docs.length === 0 ? (
                        <div className="text-[12.5px] text-gray-600">{t('p3.req.noDocs')}</div>
                    ) : (
                        docs.docs.map((d) => (
                            <div key={d.id} className="py-1 text-[13px]">
                                <Checkbox
                                    checked={docIds.includes(d.id)}
                                    onChange={(v: boolean) =>
                                        setDocIds((ids) => (v ? [...ids, d.id] : ids.filter((x) => x !== d.id)))
                                    }
                                    data-testid="p3-req-new-doc"
                                    data-doc={d.id}
                                >
                                    <span dir="auto">{docLabel(d)}</span>
                                    <span className="ms-2 text-[12px] uppercase text-gray-600">{d.file_type}</span>
                                </Checkbox>
                            </div>
                        ))
                    )}
                </div>
            </Block>
            <Note>{t('p3.req.new.note')}</Note>
        </ActionDialog>
    )
}

export default NewRequestDialog
