-- Phase 5 P4 additions to the local CDR fixture: records of calls made from the CRM (tagged
-- with their call attempt) and plain records with recordings, for the scoped /cdr* routes,
-- /cdr/recording and the attempt <-> CDR link. Synthetic data only.
--
-- Day: 2026-09-22 (PBX local time, Asia/Riyadh), so the Phase 4 fixture day (2026-09-20) and
-- the two "today" rows used by RPT-23/25 and GET /cdr stay exactly as they were.
--
-- Tagged records are written the way the calling dialplan writes them
-- (pbx-test/conf/extensions_collections.conf; the test PBX's Master.csv shows the same):
--   userfield = cawp:<attempt>, uniqueid = cawp-<attempt> on the agent channel, and the
--   Dial record plus a Return record for the same uniqueid.
-- asteriskcdrdb.cdr.uniqueid is varchar(32) (the Issabel/FreePBX schema), so the 41-character
-- cawp-<uuid> arrives TRUNCATED to 32 characters here, as it would on Issabel.
--
-- Attempts (tests/fixtures/seed_calling_cdr.sql, pytest database):
--   a1c4a001-...-000000000001  case K1 (c4.cola, contract P4-A), extension 7301, recording
--   a2c4a002-...-000000000002  case K3 (c4.colb, contract P4-B), extension 7302, recording
--   a3c4a003-...-000000000003  case K2 (c4.cola): NO userfield, only the truncated uniqueid
--   a4c4a004-...-000000000004  case K6 (c4.colb, contract P4-B) but made by c4.cola (7301)
-- Contacts: K1 0561240001, K2 0561240002, K3 0561240003, K4+K5 (one debtor) 0561240004,
-- K6 0561240006. 0569999777/0569999778 match no case. Extension 7303 belongs to nobody.

INSERT INTO asteriskcdrdb.cdr
    (calldate, clid, src, dst, dcontext, channel, dstchannel, lastapp, lastdata, duration, billsec,
     disposition, amaflags, accountcode, uniqueid, userfield, recordingfile, cnum)
VALUES
  ('2026-09-22 10:00:05', '"" <>', '', '+966561240001', 'collections-human-outbound', 'PJSIP/7301-00000101',
   'PJSIP/cawp-test-trunk-00000102', 'Dial', 'PJSIP/+966561240001@cawp-test-trunk,20', 65, 60, 'ANSWERED', 3, '',
   LEFT('cawp-a1c4a001-0000-4000-8000-000000000001', 32), 'cawp:a1c4a001-0000-4000-8000-000000000001',
   '2026/09/22/cawp-a1c4a001-0000-4000-8000-000000000001.wav', ''),
  ('2026-09-22 10:01:10', '"" <>', '', '+966561240001', 'collections-human-outbound', 'PJSIP/7301-00000101',
   '', 'Return', '', 0, 0, 'ANSWERED', 3, '',
   LEFT('cawp-a1c4a001-0000-4000-8000-000000000001', 32), 'cawp:a1c4a001-0000-4000-8000-000000000001', '', ''),
  ('2026-09-22 11:00:05', '"" <>', '', '+966561240003', 'collections-human-outbound', 'PJSIP/7302-00000103',
   'PJSIP/cawp-test-trunk-00000104', 'Dial', 'PJSIP/+966561240003@cawp-test-trunk,20', 40, 31, 'ANSWERED', 3, '',
   LEFT('cawp-a2c4a002-0000-4000-8000-000000000002', 32), 'cawp:a2c4a002-0000-4000-8000-000000000002',
   '2026/09/22/cawp-a2c4a002-0000-4000-8000-000000000002.wav', ''),
  ('2026-09-22 12:00:05', '"" <>', '', '+966561240002', 'collections-human-outbound', 'PJSIP/7301-00000105',
   'PJSIP/cawp-test-trunk-00000106', 'Dial', 'PJSIP/+966561240002@cawp-test-trunk,20', 22, 12, 'ANSWERED', 3, '',
   LEFT('cawp-a3c4a003-0000-4000-8000-000000000003', 32), '',
   '2026/09/22/cawp-a3c4a003-0000-4000-8000-000000000003.wav', ''),
  ('2026-09-22 13:00:05', '"" <>', '', '+966561240006', 'collections-human-outbound', 'PJSIP/7301-00000107',
   'PJSIP/cawp-test-trunk-00000108', 'Dial', 'PJSIP/+966561240006@cawp-test-trunk,20', 30, 25, 'ANSWERED', 3, '',
   LEFT('cawp-a4c4a004-0000-4000-8000-000000000004', 32), 'cawp:a4c4a004-0000-4000-8000-000000000004',
   '2026/09/22/cawp-a4c4a004-0000-4000-8000-000000000004.wav', ''),
  -- plain (untagged) records with Issabel-style recording names
  ('2026-09-22 14:00:00', '"Agent 7302" <7302>', '7302', '0561240004', 'from-internal', 'SIP/7302-00000109',
   'SIP/trunk-00000110', 'Dial', 'SIP/trunk/0561240004', 50, 44, 'ANSWERED', 3, '', '1790400000.6', '',
   'out-0561240004-7302-20260922-140000-1790400000.6.wav', '7302'),
  ('2026-09-22 15:00:00', '"Agent 7301" <7301>', '7301', '0561240001', 'from-internal', 'SIP/7301-00000111',
   'SIP/trunk-00000112', 'Dial', 'SIP/trunk/0561240001', 35, 30, 'ANSWERED', 3, '', '1790400000.7', '',
   'out-0561240001-7301-20260922-150000-1790400000.7.wav', '7301'),
  ('2026-09-22 16:00:00', '"Agent 7302" <7302>', '7302', '0569999777', 'from-internal', 'SIP/7302-00000113',
   'SIP/trunk-00000114', 'Dial', 'SIP/trunk/0569999777', 25, 20, 'ANSWERED', 3, '', '1790400000.8', '',
   'out-0569999777-7302-20260922-160000-1790400000.8.wav', '7302'),
  ('2026-09-22 16:30:00', '"Nobody" <7303>', '7303', '0569999778', 'from-internal', 'SIP/7303-00000115',
   'SIP/trunk-00000116', 'Dial', 'SIP/trunk/0569999778', 15, 10, 'ANSWERED', 3, '', '1790400000.9', '',
   'out-0569999778-7303-20260922-163000-1790400000.9.wav', '7303');

INSERT INTO call_center.agent (id, type, number, name, password) VALUES
  (3, 'Agent', '7301', 'Agent 7301', 'x');

INSERT INTO call_center.calls (id, id_campaign, phone, status, uniqueid, fecha_llamada, start_time, end_time, duration, id_agent) VALUES
  (101, 1, '0561240002', 'Success', '1790390000.101', '2026-09-22 09:00:00', '2026-09-22 09:00:00', '2026-09-22 09:01:30', 90, 3);

INSERT INTO call_center.call_recording (id, datetime_entry, id_call_outgoing, uniqueid, recordingfile) VALUES
  (101, '2026-09-22 09:01:30', 101, '1790390000.101', '2026/09/22/q-7301-0561240002-20260922-090000-1790390000.101.wav');
