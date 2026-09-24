-- Fixtures for the Phase 3 stream C tests (targets, sessions, /v2/me, legacy admin gates),
-- loaded once per session by tests/test_admin_support.py after tests/fixtures/seed.sql.
-- Synthetic data only. Re-runnable (ON CONFLICT DO NOTHING). Passwords are set by the
-- support module (pbkdf2 of the test password); cases/payments are created per test.
--
--   c3.admin  role AdminAll       every key the admin screens use + users_sessions
--   c3.col1    role AdminCollector own dashboard (dashbord_user), contract A, own amount target
--   c3.col2    role AdminCollector contract A, no own target (contract target applies)
--   c3.col3    role AdminCollector contract B, own percent target
--   c3.none    role AdminNone      no keys

INSERT INTO roles (id, name, permission) VALUES
  ('cccccccc-0000-4000-8000-000000000201', 'AdminAll',
   '{"users": true, "users_add": true, "users_edit": true, "users_details": true,
     "users_edit_general": true, "users_edit_target": true, "users_delete": true,
     "users_sessions": true, "roles": true, "roles_add": true, "roles_edit": true,
     "contracts": true, "contracts_add": true, "contracts_edit": true,
     "branches": true, "branches_add": true, "branches_edit": true,
     "portfolios": true, "portfolios_edit": true, "portfolios_delete": true,
     "supervisors": true, "supervisors_add": true, "supervisors_edit": true,
     "dashbord": true, "dashbord_admin": true, "dashbord_user": true, "clints": true}'),
  ('cccccccc-0000-4000-8000-000000000202', 'AdminCollector',
   '{"dashbord": true, "dashbord_user": true, "clints": true, "clints_details": true}'),
  ('cccccccc-0000-4000-8000-000000000203', 'AdminNone', '{}'),
  ('cccccccc-0000-4000-8000-000000000204', 'AdminOtherRole', '{"clints": true, "dashbord_user": true}')
ON CONFLICT (id) DO NOTHING;

INSERT INTO contracts (id, name, monthly_target_amount, monthly_target_percent,
                       monthly_target_is_percentage) VALUES
  ('cccccccc-0000-4000-8000-000000000301', 'Admin contract A', 50000, 0, false),
  ('cccccccc-0000-4000-8000-000000000302', 'Admin contract B', 0, 0, false)
ON CONFLICT (id) DO NOTHING;

INSERT INTO portfolios (id, name, contract_id) VALUES
  ('cccccccc-0000-4000-8000-000000000401', 'Admin portfolio A', 'cccccccc-0000-4000-8000-000000000301'),
  ('cccccccc-0000-4000-8000-000000000402', 'Admin portfolio B', 'cccccccc-0000-4000-8000-000000000302')
ON CONFLICT (id) DO NOTHING;

INSERT INTO users (id, email, frist_name, last_name, mobile, password, user_name, role_id,
                   nationality_id, branch_id, contract_id, target_amount, target_percent,
                   target_is_percentage, "identityNumber") VALUES
  ('cccccccc-0000-4000-8000-000000000501', 'a.admin@test.invalid', 'Admin', 'All', '0500002001',
   'not-a-real-hash', 'c3.admin', 'cccccccc-0000-4000-8000-000000000201',
   '00000000-0000-4000-8000-000000000101', '00000000-0000-4000-8000-000000000102', NULL, 0, 0, false, NULL),
  ('cccccccc-0000-4000-8000-000000000502', 'a.col1@test.invalid', 'Admin', 'Collector1', '0500002002',
   'not-a-real-hash', 'c3.col1', 'cccccccc-0000-4000-8000-000000000202',
   '00000000-0000-4000-8000-000000000101', '00000000-0000-4000-8000-000000000102',
   'cccccccc-0000-4000-8000-000000000301', 20000, 0, false, '1999999991'),
  ('cccccccc-0000-4000-8000-000000000503', 'a.col2@test.invalid', 'Admin', 'Collector2', '0500002003',
   'not-a-real-hash', 'c3.col2', 'cccccccc-0000-4000-8000-000000000202',
   '00000000-0000-4000-8000-000000000101', '00000000-0000-4000-8000-000000000102',
   'cccccccc-0000-4000-8000-000000000301', 0, 0, false, NULL),
  ('cccccccc-0000-4000-8000-000000000504', 'a.col3@test.invalid', 'Admin', 'Collector3', '0500002004',
   'not-a-real-hash', 'c3.col3', 'cccccccc-0000-4000-8000-000000000202',
   '00000000-0000-4000-8000-000000000101', '00000000-0000-4000-8000-000000000102',
   'cccccccc-0000-4000-8000-000000000302', 0, 10, true, NULL),
  ('cccccccc-0000-4000-8000-000000000505', 'a.none@test.invalid', 'Admin', 'None', '0500002005',
   'not-a-real-hash', 'c3.none', 'cccccccc-0000-4000-8000-000000000203',
   '00000000-0000-4000-8000-000000000101', '00000000-0000-4000-8000-000000000102', NULL, 0, 0, false, NULL)
ON CONFLICT (id) DO NOTHING;
