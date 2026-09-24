-- Fixtures for the Phase 4 stream R-B report tests (tests/test_reports_v2_b*.py), loaded once
-- per session after tests/fixtures/seed.sql and seed_reports.sql. Synthetic data only.
-- Re-runnable. Cases, promises, payments, notes and portfolios are created per test.
--
--   rb.super role "SuperAdmin"      unrestricted scope; every R-B key + reports_performance
--   rb.sup   role RBSupervisor      supervisor of "RB contract A" and of user rb.col2; every R-B key
--   rb.col1  role RBCollector       collector (own cases); my promises, my callbacks, RPT-11
--   rb.col2  role RBCollector       collector, supervised by rb.sup; branch RB2
--   rb.pf    role RBPortfolios      only the portfolio management key `portfolios` (RPT-12 any-of)
--   rb.none  role RBNone            no keys

INSERT INTO roles (id, name, permission) VALUES
  ('e6e6e6e6-0000-4000-8000-000000000201', 'SuperAdmin',
   '{"report_users_paymentDates": true, "report_collectors_currentUser_paymentDates": true, "reports_performance": true,
     "report_portfolios": true, "report_clintes_active": true, "report_clintes_inactive": true,
     "report_quality_neglect": true, "report_collectors_currentUser_nextCalls": true, "users_details": true}'),
  ('e6e6e6e6-0000-4000-8000-000000000202', 'RBSupervisor',
   '{"report_users_paymentDates": true, "report_collectors_currentUser_paymentDates": true, "reports_performance": true,
     "report_portfolios": true, "report_clintes_active": true, "report_clintes_inactive": true,
     "report_quality_neglect": true, "report_collectors_currentUser_nextCalls": true}'),
  ('e6e6e6e6-0000-4000-8000-000000000203', 'RBCollector',
   '{"report_users_paymentDates": false, "report_collectors_currentUser_paymentDates": true, "reports_performance": true,
     "report_portfolios": false, "report_clintes_active": false, "report_clintes_inactive": false,
     "report_quality_neglect": false, "report_collectors_currentUser_nextCalls": true}'),
  ('e6e6e6e6-0000-4000-8000-000000000204', 'RBPortfolios', '{"portfolios": true}'),
  ('e6e6e6e6-0000-4000-8000-000000000205', 'RBNone', '{}')
ON CONFLICT (id) DO NOTHING;

INSERT INTO branches (id, name) VALUES
  ('e6e6e6e6-0000-4000-8000-000000000111', 'فرع التقارير ب'),
  ('e6e6e6e6-0000-4000-8000-000000000112', 'RB branch 2')
ON CONFLICT (id) DO NOTHING;

INSERT INTO contracts (id, name) VALUES
  ('e6e6e6e6-0000-4000-8000-000000000301', 'RB contract A'),
  ('e6e6e6e6-0000-4000-8000-000000000302', 'RB contract B')
ON CONFLICT (id) DO NOTHING;

INSERT INTO users (id, email, frist_name, last_name, mobile, password, user_name, role_id,
                   nationality_id, branch_id, contract_id, "identityNumber") VALUES
  ('e6e6e6e6-0000-4000-8000-000000000501', 'rb.super@test.invalid', 'RB', 'Super', '0500006001',
   'not-a-real-hash', 'rb.super', 'e6e6e6e6-0000-4000-8000-000000000201',
   '00000000-0000-4000-8000-000000000101', 'e6e6e6e6-0000-4000-8000-000000000111', NULL, '2000006001'),
  ('e6e6e6e6-0000-4000-8000-000000000502', 'rb.sup@test.invalid', 'RB', 'Supervisor', '0500006002',
   'not-a-real-hash', 'rb.sup', 'e6e6e6e6-0000-4000-8000-000000000202',
   '00000000-0000-4000-8000-000000000101', 'e6e6e6e6-0000-4000-8000-000000000111', NULL, '2000006002'),
  ('e6e6e6e6-0000-4000-8000-000000000503', 'rb.col1@test.invalid', 'محصل', 'المواعيد', '0500006003',
   'not-a-real-hash', 'rb.col1', 'e6e6e6e6-0000-4000-8000-000000000203',
   '00000000-0000-4000-8000-000000000101', 'e6e6e6e6-0000-4000-8000-000000000111',
   'e6e6e6e6-0000-4000-8000-000000000301', '2000006003'),
  ('e6e6e6e6-0000-4000-8000-000000000504', 'rb.col2@test.invalid', 'RB', 'Collector2', '0500006004',
   'not-a-real-hash', 'rb.col2', 'e6e6e6e6-0000-4000-8000-000000000203',
   '00000000-0000-4000-8000-000000000101', 'e6e6e6e6-0000-4000-8000-000000000112',
   'e6e6e6e6-0000-4000-8000-000000000302', '2000006004'),
  ('e6e6e6e6-0000-4000-8000-000000000505', 'rb.pf@test.invalid', 'RB', 'Portfolios', '0500006005',
   'not-a-real-hash', 'rb.pf', 'e6e6e6e6-0000-4000-8000-000000000204',
   '00000000-0000-4000-8000-000000000101', 'e6e6e6e6-0000-4000-8000-000000000112', NULL, '2000006005'),
  ('e6e6e6e6-0000-4000-8000-000000000506', 'rb.none@test.invalid', 'RB', 'None', '0500006006',
   'not-a-real-hash', 'rb.none', 'e6e6e6e6-0000-4000-8000-000000000205',
   '00000000-0000-4000-8000-000000000101', 'e6e6e6e6-0000-4000-8000-000000000111', NULL, '2000006006')
ON CONFLICT (id) DO NOTHING;

INSERT INTO supervisors (id, user_id) VALUES
  ('e6e6e6e6-0000-4000-8000-000000000601', 'e6e6e6e6-0000-4000-8000-000000000502')
ON CONFLICT (id) DO NOTHING;
INSERT INTO supervisor_contracts (id, supervisor_id, contract_id) VALUES
  ('e6e6e6e6-0000-4000-8000-000000000611', 'e6e6e6e6-0000-4000-8000-000000000601',
   'e6e6e6e6-0000-4000-8000-000000000301')
ON CONFLICT (id) DO NOTHING;
INSERT INTO supervisor_users (id, supervisor_id, user_id) VALUES
  ('e6e6e6e6-0000-4000-8000-000000000621', 'e6e6e6e6-0000-4000-8000-000000000601',
   'e6e6e6e6-0000-4000-8000-000000000504')
ON CONFLICT (id) DO NOTHING;
