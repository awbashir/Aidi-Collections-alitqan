-- Phase 5 P2 (progressive campaigns) fixtures, loaded once by tests/test_calling_campaign_helpers.py
-- after seed.sql and seed_calling.sql. Synthetic data only. Fixed UUIDs in the ...0000000cb... range.
-- Users, supervisors, contracts, cases, contacts and campaigns are created per test by the helpers;
-- only the roles (with the campaign keys) and one SuperAdmin are fixed.

INSERT INTO roles (id, name, permission) VALUES
  ('00000000-0000-4000-8000-0000000cb201', 'SuperAdmin',
   '{"clints": true, "calls": true, "calls_make": true, "calls_manage": true, "clints_details": true,
     "promises": true, "campaigns": true, "campaigns_manage": true}'),
  ('00000000-0000-4000-8000-0000000cb202', 'cp supervisor',
   '{"clints": true, "calls": true, "calls_make": true, "calls_manage": true, "clints_details": true,
     "promises": true, "campaigns": true, "campaigns_manage": true}'),
  ('00000000-0000-4000-8000-0000000cb203', 'cp collector',
   '{"clints": true, "calls": true, "calls_make": true, "calls_manage": false, "clints_details": true,
     "promises": true, "campaigns": true, "campaigns_manage": false}'),
  ('00000000-0000-4000-8000-0000000cb204', 'cp collector without calls_make',
   '{"clints": true, "calls": true, "calls_make": false, "calls_manage": false, "clints_details": true,
     "promises": true, "campaigns": true, "campaigns_manage": false}'),
  ('00000000-0000-4000-8000-0000000cb205', 'cp caller without campaigns',
   '{"clints": true, "calls": true, "calls_make": true, "calls_manage": false, "clints_details": true,
     "promises": true, "campaigns": false, "campaigns_manage": false}');

INSERT INTO users (id, email, frist_name, last_name, mobile, password, user_name, role_id,
                   nationality_id, branch_id) VALUES
  ('00000000-0000-4000-8000-0000000cb501', 'cp.super@test.invalid', 'CP', 'Super', '0500000401',
   'not-a-real-hash', 'cp.super', '00000000-0000-4000-8000-0000000cb201',
   '00000000-0000-4000-8000-000000000101', '00000000-0000-4000-8000-000000000102');
