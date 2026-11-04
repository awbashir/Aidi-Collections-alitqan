-- One-time ops script (run as postgres, after migration 0093): the login of the SQL builder page.
--
--   sudo -u postgres psql -d mydata -v pw="'<generated password>'" -f db/ops/sql_reader_role.sql
--
-- Then write the URL (one line) to the secret file the API reads on every run, no restart needed:
--   install -m 0640 -o root -g cawp /dev/null /etc/alitqan/sql_reader.url
--   echo 'postgresql://alitqan_sql_reader:<password>@127.0.0.1:5432/mydata' > /etc/alitqan/sql_reader.url
-- (or set SQL_READER_DATABASE_URL in the API env file and restart the API).
--
-- The role can read the views in schema bi and nothing else: no public tables, no writes. Its
-- sessions are read-only, time-limited and few. Re-runnable (it also re-applies the grants after
-- a later migration adds a bi view).

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'alitqan_sql_reader') THEN
        CREATE ROLE alitqan_sql_reader LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION
            CONNECTION LIMIT 5;
    END IF;
END $$;

ALTER ROLE alitqan_sql_reader WITH LOGIN PASSWORD :pw;
ALTER ROLE alitqan_sql_reader SET default_transaction_read_only = on;
ALTER ROLE alitqan_sql_reader SET statement_timeout = '15s';
ALTER ROLE alitqan_sql_reader SET idle_in_transaction_session_timeout = '30s';
ALTER ROLE alitqan_sql_reader SET search_path = bi;

REVOKE ALL ON ALL TABLES IN SCHEMA public FROM alitqan_sql_reader;
REVOKE CREATE ON SCHEMA public FROM alitqan_sql_reader;
-- some installs revoke CONNECT from PUBLIC: grant it to this login explicitly
DO $$ BEGIN EXECUTE format('GRANT CONNECT ON DATABASE %I TO alitqan_sql_reader', current_database()); END $$;
GRANT USAGE ON SCHEMA bi TO alitqan_sql_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA bi TO alitqan_sql_reader;
