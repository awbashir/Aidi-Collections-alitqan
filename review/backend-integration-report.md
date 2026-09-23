# CAWP-API backend refactor — INTEGRATION pass report

Final pass: dead-code removal, `core/auth.py`, the `models.py` schema catch-up, switching the
client grids to the denormalised `clints.last_comment_at` / `last_comment_id`, re-implementing
`/dashbord/collector`, rebuild + verification, fixture cleanup and docs.

Container `cawp-api` is left **healthy on :9797 running the production `CMD`** (gunicorn
gthread, 1 worker × 32 threads). No scratch gunicorn is running (`:9798` refuses connections;
the only two `gunicorn` processes in the container are the CMD master (pid 7) and its worker
(pid 8)).

---

## 1. What was deleted from `app.py`

| | lines | `grep -c "@app.route"` |
|---|---:|---:|
| original (`logs/app.py.orig`) | 9,066 | 159 |
| after foundation + B1/B2/C (`backups/app.py.pre-integration`) | 8,526 | 163 |
| after `tools/integrate_blueprints.py --apply` (57 legacy routes deleted) | 4,721 | 106 |
| **after this pass** | **3,537** | **105** |

`app.url_map` has **163 rules** (excluding Flask's `static`):
`app.py` 105 + `clients_reports` 13 + `reports` 23 + `dashboard` 8 + `imports` 14.
That is the same 163 the pre-integration tree had — one route moved out of `app.py`
(`/dashbord/collector`, §5) and into `api/dashboard.py`, so the blueprint count went 57 → 58.

### Functions removed (988 lines), each grep-verified to have zero live references

`insertClints` (70) · `default` (3) · `_normalize_excel_key` (10) · **`_build_clints_payload`
(532)** · `dashbordCollector` (88, re-implemented — §5) · `send_notification_to_user` (38) ·
`updateClintInRedis` (5) · `getClintsBySupervisor` (40) · `check_amount` (23) · `fetchClients`
(16) · `isSuperAdmin` (9) · `isSupervisor` (8) · `fetchClientsByUser` (11) ·
`fetchClientsBySuperAdmin` (10) · `fetchClientsBySupervisor` (7) · `fetchClientsForUser` (10) ·
`filterClients` (45) · `exportServerSideMode` (11) — plus `_roles_map`, `_users_role_map`,
`invalidate_permission_maps`, `has_permission`, `required_roles` (52), which **moved** to
`core/auth.py`.

* `_build_clints_payload` — app.py's `GET /clints` is gone (`api/clients_reports.get_clints`
  serves it); the only two remaining occurrences of the name in the tree are docstring
  references.
* `updateClintInRedis` — the definition **and all 7 remaining call sites** were deleted
  outright (`lock_client`, `unlock_client`, `update_clint`, `update_clint_delinquency`,
  `createComment`, `deleteComment`, `updateClintToUnArchive`), together with the two orphaned
  `clint = db.session.get(Clints, comment.clint_id)` lines the calls left behind. The three
  remaining pure *writers* (`redis_obj.keys("clint:<id>*")` → `delete` in
  `updateClintToArchive`, `deletePortfolio`, `deleteClint`) were deleted too. `grep -c
  "redis_obj\."` in the whole tree is now **0**.

### Deliberately **kept** (the brief listed them, but they have live callers)

| Name | Live callers |
|---|---|
| `srverSideMode` | `GET /uesrs`, `/roles`, `/nationalities`, `/updateSides` (4 routes still in `app.py`) |
| `my_sort`, `my_filter`, `my_pagination`, `my_custom_filter`, `my_get_custom_filter_values`, `filter_mui` | called by `srverSideMode` (and `my_get_custom_filter_values` directly by `/uesrs`) |
| `pagination_filter_clients` | `GET /clints/archive/reson`, `GET /collection_methods` |
| `sortClints`, `filterClints`, `customFilter`, `getKey`, `get_nested_value` | called by `pagination_filter_clients` / `my_sort` / `filter_mui` |
| `FilterServerSide` (`classes/`) | used by `srverSideMode` → `classes/` kept |
| `Issable_db` | `/cdr`, `/cdr_by_date`, `/cdr/recording` in `app.py` **and** `api/dashboard.py` |
| `openpyxl`, `redis_lib` | the 4 upload routes; `redis_lib.RedisError` in the JWT blocklist loader |
| `getheaders` / `getdataasdict` / `checkheader` | the 4 `/api/…/file/upload` routes |

Removing them would have changed the behaviour of routes that are still Python-filtered;
they are the obvious next slice (`/uesrs`, `/roles`, `/nationalities`, `/updateSides`,
`/collection_methods`, `/clints/archive/reson` → `services/reports_query`) and are flagged in
§10.

### Deleted directories / imports

* **`redis_cache/` deleted** (`RedisClient`, the last `pickle` consumer). No importer left.
* `app.py` imports removed: `json`, `pickle`, `re`, `relativedelta`, `Date`, `between`,
  `UUID`, `joinedload`, `lookups`, `export_path`, `migrate`, `socketio`, `RedisClient`,
  **`pandas as ps`**.
  The one remaining pandas call site (`getclints_v2`'s `customeFilterInquiryClintByExcelValue`
  Excel filter) now uses `services.excel.column_values`, agent C's verified byte-for-byte
  replacement for `ps.read_excel(p, dtype=str).iloc[:, i].astype(str).tolist()`.
  (`pandas`/`numpy` are still in `requirements.txt` — nothing else imports them; dropping them
  would shrink the image by ~90 MB and is listed as a follow-up.)
* `api/imports.py`: `functools.wraps`; `api/dashboard.py`: `supervisor_scope`;
  `api/clients_reports.py`: `datetime`, `decimal`, `json`, `uuid`, `werkzeug.http.http_date`
  (its local big-int fallback collapsed into `core.serialization.dumps`).
* The 57 `# [integrated] …` marker comments were stripped and replaced by a short module-level
  map in the `app.py` docstring (which blueprint owns which slice, with route counts).

### `pyflakes`

```
docker exec cawp-api sh -c 'python -m pyflakes /app/app.py /app/api/*.py /app/services/*.py \
                            /app/core/*.py /app/models.py /app/extensions.py /app/tests/smoke.py'
/app/app.py:61:1: 'from models import *' used; unable to detect undefined names
```

That single informational line is the only output: **zero** "imported but unused", **zero**
"undefined name".

---

## 2. `core/auth.py`

New module (95 lines) holding `roles_map`, `users_role_map`, `invalidate_permission_maps`,
`current_role`, `has_permission`, `is_admin_identity`, `required_roles`. No behaviour change —
the bodies are the ones that were in `app.py`, with the two duplicated lookups
(`has_permission` and `_is_admin_identity` each resolved the role by hand) factored into
`current_role()`.

* `app.py` now does
  `from core.auth import (invalidate_permission_maps, is_admin_identity, required_roles)`.
* `api/imports.py` lost its local `required_roles` shim and the lazy `from app import
  has_permission` inside the decorator body; it imports `from core.auth import required_roles`
  at module level. No import cycle (`core.auth` imports only `core.lookups`, `extensions` and
  `models`).
* Verified live: `DELETE /portfolios/<id>` → **403** for a collector, **404** (i.e. past the
  gate) for the ADMIN — see the smoke test in §7b.

---

## 3. `models.py`

* `Clints.last_comment_at = db.Column(db.DateTime(timezone=True), nullable=True)` and
  `Clints.last_comment_id = db.Column(UUID(as_uuid=True), nullable=True)` added, with a comment
  pointing at `db/03_last_comment.sql` and the `comments_last_comment_trg` trigger that
  maintains them (the API never writes them).
* The three indexes added to `Clints.__table_args__` with the exact names, predicates and
  ordering:

```python
db.Index('ix_clints_last_comment_live', last_comment_at.desc().nullslast(),
         postgresql_where=db.text('is_archive = false')),
db.Index('ix_clints_user_last_comment_live', 'user_id',
         last_comment_at.desc().nullslast(),
         postgresql_where=db.text('is_archive = false')),
db.Index('ix_clints_last_comment_id', 'last_comment_id',
         postgresql_where=db.text('last_comment_id IS NOT NULL')),
```

* The four `SAWarning`s are gone. Both sides of each overlapping relationship pair got
  `overlaps=`, on the relationship **and** on its backref, which is what SQLAlchemy asks for:

```python
# Comments
clints = db.relationship('Clints', lazy=True, overlaps="clint,comments",
                         backref=db.backref('clint_comments', lazy=True, overlaps="clint,comments"))
# Portfolios
clints = db.relationship('Clints', lazy=True, cascade="delete", overlaps="clint,portfolios",
                         backref=db.backref('portfolio', lazy=True, overlaps="clint,portfolios"))
```

  No `viewonly`, no cascade change, no query change — only the mapper's conflict detection is
  silenced.

```
docker exec cawp-api sh -c 'cd /app && python -c "import app"' 2>&1 | grep -ci sawarning
0
```

### `compare_metadata` = **0**

```
docker exec cawp-api sh -c 'cd /app && PYTHONPATH=/app python /tmp/cmp_meta.py'
…alembic.runtime.migration: Context impl PostgresqlImpl.
…alembic.ddl.postgresql: Detected sequence 'login_log_id_seq' … assuming SERIAL and omitting
…alembic.ddl.postgresql: Detected sequence 'notifications_id_seq' … assuming SERIAL and omitting
DIFFS: 0
```

The only other output is the pre-existing `SAWarning: Cannot correctly sort tables; there are
unresolvable cycles between tables "clintArchive, clints"` — the mutual FK that predates this
work. No index, column or type appears as an add or a remove.

---

## 4. Using the denormalised columns in `services/clients_query.py`

| Query | before | after |
|---|---|---|
| `build_clients_query` (`/clints`, `/clients/export`, `/clints/user/<id>`) | correlated `max(Comments.create_at)` scalar subquery + `outerjoin(Comments, clint_id = id AND NOT is_deleted AND create_at = <that max>)` | `outerjoin(Comments, Comments.id == Clints.last_comment_id)` |
| its COUNT twin | joined `Comments` whenever a comment-status **or** a `comment.create_at` filter was active | joins `Comments` only for a comment-status filter — `comment.create_at` is now `Clints.last_comment_at`, a column of `clints` |
| `build_archive_query` (`/clints/archive/`, `/clients/archive/export`) | `LEFT JOIN LATERAL (SELECT create_at, comment_status_id FROM "Comments" WHERE clint_id = clints.id AND NOT is_deleted ORDER BY create_at DESC LIMIT 1)` | `outerjoin(Comments, Comments.id == Clints.last_comment_id)` |
| `build_neglect_query` (`/clints/neglect`, `/clints/neglect/export`) | `GROUP BY clint_id → max(create_at)` over **1.27 M** comments, then a second join on that value; ordering key = the subquery column | ordering/filtering on `Clints.last_comment_at`, latest comment via `Comments.id == Clints.last_comment_id` |
| neglect `COUNT(*)+SUM(amount)` | always joined `portfolios` and outer-joined `users` | joins `portfolios` only when a contract filter is active and `users` only for a branch filter (`clints.portfolios_id` is `NOT NULL` + FK, so the inner join cannot drop a row — same argument B1 used for the archive count) |

`col_map["comment.create_at"]` and `sort_map["comment.create_at"]` / `["last_follow_up"]` /
`["neglect_days"]` now point at `Clints.last_comment_at`, which holds exactly the
`Comments.create_at` of `last_comment_id` but is index-backed and needs no join. Ordering
direction and NULL placement were left byte-identical to the previous code
(`ASC` ⇒ NULLS LAST, `DESC` ⇒ NULLS FIRST — PostgreSQL defaults, unchanged).

`have_payments` / `not_have_payments` / `have_nextCalls` still use `_latest_comment_lateral()`;
they were out of scope and are already inside budget (§9).

### Parity — page 1 captured before the switch, re-captured after

`tools/lc_parity.py` (new, stdlib) captures the full page-1 body of 10 requests and then
compares them **row dict by row dict**, matched on `id`:
`logs/parity/lc_before.json` (LATERAL / GROUP BY) → `logs/parity/lc_after.json`.

```
   501ms ->    189ms  common= 100  clints/default              countRow 171452 == 171452
  3272ms ->   3399ms  common= 100  clints/comment-desc         (field=comment.create_at&sort=desc)
  3914ms ->   4146ms  common= 100  clints/cs-filter            countRow   9882 ==   9882
  1302ms ->   1414ms  common= 100  neglect/default
   794ms ->   1020ms  common=   0  neglect/comment-desc        << see below
  1134ms ->   1410ms  common= 100  neglect/neglect-days
   934ms ->    751ms  common= 100  neglect/cs-filter
   246ms ->    212ms  common= 100  archive/default             countRow 302499 == 302499
  1128ms ->   1025ms  common= 100  archive/cs-filter           countRow  14068 ==  14068
   203ms ->    187ms  common= 100  archive/sort-create
```

`common=100` means all 100 ids matched **and** every one of the 100 row dicts is byte-identical
(`rows_with_diffs = 0` on every case; the comparator prints the differing keys otherwise).
`countRow` is identical everywhere. (The wall-clock numbers in this table were taken while a
second `--reload` gunicorn was still sharing the container; the real numbers are in §9.)

**Two differences, both explained:**

1. **`totalAmount` last-digit float noise** on 5 cases
   (`696932571.935904` vs `696932571.935905`). Pre-existing PostgreSQL parallel-aggregate
   non-determinism — the foundation report established that comparing the *same* server against
   itself produces the same class of diff.
2. **`neglect/comment-desc` (`field=comment.create_at&sort=desc`) — page 1 is disjoint.**
   `DESC` puts NULLs first, and **97,127 of the 175,021 live clients have no comment at all**,
   so page 1 is 100 arbitrary rows out of a 97k-row tie. Verified: every row on **both** pages
   has `last_follow_up: null` and `neglect_days: null`. Neither the old nor the new query has a
   tiebreak on that sort, so this is a tie, not an ordering change.

### `EXPLAIN (ANALYZE)` — `/clints/neglect` page 1, ADMIN scope, warm

```
=== id phase  (ORDER BY clints.last_comment_at ASC LIMIT 100)
Limit                                                      (actual time=84.292..96.236 rows=100)
  ->  Gather Merge  Workers Launched: 2
        ->  Sort  Sort Key: clints.last_comment_at
                  Sort Method: top-N heapsort  Memory: 33kB
              ->  Hash Join  (contracts)
                    ->  Hash Join  (portfolios)
                          ->  Parallel Bitmap Heap Scan on clints        rows=57151 loops=3
                                Filter: (user_id = … OR (hashed SubPlan 1))
                                ->  Bitmap Index Scan on ix_clints_user_notarch   rows=175080
Execution Time: 96.334 ms                      <- was a GROUP BY over 1.27M comments (300-800 ms)

=== count + sum
Aggregate                                                  (actual time=105.591..105.593 rows=1)
  ->  Bitmap Heap Scan on clints                           rows=171452
        ->  Bitmap Index Scan on ix_clints_user_notarch    rows=175080
Execution Time: 106.029 ms                     <- was 295 ms (the portfolios/users joins are gone)

=== payload phase (the <=100 ids, by primary key)
Sort  Sort Key: clints.last_comment_at
  ->  Hash Join
        ->  Nested Loop Left Join
              ->  Index Scan using clints_pkey on clints                 rows=100
              ->  Index Scan using "Comments_pkey" on "Comments"         loops=100   <- last_comment_id
Execution Time: 0.608 ms
```

In-process phase breakdown (same container, warm, 3 consecutive runs):

```
run0: build=14 agg=111 maps=10 ids=87 payload=27 serialize=2  TOTAL=251ms  countRow=171452
run1: build= 1 agg= 95 maps= 0 ids=87 payload= 5 serialize=2  TOTAL=189ms
run2: build= 1 agg= 96 maps= 0 ids=116 payload=5 serialize=1  TOTAL=220ms
```

`/clints` page 1 for comparison: **id phase 0.68 ms** (`Index Scan using ix_clints_live_create`,
`Memoize` on portfolios/contracts, `Index Only Scan using ix_specify_clint_created`), count+sum
104 ms.

**Targets met** (end-to-end over HTTP on the rebuilt production container, warm — §9):
`/clints/neglect` **199–207 ms** (≤ 300 ms), `/clints` **101–137 ms` (≤ 150 ms).

*Note on `ix_clints_last_comment_live`:* it is declared `DESC NULLS LAST`, and the neglect
report's contract order is `ASC NULLS LAST` (PostgreSQL's default for `ASC`, which is what the
legacy `max(create_at) ASC` produced). Neither a forward nor a backward scan of a
`DESC NULLS LAST` index yields `ASC NULLS LAST`, so the planner sorts instead of walking the
index — it still only sorts a 24-byte-wide projection as a top-N heapsort, which is why the id
phase is 96 ms. An additional `(last_comment_at) WHERE is_archive = false` index (default
`ASC NULLS LAST`) would turn that into a ~1 ms index walk; it was **not** added, because the
brief specifies exactly three indexes and adding a fourth would have to be mirrored into
`db/03_last_comment.sql` and production. Listed as a follow-up in §10.

---

## 5. `/dashbord/collector` (v1) — re-implemented, not deleted

Moved out of `app.py` into `api/dashboard.py` (≈40 lines of body) next to the rest of the
family. Envelope, key spellings and status codes unchanged:
`{"clients": {"allClients", "allActiveClients", "allInactiveClients"}, "comments": [...], "payments": [...]}`.

* `allActiveClients` used to be `len(fetchClientsForUser(user))` — a Redis `KEYS *<user_id>*` +
  `pickle.loads`, which is what made the route answer **201** for every caller (the `clint:*`
  keyspace has no writer any more). It is now `is_archive = false`, the same rule
  `_dashboard_v1_payload` (the v1 `/dashboard`) uses. Two `COUNT(*)`s replace the scan.
* The `N+1` comment loop (4–6 `Model.query.get()` per comment) became one query with five
  outer joins; the nested `status`, `reachability`, `clint`, `portfolios.contract` blocks are
  produced exactly as before.
* `startDate` / `endDate` are still unix timestamps and still raise (→ `201`) when absent, so
  the golden snapshot for that route is unchanged.

Verified against a real collector (1,023 comments in the window):

```
status 200 keys ['clients', 'comments', 'payments']
clients {'allClients': 241, 'allActiveClients': 161, 'allInactiveClients': 80}
comments 1023  payments 5
comment keys: [call_id, clint, clint_id, comment, comment_reachability_id, comment_status_id,
               contactMethod_id, contactNumber, create_at, id, is_active, is_archive,
               is_deleted, next_contact_date, portfolios, reachability, status, update_at, user_id]
  status: وعد بالسداد   clint: 0cc2ff0d-…   portfolios.contract: تمارا   reachability: تم التوصل
```

and for the ADMIN identity with no clients: `200`, `{"allClients":0,…}`, 89 ms (the frontend
does not call this route at all — only `/dashbord/collector/v2`).

---

## 6. Rebuild + restart

```
docker compose build api        -> Flask-Compress==1.14 + brotli installed into the image
docker compose up -d api        -> Container cawp-api Started  (recreated, not restarted)
docker inspect -f '{{.State.Health.Status}}' cawp-api  -> healthy
curl -s http://localhost:9797/health
{"status":"ok","db":true,"redis":true,"version":"2.0"}
```

Boot log: `Booting worker with pid: 8` then the four blueprints
(`clients_reports 13 / reports 23 / dashboard 8 / imports 14`) — **no SAWarning**.

**gzip:**

```
Accept-Encoding: gzip       /dashbord/Allcollector/v2 ->  1,950,279 B   (Content-Encoding: gzip, Vary: Accept-Encoding)
Accept-Encoding: identity   /dashbord/Allcollector/v2 ->  7,543,399 B
```

7.5 MB → **1.95 MB on the wire (3.9×)**, and the uncompressed size is unchanged from the
golden baseline (7,550,858 B before vs 7,545,241 B now — the delta is the fixture data that was
reverted in §8).

---

## 7. Verification

### 7a. Golden snapshot

`logs/golden_after_integration.json` / `.log`, compared against `logs/golden_before.json`
(the ORIGINAL code). Run **after** the fixture revert with the Redis cache flushed, so no
count drifts from test data remain.

```
27 endpoint(s) with shape/count differences
```

Every one of the 27, by class:

| # | Class | Endpoints | Explanation |
|---|---|---|---|
| **16** | `shape:` — two **additive** keys `last_comment_at` / `last_comment_id` on client rows | `/clints` ×6, `/clints/{have,not_have}_payments`, `/clints/neglect`, `/comments` (`clint.*`), `/payments` ×2, `/payments/approval_status` ×2, `/payment_date` ×2 | The two new `clints` columns. Client rows are projected from the full column list (`services/clients_query._CLINT_COLUMNS`, `services/reports_query.model_cols`), so the new columns appear in the nested client blocks. **Nothing was removed or renamed** — a machine diff of every list's full key-path set across the whole snapshot returns `-[] +['last_comment_at','last_comment_id']` and nothing else. See "the one intentional wire change" below. |
| **2** | `keys:` — the same two additive keys | `/clints/<id>` (live + archived) | `Clints.as_dict()` returns every column. Additive only. |
| 1 | `countRow: 0 -> 1748`, `n 0 -> 200` | `/clints/user/<id>` | Expected: the legacy route was `redis KEYS *<user_id>*` over an empty `clint:*` keyspace, so it answered "this collector has no clients" for every collector. SQL now. |
| 1 | `ids: 200 differ` | `/clints/archive/` (default page) | Expected: the legacy archive query has **no `ORDER BY`** unless `sort[key]` is `clint_amount`/`archive_create_at`. New documented default `create_at DESC, id`. With an explicit sort the ids match exactly (`…&sort[key]=archive_create_at&sort[order]=desc` shows no diff). |
| 1 | `ids: 200 differ` | `/payment_date` | Same reason: legacy `PaymentDates.query.filter(...).all()` has no `ORDER BY`. |
| 1 | `status: 500 -> 200`, `keys: +comments` | `/comments/export` | Expected fix: `exportServerSideMode` applied `customeFilterMoveDateValue[]` to a `payment_date` key comments do not have → `AttributeError` → 500. |
| 1 | `loginLog: n 4 -> 10`, `ids: 14 differ` | `/loginLog/user/<id>` | Expected fix (review §3.6): `.order_by(timestamp.desc())` now runs **before** `.limit(10)`, so it returns the newest 10 instead of an arbitrary 4. |
| 3 | `n 0 -> 164487 / 6965 / 171452` | `/clints/{not_have_payments,have_payments,neglect}/export` | Expected: the legacy exports read the empty Redis `clint:*` keyspace and returned 0 rows. |
| 1 | `status: 201 -> 200`, envelope `{error,message,status}` → `{clints,countRow,totalAmount}` | `/clints/currentUser/have_nextCalls` | Expected: legacy raised `IndexError` on a missing `customeFilterDateValue[]`. |
| 1 | `status: 201 -> 200`, `+clints` | `/clints/currentUser/have_nextCalls/export` | Same. |

Not flagged but worth recording (the tool cannot flag them because the legacy body was
`null` / an error):

* `/cdr` — `null` (5 B) → `[]` (3 B), as expected.
* `/dashboard` and `/dashbord/collector` — both still `201` for the snapshot's parameters
  (`startDate=2026-09-09` is not a unix timestamp, so `int()` raises on both sides). `error` is
  now `"internal error"` instead of the exception text — the foundation pass's documented
  change, `FLASK_DEBUG` off.
* `/dashbord/Allcollector/v2` (`clients` n=66,094 on both sides, identical shapes),
  `/dashbord/commentsReport/v2` (`comments` n=5,432 on both sides), `/uesrs` (`countRow` 146),
  `/uesrs/export` (287 users), `/dashbord/admin/dailyPayments/v2`,
  `/dashbord/admin/dailyPaymentDates/v2` — **all clean after the fixture revert**; the run
  before the revert showed the expected `+2 users` / `+N rows` drift and nothing else.
* **First-list-key artefacts: none.** The tool now compares every list by key.

**The one intentional wire change of this pass** is the pair of additive keys
`last_comment_at` / `last_comment_id` on client objects. Suppressing them would mean
special-casing the projection in four places (`services/clients_query._CLINT_COLUMNS`,
`services/reports_query.model_cols`, `Clints.as_dict`, `api/dashboard`), which is more risk
than the ~60 bytes per row they cost. Grep over `CAWP-frontend/src` finds no code that
enumerates client keys (every grid reads named paths), so nothing breaks. If the product wants
byte-identical rows, the one-line change is to exclude the two names from `CLINT_COLUMN_NAMES`
and `col_pairs`.

### 7b. Smoke test — `tests/smoke.py` (new, stdlib + openpyxl)

The agents' parity harnesses (`b1_parity`, `b2_parity`, `c_parity`) all compare a legacy
handler against its `/_new/...` twin; the legacy handlers no longer exist, so none of them
supports a single-target mode. `tests/smoke.py` (494 lines) replaces them for the integrated
server:

```
docker exec cawp-api sh -c 'cd /app && PYTHONPATH=/app python tests/smoke.py'
…
RESULT: 51/51 checks passed
```

Covered: `/health`; `addClintsByExcel` (sync + `?async=1` + job polling, 6-row generated xlsx
in a throw-away portfolio); `update/byexcel` type 2 (sync + async, values verified in the DB);
`attribute/byexcel` (sync + async + the synchronous `201` validation error);
`SepcifyUsersByExcel` (sync + async); `commentsbyexcel` (sync + async, **plus an assertion that
the `comments_last_comment_trg` trigger filled `clints.last_comment_id` for 6/6 clients**);
`archivebyexcel` → `unarchivebyexcel` → `archive/ids` → `unarchivebyexcel?async=1` →
`archive/portofolio/<id>` (and its `404` on the second call); `sepcifyusers/ids`; `bulk-lock`
(+ the already-locked branch, + `400` on an empty `client_ids`); `bulk-unlock?async=1`;
`send/notification`; `DELETE /clints` (including clients that have `client_lock_history` rows —
the row legacy could not delete — and an unknown id); `required_roles` (collector → **403** on
`DELETE /portfolios/<id>`, ADMIN → **404**, i.e. past the gate); `POST /logout` → the revoked
token returns **401 `{"msg":"Token has been revoked"}`**.

Every async job reached `status: "done"` and its `result` was the synchronous body
(e.g. `{"messages": [], "message": "تم تحديث الحسابات بنجاح", "status": true}`).

Cleanup at the end: `portfolios=0 contacts=0 notifications=0; temp workbooks removed=6`.

### 7c. Concurrency (20 in flight, 40 requests) — **no 5xx anywhere**

| Request | p50 | p95 | max | statuses |
|---|---:|---:|---:|---|
| `/clints?page=1&pageSize=100` | 0.435 s | 0.761 s | 0.766 s | `200` × 40 |
| `/comments?…7-day range&page=1&pageSize=100` | 0.357 s | 0.403 s | 0.406 s | `200` × 40 |
| `/dashbord/commentsReport/v2` (cold cache) | 4.264 s | 6.401 s | 6.412 s | `200` × 40 |
| `/dashbord/commentsReport/v2` (warm cache) | 0.615 s | 0.967 s | 1.107 s | `200` × 40 |
| `/clints/neglect?page=1&pageSize=100` | 0.757 s | 1.128 s | 1.174 s | `200` × 40 |
| `/dashbord/Allcollector/v2` (warm cache) | 5.749 s | 9.771 s | 10.152 s | `200` × 40 |

`/dashbord/Allcollector/v2` is the outlier and it is **not** a database problem: a single
request is **0.13–0.15 s**, but the body is 7.5 MB and the 120 s cache stores the *rendered*
body, so twenty concurrent hits gzip 150 MB on a 2-core container. Same mechanism, smaller,
for `commentsReport/v2` cold. Caching the compressed bytes instead of the rendered ones is a
one-line change in `api/dashboard.py:_cache_set_raw`; flagged in §10 and in `DEPLOY.md`.

### Memory during a full `/clients/export`

`docker stats cawp-api --no-stream` sampled every 2 s for the whole run:

```
baseline                510.2 MiB
during (14 samples)     513.1  515.3  499.4  489.4  488.7  487.3  486.2  487.1
                        486.6  486.3  486.1  486.1  486.8  486.5  MiB
EXPORT  17.85 s   40,865,132 B gzipped   171,452 rows   (355 MB raw, ~9,600 rows/s)
```

Peak +5.1 MB, trough −24 MB against the 510 MiB baseline — **well inside ±50 MB** for a 355 MB
response body. The download was verified afterwards:
`clients rows: 171452  keys: ['clients','message','status']  status: True`.

### 7d. Final gates

| Check | Result |
|---|---|
| `compare_metadata` | **0 diffs** |
| `pyflakes` on `app.py`, `api/*`, `services/*`, `core/*`, `models.py`, `extensions.py`, `tests/smoke.py` | clean (only the `from models import *` notice) |
| `docker exec cawp-api python -c "import app"` | **0 SAWarnings** |
| `docker inspect -f '{{.State.Health.Status}}' cawp-api` | `healthy` |
| gunicorn processes in the container | pid 7 (master) + pid 8 (worker), production CMD only; `:9798` refuses connections |

---

## 8. Fixture cleanup

One transaction (`/tmp/revert.sql`, run through `psql`):

```
BEGIN
UPDATE 50   -- B1's clients restored to their previous owner (DISTINCT ON SepcifyUsers, create_at DESC)
UPDATE 2    -- B2's clients restored the same way
DELETE 3    -- "paymentDates"  (aidi.collector2)
DELETE 5    -- "Payments"      (aidi.collector2, incl. approval_by)
DELETE 5    -- "Comments"      (aidi.collector2)  -> the trigger refreshed clints.last_comment_* on the way out
DELETE 52   -- "SepcifyUsers"  (user_id or create_by = either test collector)
DELETE 29   -- login_log rows for 'aidi.collector' / 'aidi.collector2'
DELETE 2    -- users
COMMIT
```

Every one of the 52 re-assigned clients had a recoverable prior owner in `"SepcifyUsers"`
(checked: 50/50 and 2/2 before running the update), so no client was left pointing at a
deleted user.

**Proof:**

```
SELECT user_name FROM users WHERE user_name LIKE 'aidi.%';
 user_name
-----------
 aidi.dev            <- the local dev admin, documented in README.md
(1 row)

users total = 287                                     (= the golden baseline's /uesrs/export n)
clients_left=0  sepcify_left=0  comments_left=0  payments_left=0  ptp_left=0
portfolios LIKE 'C-TEST-%' OR 'SMOKE-%'  = 0
"ClintContacts" LIKE '%@test.local' OR '%@smoke.local' = 0
```

`aidi.dev` (`d7dced3b-3a75-4f32-aea6-3b5dce0aeaaf`, role ADMIN, password `Aidi@2026`) is kept
and documented in `CAWP-API/README.md` as the local dev admin, with the explicit note that it
exists **only in the local Docker database** and is not in `dump-mydata-202609151932.sql`.

---

## 9. Consolidated before → after (all agents)

Warm, page 1, ADMIN, restored production dump (171,452 live clients / 302,499 archived /
1.27 M comments). "legacy" is the original code; "new" is the integrated tree measured on the
rebuilt production container unless noted.

### Client lists and exports (B1 + this pass)

| Endpoint | legacy | new | note |
|---|---:|---:|---|
| `GET /clints` | 167–515 ms | **101–137 ms** | target ≤150 ms ✅ |
| `GET /clints` `field=amount&sort=desc` | 409 ms | **203–242 ms** | |
| `GET /clints/archive/` | 1045–5861 ms | **101–152 ms** | |
| `GET /clints/have_payments` | 533–1762 ms | **250–271 ms** | |
| `GET /clints/not_have_payments` | 923–2465 ms | **252–279 ms** | |
| `GET /clints/neglect` | 857–1821 ms | **199–207 ms** | 1.0–1.4 s after B1; the denormalised column took it the rest of the way — target ≤300 ms ✅ |
| `GET /clints/user/<id>` | 4–22 ms (returned **0 rows**) | 79–470 ms (1,748 rows) | legacy read an empty Redis keyspace |
| `GET /clients/archive/export` (365 rows) | 399–5367 ms | **29–146 ms** | |
| `GET /clients/export` (6,412 rows) | 1764 ms | **652 ms** | |
| `GET /clients/export` (171,452 rows) | **OOM-killed the worker** | **17.8 s, 41 MB gzipped, flat RSS** | ~9,600 rows/s |
| `GET /clints/neglect/export` (171,452 rows) | 0 rows (Redis) | 15.9 s, full result set | |
| `GET /clints/not_have_payments/export` (164,487) | 0 rows (Redis) | 16.3 s | |

### Comments / payments / promise-to-pay / lookups (B2)

| Endpoint | legacy | new |
|---|---:|---:|
| `GET /comments` (7 d, ~32 k rows) | 4439–4981 ms | **34–278 ms** |
| `GET /comments/export` (32,195 rows) | 8687 ms → **HTTP 500** | **6.4–7.5 s**, 200 |
| `GET /comments/current_user` | 6–13 ms | 10–22 ms |
| `GET /payments` | 129–554 ms | **41 ms** |
| `GET /payments/export` | 1280–1340 ms | **190–209 ms** |
| `GET /payments/approval_status` | 102–124 ms | **42–51 ms** |
| `GET /payments/approval_status/export` | 110–183 ms | 150–193 ms |
| `GET /payment_date` | 1939–2530 ms | **32–173 ms** |
| `GET /payment_date/export` | 1831–2727 ms | **139–203 ms** |
| `GET /contracts` | 567–587 ms | **85–102 ms** |
| `GET /supervisors` | 308–514 ms | **16–19 ms** |
| `GET /comment_status` | 89–190 ms | **7–11 ms** |
| `GET /uesrs/export` | 844–908 ms | **102–213 ms** |
| `GET /dashbord/commentsReport/v2` | 3519–5042 ms | **≈300 ms** uncached / **37 ms** cached |
| `GET /dashbord/Allcollector/v2` | 198 ms, 7.55 MB | **130–150 ms, 1.95 MB on the wire** |
| `GET /dashbord/collector/v2` | 15 ms | 13 ms |
| `GET /dashbord/admin/dailyPayments/v2` | 14 ms | 8 ms |
| `GET /dashboard` (v1) | 21–31 s | **3.0–6.0 s** |
| `GET /dashbord/collector` (v1) | **201** for every caller | **200**, 89 ms (§5) |

### Excel imports and bulk actions (C) — 3,000 rows

| Route | legacy | new | speed-up |
|---|---:|---:|---:|
| `POST /clint/addClintsByExcel` | 26.1 s | 2.18 s | **12.0×** |
| `POST /clints/update/byexcel` type 2 | 16.3 s | 2.03 s | 8.0× |
| `POST /clints/update/byexcel` type 4 | 13.6 s | 1.72 s | 7.9× |
| `POST /clints/update/byexcel` type 5 | 12.3 s | 1.77 s | 6.9× |
| `POST /clints/attribute/byexcel` | 10.9 s | 1.93 s | 5.7× |
| `POST /clint/SepcifyUsersByExcel` | 18.4 s | 3.88 s | 4.8× |
| `POST /commentsbyexcel` | 22.5 s | 2.50 s | 9.0× |
| `PUT /clint/archivebyexcel` | 22.9 s | 1.21 s | **19.0×** |
| `PUT /clint/unarchivebyexcel` | 17.5 s | 0.35 s | **49.8×** |
| `PUT /clint/archive/ids` | 19.9 s | 0.80 s | 24.8× |
| `POST /sepcifyusers/ids` | 22.0 s | 1.15 s | 19.1× |
| `PUT /clints/bulk-lock` | 19.8 s | 0.39 s | **50.5×** |
| `POST /send/notification` | 8.07 s | 2.08 s | 3.9× |
| `DELETE /clints` (8 rows) | 41 ms | 7 ms | 6.2× |

At 200 rows the same routes are 2.0×–40.7× faster; `?async=1` removes the request duration
from the browser's perspective entirely.

---

## 10. Docs written, and what is left

**Written**

* `CAWP-API/README.md` — rewritten: architecture (app.py + `api/` + `services/` + `core/` +
  `models`/`extensions`), request flow, running locally from `old/` with ports and `/health`,
  the local dev admin `aidi.dev`, every env var from `.env.example`, the full `?async=1` /
  `GET /jobs/<id>` contract with job names and status semantics, the `tests/` scripts, and the
  before/after performance tables plus *why* each change helped.
* `CAWP-API/DEPLOY.md` — new: production upgrade order (`db/01` online → `db/02` and `db/03`
  in a maintenance window, with the lock each one takes and a verification query), the env
  vars that must be set, build + `docker run` with the Dockerfile `CMD`, why one worker,
  a release smoke-check, **the warning that `flask db migrate` autogenerate must never run
  against production** (with `flask db stamp head` / `upgrade` as the alternative), rollback,
  and the operational notes (Redis is a cache, exports stream and need proxy buffering off,
  the permission gate is now enforced).
* `run.sh` / `server.sh` / `db.sh` — rewritten: `--env-file` everywhere, `flask db migrate`
  removed, `flask db upgrade` kept but guarded by `[ -d migrations ]` (there is no
  `migrations/` directory in the repo), `db.sh` applies the three `db/*.sql` scripts without
  `--single-transaction` (because of `CREATE INDEX CONCURRENTLY`), and a health wait loop.

**Follow-ups (not done, deliberately)**

1. `/uesrs`, `/roles`, `/nationalities`, `/updateSides`, `/collection_methods`,
   `/clints/archive/reson` are the last six routes that still load a table and
   filter/sort/paginate in Python (`srverSideMode` / `pagination_filter_clients` and their
   ~500 lines of helpers, plus `classes/FilterServerSide`). Moving them to
   `services/reports_query` would delete that whole block.
2. `ix_clints_last_comment_live_asc ON clints (last_comment_at) WHERE is_archive = false`
   would turn `/clints/neglect`'s 96 ms sort into a ~1 ms index walk (§4). Needs the index in
   `db/03_last_comment.sql`, in `models.py` and in production.
3. `api/dashboard.py:_cache_set_raw` should cache the **gzipped** bytes: `/dashbord/Allcollector/v2`
   is 7.5 MB and is re-compressed on every cache hit (§7c).
4. `pandas` / `numpy` can come out of `requirements.txt` — nothing imports them any more
   (~90 MB of image).
5. `services/clients_query.build_payments_flag_query` and `build_next_calls_query` still use
   `_latest_comment_lateral()`; switching them to `last_comment_id` as well is the same
   two-line change and would shave ~50 ms off `/clints/{have,not_have}_payments`.
6. `PUT /payment/approve/<id>` / `reject/<id>` are still JWT-only — the foundation pass flagged
   it as a product decision (`accounts_payments_edit` exists in `defult_permission` but the
   frontend never gates on it).
7. `services.clients_scope.invalidate_scope(user_id)` is still not wired into
   `PUT /supervisor/<id>`, `POST /supervisor_user`, `POST /supervisor_contract`, so a
   supervision change takes up to 60 s to affect the report screens (B1 §6.6).

---

## 11. Files touched by this pass

| File | Change |
|---|---|
| `CAWP-API/app.py` | 4,721 → **3,537** lines; 988 lines of dead functions, 7 `updateClintInRedis` call sites, 3 Redis key-delete blocks, 14 unused imports, the 57 `[integrated]` markers; new module map in the docstring |
| `CAWP-API/core/auth.py` | **new** (95) — the permission gate |
| `CAWP-API/models.py` | `Clints.last_comment_at` / `last_comment_id` + 3 indexes; 4 `SAWarning`s silenced with `overlaps=` |
| `CAWP-API/services/clients_query.py` | `/clints`, `/clints/archive/`, `/clints/neglect` use `last_comment_id` / `last_comment_at`; the neglect aggregate drops joins no filter needs |
| `CAWP-API/services/reports_query.py` | `safe_dumps` / `safe_jsonify` reduced to `core.serialization.dumps` / `jsonify`; `sanitize_big_ints` deleted |
| `CAWP-API/api/clients_reports.py` | `_dump` reduced to `core.serialization.dumps`; 5 unused imports |
| `CAWP-API/api/dashboard.py` | `/dashbord/collector` (v1) re-implemented here |
| `CAWP-API/api/imports.py` | `required_roles` from `core.auth` instead of the lazy `from app import …` |
| `CAWP-API/tests/smoke.py` | **new** (494) — single-target smoke test |
| `CAWP-API/README.md`, `CAWP-API/DEPLOY.md` | rewritten / new |
| `CAWP-API/run.sh`, `server.sh`, `db.sh` | rewritten |
| `CAWP-API/redis_cache/` | **deleted** |
| `tools/lc_parity.py` | **new** — page-1 row-by-row parity for the three client grids |
| `logs/golden_after_integration.{json,log}`, `logs/parity/lc_{before,after}.json`, `logs/docker_build_api_integration.log` | evidence |

`CAWP-frontend/` was not touched.
