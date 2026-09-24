-- Fixtures for the Phase 4 stream R-C report tests (tests/test_reports_v2_c*.py), loaded once
-- per session after tests/fixtures/seed.sql and seed_reports.sql. Synthetic data only.
-- Ids: ecececec-0000-4000-8000-...; logins rc.*; accounts RCACC-*.
--
--   rc.super  role "SuperAdmin"    unrestricted; every R-C key + recording keys + users_details
--   rc.sup    role RcSupervisor    supervisor of contract RC-A; dashbord_admin, clints_callsRecords,
--                                  reports_performance, reports_calls (no recording, no target keys)
--   rc.col1   role RcCollector     contract RC-A, extension 7101, own target 5000 (legacy columns)
--   rc.col2   role RcCollector     contract RC-B, extension 7102, no own target
--   rc.col3   role RcCollector     contract RC-A, extension 7103 (shared with rc.col4)
--   rc.col4   role RcCollector     contract RC-B, extension 7103, no cases
--   rc.none   role RcNone          no keys
--   RcCollector = dashbord_user + clints_callsRecords + clints_callsRecords_play
--
-- Cases (the contact numbers are those of db/cdr-test/init/02_seed.sql):
--   A1 col1  1990000101  0551230001, '+966 55 123 0002', e-mail
--   A2 col1  1990000102  0551230003
--   A3 col1  1990000103  Arabic-Indic '٠٥٥١٢٣٠٠٠٤'     (same debtor as B2)
--   A4 col1  1990000104  0551230005   archived
--   A5 col3  1990000105  0551230006   contact deleted
--   B1 col2  1990000201  966551239902
--   B2 col2  1990000103  (A3's debtor)
-- Targets (history rows for June 2026): col1 amount 5000; col2/col3/col4 none; RC-A amount
-- 3000; RC-B percent 10. June 2026 payments: A1 2500 (col1, stored at Riyadh midnight),
-- A2 1000 (recorded by col3), B1 1150 (col2), B1 400 (recorded by col1), B1 500 pending,
-- B1 300 reversed. 2026-09-20: notes and payments around the Riyadh day bounds.

INSERT INTO roles (id, name, permission) VALUES
  ('ecececec-0000-4000-8000-000000000201', 'SuperAdmin',
   '{"dashbord_user": true, "dashbord_admin": true, "reports_performance": true, "reports_calls": true,
     "clints_callsRecords": true, "clints_callsRecords_download": true, "clints_callsRecords_play": true,
     "users_details": true}'),
  ('ecececec-0000-4000-8000-000000000202', 'RcSupervisor',
   '{"dashbord_user": false, "dashbord_admin": true, "reports_performance": true, "reports_calls": true,
     "clints_callsRecords": true, "clints_callsRecords_download": false, "clints_callsRecords_play": false}'),
  ('ecececec-0000-4000-8000-000000000203', 'RcCollector',
   '{"dashbord_user": true, "dashbord_admin": false, "reports_performance": false, "reports_calls": false,
     "clints_callsRecords": true, "clints_callsRecords_download": false, "clints_callsRecords_play": true}'),
  ('ecececec-0000-4000-8000-000000000204', 'RcNone', '{}')
ON CONFLICT (id) DO NOTHING;

INSERT INTO contracts (id, name, monthly_target_amount, monthly_target_percent) VALUES
  ('ecececec-0000-4000-8000-000000000301', 'RC contract A', 3000, 0),
  ('ecececec-0000-4000-8000-000000000302', 'RC contract B', 0, 10)
ON CONFLICT (id) DO NOTHING;
INSERT INTO portfolios (id, name, contract_id) VALUES
  ('ecececec-0000-4000-8000-000000000401', 'RC portfolio A1', 'ecececec-0000-4000-8000-000000000301'),
  ('ecececec-0000-4000-8000-000000000402', 'RC portfolio B1', 'ecececec-0000-4000-8000-000000000302')
ON CONFLICT (id) DO NOTHING;

INSERT INTO users (id, email, frist_name, last_name, mobile, password, user_name, role_id, nationality_id,
                   branch_id, contract_id, extension, target_amount, target_percent, target_is_percentage,
                   "identityNumber") VALUES
  ('ecececec-0000-4000-8000-000000000501', 'rc.super@test.invalid', 'RC', 'Super', '0500007001', 'not-a-real-hash',
   'rc.super', 'ecececec-0000-4000-8000-000000000201', '00000000-0000-4000-8000-000000000101',
   '00000000-0000-4000-8000-000000000102', NULL, NULL, 0, 0, false, '2000007001'),
  ('ecececec-0000-4000-8000-000000000502', 'rc.sup@test.invalid', 'RC', 'Supervisor', '0500007002', 'not-a-real-hash',
   'rc.sup', 'ecececec-0000-4000-8000-000000000202', '00000000-0000-4000-8000-000000000101',
   '00000000-0000-4000-8000-000000000102', NULL, NULL, 0, 0, false, '2000007002'),
  ('ecececec-0000-4000-8000-000000000503', 'rc.col1@test.invalid', 'محصل', 'أول', '0500007003', 'not-a-real-hash',
   'rc.col1', 'ecececec-0000-4000-8000-000000000203', '00000000-0000-4000-8000-000000000101',
   '00000000-0000-4000-8000-000000000102', 'ecececec-0000-4000-8000-000000000301', '7101', 5000, 0, false, '2000007003'),
  ('ecececec-0000-4000-8000-000000000504', 'rc.col2@test.invalid', 'RC', 'Collector2', '0500007004', 'not-a-real-hash',
   'rc.col2', 'ecececec-0000-4000-8000-000000000203', '00000000-0000-4000-8000-000000000101',
   '00000000-0000-4000-8000-000000000102', 'ecececec-0000-4000-8000-000000000302', '7102', 0, 0, false, '2000007004'),
  ('ecececec-0000-4000-8000-000000000505', 'rc.col3@test.invalid', 'RC', 'Collector3', '0500007005', 'not-a-real-hash',
   'rc.col3', 'ecececec-0000-4000-8000-000000000203', '00000000-0000-4000-8000-000000000101',
   '00000000-0000-4000-8000-000000000102', 'ecececec-0000-4000-8000-000000000301', '7103', 0, 0, false, '2000007005'),
  ('ecececec-0000-4000-8000-000000000506', 'rc.col4@test.invalid', 'RC', 'Collector4', '0500007006', 'not-a-real-hash',
   'rc.col4', 'ecececec-0000-4000-8000-000000000203', '00000000-0000-4000-8000-000000000101',
   '00000000-0000-4000-8000-000000000102', 'ecececec-0000-4000-8000-000000000302', '7103', 0, 0, false, '2000007006'),
  ('ecececec-0000-4000-8000-000000000507', 'rc.none@test.invalid', 'RC', 'None', '0500007007', 'not-a-real-hash',
   'rc.none', 'ecececec-0000-4000-8000-000000000204', '00000000-0000-4000-8000-000000000101',
   '00000000-0000-4000-8000-000000000102', NULL, NULL, 0, 0, false, '2000007007')
ON CONFLICT (id) DO NOTHING;

INSERT INTO supervisors (id, user_id) VALUES
  ('ecececec-0000-4000-8000-000000000601', 'ecececec-0000-4000-8000-000000000502')
ON CONFLICT (id) DO NOTHING;
INSERT INTO supervisor_contracts (id, supervisor_id, contract_id) VALUES
  ('ecececec-0000-4000-8000-000000000611', 'ecececec-0000-4000-8000-000000000601',
   'ecececec-0000-4000-8000-000000000301')
ON CONFLICT (id) DO NOTHING;

INSERT INTO clints (id, name, "identityNumber", "accountNumber", amount, attribute, user_id, portfolios_id, is_archive) VALUES
  ('ecececec-0000-4000-8000-00000000a001', 'عميل RC A1', '1990000101', 'RCACC-A1', 10000, '{}',
   'ecececec-0000-4000-8000-000000000503', 'ecececec-0000-4000-8000-000000000401', false),
  ('ecececec-0000-4000-8000-00000000a002', 'RC case A2', '1990000102', 'RCACC-A2', 5000, '{}',
   'ecececec-0000-4000-8000-000000000503', 'ecececec-0000-4000-8000-000000000401', false),
  ('ecececec-0000-4000-8000-00000000a003', 'RC case A3', '1990000103', 'RCACC-A3', 2000, '{}',
   'ecececec-0000-4000-8000-000000000503', 'ecececec-0000-4000-8000-000000000401', false),
  ('ecececec-0000-4000-8000-00000000a004', 'RC case A4', '1990000104', 'RCACC-A4', 1500, '{}',
   'ecececec-0000-4000-8000-000000000503', 'ecececec-0000-4000-8000-000000000401', true),
  ('ecececec-0000-4000-8000-00000000a005', 'RC case A5', '1990000105', 'RCACC-A5', 1000, '{}',
   'ecececec-0000-4000-8000-000000000505', 'ecececec-0000-4000-8000-000000000401', false),
  ('ecececec-0000-4000-8000-00000000b001', 'RC case B1', '1990000201', 'RCACC-B1', 20000, '{}',
   'ecececec-0000-4000-8000-000000000504', 'ecececec-0000-4000-8000-000000000402', false),
  ('ecececec-0000-4000-8000-00000000b002', 'RC case B2', '1990000103', 'RCACC-B2', 3000, '{}',
   'ecececec-0000-4000-8000-000000000504', 'ecececec-0000-4000-8000-000000000402', false)
ON CONFLICT (id) DO NOTHING;

INSERT INTO "ClintContacts" (id, contact_type_id, "identityNumber", contact_status, contact, is_default, is_deleted) VALUES
  ('ecececec-0000-4000-8000-00000000c001', '00000000-0000-4000-8000-000000000706', '1990000101', 'active', '0551230001', true, false),
  ('ecececec-0000-4000-8000-00000000c002', '00000000-0000-4000-8000-000000000706', '1990000101', 'active', '+966 55 123 0002', false, false),
  ('ecececec-0000-4000-8000-00000000c003', '00000000-0000-4000-8000-000000000706', '1990000101', 'active', 'rc.a1@example.invalid', false, false),
  ('ecececec-0000-4000-8000-00000000c004', '00000000-0000-4000-8000-000000000706', '1990000102', 'active', '0551230003', true, false),
  ('ecececec-0000-4000-8000-00000000c005', '00000000-0000-4000-8000-000000000706', '1990000103', 'active', '٠٥٥١٢٣٠٠٠٤', true, false),
  ('ecececec-0000-4000-8000-00000000c006', '00000000-0000-4000-8000-000000000706', '1990000104', 'active', '0551230005', true, false),
  ('ecececec-0000-4000-8000-00000000c007', '00000000-0000-4000-8000-000000000706', '1990000105', 'active', '0551230006', true, true),
  ('ecececec-0000-4000-8000-00000000c008', '00000000-0000-4000-8000-000000000706', '1990000201', 'active', '966551239902', true, false)
ON CONFLICT (id) DO NOTHING;

-- note classification: active / inactive / unknown (is_active NULL)
INSERT INTO comment_reachability (id, name) VALUES
  ('ecececec-0000-4000-8000-00000000d101', 'تم التوصل')
ON CONFLICT (id) DO NOTHING;
INSERT INTO "CommentStatuses" (id, name, "CommentReachability_id", is_active) VALUES
  ('ecececec-0000-4000-8000-00000000d201', 'RC وعد بالسداد', 'ecececec-0000-4000-8000-00000000d101', true),
  ('ecececec-0000-4000-8000-00000000d202', 'RC لا يرد', NULL, false),
  ('ecececec-0000-4000-8000-00000000d203', 'RC unclassified', NULL, NULL)
ON CONFLICT (id) DO NOTHING;

-- notes on 2026-09-20 (Riyadh) and at its bounds
INSERT INTO "Comments" (id, clint_id, user_id, "contactMethod_id", comment, comment_status_id, comment_reachability_id,
                        create_at, is_deleted) VALUES
  ('ecececec-0000-4000-8000-00000000e001', 'ecececec-0000-4000-8000-00000000a001', 'ecececec-0000-4000-8000-000000000503',
   '00000000-0000-4000-8000-000000000701', 'rc n1 08:10', 'ecececec-0000-4000-8000-00000000d201',
   'ecececec-0000-4000-8000-00000000d101', '2026-09-20T05:10:00Z', false),
  ('ecececec-0000-4000-8000-00000000e002', 'ecececec-0000-4000-8000-00000000a002', 'ecececec-0000-4000-8000-000000000503',
   '00000000-0000-4000-8000-000000000701', 'rc n2 08:59:59', 'ecececec-0000-4000-8000-00000000d202', NULL,
   '2026-09-20T05:59:59Z', false),
  ('ecececec-0000-4000-8000-00000000e003', 'ecececec-0000-4000-8000-00000000a002', 'ecececec-0000-4000-8000-000000000503',
   '00000000-0000-4000-8000-000000000701', 'rc n3 18:00', 'ecececec-0000-4000-8000-00000000d202', NULL,
   '2026-09-20T15:00:00Z', false),
  ('ecececec-0000-4000-8000-00000000e004', 'ecececec-0000-4000-8000-00000000b001', 'ecececec-0000-4000-8000-000000000503',
   '00000000-0000-4000-8000-000000000701', 'rc n4 10:00 on a case of col2', 'ecececec-0000-4000-8000-00000000d201',
   'ecececec-0000-4000-8000-00000000d101', '2026-09-20T07:00:00Z', false),
  ('ecececec-0000-4000-8000-00000000e005', 'ecececec-0000-4000-8000-00000000a001', 'ecececec-0000-4000-8000-000000000503',
   '00000000-0000-4000-8000-000000000701', 'rc n5 retired', 'ecececec-0000-4000-8000-00000000d201', NULL,
   '2026-09-20T06:00:00Z', true),
  ('ecececec-0000-4000-8000-00000000e006', 'ecececec-0000-4000-8000-00000000a001', 'ecececec-0000-4000-8000-000000000503',
   '00000000-0000-4000-8000-000000000701', 'rc n6 00:00', 'ecececec-0000-4000-8000-00000000d202', NULL,
   '2026-09-19T21:00:00Z', false),
  ('ecececec-0000-4000-8000-00000000e007', 'ecececec-0000-4000-8000-00000000a001', 'ecececec-0000-4000-8000-000000000503',
   '00000000-0000-4000-8000-000000000701', 'rc n7 previous day 23:59:59', 'ecececec-0000-4000-8000-00000000d201', NULL,
   '2026-09-19T20:59:59Z', false),
  ('ecececec-0000-4000-8000-00000000e008', 'ecececec-0000-4000-8000-00000000a005', 'ecececec-0000-4000-8000-000000000505',
   '00000000-0000-4000-8000-000000000701', 'rc n8 13:30 unclassified', 'ecececec-0000-4000-8000-00000000d203', NULL,
   '2026-09-20T10:30:00Z', false)
ON CONFLICT (id) DO NOTHING;

-- payments: June 2026 (targets) and 2026-09-20 (dated dashboard)
INSERT INTO "Payments" (id, clint_id, user_id, collection_type_id, collection_method_id, amount, payment_date,
                        approval_status, approval_by, approval_at, create_at) VALUES
  ('ecececec-0000-4000-8000-0000000f0001', 'ecececec-0000-4000-8000-00000000a001', 'ecececec-0000-4000-8000-000000000503',
   '00000000-0000-4000-8000-000000000704', '00000000-0000-4000-8000-000000000705', 2500, '2026-06-09T21:00:00Z',
   true, 'ecececec-0000-4000-8000-000000000502', '2026-06-11T08:00:00Z', '2026-06-10T08:00:00Z'),
  ('ecececec-0000-4000-8000-0000000f0002', 'ecececec-0000-4000-8000-00000000a002', 'ecececec-0000-4000-8000-000000000505',
   '00000000-0000-4000-8000-000000000704', '00000000-0000-4000-8000-000000000705', 1000, '2026-06-20T09:00:00Z',
   true, 'ecececec-0000-4000-8000-000000000502', '2026-06-21T08:00:00Z', '2026-06-20T09:00:00Z'),
  ('ecececec-0000-4000-8000-0000000f0003', 'ecececec-0000-4000-8000-00000000b001', 'ecececec-0000-4000-8000-000000000504',
   '00000000-0000-4000-8000-000000000704', '00000000-0000-4000-8000-000000000705', 1150, '2026-06-05T10:00:00Z',
   true, 'ecececec-0000-4000-8000-000000000502', '2026-06-06T08:00:00Z', '2026-06-05T10:00:00Z'),
  ('ecececec-0000-4000-8000-0000000f0004', 'ecececec-0000-4000-8000-00000000b001', 'ecececec-0000-4000-8000-000000000503',
   '00000000-0000-4000-8000-000000000704', '00000000-0000-4000-8000-000000000705', 400, '2026-06-12T10:00:00Z',
   true, 'ecececec-0000-4000-8000-000000000502', '2026-06-13T08:00:00Z', '2026-06-12T10:00:00Z'),
  ('ecececec-0000-4000-8000-0000000f0005', 'ecececec-0000-4000-8000-00000000b001', 'ecececec-0000-4000-8000-000000000504',
   '00000000-0000-4000-8000-000000000704', '00000000-0000-4000-8000-000000000705', 500, '2026-06-06T10:00:00Z',
   NULL, NULL, NULL, '2026-06-06T10:00:00Z'),
  ('ecececec-0000-4000-8000-0000000f0006', 'ecececec-0000-4000-8000-00000000b001', 'ecececec-0000-4000-8000-000000000504',
   '00000000-0000-4000-8000-000000000704', '00000000-0000-4000-8000-000000000705', 300, '2026-06-07T10:00:00Z',
   true, 'ecececec-0000-4000-8000-000000000502', '2026-06-08T08:00:00Z', '2026-06-07T10:00:00Z'),
  ('ecececec-0000-4000-8000-0000000f0101', 'ecececec-0000-4000-8000-00000000a001', 'ecececec-0000-4000-8000-000000000503',
   '00000000-0000-4000-8000-000000000704', '00000000-0000-4000-8000-000000000705', 700.50, '2026-09-19T21:00:00Z',
   true, 'ecececec-0000-4000-8000-000000000502', '2026-09-21T08:00:00Z', '2026-09-20T08:00:00Z'),
  ('ecececec-0000-4000-8000-0000000f0102', 'ecececec-0000-4000-8000-00000000a001', 'ecececec-0000-4000-8000-000000000503',
   '00000000-0000-4000-8000-000000000704', '00000000-0000-4000-8000-000000000705', 100, '2026-09-20T09:00:00Z',
   NULL, NULL, NULL, '2026-09-20T09:00:00Z'),
  ('ecececec-0000-4000-8000-0000000f0103', 'ecececec-0000-4000-8000-00000000b001', 'ecececec-0000-4000-8000-000000000503',
   '00000000-0000-4000-8000-000000000704', '00000000-0000-4000-8000-000000000705', 250.25, '2026-09-20T09:00:00Z',
   true, 'ecececec-0000-4000-8000-000000000502', '2026-09-21T08:00:00Z', '2026-09-20T09:00:00Z'),
  ('ecececec-0000-4000-8000-0000000f0104', 'ecececec-0000-4000-8000-00000000a002', 'ecececec-0000-4000-8000-000000000503',
   '00000000-0000-4000-8000-000000000704', '00000000-0000-4000-8000-000000000705', 80, '2026-09-20T09:00:00Z',
   true, 'ecececec-0000-4000-8000-000000000502', '2026-09-21T08:00:00Z', '2026-09-20T09:00:00Z'),
  ('ecececec-0000-4000-8000-0000000f0105', 'ecececec-0000-4000-8000-00000000a002', 'ecececec-0000-4000-8000-000000000503',
   '00000000-0000-4000-8000-000000000704', '00000000-0000-4000-8000-000000000705', 60, '2026-09-20T09:00:00Z',
   false, 'ecececec-0000-4000-8000-000000000502', '2026-09-21T08:00:00Z', '2026-09-20T09:00:00Z')
ON CONFLICT (id) DO NOTHING;

-- reversals (the Phase 1 rule: reversed payments also get approval_status = false, is_deleted = true)
INSERT INTO payment_reversals (payment_id, case_id, amount, reason, reversed_by)
SELECT p.id, p.clint_id, round(p.amount::numeric, 2), 'bounced cheque', 'ecececec-0000-4000-8000-000000000502'
  FROM "Payments" p
 WHERE p.id IN ('ecececec-0000-4000-8000-0000000f0006', 'ecececec-0000-4000-8000-0000000f0104')
   AND NOT EXISTS (SELECT 1 FROM payment_reversals r WHERE r.payment_id = p.id);
UPDATE "Payments" SET approval_status = false, is_deleted = true
 WHERE id IN ('ecececec-0000-4000-8000-0000000f0006', 'ecececec-0000-4000-8000-0000000f0104') AND is_deleted = false;

-- one promise recorded by col1 (RPT-17)
INSERT INTO "paymentDates" (id, clint_id, user_id, payment_date, amount) VALUES
  ('ecececec-0000-4000-8000-00000000f201', 'ecececec-0000-4000-8000-00000000a001', 'ecececec-0000-4000-8000-000000000503',
   '2026-09-30T21:00:00Z', 1200)
ON CONFLICT (id) DO NOTHING;

-- target history for June 2026 (appended like the v2 targets screen does)
INSERT INTO targets (subject_type, subject_id, month, revision, kind, value, source, set_by, reason)
SELECT v.st, v.sid::uuid, DATE '2026-06-01', 1, v.kind, v.val, 'v2', 'ecececec-0000-4000-8000-000000000501'::uuid,
       'R-C fixture'
  FROM (VALUES ('user', 'ecececec-0000-4000-8000-000000000503', 'amount', 5000),
               ('user', 'ecececec-0000-4000-8000-000000000504', 'none', 0),
               ('user', 'ecececec-0000-4000-8000-000000000505', 'none', 0),
               ('user', 'ecececec-0000-4000-8000-000000000506', 'none', 0),
               ('contract', 'ecececec-0000-4000-8000-000000000301', 'amount', 3000),
               ('contract', 'ecececec-0000-4000-8000-000000000302', 'percent', 10)) v(st, sid, kind, val)
 WHERE NOT EXISTS (SELECT 1 FROM targets t WHERE t.subject_id = v.sid::uuid AND t.month = DATE '2026-06-01');
