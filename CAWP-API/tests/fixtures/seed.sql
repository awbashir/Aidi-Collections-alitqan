-- Minimal fixtures for the pytest suite (loaded after baseline + migrations).
-- Fixed UUIDs so tests can refer to them; see tests/conftest.py (class Ids).
-- Synthetic data only.

INSERT INTO nationalities (id, name) VALUES ('00000000-0000-4000-8000-000000000101', 'Saudi');
INSERT INTO branches (id, name) VALUES ('00000000-0000-4000-8000-000000000102', 'Riyadh HQ');

INSERT INTO roles (id, name, permission) VALUES
  ('00000000-0000-4000-8000-000000000201', 'ADMIN',
   '{"dashbord": true, "clints": true, "roles": true, "clints_Payments": true}'),
  ('00000000-0000-4000-8000-000000000202', 'محصل',
   '{"dashbord": false, "clints": true, "roles": false, "clints_Payments": true}');

INSERT INTO contracts (id, name) VALUES ('00000000-0000-4000-8000-000000000301', 'Test contract');
INSERT INTO portfolios (id, name, contract_id) VALUES
  ('00000000-0000-4000-8000-000000000401', 'Test portfolio', '00000000-0000-4000-8000-000000000301');

INSERT INTO users (id, email, frist_name, last_name, mobile, password, user_name, role_id,
                   nationality_id, branch_id, contract_id) VALUES
  ('00000000-0000-4000-8000-000000000501', 'admin@test.invalid', 'Test', 'Admin', '0500000001',
   'not-a-real-hash', 'test.admin', '00000000-0000-4000-8000-000000000201',
   '00000000-0000-4000-8000-000000000101', '00000000-0000-4000-8000-000000000102', NULL),
  ('00000000-0000-4000-8000-000000000502', 'collector@test.invalid', 'Test', 'Collector', '0500000002',
   'not-a-real-hash', 'test.collector', '00000000-0000-4000-8000-000000000202',
   '00000000-0000-4000-8000-000000000101', '00000000-0000-4000-8000-000000000102',
   '00000000-0000-4000-8000-000000000301');

INSERT INTO clints (id, name, "identityNumber", "accountNumber", amount, attribute, user_id, portfolios_id) VALUES
  ('00000000-0000-4000-8000-000000000601', 'Client One',   '1000000001', 'ACC-0001', 1000.0, '{"city": "Riyadh"}',
   '00000000-0000-4000-8000-000000000502', '00000000-0000-4000-8000-000000000401'),
  ('00000000-0000-4000-8000-000000000602', 'Client Two',   '1000000002', 'ACC-0002', 2500.5, '{}',
   '00000000-0000-4000-8000-000000000502', '00000000-0000-4000-8000-000000000401'),
  ('00000000-0000-4000-8000-000000000603', 'Client Three', '1000000003', 'ACC-0003', 750.0,  '{}',
   NULL, '00000000-0000-4000-8000-000000000401');

INSERT INTO "ContactMethods" (id, name) VALUES ('00000000-0000-4000-8000-000000000701', 'Phone');
INSERT INTO comment_reachability (id, name) VALUES ('00000000-0000-4000-8000-000000000702', 'Reached');
INSERT INTO "CommentStatuses" (id, name, contract_id, "CommentReachability_id", is_active) VALUES
  ('00000000-0000-4000-8000-000000000703', 'Promise', '00000000-0000-4000-8000-000000000301',
   '00000000-0000-4000-8000-000000000702', true);
INSERT INTO "CollectionTypes" (id, name) VALUES ('00000000-0000-4000-8000-000000000704', 'Partial');
INSERT INTO "CollectionMethods" (id, name) VALUES ('00000000-0000-4000-8000-000000000705', 'Transfer');
INSERT INTO "ClintContactTypes" (id, name) VALUES ('00000000-0000-4000-8000-000000000706', 'Mobile');

INSERT INTO "ClintContacts" (id, contact_type_id, "identityNumber", contact_status, contact, is_default) VALUES
  ('00000000-0000-4000-8000-000000000801', '00000000-0000-4000-8000-000000000706', '1000000001',
   'active', '0555000001', true);

INSERT INTO "Comments" (id, clint_id, user_id, "contactMethod_id", comment, comment_status_id, create_at) VALUES
  ('00000000-0000-4000-8000-000000000901', '00000000-0000-4000-8000-000000000601',
   '00000000-0000-4000-8000-000000000502', '00000000-0000-4000-8000-000000000701', 'first note',
   '00000000-0000-4000-8000-000000000703', '2026-09-20T09:00:00Z');

INSERT INTO "Payments" (id, clint_id, user_id, collection_type_id, collection_method_id, amount, payment_date) VALUES
  ('00000000-0000-4000-8000-000000000a01', '00000000-0000-4000-8000-000000000601',
   '00000000-0000-4000-8000-000000000502', '00000000-0000-4000-8000-000000000704',
   '00000000-0000-4000-8000-000000000705', 100.0, '2026-09-21T21:00:00Z');

INSERT INTO "paymentDates" (id, clint_id, user_id, payment_date, amount) VALUES
  ('00000000-0000-4000-8000-000000000b01', '00000000-0000-4000-8000-000000000601',
   '00000000-0000-4000-8000-000000000502', '2026-09-30T21:00:00Z', 200.0);
