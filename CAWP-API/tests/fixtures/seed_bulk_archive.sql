-- Fixtures for the Phase 2 stream B (bulk archive / restore) tests, loaded once per session by
-- tests/test_bulk_archive_support.py after tests/fixtures/seed.sql and seed_bulk.sql.
-- Synthetic data only. Re-runnable (ON CONFLICT DO NOTHING). Cases are created per test.
--
--   ba.arch   role BA-Archivist    clints_move_archive; supervisor of contract BA-A
--   ba.rest   role BA-Restorer     clints_archive (the archive screen key); supervisor of contract BA-A
--   ba.move   role BA-ArchiveMove  clints_archive_move only; supervisor of contract BA-A
--   ba.col    role BA-Collector    clints_move_archive + clints_archive; collector (own cases only)
--   ba.col2   role BA-Collector    collector, owns the contract BA-B cases
--   ba.none   role BA-None         no keys; supervisor of contract BA-A (scope but no permission)
--
-- Contract BA-A: portfolio BA-PA. Contract BA-B: portfolio BA-PB (outside every BA supervisor's scope).
-- Archive reasons: BA reason 1 / BA reason 2.

INSERT INTO roles (id, name, permission) VALUES
  ('baba0000-0000-4000-8000-000000000201', 'BA-Archivist', '{"clints": true, "clints_move_archive": true}'),
  ('baba0000-0000-4000-8000-000000000202', 'BA-Restorer', '{"clints": true, "clints_archive": true}'),
  ('baba0000-0000-4000-8000-000000000203', 'BA-ArchiveMove', '{"clints": true, "clints_archive_move": true}'),
  ('baba0000-0000-4000-8000-000000000204', 'BA-Collector',
   '{"clints": true, "clints_move_archive": true, "clints_archive": true}'),
  ('baba0000-0000-4000-8000-000000000205', 'BA-None', '{"clints": true}')
ON CONFLICT (id) DO NOTHING;

INSERT INTO contracts (id, name) VALUES
  ('baba0000-0000-4000-8000-000000000301', 'BA contract A'),
  ('baba0000-0000-4000-8000-000000000302', 'BA contract B')
ON CONFLICT (id) DO NOTHING;

INSERT INTO portfolios (id, name, contract_id) VALUES
  ('baba0000-0000-4000-8000-000000000401', 'BA portfolio A', 'baba0000-0000-4000-8000-000000000301'),
  ('baba0000-0000-4000-8000-000000000402', 'BA portfolio B', 'baba0000-0000-4000-8000-000000000302')
ON CONFLICT (id) DO NOTHING;

INSERT INTO "archiveReason" (id, name) VALUES
  ('baba0000-0000-4000-8000-000000000901', 'BA reason 1'),
  ('baba0000-0000-4000-8000-000000000902', 'BA reason 2')
ON CONFLICT (id) DO NOTHING;

INSERT INTO users (id, email, frist_name, last_name, mobile, password, user_name, role_id,
                   nationality_id, branch_id, contract_id) VALUES
  ('baba0000-0000-4000-8000-000000000501', 'ba.arch@test.invalid', 'Archive', 'Archivist', '0500003001',
   'not-a-real-hash', 'ba.arch', 'baba0000-0000-4000-8000-000000000201',
   '00000000-0000-4000-8000-000000000101', '00000000-0000-4000-8000-000000000102', NULL),
  ('baba0000-0000-4000-8000-000000000502', 'ba.rest@test.invalid', 'Archive', 'Restorer', '0500003002',
   'not-a-real-hash', 'ba.rest', 'baba0000-0000-4000-8000-000000000202',
   '00000000-0000-4000-8000-000000000101', '00000000-0000-4000-8000-000000000102', NULL),
  ('baba0000-0000-4000-8000-000000000503', 'ba.move@test.invalid', 'Archive', 'Mover', '0500003003',
   'not-a-real-hash', 'ba.move', 'baba0000-0000-4000-8000-000000000203',
   '00000000-0000-4000-8000-000000000101', '00000000-0000-4000-8000-000000000102', NULL),
  ('baba0000-0000-4000-8000-000000000504', 'ba.col@test.invalid', 'Archive', 'Collector', '0500003004',
   'not-a-real-hash', 'ba.col', 'baba0000-0000-4000-8000-000000000204',
   '00000000-0000-4000-8000-000000000101', '00000000-0000-4000-8000-000000000102',
   'baba0000-0000-4000-8000-000000000301'),
  ('baba0000-0000-4000-8000-000000000505', 'ba.col2@test.invalid', 'Archive', 'Collector2', '0500003005',
   'not-a-real-hash', 'ba.col2', 'baba0000-0000-4000-8000-000000000204',
   '00000000-0000-4000-8000-000000000101', '00000000-0000-4000-8000-000000000102',
   'baba0000-0000-4000-8000-000000000302'),
  ('baba0000-0000-4000-8000-000000000506', 'ba.none@test.invalid', 'Archive', 'None', '0500003006',
   'not-a-real-hash', 'ba.none', 'baba0000-0000-4000-8000-000000000205',
   '00000000-0000-4000-8000-000000000101', '00000000-0000-4000-8000-000000000102', NULL)
ON CONFLICT (id) DO NOTHING;

INSERT INTO supervisors (id, user_id) VALUES
  ('baba0000-0000-4000-8000-000000000601', 'baba0000-0000-4000-8000-000000000501'),
  ('baba0000-0000-4000-8000-000000000602', 'baba0000-0000-4000-8000-000000000502'),
  ('baba0000-0000-4000-8000-000000000603', 'baba0000-0000-4000-8000-000000000503'),
  ('baba0000-0000-4000-8000-000000000606', 'baba0000-0000-4000-8000-000000000506')
ON CONFLICT (id) DO NOTHING;
INSERT INTO supervisor_contracts (id, supervisor_id, contract_id) VALUES
  ('baba0000-0000-4000-8000-000000000611', 'baba0000-0000-4000-8000-000000000601',
   'baba0000-0000-4000-8000-000000000301'),
  ('baba0000-0000-4000-8000-000000000612', 'baba0000-0000-4000-8000-000000000602',
   'baba0000-0000-4000-8000-000000000301'),
  ('baba0000-0000-4000-8000-000000000613', 'baba0000-0000-4000-8000-000000000603',
   'baba0000-0000-4000-8000-000000000301'),
  ('baba0000-0000-4000-8000-000000000616', 'baba0000-0000-4000-8000-000000000606',
   'baba0000-0000-4000-8000-000000000301')
ON CONFLICT (id) DO NOTHING;
