-- Phase 6 Q0 (AI foundation) fixtures, loaded once by tests/test_ai_helpers.py after the shared
-- seed.sql. Synthetic data only. Fixed UUIDs in the ...0000000ae... range.

INSERT INTO roles (id, name, permission) VALUES
  ('00000000-0000-4000-8000-0000000ae201', 'ai admin',
   '{"ai_admin": true, "ai_assist": true, "quality_review": false, "quality_approve": false, "voice_lab": false}'),
  ('00000000-0000-4000-8000-0000000ae202', 'ai assist',
   '{"ai_admin": false, "ai_assist": true, "quality_review": false, "quality_approve": false, "voice_lab": false}'),
  ('00000000-0000-4000-8000-0000000ae203', 'ai quality reviewer',
   '{"ai_admin": false, "ai_assist": false, "quality_review": true, "quality_approve": false, "voice_lab": false}'),
  ('00000000-0000-4000-8000-0000000ae204', 'ai none',
   '{"ai_admin": false, "ai_assist": false, "quality_review": false, "quality_approve": false, "voice_lab": false, "clints": true}');

INSERT INTO users (id, email, frist_name, last_name, mobile, password, user_name, role_id,
                   nationality_id, branch_id) VALUES
  ('00000000-0000-4000-8000-0000000ae501', 'ai.admin@test.invalid', 'AI', 'Admin', '0500000401',
   'not-a-real-hash', 'ai.admin', '00000000-0000-4000-8000-0000000ae201',
   '00000000-0000-4000-8000-000000000101', '00000000-0000-4000-8000-000000000102'),
  ('00000000-0000-4000-8000-0000000ae502', 'ai.assist@test.invalid', 'AI', 'Assist', '0500000402',
   'not-a-real-hash', 'ai.assist', '00000000-0000-4000-8000-0000000ae202',
   '00000000-0000-4000-8000-000000000101', '00000000-0000-4000-8000-000000000102'),
  ('00000000-0000-4000-8000-0000000ae503', 'ai.quality@test.invalid', 'AI', 'Quality', '0500000403',
   'not-a-real-hash', 'ai.quality', '00000000-0000-4000-8000-0000000ae203',
   '00000000-0000-4000-8000-000000000101', '00000000-0000-4000-8000-000000000102'),
  ('00000000-0000-4000-8000-0000000ae504', 'ai.none@test.invalid', 'AI', 'None', '0500000404',
   'not-a-real-hash', 'ai.none', '00000000-0000-4000-8000-0000000ae204',
   '00000000-0000-4000-8000-000000000101', '00000000-0000-4000-8000-000000000102');
