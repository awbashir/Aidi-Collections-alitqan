-- Phase 6 Q1 (AI drafts) fixtures, loaded once by tests/test_ai_drafts_helpers.py after seed.sql and
-- seed_ai.sql. Synthetic data only. Fixed UUIDs in the ...0000000af... range.
--
--   users     af501 سارة القحطاني (ai_assist, collector)   af502 Omar Nasser (ai_assist, collector)
--             af503 Huda Supervisor (ai_assist, supervises af501: sees af501's cases, not af502's)
--   cases     af601 (af501; notes, payments, promise, plan, follow-up, calls)  af602 (af502: out of af501's scope)
--             af603 (af501, locked)  af604 (af501, archived)
--   calls     af801 manual on af601 by af501 (wrap-up + transcript)
--             af802 click-to-call on af601 by af502 (bridged, transcript, no wrap-up)
--             af803 manual on af602 by af502 (out of af501's scope)
--             af804 manual on af601 by af501 (wrap-up, no transcript)

INSERT INTO users (id, email, frist_name, last_name, mobile, password, user_name, role_id,
                   nationality_id, branch_id) VALUES
  ('00000000-0000-4000-8000-0000000af501', 'ai1.sara@test.invalid', 'سارة', 'القحطاني', '0500000451',
   'not-a-real-hash', 'ai1.sara', '00000000-0000-4000-8000-0000000ae202',
   '00000000-0000-4000-8000-000000000101', '00000000-0000-4000-8000-000000000102'),
  ('00000000-0000-4000-8000-0000000af502', 'ai1.omar@test.invalid', 'Omar', 'Nasser', '0500000452',
   'not-a-real-hash', 'ai1.omar', '00000000-0000-4000-8000-0000000ae202',
   '00000000-0000-4000-8000-000000000101', '00000000-0000-4000-8000-000000000102'),
  ('00000000-0000-4000-8000-0000000af503', 'ai1.huda@test.invalid', 'Huda', 'Supervisor', '0500000453',
   'not-a-real-hash', 'ai1.huda', '00000000-0000-4000-8000-0000000ae202',
   '00000000-0000-4000-8000-000000000101', '00000000-0000-4000-8000-000000000102');
INSERT INTO supervisors (id, user_id) VALUES
  ('00000000-0000-4000-8000-0000000af551', '00000000-0000-4000-8000-0000000af503');
INSERT INTO supervisor_users (supervisor_id, user_id) VALUES
  ('00000000-0000-4000-8000-0000000af551', '00000000-0000-4000-8000-0000000af501');

-- a status of another contract (the save-as-note contract check)
INSERT INTO contracts (id, name) VALUES ('00000000-0000-4000-8000-0000000af301', 'AI1 other contract');
INSERT INTO "CommentStatuses" (id, name, contract_id, "CommentReachability_id", is_active) VALUES
  ('00000000-0000-4000-8000-0000000af703', 'AI1 other-contract status', '00000000-0000-4000-8000-0000000af301',
   '00000000-0000-4000-8000-000000000702', true);

INSERT INTO clints (id, name, "identityNumber", "accountNumber", amount, attribute, user_id, portfolios_id,
                    is_locked, is_archive) VALUES
  ('00000000-0000-4000-8000-0000000af601', 'محمد عبد الله السبيعي', '1098765432', 'ACC-5566778899', 12500.0, '{}',
   '00000000-0000-4000-8000-0000000af501', '00000000-0000-4000-8000-000000000401', false, false),
  ('00000000-0000-4000-8000-0000000af602', 'Khalid Al-Otaibi', '1087654321', 'ACC-1122334455', 8000.0, '{}',
   '00000000-0000-4000-8000-0000000af502', '00000000-0000-4000-8000-000000000401', false, false),
  ('00000000-0000-4000-8000-0000000af603', 'Locked Debtor', '1076543210', 'ACC-2233445566', 500.0, '{}',
   '00000000-0000-4000-8000-0000000af501', '00000000-0000-4000-8000-000000000401', true, false),
  ('00000000-0000-4000-8000-0000000af604', 'Archived Debtor', '1065432109', 'ACC-3344556677', 900.0, '{}',
   '00000000-0000-4000-8000-0000000af501', '00000000-0000-4000-8000-000000000401', false, true);

INSERT INTO "ClintContacts" (id, contact_type_id, "identityNumber", contact_status, contact, is_default) VALUES
  ('00000000-0000-4000-8000-0000000af901', '00000000-0000-4000-8000-000000000706', '1098765432', 'active',
   '0551234567', true),
  ('00000000-0000-4000-8000-0000000af902', '00000000-0000-4000-8000-000000000706', '1098765432', 'active',
   '966501112233', false),
  ('00000000-0000-4000-8000-0000000af903', '00000000-0000-4000-8000-000000000706', '1087654321', 'active',
   '0567778899', true);

-- notes on af601 (oldest first); they mention identifiers and an employee
INSERT INTO "Comments" (id, clint_id, user_id, "contactMethod_id", comment, comment_status_id, create_at) VALUES
  ('00000000-0000-4000-8000-0000000afa01', '00000000-0000-4000-8000-0000000af601',
   '00000000-0000-4000-8000-0000000af501', '00000000-0000-4000-8000-000000000701',
   'اتصلت على 0551234567 وتحدثت مع محمد السبيعي، طلب مهلة حتى نهاية الشهر',
   '00000000-0000-4000-8000-000000000703', '2026-09-01T07:00:00Z'),
  ('00000000-0000-4000-8000-0000000afa02', '00000000-0000-4000-8000-0000000af601',
   '00000000-0000-4000-8000-0000000af502', '00000000-0000-4000-8000-000000000701',
   'NOTE-MARKER-2: Omar Nasser covered the case; the debtor asked for a call after salary day.',
   '00000000-0000-4000-8000-000000000703', '2026-09-10T08:30:00Z'),
  ('00000000-0000-4000-8000-0000000afa03', '00000000-0000-4000-8000-0000000af601',
   '00000000-0000-4000-8000-0000000af501', '00000000-0000-4000-8000-000000000701',
   'wrap-up note: the debtor confirmed the identity number 1098765432 and will pay 2000 after salary',
   '00000000-0000-4000-8000-000000000703', '2026-09-20T09:15:00Z'),
  ('00000000-0000-4000-8000-0000000afa04', '00000000-0000-4000-8000-0000000af602',
   '00000000-0000-4000-8000-0000000af502', '00000000-0000-4000-8000-000000000701',
   'out-of-scope note', '00000000-0000-4000-8000-000000000703', '2026-09-15T08:00:00Z'),
  ('00000000-0000-4000-8000-0000000afa05', '00000000-0000-4000-8000-0000000af601',
   '00000000-0000-4000-8000-0000000af501', '00000000-0000-4000-8000-000000000701',
   'short wrap-up: no answer after two rings',
   '00000000-0000-4000-8000-000000000703', '2026-09-21T09:15:00Z');

-- money on af601: one approved and one pending payment, an overdue promise, then a plan
INSERT INTO "Payments" (id, clint_id, user_id, collection_type_id, collection_method_id, amount, payment_date,
                        approval_status) VALUES
  ('00000000-0000-4000-8000-0000000afb01', '00000000-0000-4000-8000-0000000af601',
   '00000000-0000-4000-8000-0000000af501', '00000000-0000-4000-8000-000000000704',
   '00000000-0000-4000-8000-000000000705', 1000.0, '2026-08-15T09:00:00Z', true),
  ('00000000-0000-4000-8000-0000000afb02', '00000000-0000-4000-8000-0000000af601',
   '00000000-0000-4000-8000-0000000af501', '00000000-0000-4000-8000-000000000704',
   '00000000-0000-4000-8000-000000000705', 500.0, '2026-09-18T09:00:00Z', NULL);

INSERT INTO "paymentDates" (id, clint_id, user_id, payment_date, amount) VALUES
  ('00000000-0000-4000-8000-0000000afc01', '00000000-0000-4000-8000-0000000af601',
   '00000000-0000-4000-8000-0000000af501', '2026-09-09T21:00:00Z', 2000.0);

INSERT INTO repayment_plans (id, case_id, frequency, start_date, total_amount, installment_count, split_mode,
                             outstanding_at_creation, note, created_by) VALUES
  ('00000000-0000-4000-8000-0000000afd01', '00000000-0000-4000-8000-0000000af601', 'monthly', '2026-08-01',
   3000.00, 3, 'count', 11500.00, 'AI1 test plan', '00000000-0000-4000-8000-0000000af501');
INSERT INTO plan_installments (plan_id, case_id, seq, due_date, amount) VALUES
  ('00000000-0000-4000-8000-0000000afd01', '00000000-0000-4000-8000-0000000af601', 1, '2026-08-01', 1000.00),
  ('00000000-0000-4000-8000-0000000afd01', '00000000-0000-4000-8000-0000000af601', 2, '2026-09-01', 1000.00),
  ('00000000-0000-4000-8000-0000000afd01', '00000000-0000-4000-8000-0000000af601', 3, '2026-10-01', 1000.00);

INSERT INTO follow_ups (id, case_id, owner_user_id, due_at, original_due_at, next_action, status, source, created_by)
VALUES ('00000000-0000-4000-8000-0000000afe01', '00000000-0000-4000-8000-0000000af601',
        '00000000-0000-4000-8000-0000000af501', '2035-09-28T06:00:00Z', '2035-09-28T06:00:00Z',
        'FOLLOWUP-MARKER: call after salary day', 'open', 'manual', '00000000-0000-4000-8000-0000000af501');

-- calls
INSERT INTO call_attempts (id, mode, direction, state, case_id, contact_id, destination, collector_user_id, created_by,
                           created_at, started_at, ended_at, duration_seconds, duration_source, bridged_at, transcript)
VALUES
  ('00000000-0000-4000-8000-0000000af801', 'manual', 'outbound', 'ended', '00000000-0000-4000-8000-0000000af601',
   '00000000-0000-4000-8000-0000000af901', '0551234567', '00000000-0000-4000-8000-0000000af501',
   '00000000-0000-4000-8000-0000000af501', '2026-09-20T09:00:00Z', '2026-09-20T09:00:00Z', '2026-09-20T09:03:00Z',
   180, 'reported', NULL,
   E'Collector: السلام عليكم، معك سارة من شركة التحصيل. هل أتحدث مع محمد عبد الله السبيعي؟\n' ||
   E'Debtor: نعم، أنا محمد.\n' ||
   E'Collector: للتحقق، أرجو تأكيد رقم الهوية.\n' ||
   E'Debtor: 1098765432. وإذا لم أرد اتصلي على 0501112233.\n' ||
   E'Collector: المبلغ المتبقي 11500 ريال. هل يمكنك السداد هذا الشهر؟\n' ||
   E'Debtor: I can pay 2000 after salary on the 27th.'),
  ('00000000-0000-4000-8000-0000000af802', 'click_to_call', 'outbound', 'ended', '00000000-0000-4000-8000-0000000af601',
   NULL, NULL, '00000000-0000-4000-8000-0000000af502', '00000000-0000-4000-8000-0000000af502',
   '2026-09-22T10:00:00Z', '2026-09-22T10:00:00Z', '2026-09-22T10:02:00Z', 120, 'pbx', '2026-09-22T10:00:10Z',
   E'Collector: Good morning, this is Omar from the agency.\nDebtor: I already told you I will pay.'),
  ('00000000-0000-4000-8000-0000000af803', 'manual', 'outbound', 'ended', '00000000-0000-4000-8000-0000000af602',
   NULL, NULL, '00000000-0000-4000-8000-0000000af502', '00000000-0000-4000-8000-0000000af502',
   '2026-09-22T11:00:00Z', '2026-09-22T11:00:00Z', NULL, NULL, NULL, NULL, 'out of scope transcript'),
  ('00000000-0000-4000-8000-0000000af804', 'manual', 'outbound', 'ended', '00000000-0000-4000-8000-0000000af601',
   NULL, NULL, '00000000-0000-4000-8000-0000000af501', '00000000-0000-4000-8000-0000000af501',
   '2026-09-21T09:10:00Z', '2026-09-21T09:10:00Z', NULL, NULL, NULL, NULL, NULL);

INSERT INTO call_wrapups (attempt_id, case_id, outcome, note_id, reviewed_by) VALUES
  ('00000000-0000-4000-8000-0000000af801', '00000000-0000-4000-8000-0000000af601', 'connected',
   '00000000-0000-4000-8000-0000000afa03', '00000000-0000-4000-8000-0000000af501'),
  ('00000000-0000-4000-8000-0000000af804', '00000000-0000-4000-8000-0000000af601', 'no_answer',
   '00000000-0000-4000-8000-0000000afa05', '00000000-0000-4000-8000-0000000af501');
