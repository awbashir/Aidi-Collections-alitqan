-- Fixtures for the stream A (money) tests, loaded once per session by
-- tests/test_money_support.py after tests/fixtures/seed.sql. Synthetic data only.
-- Re-runnable (ON CONFLICT DO NOTHING). Cases, payments and promises are created per test.
--
-- Scope set-up:
--   m.super     role "SuperAdmin"      -> unrestricted scope, every money key
--   m.sup       role MoneySupervisor   -> supervisor of contract "Money contract" only
--   m.col       role MoneyCollector    -> collector: own cases only; clints_Payments, promises
--   m.col2      role MoneyCollector    -> owns the cases in "Other contract"
--   m.none      role MoneyNone         -> no keys at all
--   m.approver  role MoneyApproverOnly -> payments_approve only (no reverse, no promise keys)

INSERT INTO roles (id, name, permission) VALUES
  ('aaaaaaaa-0000-4000-8000-000000000201', 'SuperAdmin',
   '{"clints": true, "collections_users": true, "clints_Payments": true, "clints_PayemntDates": true, "accounts_payments": true,
     "payments_approve": true, "payments_reverse": true, "promises": true, "promises_cancel": true,
     "promises_allocate": true}'),
  ('aaaaaaaa-0000-4000-8000-000000000202', 'MoneySupervisor',
   '{"clints": true, "collections_users": true, "clints_Payments": true, "accounts_payments": true,
     "payments_approve": true, "payments_reverse": true, "promises": true, "promises_cancel": true,
     "promises_allocate": true}'),
  ('aaaaaaaa-0000-4000-8000-000000000203', 'MoneyCollector',
   '{"clints": true, "clints_Payments": true, "clints_PayemntDates": true, "accounts_payments": false,
     "payments_approve": false, "payments_reverse": false, "promises": true, "promises_cancel": false,
     "promises_allocate": false}'),
  ('aaaaaaaa-0000-4000-8000-000000000204', 'MoneyNone', '{}'),
  ('aaaaaaaa-0000-4000-8000-000000000205', 'MoneyApproverOnly', '{"payments_approve": true}')
ON CONFLICT (id) DO NOTHING;

INSERT INTO contracts (id, name) VALUES
  ('aaaaaaaa-0000-4000-8000-000000000301', 'Money contract'),
  ('aaaaaaaa-0000-4000-8000-000000000302', 'Other contract')
ON CONFLICT (id) DO NOTHING;

INSERT INTO portfolios (id, name, contract_id) VALUES
  ('aaaaaaaa-0000-4000-8000-000000000401', 'Money portfolio', 'aaaaaaaa-0000-4000-8000-000000000301'),
  ('aaaaaaaa-0000-4000-8000-000000000402', 'Other portfolio', 'aaaaaaaa-0000-4000-8000-000000000302')
ON CONFLICT (id) DO NOTHING;

INSERT INTO users (id, email, frist_name, last_name, mobile, password, user_name, role_id,
                   nationality_id, branch_id, contract_id) VALUES
  ('aaaaaaaa-0000-4000-8000-000000000501', 'm.super@test.invalid', 'Money', 'Super', '0500001001',
   'not-a-real-hash', 'm.super', 'aaaaaaaa-0000-4000-8000-000000000201',
   '00000000-0000-4000-8000-000000000101', '00000000-0000-4000-8000-000000000102', NULL),
  ('aaaaaaaa-0000-4000-8000-000000000502', 'm.sup@test.invalid', 'Money', 'Supervisor', '0500001002',
   'not-a-real-hash', 'm.sup', 'aaaaaaaa-0000-4000-8000-000000000202',
   '00000000-0000-4000-8000-000000000101', '00000000-0000-4000-8000-000000000102', NULL),
  ('aaaaaaaa-0000-4000-8000-000000000503', 'm.col@test.invalid', 'Money', 'Collector', '0500001003',
   'not-a-real-hash', 'm.col', 'aaaaaaaa-0000-4000-8000-000000000203',
   '00000000-0000-4000-8000-000000000101', '00000000-0000-4000-8000-000000000102',
   'aaaaaaaa-0000-4000-8000-000000000301'),
  ('aaaaaaaa-0000-4000-8000-000000000504', 'm.col2@test.invalid', 'Money', 'Collector2', '0500001004',
   'not-a-real-hash', 'm.col2', 'aaaaaaaa-0000-4000-8000-000000000203',
   '00000000-0000-4000-8000-000000000101', '00000000-0000-4000-8000-000000000102',
   'aaaaaaaa-0000-4000-8000-000000000302'),
  ('aaaaaaaa-0000-4000-8000-000000000505', 'm.none@test.invalid', 'Money', 'None', '0500001005',
   'not-a-real-hash', 'm.none', 'aaaaaaaa-0000-4000-8000-000000000204',
   '00000000-0000-4000-8000-000000000101', '00000000-0000-4000-8000-000000000102', NULL),
  ('aaaaaaaa-0000-4000-8000-000000000506', 'm.approver@test.invalid', 'Money', 'Approver', '0500001006',
   'not-a-real-hash', 'm.approver', 'aaaaaaaa-0000-4000-8000-000000000205',
   '00000000-0000-4000-8000-000000000101', '00000000-0000-4000-8000-000000000102', NULL)
ON CONFLICT (id) DO NOTHING;

INSERT INTO supervisors (id, user_id) VALUES
  ('aaaaaaaa-0000-4000-8000-000000000601', 'aaaaaaaa-0000-4000-8000-000000000502')
ON CONFLICT (id) DO NOTHING;
INSERT INTO supervisor_contracts (id, supervisor_id, contract_id) VALUES
  ('aaaaaaaa-0000-4000-8000-000000000611', 'aaaaaaaa-0000-4000-8000-000000000601',
   'aaaaaaaa-0000-4000-8000-000000000301')
ON CONFLICT (id) DO NOTHING;
