-- 0073 grant the new permission key `calls_supervise` to supervisor roles (customer decision
-- 2026-10-17; core/permissions.py, services/calling/common.py).
--
-- `calls_supervise` lets a SUPERVISOR block a client from calls (do-not-call, «عدم الاتصال»),
-- lift a block and release a stuck call (ended without a wrap-up, e.g. its case was deleted),
-- WITHOUT calling administration (extensions, calling windows), which stays `calls_manage`.
--
-- Set to true in roles.permission for: the role named 'مشرف', every role that has a user with an
-- active `supervisors` row, and ADMIN / SuperAdmin. Additive and re-runnable: a role that already
-- has the key set to true is not touched; the stored JSON text keeps its key order (the key is
-- appended, or an existing `false` is switched). Every changed role gets an audit_events row.
-- The permission merge that `manage_db.py up` runs afterwards adds `calls_supervise: false` to
-- every other role and never changes these values.
--
-- Locks: row locks on the few `roles` rows changed. Instant.

WITH targets AS (
    SELECT r.id, r.permission::text AS perm
      FROM roles r
     WHERE (r.name IN ('مشرف', 'SuperAdmin', 'ADMIN')
            OR EXISTS (SELECT 1 FROM users u JOIN supervisors s ON s.user_id = u.id AND s.is_active
                        WHERE u.role_id = r.id))
       AND json_typeof(r.permission) = 'object'
       AND (r.permission::jsonb -> 'calls_supervise') IS DISTINCT FROM 'true'::jsonb
       FOR UPDATE OF r
), changed AS (
    UPDATE roles r
       SET permission = CAST(CASE
               WHEN t.perm ~ '"calls_supervise"\s*:'
                   THEN regexp_replace(t.perm, '"calls_supervise"\s*:\s*[^,}]*', '"calls_supervise": true')
               WHEN t.perm ~ '^\s*\{\s*\}\s*$'
                   THEN '{"calls_supervise": true}'
               ELSE regexp_replace(t.perm, '\}\s*$', ', "calls_supervise": true}')
           END AS json),
           updated_at = now()
      FROM targets t
     WHERE r.id = t.id
    RETURNING r.id, t.perm
)
INSERT INTO audit_events (action, entity_type, entity_id, before, after, reason, source)
SELECT 'role.permissions.grant', 'role', c.id::text,
       jsonb_build_object('calls_supervise', c.perm::jsonb -> 'calls_supervise'),
       '{"calls_supervise": true}'::jsonb,
       'migration 0073: supervisors block/lift clients and release stuck calls (calls_supervise)',
       'migration'
  FROM changed c;
