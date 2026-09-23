-- Fixtures for the stream B (repayment plans) tests, loaded once per session by
-- tests/test_plans_support.py after tests/fixtures/seed.sql. Synthetic data only.
-- Re-runnable (ON CONFLICT DO NOTHING). Cases, payments, promises and plans are created per test.
--
-- Scope set-up:
--   pl.super    role "SuperAdmin"      -> unrestricted scope; plans, plans_manage, money keys, legacy
--                                          approve/reject/delete keys
--   pl.sup      role PlanSupervisor    -> supervisor of contract "Plans contract" only; plans_manage
--                                          (no `plans`: manage alone is enough to read), money keys
--   pl.col      role PlanCollector     -> collector: own cases only; `plans` (read) only
--   pl.col2     role PlanCollector     -> owns the cases in "Plans other contract"
--   pl.none     role PlanNone          -> no keys at all

INSERT INTO roles (id, name, permission) VALUES
  ('c0de0b00-0000-4000-8000-000000000201', 'SuperAdmin',
   '{"clints": true, "collections_users": true, "clints_Payments": true, "clints_PayemntDates": true,
     "accounts_payments": true, "payments_approve": true, "payments_reverse": true, "promises": true,
     "promises_cancel": true, "promises_allocate": true, "plans": true, "plans_manage": true}'),
  ('c0de0b00-0000-4000-8000-000000000202', 'PlanSupervisor',
   '{"clints": true, "collections_users": true, "clints_Payments": true, "accounts_payments": true,
     "payments_approve": true, "payments_reverse": true, "promises": true, "plans": false,
     "plans_manage": true}'),
  ('c0de0b00-0000-4000-8000-000000000203', 'PlanCollector',
   '{"clints": true, "clints_Payments": true, "payments_approve": false, "plans": true, "plans_manage": false}'),
  ('c0de0b00-0000-4000-8000-000000000204', 'PlanNone', '{}')
ON CONFLICT (id) DO NOTHING;

INSERT INTO contracts (id, name) VALUES
  ('c0de0b00-0000-4000-8000-000000000301', 'Plans contract'),
  ('c0de0b00-0000-4000-8000-000000000302', 'Plans other contract')
ON CONFLICT (id) DO NOTHING;

INSERT INTO portfolios (id, name, contract_id) VALUES
  ('c0de0b00-0000-4000-8000-000000000401', 'Plans portfolio', 'c0de0b00-0000-4000-8000-000000000301'),
  ('c0de0b00-0000-4000-8000-000000000402', 'Plans other portfolio', 'c0de0b00-0000-4000-8000-000000000302')
ON CONFLICT (id) DO NOTHING;

INSERT INTO users (id, email, frist_name, last_name, mobile, password, user_name, role_id,
                   nationality_id, branch_id, contract_id) VALUES
  ('c0de0b00-0000-4000-8000-000000000501', 'pl.super@test.invalid', 'Plan', 'Super', '0500002001',
   'not-a-real-hash', 'pl.super', 'c0de0b00-0000-4000-8000-000000000201',
   '00000000-0000-4000-8000-000000000101', '00000000-0000-4000-8000-000000000102', NULL),
  ('c0de0b00-0000-4000-8000-000000000502', 'pl.sup@test.invalid', 'Plan', 'Supervisor', '0500002002',
   'not-a-real-hash', 'pl.sup', 'c0de0b00-0000-4000-8000-000000000202',
   '00000000-0000-4000-8000-000000000101', '00000000-0000-4000-8000-000000000102', NULL),
  ('c0de0b00-0000-4000-8000-000000000503', 'pl.col@test.invalid', 'Plan', 'Collector', '0500002003',
   'not-a-real-hash', 'pl.col', 'c0de0b00-0000-4000-8000-000000000203',
   '00000000-0000-4000-8000-000000000101', '00000000-0000-4000-8000-000000000102',
   'c0de0b00-0000-4000-8000-000000000301'),
  ('c0de0b00-0000-4000-8000-000000000504', 'pl.col2@test.invalid', 'Plan', 'Collector2', '0500002004',
   'not-a-real-hash', 'pl.col2', 'c0de0b00-0000-4000-8000-000000000203',
   '00000000-0000-4000-8000-000000000101', '00000000-0000-4000-8000-000000000102',
   'c0de0b00-0000-4000-8000-000000000302'),
  ('c0de0b00-0000-4000-8000-000000000505', 'pl.none@test.invalid', 'Plan', 'None', '0500002005',
   'not-a-real-hash', 'pl.none', 'c0de0b00-0000-4000-8000-000000000204',
   '00000000-0000-4000-8000-000000000101', '00000000-0000-4000-8000-000000000102', NULL)
ON CONFLICT (id) DO NOTHING;

INSERT INTO supervisors (id, user_id) VALUES
  ('c0de0b00-0000-4000-8000-000000000601', 'c0de0b00-0000-4000-8000-000000000502')
ON CONFLICT (id) DO NOTHING;
INSERT INTO supervisor_contracts (id, supervisor_id, contract_id) VALUES
  ('c0de0b00-0000-4000-8000-000000000611', 'c0de0b00-0000-4000-8000-000000000601',
   'c0de0b00-0000-4000-8000-000000000301')
ON CONFLICT (id) DO NOTHING;
