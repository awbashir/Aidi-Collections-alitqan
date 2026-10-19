-- Fixtures for the previous statuses import tests (tests/test_previous_status_import.py), loaded
-- once per session after tests/fixtures/seed.sql. Synthetic data only. Re-runnable
-- (ON CONFLICT DO NOTHING). Cases and per-test sub-statuses are created by the tests.
--
--   ps.admin   role "SuperAdmin"   settings_comments_status_add (the gate) + clints
--   ps.sup     role PsSupervisor   settings_comments_status_add, but NOT the SuperAdmin role
--   ps.nokey   role "SuperAdmin"   without settings_comments_status_add
--
-- Statuses (general = contract NULL):
--   وعد بالسداد PS          live main, reachability PS-reached, sub "تم التواصل PS"
--   يتهرب من الاتصال PS      live main, no reachability, a DELETED sub "واتساب PS"
--   لا يرد PS               DELETED main only (never matched)
--   حالة العقد PS            a main of the PS contract AND a general main with the same name

INSERT INTO roles (id, name, permission) VALUES
  ('e9e9e9e9-0000-4000-8000-000000000201', 'SuperAdmin',
   '{"clints": true, "settings_comments_status": true, "settings_comments_status_add": true}'),
  ('e9e9e9e9-0000-4000-8000-000000000202', 'PsSupervisor',
   '{"clints": true, "settings_comments_status": true, "settings_comments_status_add": true}'),
  ('e9e9e9e9-0000-4000-8000-000000000203', 'SuperAdmin',
   '{"clints": true, "settings_comments_status": true}')
ON CONFLICT (id) DO NOTHING;

INSERT INTO contracts (id, name, is_active) VALUES
  ('e9e9e9e9-0000-4000-8000-000000000301', 'PS contract', true),
  ('e9e9e9e9-0000-4000-8000-000000000302', 'PS other contract', true)
ON CONFLICT (id) DO NOTHING;

INSERT INTO portfolios (id, name, contract_id) VALUES
  ('e9e9e9e9-0000-4000-8000-000000000401', 'PS portfolio', 'e9e9e9e9-0000-4000-8000-000000000301'),
  ('e9e9e9e9-0000-4000-8000-000000000402', 'PS other portfolio', 'e9e9e9e9-0000-4000-8000-000000000302')
ON CONFLICT (id) DO NOTHING;

INSERT INTO users (id, email, frist_name, last_name, mobile, password, user_name, role_id,
                   nationality_id, branch_id, contract_id, is_active) VALUES
  ('e9e9e9e9-0000-4000-8000-000000000501', 'ps.admin@test.invalid', 'PS', 'Admin', '0500009001',
   'not-a-real-hash', 'ps.admin', 'e9e9e9e9-0000-4000-8000-000000000201',
   '00000000-0000-4000-8000-000000000101', '00000000-0000-4000-8000-000000000102', NULL, true),
  ('e9e9e9e9-0000-4000-8000-000000000502', 'ps.sup@test.invalid', 'PS', 'Sup', '0500009002',
   'not-a-real-hash', 'ps.sup', 'e9e9e9e9-0000-4000-8000-000000000202',
   '00000000-0000-4000-8000-000000000101', '00000000-0000-4000-8000-000000000102', NULL, true),
  ('e9e9e9e9-0000-4000-8000-000000000503', 'ps.nokey@test.invalid', 'PS', 'NoKey', '0500009003',
   'not-a-real-hash', 'ps.nokey', 'e9e9e9e9-0000-4000-8000-000000000203',
   '00000000-0000-4000-8000-000000000101', '00000000-0000-4000-8000-000000000102', NULL, true)
ON CONFLICT (id) DO NOTHING;

INSERT INTO comment_reachability (id, name) VALUES
  ('e9e9e9e9-0000-4000-8000-000000000601', 'PS reached')
ON CONFLICT (id) DO NOTHING;

INSERT INTO "CommentStatuses" (id, name, contract_id, "CommentReachability_id", color, is_active,
                               parent_id, deleted_at, create_at) VALUES
  ('e9e9e9e9-0000-4000-8000-000000000701', 'وعد بالسداد PS', NULL,
   'e9e9e9e9-0000-4000-8000-000000000601', '#00FF00', true, NULL, NULL, now() - interval '3 days'),
  ('e9e9e9e9-0000-4000-8000-000000000702', 'تم التواصل PS', NULL,
   'e9e9e9e9-0000-4000-8000-000000000601', '#00FF00', true,
   'e9e9e9e9-0000-4000-8000-000000000701', NULL, now() - interval '3 days'),
  ('e9e9e9e9-0000-4000-8000-000000000703', 'يتهرب من الاتصال PS', NULL, NULL, '#FF0000', false,
   NULL, NULL, now() - interval '3 days'),
  ('e9e9e9e9-0000-4000-8000-000000000704', 'واتساب PS', NULL, NULL, '#FF0000', false,
   'e9e9e9e9-0000-4000-8000-000000000703', now() - interval '1 day', now() - interval '3 days'),
  ('e9e9e9e9-0000-4000-8000-000000000705', 'لا يرد PS', NULL, NULL, '#FF0000', false,
   NULL, now() - interval '1 day', now() - interval '3 days'),
  ('e9e9e9e9-0000-4000-8000-000000000706', 'حالة العقد PS', 'e9e9e9e9-0000-4000-8000-000000000301', NULL,
   '#0000FF', true, NULL, NULL, now() - interval '3 days'),
  ('e9e9e9e9-0000-4000-8000-000000000707', 'حالة العقد PS', NULL, NULL,
   '#0000FF', true, NULL, NULL, now() - interval '3 days')
ON CONFLICT (id) DO NOTHING;
