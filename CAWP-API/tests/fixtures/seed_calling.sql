-- Phase 5 P0 (human calling foundation) fixtures, loaded once by tests/test_calling_helpers.py
-- after the shared seed.sql. Synthetic data only. Fixed UUIDs in the ...0000000ca... range.
-- Users, supervisors, contracts, cases and contacts are created per test by the helpers, so
-- assertions never depend on other tests' rows. Only roles, one SuperAdmin and the note
-- lookups are fixed.

INSERT INTO roles (id, name, permission) VALUES
  ('00000000-0000-4000-8000-0000000ca201', 'SuperAdmin',
   '{"clints": true, "calls": true, "calls_make": true, "calls_manage": true, "clints_details": true, "promises": true}'),
  ('00000000-0000-4000-8000-0000000ca202', 'cl supervisor',
   '{"clints": true, "calls": true, "calls_make": true, "calls_manage": true, "clints_details": true, "promises": true}'),
  ('00000000-0000-4000-8000-0000000ca203', 'cl collector',
   '{"clints": true, "calls": true, "calls_make": true, "calls_manage": false, "clints_details": true, "promises": true}'),
  ('00000000-0000-4000-8000-0000000ca204', 'cl no calling',
   '{"clints": true, "calls": false, "calls_make": false, "calls_manage": false, "clints_details": true, "promises": true}'),
  ('00000000-0000-4000-8000-0000000ca205', 'cl collector without promises or numbers',
   '{"clints": true, "calls": true, "calls_make": false, "calls_manage": false, "clints_details": false, "promises": false}');

INSERT INTO users (id, email, frist_name, last_name, mobile, password, user_name, role_id,
                   nationality_id, branch_id) VALUES
  ('00000000-0000-4000-8000-0000000ca501', 'cl.super@test.invalid', 'CL', 'Super', '0500000301',
   'not-a-real-hash', 'cl.super', '00000000-0000-4000-8000-0000000ca201',
   '00000000-0000-4000-8000-000000000101', '00000000-0000-4000-8000-000000000102');

-- note lookups usable on any contract (status without a contract)
INSERT INTO comment_reachability (id, name, is_deleted) VALUES
  ('00000000-0000-4000-8000-0000000ca702', 'CL reached', false),
  ('00000000-0000-4000-8000-0000000ca712', 'CL retired reachability', true);
INSERT INTO "ContactMethods" (id, name) VALUES
  ('00000000-0000-4000-8000-0000000ca701', 'CL phone call');
INSERT INTO "CommentStatuses" (id, name, contract_id, "CommentReachability_id", is_active) VALUES
  ('00000000-0000-4000-8000-0000000ca703', 'CL call outcome', NULL, '00000000-0000-4000-8000-0000000ca702', true),
  ('00000000-0000-4000-8000-0000000ca713', 'CL status of the base contract', '00000000-0000-4000-8000-000000000301',
   '00000000-0000-4000-8000-0000000ca702', true);
