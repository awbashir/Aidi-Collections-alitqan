-- 0026 mobile-number normaliser for the spreadsheet inquiry (Phase 2, stream D).
--
-- `cawp_norm_mobile(text)`: the canonical Saudi mobile form `05XXXXXXXX` of a contact
-- value, or NULL when the value is not a Saudi mobile. The inquiry kind
-- (services/bulk_inquiry.py, `normalize_mobile`) applies exactly the same rule to the
-- spreadsheet value, and 0027 indexes this expression on "ClintContacts", so a mobile is
-- matched with an index probe whatever format it was stored in.
--
-- Rule (profiled on cawp-db, 1.28M mobile-type contacts: 934,698 `9665XXXXXXXX`,
-- 155,601 `5XXXXXXXX`, 62,936 `+9665XXXXXXXX`, 39,836 `05XXXXXXXX`, ~11.8k with spaces,
-- plus `+966(5)XXXXXXXX`, `+966-...` and a tail of landlines / junk):
--   1. Arabic-Indic (U+0660-0669) and Extended Arabic-Indic (U+06F0-06F9) digits -> ASCII;
--      trim spaces, tabs, line breaks and NBSP.
--   2. The value may only contain digits, spaces, `+ ( ) - .` and `/`; anything else
--      (letters, `@`, ...) -> NULL. So an e-mail address never "contains" a number.
--   3. Keep the digits only, then:
--        05XXXXXXXX                -> itself
--        5XXXXXXXX                 -> 0 || digits
--        [00]9665XXXXXXXX          -> 05XXXXXXXX
--        [00]96605XXXXXXXX         -> 05XXXXXXXX   (international prefix written with the trunk 0)
--        anything else             -> NULL (landlines, short codes, foreign numbers: not matched)
--
-- IMMUTABLE / STRICT / PARALLEL SAFE (only immutable built-ins), so it can back an index.
-- Locks: none (new function only). Instant.

CREATE OR REPLACE FUNCTION cawp_norm_mobile(p_value text) RETURNS text
LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE AS $$
    SELECT CASE
             WHEN d IS NULL THEN NULL
             WHEN d ~ '^05[0-9]{8}$' THEN d
             WHEN d ~ '^5[0-9]{8}$' THEN '0' || d
             WHEN d ~ '^(00)?9665[0-9]{8}$' THEN '0' || right(d, 9)
             WHEN d ~ '^(00)?96605[0-9]{8}$' THEN right(d, 10)
             ELSE NULL
           END
      FROM (SELECT CASE WHEN t ~ '^[0-9+()./ -]+$' THEN regexp_replace(t, '[^0-9]', '', 'g') END AS d
              FROM (SELECT regexp_replace(
                               btrim(translate(p_value, '٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹', '01234567890123456789'),
                                     E' \t\r\n '),
                               E'[\t\r\n ]', ' ', 'g') AS t) a) b
$$;

COMMENT ON FUNCTION cawp_norm_mobile(text) IS
    'Canonical Saudi mobile 05XXXXXXXX of a contact value, or NULL (db/migrations/0026). '
    'Mirrored by services/bulk_inquiry.normalize_mobile; indexed on "ClintContacts" by 0027.';
