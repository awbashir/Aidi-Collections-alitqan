-- Synthetic CDR rows for the local fixture (Phase 4, stream R-C). No real call data.
--
-- The numbers are the synthetic contacts of the ZZ-TEST cases on the local cawp-db copy
-- (ZZTEST-P1-0001..0004: 0551230001/02/03/04/05/06, 966551239902) and of the pytest fixture
-- CAWP-API/tests/fixtures/seed_reports_c.sql, so the same rows attribute to cases in both.
-- 0559999xxx / 0551239998 match no case.
--
-- Extensions: 7101 (pytest rc.col1), 7102 (rc.col2), 7103 (shared by rc.col3 and rc.col4:
-- an ambiguous agent), 7199 (nobody).
--
-- The fixed day is 2026-09-20, PBX local time (Asia/Riyadh). It covers the Riyadh day and
-- hour bounds (00:00:00, 17:59:59, 18:00:00, the previous and next day), inbound and
-- outbound calls, an ambiguous extension, a debtor with two cases, an archived case, a
-- deleted contact, a "contains but does not end with" number (legacy LIKE matches it, the
-- v2 phone key does not), call-center rows with an UNKNOWN duration (NULL), a call without a
-- recording row and a call with two recording rows. Two rows are dated "today" for GET /cdr.

INSERT INTO asteriskcdrdb.cdr
    (calldate, clid, src, dst, dcontext, channel, dstchannel, lastapp, lastdata, duration, billsec,
     disposition, amaflags, accountcode, uniqueid, recordingfile, cnum)
VALUES
  ('2026-09-20 08:15:00', '"Agent 7101" <7101>', '7101', '0551230001', 'from-internal', 'SIP/7101-00000001', 'SIP/trunk-00000002', 'Dial', 'SIP/trunk/0551230001', 95, 80, 'ANSWERED', 3, '', '1758345300.1', 'out-0551230001-7101-20260920-081500-1758345300.1.wav', '7101'),
  ('2026-09-20 08:40:00', '"Agent 7101" <7101>', '7101', '00966551230002', 'from-internal', 'SIP/7101-00000003', 'SIP/trunk-00000004', 'Dial', 'SIP/trunk/00966551230002', 30, 0, 'NO ANSWER', 3, '', '1758346800.2', '', '7101'),
  ('2026-09-20 09:05:00', '"Agent 7101" <7101>', '7101', '0559999001', 'from-internal', 'SIP/7101-00000005', 'SIP/trunk-00000006', 'Dial', 'SIP/trunk/0559999001', 5, 0, 'BUSY', 3, '', '1758348300.3', '', '7101'),
  ('2026-09-20 09:30:00', '"Agent 7101" <7101>', '7101', '0551230002', 'from-internal', 'SIP/7101-00000007', 'SIP/trunk-00000008', 'Dial', 'SIP/trunk/0551230002', 45, 40, 'ANSWERED', 3, '', '1758349800.17', 'out-0551230002-7101-20260920-093000-1758349800.17.wav', '7101'),
  ('2026-09-20 10:30:00', '"Agent 7102" <7102>', '7102', '966551239902', 'from-internal', 'SIP/7102-00000009', 'SIP/trunk-00000010', 'Dial', 'SIP/trunk/966551239902', 200, 185, 'ANSWERED', 3, '', '1758353400.4', 'out-966551239902-7102-20260920-103000-1758353400.4.wav', '7102'),
  ('2026-09-20 11:00:00', '"Agent 7101" <7101>', '7101', '0551230004', 'from-internal', 'SIP/7101-00000011', 'SIP/trunk-00000012', 'Dial', 'SIP/trunk/0551230004', 60, 50, 'ANSWERED', 3, '', '1758355200.5', 'out-0551230004-7101-20260920-110000-1758355200.5.wav', '7101'),
  ('2026-09-20 12:00:00', '"0551230003" <0551230003>', '0551230003', '7101', 'from-trunk', 'SIP/trunk-00000013', 'SIP/7101-00000014', 'Dial', 'SIP/7101', 40, 35, 'ANSWERED', 3, '', '1758358800.11', 'in-7101-0551230003-20260920-120000-1758358800.11.wav', '0551230003'),
  ('2026-09-20 13:00:00', '"Shared 7103" <7103>', '7103', '0551230003', 'from-internal', 'SIP/7103-00000015', 'SIP/trunk-00000016', 'Dial', 'SIP/trunk/0551230003', 20, 15, 'ANSWERED', 3, '', '1758362400.12', '', '7103'),
  ('2026-09-20 14:00:00', '"Agent 7101" <7101>', '7101', '0551230005', 'from-internal', 'SIP/7101-00000017', 'SIP/trunk-00000018', 'Dial', 'SIP/trunk/0551230005', 30, 20, 'ANSWERED', 3, '', '1758366000.13', '', '7101'),
  ('2026-09-20 15:00:00', '"Agent 7101" <7101>', '7101', '0551230006', 'from-internal', 'SIP/7101-00000019', 'SIP/trunk-00000020', 'Dial', 'SIP/trunk/0551230006', 30, 25, 'ANSWERED', 3, '', '1758369600.14', '', '7101'),
  ('2026-09-20 16:00:00', '"Nobody" <7199>', '7199', '0551239998', 'from-internal', 'SIP/7199-00000021', 'SIP/trunk-00000022', 'Dial', 'SIP/trunk/0551239998', 25, 0, 'NO ANSWER', 3, '', '1758373200.15', '', '7199'),
  ('2026-09-20 16:30:00', '"Agent 7101" <7101>', '7101', '05512300011', 'from-internal', 'SIP/7101-00000023', 'SIP/trunk-00000024', 'Dial', 'SIP/trunk/05512300011', 20, 0, 'NO ANSWER', 3, '', '1758375000.16', '', '7101'),
  ('2026-09-20 17:59:59', '"Agent 7101" <7101>', '7101', '0551230003', 'from-internal', 'SIP/7101-00000025', 'SIP/trunk-00000026', 'Dial', 'SIP/trunk/0551230003', 10, 5, 'ANSWERED', 3, '', '1758380399.6', '', '7101'),
  ('2026-09-20 18:00:00', '"Agent 7101" <7101>', '7101', '0551230003', 'from-internal', 'SIP/7101-00000027', 'SIP/trunk-00000028', 'Dial', 'SIP/trunk/0551230003', 0, 0, 'FAILED', 3, '', '1758380400.7', '', '7101'),
  ('2026-09-20 00:00:00', '"Agent 7101" <7101>', '7101', '0551230001', 'from-internal', 'SIP/7101-00000029', 'SIP/trunk-00000030', 'Dial', 'SIP/trunk/0551230001', 15, 10, 'ANSWERED', 3, '', '1758315600.8', '', '7101'),
  ('2026-09-19 23:59:59', '"Agent 7101" <7101>', '7101', '0551230001', 'from-internal', 'SIP/7101-00000031', 'SIP/trunk-00000032', 'Dial', 'SIP/trunk/0551230001', 12, 8, 'ANSWERED', 3, '', '1758315599.9', '', '7101'),
  ('2026-09-21 00:00:00', '"Agent 7101" <7101>', '7101', '0551230001', 'from-internal', 'SIP/7101-00000033', 'SIP/trunk-00000034', 'Dial', 'SIP/trunk/0551230001', 14, 9, 'ANSWERED', 3, '', '1758402000.10', '', '7101'),
  -- "today" (container date, Asia/Riyadh) for GET /cdr (Issable_db.GetSubscription)
  (CURDATE() + INTERVAL 9 HOUR, '"Agent 7101" <7101>', '7101', '0551230001', 'from-internal', 'SIP/7101-00000035', 'SIP/trunk-00000036', 'Dial', 'SIP/trunk/0551230001', 33, 30, 'ANSWERED', 3, '', 'today.1', '', '7101'),
  (CURDATE() + INTERVAL 10 HOUR, '"Agent 7102" <7102>', '7102', '0559999003', 'from-internal', 'SIP/7102-00000037', 'SIP/trunk-00000038', 'Dial', 'SIP/trunk/0559999003', 20, 0, 'NO ANSWER', 3, '', 'today.2', '', '7102');

INSERT INTO call_center.agent (id, type, number, name, password) VALUES
  (1, 'Agent', '7101', 'Agent 7101', 'x'),
  (2, 'Agent', '7102', 'Agent 7102', 'x');

INSERT INTO call_center.calls (id, id_campaign, phone, status, uniqueid, fecha_llamada, start_time, end_time, duration, id_agent) VALUES
  (1, 1, '0551230001', 'Success', '1758348000.101', '2026-09-20 09:00:00', '2026-09-20 09:00:00', '2026-09-20 09:02:00', 120, 1),
  -- unknown duration and status (a call whose outcome the call-center never wrote)
  (2, 1, '0551230003', NULL,      '1758353400.102', '2026-09-20 10:30:00', '2026-09-20 10:30:00', NULL, NULL, 1),
  -- unknown duration and no recording row (the legacy INNER JOIN drops it)
  (3, 1, '0551230004', 'Failure', '1758358200.103', '2026-09-20 11:50:00', '2026-09-20 11:50:00', NULL, NULL, 2),
  (4, 1, '0559999002', 'Success', '1758362400.104', '2026-09-20 13:00:00', '2026-09-20 13:00:00', '2026-09-20 13:00:45', 45, 1),
  -- two recording rows (the legacy join lists the call twice)
  (5, 1, '0551230001', 'Success', '1758366000.105', '2026-09-20 14:00:00', '2026-09-20 14:00:00', '2026-09-20 14:01:00', 60, 1);

INSERT INTO call_center.call_recording (id, datetime_entry, id_call_outgoing, uniqueid, recordingfile) VALUES
  (1, '2026-09-20 09:02:00', 1, '1758348000.101', '2026/09/20/q-7101-0551230001-20260920-090000-1758348000.101.wav'),
  (2, '2026-09-20 10:31:00', 2, '1758353400.102', '2026/09/20/q-7101-0551230003-20260920-103000-1758353400.102.wav'),
  (3, '2026-09-20 13:00:45', 4, '1758362400.104', '2026/09/20/q-7101-0559999002-20260920-130000-1758362400.104.wav'),
  (4, '2026-09-20 14:01:00', 5, '1758366000.105', '2026/09/20/q-7101-0551230001-20260920-140000-1758366000.105-a.wav'),
  (5, '2026-09-20 14:01:01', 5, '1758366000.105', '2026/09/20/q-7101-0551230001-20260920-140000-1758366000.105-b.wav');
