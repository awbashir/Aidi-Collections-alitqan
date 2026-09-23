/**
 * Wizard step 2: private upload and its inspection result.
 *
 * `POST /v2/uploads` (multipart, purpose = the kind). The API accepts real XLSX
 * and UTF-8 CSV only, counts formula cells without evaluating them and always
 * answers `scan_status: not_scanned`: there is no antivirus, so the screen
 * never calls the file clean. One Idempotency-Key per chosen file; "try
 * again" after an error reuses it.
 */
import classNames from 'classnames'
import { useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { HiOutlineExclamation, HiOutlineUpload } from 'react-icons/hi'
import Button from '@/components/ui/Button'
import Table from '@/components/ui/Table'
import { InlineError } from '@/components/v2/States'
import { Ltr, RiyadhTime } from '@/components/v2/Display'
import { bulkApi, describeBulkError, newIdempotencyKey } from '@/services/v2'
import type { BulkUpload, UploadSheet } from '@/services/v2'
import { formatCount } from '@/utils/v2/format'
import { formatBytes } from './shared'

const { THead, TBody, Tr, Th, Td } = Table

export const UploadStep = ({
    purpose,
    upload,
    sheet,
    onUploaded,
    onSheet,
}: {
    purpose: string
    upload: BulkUpload | null
    sheet: string | undefined
    onUploaded: (u: BulkUpload | null) => void
    onSheet: (name: string, sheet: UploadSheet) => void
}) => {
    const { t } = useTranslation()
    const input = useRef<HTMLInputElement>(null)
    const [drag, setDrag] = useState(false)
    const [busy, setBusy] = useState(false)
    const [error, setError] = useState<unknown>(null)
    const [pending, setPending] = useState<{ file: File; key: string } | null>(null)

    const send = async (file: File, key: string) => {
        setBusy(true)
        setError(null)
        setPending({ file, key })
        try {
            const u = await bulkApi.upload(file, purpose, key)
            onUploaded(u)
            const first = u.sheets.find((s) => s.rows > 0) || u.sheets[0]
            if (first) onSheet(first.name, first)
            setPending(null)
        } catch (e) {
            setError(e)
        } finally {
            setBusy(false)
        }
    }
    const choose = (files: FileList | null) => {
        const file = files && files[0]
        if (!file) return
        onUploaded(null)
        send(file, newIdempotencyKey())
    }

    return (
        <div data-testid="p2-step-upload">
            <div
                onDragOver={(e) => {
                    e.preventDefault()
                    setDrag(true)
                }}
                onDragLeave={() => setDrag(false)}
                onDrop={(e) => {
                    e.preventDefault()
                    setDrag(false)
                    choose(e.dataTransfer.files)
                }}
                className={classNames(
                    'flex cursor-pointer flex-col items-center justify-center gap-2 rounded-lg border-2 border-dashed px-6 py-8 text-center',
                    drag ? 'border-azure-500 bg-azure-50 dark:bg-night-raised' : 'border-gray-400 bg-gray-50 dark:border-night-border dark:bg-night-page'
                )}
                data-testid="p2-dropzone"
            >
                <HiOutlineUpload aria-hidden className="text-[24px] text-gray-600" />
                <span className="text-[14px] font-extrabold text-navy-600 dark:text-gray-100">{t('p2.upload.drop')}</span>
                <span className="text-[12.5px] text-gray-600">{t('p2.upload.accepted')}</span>
                <input
                    ref={input}
                    type="file"
                    accept=".xlsx,.csv,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                    className="sr-only"
                    data-testid="p2-upload-input"
                    onChange={(e) => {
                        choose(e.target.files)
                        e.target.value = ''
                    }}
                />
                <span className="mt-1 inline-flex">
                    <Button size="sm" type="button" loading={busy} onClick={() => input.current?.click()}>
                        {upload ? t('p2.upload.replace') : t('p2.upload.choose')}
                    </Button>
                </span>
            </div>

            {error ? (
                <div>
                    <InlineError error={error} describe={describeBulkError} />
                    {pending && (
                        <Button size="sm" className="mt-2" onClick={() => send(pending.file, pending.key)} loading={busy}>
                            {t('p1.action.retry')}
                        </Button>
                    )}
                </div>
            ) : null}

            {upload && (
                <div className="mt-5" data-testid="p2-inspection" data-upload={upload.id}>
                    <div
                        role="note"
                        className="mb-3 flex items-start gap-2 rounded-md border border-warning-tint bg-warning-tint px-3 py-2 text-[13px] text-warning"
                        data-testid="p2-not-scanned"
                        data-scan={upload.scan_status}
                    >
                        <HiOutlineExclamation aria-hidden className="mt-0.5 shrink-0 text-[16px]" />
                        <span>
                            <span className="font-extrabold">{t('p2.upload.notScannedTitle')}</span> {t('p2.upload.notScanned')}
                        </span>
                    </div>
                    {upload.formula_cells > 0 && (
                        <div
                            role="note"
                            className="mb-3 flex items-start gap-2 rounded-md border border-warning-tint bg-warning-tint px-3 py-2 text-[13px] text-warning"
                            data-testid="p2-formula-warning"
                        >
                            <HiOutlineExclamation aria-hidden className="mt-0.5 shrink-0 text-[16px]" />
                            <span>{t('p2.upload.formulas', { count: upload.formula_cells, n: formatCount(upload.formula_cells) })}</span>
                        </div>
                    )}
                    <dl className="mb-4 grid grid-cols-2 gap-x-6 gap-y-2 text-[13px] sm:grid-cols-4">
                        <div>
                            <dt className="text-eyebrow">{t('p2.upload.file')}</dt>
                            <dd className="truncate font-extrabold text-navy-600 dark:text-gray-100" title={upload.original_name}>
                                <bdi>{upload.original_name}</bdi>
                            </dd>
                        </div>
                        <div>
                            <dt className="text-eyebrow">{t('p2.upload.type')}</dt>
                            <dd>
                                <Ltr>
                                    {upload.detected_type.toUpperCase()}
                                    {upload.encoding ? ` · ${upload.encoding}` : ''}
                                    {upload.delimiter ? ` · "${upload.delimiter}"` : ''} · {formatBytes(upload.size_bytes)}
                                </Ltr>
                            </dd>
                        </div>
                        <div>
                            <dt className="text-eyebrow">{t('p2.upload.rows')}</dt>
                            <dd className="tabular-nums" data-testid="p2-upload-rows" data-value={upload.row_count}>
                                {formatCount(upload.row_count)}
                            </dd>
                        </div>
                        <div>
                            <dt className="text-eyebrow">{t('p2.upload.expires')}</dt>
                            <dd>
                                <RiyadhTime iso={upload.expires_at} />
                            </dd>
                        </div>
                        <div className="col-span-2 sm:col-span-4">
                            <dt className="text-eyebrow">SHA-256</dt>
                            <dd>
                                <Ltr className="break-all font-mono text-[11.5px] text-gray-600">{upload.sha256}</Ltr>
                            </dd>
                        </div>
                    </dl>

                    <div className="text-eyebrow mb-2">{t('p2.upload.sheets')}</div>
                    <div className="overflow-x-auto rounded-lg border border-gray-300 dark:border-night-border">
                        <Table>
                            <THead>
                                <Tr>
                                    <Th>{t('p2.upload.useSheet')}</Th>
                                    <Th>{t('p2.upload.sheet')}</Th>
                                    <Th className="text-end">{t('p2.upload.rows')}</Th>
                                    <Th className="text-end">{t('p2.upload.columns')}</Th>
                                    <Th className="text-end">{t('p2.upload.headerRow')}</Th>
                                    <Th className="text-end">{t('p2.upload.formulaCells')}</Th>
                                    <Th>{t('p2.upload.notes')}</Th>
                                </Tr>
                            </THead>
                            <TBody>
                                {upload.sheets.map((s) => (
                                    <Tr key={s.name} data-testid="p2-sheet-row" data-sheet={s.name}>
                                        <Td>
                                            <input
                                                type="radio"
                                                name="p2-sheet"
                                                aria-label={s.name}
                                                checked={sheet === s.name}
                                                onChange={() => onSheet(s.name, s)}
                                            />
                                        </Td>
                                        <Td className="font-extrabold">
                                            <bdi>{s.name}</bdi>
                                        </Td>
                                        <Td className="text-end tabular-nums">{formatCount(s.rows)}</Td>
                                        <Td className="text-end tabular-nums">{formatCount(s.columns)}</Td>
                                        <Td className="text-end tabular-nums">{s.header_row}</Td>
                                        <Td className="text-end tabular-nums">{formatCount(s.formula_cells)}</Td>
                                        <Td className="text-[12.5px] text-gray-600">
                                            {!s.complete && <div className="text-danger">{t('p2.upload.incomplete')}</div>}
                                            {s.unnamed_data_columns > 0 && (
                                                <div>{t('p2.upload.unnamed', { n: s.unnamed_data_columns })}</div>
                                            )}
                                            {s.rows === 0 && <div>{t('p2.upload.emptySheet')}</div>}
                                        </Td>
                                    </Tr>
                                ))}
                            </TBody>
                        </Table>
                    </div>
                </div>
            )}
        </div>
    )
}

export default UploadStep
