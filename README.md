# Aidi Collections (CAWP) — local stack

برنامج التحصيل الذكي — debt-collection CRM. Two apps + a database dump.

> **Important:** read [`docs/PROJECT-NOTES.md`](docs/PROJECT-NOTES.md) first. It holds the target feature catalogue, the "extend, don't replace" rule, and the Issabel / in-app browser softphone plan.

| Path | What | Docs |
| --- | --- | --- |
| `CAWP-API/` | Flask 2.3 / SQLAlchemy 2 / PostgreSQL 16 / Redis API (gunicorn) | `CAWP-API/README.md`, `CAWP-API/DEPLOY.md` |
| `CAWP-frontend/` | React 18 + Vite 4 + Tailwind 3 (Aidi AI Enterprise design system) | `CAWP-frontend/README.md` |
| `dump-mydata-202609151932.sql` | `pg_dump` custom-format dump (2.2 GB restored) | `db/README.md` |

## Quick start (one command)

```bash
tools/dev-up.sh          # db (restored from the dump on first run) + migrations + dev admin + api + worker
tools/dev-up.sh --web    # + frontend dev server in Docker on http://localhost:5177
tools/dev-up.sh --tailnet  # --web, reachable from your other Tailscale devices
```

`--tailnet` runs the frontend with `VITE_API_URL=/api`, so the browser calls the dev server on
whatever host it opened, and `vite.config.ts` proxies `/api` to the API. Open
`http://<this-machine>.<tailnet>.ts.net:5177` (or its `100.x` IP) from any device on the tailnet.
Browsers allow the microphone only on HTTPS or localhost, so the softphone and voice lab need
HTTPS: turn on *HTTPS Certificates* in the Tailscale admin console (DNS page) and re-run. The
script then adds `tailscale serve` and prints the `https://` URL.

Safe to re-run. It never drops anything; the dump is restored only into an empty database.
It ends by logging in as the dev admin to prove the account works. The admin is created
or repaired by `CAWP-API/dev_seed.py` (ADMIN role, supervisor scope over every active
contract, password reset to the configured value). It refuses to run against a non-local
database host. Override with `DEV_ADMIN_USER` / `DEV_ADMIN_PASSWORD`.

## Run everything with Docker (step by step)

```bash
docker compose up -d db redis          # Postgres 16 (:5433) + Redis 8 (:6379, in-memory only, LRU)
docker exec cawp-db pg_restore -U postgres -d mydata --no-owner --no-privileges -j 4 /dump/mydata.dump   # first time only, ~3 min
docker exec cawp-db psql -U postgres -d mydata -f /db/01_performance_indexes.sql   # indexes (online)
docker exec cawp-db psql -U postgres -d mydata -f /db/02_jsonb_attribute.sql       # clints.attribute -> jsonb
docker exec cawp-db psql -U postgres -d mydata -f /db/03_last_comment.sql          # last-comment denormalisation + trigger
docker compose up -d api               # API on http://localhost:9797  (GET /health)
docker compose --profile web up -d     # optional: frontend dev server in Docker on http://localhost:5177
```

Or run the frontend on the host: `cd CAWP-frontend && npm ci && npx vite --port 5177` (reads `.env.development` → `VITE_API_URL=http://localhost:9797`).

| Service | Container | Port | Notes |
| --- | --- | --- | --- |
| Postgres 16 | `cawp-db` | 5433 → 5432 | `postgres` / `101016`, db `mydata`; `shm_size: 1gb`; tuned `shared_buffers`/`work_mem` |
| Redis 8 | `cawp-redis` | 6379 | `--save "" --appendonly no --maxmemory 768mb --maxmemory-policy allkeys-lru` |
| API | `cawp-api` | 9797 (+9798 scratch) | gunicorn gthread 1×32, `.env.docker`; source bind-mounted at `/app` |
| Frontend | `cawp-web` (profile `web`) | 5177 | Vite dev server |

Local-only dev admin (exists only in this Docker DB, not in the dump): **`aidi.dev` / `Aidi@2026`** (role ADMIN, supervisor scope over all contracts). `tools/dev-up.sh` creates it, or run `docker exec cawp-api python dev_seed.py` on its own.

## Tooling

| Tool | Purpose |
| --- | --- |
| `tools/api_snapshot.py --base URL --out f.json [--compare before.json]` | golden snapshot of ~80 GET endpoints (status, ms, envelope keys, counts, row shapes); `logs/golden_before.json` = the original code |
| `tools/screenshots.js <outdir>` | Playwright (installed Chrome) screenshots of 17 screens after logging in; `logs/screens/{before,phaseA,phaseB,final}` |
| `tools/e2e_import.js`, `tools/e2e_archive_excel.js` | browser end-to-end checks of the async Excel import / archive-by-Excel dialogs (202 → `/jobs/<id>` → result) |
| `tools/integrate_blueprints.py` | moved 57 legacy routes from `app.py` into `api/*` blueprints (already applied) |
| `CAWP-API/tests/*_parity.py`, `tests/smoke.py` | old-vs-new parity harnesses and the integrated smoke test |
| `db/*.sql` | production-safe DB scripts (see `db/README.md`) |
| `review/*.md` | the audit and every agent's report (backend review, foundation, B1/B2/C, integration, frontend cleanup, redesign A/B/C) |
| `design-reference/` | copy of the Aidi AI Enterprise design system docs + HTML references |
| `backups/` | tarballs of both trees taken before the refactor, `app.py.orig` (pristine), `app.py.pre-integration` |

## What changed (summary)

See `review/backend-integration-report.md` and `review/frontend-phaseC.md` for the full before → after tables. Headlines: every list/report/dashboard endpoint now pages, sorts and filters in SQL (e.g. `/comments` 4.4 s → 34 ms, `/payment_date` 2.5 s → 32 ms, `/clints/archive/` 1.0 s → 0.1 s, `/dashboard` 20–30 s → 3–6 s), exports stream with flat memory (the old ones OOM-killed the worker), Excel imports are batched (4–50×) and can run as background jobs (`?async=1` → `GET /jobs/<id>`), Redis holds JSON (no pickle) and is never scanned with `KEYS`, 46 previously unauthenticated routes require a JWT, server-side role checks work, secrets/hosts come from env, responses are gzip-compressed (7.5 MB dashboard → ~1 MB on the wire), and the UI is on the Aidi AI Enterprise system (navy primary, white chrome, orange rails only, 36/44 px metrics, Anodina + Manifa V2, CSS −52 %).
