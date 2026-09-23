-- Fixtures for the Phase 2 stream F (bulk framework) tests, loaded once per session by
-- tests/test_bulk_framework_support.py after tests/fixtures/seed.sql. Synthetic data only.
-- Re-runnable (ON CONFLICT DO NOTHING). Cases are created per test.
--
--   b.super   role "SuperAdmin"      unrestricted scope; clints_bulk_lock + bulk_jobs_all
--   b.sup     role BulkSupervisor    supervisor of contract A and of user b.col1; clints_lock + bulk_jobs_all
--   b.sup2    role BulkSupNoAll      supervisor of user b.col1 only; clints_lock, NOT bulk_jobs_all
--   b.col1    role BulkCollector     collector (own cases); clints_bulk_lock
--   b.col2    role BulkCollector     collector, owns the contract B cases
--   b.none    role BulkNone          no keys at all

INSERT INTO roles (id, name, permission) VALUES
  ('f0f0f0f0-0000-4000-8000-000000000201', 'SuperAdmin',
   '{"clints": true, "clints_bulk_lock": true, "bulk_jobs_all": true}'),
  ('f0f0f0f0-0000-4000-8000-000000000202', 'BulkSupervisor',
   '{"clints": true, "clints_lock": true, "clints_bulk_lock": false, "bulk_jobs_all": true}'),
  ('f0f0f0f0-0000-4000-8000-000000000203', 'BulkSupNoAll',
   '{"clints": true, "clints_lock": true, "bulk_jobs_all": false}'),
  ('f0f0f0f0-0000-4000-8000-000000000204', 'BulkCollector',
   '{"clints": true, "clints_bulk_lock": true, "clints_lock": false, "bulk_jobs_all": false}'),
  ('f0f0f0f0-0000-4000-8000-000000000205', 'BulkNone', '{}')
ON CONFLICT (id) DO NOTHING;

INSERT INTO contracts (id, name) VALUES
  ('f0f0f0f0-0000-4000-8000-000000000301', 'Bulk contract A'),
  ('f0f0f0f0-0000-4000-8000-000000000302', 'Bulk contract B')
ON CONFLICT (id) DO NOTHING;

INSERT INTO portfolios (id, name, contract_id) VALUES
  ('f0f0f0f0-0000-4000-8000-000000000401', 'Bulk portfolio A', 'f0f0f0f0-0000-4000-8000-000000000301'),
  ('f0f0f0f0-0000-4000-8000-000000000402', 'Bulk portfolio B', 'f0f0f0f0-0000-4000-8000-000000000302')
ON CONFLICT (id) DO NOTHING;

INSERT INTO users (id, email, frist_name, last_name, mobile, password, user_name, role_id,
                   nationality_id, branch_id, contract_id) VALUES
  ('f0f0f0f0-0000-4000-8000-000000000501', 'b.super@test.invalid', 'Bulk', 'Super', '0500002001',
   'not-a-real-hash', 'b.super', 'f0f0f0f0-0000-4000-8000-000000000201',
   '00000000-0000-4000-8000-000000000101', '00000000-0000-4000-8000-000000000102', NULL),
  ('f0f0f0f0-0000-4000-8000-000000000502', 'b.sup@test.invalid', 'Bulk', 'Supervisor', '0500002002',
   'not-a-real-hash', 'b.sup', 'f0f0f0f0-0000-4000-8000-000000000202',
   '00000000-0000-4000-8000-000000000101', '00000000-0000-4000-8000-000000000102', NULL),
  ('f0f0f0f0-0000-4000-8000-000000000503', 'b.sup2@test.invalid', 'Bulk', 'SupNoAll', '0500002003',
   'not-a-real-hash', 'b.sup2', 'f0f0f0f0-0000-4000-8000-000000000203',
   '00000000-0000-4000-8000-000000000101', '00000000-0000-4000-8000-000000000102', NULL),
  ('f0f0f0f0-0000-4000-8000-000000000504', 'b.col1@test.invalid', 'Bulk', 'Collector1', '0500002004',
   'not-a-real-hash', 'b.col1', 'f0f0f0f0-0000-4000-8000-000000000204',
   '00000000-0000-4000-8000-000000000101', '00000000-0000-4000-8000-000000000102',
   'f0f0f0f0-0000-4000-8000-000000000301'),
  ('f0f0f0f0-0000-4000-8000-000000000505', 'b.col2@test.invalid', 'Bulk', 'Collector2', '0500002005',
   'not-a-real-hash', 'b.col2', 'f0f0f0f0-0000-4000-8000-000000000204',
   '00000000-0000-4000-8000-000000000101', '00000000-0000-4000-8000-000000000102',
   'f0f0f0f0-0000-4000-8000-000000000302'),
  ('f0f0f0f0-0000-4000-8000-000000000506', 'b.none@test.invalid', 'Bulk', 'None', '0500002006',
   'not-a-real-hash', 'b.none', 'f0f0f0f0-0000-4000-8000-000000000205',
   '00000000-0000-4000-8000-000000000101', '00000000-0000-4000-8000-000000000102', NULL)
ON CONFLICT (id) DO NOTHING;

INSERT INTO supervisors (id, user_id) VALUES
  ('f0f0f0f0-0000-4000-8000-000000000601', 'f0f0f0f0-0000-4000-8000-000000000502'),
  ('f0f0f0f0-0000-4000-8000-000000000602', 'f0f0f0f0-0000-4000-8000-000000000503')
ON CONFLICT (id) DO NOTHING;
INSERT INTO supervisor_contracts (id, supervisor_id, contract_id) VALUES
  ('f0f0f0f0-0000-4000-8000-000000000611', 'f0f0f0f0-0000-4000-8000-000000000601',
   'f0f0f0f0-0000-4000-8000-000000000301')
ON CONFLICT (id) DO NOTHING;
INSERT INTO supervisor_users (id, supervisor_id, user_id) VALUES
  ('f0f0f0f0-0000-4000-8000-000000000621', 'f0f0f0f0-0000-4000-8000-000000000601',
   'f0f0f0f0-0000-4000-8000-000000000504'),
  ('f0f0f0f0-0000-4000-8000-000000000622', 'f0f0f0f0-0000-4000-8000-000000000602',
   'f0f0f0f0-0000-4000-8000-000000000504')
ON CONFLICT (id) DO NOTHING;
