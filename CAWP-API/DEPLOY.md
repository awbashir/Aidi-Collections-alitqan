# Deploying the refactored CAWP API

This is the upgrade path from the current production release to the refactored tree
(`app.py` + `api/` blueprints + `services/` + `core/`). The HTTP contract is unchanged, so
the frontend does **not** have to be deployed at the same time.

Order matters: the database migrations come first (steps 1–3, then 3b), then the image (4–6).

---

## 0. Before you start

* Take a full backup: `pg_dump -Fc -U postgres mydata > mydata-$(date +%F).dump`.
* Note the current image tag so you can roll back (`docker image ls`).
* Decide the maintenance window: only step 2 and the backfill in step 3 need one.
  Everything else is online.

---

## 1. Indexes — **online**, no downtime

```bash
psql -U postgres -d mydata -f db/01_performance_indexes.sql
```

Drops 6 duplicate / prefix-redundant indexes (≈415 MB on `"SepcifyUsers"` alone), creates
`pg_trgm`, and adds the ~30 missing indexes the new queries use — including the two GIN
trigram indexes that make the `contains` filters on `name` / `identityNumber` index scans.

Everything is `CREATE INDEX CONCURRENTLY` / `DROP INDEX CONCURRENTLY`, so it must **not**
run inside a transaction block (plain `psql -f` is correct) and it takes no blocking lock.
The script is idempotent; re-running it is a no-op. Expect 10–25 minutes on the production
volume and a temporary increase in WAL.

If a `CREATE INDEX CONCURRENTLY` is interrupted it leaves an `INVALID` index behind:

```sql
SELECT indexrelid::regclass FROM pg_index WHERE NOT indisvalid;
-- DROP INDEX CONCURRENTLY <name>;   then re-run the script
```

## 2. `clints.attribute` → `jsonb` — **maintenance window**

```bash
psql -U postgres -d mydata -f db/02_jsonb_attribute.sql
```

`ALTER TABLE clients ALTER COLUMN attribute TYPE jsonb` rewrites the whole 600 MB `clints`
table under an `ACCESS EXCLUSIVE` lock: **1–3 minutes during which the table is
unavailable**. Make sure you have ~1.2 GB of free disk for the rewrite, then the script
runs `VACUUM (ANALYZE) clints`. Idempotent (it checks `information_schema` first).

## 3. `clints.last_comment_at` / `last_comment_id` — **maintenance window**

```bash
psql -U postgres -d mydata -f db/03_last_comment.sql
```

* two `ALTER TABLE … ADD COLUMN … (nullable)` — instant, no rewrite;
* `clints_refresh_last_comment()` + the `comments_last_comment_trg` trigger on `"Comments"`
  (AFTER INSERT / UPDATE OF create_at,is_deleted,clint_id / DELETE);
* a single-statement backfill — **≈30 s for 175k rows** while holding row locks on the
  clients it touches;
* three indexes, `CONCURRENTLY`:
  `ix_clints_last_comment_live`, `ix_clints_user_last_comment_live`,
  `ix_clints_last_comment_id`;
* `VACUUM (ANALYZE) clints`.

The trigger keeps the two columns correct from then on; the API never writes them.

**Verify:**

```sql
SELECT count(*) FROM clints WHERE last_comment_id IS NOT NULL;      -- ≈175k
SELECT count(*) FROM clints c
  WHERE c.last_comment_id IS DISTINCT FROM (
        SELECT m.id FROM "Comments" m
         WHERE m.clint_id = c.id AND m.is_deleted = false
         ORDER BY m.create_at DESC, m.id DESC LIMIT 1);              -- must be 0
```

## 3b. Record 01–03 and apply `db/migrations` with `manage_db.py` — **online**

From Phase 0 on, schema changes are numbered SQL files in `db/migrations/`, applied and
recorded (version, sha256, time, duration) in `schema_migrations` by
`CAWP-API/manage_db.py`. See `db/README.md` for the rules. The runner ships in the image;
the SQL files do not (the image is built from `CAWP-API/` only), so mount `db/` from the
release checkout:

```bash
# on the deploy host, from the checkout of the release being deployed
RUN="docker run --rm --env-file /etc/cawp/api.env -v $PWD/db:/db:ro cawp-api:<tag> python manage_db.py"
$RUN status                 # 0001-0003 "pending (legacy)", 0004+ "pending"
$RUN baseline --dry-run     # must say "present, would be recorded" for 0001, 0002, 0003
$RUN baseline               # records them; never executes them
$RUN up --dry-run           # lists what will run, in which mode, with which lock timeout
$RUN up
$RUN verify                 # exit 0 = every applied file matches its checksum
```

`--env-file` supplies `DATABASE_URL`; set `MIGRATE_DATABASE_URL` instead if migrations
should use a different (owner) role than the application. If `baseline` reports a legacy
script as *not present*, run that script with psql first (steps 1–3), then `baseline`
again. `up` refuses to run until 0001–0003 are recorded, and refuses while any applied
file's checksum differs from the file on disk.

What each Phase 0 migration locks:

| Migration | Statements | Locks | Duration |
|---|---|---|---|
| `0004_audit_events` | `CREATE TABLE audit_events`, 5 indexes, append-only trigger function + 2 triggers | only the new table | instant |
| `0005_idempotency_keys` | `CREATE TABLE idempotency_keys`, 1 index | only the new table | instant |
| `0006_revision_columns` | `ALTER TABLE … ADD COLUMN revision integer NOT NULL DEFAULT 0` on `clints`, `"Comments"`, `"Payments"`, `"ClintContacts"`, `"paymentDates"`; `cawp_bump_revision()`; 5 `BEFORE UPDATE` triggers | brief `ACCESS EXCLUSIVE` per table (metadata-only on PG ≥ 11: no rewrite, no backfill) + `SHARE ROW EXCLUSIVE` for each trigger, all in one transaction | 5 ms on the full local copy (474k clients, 1.27M comments, 1.8M contacts) |

Every transactional migration runs with `lock_timeout = 5s`: if a long report query holds a
lock on one of those tables, the migration aborts and rolls back completely (nothing is
recorded) instead of making every other request wait behind it. Re-run `up` a moment
later. The migrations are additive and the current code never reads the new objects, so
they can be applied while the old image is still serving.

What the triggers change at run time: every UPDATE on the five tables now also sets
`revision` (a `to_jsonb(OLD)`/`to_jsonb(NEW)` comparison per row, microseconds). On
`clints`, changes that only touch `last_comment_at`/`last_comment_id` (rewritten by the
`"Comments"` trigger on every new note) do not bump the revision.

---

## 4. Environment

The application no longer has any hard-coded credentials; `core/config.py` reads
everything from the environment. Set at minimum:

| Variable | Notes |
|---|---|
| `JWT_SECRET_KEY` | **required** — rotating it logs everybody out |
| `SECRET_KEY` | **required** — `JWT_SECRET_KEY` falls back to it |
| `DATABASE_URL` | `postgresql+psycopg2://user:pass@host:5432/mydata` |
| `REDIS_HOST` (`REDIS_PORT`, `REDIS_DB`, `REDIS_PASSWORD`) | cache, JWT blocklist and job state. The API degrades gracefully if Redis is down, but the dashboards get slower and logout stops revoking tokens. |
| `CORS_ORIGINS` | comma-separated list of the real frontend origins — do **not** leave it at `*` in production |
| `CDR_HOST`, `CDR_DB`, `CDR_USER`, `CDR_PASSWORD`, `CDR_CONNECT_TIMEOUT` | Asterisk CDR MySQL. Set `CDR_ENABLED=0` if the PBX is gone. |
| `RECORDINGS_ROOT`, `RECORDINGS_SSH_HOST`, `RECORDINGS_SSH_USER`, `RECORDINGS_SSH_PASSWORD`, `RECORDINGS_SSH_KEY` | call-recording download; only paths under `RECORDINGS_ROOT` are served |
| `UPLOAD_DIR` | must be a **persistent volume**: `temp/`, `documents/`, `exports/` live there and `documents/` holds uploaded client documents |
| `PORT`, `WEB_CONCURRENCY`, `GUNICORN_THREADS` | `9797`, `1`, `32` |
| `SENTRY_DSN` | optional; Sentry is only initialised when set |
| `FLASK_DEBUG` | leave **off** — it is what keeps exception text out of error bodies |

`.env.example` is the full template. Copy it to `.env` next to `app.py`, or inject the
variables through your orchestrator.

## 5. Build and run

```bash
docker build -t cawp-api:<tag> ./CAWP-API
docker run -d --name cawp-api --restart unless-stopped \
  --env-file /etc/cawp/api.env \
  -p 9797:9797 \
  -v /srv/cawp/uploads:/app/uploads \
  cawp-api:<tag>
```

The image's `CMD` is the production server — do not override it:

```
gunicorn --worker-class gthread --workers ${WEB_CONCURRENCY:-1} \
         --threads ${GUNICORN_THREADS:-32} --timeout 300 --graceful-timeout 30 \
         --keep-alive 5 --bind 0.0.0.0:${PORT:-9797} \
         --access-logfile - --error-logfile - app:app
```

* **One worker.** Flask-SocketIO runs in threading mode; more than one process needs a
  message queue, and the `alarm` notifications would otherwise only reach the clients
  connected to one worker. Scale with threads, not workers.
* **Do not raise `--timeout` to "fix" slow imports.** The imports are batched now and
  `?async=1` exists for the big files; a long timeout only hides a stuck worker.
* `HEALTHCHECK` polls `GET /health` every 15 s; put the same URL in the load balancer.
  It returns `503` when PostgreSQL is unreachable.
* `requirements.txt` gained **Flask-Compress** — the image must be rebuilt, not just
  restarted, or every response goes out uncompressed (the biggest dashboard is 7.5 MB raw
  vs 1.9 MB gzipped).

## 5b. Bulk-job worker (Phase 2) — **separate process, same image**

Reviewed bulk jobs (`/v2/bulk/*`, `/v2/jobs`, `/v2/uploads`) are validated and executed by a
separate worker process, never inside a gunicorn request. It is the same image with another
command. Details, the state machine and the API contract: `review/phase2-framework.md`.

**Order:** apply migrations `0016`–`0019` (section 3b; new tables only, instant), deploy the
API, then start the worker. Until a worker runs, jobs stay `queued` / `confirmed` (nothing
is lost; the worker picks them up when it starts).

```bash
docker run -d --name cawp-worker --restart unless-stopped \
  --env-file /etc/cawp/api.env \
  -v /srv/cawp/uploads:/app/uploads \
  --stop-timeout 90 \
  --health-cmd "python -m worker --healthcheck" --health-interval 30s --health-retries 3 \
  cawp-api:<tag> python -m worker
```

Local stack: `docker compose --profile worker up -d worker` (service `worker` in
`docker-compose.yml`; it sits behind the `worker` profile until the migrations are applied).

* **Same env and the same upload volume as the API.** Uploads and CSV artifacts live under
  `BULK_STORAGE_DIR` (default `<UPLOAD_DIR>/private/bulk`); the API writes uploads and serves
  downloads, the worker reads uploads and writes artifacts, so both must mount the same
  persistent volume. Nothing under it is ever served statically (downloads go through
  `GET /v2/bulk/jobs/<id>/artifacts/<kind>` with a permission check). Do not expose that
  directory through nginx.
* **No Redis needed.** Jobs live in PostgreSQL (`bulk_jobs`, `bulk_job_rows`, ...). The
  in-memory `core/jobs.py` + `/jobs/<id>` path of the legacy `?async=1` routes is unchanged.
* **Stopping:** `SIGTERM` finishes the current batch (one transaction), releases the job as
  `retrying` and exits 0. Give it a stop timeout longer than one batch (90 s is ample; a
  500-row lock batch takes ~0.1–0.5 s). A `SIGKILL`/crash is also safe: the lease expires after
  `BULK_LEASE_SECONDS` and another (or the restarted) worker resumes the job; rows already
  done are never done again.
* **Scaling:** run more replicas if needed (`--scale worker=N`, or more containers with
  distinct names). Claims use `FOR UPDATE SKIP LOCKED`; one job runs on one worker at a time.
* **Health:** `python -m worker --healthcheck` exits 0 while the worker loop has touched
  `BULK_WORKER_HEALTH_FILE` within `BULK_WORKER_HEALTH_MAX_AGE` seconds and PostgreSQL
  answers. It checks the loop, not HTTP (the worker has no port). `--no-db` skips the ping.
* **Retention:** the worker runs it every `BULK_RETENTION_INTERVAL_SECONDS` (one worker at a
  time, advisory lock): expires stale previews, deletes expired upload and artifact files
  (rows, run history and audit are kept), purges expired `idempotency_keys`. One-off:
  `python -m worker --retention-once`.
* **Permissions:** the kinds reuse the legacy keys (lock/unlock: `clints_bulk_lock` or
  `clints_lock`); `bulk_jobs_all` (new, merged by `manage_db.py up`, true for ADMIN /
  SuperAdmin only) lets supervisors and admins see other users' runs.

| Variable | Default | Meaning |
|---|---|---|
| `BULK_STORAGE_DIR` | `<UPLOAD_DIR>/private/bulk` | private root for uploads (`uploads/`), artifacts (`artifacts/`), temp (`tmp/`) |
| `BULK_UPLOAD_MAX_MB` | `20` | upload size limit (413 above it); keep ≤ `MAX_UPLOAD_MB` (Flask, 50) |
| `BULK_XLSX_MAX_UNCOMPRESSED_MB` | `300` | zip-bomb guard for workbooks |
| `BULK_MAX_ROWS` | `100000` | most rows per job (selection, filter result, sheet) |
| `BULK_MAX_COLUMNS` | `200` | most columns read from a sheet |
| `BULK_UPLOAD_TTL_DAYS` | `7` | upload file lifetime |
| `BULK_ARTIFACT_TTL_DAYS` | `14` | preview / errors / results CSV lifetime |
| `BULK_PREVIEW_TTL_HOURS` | `24` | a `ready` preview must be confirmed (and execution must start) within this |
| `BULK_BATCH_SIZE` | `500` | rows per execution batch = per transaction (a kind may override) |
| `BULK_PREPARE_CHUNK` | `2000` | rows per validation chunk |
| `BULK_LEASE_SECONDS` | `60` | worker lease; renewed every third of it by a heartbeat thread |
| `BULK_MAX_ATTEMPTS` | `3` | crashed/errored attempts before a job is `failed` (graceful stops don't count) |
| `BULK_WORKER_POLL_SECONDS` | `2` | idle poll interval |
| `BULK_RETENTION_INTERVAL_SECONDS` | `600` | retention task period |
| `BULK_WORKER_HEALTH_FILE` | `/tmp/cawp-worker.alive` | liveness file |
| `BULK_WORKER_HEALTH_MAX_AGE` | `120` | healthcheck threshold (seconds) |
| `WORKER_DB_POOL_SIZE` | `4` | SQLAlchemy pool of the worker (plus 2 overflow) |

The API process reads the same `BULK_*` variables (upload limits, TTLs, storage path), so set
them identically on both containers.

## 5c. v2 reports and private report exports (Phase 4) — **same API, same worker**

`/v2/reports`, `/v2/reports/<id>/rows`, `/v2/report-options/<field>` and the export endpoints
(`/v2/reports/<id>/exports`, `/v2/report-exports*`) live in the API (`api/reports_v2.py`). The
exports are generated by the **same worker process** as the bulk jobs (`python -m worker`; the
loop alternates between the two queues). Contract, examples and measurements:
`review/phase4-framework.md`.

**Order:** apply migrations `0039`–`0040` (section 3b; new tables `report_exports`,
`report_export_attempts`, instant), then restart the API **and** the worker. Nothing else is
required: no new permission key (each report uses its legacy screen's key), no new service.

* Before `0039` is applied, the preview/selector endpoints already work, but
  `POST /v2/reports/<id>/exports` would fail with a 500 (missing table); the worker simply skips
  the export queue (it checks for the table every 60 s) and its retention step skips report
  files. So apply the migrations before (or together with) the API restart.
* **Storage:** export files are written under `BULK_STORAGE_DIR/reports/<yyyy>/<mm>/` (private,
  0600) on the volume the API and the worker already share. Downloads go through
  `GET /v2/report-exports/<id>/download` (requester only, permission rechecked). Never serve the
  directory statically.
* **Snapshot:** each export runs in its own `REPEATABLE READ READ ONLY` transaction (statement
  timeout `REPORT_EXPORT_TIMEOUT_MS`); a year of RPT-06 (23,090 rows) takes ~1 s as CSV and
  ~1.6 s as XLSX on the local copy, with ~2.5 MB of Python heap (rows stream in batches of 500).
* **Retention:** the worker's retention step deletes export files past `expires_at`
  (`REPORT_EXPORT_TTL_DAYS`, default 7), sets `purged_at` and status `expired`, and writes a
  `report_export.purge` audit row. The rows (definition, totals, row count, requester) stay.
* **Stopping / crashes:** same as the bulk jobs. `SIGTERM` releases a running export as
  `retrying` (not counted); a `SIGKILL` leaves its lease to expire (`BULK_LEASE_SECONDS`), then
  any worker regenerates it from a fresh snapshot. Nothing is published twice; an unpublished
  temp file is removed on failure (or by the one-day `tmp/` sweep after a crash).

| Variable | Default | Meaning |
|---|---|---|
| `REPORT_EXPORT_TTL_DAYS` | `7` | export file lifetime (then deleted by retention) |
| `REPORT_EXPORT_BATCH` | `500` | rows fetched from the server-side cursor and written per batch |
| `REPORT_EXPORT_MAX_ROWS` | `500000` | larger exports fail `too_many_rows` (narrow the filters) |
| `REPORT_EXPORT_MAX_MB` | `200` | larger files fail `artifact_too_large` and are deleted |
| `REPORT_EXPORT_MAX_ATTEMPTS` | `3` | crashed/errored attempts before `failed` |
| `REPORT_EXPORT_TIMEOUT_MS` | `1800000` | statement timeout of the export snapshot |
| `REPORT_PREVIEW_TIMEOUT_MS` | `60000` | statement timeout of a preview / selector request (API) |

Tooling (read-only, safe on production copies): `python -m core.reports.perf reconcile` (legacy
`/payments*` vs RPT-06/07/08) and `python -m core.reports.perf timings`.

## 5d. Human calling foundation (Phase 5 P0): `/v2/calls*`, `/v2/calling/*` and the `calling` service

The API routes live in `api/calling.py`: manual call log, reviewed wrap-up, call lists, calling
settings, and the admin routes for extensions and windows. A separate long-lived process,
`python -m calling` (docker-compose service `calling`, same image), owns the AMI session to the
PBX and answers the FastAGI authorization hook. Design and examples: `review/phase5-foundation.md`.
PBX side: `docs/issabel-commissioning.md`.

**Order:**
1. Apply migrations `0048`–`0051` (section 3b; new tables only, instant). `up` also merges the
   permission keys `calls`, `calls_make` and `calls_manage` into every role.
2. Restart the API. The calling routes work without the calling service; `can_call` stays
   `false` in P0.
3. Deploy the `calling` service with **`CALLING_ENABLED=0`** (the default). It then opens no
   socket to any PBX, needs no secret and reports healthy.
4. Only after the Issabel commissioning checklist passed: set the variables below and
   `CALLING_PBX_COMMISSIONED=1`, then restart the service.

* **Refusals by design:** an enabled service refuses any PBX host other than the local test PBX
  (`pbx-test`) until `CALLING_PBX_COMMISSIONED=1`, including any name that resolves to
  10.10.0.220. It also refuses the test-only secrets from `docker-compose.yml` for any other PBX,
  and plaintext AMI except to loopback (use AMI TLS 5039, or an SSH tunnel to 127.0.0.1).
* **Restarts:** a restart marks the calls that were in flight on the PBX `uncertain`
  (`calling_service_restarted`). It **never releases a reservation**: only a saved wrap-up, the
  cancellation of an unsent attempt, or a named reconciliation (P1) can release one. The
  database enforces this (`calling_reservations_guard`).
* **Stopping:** `SIGTERM` stops accepting AGI connections, lets in-flight answers finish (5 s at
  most) and closes the AMI session (recorded as `calling_service_stopped`). Give the container
  20 s (`stop_grace_period`).
* **Health:** `python -m calling --healthcheck` (the compose healthcheck) reads the health file
  and pings the database. It reports unhealthy when the AMI session has been down longer than
  `CALLING_HEALTH_AMI_GRACE`, or when the FastAGI listener stopped. An internal JSON status is
  served on `CALLING_STATUS_PORT` (`/status`, `/healthz`). Never publish that port.
* **Database outage:** evidence stays in a bounded in-memory buffer (5,000 events) and is written
  in order once the database is back. An overflow records an `EvidenceGap` and marks the affected
  in-flight calls `uncertain`. The AMI session is not dropped for a database outage.
* **Commissioning checks** (read-only): `python -m calling.verify` on the calling host, and
  `calling/issabel/pbx-verify.sh` on the PBX.

| Variable | Default | Meaning |
|---|---|---|
| `CALLING_ENABLED` | `0` | `1` = connect to the PBX and listen for FastAGI |
| `CALLING_AMI_HOST` | (compose: `pbx-test`) | PBX host (the name in its TLS certificate) |
| `CALLING_AMI_PORT` | `5039` with TLS, `5038` without | |
| `CALLING_AMI_TLS` | `1` | `0` only with `CALLING_AMI_HOST=127.0.0.1` (tunnel) or a listed test host |
| `CALLING_AMI_CA_FILE` | system store | CA bundle that signs the PBX certificate |
| `CALLING_AMI_SERVER_NAME` | the host | TLS server name, if it differs |
| `CALLING_AMI_USERNAME` / `CALLING_AMI_SECRET` | — | the dedicated AMI account (vault) |
| `CALLING_PBX_COMMISSIONED` | `0` | operator assertion after the Issabel runbook |
| `CALLING_AGI_ENABLED` | `1` | the FastAGI hook |
| `CALLING_AGI_BIND` / `CALLING_AGI_PORT` | `0.0.0.0` / `4573` | reachable from the PBX only (firewall) |
| `CALLING_AGI_SECRET` | — | 16+ characters; equals `COLLECTIONS_AGI_KEY` on the PBX (vault) |
| `CALLING_AGI_ALLOWED_PEERS` | the AMI host | CIDRs or host names allowed to connect |
| `CALLING_HEARTBEAT_SECONDS` | `10` | AMI Ping interval |
| `CALLING_HEALTH_FILE` / `_MAX_AGE` / `_AMI_GRACE` | `/tmp/cawp-calling.alive` / `60` / `180` | health check |
| `CALLING_STATUS_PORT` | `8791` | internal status JSON; `0` = off |
| `CALLING_DATABASE_URL` | `DATABASE_URL` | override (tests point it at a test database) |
| `CALLING_DB_POOL_SIZE` | `4` | database pool of the service |

**Local test PBX** (profile `pbx-test`, never production): `docker compose --profile pbx-test up -d pbx-test`,
then `CALLING_ENABLED=1 CALLING_DATABASE_URL=… docker compose --profile pbx-test up -d calling`.
See the header of `tests/test_calling_live_pbx.py`.

## 6. Smoke-check the release

```bash
curl -fsS https://<host>/health
TOKEN=$(curl -s -X POST https://<host>/login -H 'Content-Type: application/json' \
        -d '{"username":"<user>","password":"<pass>"}' | jq -r .access_token)
curl -s -o /dev/null -w '%{http_code} %{time_total}\n' \
     -H "Authorization: Bearer $TOKEN" 'https://<host>/clints?page=1&pageSize=100'
curl -s -H 'Accept-Encoding: gzip' -o /dev/null -w '%{size_download}\n' \
     -H "Authorization: Bearer $TOKEN" 'https://<host>/dashbord/Allcollector/v2'   # ≈2 MB, not 7.5 MB
```

`tests/smoke.py` exercises the whole import/bulk/auth surface against a live server, but it
**writes test data** — run it against staging, never production.

---

## Alembic / `flask db`

> **Do not run `flask db migrate` (autogenerate) against production any more.**

`models.py` now declares *every* index that exists in the database — 67 of them, including
the partial (`postgresql_where`), descending, `NULLS LAST` and GIN-trigram ones — so the
model and the database are in exact agreement:

```
alembic.autogenerate.compare_metadata(ctx, db.metadata)  ->  0 differences
```

Autogenerate would nevertheless try to "fix" things it cannot express (the mutual
`clints` ↔ `clintArchive` foreign-key cycle, the two `SERIAL` sequences) and would happily
emit `DROP INDEX` for anything you add by hand outside the model — and for the Phase 0
objects that are deliberately *not* in the model (`audit_events`, `idempotency_keys`,
`schema_migrations`, the `revision` columns), it would emit `DROP TABLE` / `DROP COLUMN`.
Schema changes are the reviewed SQL files in `db/`, applied by `manage_db.py` (step 3b);
`schema_migrations` is the audit trail. Flask-Migrate/Alembic stay installed but unused.

If you keep Alembic in the deployment pipeline:

```bash
flask db stamp head     # record the current revision without running anything
flask db upgrade        # only ever apply *hand-written* revisions
```

There is no `migrations/` directory in this repo; `run.sh` / `server.sh` therefore only run
`flask db upgrade` when one exists, and never `flask db migrate`.

---

## Rollback

The refactor is **backward compatible with the old application code**: nothing was dropped
from the schema, only added (`clints.last_comment_at`, `clints.last_comment_id`, the
indexes) or widened (`attribute` `json` → `jsonb`, which the old code reads fine because
psycopg2 decodes both to `dict`).

So a rollback is just `docker run` with the previous image tag. Leave the database
migrations in place — reverting them is neither necessary nor safe. The same holds for the
Phase 0 migrations (0004–0006): the previous code never reads `audit_events`,
`idempotency_keys` or the `revision` columns, and the triggers only maintain `revision`.

If you must remove the revision triggers (e.g. to rule them out while debugging a write
path), drop the triggers only and keep the columns:

```sql
DROP TRIGGER IF EXISTS clints_bump_revision ON clints;
DROP TRIGGER IF EXISTS comments_bump_revision ON "Comments";
DROP TRIGGER IF EXISTS payments_bump_revision ON "Payments";
DROP TRIGGER IF EXISTS clintcontacts_bump_revision ON "ClintContacts";
DROP TRIGGER IF EXISTS paymentdates_bump_revision ON "paymentDates";
```

Recreate them by re-running the statements of `0006_revision_columns.sql` by hand (the
file is idempotent); do not delete its `schema_migrations` row.

If you must remove the denormalisation:

```sql
DROP TRIGGER IF EXISTS comments_last_comment_trg ON "Comments";
DROP FUNCTION IF EXISTS trg_comments_last_comment();
DROP FUNCTION IF EXISTS clints_refresh_last_comment(uuid);
DROP INDEX CONCURRENTLY IF EXISTS ix_clints_last_comment_live;
DROP INDEX CONCURRENTLY IF EXISTS ix_clints_user_last_comment_live;
DROP INDEX CONCURRENTLY IF EXISTS ix_clints_last_comment_id;
ALTER TABLE clints DROP COLUMN last_comment_at, DROP COLUMN last_comment_id;
```

---

## Operational notes

* **Redis is a cache, not a store.** The old `clint:*` client cache (a `KEYS` scan plus
  `pickle.loads` on every export) is gone; nothing in the API reads it any more. What is in
  Redis now: dashboard response fragments (60–150 s TTL), the JWT revocation list
  (`jwt:block:<jti>`) and background-job state (`JOB_RESULT_TTL`, 1 h). Flushing it is safe;
  it only logs users out of *revocation* (revoked tokens become valid again until they
  expire) and loses in-flight job results.
* **Exports stream.** `/clients/export` on the full book is ~355 MB of JSON, sent as ~41 MB
  gzipped in ~18 s with flat memory. Any proxy in front of the API needs a response
  timeout above 60 s and buffering off (`proxy_buffering off;` in nginx) for those routes.
* **Long imports** should use `?async=1` (see the README for the job contract) so the
  browser is not holding a request open.
* **`GET /dashbord/Allcollector/v2` is 7.5 MB raw.** It is cached for 120 s but
  re-compressed per request; under heavy concurrent use it is CPU-bound on gzip. If that
  becomes a problem, cache the compressed bytes instead of the rendered body
  (`api/dashboard.py:_cache_set_raw`).
* **The permission gate is now enforced.** 17 routes carry `@required_roles(...)` and 46
  routes that were completely unauthenticated now require a JWT. Check that the production
  roles actually have the keys they need (`branches*`, `contracts*`, `supervisors*`,
  `portfolios_delete`, `clints_delete`, `users_add`, `roles_add`, `roles_edit`) before
  going live, otherwise those screens start returning `403`.

## 5d. Click-to-call settings (Phase 5 P1)

The calling service's click-to-call settings are environment variables that must match the dialplan installed on Issabel. The full list, defaults and the matching dialplan values are in `docs/issabel-commissioning.md` §12. At a minimum:
* `CALLING_ENABLED=1`, `CALLING_PBX_COMMISSIONED=1`, and the AMI/AGI secrets are set only after the runbook's verification passes;
* the PBX and database clocks are NTP-synced, because evidence is ordered by PBX time;
* retention of call events and imported CDR is off by default. See `CALL_EVENTS_RETENTION_DAYS` / `PBX_CDR_IMPORTED_RETENTION_DAYS` (minimum 30).
