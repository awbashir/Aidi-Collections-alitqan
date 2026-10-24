-- 0086 campaign customer lists and removals (customer feedback 2026-10-24,
-- services/calling/campaign_pool.py, services/calling/campaign_cases.py).
--
-- "When creating a campaign we need more control, like selecting customers, including customer
--  ids from Excel. Before the campaign starts the user can see the list of customers and remove."
--
-- campaign_list_cases     the cases of a CUSTOMER-LIST campaign (pool key `case_list: true`):
--   filled from an Excel sheet or pasted values, each value matched against the national id
--   (the identity key of services/same_identity.py: every account of that customer) or the
--   account number, within the editor's scope. The pool is then exactly these cases (ANDed
--   with any other pool criterion), still bounded by the creator's scope and every calling rule.
-- campaign_removed_cases  cases a supervisor took out of ANY campaign (filter or list) by hand:
--   pool reason `removed_by_user`, never dialled. Restoring deletes the row (audited), and a
--   new import of the same case restores it too.
--
-- campaigns.cases_changed_at  set by every import / removal / restore, so it bumps the campaign's
--   revision (the pool summary cache and the editor's expected_revision follow it), like
--   roster_changed_at does for the members.
--
-- New, empty tables and one nullable column (catalog only): no lock on existing data. Re-runnable.

ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS cases_changed_at timestamptz;

CREATE TABLE IF NOT EXISTS campaign_list_cases (
    campaign_id  uuid        NOT NULL REFERENCES campaigns (id),
    case_id      uuid        NOT NULL,
    source       text        NOT NULL CHECK (source IN ('excel', 'paste')),
    matched_by   text        NOT NULL CHECK (matched_by IN ('identity', 'account')),
    added_by     uuid,
    added_at     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (campaign_id, case_id)
);

CREATE TABLE IF NOT EXISTS campaign_removed_cases (
    campaign_id  uuid        NOT NULL REFERENCES campaigns (id),
    case_id      uuid        NOT NULL,
    removed_by   uuid,
    removed_at   timestamptz NOT NULL DEFAULT now(),
    reason       text        CHECK (reason IS NULL OR length(reason) <= 500),
    PRIMARY KEY (campaign_id, case_id)
);

COMMENT ON TABLE campaign_list_cases IS
    'Cases of a customer-list campaign (pool case_list), imported from Excel or pasted ids (0086).';
COMMENT ON TABLE campaign_removed_cases IS
    'Cases removed from a campaign by hand before or during it: pool reason removed_by_user (0086).';
