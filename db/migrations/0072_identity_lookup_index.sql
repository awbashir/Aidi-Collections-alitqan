-- migrate:no-transaction
-- 0072 identity-key index for "other accounts of the same debtor" on the case page
-- (customer request 2026-10-17; GET /v2/cases/<id>/same-identity, services/same_identity.py).
--
-- Two accounts belong to the same debtor when their identity KEY is equal. The key is written
-- out in full here, so that the lookup (the constant `IDENTITY_KEY_SQL` in
-- services/same_identity.py, applied to clints."identityNumber") matches it exactly; the
-- tests check that the two stay identical and that the planner uses this index:
--   1. Arabic-Indic (U+0660-0669) and Extended Arabic-Indic (U+06F0-06F9) digits -> ASCII;
--   2. every whitespace character removed: ASCII whitespace, NBSP, the Unicode spaces
--      (U+1680, U+2000-U+200A, U+202F, U+205F, U+3000, line/paragraph separators) and the
--      zero-width / bidi marks pasted Arabic text carries (U+200B-U+200F, U+202A-U+202E,
--      U+2060-U+206F, BOM);
--   3. upper case (an iqama/passport number with letters matches whatever case was typed);
--   4. an empty result is NULL, so a blank identity never matches another blank identity.
-- Nothing else is folded: leading zeros and punctuation stay significant (no silent merge of
-- different debtors, docs/PROJECT-NOTES.md §2.2).
--
-- Not partial: archived accounts are listed too when the viewer asks for them. 23k rows on
-- a-alaoula, ~1M on other installs; the lookup is one index probe instead of a scan.
-- Write cost: one expression evaluation (translate + regexp_replace, microseconds) and one
-- index insertion per inserted clints row or identity change.
-- The endpoint works without this index (one sequential scan of clints per request).
--
-- CREATE INDEX CONCURRENTLY: no lock that blocks reads or writes; it must run outside a
-- transaction (hence the header) and is idempotent (IF NOT EXISTS). If a build is
-- interrupted it leaves an INVALID index: drop it (DROP INDEX CONCURRENTLY) and re-run.

CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_clints_identity_key
    ON clints ((nullif(upper(regexp_replace(translate("identityNumber", '٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹', '01234567890123456789'), E'[\\s\u00a0\u1680\u2000-\u200f\u2028-\u202f\u205f-\u206f\u3000\ufeff]', '', 'g')), '')));
