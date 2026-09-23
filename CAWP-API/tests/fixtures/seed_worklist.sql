-- Stream B (follow-ups, attention queue) fixtures, loaded once by tests/test_worklist_helpers.py
-- after the shared seed.sql. Synthetic data only. Fixed UUIDs in the ...b... range.
-- Per-test cases, contracts and portfolios are created by the helpers, so tests do not
-- depend on each other's rows (counts are asserted with a per-test contract filter).

INSERT INTO roles (id, name, permission) VALUES
  ('00000000-0000-4000-8000-00000000b201', 'SuperAdmin',
   '{"clints": true, "follow_ups": true, "follow_ups_manage": true, "attention_queue": true}'),
  ('00000000-0000-4000-8000-00000000b202', 'wl supervisor',
   '{"clints": true, "follow_ups": true, "follow_ups_manage": true, "attention_queue": true}'),
  ('00000000-0000-4000-8000-00000000b203', 'wl collector',
   '{"clints": true, "follow_ups": true, "follow_ups_manage": false, "attention_queue": true}'),
  ('00000000-0000-4000-8000-00000000b204', 'wl no access',
   '{"clints": true, "follow_ups": false, "follow_ups_manage": false, "attention_queue": false}'),
  ('00000000-0000-4000-8000-00000000b205', 'wl team lead (no manage)',
   '{"clints": true, "follow_ups": true, "follow_ups_manage": false, "attention_queue": true}');

INSERT INTO users (id, email, frist_name, last_name, mobile, password, user_name, role_id,
                   nationality_id, branch_id) VALUES
  ('00000000-0000-4000-8000-00000000b501', 'wl.super@test.invalid', 'WL', 'Super', '0500000101',
   'not-a-real-hash', 'wl.super', '00000000-0000-4000-8000-00000000b201',
   '00000000-0000-4000-8000-000000000101', '00000000-0000-4000-8000-000000000102'),
  ('00000000-0000-4000-8000-00000000b502', 'wl.supervisor@test.invalid', 'WL', 'Supervisor', '0500000102',
   'not-a-real-hash', 'wl.supervisor', '00000000-0000-4000-8000-00000000b202',
   '00000000-0000-4000-8000-000000000101', '00000000-0000-4000-8000-000000000102'),
  ('00000000-0000-4000-8000-00000000b503', 'wl.none@test.invalid', 'WL', 'NoAccess', '0500000103',
   'not-a-real-hash', 'wl.none', '00000000-0000-4000-8000-00000000b204',
   '00000000-0000-4000-8000-000000000101', '00000000-0000-4000-8000-000000000102'),
  ('00000000-0000-4000-8000-00000000b504', 'wl.lead@test.invalid', 'WL', 'Lead', '0500000104',
   'not-a-real-hash', 'wl.lead', '00000000-0000-4000-8000-00000000b205',
   '00000000-0000-4000-8000-000000000101', '00000000-0000-4000-8000-000000000102');

-- wl.supervisor and wl.lead are supervisors (their supervised users / contracts are added per test)
INSERT INTO supervisors (id, user_id) VALUES
  ('00000000-0000-4000-8000-00000000b601', '00000000-0000-4000-8000-00000000b502'),
  ('00000000-0000-4000-8000-00000000b602', '00000000-0000-4000-8000-00000000b504');
