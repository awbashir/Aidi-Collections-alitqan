/**
 * Portfolio upload wizard ("رفع المحافظ"), replacing the legacy add-by-Excel dialog on /clients.
 *
 *  1. contract + portfolio name + file (private upload, inspected; blank headers become "Column C")
 *  2. column mapping (server guess for Arabic/English headers, contact columns, optional collector)
 *  3. preview: new / from archive / skipped existing / rejected / unmatched collector + problem rows
 *  4. confirm -> progress -> result (counts, link to the portfolio's clients, XLSX report)
 *
 * One upload can create at most one portfolio: the API keeps one import record per upload, so
 * a failed import is retried on that record and a succeeded one can never run again. The
 * confirm button is disabled while the import runs and after it succeeded.
 */
import classNames from 'classnames'
import { useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'
import { HiOutlineDownload, HiOutlineExclamation, HiOutlineUpload } from 'react-icons/hi'
import Button from '@/components/ui/Button'
import Dialog from '@/components/ui/Dialog'
import Input from '@/components/ui/Input'
import Select from '@/components/ui/Select'
import Spinner from '@/components/ui/Spinner'
import { InlineError, Note } from '@/components/v2/States'
import { isV2Error, lookups, newIdempotencyKey } from '@/services/v2'
import type { Option, UploadSheet } from '@/services/v2'
import { downloadImportReport, portfolioImportApi } from '@/services/v2/portfolioImportApi'
import type {
    ImportCatalogue,
    ImportMapping,
    ImportPreview,
    ImportUpload,
    PortfolioImport,
} from '@/services/v2/portfolioImportApi'
import { formatCount } from '@/utils/v2/format'
import ImportMappingStep from './ImportMappingStep'
import { CountTiles, ProblemsTable } from './ImportSummary'
import {
    EMPTY_MAPPING,
    cleanMapping,
    describeFailure,
    describeImportError,
    guessFor,
    mappingProblems,
} from './importLogic'

type Step = 'setup' | 'mapping' | 'preview' | 'run'
const STEPS: Step[] = ['setup', 'mapping', 'preview', 'run']
const POLL_MS = 1500

const StepHeader = ({ step }: { step: Step }) => {
    const { t } = useTranslation()
    const at = STEPS.indexOf(step)
    return (
        <ol className="mb-4 flex flex-wrap items-center gap-2 text-[12.5px]" data-testid="p7-import-steps">
            {STEPS.map((s, i) => (
                <li
                    key={s}
                    aria-current={s === step ? 'step' : undefined}
                    className={classNames(
                        'rounded-pill border px-3 py-1 font-extrabold',
                        i === at && 'border-azure-500 bg-azure-50 text-azure-700 dark:bg-night-raised',
                        i < at && 'border-gray-300 text-navy-600 dark:text-gray-100',
                        i > at && 'border-gray-200 text-gray-500'
                    )}
                >
                    {i + 1}. {t(`p7.import.step.${s}`)}
                </li>
            ))}
        </ol>
    )
}

const PortfolioImportDialog = ({
    isOpen,
    onClose,
    onImported,
}: {
    isOpen: boolean
    onClose: () => void
    onImported?: (imp: PortfolioImport) => void
}) => {
    const { t } = useTranslation()
    const navigate = useNavigate()
    const fileInput = useRef<HTMLInputElement>(null)

    const [step, setStep] = useState<Step>('setup')
    const [contracts, setContracts] = useState<Array<Option & { is_active?: boolean }>>([])
    const [contractId, setContractId] = useState<string>('')
    const [name, setName] = useState('')
    const [catalogue, setCatalogue] = useState<ImportCatalogue | null>(null)
    const [upload, setUpload] = useState<ImportUpload | null>(null)
    const [sheetName, setSheetName] = useState<string | undefined>(undefined)
    const [mapping, setMapping] = useState<ImportMapping>(EMPTY_MAPPING)
    const [showProblems, setShowProblems] = useState(false)
    const [preview, setPreview] = useState<ImportPreview | null>(null)
    const [imp, setImp] = useState<PortfolioImport | null>(null)
    const [busy, setBusy] = useState(false)
    const [error, setError] = useState<unknown>(null)
    /* One key per confirm action: a double click or a lost response replays, never re-runs. */
    const [commitKey, setCommitKey] = useState(() => newIdempotencyKey())
    const notified = useRef<string | null>(null)

    useEffect(() => {
        if (!isOpen) return
        lookups.contracts().then(setContracts).catch(() => setContracts([]))
        portfolioImportApi.fields().then(setCatalogue).catch(setError)
    }, [isOpen])

    const sheet: UploadSheet | undefined = useMemo(() => {
        if (!upload) return undefined
        return upload.sheets.find((s) => s.name === sheetName) || upload.sheets.find((s) => s.rows > 0) || upload.sheets[0]
    }, [upload, sheetName])

    const running = imp?.status === 'queued' || imp?.status === 'running'
    const succeeded = imp?.status === 'succeeded'

    /* poll the import record until it finishes */
    useEffect(() => {
        if (!imp || !running) return undefined
        const ctrl = new AbortController()
        const timer = setTimeout(() => {
            portfolioImportApi
                .get(imp.id, ctrl.signal)
                .then(setImp)
                .catch((e) => {
                    if ((e as { code?: string })?.code !== 'canceled') setError(e)
                })
        }, POLL_MS)
        return () => {
            clearTimeout(timer)
            ctrl.abort()
        }
    }, [imp, running])

    useEffect(() => {
        if (imp && succeeded && notified.current !== imp.id) {
            notified.current = imp.id
            onImported?.(imp)
        }
    }, [imp, succeeded, onImported])

    const body = () => ({
        upload_id: upload?.id || '',
        sheet: sheet?.name,
        contract_id: contractId,
        portfolio_name: name.trim(),
        mapping: cleanMapping(mapping),
    })

    const chooseFile = async (files: FileList | null) => {
        const file = files && files[0]
        if (!file) return
        setBusy(true)
        setError(null)
        setUpload(null)
        setPreview(null)
        try {
            const up = await portfolioImportApi.upload(file, newIdempotencyKey())
            setUpload(up)
            setSheetName(up.guess_sheet)
            const first = up.sheets.find((s) => s.name === up.guess_sheet)
            setMapping(guessFor(up.guess, first?.headers || []))
        } catch (e) {
            setError(e)
        } finally {
            setBusy(false)
        }
    }

    const chooseSheet = async (nameOfSheet: string) => {
        if (!upload) return
        setSheetName(nameOfSheet)
        setPreview(null)
        try {
            const c = await portfolioImportApi.fields({ upload_id: upload.id, sheet: nameOfSheet })
            setMapping(guessFor(c.guess, c.headers || []))
        } catch (e) {
            setError(e)
        }
    }

    const runPreview = async () => {
        if (!catalogue) return
        setShowProblems(true)
        if (mappingProblems(catalogue.fields, mapping).length > 0) return
        setBusy(true)
        setError(null)
        try {
            const p = await portfolioImportApi.preview(body(), newIdempotencyKey())
            setPreview(p)
            setStep('preview')
        } catch (e) {
            setError(e)
        } finally {
            setBusy(false)
        }
    }

    const runCommit = async (key: string = commitKey) => {
        if (busy || running || succeeded) return
        setBusy(true)
        setError(null)
        try {
            const started = await portfolioImportApi.commit(body(), key)
            setImp(started)
            setStep('run')
            /* the next confirm (a retry after a failure) is a new action */
            setCommitKey(newIdempotencyKey())
        } catch (e) {
            /* another tab/click already started this file: follow that import instead */
            const detail = isV2Error(e) ? (e.detail as { import_id?: string } | null) : null
            if (isV2Error(e) && (e.code === 'import_in_progress' || e.code === 'already_imported') && detail?.import_id) {
                try {
                    setImp(await portfolioImportApi.get(detail.import_id))
                    setStep('run')
                } catch {
                    /* keep the original error */
                }
            }
            setError(e)
            setCommitKey(newIdempotencyKey())
        } finally {
            setBusy(false)
        }
    }

    /* Retry runs the SAME import record again (the API reuses it for this upload). */
    const retry = () => {
        const key = newIdempotencyKey()
        setCommitKey(key)
        runCommit(key)
    }

    const close = () => {
        if (busy) return
        onClose()
    }

    const canNextSetup = !!contractId && !!name.trim() && !!upload && !!sheet && sheet.rows > 0 && !!catalogue

    return (
        <Dialog isOpen={isOpen} width={980} onClose={close} onRequestClose={close} shouldCloseOnOverlayClick={false}>
            <div className="flex max-h-[85vh] flex-col" data-testid="p7-import-dialog">
                <h5 className="mb-3 pe-8 text-[17px] font-extrabold text-navy-600 dark:text-gray-100">{t('p7.import.title')}</h5>
                <StepHeader step={step} />

                <div className="min-h-0 flex-1 overflow-y-auto pe-1">
                    {step === 'setup' && (
                        <div className="flex flex-col gap-4" data-testid="p7-import-setup">
                            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                                <label className="block">
                                    <span className="text-eyebrow mb-1 block">
                                        {t('p7.import.setup.contract')} <span className="text-danger">*</span>
                                    </span>
                                    <Select
                                        size="sm"
                                        menuPosition="fixed"
                                        options={contracts}
                                        /* the API refuses a deactivated contract: show it, but not selectable */
                                        isOptionDisabled={(o) => (o as { is_active?: boolean }).is_active === false}
                                        formatOptionLabel={(o) =>
                                            (o as { is_active?: boolean }).is_active === false
                                                ? `${(o as Option).label} ${t('p7.import.setup.contractInactive')}`
                                                : (o as Option).label
                                        }
                                        placeholder={String(t('p7.import.setup.pickContract'))}
                                        value={contracts.find((o) => o.value === contractId) || null}
                                        onChange={(o) => setContractId((o as Option | null)?.value || '')}
                                    />
                                </label>
                                <label className="block">
                                    <span className="text-eyebrow mb-1 block">
                                        {t('p7.import.setup.portfolioName')} <span className="text-danger">*</span>
                                    </span>
                                    <Input
                                        size="sm"
                                        dir="auto"
                                        maxLength={200}
                                        value={name}
                                        placeholder={String(t('p7.import.setup.portfolioName'))}
                                        onChange={(e) => setName(e.target.value)}
                                        data-testid="p7-import-name"
                                    />
                                </label>
                            </div>

                            <div className="flex flex-col items-center justify-center gap-2 rounded-lg border-2 border-dashed border-gray-400 bg-gray-50 px-6 py-6 text-center dark:border-night-border dark:bg-night-page">
                                <HiOutlineUpload aria-hidden className="text-[24px] text-gray-600" />
                                <span className="text-[13px] text-gray-600">{t('p7.import.setup.accepted')}</span>
                                <input
                                    ref={fileInput}
                                    type="file"
                                    accept=".xlsx,.csv,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                                    className="sr-only"
                                    data-testid="p7-import-file"
                                    onChange={(e) => {
                                        chooseFile(e.target.files)
                                        e.target.value = ''
                                    }}
                                />
                                <Button size="sm" type="button" loading={busy} onClick={() => fileInput.current?.click()}>
                                    {upload ? t('p7.import.setup.replaceFile') : t('p7.import.setup.chooseFile')}
                                </Button>
                            </div>

                            {upload && sheet && (
                                <div className="rounded-lg border border-gray-300 p-3 text-[13px] dark:border-night-border" data-testid="p7-import-file-info">
                                    <div className="flex flex-wrap items-center gap-x-6 gap-y-2">
                                        <span className="font-extrabold text-navy-600 dark:text-gray-100">
                                            <bdi>{upload.original_name}</bdi>
                                        </span>
                                        <span>{t('p7.import.setup.rows', { n: formatCount(sheet.rows) })}</span>
                                        <span>{t('p7.import.setup.columns', { n: formatCount(sheet.headers.length) })}</span>
                                    </div>
                                    {upload.sheets.filter((s) => s.rows > 0).length > 1 && (
                                        <div className="mt-2 max-w-[340px]">
                                            <span className="text-eyebrow mb-1 block">{t('p7.import.setup.sheet')}</span>
                                            <Select
                                                size="sm"
                                                menuPosition="fixed"
                                                options={upload.sheets.map((s) => ({ value: s.name, label: `${s.name} (${s.rows})` }))}
                                                value={{ value: sheet.name, label: `${sheet.name} (${sheet.rows})` }}
                                                onChange={(o) => {
                                                    const v = (o as Option | null)?.value
                                                    if (v) chooseSheet(v)
                                                }}
                                            />
                                        </div>
                                    )}
                                    <div className="mt-2 flex items-start gap-2 text-[12.5px] text-warning">
                                        <HiOutlineExclamation aria-hidden className="mt-0.5 shrink-0" />
                                        <span>{t('p7.import.setup.notScanned')}</span>
                                    </div>
                                    {sheet.rows === 0 && <div className="mt-2 text-danger">{t('p7.import.setup.noRows')}</div>}
                                </div>
                            )}
                        </div>
                    )}

                    {step === 'mapping' && catalogue && sheet && (
                        <ImportMappingStep
                            fields={catalogue.fields}
                            contactTypes={catalogue.contact_types}
                            sheet={sheet}
                            mapping={mapping}
                            onMapping={setMapping}
                            showProblems={showProblems}
                        />
                    )}

                    {step === 'preview' && preview && (
                        <div className="flex flex-col gap-4" data-testid="p7-import-preview">
                            <Note>{t('p7.import.preview.intro', { name: name.trim() })}</Note>
                            <CountTiles counts={preview.counts} />
                            {preview.counts.created_from_archive > 0 && <Note>{t('p7.import.preview.archiveNote')}</Note>}
                            {preview.counts.skipped_active_exists > 0 && <Note>{t('p7.import.preview.skippedNote')}</Note>}
                            {preview.blocking.length > 0 && (
                                <div role="alert" className="rounded-md border border-red-200 bg-danger-tint px-3 py-2 text-[13px] text-danger" data-testid="p7-import-blocking">
                                    {preview.blocking.map((b) => (
                                        <div key={b.code}>{t(`p7.import.err.${b.code}`)}</div>
                                    ))}
                                </div>
                            )}
                            <div>
                                <div className="text-eyebrow mb-2">
                                    {t('p7.import.preview.problems', { limit: preview.problem_limit })}
                                </div>
                                <ProblemsTable rows={preview.problems} />
                            </div>
                        </div>
                    )}

                    {step === 'run' && imp && (
                        <div className="flex flex-col gap-4" data-testid="p7-import-run" data-status={imp.status}>
                            {running && (
                                <div className="flex items-center gap-3 rounded-lg border border-gray-300 p-4 dark:border-night-border">
                                    <Spinner size={22} />
                                    <div>
                                        <div className="font-extrabold text-navy-600 dark:text-gray-100">{t('p7.import.run.running')}</div>
                                        <Note>{t('p7.import.run.runningHint')}</Note>
                                    </div>
                                </div>
                            )}
                            {succeeded && (
                                <>
                                    <div role="status" className="rounded-md border border-green-200 bg-green-50 px-3 py-2 text-[13px] text-success dark:border-night-border dark:bg-night-raised">
                                        {t('p7.import.run.succeeded', { name: imp.portfolio?.name || imp.portfolio_name })}
                                    </div>
                                    <CountTiles counts={imp.counts} />
                                    <div className="flex flex-wrap gap-2">
                                        {imp.portfolio_id && (
                                            <Button
                                                size="sm"
                                                variant="solid"
                                                onClick={() => {
                                                    onClose()
                                                    navigate({ pathname: '/clients', hash: `portfolio=${imp.portfolio_id}` })
                                                }}
                                            >
                                                {t('p7.import.run.openPortfolio')}
                                            </Button>
                                        )}
                                        <Button
                                            size="sm"
                                            icon={<HiOutlineDownload />}
                                            onClick={() => downloadImportReport(imp.id).catch(setError)}
                                        >
                                            {t('p7.import.run.report')}
                                        </Button>
                                    </div>
                                </>
                            )}
                            {imp.status === 'failed' && (
                                <div role="alert" className="rounded-md border border-red-200 bg-danger-tint px-3 py-2 text-[13px] text-danger" data-testid="p7-import-failed">
                                    <div className="font-extrabold">{t('p7.import.run.failed')}</div>
                                    <div>{describeFailure(imp.error, t)}</div>
                                    <div className="mt-1 text-gray-700 dark:text-night-body">{t('p7.import.run.failedHint')}</div>
                                </div>
                            )}
                        </div>
                    )}

                    <InlineError error={error} describe={describeImportError} />
                </div>

                <div className="mt-5 flex flex-wrap items-center justify-end gap-2">
                    <Button size="sm" type="button" onClick={close} disabled={busy}>
                        {succeeded || running ? t('p7.import.action.close') : t('cancel')}
                    </Button>
                    {step === 'setup' && (
                        <Button
                            size="sm"
                            variant="solid"
                            disabled={!canNextSetup}
                            onClick={() => {
                                setError(null)
                                setShowProblems(false)
                                setStep('mapping')
                            }}
                            data-testid="p7-import-next"
                        >
                            {t('p7.import.action.next')}
                        </Button>
                    )}
                    {step === 'mapping' && (
                        <>
                            <Button size="sm" onClick={() => setStep('setup')} disabled={busy}>
                                {t('p7.import.action.back')}
                            </Button>
                            <Button size="sm" variant="solid" loading={busy} onClick={runPreview} data-testid="p7-import-preview-btn">
                                {t('p7.import.action.preview')}
                            </Button>
                        </>
                    )}
                    {step === 'preview' && preview && (
                        <>
                            <Button size="sm" onClick={() => setStep('mapping')} disabled={busy}>
                                {t('p7.import.action.back')}
                            </Button>
                            <Button
                                size="sm"
                                variant="solid"
                                loading={busy}
                                disabled={!preview.can_commit || busy || running || succeeded}
                                onClick={() => runCommit()}
                                data-testid="p7-import-confirm"
                            >
                                {t('p7.import.action.confirm', { n: formatCount(preview.counts.to_create) })}
                            </Button>
                        </>
                    )}
                    {step === 'run' && imp?.status === 'failed' && (
                        <>
                            <Button
                                size="sm"
                                onClick={() => {
                                    setError(null)
                                    setPreview(null)
                                    setStep('mapping')
                                }}
                                disabled={busy}
                            >
                                {t('p7.import.action.backToMapping')}
                            </Button>
                            <Button size="sm" variant="solid" loading={busy} onClick={retry} data-testid="p7-import-retry">
                                {t('p7.import.action.retry')}
                            </Button>
                        </>
                    )}
                </div>
            </div>
        </Dialog>
    )
}

export default PortfolioImportDialog
