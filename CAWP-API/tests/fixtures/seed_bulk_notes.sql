-- Fixtures for the Phase 2 stream C tests (bulk notes, follow-up close-out, legacy gates),
-- loaded once per session by tests/test_bulk_notes_support.py after seed.sql and
-- seed_bulk.sql. Synthetic data only. Re-runnable (ON CONFLICT DO NOTHING). Cases, notes and
-- follow-ups are created per test.
--
--   n.sup     NotesSupervisor   supervisor of contract NA and of user n.col1;
--                               clints_UpdateByExcel + follow_ups_manage + clints_sepcifyUser +
--                               bulk_jobs_all (sees and confirms n.col1's jobs: four eyes)
--   n.col1    NotesCollector    collector (own cases); clints_UpdateByExcel
--   n.col2    NotesCollector    collector, owns the contract NB cases
--   n.mgr     FollowUpManager   supervisor of contract NB; follow_ups_manage only
--   n.none    NotesNone         no keys
--   n.notify  NotifyOnly        clints_sepcifyUser only (legacy /send/notification)
--
-- Lookups: reachability R_ok / R_no / R_del (deleted); contact method M; statuses
-- S_A (contract NA, R_ok), S_A_no (contract NA, R_no), S_B (contract NB, R_ok).

INSERT INTO roles (id, name, permission) VALUES
  ('c0c0c0c0-0000-4000-8000-000000000201', 'NotesSupervisor',
   '{"clints": true, "clints_UpdateByExcel": true, "follow_ups": true, "follow_ups_manage": true, "clints_sepcifyUser": true,
     "bulk_jobs_all": true}'),
  ('c0c0c0c0-0000-4000-8000-000000000202', 'NotesCollector',
   '{"clints": true, "clints_UpdateByExcel": true, "follow_ups": true, "follow_ups_manage": false}'),
  ('c0c0c0c0-0000-4000-8000-000000000203', 'FollowUpManager', '{"follow_ups": true, "follow_ups_manage": true}'),
  ('c0c0c0c0-0000-4000-8000-000000000204', 'NotesNone', '{}'),
  ('c0c0c0c0-0000-4000-8000-000000000205', 'NotifyOnly', '{"clints_sepcifyUser": true}')
ON CONFLICT (id) DO NOTHING;

INSERT INTO contracts (id, name) VALUES
  ('c0c0c0c0-0000-4000-8000-000000000301', 'Notes contract NA'),
  ('c0c0c0c0-0000-4000-8000-000000000302', 'Notes contract NB')
ON CONFLICT (id) DO NOTHING;

INSERT INTO portfolios (id, name, contract_id) VALUES
  ('c0c0c0c0-0000-4000-8000-000000000401', 'Notes portfolio PA', 'c0c0c0c0-0000-4000-8000-000000000301'),
  ('c0c0c0c0-0000-4000-8000-000000000402', 'Notes portfolio PB', 'c0c0c0c0-0000-4000-8000-000000000302'),
  ('c0c0c0c0-0000-4000-8000-000000000403', 'Notes portfolio PA2 (filter test)', 'c0c0c0c0-0000-4000-8000-000000000301')
ON CONFLICT (id) DO NOTHING;

INSERT INTO users (id, email, frist_name, last_name, mobile, password, user_name, role_id,
                   nationality_id, branch_id, contract_id) VALUES
  ('c0c0c0c0-0000-4000-8000-000000000501', 'n.sup@test.invalid', 'Notes', 'Supervisor', '0500003001',
   'not-a-real-hash', 'n.sup', 'c0c0c0c0-0000-4000-8000-000000000201',
   '00000000-0000-4000-8000-000000000101', '00000000-0000-4000-8000-000000000102', NULL),
  ('c0c0c0c0-0000-4000-8000-000000000502', 'n.col1@test.invalid', 'Notes', 'Collector1', '0500003002',
   'not-a-real-hash', 'n.col1', 'c0c0c0c0-0000-4000-8000-000000000202',
   '00000000-0000-4000-8000-000000000101', '00000000-0000-4000-8000-000000000102',
   'c0c0c0c0-0000-4000-8000-000000000301'),
  ('c0c0c0c0-0000-4000-8000-000000000503', 'n.col2@test.invalid', 'Notes', 'Collector2', '0500003003',
   'not-a-real-hash', 'n.col2', 'c0c0c0c0-0000-4000-8000-000000000202',
   '00000000-0000-4000-8000-000000000101', '00000000-0000-4000-8000-000000000102',
   'c0c0c0c0-0000-4000-8000-000000000302'),
  ('c0c0c0c0-0000-4000-8000-000000000504', 'n.mgr@test.invalid', 'Notes', 'FollowUpManager', '0500003004',
   'not-a-real-hash', 'n.mgr', 'c0c0c0c0-0000-4000-8000-000000000203',
   '00000000-0000-4000-8000-000000000101', '00000000-0000-4000-8000-000000000102', NULL),
  ('c0c0c0c0-0000-4000-8000-000000000505', 'n.none@test.invalid', 'Notes', 'None', '0500003005',
   'not-a-real-hash', 'n.none', 'c0c0c0c0-0000-4000-8000-000000000204',
   '00000000-0000-4000-8000-000000000101', '00000000-0000-4000-8000-000000000102', NULL),
  ('c0c0c0c0-0000-4000-8000-000000000506', 'n.notify@test.invalid', 'Notes', 'Notify', '0500003006',
   'not-a-real-hash', 'n.notify', 'c0c0c0c0-0000-4000-8000-000000000205',
   '00000000-0000-4000-8000-000000000101', '00000000-0000-4000-8000-000000000102', NULL)
ON CONFLICT (id) DO NOTHING;

INSERT INTO supervisors (id, user_id) VALUES
  ('c0c0c0c0-0000-4000-8000-000000000601', 'c0c0c0c0-0000-4000-8000-000000000501'),
  ('c0c0c0c0-0000-4000-8000-000000000602', 'c0c0c0c0-0000-4000-8000-000000000504')
ON CONFLICT (id) DO NOTHING;
INSERT INTO supervisor_contracts (id, supervisor_id, contract_id) VALUES
  ('c0c0c0c0-0000-4000-8000-000000000611', 'c0c0c0c0-0000-4000-8000-000000000601',
   'c0c0c0c0-0000-4000-8000-000000000301'),
  ('c0c0c0c0-0000-4000-8000-000000000612', 'c0c0c0c0-0000-4000-8000-000000000602',
   'c0c0c0c0-0000-4000-8000-000000000302')
ON CONFLICT (id) DO NOTHING;
INSERT INTO supervisor_users (id, supervisor_id, user_id) VALUES
  ('c0c0c0c0-0000-4000-8000-000000000621', 'c0c0c0c0-0000-4000-8000-000000000601',
   'c0c0c0c0-0000-4000-8000-000000000502')
ON CONFLICT (id) DO NOTHING;

INSERT INTO comment_reachability (id, name, is_deleted) VALUES
  ('c0c0c0c0-0000-4000-8000-000000000701', 'Notes reached', false),
  ('c0c0c0c0-0000-4000-8000-000000000702', 'Notes not reached', false),
  ('c0c0c0c0-0000-4000-8000-000000000703', 'Notes retired reachability', true)
ON CONFLICT (id) DO NOTHING;

INSERT INTO "ContactMethods" (id, name) VALUES
  ('c0c0c0c0-0000-4000-8000-000000000711', 'Notes mobile')
ON CONFLICT (id) DO NOTHING;

INSERT INTO "CommentStatuses" (id, name, contract_id, "CommentReachability_id", is_active) VALUES
  ('c0c0c0c0-0000-4000-8000-000000000721', 'Notes promise (NA)', 'c0c0c0c0-0000-4000-8000-000000000301',
   'c0c0c0c0-0000-4000-8000-000000000701', false),
  ('c0c0c0c0-0000-4000-8000-000000000722', 'Notes no answer (NA)', 'c0c0c0c0-0000-4000-8000-000000000301',
   'c0c0c0c0-0000-4000-8000-000000000702', true),
  ('c0c0c0c0-0000-4000-8000-000000000723', 'Notes promise (NB)', 'c0c0c0c0-0000-4000-8000-000000000302',
   'c0c0c0c0-0000-4000-8000-000000000701', true)
ON CONFLICT (id) DO NOTHING;
