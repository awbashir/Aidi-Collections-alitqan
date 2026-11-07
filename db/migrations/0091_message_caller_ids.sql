-- 0091 message campaigns: several outbound numbers (customer request 2026-10-31, the same day as 0090:
-- "add more than one number, not only one").
--
-- campaigns.message_caller_ids  the numbers the campaign's calls go out with, in rotation (the number
--                               with the fewest calls in this campaign goes next); empty = automatic.
--                               Each one is checked against outbound_numbers when saved.
-- campaigns.message_caller_id   (0090) no longer read; the column stays (additive). Only two stopped test
--                               campaigns ever set it, and campaigns_guard keeps stopped campaigns as they are.
--
-- Catalog-only column with a constant default. Re-runnable.

ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS message_caller_ids text[] NOT NULL DEFAULT '{}';
