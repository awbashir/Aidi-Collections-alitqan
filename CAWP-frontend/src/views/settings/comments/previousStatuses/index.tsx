/**
 * Previous statuses import ("استيراد الحالات السابقة", `/settings/comments/previousStatuses`; route
 * key `previous_statuses_import` = settings_comments_status_add, derived; the API also requires the
 * SuperAdmin role). Customer request 2026-10-19: one spreadsheet per portfolio with each account's
 * main and sub status from the old system (`رقم الحساب | الحالة الرئيسية | الحالة الفرعية`).
 *
 *  1. file (private upload, inspected) + column mapping (guessed from the Arabic headers)
 *  2. preview (writes nothing): ready rows, `---` rows, unknown accounts, archived / locked cases,
 *     missing main statuses (never created), missing sub-statuses (created under their main
 *     status when the option is on), already imported, problem rows
 *  3. options: the note date (before today; default 2026-10-11, before go-live, so newer notes
 *     stay the latest and today's reports never count them), contact method, optional note
 *  4. apply -> progress -> result (counts, created sub-statuses, XLSX report of skipped rows)
 *
 * One upload is imported at most once (the API keeps one import record per upload), and a case
 * that already has the same imported status is skipped, so a second upload of the file adds nothing.
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { HiOutlineDownload, HiOutlineExclamation, HiOutlineUpload } from 'react-icons/hi'
import Button from '@/components/ui/Button'
import Checkbox from '@/components/ui/Checkbox'
import Input from '@/components/ui/Input'
import Select from '@/components/ui/Select'
import Spinner from '@/components/ui/Spinner'
import { PageHead } from '@/components/v2/ListParts'
import { ErrorState, InlineError, Note, SkeletonLines } from '@/components/v2/States'
import { describeV2Error, isV2Error, newIdempotencyKey } from '@/services/v2'
import type { Option, Translate, UploadSheet } from '@/services/v2'
import { downloadPrevStatusReport, previousStatusImportApi } from '@/services/v2/previousStatusImportApi'
import type {
    PrevStatusField,
    PrevStatusImport,
    PrevStatusMapping,
    PrevStatusOptions,
    PrevStatusPreview,
    PrevStatusUpload,
} from '@/services/v2/previousStatusImportApi'
import { formatCount } from '@/utils/v2/format'
import { Card, Notice } from '@/views/phase5/parts'
import { CountTiles, ProblemsTable, StatusList } from './PreviewParts'

const POLL_MS = 1500
const FIELDS: PrevStatusField[] = ['account_number', 'main_status', 'sub_status']
const KNOWN_ERRORS = new Set([
    'invalid_mapping',
    'already_imported',
    'import_in_progress',
    'nothing_to_import',
    'upload_purged',
    'upload_expired',
    'upload_not_found',
    'upload_purpose_mismatch',
    'import_failed',
    'statuses_changed',
    'contact_method_missing',
    'too_many_rows',
    'report_not_ready',
    'super_admin_required',
    'file_too_large',
    'unsupported_file_type',
    'not_a_workbook',
    'not_utf8',
    'empty_file',
    'no_data',
])

function describe(error: unknown, t: Translate): string {
    if (isV2Error(error) && KNOWN_ERRORS.has(error.code)) return t(`p9.prevstatus.err.${error.code}`)
    if (isV2Error(error)) {
        const d = error.detail as { noted_on?: string } | null
        if (d?.noted_on) return t(`p9.prevstatus.err.noted_on_${d.noted_on === 'not_before_today' ? 'today' : 'invalid'}`)
    }
    return describeV2Error(error, t)
}

function failureText(err: { code: string; message: string | null } | null, t: Translate): string {
    if (!err) return ''
    if (KNOWN_ERRORS.has(err.code)) return t(`p9.prevstatus.err.${err.code}`)
    return t('p9.prevstatus.err.generic', { message: err.message || err.code })
}

/** Keeps only guessed columns that exist in `headers`. */
function guessFor(guess: PrevStatusMapping | undefined, headers: string[]): PrevStatusMapping {
    const set = new Set(headers)
    const out: PrevStatusMapping = {}
    FIELDS.forEach((f) => {
        const v = guess?.[f]
        if (v && set.has(v)) out[f] = v
    })
    return out
}

const PreviousStatusesImport = () => {
    const { t } = useTranslation()
    const fileInput = useRef<HTMLInputElement>(null)

    const [opts, setOpts] = useState<PrevStatusOptions | null>(null)
    const [optsError, setOptsError] = useState<unknown>(null)
    const [upload, setUpload] = useState<PrevStatusUpload | null>(null)
    const [sheetName, setSheetName] = useState<string | undefined>(undefined)
    const [mapping, setMapping] = useState<PrevStatusMapping>({})
    const [createSubs, setCreateSubs] = useState(true)
    const [includeArchived, setIncludeArchived] = useState(false)
    const [notedOn, setNotedOn] = useState('')
    const [methodId, setMethodId] = useState('')
    const [note, setNote] = useState('')
    const [preview, setPreview] = useState<PrevStatusPreview | null>(null)
    const [imp, setImp] = useState<PrevStatusImport | null>(null)
    const [busy, setBusy] = useState(false)
    const [error, setError] = useState<unknown>(null)
    /* One key per apply action: a double click or a lost response replays, never re-runs. */
    const [applyKey, setApplyKey] = useState(() => newIdempotencyKey())

    const loadOptions = () => {
        setOptsError(null)
        previousStatusImportApi
            .options()
            .then((o) => {
                setOpts(o)
                setNotedOn((v) => v || o.default_noted_on)
                setMethodId((v) => v || o.default_contact_method_id || '')
            })
            .catch(setOptsError)
    }
    useEffect(loadOptions, [])

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
            previousStatusImportApi
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

    const body = () => ({
        upload_id: upload?.id || '',
        sheet: sheet?.name,
        mapping,
        create_missing_sub_statuses: createSubs,
        include_archived: includeArchived,
    })

    /* any change of the file, mapping or matching options makes the preview stale */
    const stale = () => {
        setPreview(null)
        setError(null)
    }

    const chooseFile = async (files: FileList | null) => {
        const file = files && files[0]
        if (!file) return
        setBusy(true)
        setError(null)
        setUpload(null)
        setPreview(null)
        setImp(null)
        try {
            const up = await previousStatusImportApi.upload(file, newIdempotencyKey())
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

    const chooseSheet = async (name: string) => {
        if (!upload) return
        setSheetName(name)
        stale()
        try {
            const o = await previousStatusImportApi.options({ upload_id: upload.id, sheet: name })
            setMapping(guessFor(o.guess, o.headers || []))
        } catch (e) {
            setError(e)
        }
    }

    const mappingComplete = !!mapping.account_number && !!mapping.main_status
    const mappingTwice = FIELDS.some((a) => FIELDS.some((b) => a !== b && mapping[a] && mapping[a] === mapping[b]))

    const runPreview = async () => {
        if (!upload || !mappingComplete || mappingTwice) return
        setBusy(true)
        setError(null)
        try {
            setPreview(await previousStatusImportApi.preview(body(), newIdempotencyKey()))
        } catch (e) {
            setError(e)
        } finally {
            setBusy(false)
        }
    }

    const runApply = async (key: string = applyKey) => {
        if (busy || running || succeeded || !preview) return
        /* a retry after a failure re-runs THE import record of this upload (the API reuses it) */
        setBusy(true)
        setError(null)
        try {
            const started = await previousStatusImportApi.apply(
                { ...body(), noted_on: notedOn, contact_method_id: methodId || undefined, note: note.trim() || undefined },
                key
            )
            setImp(started)
            setApplyKey(newIdempotencyKey())
        } catch (e) {
            const detail = isV2Error(e) ? (e.detail as { import_id?: string } | null) : null
            if (isV2Error(e) && (e.code === 'import_in_progress' || e.code === 'already_imported') && detail?.import_id) {
                try {
                    setImp(await previousStatusImportApi.get(detail.import_id))
                } catch {
                    /* keep the original error */
                }
            }
            setError(e)
            setApplyKey(newIdempotencyKey())
        } finally {
            setBusy(false)
        }
    }

    const startOver = () => {
        setUpload(null)
        setPreview(null)
        setImp(null)
        setMapping({})
        setError(null)
    }

    if (optsError) {
        if (isV2Error(optsError) && optsError.code === 'super_admin_required') {
            return (
                <div data-testid="p9-prevstatus">
                    <PageHead title={t('p9.prevstatus.title')} />
                    <Notice tone="warning" testId="p9-prevstatus-super-only">
                        {t('p9.prevstatus.err.super_admin_required')}
                    </Notice>
                </div>
            )
        }
        return (
            <div data-testid="p9-prevstatus">
                <PageHead title={t('p9.prevstatus.title')} />
                <ErrorState error={optsError} onRetry={loadOptions} describe={describe} />
            </div>
        )
    }

    const methodOptions: Option[] = (opts?.contact_methods || []).map((m) => ({ value: m.id, label: m.name }))
    const headerOptions: Option[] = (sheet?.headers || []).map((h) => ({ value: h, label: h }))
    /* after apply the inputs of this upload are fixed; a failed import is retried (same record) with the apply button */
    const locked = !!imp && imp.status !== 'failed'
    const readyCount = preview?.counts.ready ?? 0

    return (
        <div className="flex flex-col gap-4" data-testid="p9-prevstatus">
            <PageHead title={t('p9.prevstatus.title')} subtitle={t('p9.prevstatus.subtitle')} />
            <Notice tone="info">{t('p9.prevstatus.intro')}</Notice>

            {/* 1. file + mapping */}
            <Card title={t('p9.prevstatus.step.file')} testId="p9-prevstatus-file">
                {!opts ? (
                    <SkeletonLines rows={3} />
                ) : (
                    <div className="flex flex-col gap-4">
                        <div className="flex flex-col items-center justify-center gap-2 rounded-lg border-2 border-dashed border-gray-400 bg-gray-50 px-6 py-6 text-center dark:border-night-border dark:bg-night-page">
                            <HiOutlineUpload aria-hidden className="text-[24px] text-gray-600" />
                            <span className="text-[13px] text-gray-600">{t('p9.prevstatus.file.accepted')}</span>
                            <input
                                ref={fileInput}
                                type="file"
                                accept=".xlsx,.csv,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                                className="sr-only"
                                data-testid="p9-prevstatus-input"
                                onChange={(e) => {
                                    chooseFile(e.target.files)
                                    e.target.value = ''
                                }}
                            />
                            <Button size="sm" type="button" loading={busy && !upload} disabled={busy || running} onClick={() => fileInput.current?.click()}>
                                {upload ? t('p9.prevstatus.file.replace') : t('p9.prevstatus.file.choose')}
                            </Button>
                        </div>

                        {upload && sheet && (
                            <div className="rounded-lg border border-gray-300 p-3 text-[13px] dark:border-night-border" data-testid="p9-prevstatus-file-info">
                                <div className="flex flex-wrap items-center gap-x-6 gap-y-2">
                                    <span className="font-extrabold text-navy-600 dark:text-gray-100">
                                        <bdi>{upload.original_name}</bdi>
                                    </span>
                                    <span>{t('p9.prevstatus.file.rows', { n: formatCount(sheet.rows) })}</span>
                                </div>
                                {upload.sheets.filter((s) => s.rows > 0).length > 1 && (
                                    <div className="mt-2 max-w-[340px]">
                                        <span className="text-eyebrow mb-1 block">{t('p9.prevstatus.file.sheet')}</span>
                                        <Select
                                            size="sm"
                                            menuPosition="fixed"
                                            isDisabled={locked}
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
                                    <span>{t('p9.prevstatus.file.notScanned')}</span>
                                </div>
                            </div>
                        )}

                        {upload && sheet && (
                            <div className="grid grid-cols-1 gap-3 sm:grid-cols-3" data-testid="p9-prevstatus-mapping">
                                {FIELDS.map((f) => (
                                    <label key={f} className="block">
                                        <span className="text-eyebrow mb-1 block">
                                            {t(`p9.prevstatus.field.${f}`)} {f !== 'sub_status' && <span className="text-danger">*</span>}
                                        </span>
                                        <Select
                                            size="sm"
                                            menuPosition="fixed"
                                            isClearable={f === 'sub_status'}
                                            isDisabled={locked}
                                            placeholder={String(t('p9.prevstatus.field.pick'))}
                                            options={headerOptions}
                                            value={headerOptions.find((o) => o.value === mapping[f]) || null}
                                            onChange={(o) => {
                                                setMapping({ ...mapping, [f]: (o as Option | null)?.value || undefined })
                                                stale()
                                            }}
                                        />
                                    </label>
                                ))}
                                {(!mappingComplete || mappingTwice) && (
                                    <div className="text-[12.5px] text-danger sm:col-span-3">
                                        {mappingTwice ? t('p9.prevstatus.field.twice') : t('p9.prevstatus.field.required')}
                                    </div>
                                )}
                            </div>
                        )}

                        {upload && sheet && (
                            <div className="flex flex-col gap-2">
                                <Checkbox
                                    checked={createSubs}
                                    disabled={locked}
                                    onChange={(v) => {
                                        setCreateSubs(!!v)
                                        stale()
                                    }}
                                >
                                    <span className="text-[13px]">{t('p9.prevstatus.opt.createSubs')}</span>
                                </Checkbox>
                                <Checkbox
                                    checked={includeArchived}
                                    disabled={locked}
                                    onChange={(v) => {
                                        setIncludeArchived(!!v)
                                        stale()
                                    }}
                                >
                                    <span className="text-[13px]">{t('p9.prevstatus.opt.includeArchived')}</span>
                                </Checkbox>
                                <Note>{t('p9.prevstatus.opt.mainNeverCreated')}</Note>
                            </div>
                        )}

                        {upload && sheet && !locked && (
                            <div className="flex justify-end">
                                <Button
                                    size="sm"
                                    variant="solid"
                                    loading={busy && !!upload && !preview}
                                    disabled={busy || !mappingComplete || mappingTwice || sheet.rows === 0}
                                    onClick={runPreview}
                                    data-testid="p9-prevstatus-preview-btn"
                                >
                                    {preview ? t('p9.prevstatus.action.refreshPreview') : t('p9.prevstatus.action.preview')}
                                </Button>
                            </div>
                        )}
                    </div>
                )}
            </Card>

            {/* 2. preview */}
            {preview && (
                <Card title={t('p9.prevstatus.step.preview')} testId="p9-prevstatus-preview">
                    <div className="flex flex-col gap-4">
                        <Note>{t('p9.prevstatus.preview.intro')}</Note>
                        <CountTiles counts={preview.counts} />
                        <StatusList
                            testId="p9-prevstatus-to-create"
                            tone="info"
                            title={t('p9.prevstatus.preview.toCreate')}
                            items={preview.sub_statuses_to_create.map((s) => ({ main: s.main_name, name: s.name, rows: s.rows }))}
                        />
                        <StatusList
                            testId="p9-prevstatus-missing-main"
                            tone="warning"
                            title={t('p9.prevstatus.preview.missingMain')}
                            items={preview.missing_main_statuses}
                        />
                        <StatusList
                            testId="p9-prevstatus-missing-sub"
                            tone="warning"
                            title={t('p9.prevstatus.preview.missingSub')}
                            items={preview.missing_sub_statuses.map((s) => ({ main: s.main_name, name: s.name, rows: s.rows }))}
                        />
                        {preview.blocking.length > 0 && (
                            <Notice tone="danger" testId="p9-prevstatus-blocking">
                                {preview.blocking.map((b) => (
                                    <div key={b.code}>{t(`p9.prevstatus.err.${b.code}`)}</div>
                                ))}
                            </Notice>
                        )}
                        <div>
                            <div className="text-eyebrow mb-2">{t('p9.prevstatus.preview.problems', { limit: preview.problem_limit })}</div>
                            <ProblemsTable rows={preview.problems} />
                        </div>
                    </div>
                </Card>
            )}

            {/* 3. options + apply */}
            {preview && opts && (
                <Card title={t('p9.prevstatus.step.apply')} testId="p9-prevstatus-apply">
                    <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
                        <label className="block">
                            <span className="text-eyebrow mb-1 block">
                                {t('p9.prevstatus.apply.date')} <span className="text-danger">*</span>
                            </span>
                            <Input
                                type="date"
                                size="sm"
                                max={opts.latest_noted_on}
                                value={notedOn}
                                disabled={locked}
                                onChange={(e) => setNotedOn(e.target.value)}
                                data-testid="p9-prevstatus-date"
                            />
                        </label>
                        <label className="block">
                            <span className="text-eyebrow mb-1 block">
                                {t('p9.prevstatus.apply.method')} <span className="text-danger">*</span>
                            </span>
                            <Select
                                size="sm"
                                menuPosition="fixed"
                                isDisabled={locked}
                                options={methodOptions}
                                value={methodOptions.find((o) => o.value === methodId) || null}
                                onChange={(o) => setMethodId((o as Option | null)?.value || '')}
                            />
                        </label>
                        <label className="block">
                            <span className="text-eyebrow mb-1 block">{t('p9.prevstatus.apply.note')}</span>
                            <Input
                                size="sm"
                                dir="auto"
                                maxLength={opts.note_max}
                                value={note}
                                disabled={locked}
                                placeholder={String(t('p9.prevstatus.apply.notePlaceholder'))}
                                onChange={(e) => setNote(e.target.value)}
                            />
                        </label>
                    </div>
                    <Note className="mt-2">{t('p9.prevstatus.apply.dateHint', { day: opts.default_noted_on })}</Note>
                    <Note className="mt-1">
                        {t('p9.prevstatus.apply.textHint')} <bdi className="font-extrabold">{opts.marker}</bdi>
                    </Note>
                    {!locked && (
                        <div className="mt-4 flex justify-end">
                            <Button
                                size="sm"
                                variant="solid"
                                loading={busy}
                                disabled={!preview.can_apply || busy || !notedOn || !methodId || notedOn > opts.latest_noted_on}
                                onClick={() => runApply()}
                                data-testid="p9-prevstatus-confirm"
                            >
                                {t('p9.prevstatus.action.apply', { n: formatCount(readyCount) })}
                            </Button>
                        </div>
                    )}
                </Card>
            )}

            {/* 4. result */}
            {imp && (
                <Card title={t('p9.prevstatus.step.result')} testId="p9-prevstatus-result">
                    <div className="flex flex-col gap-4" data-status={imp.status}>
                        {running && (
                            <div className="flex items-center gap-3 rounded-lg border border-gray-300 p-4 dark:border-night-border">
                                <Spinner size={22} />
                                <div>
                                    <div className="font-extrabold text-navy-600 dark:text-gray-100">{t('p9.prevstatus.run.running')}</div>
                                    <Note>{t('p9.prevstatus.run.runningHint')}</Note>
                                </div>
                            </div>
                        )}
                        {succeeded && (
                            <>
                                <Notice tone="success" testId="p9-prevstatus-succeeded">
                                    {t('p9.prevstatus.run.succeeded', { n: formatCount(imp.counts.imported ?? 0) })}
                                </Notice>
                                <CountTiles counts={imp.counts} done />
                                <StatusList
                                    testId="p9-prevstatus-created"
                                    tone="info"
                                    title={t('p9.prevstatus.run.created')}
                                    items={imp.created_sub_statuses.map((s) => ({ main: s.parent_name, name: s.name, rows: s.rows }))}
                                />
                                <div className="flex flex-wrap gap-2">
                                    <Button size="sm" icon={<HiOutlineDownload />} onClick={() => downloadPrevStatusReport(imp.id).catch(setError)}>
                                        {t('p9.prevstatus.run.report')}
                                    </Button>
                                    <Button size="sm" onClick={startOver}>
                                        {t('p9.prevstatus.action.another')}
                                    </Button>
                                </div>
                            </>
                        )}
                        {imp.status === 'failed' && (
                            <Notice tone="danger" testId="p9-prevstatus-failed">
                                <div className="font-extrabold">{t('p9.prevstatus.run.failed')}</div>
                                <div>{failureText(imp.error, t)}</div>
                                <div className="mt-1 text-gray-700 dark:text-night-body">{t('p9.prevstatus.run.failedHint')}</div>
                                <div className="mt-2 flex flex-wrap gap-2">
                                    <Button size="sm" onClick={startOver}>
                                        {t('p9.prevstatus.action.another')}
                                    </Button>
                                </div>
                            </Notice>
                        )}
                    </div>
                </Card>
            )}

            <InlineError error={error} describe={describe} />
        </div>
    )
}

export default PreviousStatusesImport
