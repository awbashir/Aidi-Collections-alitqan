-- Phase 5 P4 (scoped CDR/recordings, attempt <-> CDR link, PBX CDR import) fixtures, loaded
-- once per session by tests/test_calling_cdr_support.py after tests/fixtures/seed.sql.
-- Synthetic data only. Ids c4c4c4c4-0000-4000-8000-...; logins c4.*; accounts C4ACC-*.
-- Re-runnable (ON CONFLICT DO NOTHING).
--
--   c4.super  role "SuperAdmin"   unrestricted; call records + both recording keys + calls_manage
--   c4.sup    role C4Supervisor   supervisor of contract P4-A; call records, play, calls_manage
--   c4.cola   role C4Collector    contract P4-A, extension 7301; call records + play, calls
--   c4.colb   role C4Collector    contract P4-B, extension 7302
--   c4.meta   role C4Metadata     call records only (no recording key), no cases
--   c4.none   role C4None         no keys
--
-- Cases (contacts match db/cdr-test/init/03_calling_attempts.sql, day 2026-09-22):
--   K1 cola 0561240001 | K2 cola 0561240002 | K3 colb 0561240003
--   K4 cola + K5 colb: one debtor, 0561240004 (in scope for one side only, ambiguous for SuperAdmin)
--   K6 colb 0561240006 | K7 cola 0561240007 (import only)
--   K8 + K9 cola: one debtor, 0561240008 (ambiguous inside cola's own scope; import only)
-- Attempts (ended, unknown duration): a1c4a001 K1 by cola (legs: agent + customer),
--   a2c4a002 K3 by colb, a3c4a003 K2 by cola, a4c4a004 K6 (colb's case) made by cola,
--   a5c4a005 K7 by cola to +966561240007 on 2026-09-23 10:00 Riyadh (import phone+time),
--   a6c4a006 K8 by cola to +966561240008 on 2026-09-23 11:00 Riyadh (decides K8 vs K9).

INSERT INTO roles (id, name, permission) VALUES
  ('c4c4c4c4-0000-4000-8000-000000000201', 'SuperAdmin',
   '{"clints": true, "clints_callsRecords": true, "clints_callsRecords_download": true, "clints_callsRecords_play": true,
     "calls": true, "calls_make": true, "calls_manage": true, "clints_details": true, "reports_calls": true,
     "bulk_jobs_all": true}'),
  ('c4c4c4c4-0000-4000-8000-000000000202', 'C4Supervisor',
   '{"clints": true, "clints_callsRecords": true, "clints_callsRecords_download": false, "clints_callsRecords_play": true,
     "calls": true, "calls_make": false, "calls_manage": true, "clints_details": true, "reports_calls": true}'),
  ('c4c4c4c4-0000-4000-8000-000000000203', 'C4Collector',
   '{"clints": true, "clints_callsRecords": true, "clints_callsRecords_download": false, "clints_callsRecords_play": true,
     "calls": true, "calls_make": true, "calls_manage": false, "clints_details": true, "reports_calls": false}'),
  ('c4c4c4c4-0000-4000-8000-000000000204', 'C4Metadata',
   '{"clints": true, "clints_callsRecords": true, "calls": true, "clints_details": false}'),
  ('c4c4c4c4-0000-4000-8000-000000000205', 'C4None', '{}')
ON CONFLICT (id) DO NOTHING;

INSERT INTO contracts (id, name) VALUES
  ('c4c4c4c4-0000-4000-8000-000000000301', 'P4 contract A'),
  ('c4c4c4c4-0000-4000-8000-000000000302', 'P4 contract B')
ON CONFLICT (id) DO NOTHING;
INSERT INTO portfolios (id, name, contract_id) VALUES
  ('c4c4c4c4-0000-4000-8000-000000000401', 'P4 portfolio A', 'c4c4c4c4-0000-4000-8000-000000000301'),
  ('c4c4c4c4-0000-4000-8000-000000000402', 'P4 portfolio B', 'c4c4c4c4-0000-4000-8000-000000000302')
ON CONFLICT (id) DO NOTHING;

INSERT INTO users (id, email, frist_name, last_name, mobile, password, user_name, role_id, nationality_id,
                   branch_id, contract_id, extension) VALUES
  ('c4c4c4c4-0000-4000-8000-000000000501', 'c4.super@test.invalid', 'C4', 'Super', '0500008001', 'not-a-real-hash',
   'c4.super', 'c4c4c4c4-0000-4000-8000-000000000201', '00000000-0000-4000-8000-000000000101',
   '00000000-0000-4000-8000-000000000102', NULL, NULL),
  ('c4c4c4c4-0000-4000-8000-000000000502', 'c4.sup@test.invalid', 'C4', 'Supervisor', '0500008002', 'not-a-real-hash',
   'c4.sup', 'c4c4c4c4-0000-4000-8000-000000000202', '00000000-0000-4000-8000-000000000101',
   '00000000-0000-4000-8000-000000000102', NULL, NULL),
  ('c4c4c4c4-0000-4000-8000-000000000503', 'c4.cola@test.invalid', 'C4', 'Collector A', '0500008003', 'not-a-real-hash',
   'c4.cola', 'c4c4c4c4-0000-4000-8000-000000000203', '00000000-0000-4000-8000-000000000101',
   '00000000-0000-4000-8000-000000000102', 'c4c4c4c4-0000-4000-8000-000000000301', '7301'),
  ('c4c4c4c4-0000-4000-8000-000000000504', 'c4.colb@test.invalid', 'C4', 'Collector B', '0500008004', 'not-a-real-hash',
   'c4.colb', 'c4c4c4c4-0000-4000-8000-000000000203', '00000000-0000-4000-8000-000000000101',
   '00000000-0000-4000-8000-000000000102', 'c4c4c4c4-0000-4000-8000-000000000302', '7302'),
  ('c4c4c4c4-0000-4000-8000-000000000505', 'c4.meta@test.invalid', 'C4', 'Metadata', '0500008005', 'not-a-real-hash',
   'c4.meta', 'c4c4c4c4-0000-4000-8000-000000000204', '00000000-0000-4000-8000-000000000101',
   '00000000-0000-4000-8000-000000000102', 'c4c4c4c4-0000-4000-8000-000000000301', NULL),
  ('c4c4c4c4-0000-4000-8000-000000000506', 'c4.none@test.invalid', 'C4', 'None', '0500008006', 'not-a-real-hash',
   'c4.none', 'c4c4c4c4-0000-4000-8000-000000000205', '00000000-0000-4000-8000-000000000101',
   '00000000-0000-4000-8000-000000000102', NULL, NULL)
ON CONFLICT (id) DO NOTHING;

INSERT INTO supervisors (id, user_id) VALUES
  ('c4c4c4c4-0000-4000-8000-000000000601', 'c4c4c4c4-0000-4000-8000-000000000502')
ON CONFLICT (id) DO NOTHING;
INSERT INTO supervisor_contracts (id, supervisor_id, contract_id) VALUES
  ('c4c4c4c4-0000-4000-8000-000000000611', 'c4c4c4c4-0000-4000-8000-000000000601',
   'c4c4c4c4-0000-4000-8000-000000000301')
ON CONFLICT (id) DO NOTHING;

INSERT INTO clints (id, name, "identityNumber", "accountNumber", amount, attribute, user_id, portfolios_id, is_archive) VALUES
  ('c4c4c4c4-0000-4000-8000-00000000c001', 'C4 case K1', '1880000001', 'C4ACC-K1', 1000, '{}',
   'c4c4c4c4-0000-4000-8000-000000000503', 'c4c4c4c4-0000-4000-8000-000000000401', false),
  ('c4c4c4c4-0000-4000-8000-00000000c002', 'C4 case K2', '1880000002', 'C4ACC-K2', 1000, '{}',
   'c4c4c4c4-0000-4000-8000-000000000503', 'c4c4c4c4-0000-4000-8000-000000000401', false),
  ('c4c4c4c4-0000-4000-8000-00000000c003', 'C4 case K3', '1880000003', 'C4ACC-K3', 1000, '{}',
   'c4c4c4c4-0000-4000-8000-000000000504', 'c4c4c4c4-0000-4000-8000-000000000402', false),
  ('c4c4c4c4-0000-4000-8000-00000000c004', 'C4 case K4', '1880000004', 'C4ACC-K4', 1000, '{}',
   'c4c4c4c4-0000-4000-8000-000000000503', 'c4c4c4c4-0000-4000-8000-000000000401', false),
  ('c4c4c4c4-0000-4000-8000-00000000c005', 'C4 case K5', '1880000004', 'C4ACC-K5', 1000, '{}',
   'c4c4c4c4-0000-4000-8000-000000000504', 'c4c4c4c4-0000-4000-8000-000000000402', false),
  ('c4c4c4c4-0000-4000-8000-00000000c006', 'C4 case K6', '1880000006', 'C4ACC-K6', 1000, '{}',
   'c4c4c4c4-0000-4000-8000-000000000504', 'c4c4c4c4-0000-4000-8000-000000000402', false),
  ('c4c4c4c4-0000-4000-8000-00000000c007', 'C4 case K7', '1880000007', 'C4ACC-K7', 1000, '{}',
   'c4c4c4c4-0000-4000-8000-000000000503', 'c4c4c4c4-0000-4000-8000-000000000401', false),
  ('c4c4c4c4-0000-4000-8000-00000000c008', 'C4 case K8', '1880000008', 'C4ACC-K8', 1000, '{}',
   'c4c4c4c4-0000-4000-8000-000000000503', 'c4c4c4c4-0000-4000-8000-000000000401', false),
  ('c4c4c4c4-0000-4000-8000-00000000c009', 'C4 case K9', '1880000008', 'C4ACC-K9', 1000, '{}',
   'c4c4c4c4-0000-4000-8000-000000000503', 'c4c4c4c4-0000-4000-8000-000000000401', false)
ON CONFLICT (id) DO NOTHING;

INSERT INTO "ClintContacts" (id, contact_type_id, "identityNumber", contact_status, contact, is_default, is_deleted) VALUES
  ('c4c4c4c4-0000-4000-8000-00000000d001', '00000000-0000-4000-8000-000000000706', '1880000001', 'active', '0561240001', true, false),
  ('c4c4c4c4-0000-4000-8000-00000000d002', '00000000-0000-4000-8000-000000000706', '1880000002', 'active', '0561240002', true, false),
  ('c4c4c4c4-0000-4000-8000-00000000d003', '00000000-0000-4000-8000-000000000706', '1880000003', 'active', '0561240003', true, false),
  ('c4c4c4c4-0000-4000-8000-00000000d004', '00000000-0000-4000-8000-000000000706', '1880000004', 'active', '0561240004', true, false),
  ('c4c4c4c4-0000-4000-8000-00000000d006', '00000000-0000-4000-8000-000000000706', '1880000006', 'active', '0561240006', true, false),
  ('c4c4c4c4-0000-4000-8000-00000000d007', '00000000-0000-4000-8000-000000000706', '1880000007', 'active', '966561240007', true, false),
  ('c4c4c4c4-0000-4000-8000-00000000d008', '00000000-0000-4000-8000-000000000706', '1880000008', 'active', '0561240008', true, false)
ON CONFLICT (id) DO NOTHING;

-- call attempts (P0 table): ended PBX calls with UNKNOWN duration, inserted in their final state
INSERT INTO call_attempts (id, mode, state, case_id, destination, collector_user_id, extension, created_by, created_at,
                           authorized_at, dispatched_at, agent_answered_at, customer_dialing_at, ended_at,
                           pbx_epoch, pbx_channel_id) VALUES
  ('a1c4a001-0000-4000-8000-000000000001', 'click_to_call', 'ended', 'c4c4c4c4-0000-4000-8000-00000000c001',
   '+966561240001', 'c4c4c4c4-0000-4000-8000-000000000503', '7301', 'c4c4c4c4-0000-4000-8000-000000000503',
   '2026-09-22 06:59:50+00', '2026-09-22 06:59:55+00', '2026-09-22 07:00:00+00', '2026-09-22 07:00:04+00',
   '2026-09-22 07:00:05+00', '2026-09-22 07:01:10+00', 'p4-epoch', 'cawp-a1c4a001-0000-4000-8000-000000000001'),
  ('a2c4a002-0000-4000-8000-000000000002', 'click_to_call', 'ended', 'c4c4c4c4-0000-4000-8000-00000000c003',
   '+966561240003', 'c4c4c4c4-0000-4000-8000-000000000504', '7302', 'c4c4c4c4-0000-4000-8000-000000000504',
   '2026-09-22 07:59:50+00', '2026-09-22 07:59:55+00', '2026-09-22 08:00:00+00', '2026-09-22 08:00:04+00',
   '2026-09-22 08:00:05+00', '2026-09-22 08:00:50+00', 'p4-epoch', 'cawp-a2c4a002-0000-4000-8000-000000000002'),
  ('a3c4a003-0000-4000-8000-000000000003', 'click_to_call', 'ended', 'c4c4c4c4-0000-4000-8000-00000000c002',
   '+966561240002', 'c4c4c4c4-0000-4000-8000-000000000503', '7301', 'c4c4c4c4-0000-4000-8000-000000000503',
   '2026-09-22 08:59:50+00', '2026-09-22 08:59:55+00', '2026-09-22 09:00:00+00', '2026-09-22 09:00:04+00',
   '2026-09-22 09:00:05+00', '2026-09-22 09:00:30+00', 'p4-epoch', 'cawp-a3c4a003-0000-4000-8000-000000000003'),
  ('a4c4a004-0000-4000-8000-000000000004', 'click_to_call', 'ended', 'c4c4c4c4-0000-4000-8000-00000000c006',
   '+966561240006', 'c4c4c4c4-0000-4000-8000-000000000503', '7301', 'c4c4c4c4-0000-4000-8000-000000000503',
   '2026-09-22 09:59:50+00', '2026-09-22 09:59:55+00', '2026-09-22 10:00:00+00', '2026-09-22 10:00:04+00',
   '2026-09-22 10:00:05+00', '2026-09-22 10:00:40+00', 'p4-epoch', 'cawp-a4c4a004-0000-4000-8000-000000000004'),
  ('a5c4a005-0000-4000-8000-000000000005', 'click_to_call', 'ended', 'c4c4c4c4-0000-4000-8000-00000000c007',
   '+966561240007', 'c4c4c4c4-0000-4000-8000-000000000503', '7301', 'c4c4c4c4-0000-4000-8000-000000000503',
   '2026-09-23 06:59:50+00', '2026-09-23 06:59:55+00', '2026-09-23 07:00:00+00', '2026-09-23 07:00:04+00',
   '2026-09-23 07:00:05+00', '2026-09-23 07:01:00+00', 'p4-epoch', 'cawp-a5c4a005-0000-4000-8000-000000000005'),
  ('a6c4a006-0000-4000-8000-000000000006', 'click_to_call', 'ended', 'c4c4c4c4-0000-4000-8000-00000000c008',
   '+966561240008', 'c4c4c4c4-0000-4000-8000-000000000503', '7301', 'c4c4c4c4-0000-4000-8000-000000000503',
   '2026-09-23 07:59:50+00', '2026-09-23 07:59:55+00', '2026-09-23 08:00:00+00', '2026-09-23 08:00:04+00',
   '2026-09-23 08:00:05+00', '2026-09-23 08:01:00+00', 'p4-epoch', 'cawp-a6c4a006-0000-4000-8000-000000000006')
ON CONFLICT (id) DO NOTHING;

INSERT INTO call_legs (id, pbx_epoch, uniqueid, attempt_id, role, channel, created_pbx_ts, answered_pbx_ts) VALUES
  ('c4c4c4c4-0000-4000-8000-00000000e001', 'p4-epoch', 'cawp-a1c4a001-0000-4000-8000-000000000001',
   'a1c4a001-0000-4000-8000-000000000001', 'agent', 'PJSIP/7301-00000101', 1790305200.1, 1790305204.1),
  ('c4c4c4c4-0000-4000-8000-00000000e002', 'p4-epoch', '1790305205.102',
   'a1c4a001-0000-4000-8000-000000000001', 'customer', 'PJSIP/cawp-test-trunk-00000102', 1790305205.2, 1790305209.2)
ON CONFLICT (id) DO NOTHING;
