-- Fixtures for the Phase 4 R-A report tests (tests/test_reports_v2_a.py): RPT-01 … RPT-05.
-- Loaded once per session after tests/fixtures/seed.sql and seed_reports.sql. Synthetic data only.
-- Re-runnable. Cases, notes, archive records and extra users are created per test.
--
--   ra.super    role "SuperAdmin"       unrestricted scope; the five report keys + users_details
--   ra.sup      role RaSupervisor       supervisor of "RA contract A" + supervised user ra.col2; the five keys
--   ra.col1     role RaCollector        collector; clints + report_collectors_currentUser_comments; contract A
--   ra.col2     role RaCollector        collector; contract B; supervised by ra.sup
--   ra.col3     role RaCollector        collector; contract B; NOT linked to ra.sup
--   ra.col4     role RaCollector        collector; contract A; inactive; no cases
--   ra.tgt      role RaTargets          users + users_edit_target (targets visible, identity not); own cases only
--   ra.idadmin  role RaIdentityAdmin    users + users_edit (identity administrator); own cases only
--   ra.none     role RaNone             no keys

INSERT INTO roles (id, name, permission) VALUES
  ('ea0ea0ea-0000-4000-8000-000000000201', 'SuperAdmin',
   '{"users": true, "clints": true, "clints_archive": true, "report_users_comments": true,
     "report_collectors_currentUser_comments": true, "users_details": true}'),
  ('ea0ea0ea-0000-4000-8000-000000000202', 'RaSupervisor',
   '{"users": true, "clints": true, "clints_archive": true, "report_users_comments": true,
     "report_collectors_currentUser_comments": true, "users_details": false, "users_edit": false}'),
  ('ea0ea0ea-0000-4000-8000-000000000203', 'RaCollector',
   '{"users": false, "clints": true, "clints_archive": false, "report_users_comments": false,
     "report_collectors_currentUser_comments": true}'),
  ('ea0ea0ea-0000-4000-8000-000000000204', 'RaTargets', '{"users": true, "users_edit_target": true}'),
  ('ea0ea0ea-0000-4000-8000-000000000205', 'RaNone', '{}'),
  ('ea0ea0ea-0000-4000-8000-000000000206', 'RaIdentityAdmin', '{"users": true, "users_edit": true}')
ON CONFLICT (id) DO NOTHING;

INSERT INTO branches (id, name) VALUES
  ('ea0ea0ea-0000-4000-8000-000000000111', 'فرع ر-أ الرياض'),
  ('ea0ea0ea-0000-4000-8000-000000000112', 'RA branch Jeddah')
ON CONFLICT (id) DO NOTHING;

INSERT INTO contracts (id, name) VALUES
  ('ea0ea0ea-0000-4000-8000-000000000301', 'RA contract A'),
  ('ea0ea0ea-0000-4000-8000-000000000302', 'RA contract B')
ON CONFLICT (id) DO NOTHING;

INSERT INTO users (id, email, frist_name, last_name, mobile, password, user_name, role_id, nationality_id,
                   branch_id, contract_id, "identityNumber", extension, is_active,
                   target_is_percentage, target_amount, target_percent) VALUES
  ('ea0ea0ea-0000-4000-8000-000000000501', 'ra.super@test.invalid', 'RA', 'Super', '0500006001',
   'not-a-real-hash', 'ra.super', 'ea0ea0ea-0000-4000-8000-000000000201', '00000000-0000-4000-8000-000000000101',
   'ea0ea0ea-0000-4000-8000-000000000111', NULL, '2000006001', '601', true, false, 0, 0),
  ('ea0ea0ea-0000-4000-8000-000000000502', 'ra.sup@test.invalid', 'RA', 'Supervisor', '0500006002',
   'not-a-real-hash', 'ra.sup', 'ea0ea0ea-0000-4000-8000-000000000202', '00000000-0000-4000-8000-000000000101',
   'ea0ea0ea-0000-4000-8000-000000000111', NULL, '2000006002', '602', true, false, 90000.25, 0),
  ('ea0ea0ea-0000-4000-8000-000000000503', 'ra.col1@test.invalid', 'محصل', 'ر-أ', '0500006003',
   'not-a-real-hash', 'ra.col1', 'ea0ea0ea-0000-4000-8000-000000000203', '00000000-0000-4000-8000-000000000101',
   'ea0ea0ea-0000-4000-8000-000000000111', 'ea0ea0ea-0000-4000-8000-000000000301', '2000006003', '603', true,
   false, 1500.5, 0),
  ('ea0ea0ea-0000-4000-8000-000000000504', 'ra.col2@test.invalid', 'RA', 'Collector2', '0500006004',
   'not-a-real-hash', 'ra.col2', 'ea0ea0ea-0000-4000-8000-000000000203', '00000000-0000-4000-8000-000000000101',
   'ea0ea0ea-0000-4000-8000-000000000112', 'ea0ea0ea-0000-4000-8000-000000000302', '2000006004', '604', true,
   true, 0, 12.5),
  ('ea0ea0ea-0000-4000-8000-000000000505', 'ra.col3@test.invalid', 'RA', 'Collector3', '0500006005',
   'not-a-real-hash', 'ra.col3', 'ea0ea0ea-0000-4000-8000-000000000203', '00000000-0000-4000-8000-000000000101',
   'ea0ea0ea-0000-4000-8000-000000000112', 'ea0ea0ea-0000-4000-8000-000000000302', '2000006005', '605', true,
   false, 700, 0),
  ('ea0ea0ea-0000-4000-8000-000000000506', 'ra.col4@test.invalid', 'RA', 'Collector4', '0500006006',
   'not-a-real-hash', 'ra.col4', 'ea0ea0ea-0000-4000-8000-000000000203', '00000000-0000-4000-8000-000000000101',
   'ea0ea0ea-0000-4000-8000-000000000111', 'ea0ea0ea-0000-4000-8000-000000000301', '2000006006', NULL, false,
   false, 0, 0),
  ('ea0ea0ea-0000-4000-8000-000000000507', 'ra.tgt@test.invalid', 'RA', 'Targets', '0500006007',
   'not-a-real-hash', 'ra.tgt', 'ea0ea0ea-0000-4000-8000-000000000204', '00000000-0000-4000-8000-000000000101',
   'ea0ea0ea-0000-4000-8000-000000000111', NULL, '2000006007', NULL, true, false, 0, 0),
  ('ea0ea0ea-0000-4000-8000-000000000508', 'ra.idadmin@test.invalid', 'RA', 'IdentityAdmin', '0500006008',
   'not-a-real-hash', 'ra.idadmin', 'ea0ea0ea-0000-4000-8000-000000000206', '00000000-0000-4000-8000-000000000101',
   'ea0ea0ea-0000-4000-8000-000000000112', NULL, '2000006008', NULL, true, false, 0, 0),
  ('ea0ea0ea-0000-4000-8000-000000000509', 'ra.none@test.invalid', 'RA', 'None', '0500006009',
   'not-a-real-hash', 'ra.none', 'ea0ea0ea-0000-4000-8000-000000000205', '00000000-0000-4000-8000-000000000101',
   'ea0ea0ea-0000-4000-8000-000000000111', NULL, '2000006009', NULL, true, false, 0, 0)
ON CONFLICT (id) DO NOTHING;

INSERT INTO supervisors (id, user_id) VALUES
  ('ea0ea0ea-0000-4000-8000-000000000601', 'ea0ea0ea-0000-4000-8000-000000000502')
ON CONFLICT (id) DO NOTHING;
INSERT INTO supervisor_contracts (id, supervisor_id, contract_id) VALUES
  ('ea0ea0ea-0000-4000-8000-000000000611', 'ea0ea0ea-0000-4000-8000-000000000601',
   'ea0ea0ea-0000-4000-8000-000000000301')
ON CONFLICT (id) DO NOTHING;
INSERT INTO supervisor_users (id, supervisor_id, user_id) VALUES
  ('ea0ea0ea-0000-4000-8000-000000000621', 'ea0ea0ea-0000-4000-8000-000000000601',
   'ea0ea0ea-0000-4000-8000-000000000504')
ON CONFLICT (id) DO NOTHING;

INSERT INTO comment_reachability (id, name) VALUES
  ('ea0ea0ea-0000-4000-8000-000000000711', 'RA تم التوصل'),
  ('ea0ea0ea-0000-4000-8000-000000000712', 'RA لم يتم التوصل')
ON CONFLICT (id) DO NOTHING;
INSERT INTO "CommentStatuses" (id, name, contract_id, "CommentReachability_id", is_active) VALUES
  ('ea0ea0ea-0000-4000-8000-000000000701', 'RA وعد بالسداد', 'ea0ea0ea-0000-4000-8000-000000000301',
   'ea0ea0ea-0000-4000-8000-000000000711', true),
  ('ea0ea0ea-0000-4000-8000-000000000702', 'RA لا يرد', 'ea0ea0ea-0000-4000-8000-000000000301',
   'ea0ea0ea-0000-4000-8000-000000000712', false)
ON CONFLICT (id) DO NOTHING;
INSERT INTO "ContactMethods" (id, name) VALUES
  ('ea0ea0ea-0000-4000-8000-000000000721', 'RA جوال')
ON CONFLICT (id) DO NOTHING;
INSERT INTO "archiveReason" (id, name) VALUES
  ('ea0ea0ea-0000-4000-8000-000000000731', 'RA منتهي'),
  ('ea0ea0ea-0000-4000-8000-000000000732', 'RA سداد كامل')
ON CONFLICT (id) DO NOTHING;
