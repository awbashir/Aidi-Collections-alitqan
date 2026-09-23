-- Fixtures for the Phase 2 stream D tests (spreadsheet inquiry, attribute corrections, legacy
-- gates of /clints/update/byexcel and /clints/attribute/byexcel). Loaded once per session by
-- tests/test_bulk_inquiry_support.py after tests/fixtures/seed.sql and seed_bulk.sql.
-- Synthetic data only. Re-runnable (ON CONFLICT DO NOTHING). Cases are created per test.
--
--   d.super  role "SuperAdmin"        unrestricted scope; every stream D key + bulk_jobs_all
--   d.sup    role DInqSupervisor      supervisor of contract DA and of user d.col1; inquiry + request + review
--                                    + bulk_jobs_all (sees and confirms d.col1's jobs)
--   d.col1   role DInqCollector       collector (own cases, contract DA); inquiry + request
--   d.col2   role DInqCollector       collector, owns the contract DB cases
--   d.rev    role DInqReviewer        supervisor of contract DA; case_corrections_review only
--   d.none   role DInqNone            no keys at all
--   d.xl     role DUpdateByExcel      clints_UpdateByExcel only (legacy update/attribute by Excel)

INSERT INTO roles (id, name, permission) VALUES
  ('d1d1d1d1-0000-4000-8000-000000000201', 'SuperAdmin',
   '{"clints": true, "client_query": true, "case_corrections_request": true, "case_corrections_review": true,
     "clints_UpdateByExcel": true, "bulk_jobs_all": true}'),
  ('d1d1d1d1-0000-4000-8000-000000000202', 'DInqSupervisor',
   '{"clints": true, "client_query": true, "case_corrections_request": true, "case_corrections_review": true,
     "clints_UpdateByExcel": false, "bulk_jobs_all": true}'),
  ('d1d1d1d1-0000-4000-8000-000000000203', 'DInqCollector',
   '{"clints": true, "client_query": true, "case_corrections_request": true, "case_corrections_review": false,
     "clints_UpdateByExcel": false}'),
  ('d1d1d1d1-0000-4000-8000-000000000204', 'DInqReviewer',
   '{"clints": true, "client_query": false, "case_corrections_request": false, "case_corrections_review": true}'),
  ('d1d1d1d1-0000-4000-8000-000000000205', 'DInqNone', '{}'),
  ('d1d1d1d1-0000-4000-8000-000000000206', 'DUpdateByExcel', '{"clints_UpdateByExcel": true}')
ON CONFLICT (id) DO NOTHING;

INSERT INTO contracts (id, name) VALUES
  ('d1d1d1d1-0000-4000-8000-000000000301', 'Inquiry contract DA'),
  ('d1d1d1d1-0000-4000-8000-000000000302', 'Inquiry contract DB')
ON CONFLICT (id) DO NOTHING;

INSERT INTO portfolios (id, name, contract_id) VALUES
  ('d1d1d1d1-0000-4000-8000-000000000401', 'Inquiry portfolio DA', 'd1d1d1d1-0000-4000-8000-000000000301'),
  ('d1d1d1d1-0000-4000-8000-000000000402', 'Inquiry portfolio DB', 'd1d1d1d1-0000-4000-8000-000000000302')
ON CONFLICT (id) DO NOTHING;

INSERT INTO users (id, email, frist_name, last_name, mobile, password, user_name, role_id,
                   nationality_id, branch_id, contract_id) VALUES
  ('d1d1d1d1-0000-4000-8000-000000000501', 'd.super@test.invalid', 'Inq', 'Super', '0500003001',
   'not-a-real-hash', 'd.super', 'd1d1d1d1-0000-4000-8000-000000000201',
   '00000000-0000-4000-8000-000000000101', '00000000-0000-4000-8000-000000000102', NULL),
  ('d1d1d1d1-0000-4000-8000-000000000502', 'd.sup@test.invalid', 'Inq', 'Supervisor', '0500003002',
   'not-a-real-hash', 'd.sup', 'd1d1d1d1-0000-4000-8000-000000000202',
   '00000000-0000-4000-8000-000000000101', '00000000-0000-4000-8000-000000000102', NULL),
  ('d1d1d1d1-0000-4000-8000-000000000503', 'd.col1@test.invalid', 'Inq', 'Collector1', '0500003003',
   'not-a-real-hash', 'd.col1', 'd1d1d1d1-0000-4000-8000-000000000203',
   '00000000-0000-4000-8000-000000000101', '00000000-0000-4000-8000-000000000102',
   'd1d1d1d1-0000-4000-8000-000000000301'),
  ('d1d1d1d1-0000-4000-8000-000000000504', 'd.col2@test.invalid', 'Inq', 'Collector2', '0500003004',
   'not-a-real-hash', 'd.col2', 'd1d1d1d1-0000-4000-8000-000000000203',
   '00000000-0000-4000-8000-000000000101', '00000000-0000-4000-8000-000000000102',
   'd1d1d1d1-0000-4000-8000-000000000302'),
  ('d1d1d1d1-0000-4000-8000-000000000505', 'd.rev@test.invalid', 'Inq', 'Reviewer', '0500003005',
   'not-a-real-hash', 'd.rev', 'd1d1d1d1-0000-4000-8000-000000000204',
   '00000000-0000-4000-8000-000000000101', '00000000-0000-4000-8000-000000000102', NULL),
  ('d1d1d1d1-0000-4000-8000-000000000506', 'd.none@test.invalid', 'Inq', 'None', '0500003006',
   'not-a-real-hash', 'd.none', 'd1d1d1d1-0000-4000-8000-000000000205',
   '00000000-0000-4000-8000-000000000101', '00000000-0000-4000-8000-000000000102', NULL),
  ('d1d1d1d1-0000-4000-8000-000000000507', 'd.xl@test.invalid', 'Inq', 'Excel', '0500003007',
   'not-a-real-hash', 'd.xl', 'd1d1d1d1-0000-4000-8000-000000000206',
   '00000000-0000-4000-8000-000000000101', '00000000-0000-4000-8000-000000000102', NULL)
ON CONFLICT (id) DO NOTHING;

INSERT INTO supervisors (id, user_id) VALUES
  ('d1d1d1d1-0000-4000-8000-000000000601', 'd1d1d1d1-0000-4000-8000-000000000502'),
  ('d1d1d1d1-0000-4000-8000-000000000602', 'd1d1d1d1-0000-4000-8000-000000000505')
ON CONFLICT (id) DO NOTHING;
INSERT INTO supervisor_contracts (id, supervisor_id, contract_id) VALUES
  ('d1d1d1d1-0000-4000-8000-000000000611', 'd1d1d1d1-0000-4000-8000-000000000601',
   'd1d1d1d1-0000-4000-8000-000000000301'),
  ('d1d1d1d1-0000-4000-8000-000000000612', 'd1d1d1d1-0000-4000-8000-000000000602',
   'd1d1d1d1-0000-4000-8000-000000000301')
ON CONFLICT (id) DO NOTHING;
INSERT INTO supervisor_users (id, supervisor_id, user_id) VALUES
  ('d1d1d1d1-0000-4000-8000-000000000621', 'd1d1d1d1-0000-4000-8000-000000000601',
   'd1d1d1d1-0000-4000-8000-000000000503')
ON CONFLICT (id) DO NOTHING;
