/**
 * Campaign customers (customer feedback 2026-10-24, migration 0086).
 *
 * - CaseImport: adds customers to a customer-list campaign (`pool.case_list`) from an Excel /
 *   CSV file (one column) or pasted values, matched by the national id OR the account number,
 *   never both (`POST /v2/campaigns/:id/cases/import`). A national id adds every account of that
 *   customer; an account number adds that account only. The result lists the values that match
 *   nothing in my scope. The list itself is reviewed (and customers removed) in the Pool tab.
 * - CalledTab: the customers the campaign called (`GET /v2/campaigns/:id/called`): attempts,
 *   whether they ever answered, the last call's result and agent, with called / answered counts.
 */
import { useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import Button from '@/components/ui/Button'
import Input from '@/components/ui/Input'
import Table from '@/components/ui/Table'
import { RiyadhTime } from '@/components/v2/Display'
import { EmptyState, ErrorState, SkeletonRows } from '@/components/v2/States'
import { PagerFooter, TableFrame } from '@/components/v2/ListParts'
import { campaignsApi, describeP5Error, newIdempotencyKey } from '@/services/v2'
import type { Campaign, CallState, CaseImportResult, Outcome, Translate } from '@/services/v2'
import { useV2Query } from '@/utils/v2/hooks'
import { formatCount } from '@/utils/v2/format'
import { Segmented } from '@/views/phase1/followUps/parts'
import { CallStateBadge, Card, CaseCell, Fact, Notice, OutcomeBadge } from '../parts'
import { reasonLabel } from '../labels'

const { THead, TBody, Tr, Th, Td } = Table
const describe = (e: unknown, t: Translate) => describeP5Error(e, t)
type MatchBy = 'identity' | 'account'

export const CaseImport = ({ campaign, onImported }: { campaign: Campaign; onImported: () => void }) => {
    const { t } = useTranslation()
    const [matchBy, setMatchBy] = useState<MatchBy>('identity')
    const [file, setFile] = useState<File | null>(null)
    const [column, setColumn] = useState('1')
    const [text, setText] = useState('')
    const [busy, setBusy] = useState<'file' | 'paste' | null>(null)
    const [result, setResult] = useState<CaseImportResult | null>(null)
    const [error, setError] = useState<string | null>(null)
    const fileInput = useRef<HTMLInputElement>(null)
    const pasted = text
        .split(/[\n,;\t]+/)
        .map((v) => v.trim())
        .filter(Boolean)

    const run = async (mode: 'file' | 'paste') => {
        setBusy(mode)
        setError(null)
        try {
            const base = { match_by: matchBy, expected_revision: campaign.revision }
            const res =
                mode === 'file' && file
                    ? await campaignsApi.importCases(campaign.id, { ...base, file, column: Number(column) || 1 }, newIdempotencyKey())
                    : await campaignsApi.importCases(campaign.id, { ...base, values: pasted }, newIdempotencyKey())
            setResult(res)
            if (mode === 'file') {
                setFile(null)
                if (fileInput.current) fileInput.current.value = ''
            } else setText('')
            onImported()
        } catch (e) {
            setError(String(describe(e, t)))
        } finally {
            setBusy(null)
        }
    }

    return (
        <Card title={t('p12.campcases.import.title')} testId="p12-cd-import" className="mb-4">
            <div className="mb-3">
                <div className="text-eyebrow mb-1">{t('p12.campcases.import.matchBy')}</div>
                <Segmented<MatchBy>
                    value={matchBy}
                    onChange={setMatchBy}
                    testId="p12-cd-import-match"
                    label={String(t('p12.campcases.import.matchBy'))}
                    options={[
                        { value: 'identity', label: t('p12.campcases.import.identity') },
                        { value: 'account', label: t('p12.campcases.import.account') },
                    ]}
                />
                <div className="mt-1 text-[12px] text-gray-600">
                    {matchBy === 'identity' ? t('p12.campcases.import.identityHint') : t('p12.campcases.import.accountHint')}
                </div>
            </div>
            <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
                <div>
                    <div className="text-eyebrow mb-1">{t('p12.campcases.import.file')}</div>
                    <input
                        ref={fileInput}
                        type="file"
                        accept=".xlsx,.xlsm,.csv"
                        className="block w-full text-[13px]"
                        onChange={(e) => setFile(e.target.files?.[0] || null)}
                        data-testid="p12-cd-import-file"
                    />
                    <div className="mt-2 flex items-end gap-2">
                        <div className="w-24">
                            <div className="text-eyebrow mb-1">{t('p12.campcases.import.column')}</div>
                            <Input
                                size="sm"
                                dir="ltr"
                                inputMode="numeric"
                                value={column}
                                onChange={(e) => setColumn(e.target.value.replace(/[^0-9]/g, '').slice(0, 2))}
                                data-testid="p12-cd-import-column"
                            />
                        </div>
                        <Button size="sm" variant="solid" disabled={!file || busy !== null} loading={busy === 'file'} onClick={() => run('file')} data-testid="p12-cd-import-upload">
                            {t('p12.campcases.import.upload')}
                        </Button>
                    </div>
                    <div className="mt-1 text-[12px] text-gray-600">{t('p12.campcases.import.columnHint')}</div>
                </div>
                <div>
                    <div className="text-eyebrow mb-1">{t('p12.campcases.import.paste')}</div>
                    <Input textArea dir="ltr" value={text} onChange={(e) => setText(e.target.value)} data-testid="p12-cd-import-paste" />
                    <Button
                        size="sm"
                        className="mt-2"
                        disabled={pasted.length === 0 || busy !== null}
                        loading={busy === 'paste'}
                        onClick={() => run('paste')}
                        data-testid="p12-cd-import-add"
                    >
                        {t('p12.campcases.import.addPasted')}
                        {pasted.length ? ` (${formatCount(pasted.length)})` : ''}
                    </Button>
                </div>
            </div>
            {error && (
                <Notice tone="danger" className="mt-3" testId="p12-cd-import-error">
                    {error}
                </Notice>
            )}
            {result && (
                <Notice tone={result.not_found_count ? 'warning' : 'success'} className="mt-3" testId="p12-cd-import-result">
                    <div>
                        {t('p12.campcases.import.result', {
                            matched: formatCount(result.matched_values),
                            values: formatCount(result.values),
                            added: formatCount(result.cases_added),
                        })}
                        {result.cases_restored > 0 ? ` ${t('p12.campcases.import.restored', { count: result.cases_restored })}` : ''}
                    </div>
                    {result.not_found_count > 0 && (
                        <div className="mt-1">
                            {t('p12.campcases.import.notFound', { count: result.not_found_count })}{' '}
                            <span dir="ltr" className="break-all font-mono text-[12px]">
                                {result.not_found.join('، ')}
                                {result.not_found_count > result.not_found.length ? ' …' : ''}
                            </span>
                        </div>
                    )}
                </Notice>
            )}
        </Card>
    )
}

type Answered = 'all' | '1' | '0'

export const CalledTab = ({ campaignId, page, onState }: { campaignId: string; page: number; onState: (p: { page: number }) => void }) => {
    const { t } = useTranslation()
    const [answered, setAnswered] = useState<Answered>('all')
    const q = useV2Query(
        (signal) => campaignsApi.called(campaignId, { answered: answered === 'all' ? undefined : answered, page, page_size: 25 }, signal),
        [campaignId, answered, page]
    )
    const data = q.data
    return (
        <div data-testid="p12-cd-called-tab">
            <div className="mb-3 flex flex-wrap items-end justify-between gap-3">
                <Segmented<Answered>
                    value={answered}
                    onChange={(v) => {
                        setAnswered(v)
                        onState({ page: 1 })
                    }}
                    testId="p12-cd-called-filter"
                    options={[
                        { value: 'all', label: t('p12.campcases.called.all') },
                        { value: '1', label: t('p12.campcases.called.answered') },
                        { value: '0', label: t('p12.campcases.called.notAnswered') },
                    ]}
                />
                {data && (
                    <div className="flex gap-6">
                        <Fact label={t('p12.campcases.progress.called')} testId="p12-cd-called-count">
                            {formatCount(data.counts.called)}
                        </Fact>
                        <Fact label={t('p12.campcases.progress.answered')} testId="p12-cd-answered-count">
                            {formatCount(data.counts.answered)}
                        </Fact>
                    </div>
                )}
            </div>
            <TableFrame footer={data && data.total > 0 ? <PagerFooter page={page} pageSize={25} total={data.total} onPage={(p) => onState({ page: p })} /> : undefined}>
                {q.error && !data ? (
                    <ErrorState error={q.error} onRetry={q.reload} describe={describe} />
                ) : (
                    <Table>
                        <THead>
                            <Tr>
                                <Th>{t('p5.col.case')}</Th>
                                <Th className="text-end">{t('p12.campcases.called.col.attempts')}</Th>
                                <Th>{t('p12.campcases.called.col.answered')}</Th>
                                <Th>{t('p12.campcases.called.col.lastCall')}</Th>
                                <Th>{t('p12.campcases.called.col.lastResult')}</Th>
                                <Th>{t('p12.campcases.called.col.agent')}</Th>
                            </Tr>
                        </THead>
                        <TBody>
                            {q.loading && !data ? (
                                <SkeletonRows cols={6} />
                            ) : data && data.items.length ? (
                                data.items.map((it) => (
                                    <Tr key={it.case.id} data-testid="p12-cd-called-row" data-answered={it.answered ? '1' : '0'}>
                                        <Td>
                                            <CaseCell kase={{ ...it.case, missing: false }} />
                                        </Td>
                                        <Td className="text-end tabular-nums">{it.attempts}</Td>
                                        <Td>
                                            <span className={it.answered ? 'font-bold text-success-600 dark:text-success-400' : 'text-gray-600'}>
                                                {it.answered ? t('p12.campcases.called.yes') : t('p12.campcases.called.no')}
                                            </span>
                                        </Td>
                                        <Td>
                                            <RiyadhTime iso={it.last_call_at} />
                                        </Td>
                                        <Td>
                                            <div className="flex flex-wrap items-center gap-1">
                                                {it.last_outcome ? (
                                                    <OutcomeBadge outcome={it.last_outcome as Outcome} />
                                                ) : it.last_state ? (
                                                    <CallStateBadge state={it.last_state as CallState} />
                                                ) : null}
                                                {it.last_reason && <span className="text-[12px] text-gray-600">{reasonLabel(t, it.last_reason)}</span>}
                                            </div>
                                        </Td>
                                        <Td>{it.last_agent?.name || '—'}</Td>
                                    </Tr>
                                ))
                            ) : (
                                <tr>
                                    <td colSpan={6}>
                                        <EmptyState title={t('p12.campcases.called.empty')} />
                                    </td>
                                </tr>
                            )}
                        </TBody>
                    </Table>
                )}
            </TableFrame>
            <div className="mt-2 text-[12px] text-gray-600">{t('p12.campcases.called.rule')}</div>
        </div>
    )
}
