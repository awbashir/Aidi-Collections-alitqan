-- Fixtures for the Phase 2 stream A (bulk assignment) tests, loaded once per session by
-- tests/test_bulk_assign_support.py after tests/fixtures/seed.sql and seed_bulk.sql.
-- Synthetic data only. Re-runnable (ON CONFLICT DO NOTHING). Cases are created per test.
--
--   a.super    role "SuperAdmin"        unrestricted scope; clints_sepcifyUser + bulk_jobs_all
--   a.sup      role AssignSupervisor    supervisor of contract A and of user a.col1; clints_sepcifyUser
--   a.sup_nk   role AssignSupNoKey      supervisor of contract A; NO clints_sepcifyUser
--   a.col1     role AssignCollector     collector, users.contract_id = A (also supervised by a.sup)
--   a.col2     role AssignCollector     collector, users.contract_id = B (outside a.sup's scope)
--   a.col3     role AssignCollector     collector, users.contract_id = A
--   a.col4     role AssignCollector     collector, no contract; in a.sup's scope only by owning a
--                                       case in contract A (the can_view_user rule)
--   a.gone     role AssignCollector     inactive (users.is_active = false), contract A
--   a.clerk    role AssignClerk         active, contract A, role WITHOUT collections_currentUser
--   a.ckey     role AssignCollectorKey  a collector who also holds clints_sepcifyUser (own scope only)
--   a.legacy   role AssignLegacyKey     only clints_sepcifyUser (legacy gate tests)
--   a.nokey    role AssignNone          no keys

INSERT INTO roles (id, name, permission) VALUES
  ('a5a5a5a5-0000-4000-8000-000000000201', 'SuperAdmin',
   '{"clints": true, "clints_sepcifyUser": true, "bulk_jobs_all": true, "collections_currentUser": true}'),
  ('a5a5a5a5-0000-4000-8000-000000000202', 'AssignSupervisor',
   '{"clints": true, "clints_sepcifyUser": true, "collections_currentUser": true, "bulk_jobs_all": false}'),
  ('a5a5a5a5-0000-4000-8000-000000000203', 'AssignSupNoKey',
   '{"clints": true, "clints_sepcifyUser": false, "collections_currentUser": true}'),
  ('a5a5a5a5-0000-4000-8000-000000000204', 'AssignCollector',
   '{"clints": true, "collections_currentUser": true, "clints_sepcifyUser": false}'),
  ('a5a5a5a5-0000-4000-8000-000000000205', 'AssignClerk',
   '{"clints": true, "collections_currentUser": false}'),
  ('a5a5a5a5-0000-4000-8000-000000000206', 'AssignCollectorKey',
   '{"clints": true, "collections_currentUser": true, "clints_sepcifyUser": true}'),
  ('a5a5a5a5-0000-4000-8000-000000000207', 'AssignLegacyKey', '{"clints_sepcifyUser": true}'),
  ('a5a5a5a5-0000-4000-8000-000000000208', 'AssignNone', '{}')
ON CONFLICT (id) DO NOTHING;

INSERT INTO contracts (id, name) VALUES
  ('a5a5a5a5-0000-4000-8000-000000000301', 'Assign contract A'),
  ('a5a5a5a5-0000-4000-8000-000000000302', 'Assign contract B')
ON CONFLICT (id) DO NOTHING;

INSERT INTO portfolios (id, name, contract_id) VALUES
  ('a5a5a5a5-0000-4000-8000-000000000401', 'Assign portfolio A', 'a5a5a5a5-0000-4000-8000-000000000301'),
  ('a5a5a5a5-0000-4000-8000-000000000402', 'Assign portfolio B', 'a5a5a5a5-0000-4000-8000-000000000302')
ON CONFLICT (id) DO NOTHING;

INSERT INTO users (id, email, frist_name, last_name, mobile, password, user_name, role_id,
                   nationality_id, branch_id, contract_id, is_active)
SELECT v.id::uuid, v.login || '@test.invalid', 'Assign', v.last, '05000030' || right(v.id, 2), 'not-a-real-hash',
       v.login, v.role::uuid, '00000000-0000-4000-8000-000000000101', '00000000-0000-4000-8000-000000000102',
       v.contract::uuid, v.active
  FROM (VALUES
    ('a5a5a5a5-0000-4000-8000-000000000501', 'a.super',  'Super',      'a5a5a5a5-0000-4000-8000-000000000201', NULL, true),
    ('a5a5a5a5-0000-4000-8000-000000000502', 'a.sup',    'Supervisor', 'a5a5a5a5-0000-4000-8000-000000000202', NULL, true),
    ('a5a5a5a5-0000-4000-8000-000000000503', 'a.sup_nk', 'SupNoKey',   'a5a5a5a5-0000-4000-8000-000000000203', NULL, true),
    ('a5a5a5a5-0000-4000-8000-000000000504', 'a.col1',   'Collector1', 'a5a5a5a5-0000-4000-8000-000000000204',
     'a5a5a5a5-0000-4000-8000-000000000301', true),
    ('a5a5a5a5-0000-4000-8000-000000000505', 'a.col2',   'Collector2', 'a5a5a5a5-0000-4000-8000-000000000204',
     'a5a5a5a5-0000-4000-8000-000000000302', true),
    ('a5a5a5a5-0000-4000-8000-000000000506', 'a.col3',   'Collector3', 'a5a5a5a5-0000-4000-8000-000000000204',
     'a5a5a5a5-0000-4000-8000-000000000301', true),
    ('a5a5a5a5-0000-4000-8000-000000000507', 'a.col4',   'Collector4', 'a5a5a5a5-0000-4000-8000-000000000204', NULL, true),
    ('a5a5a5a5-0000-4000-8000-000000000508', 'a.gone',   'Inactive',   'a5a5a5a5-0000-4000-8000-000000000204',
     'a5a5a5a5-0000-4000-8000-000000000301', false),
    ('a5a5a5a5-0000-4000-8000-000000000509', 'a.clerk',  'Clerk',      'a5a5a5a5-0000-4000-8000-000000000205',
     'a5a5a5a5-0000-4000-8000-000000000301', true),
    ('a5a5a5a5-0000-4000-8000-000000000510', 'a.ckey',   'CollKey',    'a5a5a5a5-0000-4000-8000-000000000206',
     'a5a5a5a5-0000-4000-8000-000000000301', true),
    ('a5a5a5a5-0000-4000-8000-000000000511', 'a.legacy', 'LegacyKey',  'a5a5a5a5-0000-4000-8000-000000000207', NULL, true),
    ('a5a5a5a5-0000-4000-8000-000000000512', 'a.nokey',  'NoKey',      'a5a5a5a5-0000-4000-8000-000000000208', NULL, true)
  ) AS v(id, login, last, role, contract, active)
ON CONFLICT (id) DO NOTHING;

INSERT INTO supervisors (id, user_id) VALUES
  ('a5a5a5a5-0000-4000-8000-000000000601', 'a5a5a5a5-0000-4000-8000-000000000502'),
  ('a5a5a5a5-0000-4000-8000-000000000602', 'a5a5a5a5-0000-4000-8000-000000000503')
ON CONFLICT (id) DO NOTHING;
INSERT INTO supervisor_contracts (id, supervisor_id, contract_id) VALUES
  ('a5a5a5a5-0000-4000-8000-000000000611', 'a5a5a5a5-0000-4000-8000-000000000601',
   'a5a5a5a5-0000-4000-8000-000000000301'),
  ('a5a5a5a5-0000-4000-8000-000000000612', 'a5a5a5a5-0000-4000-8000-000000000602',
   'a5a5a5a5-0000-4000-8000-000000000301')
ON CONFLICT (id) DO NOTHING;
INSERT INTO supervisor_users (id, supervisor_id, user_id) VALUES
  ('a5a5a5a5-0000-4000-8000-000000000621', 'a5a5a5a5-0000-4000-8000-000000000601',
   'a5a5a5a5-0000-4000-8000-000000000504')
ON CONFLICT (id) DO NOTHING;

-- a.col4 owns one (archived) case in contract A: that is what puts them in a.sup's scope
INSERT INTO clints (id, name, "identityNumber", "accountNumber", amount, attribute, user_id, portfolios_id, is_archive)
VALUES ('a5a5a5a5-0000-4000-8000-000000000701', 'Assign col4 anchor', '1555000701', 'ASG-ANCHOR-701', 10, '{}',
        'a5a5a5a5-0000-4000-8000-000000000507', 'a5a5a5a5-0000-4000-8000-000000000401', true)
ON CONFLICT (id) DO NOTHING;
