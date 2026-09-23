-- Fixtures for the stream C (history) tests: loaded after tests/fixtures/seed.sql by the
-- `hist_seed` fixture in tests/test_history_support.py. Synthetic data only. UUIDs use
-- the ...0c.. block so they never collide with seed.sql or other streams.
-- Reuses from seed.sql: nationality ...0101, branch ...0102, contact method ...0701,
-- comment status ...0703.

-- roles -----------------------------------------------------------------------------------
INSERT INTO roles (id, name, permission) VALUES
  ('00000000-0000-4000-8000-0000000c0001', 'SuperAdmin',
   '{"clints": true, "clints_details": true, "clints_history": true, "clints_comments_correct": true,
     "clints_comments_retire": true, "clints_contacts_edit": true, "case_corrections_request": true,
     "case_corrections_review": true}'),
  ('00000000-0000-4000-8000-0000000c0002', 'HistReviewer',
   '{"clints": true, "clints_details": true, "clints_history": true, "clints_comments_correct": true,
     "clints_comments_retire": true, "clints_contacts_edit": true, "case_corrections_request": true,
     "case_corrections_review": true}'),
  ('00000000-0000-4000-8000-0000000c0003', 'HistCollector',
   '{"clints": true, "clints_details": true, "clints_history": true, "clints_comments_correct": true,
     "clints_comments_retire": true, "clints_contacts_edit": true, "case_corrections_request": true,
     "case_corrections_review": false}'),
  ('00000000-0000-4000-8000-0000000c0004', 'HistReadOnly',
   '{"clints": true, "clints_details": true, "clints_history": false, "clints_comments_correct": false,
     "clints_comments_retire": false, "clints_contacts_edit": false, "case_corrections_request": false,
     "case_corrections_review": false}');

-- contracts / portfolios --------------------------------------------------------------------
INSERT INTO contracts (id, name) VALUES
  ('00000000-0000-4000-8000-0000000c0101', 'Hist contract 1'),
  ('00000000-0000-4000-8000-0000000c0102', 'Hist contract 2');
INSERT INTO portfolios (id, name, contract_id) VALUES
  ('00000000-0000-4000-8000-0000000c0201', 'Hist portfolio 1', '00000000-0000-4000-8000-0000000c0101'),
  ('00000000-0000-4000-8000-0000000c0202', 'Hist portfolio 2', '00000000-0000-4000-8000-0000000c0102');

-- users -----------------------------------------------------------------------------------
INSERT INTO users (id, email, frist_name, last_name, mobile, password, user_name, role_id,
                   nationality_id, branch_id, contract_id) VALUES
  ('00000000-0000-4000-8000-0000000c0301', 'hist.super@test.invalid', 'Hist', 'Super', '0500000301',
   'not-a-real-hash', 'hist.super', '00000000-0000-4000-8000-0000000c0001',
   '00000000-0000-4000-8000-000000000101', '00000000-0000-4000-8000-000000000102', NULL),
  ('00000000-0000-4000-8000-0000000c0302', 'hist.reviewer@test.invalid', 'Hist', 'Reviewer', '0500000302',
   'not-a-real-hash', 'hist.reviewer', '00000000-0000-4000-8000-0000000c0002',
   '00000000-0000-4000-8000-000000000101', '00000000-0000-4000-8000-000000000102', NULL),
  ('00000000-0000-4000-8000-0000000c0303', 'hist.col1@test.invalid', 'Hist', 'Collector1', '0500000303',
   'not-a-real-hash', 'hist.col1', '00000000-0000-4000-8000-0000000c0003',
   '00000000-0000-4000-8000-000000000101', '00000000-0000-4000-8000-000000000102',
   '00000000-0000-4000-8000-0000000c0101'),
  ('00000000-0000-4000-8000-0000000c0304', 'hist.col2@test.invalid', 'Hist', 'Collector2', '0500000304',
   'not-a-real-hash', 'hist.col2', '00000000-0000-4000-8000-0000000c0003',
   '00000000-0000-4000-8000-000000000101', '00000000-0000-4000-8000-000000000102',
   '00000000-0000-4000-8000-0000000c0102'),
  ('00000000-0000-4000-8000-0000000c0305', 'hist.ro@test.invalid', 'Hist', 'ReadOnly', '0500000305',
   'not-a-real-hash', 'hist.ro', '00000000-0000-4000-8000-0000000c0004',
   '00000000-0000-4000-8000-000000000101', '00000000-0000-4000-8000-000000000102',
   '00000000-0000-4000-8000-0000000c0101'),
  ('00000000-0000-4000-8000-0000000c0306', 'hist.reviewer2@test.invalid', 'Hist', 'Reviewer2', '0500000306',
   'not-a-real-hash', 'hist.reviewer2', '00000000-0000-4000-8000-0000000c0002',
   '00000000-0000-4000-8000-000000000101', '00000000-0000-4000-8000-000000000102', NULL);

-- the reviewers supervise contract 1 (not contract 2)
INSERT INTO supervisors (id, user_id) VALUES
  ('00000000-0000-4000-8000-0000000c0401', '00000000-0000-4000-8000-0000000c0302'),
  ('00000000-0000-4000-8000-0000000c0402', '00000000-0000-4000-8000-0000000c0306');
INSERT INTO supervisor_contracts (supervisor_id, contract_id) VALUES
  ('00000000-0000-4000-8000-0000000c0401', '00000000-0000-4000-8000-0000000c0101'),
  ('00000000-0000-4000-8000-0000000c0402', '00000000-0000-4000-8000-0000000c0101');

-- lookups -----------------------------------------------------------------------------------
INSERT INTO "ClintContactTypes" (id, name) VALUES
  ('00000000-0000-4000-8000-0000000c0501', 'mobile'),
  ('00000000-0000-4000-8000-0000000c0502', 'email'),
  ('00000000-0000-4000-8000-0000000c0503', 'phone');
INSERT INTO "delinquencyReason" (id, name) VALUES
  ('00000000-0000-4000-8000-0000000c0601', 'Job loss'),
  ('00000000-0000-4000-8000-0000000c0602', 'Dispute');
INSERT INTO "CollectionTypes" (id, name) VALUES ('00000000-0000-4000-8000-0000000c0701', 'Hist partial');
INSERT INTO "CollectionMethods" (id, name) VALUES ('00000000-0000-4000-8000-0000000c0702', 'Hist transfer');

-- cases: H1 and H2 belong to the same debtor (identity 1100000001) -----------------------------
INSERT INTO clints (id, name, "identityNumber", "accountNumber", amount, attribute, user_id, portfolios_id) VALUES
  ('00000000-0000-4000-8000-0000000c1001', 'Hist Case One', '1100000001', 'HIST-0001', 1000.0,
   '{"city": "Riyadh", "bucket": "B1", "score": 7}',
   '00000000-0000-4000-8000-0000000c0303', '00000000-0000-4000-8000-0000000c0201'),
  ('00000000-0000-4000-8000-0000000c1002', 'Hist Case Two', '1100000001', 'HIST-0002', 500.0, '{}',
   '00000000-0000-4000-8000-0000000c0303', '00000000-0000-4000-8000-0000000c0201'),
  ('00000000-0000-4000-8000-0000000c1003', 'Hist Case Three', '1100000003', 'HIST-0003', 750.0, '{}',
   '00000000-0000-4000-8000-0000000c0304', '00000000-0000-4000-8000-0000000c0202'),
  ('00000000-0000-4000-8000-0000000c1004', 'Hist Case Four', '1100000004', 'HIST-0004', 300.0, '{}',
   '00000000-0000-4000-8000-0000000c0305', '00000000-0000-4000-8000-0000000c0201');
