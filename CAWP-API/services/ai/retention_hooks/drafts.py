"""AI retention hook for Q1 drafts (table ai_drafts, migration 0061), category `drafts`
(the policy's draft_days). Registered on import by services.ai.retention.discover().

For drafts created before the cutoff:
* not saved (generated, edited, discarded): the row is deleted;
* saved as a note: the row stays (it links the note to its provenance: kind, requester, provider
  call, model, policy revision, hashes) but its content is cleared (`purged_at`). The note itself
  is case data and is not touched.

Idempotent: purged saved drafts (`purged_at` set) and deleted rows are never counted again. At
most `limit` drafts per batch, oldest first; no commit here (the retention task commits the batch
with its audit event). The DELETE runs under `SET LOCAL cawp.retention_purge = 'ai_drafts'`,
which the 0061 guard requires.
"""
from __future__ import annotations

from sqlalchemy import text

from services.ai.retention import register

HOOK = "q1.ai_drafts"


def purge_drafts(session, *, cutoff, limit) -> int:
    if not session.execute(text("SELECT to_regclass('public.ai_drafts') IS NOT NULL")).scalar():
        return 0                                   # migration 0061 not applied yet
    session.execute(text("SELECT set_config('cawp.retention_purge', 'ai_drafts', true)"))
    deleted = session.execute(text("""
        DELETE FROM ai_drafts WHERE id IN (
            SELECT id FROM ai_drafts
             WHERE created_at < :cutoff AND status <> 'saved_as_note'
             ORDER BY created_at, id LIMIT :n FOR UPDATE SKIP LOCKED)"""), {"cutoff": cutoff, "n": limit}).rowcount
    left = max(limit - deleted, 0)
    cleared = 0
    if left:
        cleared = session.execute(text("""
            UPDATE ai_drafts SET content = NULL, generated_text = NULL, text = NULL, purged_at = now()
             WHERE id IN (
                SELECT id FROM ai_drafts
                 WHERE created_at < :cutoff AND status = 'saved_as_note' AND purged_at IS NULL
                 ORDER BY created_at, id LIMIT :n FOR UPDATE SKIP LOCKED)"""),
            {"cutoff": cutoff, "n": left}).rowcount
    return int(deleted) + int(cleared)


register(HOOK, "drafts", purge_drafts, "AI drafts: unsaved deleted, saved ones' content cleared")
