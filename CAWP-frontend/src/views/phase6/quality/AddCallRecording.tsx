/**
 * "Add call recording to quality review" (`POST /v2/quality/recordings/from-call`), next to a
 * call's PBX recording on the call detail (the Calls screen and the case Calls tab).
 *
 * A call recording is never copied into the QC library without this explicit, confirmed action.
 * The API also requires a recording key (`clints_callsRecords_play` / `_download`) and the call's
 * case in the caller's scope, reads the file through the scoped, audited Phase 5 path and keeps
 * the provenance. Without a recording key the button is disabled with the reason; every other
 * refusal (not in scope, no such recording, several recordings) is the API's, shown in words.
 */
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { ClipboardCheck } from 'lucide-react'
import Button from '@/components/ui/Button'
import Input from '@/components/ui/Input'
import ActionDialog, { DialogField } from '@/components/v2/ActionDialog'
import { Ltr } from '@/components/v2/Display'
import { describeP6Error, qualityApi } from '@/services/v2'
import type { QcImportResult, Translate } from '@/services/v2'
import { usePermissions } from '@/utils/v2/hooks'
import { Notice } from '@/views/phase5/parts'
import { CheckField } from '../parts'
import { qualityLink } from './links'

const RECORDING_KEYS = ['clints_callsRecords_play', 'clints_callsRecords_download']
const describe = (e: unknown, t: Translate) => describeP6Error(e, t)

const AddCallRecording = ({ attemptId, recordingPath }: { attemptId: string; recordingPath?: string | null }) => {
    const { t } = useTranslation()
    const perms = usePermissions()
    const [open, setOpen] = useState(false)
    const [title, setTitle] = useState('')
    const [confirmed, setConfirmed] = useState(false)
    const [done, setDone] = useState<QcImportResult | null>(null)
    if (!perms.quality_review) return null
    const hasKey = RECORDING_KEYS.some((k) => perms[k])
    if (done) {
        return (
            <span className="inline-flex items-center gap-1 text-[12px]" data-testid="p6-qc-added" data-recording={done.recording.id} data-duplicate={done.duplicate ? '1' : '0'}>
                <ClipboardCheck aria-hidden size={13} className="text-success" />
                <Link className="font-extrabold text-azure-600 hover:underline" to={qualityLink(done.recording.id)}>
                    {done.duplicate ? t('p6.qc.fromCall.duplicateLink') : t('p6.qc.fromCall.addedLink')}
                </Link>
            </span>
        )
    }
    return (
        <>
            <Button
                size="xs"
                icon={<ClipboardCheck size={13} />}
                disabled={!hasKey}
                aria-disabled={!hasKey}
                title={hasKey ? String(t('p6.qc.fromCall.button')) : String(t('p6.qc.fromCall.noKey'))}
                onClick={() => {
                    setTitle('')
                    setConfirmed(false)
                    setOpen(true)
                }}
                data-testid="p6-qc-add-call"
                data-enabled={hasKey ? '1' : '0'}
            >
                {t('p6.qc.fromCall.button')}
            </Button>
            {!hasKey && (
                <span className="text-[11.5px] text-gray-600" data-testid="p6-qc-add-call-nokey">
                    {t('p6.qc.fromCall.noKey')}
                </span>
            )}
            <ActionDialog
                isOpen={open}
                title={t('p6.qc.fromCall.title')}
                confirmLabel={t('p6.qc.fromCall.confirm')}
                canSubmit={confirmed}
                testId="p6-qc-add-call-dialog"
                describeError={describe}
                onClose={() => setOpen(false)}
                onSubmit={async (key) => {
                    const res = await qualityApi.fromCall(
                        {
                            attempt_id: attemptId,
                            ...(recordingPath ? { recording_path: recordingPath } : {}),
                            ...(title.trim() ? { title: title.trim() } : {}),
                            confirmed: true,
                        },
                        key
                    )
                    setDone(res)
                }}
            >
                <Notice tone="info" className="mb-3">
                    {t('p6.qc.fromCall.explain')}
                </Notice>
                {recordingPath && (
                    <div className="mb-3 text-[12.5px] text-gray-600">
                        {t('p6.qc.fromCall.file')}: <Ltr className="break-all">{recordingPath}</Ltr>
                    </div>
                )}
                <DialogField label={t('p6.qc.titleField')} hint={t('p6.qc.fromCall.titleHint')}>
                    <Input size="sm" maxLength={200} value={title} onChange={(e) => setTitle(e.target.value)} data-testid="p6-qc-add-call-title" />
                </DialogField>
                <CheckField checked={confirmed} onChange={setConfirmed} testId="p6-qc-add-call-confirm">
                    <span className="text-[13.5px]">{t('p6.qc.fromCall.confirmCheck')}</span>
                </CheckField>
            </ActionDialog>
        </>
    )
}

export default AddCallRecording
