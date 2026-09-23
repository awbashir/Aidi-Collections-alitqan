-- 0034 targets with history (Phase 3, stream C).
--
-- Legacy keeps ONE current target per user (users.target_amount / target_percent /
-- target_is_percentage) and per contract (contracts.monthly_target_amount /
-- monthly_target_percent / monthly_target_is_percentage), edited through the legacy user
-- and contract forms, with no history. From this migration on every change is kept:
--
--   targets            append-only. One row per (subject, month, revision). `month` is the
--                      first day of an Asia/Riyadh civil month. The target in force for a
--                      month is the highest revision of the latest month <= that month
--                      (a target carries forward until it is changed).
--   capture triggers   AFTER INSERT / AFTER UPDATE OF the legacy target columns on users
--                      and contracts: a legacy write is recorded as a new revision for the
--                      CURRENT Riyadh month (source 'legacy'). The legacy columns stay the
--                      current value; no legacy response changes.
--                      The v2 service (services/targets.py) writes its own row (actor,
--                      reason, request id, idempotency key) and, for the current month,
--                      mirrors it into the legacy columns with
--                      set_config('cawp_targets.skip_capture', 'on', true) so it is not
--                      recorded twice.
--                      A capture failure never fails the legacy write (it is logged as a
--                      WARNING and skipped); every value the legacy columns can hold fits
--                      the table (unbounded numeric).
--
-- kind / value follow the legacy dashboard rule exactly (CAWP-frontend
-- src/views/dashboard2/components/KPICollector.tsx:86-114 and AllCollectorsKPI.tsx:200-224):
--   user:     is_percentage AND percent > 0      -> 'percent', percent
--             NOT is_percentage AND amount > 0   -> 'amount',  amount
--             otherwise                          -> 'none' (the contract target applies)
--   contract: percent > 0 -> 'percent' (the flag is ignored by the legacy dashboard);
--             amount > 0  -> 'amount'; otherwise 'none'.
-- The raw legacy triple is kept too (raw_amount / raw_percent / raw_is_percentage).
--
-- Locks: new table; CREATE TRIGGER on users and contracts takes SHARE ROW EXCLUSIVE for
-- milliseconds (lock_timeout 5s). No backfill here (0035).

CREATE TABLE IF NOT EXISTS targets (
    id                 bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    subject_type       text        NOT NULL CHECK (subject_type IN ('user', 'contract')),
    subject_id         uuid        NOT NULL,
    month              date        NOT NULL,
    revision           integer     NOT NULL CHECK (revision >= 1),
    kind               text        NOT NULL CHECK (kind IN ('amount', 'percent', 'none')),
    value              numeric     NOT NULL,
    raw_amount         double precision,
    raw_percent        double precision,
    raw_is_percentage  boolean,
    source             text        NOT NULL CHECK (source IN ('legacy', 'v2', 'imported_baseline')),
    set_by             uuid,
    set_at             timestamptz NOT NULL DEFAULT now(),
    reason             text,
    request_id         text,
    idempotency_key    text,
    CONSTRAINT targets_month_is_first_day CHECK (extract(day FROM month) = 1),
    CONSTRAINT targets_revision_uq UNIQUE (subject_type, subject_id, month, revision),
    CONSTRAINT targets_none_is_zero CHECK (kind <> 'none' OR value = 0),
    CONSTRAINT targets_v2_fields CHECK (
        source <> 'v2' OR (set_by IS NOT NULL AND reason IS NOT NULL AND btrim(reason) <> ''))
);

COMMENT ON TABLE targets IS
    'Monthly target history for users and contracts (append-only). In force for month M: the '
    'highest revision of the latest month <= M. Legacy columns on users/contracts hold the current '
    'value; legacy writes are captured by trigger (source legacy), v2 writes carry actor and reason.';
COMMENT ON COLUMN targets.month IS 'First day of an Asia/Riyadh civil month.';
COMMENT ON COLUMN targets.kind IS 'amount | percent | none (none = no own target: users fall back to the contract target).';
COMMENT ON COLUMN targets.value IS 'SAR (2 decimals) for amount, percent points for percent, 0 for none.';

CREATE INDEX IF NOT EXISTS ix_targets_subject_month
    ON targets (subject_type, subject_id, month DESC, revision DESC);
CREATE INDEX IF NOT EXISTS ix_targets_month ON targets (month, subject_type);

CREATE OR REPLACE FUNCTION cawp_targets_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% is append-only: % is not allowed', TG_TABLE_NAME, TG_OP
        USING ERRCODE = 'insufficient_privilege',
              HINT = 'Record a new revision instead.';
END $$;

DROP TRIGGER IF EXISTS targets_append_only ON targets;
CREATE TRIGGER targets_append_only
    BEFORE UPDATE OR DELETE ON targets
    FOR EACH ROW EXECUTE FUNCTION cawp_targets_append_only();

DROP TRIGGER IF EXISTS targets_no_truncate ON targets;
CREATE TRIGGER targets_no_truncate
    BEFORE TRUNCATE ON targets
    FOR EACH STATEMENT EXECUTE FUNCTION cawp_targets_append_only();

-- ------------------------------------------------------------------ helpers
CREATE OR REPLACE FUNCTION cawp_riyadh_month(ts timestamptz DEFAULT now()) RETURNS date
LANGUAGE sql STABLE PARALLEL SAFE AS $$
    SELECT date_trunc('month', (ts AT TIME ZONE 'Asia/Riyadh'))::date
$$;

COMMENT ON FUNCTION cawp_riyadh_month(timestamptz) IS
    'First day of the Asia/Riyadh civil month of ts (default: transaction start).';

CREATE OR REPLACE FUNCTION cawp_admin_ctx(p_name text) RETURNS text
LANGUAGE sql STABLE AS $$
    SELECT nullif(current_setting('cawp_admin.' || p_name, true), '')
$$;

COMMENT ON FUNCTION cawp_admin_ctx(text) IS
    'Transaction-local context set by stream C code (cawp_admin.actor/reason/source/request_id/'
    'idempotency_key); NULL when unset (plain legacy writers).';

CREATE OR REPLACE FUNCTION cawp_target_kind(p_subject text, p_amount double precision,
                                            p_percent double precision, p_is_pct boolean,
                                            OUT kind text, OUT value numeric)
LANGUAGE plpgsql IMMUTABLE AS $$
BEGIN
    IF p_subject = 'user' THEN
        IF coalesce(p_is_pct, false) AND coalesce(p_percent, 0) > 0 THEN
            kind := 'percent'; value := p_percent::numeric;
        ELSIF NOT coalesce(p_is_pct, false) AND coalesce(p_amount, 0) > 0 THEN
            kind := 'amount'; value := round(p_amount::numeric, 2);
        ELSE
            kind := 'none'; value := 0;
        END IF;
    ELSE
        IF coalesce(p_percent, 0) > 0 THEN
            kind := 'percent'; value := p_percent::numeric;
        ELSIF coalesce(p_amount, 0) > 0 THEN
            kind := 'amount'; value := round(p_amount::numeric, 2);
        ELSE
            kind := 'none'; value := 0;
        END IF;
    END IF;
END $$;

COMMENT ON FUNCTION cawp_target_kind(text, double precision, double precision, boolean) IS
    'Effective kind/value of a legacy target triple, exactly as the legacy dashboard reads it.';

-- Appends one captured revision. The subject row is already locked by the UPDATE/INSERT
-- that fired the trigger, so concurrent writers of the same subject are serialized.
CREATE OR REPLACE FUNCTION cawp_targets_capture(p_subject text, p_id uuid, p_amount double precision,
                                                p_percent double precision, p_is_pct boolean)
RETURNS void
LANGUAGE plpgsql AS $$
DECLARE
    v_month date := cawp_riyadh_month(clock_timestamp());
    v_kind text;
    v_value numeric;
BEGIN
    SELECT k.kind, k.value INTO v_kind, v_value
      FROM cawp_target_kind(p_subject, p_amount, p_percent, p_is_pct) k;
    INSERT INTO targets (subject_type, subject_id, month, revision, kind, value,
                         raw_amount, raw_percent, raw_is_percentage, source, set_by, reason,
                         request_id, idempotency_key)
    SELECT p_subject, p_id, v_month,
           coalesce((SELECT max(t.revision) FROM targets t
                      WHERE t.subject_type = p_subject AND t.subject_id = p_id
                        AND t.month = v_month), 0) + 1,
           v_kind, v_value, p_amount, p_percent, p_is_pct,
           'legacy', cawp_admin_ctx('actor')::uuid, cawp_admin_ctx('reason'),
           cawp_admin_ctx('request_id'), cawp_admin_ctx('idempotency_key');
EXCEPTION WHEN OTHERS THEN
    -- never fail the legacy write because of the history
    RAISE WARNING 'targets capture for % % skipped: % (%)', p_subject, p_id, SQLERRM, SQLSTATE;
END $$;

CREATE OR REPLACE FUNCTION cawp_targets_capture_users() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF coalesce(current_setting('cawp_targets.skip_capture', true), '') = 'on' THEN
        RETURN NULL;
    END IF;
    IF TG_OP = 'INSERT' THEN
        IF coalesce(NEW.target_amount, 0) = 0 AND coalesce(NEW.target_percent, 0) = 0
           AND NOT coalesce(NEW.target_is_percentage, false) THEN
            RETURN NULL;   -- a new user without a target: nothing to record
        END IF;
    ELSIF (OLD.target_amount, OLD.target_percent, OLD.target_is_percentage)
          IS NOT DISTINCT FROM (NEW.target_amount, NEW.target_percent, NEW.target_is_percentage) THEN
        RETURN NULL;
    END IF;
    PERFORM cawp_targets_capture('user', NEW.id, NEW.target_amount, NEW.target_percent,
                                 NEW.target_is_percentage);
    RETURN NULL;
END $$;

CREATE OR REPLACE FUNCTION cawp_targets_capture_contracts() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF coalesce(current_setting('cawp_targets.skip_capture', true), '') = 'on' THEN
        RETURN NULL;
    END IF;
    IF TG_OP = 'INSERT' THEN
        IF coalesce(NEW.monthly_target_amount, 0) = 0 AND coalesce(NEW.monthly_target_percent, 0) = 0
           AND NOT coalesce(NEW.monthly_target_is_percentage, false) THEN
            RETURN NULL;
        END IF;
    ELSIF (OLD.monthly_target_amount, OLD.monthly_target_percent, OLD.monthly_target_is_percentage)
          IS NOT DISTINCT FROM
          (NEW.monthly_target_amount, NEW.monthly_target_percent, NEW.monthly_target_is_percentage) THEN
        RETURN NULL;
    END IF;
    PERFORM cawp_targets_capture('contract', NEW.id, NEW.monthly_target_amount,
                                 NEW.monthly_target_percent, NEW.monthly_target_is_percentage);
    RETURN NULL;
END $$;

DROP TRIGGER IF EXISTS users_targets_capture_ins ON users;
CREATE TRIGGER users_targets_capture_ins
    AFTER INSERT ON users
    FOR EACH ROW EXECUTE FUNCTION cawp_targets_capture_users();

DROP TRIGGER IF EXISTS users_targets_capture_upd ON users;
CREATE TRIGGER users_targets_capture_upd
    AFTER UPDATE OF target_amount, target_percent, target_is_percentage ON users
    FOR EACH ROW EXECUTE FUNCTION cawp_targets_capture_users();

DROP TRIGGER IF EXISTS contracts_targets_capture_ins ON contracts;
CREATE TRIGGER contracts_targets_capture_ins
    AFTER INSERT ON contracts
    FOR EACH ROW EXECUTE FUNCTION cawp_targets_capture_contracts();

DROP TRIGGER IF EXISTS contracts_targets_capture_upd ON contracts;
CREATE TRIGGER contracts_targets_capture_upd
    AFTER UPDATE OF monthly_target_amount, monthly_target_percent, monthly_target_is_percentage ON contracts
    FOR EACH ROW EXECUTE FUNCTION cawp_targets_capture_contracts();

-- ------------------------------------------------------------------ current revision per month
CREATE OR REPLACE VIEW v_target_months AS
SELECT DISTINCT ON (subject_type, subject_id, month)
       subject_type, subject_id, month, revision, kind, value, source, set_by, set_at, reason, id
  FROM targets
 ORDER BY subject_type, subject_id, month, revision DESC;

COMMENT ON VIEW v_target_months IS
    'Latest revision of every (subject, month) that has target rows. The target in force for a '
    'month without rows is the latest earlier month (services/targets.py effective_targets).';
