-- Phase 6 Q2 (quality workspace) fixtures, loaded once by tests/test_quality_support.py after the
-- shared seed.sql. Synthetic data only. Ids a2a2a2a2-0000-4000-8000-...; logins q2.*.
-- Re-runnable (ON CONFLICT DO NOTHING).
--
--   q2.rev_a   Q2Reviewer   supervisor of contract Q2-A           quality_review
--   q2.app_a   Q2Approver   supervisor of contract Q2-A           quality_review + quality_approve
--   q2.rev_b   Q2Reviewer   collector, owns case KB1 (contract Q2-B)
--   q2.none    Q2None       no quality key
--   q2.col_a   Q2None       collector, owns case KA1 (contract Q2-A)
--
-- Cases: KA1 (contract A; debtor name, identity, account and a mobile used by the masking tests),
--        KB1 (contract B).

INSERT INTO roles (id, name, permission) VALUES
  ('a2a2a2a2-0000-4000-8000-000000000201', 'Q2Reviewer',
   '{"clints": true, "quality_review": true, "quality_approve": false, "ai_assist": false}'),
  ('a2a2a2a2-0000-4000-8000-000000000202', 'Q2Approver',
   '{"clints": true, "quality_review": true, "quality_approve": true}'),
  ('a2a2a2a2-0000-4000-8000-000000000203', 'Q2None',
   '{"clints": true, "quality_review": false, "quality_approve": false}')
ON CONFLICT (id) DO NOTHING;

INSERT INTO contracts (id, name) VALUES
  ('a2a2a2a2-0000-4000-8000-000000000301', 'Q2 contract A'),
  ('a2a2a2a2-0000-4000-8000-000000000302', 'Q2 contract B')
ON CONFLICT (id) DO NOTHING;
INSERT INTO portfolios (id, name, contract_id) VALUES
  ('a2a2a2a2-0000-4000-8000-000000000401', 'Q2 portfolio A', 'a2a2a2a2-0000-4000-8000-000000000301'),
  ('a2a2a2a2-0000-4000-8000-000000000402', 'Q2 portfolio B', 'a2a2a2a2-0000-4000-8000-000000000302')
ON CONFLICT (id) DO NOTHING;

INSERT INTO users (id, email, frist_name, last_name, mobile, password, user_name, role_id, nationality_id,
                   branch_id, contract_id) VALUES
  ('a2a2a2a2-0000-4000-8000-000000000501', 'q2.rev_a@test.invalid', 'Q2', 'Reviewer A', '0500009001', 'not-a-real-hash',
   'q2.rev_a', 'a2a2a2a2-0000-4000-8000-000000000201', '00000000-0000-4000-8000-000000000101',
   '00000000-0000-4000-8000-000000000102', NULL),
  ('a2a2a2a2-0000-4000-8000-000000000502', 'q2.app_a@test.invalid', 'Q2', 'Approver A', '0500009002', 'not-a-real-hash',
   'q2.app_a', 'a2a2a2a2-0000-4000-8000-000000000202', '00000000-0000-4000-8000-000000000101',
   '00000000-0000-4000-8000-000000000102', NULL),
  ('a2a2a2a2-0000-4000-8000-000000000503', 'q2.rev_b@test.invalid', 'Q2', 'Reviewer B', '0500009003', 'not-a-real-hash',
   'q2.rev_b', 'a2a2a2a2-0000-4000-8000-000000000201', '00000000-0000-4000-8000-000000000101',
   '00000000-0000-4000-8000-000000000102', 'a2a2a2a2-0000-4000-8000-000000000302'),
  ('a2a2a2a2-0000-4000-8000-000000000504', 'q2.none@test.invalid', 'Q2', 'None', '0500009004', 'not-a-real-hash',
   'q2.none', 'a2a2a2a2-0000-4000-8000-000000000203', '00000000-0000-4000-8000-000000000101',
   '00000000-0000-4000-8000-000000000102', NULL),
  ('a2a2a2a2-0000-4000-8000-000000000505', 'q2.col_a@test.invalid', 'Q2', 'Collector A', '0500009005', 'not-a-real-hash',
   'q2.col_a', 'a2a2a2a2-0000-4000-8000-000000000203', '00000000-0000-4000-8000-000000000101',
   '00000000-0000-4000-8000-000000000102', 'a2a2a2a2-0000-4000-8000-000000000301')
ON CONFLICT (id) DO NOTHING;

INSERT INTO supervisors (id, user_id) VALUES
  ('a2a2a2a2-0000-4000-8000-000000000601', 'a2a2a2a2-0000-4000-8000-000000000501'),
  ('a2a2a2a2-0000-4000-8000-000000000602', 'a2a2a2a2-0000-4000-8000-000000000502')
ON CONFLICT (id) DO NOTHING;
INSERT INTO supervisor_contracts (id, supervisor_id, contract_id) VALUES
  ('a2a2a2a2-0000-4000-8000-000000000611', 'a2a2a2a2-0000-4000-8000-000000000601',
   'a2a2a2a2-0000-4000-8000-000000000301'),
  ('a2a2a2a2-0000-4000-8000-000000000612', 'a2a2a2a2-0000-4000-8000-000000000602',
   'a2a2a2a2-0000-4000-8000-000000000301')
ON CONFLICT (id) DO NOTHING;

INSERT INTO clints (id, name, "identityNumber", "accountNumber", amount, attribute, user_id, portfolios_id, is_archive) VALUES
  ('a2a2a2a2-0000-4000-8000-00000000c001', 'خالد عبدالله العتيبي', '1076543219', 'Q2ACC-7788990011', 5000, '{}',
   'a2a2a2a2-0000-4000-8000-000000000505', 'a2a2a2a2-0000-4000-8000-000000000401', false),
  ('a2a2a2a2-0000-4000-8000-00000000c002', 'Q2 case KB1', '1076543220', 'Q2ACC-KB1', 900, '{}',
   'a2a2a2a2-0000-4000-8000-000000000503', 'a2a2a2a2-0000-4000-8000-000000000402', false)
ON CONFLICT (id) DO NOTHING;

INSERT INTO "ClintContacts" (id, contact_type_id, "identityNumber", contact_status, contact, is_default) VALUES
  ('a2a2a2a2-0000-4000-8000-000000000801', '00000000-0000-4000-8000-000000000706', '1076543219',
   'active', '0551234567', true)
ON CONFLICT (id) DO NOTHING;
