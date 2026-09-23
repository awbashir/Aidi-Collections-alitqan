# Database scripts and migrations

All schema changes are **additive, reviewed plain SQL** in this directory, applied and
recorded by `CAWP-API/manage_db.py`. Never change the schema by hand, never edit a
migration after it was applied, and never drop or restore a database to "fix" one.

```
db/
├── 01_performance_indexes.sql     legacy (pre-runner) scripts, versions 0001-0003
├── 02_jsonb_attribute.sql
├── 03_last_comment.sql
├── migrations/NNNN_description.sql  new migrations, applied in version order
└── schema/baseline.sql            schema-only dump for tests/CI (no data, no owners/grants)
```

## Migration list

| Version | File | What | Locks / duration | Prod-safe |
| --- | --- | --- | --- | --- |
| 0001 | `01_performance_indexes.sql` | Drops 6 redundant indexes, adds ~35 with `CREATE INDEX CONCURRENTLY`, autovacuum tuning | online, 10-25 min on prod | Yes (legacy, applied with psql) |
| 0002 | `02_jsonb_attribute.sql` | `clints.attribute json -> jsonb` | table rewrite, `ACCESS EXCLUSIVE` 1-3 min | Maintenance window (legacy) |
| 0003 | `03_last_comment.sql` | `clints.last_comment_at/last_comment_id` + trigger on `"Comments"` + backfill + 4 partial indexes | backfill ~30 s of row locks | Yes (legacy) |
| 0004 | `migrations/0004_audit_events.sql` | `audit_events` (append-only: UPDATE/DELETE/TRUNCATE rejected by triggers) + 5 indexes | new table only, instant | Yes |
| 0005 | `migrations/0005_idempotency_keys.sql` | `idempotency_keys` (unique `(user_id, key)`, expiry index) | new table only, instant | Yes |
| 0006 | `migrations/0006_revision_columns.sql` | `revision int NOT NULL DEFAULT 0` + `cawp_bump_revision()` BEFORE UPDATE trigger on `clints`, `"Comments"`, `"Payments"`, `"ClintContacts"`, `"paymentDates"` | metadata-only `ADD COLUMN` (no rewrite) but a brief `ACCESS EXCLUSIVE` per table; `lock_timeout 5s`; measured 5 ms on the full local copy | Yes |

The `revision` columns are **not mapped in `models.py`**: every legacy serializer iterates
mapped columns only and no legacy query selects `*`, so they never appear in a legacy
response (proved with `tools/api_snapshot.py`, see `review/phase0-backend.md`).

## The runner (`CAWP-API/manage_db.py`)

| Command | What it does |
| --- | --- |
| `status` | every version with its state: `baselined`, `applied` (time, ms), `pending`, `CHECKSUM MISMATCH`; exit 1 on a mismatch |
| `baseline [--dry-run]` | records the legacy scripts 0001-0003 as `baselined` **only if their objects are present** (pg_trgm + every index they create, valid; `clints.attribute` is jsonb; the `last_comment` columns, functions, trigger and indexes). Never runs them |
| `up [--dry-run] [--target NNNN] [--no-permissions]` | applies pending migrations in order, then merges new permission keys (`core/permissions.py`) into `roles.permission`. Refuses while a legacy script is unrecorded |
| `verify` | exit 1 when an applied file's sha256 changed or an applied file is missing |
| `sync-permissions [--dry-run]` | only the permission merge |
| `load-schema FILE` | loads a schema-only dump into an **empty** database (tests/CI); refuses if the database has tables |

Options: `--database-url` (default `$MIGRATE_DATABASE_URL`, then `$DATABASE_URL`, then
`CAWP-API/.env`), `--dir` (default `../db` from the script, i.e. `/db` in the container,
or `$CAWP_DB_DIR`), `--lock-timeout` (default `5s`).

Guarantees:

* `schema_migrations(version, name, checksum, applied_at, duration_ms, execution, applied_by)`
  is created on first use. `checksum` is the sha256 of the file (CRLF normalised); the
  runner refuses to run `up`/`baseline` while any applied file differs.
* Each file runs in **one transaction together with its `schema_migrations` row**: it is
  applied completely or not at all. `lock_timeout` (5 s) makes a blocked `ALTER TABLE`
  fail fast instead of queueing live traffic; `statement_timeout` is 0.
* A file whose header contains `-- migrate:no-transaction` runs statement by statement in
  autocommit (needed for `CREATE INDEX CONCURRENTLY`) with no lock timeout, and is recorded
  only when every statement succeeded. Such files must be idempotent (`IF NOT EXISTS`).
  `-- migrate:lock-timeout 30s` overrides the lock timeout for one file.
* `BEGIN`/`COMMIT` and psql meta-commands (`\set`, `\timing`) are rejected in migration files.
* A session advisory lock allows one runner at a time.

### Local (Docker)

The compose file mounts `./db` at `/db` in the `api` container:

```bash
docker exec cawp-api python manage_db.py status
docker exec cawp-api python manage_db.py baseline --dry-run   # what would be recorded
docker exec cawp-api python manage_db.py baseline             # once per database
docker exec cawp-api python manage_db.py up --dry-run
docker exec cawp-api python manage_db.py up
docker restart cawp-api                                       # the API has no auto-reload
```

On a database restored from the dump without 01-03, apply those first (they use psql
meta-commands and `CONCURRENTLY`, so they stay psql scripts), then `baseline`:

```bash
docker exec cawp-db psql -U postgres -d mydata -f /db/01_performance_indexes.sql
docker exec cawp-db psql -U postgres -d mydata -f /db/02_jsonb_attribute.sql
docker exec cawp-db psql -U postgres -d mydata -f /db/03_last_comment.sql
```

Restore the dump into the Docker DB (already done by the compose setup):

```bash
docker exec cawp-db pg_restore -U postgres -d mydata --no-owner --no-privileges -j 4 /dump/mydata.dump
```

### Writing a migration

1. Next free number: `db/migrations/0007_short_description.sql` (lower_snake_case).
2. Header comment: what, why, **which locks it takes and for how long**.
3. Additive only (new tables/columns/indexes/triggers/views). No `DROP`/`RENAME` of
   anything the current code reads. Use `IF NOT EXISTS` / `CREATE OR REPLACE` so a manual
   re-run is harmless.
4. A new column on a table that a legacy model maps: do **not** map it in `models.py`
   unless the response change is intended; check `services/` for `SELECT *` first.
5. Indexes on big existing tables: a separate `-- migrate:no-transaction` file with
   `CREATE INDEX CONCURRENTLY IF NOT EXISTS`.
6. Run `up --dry-run`, then the pytest suite (`docker compose --profile test run --rm api-test`),
   which applies every migration to a fresh copy of `schema/baseline.sql`.

## `schema/baseline.sql`

`pg_dump --schema-only --no-owner --no-privileges --no-comments` of the local `cawp-db`
(production dump + 01-03) taken on 2026-09-23 **before** 0004. Structure only: no rows,
no roles, no passwords (only the `users.password` column definition). The psql-only
`\restrict` lines were removed. It is what the pytest harness and CI build their database
from; after `baseline` + `up` the result is schema-identical to the live database
(verified by diffing two `pg_dump --schema-only` outputs). Regenerate it only from a
database at a known migration state, and never load it into a database with data.
