-- Fixtures for the Phase 4 R0 report tests (tests/test_reports_v2_*.py), loaded once per
-- session after tests/fixtures/seed.sql. Synthetic data only. Re-runnable.
-- Cases, payments, contracts and portfolios are created per test (helpers in
-- tests/test_reports_v2_support.py) so assertions never depend on other tests' rows.
--
--   r.super  role "SuperAdmin"     unrestricted scope; the three collection keys + users_details
--   r.sup    role RepSupervisor    supervisor of "Reports contract A"; the three collection keys
--   r.col1   role RepCollector     collector (own cases); collections_currentUser only; branch R1
--   r.col2   role RepCollector     collector; branch R2
--   r.acct   role RepAccounting    accounts_payments only (RPT-08), supervisor of contract A
--   r.none   role RepNone          no keys

INSERT INTO roles (id, name, permission) VALUES
  ('e5e5e5e5-0000-4000-8000-000000000201', 'SuperAdmin',
   '{"collections_users": true, "collections_currentUser": true, "accounts_payments": true, "users_details": true}'),
  ('e5e5e5e5-0000-4000-8000-000000000202', 'RepSupervisor',
   '{"collections_users": true, "collections_currentUser": true, "accounts_payments": true}'),
  ('e5e5e5e5-0000-4000-8000-000000000203', 'RepCollector',
   '{"collections_users": false, "collections_currentUser": true, "accounts_payments": false}'),
  ('e5e5e5e5-0000-4000-8000-000000000204', 'RepAccounting', '{"accounts_payments": true}'),
  ('e5e5e5e5-0000-4000-8000-000000000205', 'RepNone', '{}')
ON CONFLICT (id) DO NOTHING;

INSERT INTO branches (id, name) VALUES
  ('e5e5e5e5-0000-4000-8000-000000000111', 'فرع التقارير الرياض'),
  ('e5e5e5e5-0000-4000-8000-000000000112', 'Reports branch Jeddah')
ON CONFLICT (id) DO NOTHING;

INSERT INTO contracts (id, name) VALUES
  ('e5e5e5e5-0000-4000-8000-000000000301', 'Reports contract A'),
  ('e5e5e5e5-0000-4000-8000-000000000302', 'Reports contract B')
ON CONFLICT (id) DO NOTHING;

INSERT INTO users (id, email, frist_name, last_name, mobile, password, user_name, role_id,
                   nationality_id, branch_id, contract_id, "identityNumber") VALUES
  ('e5e5e5e5-0000-4000-8000-000000000501', 'r.super@test.invalid', 'Report', 'Super', '0500005001',
   'not-a-real-hash', 'r.super', 'e5e5e5e5-0000-4000-8000-000000000201',
   '00000000-0000-4000-8000-000000000101', 'e5e5e5e5-0000-4000-8000-000000000111', NULL, '2000005001'),
  ('e5e5e5e5-0000-4000-8000-000000000502', 'r.sup@test.invalid', 'Report', 'Supervisor', '0500005002',
   'not-a-real-hash', 'r.sup', 'e5e5e5e5-0000-4000-8000-000000000202',
   '00000000-0000-4000-8000-000000000101', 'e5e5e5e5-0000-4000-8000-000000000111', NULL, '2000005002'),
  ('e5e5e5e5-0000-4000-8000-000000000503', 'r.col1@test.invalid', 'محصل', 'التقارير', '0500005003',
   'not-a-real-hash', 'r.col1', 'e5e5e5e5-0000-4000-8000-000000000203',
   '00000000-0000-4000-8000-000000000101', 'e5e5e5e5-0000-4000-8000-000000000111',
   'e5e5e5e5-0000-4000-8000-000000000301', '2000005003'),
  ('e5e5e5e5-0000-4000-8000-000000000504', 'r.col2@test.invalid', 'Report', 'Collector2', '0500005004',
   'not-a-real-hash', 'r.col2', 'e5e5e5e5-0000-4000-8000-000000000203',
   '00000000-0000-4000-8000-000000000101', 'e5e5e5e5-0000-4000-8000-000000000112',
   'e5e5e5e5-0000-4000-8000-000000000302', '2000005004'),
  ('e5e5e5e5-0000-4000-8000-000000000505', 'r.acct@test.invalid', 'Report', 'Accounting', '0500005005',
   'not-a-real-hash', 'r.acct', 'e5e5e5e5-0000-4000-8000-000000000204',
   '00000000-0000-4000-8000-000000000101', 'e5e5e5e5-0000-4000-8000-000000000112', NULL, '2000005005'),
  ('e5e5e5e5-0000-4000-8000-000000000506', 'r.none@test.invalid', 'Report', 'None', '0500005006',
   'not-a-real-hash', 'r.none', 'e5e5e5e5-0000-4000-8000-000000000205',
   '00000000-0000-4000-8000-000000000101', 'e5e5e5e5-0000-4000-8000-000000000111', NULL, '2000005006')
ON CONFLICT (id) DO NOTHING;

INSERT INTO supervisors (id, user_id) VALUES
  ('e5e5e5e5-0000-4000-8000-000000000601', 'e5e5e5e5-0000-4000-8000-000000000502'),
  ('e5e5e5e5-0000-4000-8000-000000000602', 'e5e5e5e5-0000-4000-8000-000000000505')
ON CONFLICT (id) DO NOTHING;
INSERT INTO supervisor_contracts (id, supervisor_id, contract_id) VALUES
  ('e5e5e5e5-0000-4000-8000-000000000611', 'e5e5e5e5-0000-4000-8000-000000000601',
   'e5e5e5e5-0000-4000-8000-000000000301'),
  ('e5e5e5e5-0000-4000-8000-000000000612', 'e5e5e5e5-0000-4000-8000-000000000602',
   'e5e5e5e5-0000-4000-8000-000000000301')
ON CONFLICT (id) DO NOTHING;
