/**
 * Report download (`GET …/report?language=ar|en[&approval_id=]`): a self-contained HTML file that
 * states its status (draft, AI draft, approved, superseded) and provenance. It is saved as a file,
 * never rendered inside the app. Every export is audited by the API.
 */
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { FileDown } from 'lucide-react'
import Button from '@/components/ui/Button'
import { describeP6Error, downloadQualityReport } from '@/services/v2'

const ReportButtons = ({ recordingId, approvalId }: { recordingId: string; approvalId?: string }) => {
    const { t } = useTranslation()
    const [busy, setBusy] = useState<'ar' | 'en' | null>(null)
    const [error, setError] = useState<unknown>(null)
    const [last, setLast] = useState<string | null>(null)
    const go = async (lang: 'ar' | 'en') => {
        setBusy(lang)
        setError(null)
        try {
            setLast(await downloadQualityReport(recordingId, lang, approvalId))
        } catch (e) {
            setError(e)
        } finally {
            setBusy(null)
        }
    }
    const suffix = approvalId ? '-approval' : ''
    return (
        <div className="flex flex-wrap items-center gap-2" data-testid={`p6-qc-report${suffix}`}>
            <Button size="sm" icon={<FileDown size={15} />} loading={busy === 'ar'} onClick={() => go('ar')} data-testid={`p6-qc-report-ar${suffix}`}>
                {t('p6.qc.report.ar')}
            </Button>
            <Button size="sm" icon={<FileDown size={15} />} loading={busy === 'en'} onClick={() => go('en')} data-testid={`p6-qc-report-en${suffix}`}>
                {t('p6.qc.report.en')}
            </Button>
            {last && !error && (
                <span className="text-[12px] text-gray-600" data-testid={`p6-qc-report-done${suffix}`}>
                    {t('p6.qc.report.saved', { name: last })}
                </span>
            )}
            {!!error && <span className="text-[12px] text-danger">{describeP6Error(error, t)}</span>}
        </div>
    )
}

export default ReportButtons
