-- Fixtures for the portfolio upload tests (tests/test_portfolio_import.py), loaded once per
-- session after tests/fixtures/seed.sql. Synthetic data only. Re-runnable (ON CONFLICT DO NOTHING).
-- Cases are created per test.
--
--   pi.admin      role "SuperAdmin"   unrestricted scope; clints_add + every read key used here
--   pi.col        role PiCollector    collector (own cases only); clints_add, clints_details
--   pi.viewer     role PiViewer       clints + clints_details, NOT clints_add
--   Pi.Collector.A  role PiCollector  an active collector (username matched case-insensitively)
--   pi.off        role PiCollector    an INACTIVE user

INSERT INTO roles (id, name, permission) VALUES
  ('c7c7c7c7-0000-4000-8000-000000000201', 'SuperAdmin',
   '{"clints": true, "clints_add": true, "clints_details": true, "clints_Payments": true,
     "clints_PayemntDates": true, "promises": true, "follow_ups": true}'),
  ('c7c7c7c7-0000-4000-8000-000000000202', 'PiCollector',
   '{"clints": true, "clints_add": true, "clints_details": true, "collections_currentUser": true}'),
  ('c7c7c7c7-0000-4000-8000-000000000203', 'PiViewer',
   '{"clints": true, "clints_details": true}')
ON CONFLICT (id) DO NOTHING;

INSERT INTO contracts (id, name, is_active) VALUES
  ('c7c7c7c7-0000-4000-8000-000000000301', 'PI contract', true),
  ('c7c7c7c7-0000-4000-8000-000000000302', 'PI inactive contract', false)
ON CONFLICT (id) DO NOTHING;

INSERT INTO portfolios (id, name, contract_id) VALUES
  ('c7c7c7c7-0000-4000-8000-000000000401', 'PI old portfolio', 'c7c7c7c7-0000-4000-8000-000000000301')
ON CONFLICT (id) DO NOTHING;

INSERT INTO users (id, email, frist_name, last_name, mobile, password, user_name, role_id,
                   nationality_id, branch_id, contract_id, is_active) VALUES
  ('c7c7c7c7-0000-4000-8000-000000000501', 'pi.admin@test.invalid', 'PI', 'Admin', '0500007001',
   'not-a-real-hash', 'pi.admin', 'c7c7c7c7-0000-4000-8000-000000000201',
   '00000000-0000-4000-8000-000000000101', '00000000-0000-4000-8000-000000000102', NULL, true),
  ('c7c7c7c7-0000-4000-8000-000000000502', 'pi.col@test.invalid', 'PI', 'Col', '0500007002',
   'not-a-real-hash', 'pi.col', 'c7c7c7c7-0000-4000-8000-000000000202',
   '00000000-0000-4000-8000-000000000101', '00000000-0000-4000-8000-000000000102',
   'c7c7c7c7-0000-4000-8000-000000000301', true),
  ('c7c7c7c7-0000-4000-8000-000000000503', 'pi.viewer@test.invalid', 'PI', 'Viewer', '0500007003',
   'not-a-real-hash', 'pi.viewer', 'c7c7c7c7-0000-4000-8000-000000000203',
   '00000000-0000-4000-8000-000000000101', '00000000-0000-4000-8000-000000000102', NULL, true),
  ('c7c7c7c7-0000-4000-8000-000000000504', 'pi.a@test.invalid', 'Collector', 'A', '0500007004',
   'not-a-real-hash', 'Pi.Collector.A', 'c7c7c7c7-0000-4000-8000-000000000202',
   '00000000-0000-4000-8000-000000000101', '00000000-0000-4000-8000-000000000102',
   'c7c7c7c7-0000-4000-8000-000000000301', true),
  ('c7c7c7c7-0000-4000-8000-000000000505', 'pi.off@test.invalid', 'Collector', 'Off', '0500007005',
   'not-a-real-hash', 'pi.off', 'c7c7c7c7-0000-4000-8000-000000000202',
   '00000000-0000-4000-8000-000000000101', '00000000-0000-4000-8000-000000000102',
   'c7c7c7c7-0000-4000-8000-000000000301', false)
ON CONFLICT (id) DO NOTHING;

INSERT INTO "ClintContactTypes" (id, name) VALUES
  ('c7c7c7c7-0000-4000-8000-000000000706', 'email')
ON CONFLICT (id) DO NOTHING;

INSERT INTO "archiveReason" (id, name) VALUES
  ('c7c7c7c7-0000-4000-8000-000000000801', 'PI closed by client')
ON CONFLICT (id) DO NOTHING;
