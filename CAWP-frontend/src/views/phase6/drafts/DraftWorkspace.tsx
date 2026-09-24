/**
 * The AI drafts workspace (`ai_assist`), used on the case page (tab "AI drafts") and, for a
 * call, from the call detail and the wrap-up.
 *
 * Choose the kind and the language → Generate (`POST /v2/ai/drafts`) → the draft, with the API's
 * "AI-generated, review before saving" notice, the items the server flagged (no case fact cited,
 * unknown fact ids, a coaching quote not found in the transcript) and the facts it cited → edit
 * the text (only in this browser until "Save edits", a new revision) → "Save as note…" (explicit
 * confirmation; the note's status / method as in the note form) or "Discard".
 *
 * Nothing is saved on its own and generating changes nothing on the case. Without a key, a model
 * or an approved data policy, or with the day's quota used up, the honest state is shown and
 * Generate stays disabled; a refusal that only the generation itself can reveal (a data category
 * the policy does not allow) is shown exactly as the API gives it. Nothing is simulated.
 */
import classNames from 'classnames'
import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Sparkles } from 'lucide-react'
import Button from '@/components/ui/Button'
import Input from '@/components/ui/Input'
import ActionDialog from '@/components/v2/ActionDialog'
import { Ltr, RiyadhTime } from '@/components/v2/Display'
import { EmptyState, ErrorState, InlineError, Note, SkeletonLines } from '@/components/v2/States'
import { PagerFooter } from '@/components/v2/ListParts'
import ToastNotification from '@/components/shared/myComponents/ToastNotification'
import { Segmented } from '@/views/phase1/followUps/parts'
import { aiApi, categoryLabel, describeP6Error, draftsApi, newIdempotencyKey } from '@/services/v2'
import type { AiDraft, DraftKind, DraftLanguage, DraftOptions, SavedNote, Translate } from '@/services/v2'
import { useAppSelector } from '@/store'
import { useV2Query } from '@/utils/v2/hooks'
import { Card, Fact, Notice } from '@/views/phase5/parts'
import { AiErrorNotice, AiGeneratedNotice, AiReadinessNotice, CheckField, DraftStatusBadge, QuotaLine } from '../parts'
import { draftKindLabel } from '../labels'
import SaveAsNoteDialog from './SaveAsNoteDialog'
import { flagged, flaggedItemText, isLive } from './helpers'
import type { DraftSource } from './helpers'

const describe = (e: unknown, t: Translate) => describeP6Error(e, t)
const LIST_SIZE = 10

/* ------------------------------------------------------------------ generate form */

const GenerateForm = ({
    kinds,
    disabled,
    busy,
    onGenerate,
}: {
    kinds: DraftKind[]
    disabled: boolean
    busy: boolean
    onGenerate: (kind: DraftKind, language: DraftLanguage, options: DraftOptions) => void
}) => {
    const { t, i18n } = useTranslation()
    const [kind, setKind] = useState<DraftKind>(kinds[0])
    const [language, setLanguage] = useState<DraftLanguage>((i18n.language || 'ar').startsWith('ar') ? 'ar' : 'en')
    const [audience, setAudience] = useState<'supervisor' | 'legal'>('supervisor')
    const [channel, setChannel] = useState<'sms' | 'whatsapp' | 'email'>('sms')
    const [withTranscript, setWithTranscript] = useState(false)
    const options: DraftOptions =
        kind === 'narrative' ? { audience } : kind === 'followup_message' ? { channel } : kind === 'call_summary' ? { include_transcript: withTranscript } : {}
    return (
        <div data-testid="p6-draft-generate">
            <div className="text-eyebrow mb-2">{t('p6.drafts.kind')}</div>
            <div role="radiogroup" aria-label={String(t('p6.drafts.kind'))} className="mb-3 grid grid-cols-1 gap-2 sm:grid-cols-2" data-testid="p6-draft-kinds">
                {kinds.map((k) => {
                    const active = k === kind
                    return (
                        <button
                            key={k}
                            type="button"
                            role="radio"
                            aria-checked={active}
                            data-kind={k}
                            onClick={() => setKind(k)}
                            className={classNames(
                                'flex min-h-[44px] flex-col items-start rounded-md border px-3 py-2 text-start focus:outline-none focus-visible:ring-2 focus-visible:ring-azure-500',
                                active ? 'border-azure-500 bg-azure-50 dark:bg-night-raised' : 'border-gray-300 bg-white hover:bg-gray-50 dark:border-night-border dark:bg-night-card'
                            )}
                        >
                            <span className={classNames('text-[13.5px]', active ? 'font-extrabold text-navy-600 dark:text-gray-100' : 'text-gray-800 dark:text-night-body')}>
                                {draftKindLabel(t, k)}
                            </span>
                            <span className="text-[11.5px] text-gray-600">{t(`p6.kindHint.${k}`)}</span>
                        </button>
                    )
                })}
            </div>
            <div className="mb-3 flex flex-wrap items-end gap-4">
                <div>
                    <div className="text-eyebrow mb-1">{t('p6.drafts.language')}</div>
                    <Segmented<DraftLanguage>
                        value={language}
                        options={[
                            { value: 'ar', label: 'العربية' },
                            { value: 'en', label: 'English' },
                        ]}
                        onChange={setLanguage}
                        testId="p6-draft-language"
                    />
                </div>
                {kind === 'narrative' && (
                    <div>
                        <div className="text-eyebrow mb-1">{t('p6.drafts.audience')}</div>
                        <Segmented
                            value={audience}
                            options={[
                                { value: 'supervisor', label: t('p6.drafts.audienceValue.supervisor') },
                                { value: 'legal', label: t('p6.drafts.audienceValue.legal') },
                            ]}
                            onChange={setAudience}
                            testId="p6-draft-audience"
                        />
                    </div>
                )}
                {kind === 'followup_message' && (
                    <div>
                        <div className="text-eyebrow mb-1">{t('p6.drafts.channel')}</div>
                        <Segmented
                            value={channel}
                            options={(['sms', 'whatsapp', 'email'] as const).map((c) => ({ value: c, label: t(`p6.drafts.channelValue.${c}`) }))}
                            onChange={setChannel}
                            testId="p6-draft-channel"
                        />
                    </div>
                )}
                {kind === 'call_summary' && (
                    <CheckField checked={withTranscript} onChange={setWithTranscript} testId="p6-draft-include-transcript">
                        <span className="text-[13px]">{t('p6.drafts.includeTranscript')}</span>
                    </CheckField>
                )}
            </div>
            {kind === 'followup_message' && <Note className="mb-3">{t('p6.drafts.followupNeverSent')}</Note>}
            {kind === 'coaching' && <Note className="mb-3">{t('p6.drafts.coachingNote')}</Note>}
            <Button
                size="sm"
                variant="solid"
                icon={<Sparkles size={15} />}
                loading={busy}
                disabled={disabled}
                aria-disabled={disabled}
                onClick={() => onGenerate(kind, language, options)}
                data-testid="p6-draft-generate-btn"
            >
                {t('p6.drafts.generate')}
            </Button>
        </div>
    )
}

/* ------------------------------------------------------------------ one draft */

const DraftEditor = ({
    draft,
    mine,
    contractId,
    caseLocked,
    caseArchived,
    onChanged,
    onSaved,
    onUseText,
}: {
    draft: AiDraft
    mine: boolean
    contractId: string | null
    caseLocked?: boolean
    caseArchived?: boolean
    onChanged: (d?: AiDraft) => void
    onSaved: (res: SavedNote) => void
    onUseText?: (text: string) => void
}) => {
    const { t } = useTranslation()
    const [text, setText] = useState(draft.text || '')
    const [saving, setSaving] = useState(false)
    const [error, setError] = useState<unknown>(null)
    const [noteOpen, setNoteOpen] = useState(false)
    const [discardOpen, setDiscardOpen] = useState(false)
    const [editKey, setEditKey] = useState(() => newIdempotencyKey())
    useEffect(() => {
        setText(draft.text || '')
        setError(null)
    }, [draft.id, draft.revision, draft.text])
    const live = isLive(draft)
    const editable = live && mine
    const dirty = text !== (draft.text || '')
    const flags = flagged(draft.checks)
    const citations = Object.entries(draft.citations || {})

    const saveEdits = async () => {
        setSaving(true)
        setError(null)
        try {
            await draftsApi.edit(draft.id, { text, expected_revision: draft.revision }, editKey)
            setEditKey(newIdempotencyKey())
            ToastNotification(String(t('p6.drafts.editSaved')), String(t('p6.drafts.title')), 'success', 2000)
            onChanged()
        } catch (e) {
            setError(e)
        } finally {
            setSaving(false)
        }
    }

    return (
        <div className="flex flex-col gap-3" data-testid="p6-draft" data-draft={draft.id} data-status={draft.status} data-revision={draft.revision} data-kind={draft.kind}>
            <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="flex flex-wrap items-center gap-2">
                    <span className="text-[15px] font-extrabold text-navy-600 dark:text-gray-100">{draftKindLabel(t, draft.kind)}</span>
                    <DraftStatusBadge status={draft.status} />
                    <span className="text-[12px] text-gray-600">{draft.language === 'ar' ? 'العربية' : 'English'}</span>
                </div>
                <span className="text-[12px] text-gray-600">
                    {draft.requested_by?.name || '—'} · <RiyadhTime iso={draft.created_at} />
                </span>
            </div>
            <AiGeneratedNotice text={draft.notice} />
            {(flags.unsupported.length > 0 || flags.unverified.length > 0 || flags.unknownFacts.length > 0) && (
                <Notice tone="warning" title={t('p6.drafts.flaggedTitle')} testId="p6-draft-flagged">
                    {flags.unsupported.length > 0 && (
                        <div className="mb-1" data-testid="p6-draft-unsupported" data-count={flags.unsupported.length}>
                            <div>{t('p6.drafts.unsupported')}</div>
                            <ul className="list-disc ps-5">
                                {flags.unsupported.map((p) => (
                                    <li key={p} dir="auto">
                                        {flaggedItemText(draft, p)}
                                    </li>
                                ))}
                            </ul>
                        </div>
                    )}
                    {flags.unverified.length > 0 && (
                        <div className="mb-1" data-testid="p6-draft-unverified" data-count={flags.unverified.length}>
                            <div>{t('p6.drafts.unverified')}</div>
                            <ul className="list-disc ps-5">
                                {flags.unverified.map((p) => (
                                    <li key={p} dir="auto">
                                        {flaggedItemText(draft, p)}
                                    </li>
                                ))}
                            </ul>
                        </div>
                    )}
                    {flags.unknownFacts.length > 0 && (
                        <div data-testid="p6-draft-unknown-facts">
                            {t('p6.drafts.unknownFacts', { ids: flags.unknownFacts.join(', ') })}
                        </div>
                    )}
                </Notice>
            )}
            {draft.content_available ? (
                <div>
                    <div className="mb-1 flex items-center justify-between gap-2">
                        <span className="text-eyebrow">{t('p6.drafts.text')}</span>
                        {dirty && (
                            <span className="text-[12px] font-extrabold text-warning" data-testid="p6-draft-dirty">
                                {t('p6.drafts.unsaved')}
                            </span>
                        )}
                    </div>
                    <Input
                        textArea
                        dir="auto"
                        value={text}
                        readOnly={!editable}
                        maxLength={20000}
                        onChange={(e) => setText(e.target.value)}
                        data-testid="p6-draft-text"
                        className="min-h-[220px] font-normal"
                    />
                    {!mine && live && <Note className="mt-1">{t('p6.drafts.notMine')}</Note>}
                </div>
            ) : (
                <Notice tone="neutral" testId="p6-draft-no-content">
                    {draft.status === 'discarded' ? t('p6.drafts.discardedNoContent') : t('p6.drafts.purgedNoContent')}
                </Notice>
            )}
            {draft.status === 'saved_as_note' && (
                <Notice tone="success" testId="p6-draft-saved-note">
                    {t('p6.drafts.savedAsNote', { by: draft.saved_by?.name || '—' })} <RiyadhTime iso={draft.saved_at || null} />
                </Notice>
            )}
            <InlineError error={error} describe={describe} onReload={() => onChanged()} />
            {editable && (
                <div className="flex flex-wrap items-center gap-2">
                    <Button size="sm" variant="solid" onClick={() => setNoteOpen(true)} disabled={!text.trim()} data-testid="p6-draft-save-note">
                        {t('p6.drafts.saveAsNote')}
                    </Button>
                    <Button size="sm" loading={saving} disabled={!dirty || !text.trim()} aria-disabled={!dirty || !text.trim()} onClick={saveEdits} data-testid="p6-draft-save-edits">
                        {t('p6.drafts.saveEdits')}
                    </Button>
                    {dirty && (
                        <Button size="sm" variant="plain" onClick={() => setText(draft.text || '')} data-testid="p6-draft-revert">
                            {t('p6.drafts.revert')}
                        </Button>
                    )}
                    {onUseText && (
                        <Button size="sm" variant="plain" onClick={() => onUseText(text)} data-testid="p6-draft-use-text">
                            {t('p6.drafts.useInWrapup')}
                        </Button>
                    )}
                    <Button size="sm" variant="plain" className="ms-auto text-danger" onClick={() => setDiscardOpen(true)} data-testid="p6-draft-discard">
                        {t('p6.drafts.discard')}
                    </Button>
                </div>
            )}
            {editable && <Note>{t('p6.drafts.nothingAutoSaved')}</Note>}
            <details className="rounded-md border border-gray-300 px-3 py-2 text-[12.5px] dark:border-night-border" data-testid="p6-draft-provenance">
                <summary className="cursor-pointer font-extrabold text-navy-600 dark:text-gray-100">{t('p6.drafts.provenance')}</summary>
                <div className="mt-2 grid grid-cols-2 gap-2">
                    <Fact label={t('p6.drafts.model')}>{draft.provenance.model ? <Ltr>{draft.provenance.model}</Ltr> : '—'}</Fact>
                    <Fact label={t('p6.drafts.policyRevision')}>
                        <Ltr>{draft.provenance.policy_revision ?? '—'}</Ltr>
                    </Fact>
                    <Fact label={t('p6.drafts.categoriesSent')}>{draft.input.categories.map((c) => categoryLabel(t, c)).join(String(t('p6.listSep'))) || '—'}</Fact>
                    <Fact label={t('p6.drafts.masked')}>
                        {draft.provenance.masked ? t('p6.drafts.maskedCount', { count: draft.provenance.masked_count ?? 0 }) : t('p6.calls.notMasked')}
                    </Fact>
                    <Fact label={t('p6.drafts.expires')}>
                        <RiyadhTime iso={draft.expires_at} />
                    </Fact>
                    <Fact label={t('p6.drafts.revision')}>
                        <Ltr>{draft.revision}</Ltr>
                        {draft.edit_count > 0 && <span className="ms-1 text-gray-600">({t('p6.drafts.edits', { count: draft.edit_count })})</span>}
                    </Fact>
                </div>
                {citations.length > 0 && (
                    <div className="mt-2" data-testid="p6-draft-citations">
                        <div className="text-eyebrow mb-1">{t('p6.drafts.citations')}</div>
                        <ul className="flex flex-col gap-1">
                            {citations.map(([id, line]) => (
                                <li key={id} dir="auto">
                                    <Ltr className="font-extrabold">{id}</Ltr> · {line}
                                </li>
                            ))}
                        </ul>
                    </div>
                )}
            </details>
            {editable && (
                <SaveAsNoteDialog
                    draft={draft}
                    text={text}
                    isOpen={noteOpen}
                    contractId={contractId}
                    caseLocked={caseLocked}
                    caseArchived={caseArchived}
                    onClose={() => setNoteOpen(false)}
                    onReload={() => onChanged()}
                    onSaved={(res) => {
                        ToastNotification(String(t('p6.drafts.noteSaved')), String(t('p6.drafts.title')), 'success', 3000)
                        onSaved(res)
                        onChanged(res.draft)
                    }}
                />
            )}
            <ActionDialog
                isOpen={discardOpen}
                title={t('p6.drafts.discardTitle')}
                confirmLabel={t('p6.drafts.discard')}
                tone="danger"
                testId="p6-draft-discard-dialog"
                describeError={describe}
                onClose={() => setDiscardOpen(false)}
                onReload={() => onChanged()}
                onSubmit={async (key) => {
                    const d = await draftsApi.discard(draft.id, draft.revision, key)
                    ToastNotification(String(t('p6.drafts.discarded')), String(t('p6.drafts.title')), 'success', 2000)
                    onChanged(d)
                }}
            >
                <p className="text-[13.5px] text-gray-700 dark:text-night-body">{t('p6.drafts.discardHint')}</p>
            </ActionDialog>
        </div>
    )
}

/* ------------------------------------------------------------------ list */

const DraftList = ({
    source,
    selected,
    tick,
    onSelect,
}: {
    source: DraftSource
    selected: string | undefined
    tick: number
    onSelect: (id: string) => void
}) => {
    const { t } = useTranslation()
    const [page, setPage] = useState(1)
    const q = useV2Query(
        (signal) =>
            draftsApi.list(
                source.attemptId ? { attempt: source.attemptId, page, page_size: LIST_SIZE } : { case: source.caseId, page, page_size: LIST_SIZE },
                signal
            ),
        [source.caseId, source.attemptId, page, tick]
    )
    const data = q.data
    return (
        <div data-testid="p6-draft-list" data-total={data?.total ?? ''}>
            {q.loading && !data ? (
                <SkeletonLines rows={4} />
            ) : q.error && !data ? (
                <ErrorState error={q.error} onRetry={q.reload} describe={describe} />
            ) : data && data.items.length > 0 ? (
                <ul className="flex flex-col divide-y divide-gray-200 dark:divide-night-border">
                    {data.items.map((d) => (
                        <li key={d.id}>
                            <button
                                type="button"
                                onClick={() => onSelect(d.id)}
                                data-testid="p6-draft-row"
                                data-draft={d.id}
                                data-status={d.status}
                                aria-current={selected === d.id ? 'true' : undefined}
                                className={classNames(
                                    'flex w-full flex-col items-start gap-1 px-2 py-2 text-start hover:bg-gray-50 dark:hover:bg-night-raised',
                                    selected === d.id && 'bg-azure-50 dark:bg-night-raised'
                                )}
                            >
                                <span className="flex w-full flex-wrap items-center justify-between gap-2">
                                    <span className="text-[13.5px] font-extrabold text-navy-600 dark:text-gray-100">{draftKindLabel(t, d.kind)}</span>
                                    <DraftStatusBadge status={d.status} size="row" />
                                </span>
                                <span className="text-[12px] text-gray-600">
                                    {d.requested_by?.name || '—'} · <RiyadhTime iso={d.created_at} /> · {d.language === 'ar' ? 'ع' : 'EN'}
                                </span>
                                {d.text && (
                                    <span className="line-clamp-2 text-[12.5px] text-gray-700 dark:text-night-body" dir="auto">
                                        {d.text}
                                    </span>
                                )}
                            </button>
                        </li>
                    ))}
                </ul>
            ) : (
                <EmptyState title={t('p6.drafts.emptyTitle')} hint={t('p6.drafts.emptyHint')} />
            )}
            {data && data.total > LIST_SIZE && <PagerFooter page={page} pageSize={LIST_SIZE} total={data.total} onPage={setPage} />}
        </div>
    )
}

/* ------------------------------------------------------------------ workspace */

const DraftWorkspace = ({
    source,
    kinds,
    contractId,
    caseLocked,
    caseArchived,
    selectedId,
    onSelect,
    onNoteSaved,
    onUseText,
    compact,
}: {
    source: DraftSource
    kinds: DraftKind[]
    contractId: string | null
    caseLocked?: boolean
    caseArchived?: boolean
    /** The open draft (the case page keeps it in the URL hash). */
    selectedId: string | undefined
    onSelect: (id: string | undefined) => void
    onNoteSaved?: (res: SavedNote) => void
    /** Wrap-up only: copy the reviewed text into the wrap-up note. */
    onUseText?: (text: string) => void
    compact?: boolean
}) => {
    const { t } = useTranslation()
    const myId = useAppSelector((s) => s.auth.user.id)
    const status = useV2Query((signal) => aiApi.status(signal), [])
    const [tick, setTick] = useState(0)
    const [busy, setBusy] = useState(false)
    const [genError, setGenError] = useState<unknown>(null)
    const draftQ = useV2Query(selectedId ? (signal) => draftsApi.get(selectedId, signal) : null, [selectedId, tick])
    const cap = status.data?.capabilities?.generate_structured
    const quota = status.data?.quota
    const notReady = !!status.data && (!cap?.ready || !!quota?.exceeded)
    const draft = selectedId && draftQ.data && draftQ.data.id === selectedId ? draftQ.data : null

    /* One Idempotency-Key per generation request: a retry of the same request (after a lost
     * answer) replays the stored draft instead of calling the provider again; a changed request
     * or a new generation after a success gets a new key. */
    const [gen, setGen] = useState<{ key: string; body: string }>({ key: newIdempotencyKey(), body: '' })
    const generate = async (kind: DraftKind, language: DraftLanguage, options: DraftOptions) => {
        setBusy(true)
        setGenError(null)
        const body = {
            kind,
            language,
            ...(source.attemptId && (kind === 'call_summary' || kind === 'coaching') ? { attempt_id: source.attemptId } : { case_id: source.caseId }),
            ...(Object.keys(options).length ? { options } : {}),
        }
        const text = JSON.stringify(body)
        const key = gen.body === text ? gen.key : newIdempotencyKey()
        setGen({ key, body: text })
        try {
            const d = await draftsApi.create(body, key)
            setGen({ key: newIdempotencyKey(), body: '' })
            onSelect(d.id)
            setTick((n) => n + 1)
        } catch (e) {
            setGenError(e)
            status.reload()
        } finally {
            setBusy(false)
        }
    }

    const leftCol = useMemo(
        () => (compact ? 'flex flex-col gap-4' : 'grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1fr)_360px]'),
        [compact]
    )

    return (
        <div className={leftCol} data-testid="p6-drafts" data-ready={status.data ? (notReady ? '0' : '1') : ''}>
            <div className="flex min-w-0 flex-col gap-4">
                <Card title={t('p6.drafts.newTitle')} testId="p6-draft-new">
                    <div className="mb-3 text-[13px] text-gray-700 dark:text-night-body">{t('p6.drafts.intro')}</div>
                    {status.error && !status.data ? (
                        <InlineError error={status.error} describe={describe} />
                    ) : (
                        <AiReadinessNotice capability={cap} quota={quota} className="mb-3" testId="p6-draft-not-ready" />
                    )}
                    <GenerateForm kinds={kinds} disabled={busy || notReady} busy={busy} onGenerate={generate} />
                    <AiErrorNotice error={genError} className="mt-3" testId="p6-draft-refusal" />
                    <div className="mt-3">
                        <QuotaLine quota={quota} />
                    </div>
                </Card>
                {selectedId && (
                    <Card
                        title={t('p6.drafts.openTitle')}
                        testId="p6-draft-card"
                        actions={
                            <Button size="xs" variant="plain" onClick={() => onSelect(undefined)} data-testid="p6-draft-close">
                                {t('p1.action.close')}
                            </Button>
                        }
                    >
                        {draftQ.loading && !draft ? (
                            <SkeletonLines rows={8} />
                        ) : draftQ.error && !draft ? (
                            <ErrorState error={draftQ.error} onRetry={draftQ.reload} describe={describe} />
                        ) : draft ? (
                            <DraftEditor
                                draft={draft}
                                mine={!!draft.requested_by && draft.requested_by.id === myId}
                                contractId={contractId}
                                caseLocked={caseLocked}
                                caseArchived={caseArchived}
                                onChanged={() => setTick((n) => n + 1)}
                                onSaved={(res) => onNoteSaved?.(res)}
                                onUseText={onUseText}
                            />
                        ) : null}
                    </Card>
                )}
            </div>
            <Card title={source.attemptId ? t('p6.drafts.callDrafts') : t('p6.drafts.caseDrafts')} testId="p6-draft-previous">
                <DraftList source={source} selected={selectedId} tick={tick} onSelect={(id) => onSelect(id)} />
            </Card>
        </div>
    )
}

export default DraftWorkspace
