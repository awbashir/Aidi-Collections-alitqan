/**
 * Case detail → "AI drafts" tab (`ai_assist`): the case kinds (call script, case guidance,
 * narrative, follow-up message), the case's previous drafts, and "save as note" onto this case.
 * The open draft is kept in the URL hash (`#draft=<id>`), so a reload or a shared link reopens it.
 * Call summaries and coaching are drafted from a call (call detail, wrap-up); they are listed here
 * too because they belong to the case.
 */
import { useTranslation } from 'react-i18next'
import useHashState, { hashCodec } from '@/utils/hooks/useHashState'
import type { HashSchema } from '@/utils/hooks/useHashState'
import { CASE_DRAFT_KINDS } from '@/services/v2'
import type { CasePanelProps } from '@/views/phase1/caseTypes'
import DraftWorkspace from './DraftWorkspace'
import { DRAFT_HASH_KEY } from './helpers'

type HashState = { draft: string | number | undefined }
const schema: HashSchema<HashState> = {
    draft: { key: DRAFT_HASH_KEY, codec: hashCodec.id(), default: undefined },
}

const AiDraftsPanel = ({ caseData, onChanged }: CasePanelProps) => {
    const { t } = useTranslation()
    const { state, setState } = useHashState(schema)
    const selected = state.draft === undefined ? undefined : String(state.draft)
    return (
        <div data-testid="p6-case-drafts">
            <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                <h5 className="mb-0">{t('p6.drafts.title')}</h5>
            </div>
            <DraftWorkspace
                source={{ caseId: caseData.id }}
                kinds={CASE_DRAFT_KINDS}
                contractId={caseData.contract?.id || null}
                caseLocked={!!caseData.is_locked}
                caseArchived={!!caseData.is_archive}
                selectedId={selected}
                onSelect={(id) => setState({ draft: id })}
                onNoteSaved={() => onChanged?.()}
            />
        </div>
    )
}

export default AiDraftsPanel
