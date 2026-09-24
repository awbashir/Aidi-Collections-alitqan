-- migrate:no-transaction
-- 0046 phone-key index for the CDR attribution of the v2 call reports (Phase 4, stream R-C:
-- RPT-23 calls mode and RPT-25; services/reports_v2_cdr.py).
--
-- The legacy case call-records screen (`GET /cdr_by_date`, issable/db_connection.py
-- `_mobile_like_clause`) matches a CDR row to a case when the src/dst of the call contains
-- the LAST 9 DIGITS of one of the case debtor's contacts. The v2 reports attribute in the
-- other direction (CDR row -> contacts -> cases -> current owner): the last 9 digits of the
-- CDR src/dst are looked up among the contacts' last 9 digits. This index makes that lookup
-- an index probe; "ClintContacts" has 1.8M rows on cawp-db and no index on `contact`.
--
-- The indexed expression is the phone key, written out in full so that the report SQL (the
-- constant `KEY_SQL` in services/reports_v2_cdr.py) matches it textually:
--     Arabic-Indic / Extended Arabic-Indic digits -> ASCII, keep the digits, take the last 9.
-- A contact with fewer than 9 digits gets a shorter key that never equals a 9-digit CDR key
-- (the report only looks up 9-digit keys), exactly like legacy, which skips such contacts.
-- Unlike 0027 (`cawp_norm_mobile`, Saudi mobiles only) it also covers landlines and
-- foreign numbers, as the legacy LIKE did.
--
-- Partial on live contacts (is_deleted = false): the report only matches live contacts.
-- Write cost: one expression evaluation (translate + regexp_replace, microseconds) and one
-- index insertion per inserted or contact-changing "ClintContacts" row.
--
-- The reports work without this index (one sequential scan of "ClintContacts" per CDR
-- window instead of index probes; see review/phase4-reports-c.md for both timings).
--
-- CREATE INDEX CONCURRENTLY: no lock that blocks reads or writes; it must run outside a
-- transaction (hence the header) and is idempotent (IF NOT EXISTS). If a build is
-- interrupted it leaves an INVALID index: drop it (DROP INDEX CONCURRENTLY) and re-run.

CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_clintcontacts_phone_key
    ON "ClintContacts" (right(regexp_replace(translate(contact, '٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹', '01234567890123456789'), '[^0-9]', '', 'g'), 9))
    WHERE is_deleted = false;
