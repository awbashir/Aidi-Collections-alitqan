-- 0030 indexes for internal requests (Phase 3, stream A).
--
-- All on the new (empty) tables, so no CONCURRENTLY is needed and the file is transactional.
-- Nothing is added to legacy tables: scope is checked through clints' primary key and the
-- existing clints indexes (review/phase3-requests.md §6).
--
--   ix_requests_created        newest-first list walk (unrestricted scope, date filter)
--   ix_requests_status         status tab (open / in_review / resolved / cancelled)
--   ix_requests_department     department tab of a reviewer
--   ix_requests_requester      "my requests" (every caller with `requests`)
--   ix_requests_case           case detail Requests section, and the scope join
--   ix_requests_unresolved     attention reason open_request (open + in_review per department/case)
--   ix_request_events_request  the trail of one request, in order
--   ix_request_events_actor    "what did this user do" (audit/support)
--
-- Locks: new tables only. Instant.

CREATE INDEX IF NOT EXISTS ix_requests_created    ON requests (created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS ix_requests_status     ON requests (status, created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS ix_requests_department ON requests (department, status, created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS ix_requests_requester  ON requests (requested_by, created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS ix_requests_case       ON requests (case_id, created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS ix_requests_unresolved
    ON requests (department, case_id, created_at) WHERE status IN ('open', 'in_review');

CREATE INDEX IF NOT EXISTS ix_request_events_request ON request_events (request_id, id);
CREATE INDEX IF NOT EXISTS ix_request_events_actor   ON request_events (actor_user_id, occurred_at DESC);
