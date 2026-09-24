/**
 * The reviewer's evidence for one recording (`PATCH /v2/quality/recordings/<id>`, key
 * `quality_review`): title, the template (version), the verified employee label, the employee's
 * notes, the recorded action and the context. Saved against the loaded revision (409 when stale).
 * Any evidence change (not the title) clears the current review and approval: the API keeps the
 * old approval in the history, labelled "superseded"; the form says so before saving.
 */
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import Button from '@/components/ui/Button'
import Input from '@/components/ui/Input'
import Select from '@/components/ui/Select'
import { DialogField } from '@/components/v2/ActionDialog'
import { InlineError } from '@/components/v2/States'
import ToastNotification from '@/components/shared/myComponents/ToastNotification'
import { describeP6Error, qualityApi } from '@/services/v2'
import type { Option, QcEvidenceBody, QcRecording, QcTemplate, Translate } from '@/services/v2'
import { useActionKey } from '@/utils/v2/hooks'
import { Notice } from '@/views/phase5/parts'
import { templateName } from '../labels'

const describe = (e: unknown, t: Translate) => describeP6Error(e, t)

type Form = { title: string; template: string; employee_label: string; notes: string; recorded_action: string; context: string }

const tplValue = (k: { key: string; version: number } | null | undefined) => (k ? `${k.key}@${k.version}` : '')

const fromRecording = (r: QcRecording): Form => ({
    title: r.title || '',
    template: tplValue(r.evidence.template),
    employee_label: r.evidence.employee_label || '',
    notes: r.evidence.notes || '',
    recorded_action: r.evidence.recorded_action || '',
    context: r.evidence.context || '',
})

const EvidenceForm = ({
    recording,
    templates,
    canEdit,
    onSaved,
    onReload,
}: {
    recording: QcRecording
    templates: QcTemplate[]
    canEdit: boolean
    onSaved: (r: QcRecording) => void
    onReload: () => void
}) => {
    const { t, i18n } = useTranslation()
    const [form, setForm] = useState<Form>(() => fromRecording(recording))
    const [busy, setBusy] = useState(false)
    const [error, setError] = useState<unknown>(null)
    const { key, renew } = useActionKey(true)
    useEffect(() => {
        setForm(fromRecording(recording))
        setError(null)
    }, [recording])
    const base = fromRecording(recording)
    const evidenceChanged = (['template', 'employee_label', 'notes', 'recorded_action', 'context'] as const).some((k) => form[k] !== base[k])
    const changed = evidenceChanged || form.title !== base.title
    const options: Option[] = templates.map((tpl) => ({ value: tplValue(tpl), label: `${templateName(tpl, i18n.language)} · v${tpl.version}` }))

    const save = async () => {
        setBusy(true)
        setError(null)
        const body: QcEvidenceBody = { expected_revision: recording.revision }
        if (form.title !== base.title) body.title = form.title.trim()
        if (form.template !== base.template) {
            const [k, v] = form.template.split('@')
            body.template = form.template ? { key: k, version: Number(v) } : null
        }
        ;(['employee_label', 'notes', 'recorded_action', 'context'] as const).forEach((f) => {
            if (form[f] !== base[f]) body[f] = form[f].trim() || null
        })
        try {
            const r = await qualityApi.updateEvidence(recording.id, body, key)
            renew()
            ToastNotification(String(t('p6.qc.evidence.saved')), String(t('p6.qc.title')), 'success', 2000)
            onSaved(r)
        } catch (e) {
            setError(e)
        } finally {
            setBusy(false)
        }
    }

    return (
        <div data-testid="p6-qc-evidence">
            <p className="mb-4 text-[13px] text-gray-700 dark:text-night-body">{t('p6.qc.evidence.hint')}</p>
            <div className="grid grid-cols-1 gap-x-4 md:grid-cols-2">
                <DialogField label={t('p6.qc.titleField')} required>
                    <Input size="sm" maxLength={200} disabled={!canEdit} value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} data-testid="p6-qc-ev-title" />
                </DialogField>
                <DialogField label={t('p6.qc.template')} required hint={t('p6.qc.evidence.templateHint')}>
                    <div data-testid="p6-qc-ev-template">
                        <Select
                            size="sm"
                            menuPosition="fixed"
                            isDisabled={!canEdit}
                            placeholder={t('p1.followUps.select')}
                            options={options}
                            value={options.find((o) => o.value === form.template) || null}
                            onChange={(o) => setForm({ ...form, template: (o as Option | null)?.value || '' })}
                        />
                    </div>
                </DialogField>
                <DialogField label={t('p6.qc.evidence.employee')} hint={t('p6.qc.evidence.employeeHint')}>
                    <Input size="sm" maxLength={100} disabled={!canEdit} value={form.employee_label} onChange={(e) => setForm({ ...form, employee_label: e.target.value })} data-testid="p6-qc-ev-employee" />
                </DialogField>
                <DialogField label={t('p6.qc.evidence.action')} hint={t('p6.qc.evidence.actionHint')}>
                    <Input size="sm" maxLength={1000} disabled={!canEdit} value={form.recorded_action} onChange={(e) => setForm({ ...form, recorded_action: e.target.value })} data-testid="p6-qc-ev-action" />
                </DialogField>
                <DialogField label={t('p6.qc.evidence.notes')} hint={t('p6.qc.evidence.notesHint')}>
                    <Input textArea dir="auto" maxLength={4000} disabled={!canEdit} value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} data-testid="p6-qc-ev-notes" />
                </DialogField>
                <DialogField label={t('p6.qc.evidence.context')} hint={t('p6.qc.evidence.contextHint')}>
                    <Input textArea dir="auto" maxLength={4000} disabled={!canEdit} value={form.context} onChange={(e) => setForm({ ...form, context: e.target.value })} data-testid="p6-qc-ev-context" />
                </DialogField>
            </div>
            {evidenceChanged && (recording.review || recording.approval) && (
                <Notice tone="warning" className="mb-3" testId="p6-qc-ev-clears">
                    {recording.approval ? t('p6.qc.evidence.clearsApproval') : t('p6.qc.evidence.clearsReview')}
                </Notice>
            )}
            <InlineError error={error} describe={describe} onReload={onReload} />
            {canEdit && (
                <div className="mt-3 flex justify-end gap-2">
                    <Button size="sm" disabled={!changed || busy} aria-disabled={!changed || busy} onClick={() => setForm(base)}>
                        {t('p6.policy.reset')}
                    </Button>
                    <Button size="sm" variant="solid" loading={busy} disabled={!changed || !form.title.trim()} aria-disabled={!changed || !form.title.trim()} onClick={save} data-testid="p6-qc-ev-save">
                        {t('p6.qc.evidence.save')}
                    </Button>
                </div>
            )}
        </div>
    )
}

export default EvidenceForm
