/**
 * Reviewed bulk wizard, steps 1–4 (`/jobs/new/:kind`): source → upload →
 * column mapping → params. "Validate and preview" creates the job
 * (`POST /v2/bulk/<kind>`) and hands over to `/jobs/run/:id` (steps 5–9),
 * which is reload-safe. Nothing changes business data until the job is
 * explicitly confirmed there.
 *
 * Everything the kind offers comes from `GET /v2/bulk/kinds` (labels, sources,
 * mapping fields, `allowed`). The calling screen passes its context in router
 * state (`BulkLaunch`): selected case ids, its current filter exactly as it
 * sends it (and the count it shows), or a portfolio.
 */
import classNames from 'classnames'
import { useEffect, useMemo, useState } from 'react'
import { Link, useLocation, useNavigate, useParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { HiArrowLeft, HiArrowRight } from 'react-icons/hi'
import Button from '@/components/ui/Button'
import Select from '@/components/ui/Select'
import { ErrorState, ForbiddenState, InlineError, Note, SkeletonLines } from '@/components/v2/States'
import { PageHead } from '@/components/v2/ListParts'
import { bulkApi, describeBulkError, isV2Error, lookups } from '@/services/v2'
import type { BulkCreateBody, BulkKind, BulkSource, BulkUpload, Option } from '@/services/v2'
import { useActionKey } from '@/utils/v2/hooks'
import { formatCount } from '@/utils/v2/format'
import { Panel, Stepper } from './parts'
import type { StepKey, StepState } from './parts'
import { useBulkKinds, useKindLabel } from './shared'
import type { BulkLaunch } from './shared'
import UploadStep from './UploadStep'
import MappingStep from './MappingStep'
import { guessMapping, mappingProblems } from './mapping'
import type { AttrRow } from './mapping'
import { ParamsForm } from './params'
import { buildParams, paramsValid } from './paramsLogic'
import type { Params } from './paramsLogic'

type Step = 'source' | 'upload' | 'mapping' | 'params'

const SOURCE_ORDER: BulkSource[] = ['selection', 'filter', 'portfolio', 'spreadsheet', 'query']

function sourceAvailable(kind: BulkKind, s: BulkSource, launch: BulkLaunch): boolean {
    if (s === 'selection') return (launch.caseIds?.length || 0) > 0
    if (s === 'filter') return !!launch.filter && kind.filter_lists.includes(launch.filter.list)
    if (s === 'portfolio') return !!launch.portfolioId || launch.source === 'portfolio'
    return true
}

function initialSource(kind: BulkKind, launch: BulkLaunch): BulkSource | undefined {
    const ok = kind.sources.filter((s) => sourceAvailable(kind, s, launch))
    if (launch.source && ok.includes(launch.source)) return launch.source
    if (ok.length === 1) return ok[0]
    for (const s of SOURCE_ORDER) if (ok.includes(s) && s !== 'spreadsheet') return s
    return ok[0]
}

function initialParams(kind: string, launch: BulkLaunch): Params {
    const p: Params = { ...(launch.params || {}) }
    if (kind === 'round_robin') {
        if (!p.portfolio_id && launch.portfolioId) p.portfolio_id = launch.portfolioId
        p.__ui = { scope: p.contract_id && !p.portfolio_id ? 'contract' : 'portfolio' }
    }
    if (kind === 'inquiry' && !p.match_mode) p.match_mode = 'all'
    if (kind === 'attribute_corrections' && !p.blank_cells) p.blank_cells = 'skip'
    return p
}

const NewJobPage = () => {
    const { t } = useTranslation()
    const { kind: kindKey = '' } = useParams()
    const location = useLocation()
    const launch: BulkLaunch = ((location.state as { bulkLaunch?: BulkLaunch } | null)?.bulkLaunch || {}) as BulkLaunch
    const kinds = useBulkKinds()
    const kind = kinds.byKey[kindKey]
    const label = useKindLabel()

    if (kinds.loading) {
        return (
            <div data-testid="p2-wizard">
                <PageHead title={t('p2.wizard.title')} />
                <SkeletonLines rows={6} />
            </div>
        )
    }
    if (kinds.error) {
        return (
            <div data-testid="p2-wizard">
                <PageHead title={t('p2.wizard.title')} />
                <ErrorState error={kinds.error} describe={describeBulkError} />
            </div>
        )
    }
    if (!kind) {
        return (
            <div data-testid="p2-wizard">
                <PageHead title={t('p2.wizard.title')} />
                <ErrorState error={new Error(String(t('p2.wizard.unknownKind', { kind: kindKey })))} />
            </div>
        )
    }
    if (!kind.allowed) {
        return (
            <div data-testid="p2-wizard" data-kind={kind.key}>
                <PageHead title={label(kind)} />
                <ForbiddenState keys={kind.permissions} />
            </div>
        )
    }
    return <Wizard key={kind.key} kind={kind} launch={launch} title={label(kind)} />
}

const Wizard = ({ kind, launch, title }: { kind: BulkKind; launch: BulkLaunch; title: string }) => {
    const { t } = useTranslation()
    const navigate = useNavigate()
    const [step, setStep] = useState<Step>('source')
    const [source, setSource] = useState<BulkSource | undefined>(() => initialSource(kind, launch))
    const [portfolioId, setPortfolioId] = useState<string | undefined>(launch.portfolioId)
    const [upload, setUpload] = useState<BulkUpload | null>(null)
    const [sheetName, setSheetName] = useState<string | undefined>()
    const [mapping, setMapping] = useState<Record<string, string>>({})
    const [attrs, setAttrs] = useState<AttrRow[]>([{ key: '', column: '' }])
    const [params, setParams] = useState<Params>(() => initialParams(kind.key, launch))
    const [showProblems, setShowProblems] = useState(false)
    const [busy, setBusy] = useState(false)
    const [error, setError] = useState<unknown>(null)
    const { key: createKey } = useActionKey(true)

    const spreadsheet = source === 'spreadsheet'
    const sheet = upload?.sheets.find((s) => s.name === sheetName)
    const order = useMemo<Step[]>(() => (spreadsheet ? ['source', 'upload', 'mapping', 'params'] : ['source', 'params']), [spreadsheet])
    const idx = order.indexOf(step)

    const stepStates = useMemo(() => {
        const st = {} as Record<StepKey, StepState>
        const all: StepKey[] = ['source', 'upload', 'mapping', 'params', 'validating', 'preview', 'confirm', 'running', 'results']
        all.forEach((k) => {
            st[k] = 'todo'
        })
        if (!spreadsheet) {
            st.upload = 'skipped'
            st.mapping = 'skipped'
        }
        if (kind.read_only) {
            st.preview = 'skipped'
            st.confirm = 'skipped'
            st.running = 'skipped'
        }
        order.forEach((k, i) => {
            if (i < idx) st[k] = 'done'
            if (i === idx) st[k] = 'current'
        })
        return st
    }, [spreadsheet, kind.read_only, order, idx])

    const attributes = useMemo(() => {
        const out: Record<string, string> = {}
        attrs.forEach((a) => {
            if (a.key.trim() && a.column) out[a.key.trim()] = a.column
        })
        return out
    }, [attrs])

    const canNext = (() => {
        if (step === 'source') return !!source && (source !== 'portfolio' || !!portfolioId)
        if (step === 'upload') return !!upload && !!sheet && sheet.rows > 0
        if (step === 'mapping') return !!sheet && mappingProblems(kind, mapping, attrs).length === 0
        return paramsValid(kind.key, source, params)
    })()

    const next = () => {
        if (step === 'mapping' && !canNext) {
            setShowProblems(true)
            return
        }
        if (!canNext) return
        setError(null)
        setStep(order[idx + 1])
    }
    const back = () => {
        setError(null)
        if (idx > 0) setStep(order[idx - 1])
    }

    const create = async () => {
        if (!source || busy || !paramsValid(kind.key, source, params)) return
        const body: BulkCreateBody = { source, params: buildParams(kind.key, source, params, attributes) }
        if (source === 'selection') body.case_ids = launch.caseIds || []
        if (source === 'filter' && launch.filter) body.filter = { list: launch.filter.list, query: launch.filter.query }
        if (source === 'portfolio') body.portfolio_id = portfolioId
        if (source === 'spreadsheet' && upload) {
            body.upload_id = upload.id
            body.sheet = sheetName
            body.mapping = mapping
        }
        setBusy(true)
        setError(null)
        try {
            const job = await bulkApi.create(kind.key, body, createKey)
            navigate(`/jobs/run/${job.id}`, { replace: true, state: { bulkLaunch: launch } })
        } catch (e) {
            setBusy(false)
            setError(e)
            if (isV2Error(e) && e.code === 'mapping_invalid') setStep('mapping')
        }
    }

    const backLink = launch.from ? (
        <Link to={launch.from.path} className="text-[13px] font-extrabold text-azure-600 hover:underline" data-testid="p2-back-to-caller">
            {t(launch.from.labelKey)}
        </Link>
    ) : (
        <Link to="/jobs" className="text-[13px] font-extrabold text-azure-600 hover:underline">
            {t('p2.jobs.title')}
        </Link>
    )

    return (
        <div data-testid="p2-wizard" data-kind={kind.key} data-step={step}>
            <PageHead title={title} subtitle={t('p2.wizard.subtitle')} actions={backLink} />
            <Stepper states={stepStates} />

            {step === 'source' && (
                <Panel title={t('p2.step.source')} testId="p2-step-source">
                    <SourceChoice kind={kind} launch={launch} value={source} onChange={setSource} portfolioId={portfolioId} onPortfolio={setPortfolioId} />
                </Panel>
            )}

            {step === 'upload' && (
                <Panel title={t('p2.step.upload')}>
                    <UploadStep
                        purpose={kind.key}
                        upload={upload}
                        sheet={sheetName}
                        onUploaded={(u) => {
                            setUpload(u)
                            setMapping({})
                            setShowProblems(false)
                        }}
                        onSheet={(name, sh) => {
                            setSheetName(name)
                            // prefill obvious matches for the chosen sheet; every choice stays editable
                            setMapping(guessMapping(kind.mapping_fields, sh.headers || []))
                            setShowProblems(false)
                        }}
                    />
                </Panel>
            )}

            {step === 'mapping' && sheet && (
                <Panel title={t('p2.step.mapping')}>
                    <MappingStep
                        kind={kind}
                        sheet={sheet}
                        mapping={mapping}
                        onMapping={setMapping}
                        attrs={attrs}
                        onAttrs={setAttrs}
                        params={params}
                        onParams={setParams}
                        showProblems={showProblems}
                    />
                    {error ? <InlineError error={error} describe={describeBulkError} /> : null}
                </Panel>
            )}

            {step === 'params' && (
                <Panel title={t('p2.step.params')} testId="p2-step-params">
                    <SourceSummary kind={kind} source={source} launch={launch} upload={upload} sheet={sheetName} portfolioId={portfolioId} />
                    <div className="max-w-[720px]">
                        <ParamsForm kind={kind.key} source={source} value={params} onChange={setParams} />
                    </div>
                    <InlineError error={error} describe={describeBulkError} />
                </Panel>
            )}

            <div className="flex flex-wrap items-center justify-between gap-2">
                <Button size="sm" icon={<HiArrowLeft className="rtl:rotate-180" />} disabled={idx === 0 || busy} onClick={back} data-testid="p2-back">
                    {t('p2.wizard.back')}
                </Button>
                {step !== 'params' ? (
                    <Button
                        size="sm"
                        variant="solid"
                        icon={<HiArrowRight className="rtl:rotate-180" />}
                        disabled={!canNext && step !== 'mapping'}
                        aria-disabled={!canNext}
                        onClick={next}
                        data-testid="p2-next"
                    >
                        {t('p2.wizard.next')}
                    </Button>
                ) : (
                    <Button
                        size="sm"
                        variant="solid"
                        loading={busy}
                        disabled={!canNext}
                        aria-disabled={!canNext || busy}
                        onClick={create}
                        data-testid="p2-create"
                    >
                        {kind.read_only ? t('p2.wizard.runInquiry') : t('p2.wizard.validate')}
                    </Button>
                )}
            </div>
            <Note className="mt-3">{kind.read_only ? t('p2.wizard.readOnlyNote') : t('p2.wizard.previewNote')}</Note>
        </div>
    )
}

/* -------------------------------------------------------------- source */

const SourceChoice = ({
    kind,
    launch,
    value,
    onChange,
    portfolioId,
    onPortfolio,
}: {
    kind: BulkKind
    launch: BulkLaunch
    value: BulkSource | undefined
    onChange: (s: BulkSource) => void
    portfolioId: string | undefined
    onPortfolio: (id: string | undefined) => void
}) => {
    const { t } = useTranslation()
    const [portfolios, setPortfolios] = useState<Option[]>([])
    useEffect(() => {
        if (!kind.sources.includes('portfolio')) return undefined
        let alive = true
        lookups
            .portfolios()
            .then((p) => alive && setPortfolios(p))
            .catch(() => undefined)
        return () => {
            alive = false
        }
    }, [kind.sources])
    const count = (s: BulkSource) => {
        if (s === 'selection') return t('p2.source.selectionCount', { n: formatCount(launch.caseIds?.length || 0) })
        if (s === 'filter') {
            const c = launch.filter?.count
            return c === null || c === undefined ? t('p2.source.filterNoCount') : t('p2.source.filterCount', { n: formatCount(c) })
        }
        if (s === 'portfolio') return launch.portfolioName || ''
        return ''
    }
    return (
        <div role="radiogroup" aria-label={String(t('p2.step.source'))} className="flex flex-col gap-2">
            {kind.sources.map((s) => {
                const ok = sourceAvailable(kind, s, launch)
                const active = value === s
                return (
                    <label
                        key={s}
                        data-testid="p2-source"
                        data-source={s}
                        data-available={ok ? '1' : '0'}
                        className={classNames(
                            'flex items-start gap-3 rounded-lg border px-4 py-3',
                            active ? 'border-azure-500 bg-azure-50 dark:bg-night-raised' : 'border-gray-300 dark:border-night-border',
                            ok ? 'cursor-pointer' : 'cursor-not-allowed opacity-60'
                        )}
                    >
                        <input type="radio" name="p2-source" className="mt-1" checked={active} disabled={!ok} onChange={() => onChange(s)} />
                        <span className="min-w-0 flex-1">
                            <span className="block text-[14px] font-extrabold text-navy-600 dark:text-gray-100">
                                {t(`p2.source.${s}`)}
                                {ok && count(s) && (
                                    <span className="ms-2 font-normal tabular-nums text-gray-700 dark:text-night-body" data-testid={`p2-source-count-${s}`}>
                                        {count(s)}
                                    </span>
                                )}
                            </span>
                            <span className="block text-[12.5px] text-gray-600">
                                {ok ? t(`p2.source.${s}Hint.${kind.key}`, { defaultValue: t(`p2.source.${s}Hint.default`) }) : t(`p2.source.${s}Unavailable`)}
                            </span>
                            {s === 'portfolio' && active && (
                                <span className="mt-2 block max-w-[420px]" data-testid="p2-source-portfolio">
                                    <Select
                                        size="sm"
                                        menuPosition="fixed"
                                        options={portfolios}
                                        value={portfolios.find((p) => p.value === portfolioId) || null}
                                        onChange={(o) => onPortfolio((o as Option | null)?.value)}
                                    />
                                </span>
                            )}
                        </span>
                    </label>
                )
            })}
        </div>
    )
}

/** One line saying what the job will run on (params step). */
export const SourceSummary = ({
    kind,
    source,
    launch,
    upload,
    sheet,
    portfolioId,
}: {
    kind: BulkKind
    source: BulkSource | undefined
    launch: BulkLaunch
    upload: BulkUpload | null
    sheet: string | undefined
    portfolioId: string | undefined
}) => {
    const { t } = useTranslation()
    let text = ''
    if (source === 'selection') text = t('p2.source.selectionCount', { n: formatCount(launch.caseIds?.length || 0) })
    if (source === 'filter') text = launch.filter?.count != null ? t('p2.source.filterCount', { n: formatCount(launch.filter.count) }) : t('p2.source.filter')
    if (source === 'portfolio') text = `${t('p2.source.portfolio')}: ${launch.portfolioId === portfolioId && launch.portfolioName ? launch.portfolioName : portfolioId || ''}`
    if (source === 'spreadsheet' && upload) text = t('p2.source.fileSummary', { file: upload.original_name, sheet: sheet || '', rows: formatCount(upload.sheets.find((s) => s.name === sheet)?.rows ?? upload.row_count) })
    if (source === 'query') text = t(`p2.source.queryHint.${kind.key}`, { defaultValue: t('p2.source.queryHint.default') })
    return (
        <div className="mb-4 rounded-md border border-gray-300 bg-gray-50 px-4 py-2.5 text-[13px] text-gray-700 dark:border-night-border dark:bg-night-raised dark:text-night-body" data-testid="p2-source-summary">
            <span className="font-extrabold text-navy-600 dark:text-gray-100">{t('p2.step.source')}: </span>
            <bdi>{text}</bdi>
        </div>
    )
}

export default NewJobPage
