-- migrate:no-transaction
-- 0012 indexes for the attention queue's "unscheduled" tier (Phase 1, stream B).
--
-- `unscheduled` is most of the live book (about 165k of 171k live cases on the full local
-- copy), so /v2/attention pages it instead of returning it, in neglect order: last contact
-- oldest first, never-contacted cases first (clints.last_comment_at ASC NULLS FIRST, id).
-- The existing ix_clints_last_comment_live* indexes have that order only up to ties, and
-- 97k live cases tie on NULL, so every page had to sort ~97k rows first. With `id` in the
-- key a page is an ordered index walk that stops after pageSize rows (0.5 ms instead of
-- 78 s / 0.2 s, see review/phase1-worklist.md):
--
--   ix_clints_worklist_neglect       unrestricted and supervisor scopes; INCLUDE (user_id,
--                                    portfolios_id) so the scope filter is checked on the index
--   ix_clints_worklist_user_neglect  collector scope and the owner filter (user_id = ...)
--
-- Both are partial on live, active cases (the only ones that can be unscheduled), about
-- 171k entries each. Write cost: clints rows already get non-HOT updates on every note
-- (last_comment_at is in four existing indexes), so this adds two index insertions per
-- note and per live-case insert.
--
-- CREATE INDEX CONCURRENTLY: no lock that blocks reads or writes; it must run outside a
-- transaction (hence the header) and is idempotent (IF NOT EXISTS). If a build is
-- interrupted it leaves an INVALID index: drop it (DROP INDEX CONCURRENTLY) and re-run.

CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_clints_worklist_neglect
    ON clints (last_comment_at ASC NULLS FIRST, id) INCLUDE (user_id, portfolios_id)
    WHERE is_archive = false AND is_active = true;

CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_clints_worklist_user_neglect
    ON clints (user_id, last_comment_at ASC NULLS FIRST, id)
    WHERE is_archive = false AND is_active = true;
