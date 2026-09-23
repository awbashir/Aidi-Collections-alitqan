# CAWP API (Aidi Collections)

Flask + PostgreSQL + Redis REST API behind the `CAWP-frontend` React app: debt-collection
portfolios, clients, follow-up comments, payments, promise-to-pay dates, Excel imports and
the collector/supervisor dashboards.

Production deployment steps are in [DEPLOY.md](DEPLOY.md).

---

## Architecture

```
CAWP-API/
├── app.py                 Flask app factory-less entry point + 105 routes
├── extensions.py          db / migrate / jwt / cors / compress / socketio singletons
├── models.py              the 29 SQLAlchemy models + `defult_permission`
├── manage_db.py           schema migration runner for ../db (status / baseline / up / verify)
├── core/                  framework-level helpers, no business logic
│   ├── config.py          `Config` - every setting from env vars, with the old
│   │                      hard-coded values as defaults
│   ├── auth.py            `has_permission` / `required_roles` / `is_admin_identity`
│   ├── cache.py           Redis helpers (JSON, never pickle) + `client()`
│   ├── serialization.py   orjson JSON provider (RFC-822 datetimes, big-int fallback)
│   ├── lookups.py         tiny process-local TTL map cache
│   ├── jobs.py            background thread pool behind `?async=1` / `GET /jobs/<id>`
│   ├── http.py            `error_response(message, e, status=201)`  (legacy routes)
│   ├── files.py           safe upload/temp/export/document paths (`secure_filename`
│   │                      + realpath containment)
│   │   -- Phase 0 helpers, used by NEW routes only --
│   ├── api_response.py    real HTTP codes, `{message, status, data}` / `{message, status:false, error, code}`
│   ├── audit.py           `record()` an `audit_events` row in the caller's transaction
│   ├── idempotency.py     `@idempotent()` for the `Idempotency-Key` header (replay / 422 / 409)
│   ├── revisions.py       expected-revision checks on the unmapped `revision` columns (409)
│   ├── riyadh.py          Asia/Riyadh `today()`, `day_bounds()`, `range_bounds()`, `is_overdue()`
│   └── permissions.py     registry of new permission keys + merge into `roles.permission`
├── services/              SQL builders and row serializers - **no Flask imports**
│   ├── clients_query.py   /clints, archive, have/not_have payments, neglect, next calls
│   ├── clients_scope.py   `resolve_scope()` - who may see which clients
│   ├── reports_query.py   the SQL translation of the legacy Python filter/sort/paginate
│   ├── report_scope.py    per-report visibility rules
│   ├── excel.py           `read_sheet` / `column_values` (exact pandas replacement)
│   ├── imports.py         the Excel import runners (batched)
│   └── bulk_actions.py    archive / unarchive / assign / lock / delete / notify
├── api/                   Flask blueprints, registered by `api.register_blueprints(app)`
│   ├── clients_reports.py 13 routes - client lists + streamed client exports
│   ├── reports.py         23 routes - comments / payments / promise-to-pay /
│   │                      contracts / supervisors / comment_status / uesrs export
│   ├── dashboard.py        8 routes - `/dashbord/*` + the v1 `/dashboard`
│   └── imports.py         14 routes - Excel imports, bulk actions, `?async=1`
├── classes/               `FilterServerSide` - the legacy `*_Qkey[]` filter quad
├── issable/               Asterisk CDR access (MySQL + SFTP for recordings)
└── tests/                 see "Tests" below (pytest suite + live-stack scripts)
```

`app.py` keeps the routes that are plain CRUD over a single table (users, roles,
nationalities, branches, contracts, portfolios, comment statuses, collection methods,
contacts, documents, notifications, CDR, auth, `/health`, `/jobs/<id>`). Everything that
was a report, an export, a dashboard or an Excel import lives in `api/` on top of
`services/`.

**New routes (Phase 0+)** live in new blueprints and use `core/api_response.py`: real
HTTP status codes (400/403/404/409/422) with `{"message", "status": false, "error", "code"}`,
and `{"message", "status": true, "data"}` on success; mutating ones take an
`Idempotency-Key` header (`core/idempotency.py`) and an expected revision
(`core/revisions.py`), and write an `audit_events` row (`core/audit.py`). None of this
applies to the legacy routes below.

**Wire compatibility is frozen.** Path spellings (`clints`, `uesrs`, `dashbord`,
`sepcify`, `reson`, `frist_name`, `portofolio`, …), response envelopes, Arabic messages
and status codes are exactly what the React app already reads — including the
201-on-business-error convention (`{"status": false, …}` with a 2xx code) and the
`423 Locked` from `check_client_lock`. `401` is never returned for a business error,
because the frontend force-logs-out on it.

### Request flow

```
client → gunicorn (gthread, 1 worker × 32 threads)
       → Flask  → @jwt_required (JWT in the Authorization header; revocation list in Redis)
                → @required_roles('<permission key>')    (core/auth.py)
                → route (app.py or api/*.py)
                → services/*  → SQLAlchemy → PostgreSQL
                → core/serialization (orjson) → Flask-Compress (gzip)
```

---

## Running locally

Everything runs from the parent directory (`old/`), which holds `docker-compose.yml`:

```bash
cd old
docker compose up -d              # postgres + redis + api
docker compose --profile web up -d  # + the Vite dev server for CAWP-frontend
docker compose logs -f api
```

| Service | Container | Port |
|---|---|---|
| API | `cawp-api` | **9797** (`9798` is a scratch port for a second gunicorn inside the container) |
| PostgreSQL 16 | `cawp-db` | **5433** → 5432 |
| Redis 8 | `cawp-redis` | **6379** |
| Frontend dev server (profile `web`) | `cawp-web` | **5177** |

`./CAWP-API` is bind-mounted into `/app`, so edits are visible immediately; restart the
container (or run a `--reload` gunicorn on `:9798`) to pick them up.

```bash
curl -s http://localhost:9797/health
# {"status":"ok","db":true,"redis":true,"version":"2.0"}
```

`GET /health` is the only unauthenticated route besides `GET /` and `POST /login`. It is
what the Docker `HEALTHCHECK` polls; it returns `503` when the database is unreachable
(`redis` is reported but not fatal — Redis is used for caching and the JWT blocklist, and
every call to it is best-effort).

**Local dev admin:** `aidi.dev` / `Aidi@2026` (role `ADMIN`). This account exists **only
in the local Docker database** — it was created for the refactor and is *not* in
`dump-mydata-202609151932.sql`, so it will not appear in production or in a fresh restore.

### Without Docker

```bash
cp .env.example .env        # then edit DATABASE_URL / REDIS_HOST / JWT_SECRET_KEY
pip install -r requirements.txt
gunicorn --worker-class gthread --threads 32 --bind 0.0.0.0:9797 app:app
```

---

## Configuration

Every setting comes from the environment (`core/config.py`), with the previously
hard-coded value as the default. `.env` is loaded automatically via `python-dotenv`;
`.env.example` is the template and `.env.docker` is what the compose file passes to the
container.

| Variable | Default | Notes |
|---|---|---|
| `PORT` | `9797` | also used by the Docker `HEALTHCHECK` |
| `FLASK_DEBUG` | `0` | when on, `error` in error bodies carries the real exception |
| `DATABASE_URL` | – (**required**, startup fails without it) | pool 20 + 30 overflow, `pool_pre_ping`, `statement_timeout` |
| `DB_POOL_SIZE`, `DB_MAX_OVERFLOW`, `DB_STATEMENT_TIMEOUT_MS` | `20`, `30`, `120000` | |
| `REDIS_HOST`, `REDIS_PORT`, `REDIS_DB`, `REDIS_PASSWORD` | `10.10.10.240`, `6379`, `0`, – | cache + JWT blocklist + job state |
| `CACHE_DEFAULT_TTL` | `120` | dashboard fragment TTL |
| `SECRET_KEY`, `JWT_SECRET_KEY` | – (random per process, logged as CRITICAL) | **must** be set, or every restart logs everyone out |
| `JWT_ACCESS_HOURS` | `24` | |
| `CORS_ORIGINS` | `*` | comma-separated list |
| `MAX_UPLOAD_MB` | `50` | `MAX_CONTENT_LENGTH` |
| `UPLOAD_DIR` | `./uploads` | `temp/`, `documents/`, `exports/` are created on boot |
| `CDR_HOST`, `CDR_DB`, `CDR_USER`, `CDR_PASSWORD`, `CDR_CONNECT_TIMEOUT`, `CDR_ENABLED` | Asterisk box; no password default | `/cdr*` and the dashboard call charts |
| `RECORDINGS_ROOT`, `RECORDINGS_SSH_HOST/USER/PASSWORD/KEY/TIMEOUT` | Asterisk box; no password default (falls back to the SSH key) | only paths under `RECORDINGS_ROOT` can be downloaded |
| `JOB_WORKERS`, `JOB_RESULT_TTL` | `4`, `3600` | background jobs |
| `WEB_CONCURRENCY`, `GUNICORN_THREADS` | `1`, `32` | SocketIO in threading mode needs one worker |
| `SENTRY_DSN` | – | Sentry is only initialised when this is set |

---

## The async job contract

Every Excel-import and bulk-action route accepts `?async=1` (or `{"async": true}` in the
body; `1/true/yes/on`, case-insensitive, query string wins):

```
POST /clints/update/byexcel?async=1
```

**The request is still validated synchronously.** A missing or unsafe file, an unknown
contract, an empty portfolio, a missing `identifier_column`, an empty `client_ids` — all
return the normal body and status (`201` / `400` / `404`) immediately. Only work that
passed validation is queued:

```json
202 Accepted
{"status": true, "job_id": "0f3c…hex", "message": "تم بدء المعالجة في الخلفية", "async": true}
```

Poll it:

```
GET /jobs/<job_id>          (jwt; the owner or an ADMIN/SuperAdmin)
```
```json
{"id": "0f3c…", "name": "update-clints-by-excel", "owner": "<user id>",
 "status": "queued" | "running" | "done" | "failed",
 "progress": 0-100, "message": "rows 1500/3000",
 "result": { … },   // null until status == "done"
 "error": null,     // str(exception) when status == "failed"
 "created_at": …, "started_at": …, "finished_at": …}
```

* `result` **is the body the synchronous call would have returned**, byte for byte, so a
  dialog can keep the code it already has (`if (r.status) toast.success(r.message)`).
* A job whose *business* outcome is a failure (`"الحساب غير موجود"`) still finishes as
  `done` with `result.status === false`. `failed` means an unhandled exception.
* `progress` moves per 500-row batch.
* Job state lives in Redis for `JOB_RESULT_TTL` (3600 s). A `404
  {"message": "job not found"}` means *expired*, not *error*.
* `GET /jobs/<job_id>/download` streams `result.file` when a job produced an export file.

Job names: `add-clints-by-excel`, `update-clints-by-excel`, `attribute-by-excel`,
`sepcify-users-by-excel`, `comments-by-excel`, `archive-by-excel`, `unarchive-by-excel`,
`archive-by-ids`, `archive-portfolio`, `sepcify-users-by-ids`, `bulk-lock`, `bulk-unlock`,
`delete-clints`, `send-notification`.

Synchronous calls are now fast enough that `async` is only worth using above a few
thousand rows.

---

## Auth

* `POST /login` → `{"access_token", "status": true, "user": {…}}`; the frontend sends
  `Authorization: Bearer <token>` on every call and never uses cookies.
* `POST /logout` adds the token's `jti` to `jwt:block:<jti>` in Redis with a TTL equal to
  its remaining lifetime; the next request gets `401 {"msg": "Token has been revoked"}`.
* `@required_roles('<key>')` (`core/auth.py`) resolves the caller's role from the JWT
  identity and reads `<key>` out of the role's `permission` JSON; it returns
  `403 {"message": "You are not authorized to access this resource"}`. It must sit
  **below** `@jwt_required()`. `invalidate_permission_maps()` is called by every route
  that writes roles or users.

---

## Tests

### pytest suite (new code; no live stack needed)

`tests/test_*.py` cover the migration runner, `audit_events`, idempotency, the revision
triggers and helpers, Asia/Riyadh dates, the permission merge and the new-route response
convention. They run against a small throwaway PostgreSQL 16 (`test-db`, tmpfs, port
5439), never the 2.2 GB dump: the harness loads `db/schema/baseline.sql` (schema-only),
runs `manage_db.py baseline` + `up`, and seeds `tests/fixtures/seed.sql`.

```bash
# from the repository root: one command, everything in Docker
docker compose --profile test run --rm api-test
docker compose --profile test run --rm api-test -k riyadh -vv     # pytest args pass through
docker compose --profile test rm -sf test-db                         # stop + remove only test-db (`down` would stop the whole stack)

# or from the host (Python 3.11 + requirements.txt + requirements-dev.txt installed)
docker compose --profile test up -d test-db
TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5439/postgres python -m pytest -q CAWP-API/tests
```

`TEST_DATABASE_URL` is an admin connection; the suite creates and drops only databases
named `cawp_test*` (`KEEP_TEST_DB=1` keeps them for inspection). Without it, only the
tests marked `no_db` run. `pytest` lives in `requirements-dev.txt`, not in the production
`requirements.txt`. CI (`.github/workflows/ci.yml` at the repository root) runs the same
suite on every push/PR after loading the baseline and applying the migrations.

### Live-stack scripts

These run inside the API container against the running server.

```bash
# post-integration smoke test: imports (sync + async), bulk actions, DELETE /clints,
# logout/revocation, the permission gate.  Cleans up after itself.
docker exec cawp-api sh -c 'cd /app && PYTHONPATH=/app python tests/smoke.py'

# drop anything an interrupted import harness left behind
docker exec cawp-api sh -c 'cd /app && python tests/c_cleanup.py'
```

`tests/b1_parity.py`, `tests/b2_parity.py` and `tests/c_parity.py` are the refactor's
old-vs-new harnesses: they compare a legacy handler in `app.py` against its `/_new/...`
blueprint twin. The legacy handlers were deleted at integration, so these are kept as
documentation of what was verified and no longer run as-is.

From the repository root, `tools/api_snapshot.py` records status / timing / envelope keys /
`countRow` / first-row shape for ~75 GET endpoints and diffs two runs:

```bash
python3 tools/api_snapshot.py --base http://localhost:9797 \
        --out logs/golden_after.json --compare logs/golden_before.json
```

`tools/lc_parity.py` does the same for page 1 of the three client grids, row dict by row
dict (used to prove the switch to the denormalised `clints.last_comment_at`).

---

## Performance

The refactor replaced "load every row, then filter/sort/paginate in Python" with SQL, and
"one query per row" with batched lookup maps. Measured on the restored production dump
(171k live clients, 302k archived, 1.27M comments) with the same ADMIN user, page 1,
warm.

### Client lists and exports

| Endpoint | before | after |
|---|---:|---:|
| `GET /clints` (100 rows) | 167–515 ms | **86–180 ms** |
| `GET /clints/archive/` | 1045–5861 ms | **95–190 ms** |
| `GET /clints/have_payments` | 533–1762 ms | **198–284 ms** |
| `GET /clints/not_have_payments` | 923–2465 ms | **218–320 ms** |
| `GET /clints/neglect` | 857–1821 ms | **~190 ms** (after `clints.last_comment_at`) |
| `GET /clients/archive/export` (365 rows) | 399–5367 ms | **29–146 ms** |
| `GET /clients/export` (6,412 rows) | 1764 ms | **652 ms** |
| `GET /clients/export` (171,452 rows) | OOM-killed the worker | **25.8 s, flat RSS, 6.6k rows/s** |

### Comments / payments / promise-to-pay / dashboards

| Endpoint | before | after |
|---|---:|---:|
| `GET /comments` (7-day window, 32k rows) | 4439 ms | **34 ms** |
| `GET /payment_date` | 2530 ms | **32 ms** |
| `GET /payments` | 129 ms | **41 ms** |
| `GET /payments/approval_status` | 102 ms | **51 ms** |
| `GET /payments/export` | 1280 ms | **158 ms** |
| `GET /payment_date/export` | 1831 ms | **139 ms** |
| `GET /comments/export` (32,195 rows) | 8687 ms → **HTTP 500** | **5.9 s** (bug fixed) |
| `GET /contracts` | 567 ms | **85 ms** |
| `GET /supervisors` | 514 ms | **16 ms** |
| `GET /comment_status` | 89 ms | **7 ms** |
| `GET /uesrs/export` | 908 ms | **102 ms** |
| `GET /dashbord/commentsReport/v2` | 3519–5042 ms | **~300 ms** uncached / 15 ms cached |
| `GET /dashbord/Allcollector/v2` | 7.6 MB payload | **~1 MB on the wire** (gzip), 18 ms cached |
| `GET /dashboard` (v1) | 21–31 s | **3.0–6.0 s** |

### Excel imports and bulk actions (3,000 rows)

| Route | before | after | speed-up |
|---|---:|---:|---:|
| `POST /clint/addClintsByExcel` | 26.1 s | 2.2 s | 12× |
| `POST /clints/update/byexcel` | 12.3–16.3 s | 1.7–2.0 s | 7–8× |
| `POST /commentsbyexcel` | 22.5 s | 2.5 s | 9× |
| `POST /clint/SepcifyUsersByExcel` | 18.4 s | 3.9 s | 4.8× |
| `PUT /clint/archivebyexcel` | 22.9 s | 1.2 s | 19× |
| `PUT /clint/unarchivebyexcel` | 17.5 s | 0.35 s | 50× |
| `PUT /clint/archive/ids` | 19.9 s | 0.80 s | 25× |
| `POST /sepcifyusers/ids` | 22.0 s | 1.2 s | 19× |
| `PUT /clints/bulk-lock` | 19.8 s | 0.39 s | 50× |
| `POST /send/notification` | 8.1 s | 2.1 s | 3.9× |

What made the difference:

* **SQL instead of Python.** Filtering, sorting and paging happen in PostgreSQL; the
  routes select labelled columns, never ORM entities (an entity query pins every loaded
  object in the session identity map, which is what OOM-killed `/clients/export`).
* **No per-row queries.** Reference tables (users, portfolios, contracts, comment
  statuses, …) are read once per request through `core.lookups`.
* **Indexes** — `db/01_performance_indexes.sql` adds 30+, including two GIN trigram
  indexes so `contains` filters on `name` / `identityNumber` use an index.
  `db/02_jsonb_attribute.sql` converts `clints.attribute` from `json` to `jsonb`.
* **Denormalised latest follow-up** — `db/03_last_comment.sql` adds
  `clints.last_comment_at` / `clints.last_comment_id`, maintained by a trigger on
  `"Comments"`. The client grids and the neglect report join the latest comment by
  primary key instead of a `LATERAL`/`GROUP BY` over 1.27M comments.
* **Batched writes.** One commit per 500 rows instead of one per row; the bulk actions
  are single `UPDATE … WHERE id IN (…)` / `INSERT … VALUES (…),(…)` statements.
* **Streamed exports.** `Response(stream_with_context(...))` + `yield_per(2000)` +
  incremental gzip: a 355 MB export never exists in memory.
* **gzip** (Flask-Compress) on every JSON response — the 7.6 MB dashboard payload goes
  out at ~1 MB.
* **orjson** as the Flask JSON provider, with the same RFC-822 datetime rendering as
  before.
