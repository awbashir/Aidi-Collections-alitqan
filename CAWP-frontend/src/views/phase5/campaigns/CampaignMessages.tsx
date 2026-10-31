/**
 * Message campaigns (customer request 2026-10-25, migration 0088): the PBX dials the client and plays
 * the uploaded message; 1 rings a free campaign member, 2 closes.
 *
 * - SoundsCard: upload / replace / listen to / remove the three messages (`GET|POST|DELETE
 *   /v2/campaigns/:id/sounds[/:kind]`, the converted WAV at `.../audio`). The server converts the
 *   file to 8 kHz mono WAV and installs it on the PBX; "not installed" shows the PBX's reason.
 *   Uploading is refused while the campaign runs (pause it first).
 * - MessageSummaryCard: the live view's `messages` (calls now, clients answered, the queue, results).
 * - MessageCallsTab: every dial (`GET /v2/campaigns/:id/message-calls`), filterable by result.
 */
import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import Button from '@/components/ui/Button'
import Select from '@/components/ui/Select'
import Table from '@/components/ui/Table'
import { Ltr, RiyadhTime } from '@/components/v2/Display'
import { EmptyState, ErrorState, SkeletonRows } from '@/components/v2/States'
import { PagerFooter, TableFrame } from '@/components/v2/ListParts'
import ToastNotification from '@/components/shared/myComponents/ToastNotification'
import { campaignsApi, describeP5Error, newIdempotencyKey } from '@/services/v2'
import type { Campaign, CampaignSound, MessageCallResult, MessageSummary, Option, SoundKind, Translate } from '@/services/v2'
import { useV2Query } from '@/utils/v2/hooks'
import { formatCount } from '@/utils/v2/format'
import { Card, CaseCell, Fact, Notice } from '../parts'

const { THead, TBody, Tr, Th, Td } = Table
const describe = (e: unknown, t: Translate) => describeP5Error(e, t)
const KINDS: SoundKind[] = ['message', 'callback', 'goodbye']
const RESULTS: MessageCallResult[] = [
    'transferred', 'no_agent', 'closed', 'no_choice', 'hung_up', 'no_answer', 'busy', 'unavailable', 'congestion',
    'failed', 'denied', 'no_trunk', 'unknown',
]

/* ------------------------------------------------------------------ sounds */

const SoundRow = ({ campaign, kind, sound, editable, onChanged }: {
    campaign: Campaign
    kind: SoundKind
    sound: CampaignSound | undefined
    editable: boolean
    onChanged: () => void
}) => {
    const { t } = useTranslation()
    const input = useRef<HTMLInputElement>(null)
    const [busy, setBusy] = useState(false)
    const [url, setUrl] = useState<string | null>(null)
    const [pbxError, setPbxError] = useState<string | null>(null)
    useEffect(() => () => {
        if (url) URL.revokeObjectURL(url)
    }, [url])
    const required = kind === 'message' || (kind === 'callback' && campaign.message?.transfer)
    const upload = async (file: File) => {
        setBusy(true)
        try {
            const res = await campaignsApi.uploadSound(campaign.id, kind, file, newIdempotencyKey())
            setPbxError(res.pbx_error || null)
            if (url) URL.revokeObjectURL(url)
            setUrl(null)
            ToastNotification(String(t('p12.msg.sound.uploaded')), campaign.name, res.installed ? 'success' : 'warning')
            onChanged()
        } catch (e) {
            ToastNotification(String(describe(e, t)), campaign.name, 'danger')
        } finally {
            setBusy(false)
            if (input.current) input.current.value = ''
        }
    }
    const remove = async () => {
        setBusy(true)
        try {
            await campaignsApi.deleteSound(campaign.id, kind, newIdempotencyKey())
            if (url) URL.revokeObjectURL(url)
            setUrl(null)
            onChanged()
        } catch (e) {
            ToastNotification(String(describe(e, t)), campaign.name, 'danger')
        } finally {
            setBusy(false)
        }
    }
    const listen = async () => {
        try {
            setUrl(await campaignsApi.soundUrl(campaign.id, kind))
        } catch (e) {
            ToastNotification(String(describe(e, t)), campaign.name, 'danger')
        }
    }
    return (
        <Tr data-testid={`p12-msg-sound-${kind}`} data-installed={sound?.installed ? '1' : '0'}>
            <Td>
                <div className="font-bold">{t(`p12.msg.sound.kind.${kind}`)}</div>
                <div className="text-[12px] text-gray-600">
                    {t(`p12.msg.sound.hint.${kind}`)}
                    {required ? ` · ${t('p12.msg.sound.required')}` : ''}
                </div>
            </Td>
            <Td>
                {sound ? (
                    <div className="flex flex-col">
                        <span dir="auto">{sound.name || '—'}</span>
                        <span className="text-[12px] text-gray-600 tabular-nums">{t('p12.msg.sound.seconds', { n: sound.duration_seconds })}</span>
                    </div>
                ) : (
                    <span className="text-gray-500">{t('p12.msg.sound.none')}</span>
                )}
            </Td>
            <Td>
                {sound ? (
                    sound.installed ? (
                        <span className="font-bold text-success-600 dark:text-success-400">{t('p12.msg.sound.installed')}</span>
                    ) : (
                        <span className="font-bold text-warning-600" title={pbxError || undefined}>
                            {t('p12.msg.sound.notInstalled')}
                            {pbxError ? ` (${pbxError})` : ''}
                        </span>
                    )
                ) : (
                    '—'
                )}
            </Td>
            <Td>
                <div className="flex flex-wrap items-center gap-2">
                    <input
                        ref={input}
                        type="file"
                        accept=".wav,.mp3,.m4a,.ogg,audio/*"
                        className="hidden"
                        onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])}
                        data-testid={`p12-msg-sound-file-${kind}`}
                    />
                    <Button size="xs" variant="solid" disabled={!editable || busy} loading={busy} onClick={() => input.current?.click()}>
                        {sound ? t('p12.msg.sound.replace') : t('p12.msg.sound.upload')}
                    </Button>
                    {sound && (
                        <Button size="xs" onClick={listen} disabled={busy}>
                            {t('p12.msg.sound.listen')}
                        </Button>
                    )}
                    {sound && (
                        <Button size="xs" variant="plain" disabled={!editable || busy} onClick={remove}>
                            {t('p12.msg.sound.remove')}
                        </Button>
                    )}
                </div>
                {url && (
                    // a recorded prompt preview: the text is the operator's own script, no caption track exists
                    // eslint-disable-next-line jsx-a11y/media-has-caption
                    <audio className="mt-2 w-full max-w-[320px]" src={url} controls autoPlay data-testid={`p12-msg-sound-audio-${kind}`} />
                )}
            </Td>
        </Tr>
    )
}

/** 2026-10-31 (0090, 0091): the numbers the client sees on this campaign's calls, used in rotation. None = automatic. */
export const CallerIdTab = ({ campaign, onChanged }: { campaign: Campaign; onChanged: () => void }) => {
    const { t } = useTranslation()
    const q = useV2Query((signal) => campaignsApi.outboundNumbers(signal), [])
    const current = (campaign.message?.caller_ids || []).join(',')
    const [value, setValue] = useState<string[]>(campaign.message?.caller_ids || [])
    const [saving, setSaving] = useState(false)
    useEffect(() => setValue(current ? current.split(',') : []), [current])
    const items = q.data?.items || []
    const label = (n: string) => (items.find((i) => i.number === n)?.pbx_trunk === null ? `${n} — ${t('p12.msg.caller.noTrunkShort')}` : n)
    const options: Option[] = items.map((n) => ({ value: n.number, label: label(n.number) }))
    const noTrunk = value.some((n) => items.find((i) => i.number === n)?.pbx_trunk === null)
    const editable = campaign.status !== 'stopped'
    const save = async () => {
        setSaving(true)
        try {
            await campaignsApi.update(campaign.id, { expected_revision: campaign.revision, message_caller_ids: value }, newIdempotencyKey())
            ToastNotification(String(t('p12.msg.caller.saved')), campaign.name, 'success')
            onChanged()
        } catch (e) {
            ToastNotification(String(describe(e, t)), campaign.name, 'danger')
        } finally {
            setSaving(false)
        }
    }
    return (
        <Card title={t('p12.msg.caller.title')} testId="p12-msg-caller">
            <div className="mb-3 text-[12.5px] text-gray-600">{t('p12.msg.caller.hint')}</div>
            {q.error && !q.data ? (
                <ErrorState error={q.error} onRetry={q.reload} describe={describe} />
            ) : (
                <>
                    <label className="flex w-full max-w-[640px] flex-col gap-1 text-[12.5px]">
                        <span className="text-gray-600">{t('p12.msg.caller.label')}</span>
                        <Select
                            size="sm"
                            isMulti
                            isSearchable
                            closeMenuOnSelect={false}
                            isDisabled={!editable}
                            isLoading={q.loading}
                            placeholder={t('p12.msg.caller.auto')}
                            options={options}
                            value={value.map((n) => ({ value: n, label: label(n) }))}
                            onChange={(v) => setValue(((v as Option[] | null) || []).map((o) => o.value))}
                        />
                    </label>
                    <div className="mt-3 flex flex-wrap items-center gap-3">
                        <Button size="sm" variant="solid" loading={saving} disabled={!editable || saving || value.join(',') === current} onClick={save} data-testid="p12-msg-caller-save">
                            {t('p12.msg.caller.save')}
                        </Button>
                        {editable && (
                            <>
                                <button type="button" className="text-[12.5px] font-extrabold text-navy-600 underline dark:text-gray-100" onClick={() => setValue(items.map((i) => i.number))} data-testid="p12-msg-caller-all">
                                    {t('p12.msg.caller.selectAll', { n: items.length })}
                                </button>
                                {value.length > 0 && (
                                    <button type="button" className="text-[12.5px] font-extrabold text-navy-600 underline dark:text-gray-100" onClick={() => setValue([])}>
                                        {t('p12.msg.caller.clear')}
                                    </button>
                                )}
                            </>
                        )}
                        <span className="text-[12.5px] text-gray-600" data-testid="p12-msg-caller-count">
                            {value.length ? t('p12.msg.caller.count', { n: value.length }) : t('p12.msg.caller.auto')}
                        </span>
                    </div>
                </>
            )}
            {noTrunk && (
                <Notice tone="warning" className="mt-3" testId="p12-msg-caller-no-trunk">
                    {t('p12.msg.caller.noTrunk')}
                </Notice>
            )}
            {campaign.status === 'running' && <div className="mt-3 text-[12px] text-gray-600">{t('p12.msg.caller.running')}</div>}
        </Card>
    )
}

export const SoundsCard = ({ campaign, onChanged }: { campaign: Campaign; onChanged?: () => void }) => {
    const { t } = useTranslation()
    const q = useV2Query((signal) => campaignsApi.sounds(campaign.id, signal), [campaign.id, campaign.revision])
    const editable = campaign.status !== 'running' && campaign.status !== 'stopped'
    const items = q.data?.items || []
    const reload = () => {
        q.reload()
        onChanged?.()
    }
    return (
        <Card title={t('p12.msg.sound.title')} testId="p12-msg-sounds" className="mb-4">
            {!editable && campaign.status === 'running' && (
                <Notice tone="info" className="mb-2">
                    {t('p12.msg.sound.pauseFirst')}
                </Notice>
            )}
            <div className="mb-2 text-[12px] text-gray-600">{t('p12.msg.sound.formats')}</div>
            {q.error && !q.data ? (
                <ErrorState error={q.error} onRetry={q.reload} describe={describe} />
            ) : (
                <Table>
                    <THead>
                        <Tr>
                            <Th>{t('p12.msg.sound.col.kind')}</Th>
                            <Th>{t('p12.msg.sound.col.file')}</Th>
                            <Th>{t('p12.msg.sound.col.pbx')}</Th>
                            <Th>{t('p12.campcases.col.action')}</Th>
                        </Tr>
                    </THead>
                    <TBody>
                        {KINDS.map((k) => (
                            <SoundRow key={k} campaign={campaign} kind={k} sound={items.find((s) => s.kind === k)} editable={editable} onChanged={reload} />
                        ))}
                    </TBody>
                </Table>
            )}
        </Card>
    )
}

/* ------------------------------------------------------------------ live summary */

export const MessageSummaryCard = ({ summary }: { summary: MessageSummary }) => {
    const { t } = useTranslation()
    const results = RESULTS.filter((r) => summary.results[r])
    return (
        <Card title={t('p12.msg.live.title')} testId="p12-msg-live">
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                <Fact label={t('p12.msg.live.dialing')} testId="p12-msg-live-dialing">
                    {formatCount(summary.now.dialing)} / {formatCount(summary.max_concurrent)}
                </Fact>
                <Fact label={t('p12.msg.live.listening')}>{formatCount(summary.now.listening)}</Fact>
                <Fact label={t('p12.msg.live.transferring')}>{formatCount(summary.now.transferring)}</Fact>
                <Fact label={t('p12.msg.live.talking')}>{formatCount(summary.now.talking)}</Fact>
                <Fact label={t('p12.campcases.progress.called')} testId="p12-msg-live-called">{formatCount(summary.clients_called)}</Fact>
                <Fact label={t('p12.campcases.progress.answered')} testId="p12-msg-live-answered">{formatCount(summary.clients_answered)}</Fact>
                <Fact label={t('p12.campcases.progress.waiting')} testId="p12-msg-live-waiting">{formatCount(summary.waiting)}</Fact>
                <Fact label={t('p12.msg.live.calls')}>{formatCount(summary.calls)}</Fact>
            </div>
            {results.length > 0 && (
                <div className="mt-3" data-testid="p12-msg-live-results">
                    <div className="text-eyebrow mb-1">{t('p12.msg.live.results')}</div>
                    <ul className="grid grid-cols-1 gap-1 text-[12.5px] sm:grid-cols-2">
                        {results.map((r) => (
                            <li key={r} className="flex justify-between gap-2" data-result={r}>
                                <span>{t(`p12.msg.result.${r}`)}</span>
                                <span className="tabular-nums">{formatCount(summary.results[r] || 0)}</span>
                            </li>
                        ))}
                    </ul>
                </div>
            )}
            {summary.uncertain > 0 && (
                <div className="mt-2 text-[12px] text-warning-600">{t('p12.msg.live.uncertain', { n: summary.uncertain })}</div>
            )}
            {summary.sounds.some((s) => !s.installed) && (
                <Notice tone="warning" className="mt-2">
                    {t('p12.msg.live.soundsMissing')}
                </Notice>
            )}
        </Card>
    )
}

/* ------------------------------------------------------------------ calls */

export const MessageCallsTab = ({ campaignId, page, onState }: { campaignId: string; page: number; onState: (p: { page: number }) => void }) => {
    const { t } = useTranslation()
    const [result, setResult] = useState<string | undefined>(undefined)
    const q = useV2Query((signal) => campaignsApi.messageCalls(campaignId, { result, page, page_size: 25 }, signal), [campaignId, result, page])
    const data = q.data
    const options: Option[] = RESULTS.map((r) => ({ value: r, label: String(t(`p12.msg.result.${r}`)) }))
    return (
        <div data-testid="p12-msg-calls">
            <div className="mb-3 w-[260px]">
                <div className="text-eyebrow mb-1">{t('p12.msg.calls.result')}</div>
                <Select
                    size="sm"
                    isClearable
                    options={options}
                    value={options.find((o) => o.value === result) || null}
                    onChange={(o) => {
                        setResult((o as Option | null)?.value || undefined)
                        onState({ page: 1 })
                    }}
                />
            </div>
            <TableFrame footer={data && data.total > 0 ? <PagerFooter page={page} pageSize={25} total={data.total} onPage={(p) => onState({ page: p })} /> : undefined}>
                {q.error && !data ? (
                    <ErrorState error={q.error} onRetry={q.reload} describe={describe} />
                ) : (
                    <Table>
                        <THead>
                            <Tr>
                                <Th>{t('p5.col.case')}</Th>
                                <Th>{t('p12.msg.calls.when')}</Th>
                                <Th>{t('p12.msg.calls.state')}</Th>
                                <Th>{t('p12.msg.calls.result')}</Th>
                                <Th>{t('p12.msg.calls.key')}</Th>
                                <Th>{t('p12.msg.calls.member')}</Th>
                                <Th>{t('p12.msg.caller.col')}</Th>
                            </Tr>
                        </THead>
                        <TBody>
                            {q.loading && !data ? (
                                <SkeletonRows cols={7} />
                            ) : data && data.items.length ? (
                                data.items.map((it) => (
                                    <Tr key={it.id} data-testid="p12-msg-call-row" data-result={it.result || ''}>
                                        <Td>
                                            <CaseCell kase={{ ...it.case, missing: false }} />
                                        </Td>
                                        <Td>
                                            <RiyadhTime iso={it.created_at} />
                                        </Td>
                                        <Td>{t(`p12.msg.state.${it.state}`)}</Td>
                                        <Td>{it.result ? t(`p12.msg.result.${it.result}`) : '—'}</Td>
                                        <Td className="tabular-nums">{it.key || '—'}</Td>
                                        <Td>{it.member?.name || '—'}</Td>
                                        <Td>
                                            <Ltr className="tabular-nums">{it.caller_id || '—'}</Ltr>
                                        </Td>
                                    </Tr>
                                ))
                            ) : (
                                <tr>
                                    <td colSpan={7}>
                                        <EmptyState title={t('p12.msg.calls.empty')} />
                                    </td>
                                </tr>
                            )}
                        </TBody>
                    </Table>
                )}
            </TableFrame>
            <div className="mt-2 text-[12px] text-gray-600">{t('p12.msg.calls.rule')}</div>
        </div>
    )
}
