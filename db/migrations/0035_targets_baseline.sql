-- 0035 targets baseline (Phase 3, stream C): "imported baseline" history.
--
-- Legacy has no target history, only the current value. One row per existing user and
-- contract is recorded for the Riyadh month in which this migration runs, source
-- 'imported_baseline', revision 1, with the legacy triple and the effective kind/value
-- (cawp_target_kind, 0034). Months before that stay unknown: the v2 API reports them as
-- `known: false`, never as zero.
--
-- Re-runnable: a subject that already has a row for the month is skipped.
-- Locks: reads users / contracts (ACCESS SHARE); ~300 + 15 inserted rows on the full local
-- copy, instant.

INSERT INTO targets (subject_type, subject_id, month, revision, kind, value,
                     raw_amount, raw_percent, raw_is_percentage, source, reason)
SELECT 'user', u.id, cawp_riyadh_month(), 1, k.kind, k.value,
       u.target_amount, u.target_percent, u.target_is_percentage,
       'imported_baseline', 'legacy value at migration 0035'
  FROM users u
 CROSS JOIN LATERAL cawp_target_kind('user', u.target_amount, u.target_percent,
                                     u.target_is_percentage) k
 WHERE NOT EXISTS (SELECT 1 FROM targets t
                    WHERE t.subject_type = 'user' AND t.subject_id = u.id
                      AND t.month = cawp_riyadh_month());

INSERT INTO targets (subject_type, subject_id, month, revision, kind, value,
                     raw_amount, raw_percent, raw_is_percentage, source, reason)
SELECT 'contract', c.id, cawp_riyadh_month(), 1, k.kind, k.value,
       c.monthly_target_amount, c.monthly_target_percent, c.monthly_target_is_percentage,
       'imported_baseline', 'legacy value at migration 0035'
  FROM contracts c
 CROSS JOIN LATERAL cawp_target_kind('contract', c.monthly_target_amount, c.monthly_target_percent,
                                     c.monthly_target_is_percentage) k
 WHERE NOT EXISTS (SELECT 1 FROM targets t
                    WHERE t.subject_type = 'contract' AND t.subject_id = c.id
                      AND t.month = cawp_riyadh_month());
