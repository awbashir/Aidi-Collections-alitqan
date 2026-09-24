-- Stream A (Phase 3, internal requests) fixtures, loaded once by tests/test_requests_helpers.py
-- after the shared seed.sql. Synthetic data only. Fixed UUIDs in the ...0000000e... range.
-- Users, supervisors, contracts, cases and documents are created per test by the helpers, so
-- assertions never depend on other tests' rows. Only the roles and one SuperAdmin are fixed.

INSERT INTO roles (id, name, permission) VALUES
  ('00000000-0000-4000-8000-0000000e0201', 'SuperAdmin',
   '{"clints": true, "requests": true, "requests_legal": true, "requests_finance": true, "requests_operations": true}'),
  ('00000000-0000-4000-8000-0000000e0202', 'rq supervisor',
   '{"clints": true, "requests": true, "requests_legal": false, "requests_finance": false, "requests_operations": false}'),
  ('00000000-0000-4000-8000-0000000e0203', 'rq collector',
   '{"clints": true, "requests": true, "requests_legal": false, "requests_finance": false, "requests_operations": false}'),
  ('00000000-0000-4000-8000-0000000e0204', 'rq legal reviewer (no base key)',
   '{"clints": true, "requests": false, "requests_legal": true, "requests_finance": false, "requests_operations": false}'),
  ('00000000-0000-4000-8000-0000000e0205', 'rq finance reviewer',
   '{"clints": true, "requests": false, "requests_legal": false, "requests_finance": true, "requests_operations": false}'),
  ('00000000-0000-4000-8000-0000000e0206', 'rq operations reviewer',
   '{"clints": true, "requests": false, "requests_legal": false, "requests_finance": false, "requests_operations": true}'),
  ('00000000-0000-4000-8000-0000000e0207', 'rq no access',
   '{"clints": true, "requests": false, "requests_legal": false, "requests_finance": false, "requests_operations": false}'),
  ('00000000-0000-4000-8000-0000000e0208', 'rq legal + base',
   '{"clints": true, "requests": true, "requests_legal": true, "requests_finance": false, "requests_operations": false}');

INSERT INTO users (id, email, frist_name, last_name, mobile, password, user_name, role_id,
                   nationality_id, branch_id) VALUES
  ('00000000-0000-4000-8000-0000000e0501', 'rq.super@test.invalid', 'RQ', 'Super', '0500000201',
   'not-a-real-hash', 'rq.super', '00000000-0000-4000-8000-0000000e0201',
   '00000000-0000-4000-8000-000000000101', '00000000-0000-4000-8000-000000000102');
